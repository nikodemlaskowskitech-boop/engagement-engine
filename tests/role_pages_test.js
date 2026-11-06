// Role pages in a browser (Node + Playwright): each person's page from data/pages/
// opens without a script error, on its role's first tab, with only its role's tabs
// and a page switcher; and the forecast and next-step forms appear exactly where the
// page's edit rights say (own account yes, a teammate's no, the leader never), and
// so do reassign, hand-back and the handover Confirm (code review, 6 Nov 2026),
// against a stand-in database. tests/roles_test.py checks the files themselves
// (written from ROLES.md by a separate agent); this checks they work.
//   node tests/role_pages_test.js        (run python roles.py or export_views.py first)
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");
const DIR = path.join(__dirname, "..", "data", "pages");
const FAKE_DB = fs.readFileSync(path.join(__dirname, "cockpit_test.js"), "utf8").match(/const FAKE_DB = `([\s\S]*?)`;/)[1];
let passed = 0, failed = 0;
const check = (ok, name, detail = "") => { ok ? passed++ : failed++; console.log(`  ${ok ? "PASS" : "FAIL"}  ${name}${!ok && detail ? "  ->  " + detail : ""}`); };
const meta = (f) => JSON.parse(fs.readFileSync(path.join(DIR, f), "utf8").match(/<script id="page-meta" type="application\/json">([\s\S]*?)<\/script>/)[1]);

