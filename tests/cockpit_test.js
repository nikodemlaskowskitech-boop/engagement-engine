// Cockpit test: opens data/views.html in a headless browser, the way a person
// would, and checks that it works, that its numbers agree with the export and
// with each other, that the segment bar is respected, and that the buttons save.
//
//   node tests/cockpit_test.js            (after python export_views.py)
//
// Needs Node and Playwright with Chromium (npm i -g playwright; npx playwright
// install chromium). Exit code 1 if anything fails, so it can gate a merge.
// The saving tests use a stand-in for the published page's database.

const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const ROOT = path.resolve(__dirname, "..");
const PAGE = "file://" + path.join(ROOT, "data", "views.html");
const D = JSON.parse(fs.readFileSync(path.join(ROOT, "data", "views_data.json"), "utf8"));
const TABS = ["overview", "lead", "mybook", "tasks", "outcomes", "timeline", "onboarding", "team", "book"];

let passed = 0, failed = 0;
const check = (ok, name, detail = "") => {
  ok ? passed++ : failed++;
  console.log(`  ${ok ? "PASS" : "FAIL"}  ${name}${!ok && detail ? "  ->  " + detail : ""}`);
};

// the page's own money format, to compare like with like
const CUR = D.settings.currency === "EUR" ? "€" : (D.settings.currency || "") + " ";
const money = (v) => Math.abs(v) >= 1e6 ? CUR + (v / 1e6).toFixed(1).replace(/\.0$/, "") + "M"
  : Math.abs(v) >= 1000 ? CUR + Math.round(v / 1000) + "k" : CUR + Math.round(v);
const churnValue = (a) => ((a.flags.find((f) => f.flag === "churn") || {}).value) || 0;

// a stand-in for the page database (the published cockpit's decisions store)
const FAKE_DB = `
  (() => {
    const docs = {}; let cb = null;
    const snap = () => ({ docs: Object.entries(docs).map(([id, v]) => ({ id, data: () => v })) });
    const db = { collection: () => ({
      onSnapshot: (f) => { cb = f; f(snap()); },
      doc: (id) => ({ set: async (v) => { docs[id] = v; cb && cb(snap()); },
                      delete: async () => { delete docs[id]; cb && cb(snap()); } }) }) };
    window.__docs = docs; window.__db = db;
    window.claude = { use: async (n) => n === "db" ? db : n === "user" ? { id: async () => "tester" } : null };
  })();`;

async function open(browser, opts = {}) {
  const page = await browser.newPage({ viewport: { width: opts.width || 1300, height: 900 }, colorScheme: opts.scheme || "light" });
  page.setDefaultTimeout(5000);                        // a missing element fails fast instead of hanging
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => { if (m.type() === "error" && !/fonts\.g|ERR_|net::/.test(m.text())) errors.push(m.text()); });
  if (opts.db) await page.addInitScript(FAKE_DB);
  await page.addInitScript(() => { try { localStorage.clear(); } catch (e) { /* none */ } });
  await page.goto(PAGE + (opts.hash || ""));
  await page.waitForTimeout(700);
  return { page, errors };
}
const tab = async (page, t) => { await page.click("#tab-" + t); await page.waitForTimeout(150); };
const segment = async (page, values) => {
  await page.click("#seg-reset").catch(() => {});
  for (const [k, v] of Object.entries(values)) { await page.selectOption("#seg-" + k, v); await page.waitForTimeout(120); }
};

