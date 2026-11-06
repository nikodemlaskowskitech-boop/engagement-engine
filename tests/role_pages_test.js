// Role pages in a browser (Node + Playwright): each person's page from data/pages/
// opens without a script error, on its role's first tab, with only its role's tabs
// and a page switcher; and the forecast and next-step forms appear exactly where the
// page's edit rights say (own account yes, a teammate's no, the leader never),
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
  await b.close();
  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
})();