(async () => {
  if (!fs.existsSync(DIR)) { console.log("  FAIL  data/pages/ is missing: run python roles.py"); process.exit(1); }
  const b = await chromium.launch();
  const open = async (f, hash = "", db = false) => {
    const p = await b.newPage(); const errors = [];
    p.on("pageerror", (e) => errors.push(e.message));
    p.on("console", (m) => { if (m.type() === "error" && !/ERR_|net::|fonts/.test(m.text())) errors.push(m.text()); });
    if (db) await p.addInitScript(FAKE_DB);
    await p.goto("file://" + path.join(DIR, f) + (hash ? "#" + hash : "")); await p.waitForTimeout(300);
    return { p, errors };
  };
  console.log("\nEvery page opens, on its role's first tab, with only its tabs");
  for (const f of fs.readdirSync(DIR).filter((x) => x.endsWith(".html")).sort()) {
    const m = meta(f), { p, errors } = await open(f);
    const tabs = await p.$$eval("nav button[data-view]", (bs) => bs.map((x) => x.dataset.view));
    const first = await p.$eval('nav button[aria-selected="true"]', (x) => x.dataset.view).catch(() => null);
    for (const t of tabs) await p.click(`#tab-${t}`);
    const sw = await p.$$eval("#page-switch option", (o) => o.length);
    check(!errors.length && JSON.stringify(tabs) === JSON.stringify(m.tabs) && first === m.tabs[0] && sw > 1,
      `${f}: opens on ${m.tabs[0]}, ${tabs.length} tabs, every tab draws, switcher`, JSON.stringify({ errors, tabs, first, sw }));
    await p.close();
  }
  console.log("\nForms only where the person may act (stand-in database)");
  for (const f of ["csm_mia.html", "pm_jonas.html", "mgr_sophie.html", "leader.html"]) {
    const m = meta(f), mine = m.edit.forecast || [], other = m.accounts.find((a) => !mine.includes(a));
    for (const [acc, want] of [[mine[0], true], [other, false]]) {
      if (!acc) continue;
      const { p } = await open(f, acc, true);
      const fc = !!(await p.$("#fc-save")), ns = !!(await p.$("#ns-save"));
      check(fc === want && ns === want, `${f}: ${acc} ${want ? "can" : "can't"} be forecast or planned here`, `forecast ${fc}, next step ${ns}`);
      await p.close();
    }
  }
  console.log("\nTask decisions only for that manager's own team (stand-in database)");
  for (const f of ["mgr_sophie.html", "mgr_james.html", "csm_mia.html", "leader.html"]) {
    const m = meta(f), { p } = await open(f, "tasks", true);
    if (!m.tabs.includes("tasks")) { await p.close(); continue; }
    await p.click("#tab-tasks");
    for (let i = 0; i < 4; i++) {                       // every status (the chips redraw after each click)
      const chip = await p.$("#tk-status .chip[aria-pressed=false]"); if (!chip) break; await chip.click(); }
    const rows = await p.$$eval("#tk-list tbody tr", (trs) => trs.map((tr) => ({
      acc: (tr.querySelector("[data-open]") || {}).dataset ? tr.querySelector("[data-open]").dataset.open : null,
      act: !!tr.querySelector("select[data-assign], [data-approve]") })));
    const wrong = rows.filter((r) => r.act && !(m.edit.assign || []).includes(r.acc) && !(m.edit.handback || []).includes(r.acc));
    const some = rows.some((r) => r.act);
    check(!wrong.length && (m.role === "manager" ? some : !some),
      `${f}: ${m.role === "manager" ? "reassign or hand-back offered only on its team's tasks" : "no reassign or hand-back at all"}`,
      JSON.stringify({ rows: rows.length, offered: rows.filter((r) => r.act).length, wrong: wrong.slice(0, 3) }));
    await p.close();
  }
  console.log("\nHandover Confirm only for the outgoing owner or their manager (stand-in database)");
  for (const [f, acc, want] of [["csm_mia.html", "acc_024", true], ["csm_lukas.html", "acc_024", false],
                                ["mgr_sophie.html", "acc_024", true], ["leader.html", "acc_024", false],
                                ["pm_priya.html", "acc_156", true], ["pm_owen.html", "acc_156", false]]) {
    const { p } = await open(f, acc, true);
    const btn = !!(await p.$("#ho-confirm"));
    check(btn === want, `${f}: ${acc} handover ${want ? "can" : "can't"} be confirmed here`, `button ${btn}`);
    await p.close();
  }
  console.log("\nMy book on a CSM's page: the same medians as the full cockpit, a small book in full sight");
  {
    const full = path.join(DIR, "..", "views.html");
    if (fs.existsSync(full)) {
      const read = async (url, owner) => { const p = await b.newPage(); await p.goto(url); if (owner) await p.selectOption("#seg-owner", owner);
        await p.click("#tab-mybook"); await p.waitForTimeout(250);
        const r = await p.evaluate(() => ({ med: [...document.querySelectorAll("#mb-work .tile .ref, #mb-eng-tiles .tile .ref")].map((x) => x.innerText).join(" | "),
          eng: document.getElementById("mb-eng-finding").innerText.replace(/.*median/, ""), rows: document.querySelectorAll("#mb-port tbody tr.click").length }));
        await p.close(); return r; };
      const a = await read("file://" + full, "csm_mia"), m = await read("file://" + path.join(DIR, "csm_mia.html"));
      check(a.med === m.med && a.eng === m.eng, "Mia's page: every 'median for CSMs' matches the full cockpit (book-wide, not her team's)", JSON.stringify({ full: a, page: m }));
      check(m.rows === meta("csm_mia.html").edit.forecast.length, "Mia's 8 accounts all listed, the 'No flags' ones too", `${m.rows} rows`);
    } else console.log("  SKIP  no data/views.html to compare with");
  }
  console.log("\nThe switcher keeps the database (published: only the main page gets it)");
  {
    const http = require("http");
    const server = http.createServer((q, r) => { const f = path.join(DIR, path.basename(q.url.split("?")[0]) || "index.html");
      if (!fs.existsSync(f)) { r.writeHead(404); return r.end(); } r.writeHead(200, { "Content-Type": "text/html" }); r.end(fs.readFileSync(f)); });
    await new Promise((ok) => server.listen(0, ok));
    const p = await b.newPage(), errors = [];
    p.on("pageerror", (e) => errors.push(e.message));
    await p.goto(`http://localhost:${server.address().port}/index.html`);
    // the runtime given once, to the main page only, with live subscriptions counted
    await p.evaluate(() => {
      const docs = {}, subs = new Set();
      const snap = () => ({ docs: Object.entries(docs).map(([id, v]) => ({ id, data: () => v })) });
      const db = { collection: () => ({ onSnapshot: (f) => { subs.add(f); f(snap()); return () => subs.delete(f); },
        doc: (id) => ({ set: async (v) => { docs[id] = v; subs.forEach((f) => f(snap())); }, delete: async () => {} }) }) };
      window.__subs = subs; window.claude = { use: async (n) => n === "db" ? db : n === "user" ? { id: async () => "tester" } : null };
    });
    const person = () => p.evaluate(() => JSON.parse(document.getElementById("page-meta").textContent).person);
    const switchTo = async (f) => { await p.selectOption("#page-switch", f); await p.waitForTimeout(600); };
    await switchTo("mgr_sophie.html");
    await p.click("#tab-tasks");
    const assigns = await p.$$eval("select[data-assign]", (x) => x.length);
    check(await person() === "mgr_sophie" && assigns > 0, "switch to Sophie: her page, and her reassign controls work (database reached)", `${await person()}, ${assigns} controls`);
    await switchTo("csm_mia.html");
    const mine = meta("csm_mia.html").edit.forecast[0];
    await p.click("#tab-timeline");
    await p.fill("#tl-find", mine); await p.keyboard.press("Enter"); await p.waitForTimeout(300);
    check(await person() === "csm_mia" && !!(await p.$("#fc-save")) && !!(await p.$("#ns-save")),
      `then to Mia: her forecast and next-step forms on ${mine}`, `${await person()}`);
    check(await p.evaluate(() => window.__subs.size) === 1, "one live database subscription after two switches (the old page's is stopped)");
    check(p.url().split("#")[0].endsWith("/index.html") && !errors.length, "still on the main page, no script error", `${p.url()} ${errors}`);
    await p.close(); server.close();
  }
  await b.close();
  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
})();
