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
    const shown = await s.$$eval("#results > li", (ls) => ls.map((l) => ({ head: l.querySelector(".head").textContent + " " + ((l.querySelector(".case .cite") || {}).textContent || ""),
                                                                      name: (l.querySelector(".head .name") || {}).textContent || "",
                                                                      marks: l.querySelectorAll("mark").length })));
    const ok = shown.every((x) => { const a = x.head.match(/(acc_h?\d+)/), d = x.head.match(/\[(\w+)\]/); return a && d && cites.has(a[1] + "|" + d[1]); });
    check(shown.length > 0 && shown.length <= 5 && ok, `an example question: ${shown.length} accounts, each citing a document that's in the redacted file`, JSON.stringify(shown));
    check(shown.some((x) => x.marks > 0), "the matching words are highlighted");
    const accLinks = await s.$$eval("#results > li", (ls) => ls.map((l) => [l.querySelector(".head a.acc").getAttribute("href"),
                                                                          l.textContent.match(/(acc_h?\d+)/)[1]]));
    check(accLinks.length === shown.length && accLinks.every(([h, id]) => h === "index.html#" + id),
      "each result's account opens its timeline in the cockpit (16 Nov 2026 review: IDs led nowhere)", JSON.stringify(accLinks));
    check(/[?]q=/.test(s.url()), "the address holds the question, so it can be shared", s.url());
    // 23 Nov 2026 (Nikodem): "acc_087 is hard for a normal person": the name first, from the cockpit
    const ACC = await s.evaluate(() => ACCOUNTS);
    check(shown.every((x) => { const id = x.head.match(/(acc_h?\d+)/)[1]; return ACC[id] && x.name === ACC[id].n; }),
      "each result leads with the account's name (then ID, tier, ARR, flags)", JSON.stringify(shown.map((x) => x.name)));
    check(Object.keys(ACC).every((id) => file.some((d) => d.account_id === id)) && file.every((d) => ACC[d.account_id]),
      `the page knows exactly the accounts in the documents (${Object.keys(ACC).length}), nothing more`);
    check(await s.evaluate(() => !highlight("CRM account: acc_037", ["acc", "037"]).includes("<mark>account")),
      "a short stem marks a whole word only: 'acc' no longer marks 'account'");
    // asking for one account (by ID, by name, with a topic): that account only (Nikodem: "no need to show anything else")
    const byDocs = {}; file.forEach((d) => { (byDocs[d.account_id] = byDocs[d.account_id] || []).push(d.id); });
    const pick = Object.keys(byDocs).sort((x, y) => byDocs[y].length - byDocs[x].length)[0];
    const onlyThat = async (q, what) => {
      await s.fill("#q", q); await s.click("button.go");
      await s.waitForFunction(() => /Only this account|No documents/.test(document.getElementById("status").textContent), null, { timeout: 15000 });
      const docs = (await s.$$eval("#results .case", (cs) => cs.map((c) => c.dataset.docs))).join(",").split(",").filter(Boolean);
      const card = await s.$eval("#results li.account-card", (l) => l.textContent).catch(() => "");
      check(card.includes(ACC[pick].n) && card.includes(pick) && docs.length && docs.every((d) => byDocs[pick].includes(d)),
        `${what}: only ${ACC[pick].n}'s documents (${docs.length}), under its card`, JSON.stringify({ card, docs }));
      return docs;
    };
    const all = await onlyThat(pick, "its ID alone");
    check(all.length === byDocs[pick].length && new Set(all).size === all.length && /newest first/.test(await s.textContent("#status")),
      "...all of them, each in exactly one case, newest case first", JSON.stringify(all));
    // 23 Nov 2026 (Nikodem): "10 emails on one case, are we showing them all?": one line per case
    const thread = file.find((d) => d.case !== d.id);                   // a document that joined an older one's case
    const mates = file.filter((d) => d.account_id === thread.account_id && d.case === thread.case).map((d) => d.id).sort();
    await s.fill("#q", thread.account_id); await s.click("button.go");
    await s.waitForFunction(() => /Only this account/.test(document.getElementById("status").textContent), null, { timeout: 15000 });
    const cards = await s.$$eval("#results .case", (cs) => cs.map((c) => ({ docs: c.dataset.docs.split(","), text: c.textContent })));
    const card = cards.find((c) => c.docs.includes(thread.id)) || { docs: [], text: "" };
    check(card.docs.slice().sort().join() === mates.join() && cards.filter((c) => c.docs.includes(thread.id)).length === 1,
      `a case shows once: ${mates.join(", ")} on ${thread.account_id} are one line`, JSON.stringify(card.docs));
    check(new RegExp(`${mates.length} messages`).test(card.text) && /Show all/.test(card.text),
      "...saying how many messages, with the history behind 'Show all'", card.text.slice(0, 200));
    const twice = file.find((d) => (d.logged_by || []).length > 1);
    check(twice && twice.logged_by.join() === "CS,Implementation"
          && file.filter((d) => d.account_id === twice.account_id && d.text === twice.text).length === 1,
      "an email logged by the CSM and by Implementation is one document in the file, with both teams", twice && twice.logged_by);
    await onlyThat(ACC[pick].n.toUpperCase(), "its name, in any case");
    await onlyThat(`show me ${ACC[pick].n} please`, "its name inside a sentence");
    await onlyThat(`${ACC[pick].n} customer call`, "its name and a topic");
    await s.fill("#q", "acc_999"); await s.click("button.go");
    await s.waitForFunction(() => /No documents/.test(document.getElementById("status").textContent), null, { timeout: 15000 });
    check(!(await s.$("#results li:not(.account-card)")), "an account with no documents: says so, shows nothing else");
    // 24 Nov 2026 (Nikodem): a leader reads by money, a CSM by relevance; and how far back
    const Q = "Which accounts are disputing an invoice?";
    const read = async () => s.$$eval("#results > li", (ls) => ls.map((l) => ({ id: l.textContent.match(/(acc_h?\d+)/)[1],
      lasts: [...l.querySelectorAll(".case")].map((c) => c.dataset.last) })));
    await s.goto(base + "search.html?view=leader&q=" + encodeURIComponent(Q));
    await s.waitForSelector("#results > li", { timeout: 20000 });
    const byValue = await read(), ARR = (id) => (ACC[id] || {}).a || 0;
    check(/biggest ARR first/.test(await s.textContent("#status")) && (await s.getAttribute('[data-order="value"]', "aria-pressed")) === "true"
          && byValue.every((x, i) => !i || ARR(byValue[i - 1].id) >= ARR(x.id)),
      "from a leader's page: biggest ARR first, among the best matches", JSON.stringify(byValue.map((x) => [x.id, ARR(x.id)])));
    await s.click('[data-order="match"]'); await s.waitForTimeout(150);
    const byMatch = await read();
    check(/best match first/.test(await s.textContent("#status")) && byMatch.length === byValue.length && /order/.test(s.url()) === false,
      "...one click: best match first (no new request; the address follows)", s.url());
    await s.goto(base + "search.html?view=csm&q=" + encodeURIComponent(Q));
    await s.waitForSelector("#results > li", { timeout: 20000 });
    check((await s.getAttribute('[data-order="match"]', "aria-pressed")) === "true" && /best match first/.test(await s.textContent("#status")),
      "from a CSM's page: best match first");
    const asOf = await s.evaluate(() => AS_OF), cut = new Date(Date.parse(asOf) - 90 * 864e5).toISOString().slice(0, 10);
    await s.click('[data-since="90"]'); await s.waitForTimeout(150);
    const recent = await read();
    check(/last 90 days/.test(await s.textContent("#status")) && recent.every((x) => x.lasts.length && x.lasts.every((d) => d >= cut))
          && /since=90/.test(s.url()),
      `"90 days": only cases with a message since ${cut} (the data's last day ${asOf}, not today)`, JSON.stringify(recent));
    await s.click('[data-since="0"]'); await s.waitForTimeout(150);
    check((await read()).length >= recent.length && !/since=/.test(s.url()), "...'All time' brings the older ones back");
    await s.fill("#q", "   "); await s.click("button.go"); await s.waitForTimeout(150);
    check(/Type a question/.test(await s.textContent("#status")) && !(await s.$("#results li")), "an empty question: a hint, no request");
    await s.fill("#q", "__busy__"); await s.click("button.go"); await s.waitForTimeout(400);
    check(/try again/i.test(await s.textContent("#status")), "the rate limit (429): a friendly 'try again', not an error");
    await s.fill("#q", "zzqx vvbn"); await s.click("button.go"); await s.waitForTimeout(2500);
    check(/Nothing in the documents is close/.test(await s.textContent("#status")), "no match: says so (in words or in meaning) and suggests the examples");
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
    // the leader's four places (23 Nov 2026): one account, then Leadership three times (report up,
    // by team, the signals), Operational twice, Did it work?; each step lights up a visible part
    const seen = [];
    for (let i = 0; i < 12; i++) {
      seen.push(await p.evaluate(() => [document.querySelector("#tour .step").textContent, document.querySelector("nav [aria-selected=true]").dataset.view,
        [...document.querySelectorAll(".tour-focus")].map((e) => (e.id || e.className.split(" ")[0]) + (e.offsetParent ? "" : ":hidden")).join()]));
      const n = await p.$('[data-tour="next"]'); if (!n) break; await n.click(); await p.waitForTimeout(250);
    }
    // and Slack (23 Nov 2026): the step after the funnel opens a task's Slack card and lights it up
    const views = seen.map((s) => s[1]), litL = seen.slice(1, 8).map((s) => s[2]);
    check(seen.length === 9 && /Step 1 of 9/.test(seen[0][0]) && views.slice(0, 8).join() === "timeline,lead,lead,lead,tasks,tasks,tasks,outcomes"
          && litL.join() === "cs-tiles,cs-teams-panel,ld-signals,tk-funnel-panel,slack-prev,op-promises-panel,oc-rules-panel",
      "the tour: 9 steps, one account, then the leader's places and Slack, each lighting up a visible part", JSON.stringify(seen));
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
    // the same for a promise typed by a person (23 Nov 2026): shown only when tracked from a
    // document (its redacted line) or made up by the generator (via cs_history)
    const typed = [], shown = [];
    for (const f of fs.readdirSync(DIR).filter((x) => x.endsWith(".html") && x !== "search.html")) {
      const mm = fs.readFileSync(path.join(DIR, f), "utf8").match(/<script id="views-data" type="application\/json">([\s\S]*?)<\/script>/);
      if (mm) for (const c of JSON.parse(mm[1]).commitments || []) {
        if (!c.doc && c.via !== "cs_history" && !/kept off the public site/.test(c.what)) typed.push(`${f}: ${c.account_id}`);
        if (c.via === "cs_history") shown.push(c.what); } }
    check(!typed.length && shown.length > 0, `no promise typed by a person on any public page; the generator's ${shown.length} show`, typed.slice(0, 3).join(", "));
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
      // 24 Nov 2026: the leader's link to the search opens it by ARR (the CSM's by best match)
      const sHref = await q.evaluate(() => {
        const a = document.querySelector('a[href="search.html"]'); if (!a) return "";
        document.addEventListener("click", (e) => e.preventDefault(), { once: true });
        a.click(); return a.getAttribute("href");
      });
      check(sHref === "search.html?view=leader", "the leader's link to the search opens it in the leader's order", sHref);
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
    // the four kinds of work (22 Nov 2026): Anna marks a to-do not needed (it leaves My work and the
    // history says so), plans a next step with an aim, and answers whether it achieved it
    {
      const w = await ctx.newPage(); const ew = []; w.on("pageerror", (e) => ew.push(e.message));
      await w.goto(base + csmDoor); await w.waitForTimeout(900);
      await w.evaluate(() => localStorage.removeItem("cockpit-sandbox-decisions")); await w.reload(); await w.waitForTimeout(900);
      await w.click("#tab-tasks"); await w.waitForTimeout(300);
      const ids = () => w.$$eval("table.queue tbody tr:not(.detail)", (r) => r.map((x) => x.querySelector("[data-open]").dataset.open));
      const before = await ids();
      await w.click(`table.queue [data-open="${before[0]}"]`); await w.waitForTimeout(500);
      await w.click("#tl-why [data-decline]"); await w.selectOption("#tl-why .dc-reason", "other"); await w.click("#tl-why .dc-save"); await w.waitForTimeout(200);
      const needNote = /note/i.test(await w.textContent("#tl-why .dc-msg"));
      await w.selectOption("#tl-why .dc-reason", "handled"); await w.click("#tl-why .dc-save"); await w.waitForTimeout(500);
      const why = (await w.textContent("#tl-why")).replace(/\s+/g, " "), hist = await w.textContent("#tl-done");
      await w.click("#tab-tasks"); await w.waitForTimeout(300);
      const after = await ids(), fin = await w.textContent("#tk-finding");
      check(needNote && /Not needed: Already handled elsewhere/.test(why) && /Marked not needed/.test(hist) && after.length === before.length - 1 && !after.includes(before[0]) && /marked not needed/.test(fin),
        `a CSM marks a to-do not needed (\"Other\" asks for a note): it leaves My work (${before.length} -> ${after.length}) and the history says so`, `${why.slice(0, 160)} | ${fin}`);
      // the next to-do; when that was the only one (25 Nov 2026, the data changed), back to the same account's page
      if (after[0]) await w.click(`table.queue [data-open="${after[0]}"]`);
      else await w.click("#tab-timeline");          // the account page it was on
      await w.waitForTimeout(500);
      await w.fill("#ns-due", await w.getAttribute("#ns-due", "min")); await w.selectOption("#ns-aim", "volume"); await w.click("#ns-save"); await w.waitForTimeout(500);
      const asked = !!(await w.$('#tl-next [data-aim="partly"]'));
      if (asked) { await w.click('#tl-next [data-aim="partly"]'); await w.waitForTimeout(500); }
      const nx = (await w.textContent("#tl-next")).replace(/\s+/g, " ");
      check(asked && /Aim: Confirm the renewal volume/.test(nx) && /Did it achieve its aim\? Partly/.test(nx) && /Aim checked/.test(await w.textContent("#tl-done")) && !ew.length,
        "a next step carries its aim; once its date comes the owner is asked, answers \"Partly\", and the history records it; no script errors", nx.slice(0, 200) + ew.join(" | "));
      await w.evaluate(() => localStorage.removeItem("cockpit-sandbox-decisions"));
      await w.close();
    }
    // commitments (22 Nov 2026): Mia tracks a promise found in Yarrow Studio's documents, keeps it,
    // and adds one already past its date; the panel counts them, the suggestion leaves the list
    {
      const c = await ctx.newPage(); const ec = []; c.on("pageerror", (e) => ec.push(e.message));
      await c.goto(base + "csm_mia.html"); await c.waitForTimeout(900);
      await c.evaluate(() => localStorage.removeItem("cockpit-sandbox-decisions")); await c.reload(); await c.waitForTimeout(900);
      await c.click("#tab-timeline"); await c.fill("#tl-find", "acc_024"); await c.keyboard.press("Enter"); await c.waitForTimeout(500);
      const sugg = () => c.$$eval("#tl-commit [data-track]", (b) => b.length);
      // counted from where the account starts: the generator plants promises too (23 Nov 2026)
      const count = async () => { const t = await c.textContent("#tl-commit .finding");
        const g = (re) => +((t.match(re) || [])[1] || 0);
        return { open: g(/(\d+) open commitment/), late: g(/, (\d+) late/), kept: g(/(\d+) kept/), t }; };
      const n0 = await count(), s0 = await sugg();
      const line = (await c.$eval("#tl-commit [data-track]", (b) => b.closest("li").textContent)).match(/"([^"]{20,60})/)[1];
      await c.click("#tl-commit [data-track]"); await c.waitForTimeout(400);
      const n1 = await count();
      const mine = await c.$$eval("#tl-commit li", (lis, l) => lis.findIndex((li) => li.textContent.includes(l) && li.querySelector('[data-result="kept"]')), line);
      await (await c.$$("#tl-commit li"))[mine].$('[data-result="kept"]').then((b) => b.click()); await c.waitForTimeout(400);
      const n2 = await count();
      await c.fill("#com-what", "Send the iOS fix timeline"); await c.fill("#com-due", "2026-10-10"); await c.click("#com-save"); await c.waitForTimeout(400);
      const n3 = await count(), fin = n3.t;
      check(s0 >= 3 && (await sugg()) === s0 - 1 && mine >= 0 && n1.open === n0.open + 1 && n2.open === n0.open && n2.kept === n0.kept + 1
        && n3.open === n0.open + 1 && n3.late === n0.late + 1
        && /Late 9 d/.test(await c.textContent("#tl-commit")) && !ec.length,
        `commitments: a promise from the documents tracked (${s0} suggestions -> ${s0 - 1}), kept, and a late one added; counted right`, fin + ec.join(" | "));
      await c.evaluate(() => localStorage.removeItem("cockpit-sandbox-decisions"));
      await c.close();
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
        [...document.querySelectorAll(".tour-focus")].map((e) => (e.id || e.className.split(" ")[0]) + (e.offsetParent ? "" : ":hidden")).join()]));
      const n = await p2.$('[data-tour="next"]'); if (!n) break; await n.click(); await p2.waitForTimeout(300);
    }
    const cviews = cs.map((x) => x[1]).join(">"), lit = cs.map((x) => x[2]);
    check(cs.length === 7 && cviews.startsWith("mybook>mybook>tasks>tasks>tasks>timeline") && lit.slice(0, 6).every((x) => x && !/hidden/.test(x)) && lit[4] === "slack-prev",
      "...7 steps: My book (twice), My work (twice), its Slack card, the account, each lighting up a visible part", JSON.stringify(cs));
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
