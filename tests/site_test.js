// The public demo site (Node + Playwright): the sandbox pages from data/site/ served over
// HTTP with the same content security policy CloudFront adds (read from infra/data_stack.py,
// so the two can't drift). Checks: every page opens with no script error and no CSP
// violation; the sandbox banner shows; the switcher opens the other page's own address;
// a saved next step survives a reload and a switch to the manager's page, but a second
// visitor (a fresh browser) never sees it; "Clear my changes" empties it; the leader page
// offers no forms; blocked storage = no forms and no error; the laptop pages are no sandbox.
//   python roles.py --site, then node tests/site_test.js
const { chromium } = require("playwright");
const fs = require("fs");
const http = require("http");
const path = require("path");
const { execFileSync } = require("child_process");
const ROOT = path.join(__dirname, "..");
const DIR = path.join(ROOT, "data", "site");
const CSP = fs.readFileSync(path.join(ROOT, "infra", "data_stack.py"), "utf8")
  .match(/SITE_CSP = \(([\s\S]*?)\)\n/)[1].match(/"([^"]*)"/g).map((s) => s.slice(1, -1)).join("");
let passed = 0, failed = 0;
const check = (ok, name, detail = "") => { ok ? passed++ : failed++; console.log(`  ${ok ? "PASS" : "FAIL"}  ${name}${!ok && detail ? "  ->  " + detail : ""}`); };
const meta = (html) => JSON.parse(html.match(/<script id="page-meta" type="application\/json">([\s\S]*?)<\/script>/)[1]);