(async () => {
  const browser = await chromium.launch();

  // --- 1. It works: every tab, light and dark, desktop and phone ----------------
  console.log("\n1. Every tab draws, with no script errors and no sideways scrolling");
  for (const [scheme, width] of [["light", 1300], ["dark", 1300], ["light", 390], ["dark", 390]]) {
    const { page, errors } = await open(browser, { scheme, width });
    const problems = [];
    for (const t of TABS) {
      await tab(page, t);
      const visible = await page.isVisible("#view-" + t);
      const sw = await page.evaluate(() => document.documentElement.scrollWidth);
      if (!visible) problems.push(`${t} hidden`);
      if (sw > width + 1) problems.push(`${t} scrolls sideways (${sw}px)`);
    }
    check(!problems.length && !errors.length, `${scheme}, ${width}px wide`, [...problems, ...errors].join("; "));
    await page.close();
  }

  // --- 2. The numbers agree with the export and with each other ------------------
  console.log("\n2. Numbers agree with the export and across tabs");
  {
    const { page } = await open(browser);
    const people = D.accounts.filter((a) => a.tier !== "Self-service");
    const atRisk = people.reduce((t, a) => t + churnValue(a), 0);
    const act = people.filter((a) => a.flags.some((f) => f.flag === "churn" && f.status === "Act")).length;
    await tab(page, "lead");
    const tiles = await page.textContent("#ld-tiles");
    check(tiles.includes(money(atRisk)), `Leadership ARR at risk = the export (${money(atRisk)})`, tiles.slice(0, 80));
    check(tiles.includes(`${act} at Act`), `Leadership Act count = the export (${act})`);

    // one CSM: My book and Leadership tell the same story
    const mia = D.accounts.filter((a) => a.owner_id === "csm_mia");
    const miaRisk = mia.reduce((t, a) => t + churnValue(a), 0);
    await segment(page, { owner: "csm_mia" });
    await tab(page, "mybook");
    const port = await page.textContent("#mb-port-finding");
    check(port.startsWith(money(miaRisk)), `My book at risk for Mia = the export (${money(miaRisk)})`, port);
    await tab(page, "lead");
    const lead = await page.textContent("#ld-finding");
    check(lead.includes(money(miaRisk)), "Leadership for Mia = My book for Mia", lead);

    // tasks: open count on the Tasks tab = the export
    await segment(page, {});
    await tab(page, "tasks");
    const openCount = D.tasks.filter((t) => t.action_status === "Open").length;
    const chips = await page.textContent("#tk-status");
    check(chips.includes(`Open ${openCount}`), `Tasks open = the export (${openCount})`, chips);

    // the backtest panel: ARR really lost = the export
    await tab(page, "outcomes");
    const lost = D.renewals.filter((r) => r.flagged).reduce((t, r) => t + (r.lost_arr || 0), 0);
    const oc = await page.textContent("#oc-value-finding");
    check(oc.includes(money(lost)), `Did it work? flagged ARR lost = the export (${money(lost)})`, oc);
    await page.close();
  }

  // --- 3. The segment bar is respected on every tab ----------------------------
  console.log("\n3. With Owner = Mia, every account shown is hers (or marked as outside)");
  {
    const { page } = await open(browser);
    const mine = new Set(D.accounts.filter((a) => a.owner_id === "csm_mia").map((a) => a.account_id));
    await segment(page, { owner: "csm_mia" });
    const ids = async (sel) => page.$$eval(sel, (els) => els.map((e) => e.dataset.id || e.dataset.open).filter(Boolean));
    const notMine = (list) => list.filter((id) => !mine.has(id));

    await tab(page, "mybook");
    let list = await ids("#mb-port tr.click");
    check(list.length && !notMine(list).length, `My book portfolio (${list.length} rows)`, notMine(list).join(", "));

    await tab(page, "lead");
    for (const host of ["#ld-churn", "#ld-growth"]) {
      const more = await page.$(host + " [data-more]"); if (more) await more.click();
      list = await ids(host + " tr.click");
      check(!notMine(list).length, `Leadership ${host.slice(4)} list (${list.length} rows)`, notMine(list).join(", "));
    }
    await tab(page, "tasks");
    list = await ids("#tk-list [data-open]");
    check(!notMine(list).length, `Tasks list (${list.length} rows)`, notMine(list).join(", "));

    await tab(page, "timeline");
    const shown = await page.inputValue("#tl-account");
    check(mine.has(shown), "Timeline opens one of her accounts", shown);
    const chips = await page.$$eval("#tl-picks .chip", (els) => els.map((e) => e.id.replace("pick-", "")));
    check(chips.length && !notMine(chips).length, `Timeline example chips (${chips.length})`, notMine(chips).join(", "));

    await page.click("#seg-find"); await page.waitForTimeout(150);
    list = await ids("#seg-find-list li[data-id]");
    check(list.length && !notMine(list).length, `Find box offers only her accounts (${list.length})`, notMine(list).join(", "));

    // narrowing filters: Mia -> only her manager, team and tier
    const opts = async (k) => (await page.$$eval(`#seg-${k} option`, (os) => os.map((o) => o.value))).filter(Boolean);
    check(JSON.stringify(await opts("manager")) === '["mgr_sophie"]', "Manager list narrows to Sophie");
    check(JSON.stringify(await opts("tier")) === '["Enterprise"]', "Tier list narrows to Enterprise");

    // an account from outside the segment is marked
    const other = D.accounts.find((a) => a.owner_id === "csm_tom");
    await page.fill("#tl-find", other.account_id); await page.keyboard.press("Enter"); await page.waitForTimeout(200);
    check((await page.textContent("#tl-finding")).startsWith("Outside"), "An account outside the segment is marked 'Outside'");
    await page.close();
  }

  // --- 4. The buttons save (stand-in database) --------------------------------
  console.log("\n4. Saving: next step, forecast, reassignment");
  {
    const pool = D.accounts.find((a) => a.tier === "Scale" && a.flags.some((f) => f.flag === "churn" && f.status === "Act"));
    const { page, errors } = await open(browser, { db: true, hash: "#" + pool.account_id });
    await page.click("#tl-next [data-actas=owner]"); await page.waitForTimeout(250);
    check(await page.isVisible("#ns-save"), `'Act as' the owner opens the forms (${pool.owner})`);
    await page.click("#ns-save"); await page.waitForTimeout(100);
    check((await page.textContent("#ns-msg")).includes("Pick a date"), "A next step without a date is refused");
    const due = new Date(Date.parse(D.as_of) + 10 * 864e5).toISOString().slice(0, 10);
    await page.fill("#ns-due", due); await page.fill("#ns-note", "Test call"); await page.click("#ns-save"); await page.waitForTimeout(250);
    check((await page.textContent("#tl-next")).includes("Test call"), "A next step saves and shows");
    check((await page.textContent("#tl-sum-line")).includes("Next:"), "The summary shows the next step");

    await page.click("#fc-save"); await page.waitForTimeout(100);
    check((await page.textContent("#fc-msg")).includes("reason"), "A forecast without a reason is refused");
    await page.selectOption("#fc-call", "downsize");
    await page.fill("#fc-amount", String(Math.round(pool.arr * 2))); await page.fill("#fc-reason", "Test"); await page.click("#fc-save"); await page.waitForTimeout(100);
    check((await page.textContent("#fc-msg")).includes("between"), "A downsize above today's ARR is refused");
    await page.fill("#fc-amount", String(Math.round(pool.arr / 2))); await page.click("#fc-save"); await page.waitForTimeout(250);
    check((await page.textContent("#tl-forecast")).includes("Downsize to"), "A forecast saves and shows");

    // the manager gives an open task to someone else
    const t = D.tasks.find((x) => x.action_status === "Open" && x.manager === "mgr_sophie");
    await segment(page, { manager: "mgr_sophie" });
    await tab(page, "tasks");
    const select = await page.$("#tk-list select[data-assign]");
    if (select) {
      const to = await select.$$eval("option", (os) => os.map((o) => o.value).filter(Boolean)[0]);
      await select.selectOption(to); await page.waitForTimeout(250);
      check((await page.textContent("#tk-list")).includes("on the next run"), "A reassignment saves and shows 'Going to …'");
    } else check(!t, "A reassignment can be made (no open task for Sophie in this data)");
    const saved = await page.evaluate(() => Object.values(window.__docs).map((d) => d.kind));
    check(["next_step", "forecast"].every((k) => saved.includes(k)), "The database holds what was saved", saved.join(", "));
    check(!errors.length, "No script errors while saving", errors.join("; "));
    await page.close();
  }

  // --- 5. Edge cases -----------------------------------------------------------
  console.log("\n5. Edge cases");
  {
    const { page, errors } = await open(browser, { db: true });
    await tab(page, "timeline");
    const self = D.accounts.find((a) => a.tier === "Self-service");
    await page.fill("#tl-find", self.account_id); await page.keyboard.press("Enter"); await page.waitForTimeout(200);
    check(!(await page.textContent("#tl-next")).trim(), "Self-service: no plan or forecast form (no owner to plan)");
    const orphan = D.accounts.find((a) => a.tier === "Enterprise" && !a.owner_id);
    if (orphan) {
      await page.fill("#tl-find", orphan.account_id); await page.keyboard.press("Enter"); await page.waitForTimeout(200);
      check((await page.textContent("#tl-next")).includes("No owner assigned"), "An account with no owner says CS ops must assign one");
    }
    await segment(page, { region: "Nordics", tier: "Self-service" });
    for (const t of ["lead", "mybook", "team", "book", "outcomes"]) await tab(page, t);
    check(!errors.length, "A small segment (Nordics Self-service) draws every tab without errors", errors.join("; "));
    await page.click("#seg-reset");
    const pickedPool = D.team.find((t) => t.owner_type === "pool");
    await segment(page, { team: pickedPool.region, tier: "Scale" });
    await tab(page, "mybook");
    check((await page.textContent("#mb-finding")).includes(pickedPool.owner_name), "Team + Scale opens the pool's book");
    await page.close();
  }

  // --- 6. Handover brief (AI) ------------------------------------------------------
  console.log("\n6. Handover brief: sources, privacy, confirm");
  {
    const H = D.handovers || {}, accs = Object.keys(H);
    const none = D.accounts.find((a) => !H[a.account_id] && a.tier === "Enterprise");
    const errors = [];
    const visit = async (id) => { const o = await open(browser, { db: true, hash: "#" + id }); o.page.on("pageerror", (e) => errors.push(e.message)); return o.page; };
    let page = await visit(none.account_id);
    check(await page.isHidden("#tl-handover"), "No brief: no handover panel");
    if (!accs.length) console.log("  SKIP  no briefs in the export (run handover.py, then export_views.py)");
    for (const id of accs) {
      const h = H[id];
      await page.close(); page = await visit(id);
      check(await page.isVisible("#tl-handover"), `${id}: the panel shows`);
      const cites = await page.$$eval("#tl-handover .ho-brief .cite", (bs) => bs.map((b) => b.dataset.src));
      const ids = new Set(h.sources.map((x) => x.id));
      check(cites.length > 0 && cites.every((c) => ids.has(c)), `${id}: ${cites.length} citations, all to real sources`);
      const brief = await page.textContent("#tl-handover .ho-brief");
      check(!/Customer [A-Z]\b|\bCS \d\b/.test(brief), `${id}: real names are back in the brief (no 'Customer A' or 'CS 1' left)`);
      // privacy in the page: what the model saw holds no real name, email or phone
      const keyFile = path.join(ROOT, "data", "handover", id + "_key.json");
      const seen = await page.textContent("#ho-sent");
      if (fs.existsSync(keyFile)) {
        const key = JSON.parse(fs.readFileSync(keyFile, "utf8"));
        const leaks = Object.entries(key).flatMap(([label, real]) => [real, ...(label === "the customer" ? [] : [real.split(" ")[0]])])
          .filter((w) => new RegExp("\\b" + w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "\\b").test(seen));
        leaks.push(...(seen.match(/[\w.+-]+@[\w-]+\.[\w.-]+/g) || []), ...(seen.match(/\+\d[\d ()-]{7,}\d/g) || []));
        check(!leaks.length, `${id}: 'What the model saw' shows no real name, email or phone`, leaks.join(", "));
      }
      const c = cites.find((x) => x !== "DATA") || cites[0];
      await page.click(`#tl-handover .cite[data-src=${c}]`); await page.waitForTimeout(300);
      check(await page.evaluate((c) => document.getElementById("ho-sent").open && document.getElementById("ho-src-" + c).classList.contains("hit"), c),
        `${id}: clicking [${c}] opens that source`);
    }
    if (accs.length) {
      const id = accs[0], h = H[id], a = D.accounts.find((x) => x.account_id === id);
      await page.close(); page = await visit(id);
      const other = D.team.find((t) => t.owner_type === "csm" && t.owner_id !== h.from);
      await segment(page, { owner: other.owner_id });
      await tab(page, "timeline"); await page.fill("#tl-find", id); await page.keyboard.press("Enter"); await page.waitForTimeout(300);
      check(!(await page.isVisible("#ho-confirm")), `${other.owner_name} (not the outgoing owner) can't confirm`);
      await page.click("#tl-handover [data-actas=owner]"); await page.waitForTimeout(300);
      check(await page.isVisible("#ho-confirm"), `Acting as ${a.owner} opens Confirm`);
      await page.fill("#ho-note", "Test note"); await page.click("#ho-confirm"); await page.waitForTimeout(300);
      const head = await page.textContent("#tl-handover .ho-head");
      check(head.includes("Confirmed") && (await page.textContent("#tl-handover")).includes("Test note"), "Confirm saves and shows, with the note");
      const doc = await page.evaluate(() => Object.values(window.__docs).find((d) => d.kind === "handover_confirm"));
      check(doc && doc.version === h.version && doc.by === h.from, "The database holds the confirm for this version", JSON.stringify(doc));
      // a rewrite after the confirm: the stored confirm points at an older version
      await page.evaluate((id) => window.__db.collection("decisions").doc("handover_confirm~" + id + "~1").set(
        { kind: "handover_confirm", account_id: id, version: "older", by: "x", role: "owner", decided_at: "2099-01-01T00:00:00Z" }), id);
      await page.waitForTimeout(250);
      check((await page.textContent("#tl-handover .ho-head")).includes("rewritten since it was confirmed") && await page.isVisible("#ho-confirm"),
        "A brief rewritten after the confirm is a draft again, ready to confirm");
    }
    check(!errors.length, "No script errors in the handover panel", errors.join("; "));
    await page.close();
  }

  await browser.close();
  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
})();
