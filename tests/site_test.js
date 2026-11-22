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
    const accLinks = await s.$$eval("#results li .head a.acc", (as) => as.map((x) => [x.getAttribute("href"), x.textContent]));
    check(accLinks.length === shown.length && accLinks.every(([h, id]) => h === "index.html#" + id),
      "each result's account opens its timeline in the cockpit (16 Nov 2026 review: IDs led nowhere)", JSON.stringify(accLinks));
    check(/[?]q=/.test(s.url()), "the address holds the question, so it can be shared", s.url());
    await s.fill("#q", "   "); await s.click("button.go"); await s.waitForTimeout(150);
    check(/Type a question/.test(await s.textContent("#status")) && !(await s.$("#results li")), "an empty question: a hint, no request");
    await s.fill("#q", "__busy__"); await s.click("button.go"); await s.waitForTimeout(400);
    check(/try again/i.test(await s.textContent("#status")), "the rate limit (429): a friendly 'try again', not an error");
    await s.fill("#q", "zzqx vvbn"); await s.click("button.go"); await s.waitForTimeout(2500);
    check(/No document matches/.test(await s.textContent("#status")), "no match: says so and suggests the examples");
    // 16 Nov 2026: "what is my biggest churn" found nothing, with no reason why
    await s.fill("#q", "zzqx biggest churn"); await s.click("button.go"); await s.waitForTimeout(2500);
    check(/numbers/.test(await s.textContent("#status")) && (await s.getAttribute("#status a", "href")) === "index.html#lead",
      "a number question with no match: says the search doesn't read numbers, links to Leadership");
    check(/Leadership in the cockpit/.test(await s.textContent(".lede")), "the page says up front what it reads and where the numbers are");
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

  console.log("\nWho built it, and a 3-minute tour (demo review, 16 Nov 2026)");
  {
    const TOML = fs.readFileSync(path.join(ROOT, "settings.toml"), "utf8");
    const demo = (k) => (TOML.split("[demo]")[1] || "").match(new RegExp(`^${k} = "([^"]*)"`, "m"))[1];
    const ctx = await b.newContext(), p = await ctx.newPage(), errors = watch(p);
    await p.goto(base + "index.html"); await p.waitForTimeout(600);
    const strip = await p.textContent("#who-strip");
    check(await p.isVisible("#who-strip") && strip.includes(demo("author")) && (await p.getAttribute("#who-strip a", "href")).startsWith(demo("repo_url")),
      "the landing page says who built it and why, and links to the repo (from settings.toml [demo])", strip);
    const tabs = (await p.$$("nav [role=tab]")).length;
    const lede = await p.textContent(".lede");
    const WORDS = ["One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve"];
    check(lede.startsWith(`${WORDS[tabs - 1]} views`), `the intro counts the tabs on the page (${tabs}), never a typed number`, lede.slice(0, 40));
    await p.click("#tour-start"); await p.waitForTimeout(300);
    // step 1 (18 Nov 2026, recruiter review): one account from signal to renewal outcome, by rule
    const jr = await p.$$eval("#tour ol.journey li", (ls) => ls.map((l) => l.textContent));
    const jAcc = await p.$eval("#tl-account", (e) => e.value).catch(() => "");
    const data = await p.evaluate(() => JSON.parse(document.getElementById("views-data").textContent));
    const js = data.churn_signals.find((x) => x.account_id === jAcc) || {};
    const jr2 = data.renewals.find((x) => x.account_id === jAcc) || {};
    check(jr.map((t) => t.split(" ")[0]).join() === "Signal,Task,Action,Reached,Renewal,Measured"
          && js.call_date >= js.signal_date && jr2.outcome && jr.join(" ").includes(jr2.outcome),
      `...step 1 follows one account (${jAcc}) through six stages: called on or after its signal, with its real renewal outcome`, jr.join(" | "));
    const seen = [];
    for (let i = 0; i < 8; i++) {
      seen.push([(await p.textContent("#tour .step")), await p.$eval("nav [aria-selected=true]", (e) => e.dataset.view)]);
      const n = await p.$('[data-tour="next"]'); if (!n) break; await n.click(); await p.waitForTimeout(250);
    }
    const views = seen.map((s) => s[1]);
    check(seen.length === 5 && /Step 1 of 5/.test(seen[0][0]) && views.slice(0, 4).join() === "timeline,lead,tasks,outcomes",
      "the tour: 5 steps, one account through the system, Leadership, Tasks, Did it work?", JSON.stringify(seen));
    const links = await p.$$eval("#tour a", (as) => as.map((a) => a.getAttribute("href")));
    check(links.includes("search.html") && links.some((h) => /^csm_\w+\.html$/.test(h)), "...ending on a CSM's page and the search", links.join(", "));
    await p.click('[data-tour="close"]');
    check(await p.isHidden("#tour") && !errors.length, "...and it closes, with no script errors", errors.join(" | "));
    const ph = await ctx.newPage(); await ph.setViewportSize({ width: 390, height: 844 });
    await ph.goto(base + "index.html"); await ph.waitForTimeout(500);
    // the page's headline since 21 Nov 2026 is the Performance band's (GRR and NRR), first on the page
    const top = await ph.$eval("#cs-finding", (e) => e.getBoundingClientRect().top);
    check(top < 844 - 60, `phone: the Leadership headline (GRR and NRR) is in the first screen (top ${Math.round(top)} px of 844)`);
    await ph.close();
    // a meeting's free-text note is typed in Slack and may hold anything: never on the public site (18 Nov 2026)
    const notes = [];
    for (const f of fs.readdirSync(DIR).filter((x) => x.endsWith(".html") && x !== "search.html")) {
      const mm = fs.readFileSync(path.join(DIR, f), "utf8").match(/<script id="views-data" type="application\/json">([\s\S]*?)<\/script>/);
      if (mm) for (const t of JSON.parse(mm[1]).touches || []) if (t.note && t.source !== "cs_history") notes.push(`${f}: ${t.account_id} (${t.source})`); }
    check(!notes.length, "no note typed in Slack on any public page (the generator's made-up notes may show)", notes.slice(0, 3).join(", "));
    // two doors instead of a list of every person (18 Nov 2026): Leader | CSM, the rest under "More people"
    const csmDoor = demo("csm") + ".html";
    const door = async (pg) => ({ shown: await pg.isVisible("#doors"),
      current: await pg.$$eval("#doors a[aria-current=page]", (as) => as.map((a) => a.id)),
      csm: await pg.getAttribute("#door-csm", "href"), leader: await pg.getAttribute("#door-leader", "href"),
      first: await pg.$eval("#page-switch", (s) => s.options[s.selectedIndex].text) });
    let d = await door(p);
    check(d.shown && d.current.join() === "door-leader" && d.csm === csmDoor && d.leader === "leader.html#lead"
          && d.first === "More people\u2026",
      `landing page: Leader | CSM doors, Leader current, CSM opens ${csmDoor}, the list says "More people…"`, JSON.stringify(d));
    await p.click("#door-csm"); await p.waitForTimeout(600);
    d = await door(p);
    check(new URL(p.url()).pathname.endsWith(csmDoor) && d.current.join() === "door-csm" && d.first === "More people\u2026",
      "...the CSM door opens that CSM's book, marked current", `${p.url()} ${JSON.stringify(d)}`);
    const other = csmDoor === "csm_mia.html" ? ["csm_tom.html", /Tom/] : ["csm_mia.html", /Mia/];   // anyone but the door person
    await p.selectOption("#page-switch", other[0]); await p.waitForTimeout(600);
    d = await door(p);
    check(new URL(p.url()).pathname.endsWith(other[0]) && !d.current.length && other[1].test(d.first) && (await p.textContent("#page-switch-label")) === "Viewing as",
      '..."More people" opens anyone else: no door current, "Viewing as" that person', `${p.url()} ${JSON.stringify(d)}`);
    // About this project (18 Nov 2026, recruiter review): in place, from settings [demo.about]
    const pa = await ctx.newPage(); await pa.goto(base + "index.html"); await pa.waitForTimeout(500);
    const hiddenFirst = await pa.isHidden("#about");
    await pa.click("#about-open"); await pa.waitForTimeout(150);
    const heads = await pa.$$eval("#about h3", (hs) => hs.map((h) => h.textContent));
    const aboutText = await pa.textContent("#about");
    const mine = (TOML.split("[demo.about]")[1] || "").match(/^mine = "((?:[^"\\]|\\.)*)"/m)[1].replace(/\\"/g, '"');
    check(hiddenFirst && await pa.isVisible("#about") && heads.join() === "The problem,The approach,What I did,The technology,The limits"
          && aboutText.includes(mine) && /Claude Code/.test(aboutText) && (await pa.getAttribute("#about-open", "aria-expanded")) === "true",
      "About this project: closed at first, opens in place with the five parts from [demo.about], Claude Code named", heads.join());
    await pa.click("#about-close"); await pa.waitForTimeout(100);
    check(await pa.isHidden("#about") && await pa.isVisible("#ld-finding"), "...closes, and the cockpit was never behind it");
    await pa.goto(base + "index.html?about"); await pa.waitForTimeout(500);
    check(await pa.isVisible("#about"), "...and ?about opens it straight away (a link to send)");
    await pa.close();
    const pw = await ctx.newPage(); await pw.setViewportSize({ width: 390, height: 844 });
    await pw.goto(base + "index.html"); await pw.waitForTimeout(500);
    const wide = await pw.evaluate(() => document.documentElement.scrollWidth);
    check(await pw.isVisible("#doors") && wide <= 390, `phone: the doors fit, no side scroll (${wide} px)`);
    await pw.close();
    // the managers' queues (21 Nov 2026, the leadership assessment, step 3): the leader gets one
    // line on where a manager is needed, a manager's page keeps the lists
    {
      const q = await ctx.newPage(); const eq = []; q.on("pageerror", (e) => eq.push(e.message));
      await q.goto(base + "leader.html#lead"); await q.waitForTimeout(800);
      const L = await q.evaluate(() => ({ line: document.getElementById("ld-ops-line").hidden ? "" : document.getElementById("ld-ops-finding").textContent,
        lists: !document.getElementById("ld-ops-reach").hidden || !document.getElementById("ld-ops-grid").hidden }));
      check(!L.lists && /past their reach deadline|late \(open past|with no CSM/.test(L.line), "the leader: no queue lists, one line on where a manager is needed", L.line);
      // one clock for a late task (the definitions pass, 22 Nov 2026): the line and Tasks agree
      const lineLate = +((L.line.match(/(\d+) tasks? late/) || [])[1] || 0);
      await q.click("#tab-tasks"); await q.waitForTimeout(300);
      const tasksLate = +(((await q.textContent("#tk-finding")) || "").match(/(\d+) of them late/) || [])[1] || 0;
      check(lineLate === tasksLate, `the leader: ${lineLate} tasks late on Leadership, ${tasksLate} on Tasks: one rule`, `${lineLate} vs ${tasksLate}`);
      const gl = await q.evaluate(() => document.querySelectorAll("#ld-glossary-list dt").length);
      check(gl >= 6, `the leader: the glossary lists ${gl} definitions`);
      await q.goto(base + "mgr_james.html#lead"); await q.waitForTimeout(800);
      const M = await q.evaluate(() => ({ line: !document.getElementById("ld-ops-line").hidden, reach: !document.getElementById("ld-ops-reach").hidden, grid: !document.getElementById("ld-ops-grid").hidden }));
      check(!M.line && M.reach && M.grid && !eq.length, "a manager: the full queue lists, no summary line, no script errors", JSON.stringify(M) + eq.join(" | "));
      await q.close();
    }
    // a CSM's page has its own walkthrough (21 Nov 2026): where I stand, what's coming, what to pick
    // up, the account, then the leader's page it rolls up to; each step lights up its part
    const p2 = await ctx.newPage(); const e2 = []; p2.on("pageerror", (e) => e2.push(e.message));
    await p2.setViewportSize({ width: 1280, height: 900 });
    await p2.goto(base + csmDoor); await p2.waitForTimeout(600);
    check(await p2.isVisible("#who-strip") && (await p2.textContent("#tour-start")) === "Take the 2-minute tour", "a CSM's page: who built it, and its own tour");
    await p2.click("#tour-start"); await p2.waitForTimeout(400);
    const cs = [];
    for (let i = 0; i < 10; i++) {
      cs.push(await p2.evaluate(() => [document.querySelector("#tour .step").textContent, document.querySelector("nav [aria-selected=true]").dataset.view,
        [...document.querySelectorAll(".tour-focus")].map((e) => e.id + (e.offsetParent ? "" : ":hidden")).join()]));
      const n = await p2.$('[data-tour="next"]'); if (!n) break; await n.click(); await p2.waitForTimeout(300);
    }
    const cviews = cs.map((x) => x[1]).join(">"), lit = cs.map((x) => x[2]);
    check(cs.length === 6 && cviews.startsWith("mybook>mybook>tasks>tasks>timeline") && lit.slice(0, 5).every((x) => x && !/hidden/.test(x)),
      "...6 steps: My book (twice), My work (twice), the account, each lighting up a visible part", JSON.stringify(cs));
    const l2 = await p2.$$eval("#tour a", (as) => as.map((a) => a.getAttribute("href")));
    check(l2.includes("leader.html") && l2.includes("search.html"), "...and ends on the leader's page it rolls up to, and the documents", JSON.stringify(l2));
    await p2.click('[data-tour="close"]');
    check(await p2.isHidden("#tour") && !(await p2.$(".tour-focus")) && !e2.length, "...it closes, the highlight goes, no script errors", e2.join(" | "));
    await ctx.close();
  }

  console.log("\nThe laptop's pages stay as they were");
  for (const f of ["views.html", "pages/csm_mia.html"]) {
    const file = path.join(ROOT, "data", f);
    if (!fs.existsSync(file)) { check(false, `data/${f} exists`); continue; }
    const html = fs.readFileSync(file, "utf8"), m = html.match(/<script id="page-meta"[^>]*>([\s\S]*?)<\/script>/);
    const sandbox = m && m[1] !== "null" ? JSON.parse(m[1]).sandbox : false;
    check(!sandbox, `data/${f}: not in sandbox mode`);
    check(!/id="who-strip"[^>]*>[^<]/.test(html), `data/${f}: no who-built-it strip (the in-house cockpit isn't a demo)`);
    check(!/<option value="" selected>More people/.test(html) && /<span id="page-switch-label">Viewing as<\/span>/.test(html),
      `data/${f}: the in-house "Viewing as" list, no demo doors`);
    const meta = m && m[1] !== "null" ? JSON.parse(m[1]) : {};
    check(!meta.demo && /<section class="about" id="about" hidden/.test(html), `data/${f}: no About (no demo settings in the page; the section stays hidden)`);
  }

  await b.close(); server.close();
  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
})();