(async () => {
  if (!fs.existsSync(path.join(DIR, "index.html"))) { console.log("  FAIL  data/site/ is missing: run python roles.py --site"); process.exit(1); }
  // /search runs the real search function (infra/lambdas/search/handler.py) over the real redacted
  // file, like CloudFront -> API Gateway -> Lambda would; "__busy__" stands in for the rate limit
  const SEARCH = `import json, sys
sys.path[:0] = ["infra/lambdas/search", "."]
import handler
r = handler.handler({"requestContext": {"http": {"method": "GET"}}, "queryStringParameters": {"q": sys.argv[1]}})
print(json.dumps(r))`;
  let searches = 0;
  const server = http.createServer((req, res) => {
    const name = path.basename(decodeURIComponent(req.url.split(/[?#]/)[0])) || "index.html";
    if (name === "search") {
      searches++;
      const q = new URL(req.url, "http://x").searchParams.get("q") || "";
      if (q === "__busy__") { res.writeHead(429, { "Content-Type": "application/json" }); return res.end('{"message":"Too Many Requests"}'); }
      const r = JSON.parse(execFileSync("python3", ["-c", SEARCH, q], { cwd: ROOT, encoding: "utf8" }));
      res.writeHead(r.statusCode, r.headers);
      return res.end(r.body);
    }
    const file = path.join(DIR, name);
    if (!fs.existsSync(file)) { res.writeHead(404); return res.end(); }
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8", "Content-Security-Policy": CSP });
    res.end(fs.readFileSync(file));
  }).listen(0);
  const base = `http://127.0.0.1:${server.address().port}/`;
  const b = await chromium.launch();
  const watch = (p) => {
    const errors = [];
    p.on("pageerror", (e) => errors.push(e.message));
    p.on("console", (m) => { if (m.type() === "error" && !/net::ERR_/.test(m.text())) errors.push(m.text()); });   // fonts are offline here
    return errors;
  };

  console.log("\nEvery page opens under the site's content policy, in sandbox mode");
  const files = fs.readdirSync(DIR).filter((f) => f.endsWith(".html") && f !== "search.html").sort();   // the search page: below
  check(files.length >= 18 && files.includes("index.html") && files.includes("leader.html"), `${files.length} pages, with index.html and leader.html`);
  for (const f of files) {
    const ctx = await b.newContext(), p = await ctx.newPage(), errors = watch(p);
    await p.addInitScript(() => document.addEventListener("securitypolicyviolation", (e) => console.error("CSP " + e.violatedDirective + " " + e.blockedURI)));
    await p.goto(base + f); await p.waitForTimeout(250);
    for (const t of await p.$$eval("nav button[data-view]", (bs) => bs.map((x) => x.dataset.view))) await p.click(`#tab-${t}`);
    const banner = await p.isVisible("#sandbox-note");
    check(!errors.length && banner && meta(fs.readFileSync(path.join(DIR, f), "utf8")).sandbox === true,
      `${f}: no script error or CSP violation, every tab draws, sandbox banner`, JSON.stringify({ errors: errors.slice(0, 3), banner }));
    await ctx.close();
  }

  // open an account on a page's timeline (a fresh load, so the #account opens it)
  const openAcc = async (pg, f) => { await pg.goto("about:blank"); await pg.goto(base + f + "#" + acc); await pg.waitForTimeout(350); };
  console.log("\nSaving, privately");
  const mia = meta(fs.readFileSync(path.join(DIR, "csm_mia.html"), "utf8"));
  const acc = mia.edit.next_step[0], note = "Sandbox test " + Date.now();
  const ctx = await b.newContext(), p = await ctx.newPage(), errors = watch(p);
  await p.goto(base + "index.html"); await p.waitForTimeout(250);
  check(!(await p.$("#ns-save")) && !(await p.$("#fc-save")), "the leader page (the landing page) offers no forms");
  await p.selectOption("#page-switch", "csm_mia.html"); await p.waitForURL(/csm_mia\.html/); await p.waitForTimeout(250);
  check(/\/csm_mia\.html/.test(p.url()), "the switcher opens the other page's own address", p.url());
  await openAcc(p, "csm_mia.html");
  await p.fill("#ns-due", "2030-01-15"); await p.fill("#ns-note", note); await p.click("#ns-save"); await p.waitForTimeout(300);
  check((await p.textContent("body")).includes(note), `Mia plans a next step on ${acc} and sees it`);
  await p.reload(); await p.waitForTimeout(300);
  check((await p.textContent("body")).includes(note), "it's still there after a reload (kept in this browser)");
  const stored = await p.evaluate(() => Object.values(JSON.parse(localStorage.getItem("cockpit-sandbox-decisions") || "{}")));
  check(stored.length === 1 && stored[0].account_id === acc && stored[0].decided_by === "sandbox:csm_mia", "stored once, in this browser, signed as the sandbox page", JSON.stringify(stored));
  const team = JSON.parse(fs.readFileSync(path.join(ROOT, "data", "views_data.json"), "utf8")).team;
  const mgr = (team.find((t) => t.owner_id === "csm_mia") || {}).manager;
  await openAcc(p, mgr + ".html");
  const mgrSees = (await p.textContent("body")).includes(note);
  check(mgrSees, "her manager's page in the same browser shows it too");
  const other = await b.newContext(), q = await other.newPage();
  await openAcc(q, "csm_mia.html");
  check(!(await q.textContent("body")).includes(note), "a second visitor (a fresh browser) never sees it");
  await other.close();
  await openAcc(p, "csm_mia.html");
  await p.click("#sandbox-clear"); await p.waitForTimeout(300);
  check(!(await p.textContent("body")).includes(note) && (await p.evaluate(() => localStorage.getItem("cockpit-sandbox-decisions"))) === null,
    "Clear my changes empties it");
  check(!errors.length, "no script error while saving, switching and clearing", errors.slice(0, 3).join(" | "));
  await ctx.close();

  console.log("\nStorage blocked (private mode, strict settings)");
  const c2 = await b.newContext(), r = await c2.newPage(), e2 = watch(r);
  await r.addInitScript(() => { Storage.prototype.setItem = () => { throw new Error("blocked"); }; });
  await openAcc(r, "csm_mia.html");
  check(!(await r.isVisible("#sandbox-note")) && !(await r.$("#ns-save")) && !e2.length,
    "no banner, no forms, no script error", e2.join(" | "));
  await c2.close();

  console.log("\nThe search page (search.html): /search on the site's own address");
  {
    const ctx = await b.newContext(), s = await ctx.newPage(), errors = watch(s);
    await s.addInitScript(() => document.addEventListener("securitypolicyviolation", (e) => console.error("CSP " + e.violatedDirective + " " + e.blockedURI)));
    await s.goto(base + "search.html"); await s.waitForTimeout(200);
    const chips = await s.$$eval(".chip", (cs) => cs.map((c) => c.textContent));
    check(fs.existsSync(path.join(DIR, "search.html")) && chips.length >= 4 && /fictional and generated/.test(await s.textContent(".disclaimer")),
      "opens with the disclaimer and the example questions", JSON.stringify(chips));
    const file = fs.readFileSync(path.join(ROOT, "infra", "lambdas", "search", "documents.jsonl"), "utf8")
      .trim().split("\n").map((l) => JSON.parse(l));
    const cites = new Set(file.map((d) => d.account_id + "|" + d.id));
    await s.click(".chip"); await s.waitForSelector("#results li", { timeout: 15000 });
    const shown = await s.$$eval("#results li", (ls) => ls.map((l) => ({ head: l.querySelector(".head").textContent, marks: l.querySelectorAll("mark").length })));
    const ok = shown.every((x) => { const m = x.head.match(/^(acc_h?\d+).*\[(\w+)\]/); return m && cites.has(m[1] + "|" + m[2]); });
    check(shown.length > 0 && shown.length <= 5 && ok, `an example question: ${shown.length} accounts, each citing a document that's in the redacted file`, JSON.stringify(shown));
    check(shown.some((x) => x.marks > 0), "the matching words are highlighted");
    check(/[?]q=/.test(s.url()), "the address holds the question, so it can be shared", s.url());
    await s.fill("#q", "   "); await s.click("button.go"); await s.waitForTimeout(150);
    check(/Type a question/.test(await s.textContent("#status")) && !(await s.$("#results li")), "an empty question: a hint, no request");
    await s.fill("#q", "__busy__"); await s.click("button.go"); await s.waitForTimeout(400);
    check(/try again/i.test(await s.textContent("#status")), "the rate limit (429): a friendly 'try again', not an error");
    await s.fill("#q", "zzqx vvbn"); await s.click("button.go"); await s.waitForTimeout(2500);
    check(/No document matches/.test(await s.textContent("#status")), "no match: says so and suggests the examples");
    const page2 = await ctx.newPage(), e2 = watch(page2);
    await page2.goto(base + "search.html?q=" + encodeURIComponent("Which customers need more capacity?"));
    await page2.waitForSelector("#results li", { timeout: 15000 });
    check((await page2.$$("#results li")).length > 0, "a shared link with ?q= runs the question on opening");
    await page2.setViewportSize({ width: 375, height: 800 });
    check(await page2.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), "phone width: no sideways scroll");
    const real = errors.concat(e2).filter((e) => !/status of 429/.test(e));   // the browser's note on our test 429
    check(!real.length, "no script error or CSP violation", real.slice(0, 3).join(" | "));
    const c3 = await b.newContext(), m3 = await c3.newPage();
    await m3.goto(base + "csm_mia.html"); await m3.waitForTimeout(300);
    check(await m3.isVisible("#sandbox-note a[href='search.html']") && /fictional and generated/.test(await m3.textContent("#sandbox-note")),
      "every cockpit page's sandbox banner: the disclaimer and a link to the search");
    await c3.close(); await ctx.close();
  }

  console.log("\nThe laptop's pages stay as they were");
  for (const f of ["views.html", "pages/csm_mia.html"]) {
    const file = path.join(ROOT, "data", f);
    if (!fs.existsSync(file)) { check(false, `data/${f} exists`); continue; }
    const html = fs.readFileSync(file, "utf8"), m = html.match(/<script id="page-meta"[^>]*>([\s\S]*?)<\/script>/);
    const sandbox = m && m[1] !== "null" ? JSON.parse(m[1]).sandbox : false;
    check(!sandbox, `data/${f}: not in sandbox mode`);
  }

  await b.close(); server.close();
  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
})();
