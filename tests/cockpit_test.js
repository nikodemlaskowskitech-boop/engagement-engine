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
const TABS = ["overview", "lead", "case", "mybook", "tasks", "outcomes", "timeline", "onboarding", "team", "book"];

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
// a clicked list shows its first 10 rows: open it in full before counting
const listAll = async (page, host) => {
  const b = await page.$(`#${host} [data-all]`);
  if (b && /Show all/.test(await b.textContent())) { await b.click(); await page.waitForTimeout(60); }
  return page.$$eval(`#${host} tbody tr`, (r) => r.length);
};
const tab = async (page, t) => { await page.click("#tab-" + t); await page.waitForTimeout(150); };
const segment = async (page, values) => {
  await page.click("#seg-reset").catch(() => {});
  if (await page.isVisible("#seg-toggle") && !(await page.$eval("#segbar", (b) => b.classList.contains("open")))) await page.click("#seg-toggle");
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

    // the trail: four cairns, the urgency said by colour, height and words, and every stage opens its accounts
    const stages = await page.$$eval("#ld-tiles .wp", (bs) => bs.map((b) => ({ key: b.dataset.stage, cls: b.className, v: +b.querySelector(".v").textContent,
      cairn: b.querySelector("use").getAttribute("href"), u: b.querySelector(".u").textContent })));
    const churnRows = people.filter((a) => a.flags.some((f) => f.flag === "churn"));
    check(stages.length === 4 && stages[0].v === churnRows.length && stages[1].v === act,
      `the trail: ${churnRows.length} at risk, then ${act} at Act`, JSON.stringify(stages.map((x) => x.v)));
    const CAIRN = { act: "#cairn-red", attention: "#cairn-amber", ok: "#cairn-green" };
    check(stages.every((st) => { const lvl = ["act", "attention", "ok"].find((c) => st.cls.split(" ").includes(c)); return lvl && st.cairn === CAIRN[lvl] && st.u.length > 0; }),
      "each cairn's colour and height match its urgency, and the urgency is also in words", JSON.stringify(stages));
    const heights = await page.$$eval("symbol[id^='cairn-'] ", (ss) => Object.fromEntries(ss.map((x) => [x.id, x.querySelectorAll(':scope > path[fill]:not([fill="none"])').length])));
    check(heights["cairn-red"] === 4 && heights["cairn-amber"] === 3 && heights["cairn-green"] === 2, "red = 4 stones, amber = 3, green = 2", JSON.stringify(heights));
    const planned = stages[3];
    await page.click('#ld-tiles .wp[data-stage="planned"]');
    const rows = await page.$$eval("#ld-trail-list tbody tr", (r) => r.length);
    const missing = act - planned.v;
    check(!(await page.isHidden("#ld-trail-panel")) && rows === (missing || planned.v),
      `clicking "with a next step" lists the ${missing} Act accounts with no plan`, `${rows} rows`);
    const logo = await page.$eval("header .logo use", (u) => u.getAttribute("href"));
    const logoFills = await page.$$eval("#logo-path path[fill]", (ps) => ps.map((p) => p.getAttribute("fill").toLowerCase()));
    check(logo === "#logo-path" && !logoFills.some((f) => ["#e07a6a", "#e3b65a", "#7fbf94"].includes(f)), "the logo uses stone colours only, never a status colour");

    // one CSM: My book and Leadership tell the same story
    const mia = D.accounts.filter((a) => a.owner_id === "csm_mia");
    const miaRisk = mia.reduce((t, a) => t + churnValue(a), 0);
    await segment(page, { owner: "csm_mia" });
    await tab(page, "mybook");
    const port = await page.textContent("#mb-port-finding");
    check(port.startsWith(money(miaRisk)), `My book at risk for Mia = the export (${money(miaRisk)})`, port);

    // the reference stacked charts (My book: meetings looking back, the pipeline looking ahead)
    const charts = await page.evaluate(() => {
      const css = getComputedStyle(document.documentElement), v = (n) => css.getPropertyValue(n).trim().toLowerCase();
      const viz = { qbr: v("--viz-qbr"), call: v("--viz-call"), renewal: v("--viz-renewal"), other: v("--viz-other") };
      const read = (id) => [...document.querySelectorAll(`#${id} g.bar`)].map((g) => {
        const segs = [...g.querySelectorAll("rect.fill")].map((r) => ({ fill: r.getAttribute("fill"), y: +r.getAttribute("y"), h: +r.getAttribute("height") }));
        const tot = g.querySelector("text.tot"); return { segs, tot: tot ? +tot.textContent : 0 };
      });
      const legend = (id) => [...document.querySelectorAll(`#${id} > span:not(.aside)`)].map((s) => s.textContent);
      return { viz, eng: read("mb-eng-chart"), pipe: read("mb-pipe"), engLegend: legend("mb-eng-legend"), pipeLegend: legend("mb-pipe-legend"),
        pipeFinding: document.getElementById("mb-pipe-finding").textContent, aside: (document.querySelector("#mb-pipe-legend .aside") || {}).textContent || "" };
    });
    const tokenOf = (f) => (f.match(/--viz-(\w+)/) || [])[1];
    const segOK = (bars, order) => bars.every((b) => {
      const ks = b.segs.map((x) => tokenOf(x.fill));                          // drawn bottom first
      const idx = ks.map((k) => order.indexOf(k));
      const gaps = b.segs.slice(1).every((x, i) => b.segs[i].y - (x.y + x.h) >= 1.9);   // 2px between segments
      return ks.every(Boolean) && idx.every((x, i) => i === 0 || x > idx[i - 1]) && gaps;
    });
    check(charts.eng.length === 13 && segOK(charts.eng, ["other", "qbr", "call"]) && segOK(charts.pipe, ["renewal", "qbr", "followup".replace("followup", "call")]),
      "stacked charts: data colours only (--viz-*), the same stack order everywhere, a 2px gap between segments", JSON.stringify(charts.pipe.slice(0, 3)));
    check(!charts.pipe.some((b) => b.segs.some((x) => tokenOf(x.fill) === "renewal") && b.segs.some((x) => tokenOf(x.fill) === "call") &&
      b.segs.findIndex((x) => tokenOf(x.fill) === "call") - b.segs.findIndex((x) => tokenOf(x.fill) === "renewal") === 1),
      "blue never touches violet in a stack");
    check(charts.engLegend.join("|") === "Kickoff, check-in, other|QBR|Churn, growth or activation call, follow-up" &&
      charts.pipeLegend.join("|") === "Renewal|QBR booked|Follow-up due", "legends list the categories in stack order", JSON.stringify([charts.engLegend, charts.pipeLegend]));
    const booked = +(charts.pipeFinding.match(/has (\d+) booked/) || [])[1];
    check(charts.pipe.reduce((t, b) => t + b.tot, 0) === booked && /on Watch/.test(charts.aside),
      "pipeline totals add up to the booked engagements; Watch accounts are named beside it, not counted", `${charts.pipe.map((b) => b.tot)} vs ${booked}`);
    check(Object.values(charts.viz).every((c) => /^#[0-9a-f]{6}$/.test(c)) && !Object.values(charts.viz).some((c) => ["#7fbf94", "#e3b65a", "#e07a6a", "#e39a6f"].includes(c)),
      "data colours are their own set, never a status colour", JSON.stringify(charts.viz));
    const bar = await page.$$("#mb-pipe g.bar"); const withItems = charts.pipe.findIndex((b) => b.tot > 0);
    if (withItems >= 0) {
      await bar[withItems].scrollIntoViewIfNeeded(); const bb = await bar[withItems].boundingBox(); await page.mouse.move(bb.x + bb.width / 2, bb.y + bb.height - 10); await page.waitForTimeout(100);
      const tipText = await page.$eval(".tip", (t) => t.hidden ? "" : t.innerText);
      check(/Week commencing/.test(tipText) && /Renewal/.test(tipText) && /QBR booked/.test(tipText) && /Follow-up due/.test(tipText) && /Total/.test(tipText),
        "the tooltip gives the week and every category with the total", tipText.replace(/\n/g, " | "));
      await page.mouse.move(5, 5);
    }
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
      const staleShown = await page.isVisible("#tl-handover .ho-stale");
      check(staleShown === Boolean(h.stale && h.stale.length),
        `${id}: "Out of date" shows ${h.stale && h.stale.length ? "with why" : "nowhere"} (the engine's free check)`);
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
      check(!(await page.$eval("#ho-fold", (d) => d.open)), `${id}: the brief starts folded ("Read the brief")`);
      await page.click("#ho-fold summary"); await page.waitForTimeout(100);
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
      // (signed by the outgoing owner: a confirm by anyone else is ignored, review 6 Nov 2026)
      await page.evaluate(([id, from]) => window.__db.collection("decisions").doc("handover_confirm~" + id + "~1").set(
        { kind: "handover_confirm", account_id: id, version: "older", by: from, role: "owner", decided_at: "2099-01-01T00:00:00Z" }), [id, h.from]);
      await page.waitForTimeout(250);
      check((await page.textContent("#tl-handover .ho-head")).includes("rewritten since it was confirmed") && await page.isVisible("#ho-confirm"),
        "A brief rewritten after the confirm is a draft again, ready to confirm");
    }
    check(!errors.length, "No script errors in the handover panel", errors.join("; "));
    await page.close();
  }

  // --- the visualisation review of 10 Nov 2026: each finding gets a check -------------------
  {
    const { page, errors } = await open(browser);
    // colour groups: no chart mark uses the brand accent or the retired --s1..--s4 colours
    const bad = [];
    for (const t of ["overview", "lead", "case", "mybook", "outcomes", "timeline", "onboarding", "team", "book"]) {
      if (t === "mybook") await segment(page, { owner: "csm_mia" }); else await segment(page, {});
      await tab(page, t);
      bad.push(...await page.$$eval("main svg [fill], main svg [stroke]", (els) => els.flatMap((e) =>
        ["fill", "stroke"].map((a) => e.getAttribute(a) || "").filter((v) => /--accent\b|--s[1-4]\)/.test(v)))).then((xs) => xs.map((x) => t + ": " + x)));
    }
    check(!bad.length, "review: no chart uses the brand accent or the retired category colours", bad.slice(0, 5).join(" | "));
    // the meeting colours: stone, sage and lavender apart by OKLab ΔE ≥ 15 for normal vision
    const okl = (hex) => { const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((v) => v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
      const l = Math.cbrt(0.4122214708 * c[0] + 0.5363325363 * c[1] + 0.0514459929 * c[2]), m = Math.cbrt(0.2119034982 * c[0] + 0.6806995451 * c[1] + 0.1073969566 * c[2]), s = Math.cbrt(0.0883024619 * c[0] + 0.2817188376 * c[1] + 0.6299787005 * c[2]);
      return [0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s, 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s, 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s]; };
    const dE = (a, b) => 100 * Math.hypot(...okl(a).map((x, i) => x - okl(b)[i]));
    const viz = await page.evaluate(() => Object.fromEntries(["qbr", "call", "renewal", "other"].map((k) => [k, getComputedStyle(document.documentElement).getPropertyValue("--viz-" + k).trim()])));
    const pairs = [["other", "qbr"], ["qbr", "call"], ["renewal", "qbr"]].map(([a, b]) => [a + "/" + b, Math.round(dE(viz[a], viz[b]) * 10) / 10]);
    check(pairs.every(([, d]) => d >= 15), "review: stacked neighbours differ by ΔE 15+ (stone vs sage was 9.5)", JSON.stringify(pairs));
    // heatmap labels readable on their cells (4.5:1)
    await segment(page, {}); await tab(page, "book");
    const worst = await page.evaluate(() => {
      const lum = (rgb) => { const c = rgb.match(/\d+/g).slice(0, 3).map((v) => v / 255).map((v) => v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4); return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]; };
      let w = 99;
      for (const t of document.querySelectorAll("#bk-heat text.val")) {
        const r = t.previousElementSibling; if (!r || r.tagName !== "rect") continue;
        const a = lum(getComputedStyle(t).fill), b = lum(getComputedStyle(r).fill), c = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
        w = Math.min(w, c);
      }
      return Math.round(w * 100) / 100;
    });
    check(worst >= 4.5, "review: every heatmap label is readable on its cell (4.5:1 or better)", `worst ${worst}:1`);
    // the CS team heatmap has a key with its maximum, not "darker means more"
    await tab(page, "team");
    const key = await page.textContent("#tm-weeks");
    check(/meetings? a week/.test(key) && !/Darker means more/.test(await page.textContent("#view-team")), "review: the meetings heatmap has a key with its maximum");
    check(!(await page.$("#tm-table td.bad")), "decision 10 Nov: the CS team 'Called' column is neutral");
    const dotFills = await page.$$eval("#tm-legend svg *", (els) => els.map((e) => e.getAttribute("fill") || ""));
    check(!dotFills.some((f) => /--good/.test(f)), "review: the dot plot uses no status green for a category", dotFills.join(","));
    // timeline: a meeting's diamond is coloured by its type, as on My book
    const qbrAcc = D.touches.find((t) => t.event === "meeting_held" && t.topic === "qbr");
    if (qbrAcc) {
      await page.goto(PAGE + "#" + qbrAcc.account_id); await page.waitForTimeout(500);
      const cols = await page.$$eval("#tl-chart svg path, #tl-chart svg rect, #tl-chart svg circle", (els) => [...new Set(els.map((e) => e.getAttribute("fill") || ""))]);
      check(cols.includes("var(--viz-qbr)"), "review: the timeline colours a QBR sage, like My book", cols.join(","));
    }
    // tasks: Open is neutral until it has waited 14 days, then amber; Claimed is neutral
    await tab(page, "tasks");
    const bad2 = await page.$$eval("#view-tasks .badge", (bs) => bs.map((b) => {
      const tr = b.closest("tr"); const age = tr ? +(tr.textContent.match(/(\d+) d\b/) || [0, -1])[1] : -1;
      return { cls: b.className, age, bg: getComputedStyle(b).backgroundColor };
    }));
    const warnBg = await page.evaluate(() => { const d = document.createElement("span"); d.style.background = "var(--warning-bg)"; document.body.append(d); const c = getComputedStyle(d).backgroundColor; d.remove(); return c; });
    const wrong = bad2.filter((b) => /\bOpen\b/.test(b.cls) && b.age >= 0 && ((b.age >= 14) !== (b.bg === warnBg)));
    check(bad2.length > 0 && !wrong.length, "decision 10 Nov: an Open task is amber only once it has waited 14 days", JSON.stringify(wrong.slice(0, 3)));
    // Leadership trail: Act accounts not called yet are "act now", as on Overview
    await tab(page, "lead");
    const calledCls = await page.$eval('#ld-tiles .wp[data-stage="called"]', (b) => b.className);
    check(/\bact\b/.test(calledCls) || /\bok\b/.test(calledCls), "decision 10 Nov: 'not called yet' is red on the trail", calledCls);
    check(!errors.length, "no script errors in the review checks", errors.join("; "));
    await page.close();
  }
  // tooltips stay readable in the light theme
  {
    const { page } = await open(browser);
    await page.evaluate(() => document.documentElement.setAttribute("data-theme", "light"));
    await segment(page, { owner: "csm_mia" }); await tab(page, "mybook");
    const bar = (await page.$$("#mb-pipe g.bar"))[0]; await bar.scrollIntoViewIfNeeded();
    const bb = await bar.boundingBox(); await page.mouse.move(bb.x + bb.width / 2, bb.y + bb.height - 8); await page.waitForTimeout(120);
    const c = await page.$eval(".tip", (t) => { const lum = (rgb) => { const c = rgb.match(/\d+/g).slice(0, 3).map((v) => v / 255).map((v) => v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4); return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]; };
      const s = getComputedStyle(t), a = lum(s.color), b = lum(s.backgroundColor); return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05); });
    check(c >= 4.5, "review: tooltips are readable in the light theme too", `${c.toFixed(2)}:1`);
    await page.close();
  }

  // --- phone: chart text stays readable (review 10 Nov 2026: an 880-wide chart shrank to 4px text) ---
  {
    const { page, errors } = await open(browser, { width: 390, scheme: "dark" });
    const small = [];
    let inside = 0;
    for (const t of TABS) {
      if (t === "mybook") await segment(page, { owner: "csm_mia" });
      await tab(page, t);
      small.push(...await page.$$eval(`#view-${t} svg[viewBox]`, (svgs, tt) => svgs
        .filter((s) => s.getBoundingClientRect().width > 60 && !s.closest(".trail") && s.querySelector("text"))
        .map((s) => { const k = s.getBoundingClientRect().width / s.viewBox.baseVal.width;
          const px = Math.min(...[...s.querySelectorAll("text")].map((x) => parseFloat(getComputedStyle(x).fontSize) * k));
          return [tt + ": " + (s.getAttribute("aria-label") || s.parentElement.id), px]; })
        .filter(([, px]) => px < 9.9).map(([n, px]) => `${n} ${px.toFixed(1)}px`), t));
      inside += await page.$$eval(`#view-${t} svg.stack text.seg`, (x) => x.length);
    }
    check(!small.length, "phone: every chart's text is 10px or more", small.join(" | "));
    check(inside === 0, "phone: no numbers inside stacked segments (the tooltip and the total carry them)", `${inside} inside`);
    // the labels under a stacked chart never run into each other
    await segment(page, { owner: "csm_mia" }); await tab(page, "mybook");
    const overlap = await page.$$eval("#view-mybook svg.stack", (svgs) => svgs.some((s) => {
      const H = s.viewBox.baseVal.height, xs = [...s.querySelectorAll(":scope > text")].filter((t) => +t.getAttribute("y") > H - 10).map((t) => t.getBBox()).sort((a, b) => a.x - b.x);
      return xs.some((b, i) => i && b.x < xs[i - 1].x + xs[i - 1].width + 2);
    }));
    check(!overlap, "phone: week labels under the stacked charts don't overlap");
    check(!errors.length, "phone: no script errors", errors.join("; "));
    await page.close();
  }
  // desktop: numbers inside only on QBR and call segments, never on "other" or renewals
  {
    const { page } = await open(browser);
    await segment(page, { owner: "csm_mia" }); await tab(page, "mybook");
    const fills = await page.$$eval("svg.stack text.seg", (ts) => ts.map((t) => t.previousElementSibling.getAttribute("fill")));
    check(fills.length > 0 && fills.every((f) => /viz-qbr|viz-call/.test(f)), "desktop: inside numbers only on QBR and call segments", [...new Set(fills)].join(","));
    await page.close();
  }

  // --- every number lists its accounts (review 10 Nov 2026: five charts had hover only) ---------
  {
    const { page, errors } = await open(browser);
    const n = (txt) => +((txt.match(/: (\d+) /) || [])[1] ?? -1);
    // My book meetings: the busiest week's bar lists exactly its total
    await segment(page, { owner: "csm_mia" }); await tab(page, "mybook");
    const bars = await page.$$("#mb-eng-chart g.bar");
    let best = null, bestT = -1;
    for (const b of bars) { const t = +(await b.$eval("text.tot", (x) => x.textContent).catch(() => 0)); if (t > bestT) { bestT = t; best = b; } }
    await best.click(); await page.waitForTimeout(100);
    const mt = await page.textContent("#mb-eng-title"), mr = await page.$$eval("#mb-eng-list tbody tr", (r) => r.length);
    check(bestT > 0 && n(mt) === bestT && mr === bestT, `My book: a week of ${bestT} meetings lists ${bestT}`, `${mt} / ${mr} rows`);
    // CS team heatmap: a cell lists that person's meetings that week
    await segment(page, {}); await tab(page, "team");
    // the first person's last week, counted independently from the export
    const first = D.team.find((t) => t.owner_type !== "pool"), we = D.week_ends[D.week_ends.length - 1];
    const dayN = (d) => Math.floor(Date.parse(d + "T00:00:00Z") / 864e5);
    const expectM = D.touches.filter((x) => x.event === "meeting_held" && x.owner_id === first.owner_id && dayN(x.date) > dayN(we) - 7 && dayN(x.date) <= dayN(we)).length;
    const cells = await page.$$("#tm-weeks rect.cell");
    const rowLen = D.week_ends.length;
    const order = await page.$$eval("#tm-weeks svg text.lbl", (ts) => ts.map((t) => t.textContent));
    const ri = order.indexOf(first.owner_name);
    await cells[ri * rowLen + rowLen - 1].dispatchEvent("click"); await page.waitForTimeout(100);
    const tt = await page.textContent("#tm-weeks-title"), tr = await page.$$eval("#tm-weeks-list tbody tr", (r) => r.length);
    check(ri >= 0 && tt.startsWith(first.owner_name) && n(tt) === expectM && (tr === expectM || (expectM === 0 && tr === 0)),
      `CS team: ${first.owner_name}'s last week lists ${expectM} meetings`, `${tt} / ${tr} rows`);
    // CS team dot plot: equal values stack in columns, every dot inside its day's lane
    const lanes = await page.$$eval("#tm-dots svg", (svgs) => { const s = svgs[0]; return [...s.querySelectorAll("text.val")].map((t) => t.textContent); });
    check(lanes.some((t) => /^\d+\+$/.test(t)), 'CS team: the last tick says "N+" (anything further is pinned there)', lanes.join(","));
    // Book heatmap: a cell's list matches its count
    await tab(page, "book");
    const bcell = (await page.$$("#bk-heat rect.cell[style*='pointer']"))[5];
    await bcell.dispatchEvent("click"); await page.waitForTimeout(100);
    const bt = await page.textContent("#bk-cell-title"), br = await page.$$eval("#bk-cell-list tbody tr", (r) => r.length);
    const shown = await page.evaluate((i) => { const c = document.querySelectorAll("#bk-heat rect.cell[style*='pointer']")[i]; const t = c.nextElementSibling; return t && t.tagName === "text" ? t.textContent : ""; }, 5);
    check(n(bt) === br, "Book: a heatmap cell lists the accounts behind it", `${bt} / ${br} rows, cell shows ${shown}`);
    // Onboarding: a histogram bar and an outcome row list what they count
    await tab(page, "onboarding");
    const obBar = (await page.$$("#ob-dots path.bar"))[0];
    const obV = await page.evaluate((b) => +b.nextElementSibling.textContent, obBar);
    await obBar.dispatchEvent("click"); await page.waitForTimeout(100);
    const ot = await page.textContent("#ob-list-title");
    check(n(ot) === obV, `Onboarding: a histogram bar of ${obV} lists ${obV}`, ot);
    const row = (await page.$$("#ob-funnel g.obbar"))[2];
    const want = await row.$eval("text.val", (t) => +t.textContent.split(" ")[0]);
    await row.dispatchEvent("click"); await page.waitForTimeout(100);
    const ot2 = await page.textContent("#ob-list-title");
    check(n(ot2) === want, `Onboarding: an outcome bar of ${want} lists ${want}`, ot2);
    check(!errors.length, "no script errors in the click-through checks", errors.join("; "));
    await page.close();
  }

  // light theme: every data colour stands out from the paper (3:1, WCAG for graphics)
  {
    const { page } = await open(browser);
    await page.evaluate(() => document.documentElement.setAttribute("data-theme", "light"));
    const low = await page.evaluate(() => {
      const st = getComputedStyle(document.documentElement), hex = (k) => st.getPropertyValue(k).trim();
      const lum = (h) => { const c = [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16) / 255).map((v) => v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4); return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]; };
      const bg = lum(hex("--surface"));
      return ["--viz-qbr", "--viz-call", "--viz-renewal", "--viz-other", "--viz-touch", "--viz-measure", "--viz-measure-soft"]
        .map((k) => { const l = lum(hex(k)); return [k, (Math.max(l, bg) + 0.05) / (Math.min(l, bg) + 0.05)]; }).filter(([, c]) => c < 3).map(([k, c]) => `${k} ${c.toFixed(2)}:1`);
    });
    check(!low.length, "light theme: every chart colour is 3:1 or more on the paper", low.join(", "));
    await page.close();
  }

  // --- the re-review of 10 Nov 2026: each new finding gets a check ------------------------------
  const lumOf = (rgb) => { const c = rgb.match(/\d+/g).slice(0, 3).map((v) => v / 255).map((v) => v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4); return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]; };
  {
    // light theme: heatmap labels 4.5:1 on their cells (the mid blue needed white)
    const { page } = await open(browser);
    await page.evaluate(() => document.documentElement.setAttribute("data-theme", "light"));
    await segment(page, {}); await tab(page, "book");
    await page.click("#bk-growth"); await page.click("#bk-risk"); await page.waitForTimeout(100);   // redraw under the light theme
    const worstL = await page.$$eval("#bk-heat text.val", (ts) => ts.map((t) => [getComputedStyle(t).fill, t.previousElementSibling && getComputedStyle(t.previousElementSibling).fill]).filter(([, b]) => b));
    const wl = Math.min(...worstL.map(([a, b]) => { const x = lumOf(a), y = lumOf(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); }));
    check(wl >= 4.5, "light theme: every heatmap label is 4.5:1 on its cell", `worst ${wl.toFixed(2)}:1`);
    await page.close();
  }
  {
    const { page } = await open(browser, { scheme: "dark" });
    const v = await page.evaluate(() => { const st = getComputedStyle(document.documentElement), g = (k) => st.getPropertyValue(k).trim(); return { touch: g("--viz-touch"), other: g("--viz-other"), call: g("--viz-call"), label: g("--viz-label"), surface: g("--surface") }; });
    const hexLum = (h) => lumOf(`rgb(${[1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16)).join(",")})`);
    const ratio = (a, b) => { const x = hexLum(a), y = hexLum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
    check(ratio(v.label, v.call) >= 4.5, "inside counts are 4.5:1 on the call colour", ratio(v.label, v.call).toFixed(2));
    check(ratio(v.other, v.surface) >= 3, "'other meetings' stone is 3:1 on the dark panel", ratio(v.other, v.surface).toFixed(2));
    // onboarding: welcomed vs. control are clearly apart (they sit side by side)
    const okl = (hex) => { const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((x) => x <= 0.04045 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4);
      const l = Math.cbrt(0.4122214708 * c[0] + 0.5363325363 * c[1] + 0.0514459929 * c[2]), m = Math.cbrt(0.2119034982 * c[0] + 0.6806995451 * c[1] + 0.1073969566 * c[2]), q = Math.cbrt(0.0883024619 * c[0] + 0.2817188376 * c[1] + 0.6299787005 * c[2]);
      return [0.2104542553 * l + 0.793617785 * m - 0.0040720468 * q, 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * q, 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * q]; };
    const dE = 100 * Math.hypot(...okl(v.touch).map((x, i) => x - okl(v.other)[i]));
    check(dE >= 15, "onboarding: welcomed and control bars are ΔE 15+ apart", dE.toFixed(1));
    // Leadership: "book met" is red only when 10+ points under the median
    await tab(page, "lead");
    const reds = await page.$$eval("#ld-team td.bad", (tds) => tds.map((td) => { const m = td.textContent.match(/\((\d+)%\).*median (\d+)%/); return m ? +m[2] - +m[1] : -1; }));
    check(reds.every((d) => d >= (D.settings.below_median_points ?? 10)), "Leadership: 'book met' turns red only 10+ points under the median", JSON.stringify(reds));
    await page.close();
  }
  {
    // phone: the outcomes matrix stays in its panel, chart labels stay inside their chart and don't touch
    const { page } = await open(browser, { width: 390, scheme: "dark" });
    await tab(page, "outcomes");
    const over = await page.$eval("#oc-matrix", (m) => { const p = m.closest(".panel").getBoundingClientRect(), r = m.getBoundingClientRect(); return r.right - p.right; });
    check(over <= 0, "phone: the outcomes matrix stays inside its panel", `${over.toFixed(0)}px over`);
    const wt = await page.$$eval("#oc-warning svg > text", (ts) => ts.map((t) => { const b = t.getBBox(); return { x: b.x, y: b.y, width: b.width }; }).filter((b) => b.y > 170 && b.y < 190).sort((a, b) => a.x - b.x));
    check(wt.length > 3 && wt.every((b, i) => !i || b.x >= wt[i - 1].x + wt[i - 1].width + 2), "phone: warning-time labels don't touch", JSON.stringify(wt.map((b) => [Math.round(b.x), Math.round(b.y), Math.round(b.width)])));
    await segment(page, { owner: "csm_mia" }); await tab(page, "mybook");
    const clipped = await page.$$eval("#view-mybook svg.stack", (svgs) => svgs.flatMap((s) => [...s.querySelectorAll(":scope > text")].filter((t) => { const b = t.getBBox(); return b.x < -1 || b.x + b.width > s.viewBox.baseVal.width + 1; }).map((t) => t.textContent)));
    check(!clipped.length, "phone: no week label runs past the chart's edge", clipped.join(","));
    await page.close();
  }

  // --- Find's list shows over the page (10 Nov 2026: the header clipped it, typing showed nothing) ---
  {
    const { page } = await open(browser);
    await tab(page, "lead");
    await page.fill("#seg-find", "a"); await page.waitForTimeout(150);
    const onTop = await page.evaluate(() => { const l = document.getElementById("seg-find-list"); if (l.hidden) return "hidden";
      const b = l.getBoundingClientRect(), y = Math.min(b.bottom - 6, b.top + 60), hit = document.elementFromPoint(b.left + 20, y);
      return l.contains(hit) ? "ok" : `covered by ${hit && (hit.id || hit.className || hit.tagName)}`; });
    check(onTop === "ok", "Find: the suggestion list is on top of the page, not behind it", onTop);
    await page.close();
  }
  // --- Did it work? 2x2: hover names the accounts, a click lists them, stays selected, and can pin them ---
  {
    const { page } = await open(browser);
    await tab(page, "outcomes");
    const btn = (await page.$$("#oc-matrix button.cellbtn"))[0];
    const n0 = +(await btn.$eval(".v", (v) => v.textContent));
    await btn.hover(); await page.waitForTimeout(120);
    const tip = await page.$eval(".tip", (t) => t.textContent);
    check(/Caught: \d+ account/.test(tip) && /Click to list them/.test(tip), "Did it work?: hovering a cell names its accounts", tip.slice(0, 80));
    await btn.click(); await page.waitForTimeout(400);
    const title = await page.textContent("#oc-list-title");
    const sel = await page.$$eval("#oc-matrix button.cellbtn.on", (b) => b.map((x) => x.dataset.name));
    const inView = await page.$eval("#oc-list-title", (h) => { const r = h.getBoundingClientRect(); return r.top >= 0 && r.top < innerHeight; });
    check(title.includes(`${n0} account`) && sel.length === 1 && sel[0] === "Caught" && inView, "Did it work?: a click lists the cell, keeps it selected and scrolls to the list", `${title} · ${sel} · in view ${inView}`);
    await page.click("#oc-pin"); await page.waitForTimeout(250);
    const pins = await page.$$eval("#seg-pins .pin", (p) => p.length);
    const onLead = await page.$eval("#view-lead", (v) => !v.hidden);
    check(pins === n0 && onLead, `Did it work?: "show only these" pins the ${n0} accounts and opens Leadership`, `${pins} pinned, Leadership ${onLead}`);
    // the team-totals version has no click, so it mustn't look clickable
    const cursor = await page.evaluate(() => { const s = document.createElement("span"); s.className = "cellbtn good"; document.body.append(s); const c = getComputedStyle(s).cursor; s.remove(); return c; });
    check(cursor === "default", "Did it work?: a team-totals cell (not a link) doesn't show a pointer", cursor);
    await page.evaluate(() => { try { localStorage.clear(); } catch (e) {} });
    await page.close();
  }

  // --- time to call, redesigned (10 Nov 2026): one axis, waiting accounts at their age ---------
  for (const width of [1300, 390]) {
    const { page, errors } = await open(browser, { width });
    await tab(page, "team");
    const TARGET = D.settings.call_target_days ?? 14, ASOF = Date.parse(D.as_of + "T00:00:00Z") / 864e5;
    const act = D.time_to_action.filter((a) => a.owner_type === "csm" || a.owner_type === "pool");
    const waiting = act.filter((a) => !a.call_date), lateN = waiting.filter((a) => ASOF - Date.parse(a.signal_date + "T00:00:00Z") / 864e5 >= TARGET).length;
    const got = await page.$$eval("#tm-dots circle.waiting, #tm-dots circle.late", (cs) => ({ waiting: cs.filter((c) => c.classList.contains("waiting")).length, late: cs.filter((c) => c.classList.contains("late")).length,
      lateRed: cs.filter((c) => c.classList.contains("late")).every((c) => /critical/.test(c.getAttribute("stroke"))), freshNeutral: cs.filter((c) => c.classList.contains("waiting")).every((c) => !/critical/.test(c.getAttribute("stroke"))) }));
    const plus = await page.$$eval("#tm-dots text.val", (ts) => ts.filter((t) => /^\+\d+$/.test(t.textContent)).reduce((s, t) => s + +t.textContent.slice(1), 0));
    check(got.late + got.waiting + (width < 640 ? plus : 0) >= waiting.length - (width < 640 ? plus : 0) && got.late <= lateN && got.lateRed && got.freshNeutral,
      `${width}px: every account not yet called sits at its waiting age, red only past ${TARGET} days`, JSON.stringify({ ...got, expect: waiting.length, lateN, plus }));
    if (width > 640) check(got.late === lateN && got.late + got.waiting === waiting.length, `${width}px: ${lateN} late and ${waiting.length - lateN} still-in-time rings, as the export says`, JSON.stringify(got));
    const col = await page.$$eval("#tm-dots text", (ts) => ts.some((t) => t.textContent === "no call"));
    check(!col, `${width}px: no separate "no call" column any more`);
    // every mark stays inside its owner's row (the old stacking poked into the row above)
    const bleed = await page.$eval("#tm-dots svg", (s) => {
      const seps = [...s.querySelectorAll("line")].filter((l) => l.getAttribute("x1") === "0").map((l) => +l.getAttribute("y1"));
      return [...s.querySelectorAll("circle, rect")].filter((m) => { const y = m.tagName === "rect" ? +m.getAttribute("y") + 4.5 : +m.getAttribute("cy"); return seps.some((sy) => Math.abs(sy - y) < 5); }).length;
    });
    check(bleed === 0, `${width}px: no mark sits on a row line`, `${bleed} marks`);
    const touching = await page.$eval("#tm-dots svg", (s) => { const pts = [...s.querySelectorAll("circle, rect")].map((m) => m.tagName === "rect" ? [+m.getAttribute("x") + 4.5, +m.getAttribute("y") + 4.5] : [+m.getAttribute("cx"), +m.getAttribute("cy")]);
      let n = 0; for (let i = 0; i < pts.length; i++) for (let j = i + 1; j < pts.length; j++) if (Math.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1]) < 9) n++; return n; });
    check(touching === 0, `${width}px: no two marks touch (a ring sat on a dot on day 11)`, `${touching} pairs`);
    // each owner's summary counts what the export says
    const first = D.team.find((t) => t.owner_type === "csm" && act.some((a) => a.routed_to === t.owner_id));
    const w = act.filter((a) => a.routed_to === first.owner_id && !a.call_date).length;
    const txt = await page.$$eval("#tm-dots text.val", (ts) => ts.map((t) => t.textContent));
    check(txt.some((t) => t.includes(`${w} waiting`)), `${width}px: ${first.owner_name}'s row says ${w} waiting`, txt.filter((t) => /waiting/.test(t)).slice(0, 3).join(" | "));
    check(!errors.length, `${width}px: no script errors on the time-to-call chart`, errors.join("; "));
    await page.close();
  }

  // --- Leadership, 12 Nov 2026: work not picked up, Enterprise accounts with no CSM, folded tables ---
  {
    const { page, errors } = await open(browser);
    await tab(page, "lead");
    const STALE = D.settings.stale_task_days || 14;
    const open_ = D.tasks.filter((t) => t.action_status === "Open").sort((a, b) => b.age_days - a.age_days);
    const qf = await page.textContent("#ld-queue-finding");
    check(qf.startsWith(`${open_.length} task`) && qf.includes(`the oldest ${open_[0].age_days} days`), `Leadership: ${open_.length} tasks not picked up, the oldest ${open_[0].age_days} days`, qf);
    const firstAge = await page.$eval("#ld-queue tbody tr td:last-child", (td) => td.textContent.trim());
    check(firstAge === `${open_[0].age_days} days`, "Leadership: the queue starts with the longest wait", firstAge);
    const badges = await page.$$eval("#ld-queue .badge", (bs) => bs.map((b) => [+b.textContent.split(" ")[0], b.classList.contains("stale")]));
    check(badges.length && badges.every(([d, st]) => st === (d >= STALE)), `Leadership: a wait turns amber at ${STALE} days, not before`, JSON.stringify(badges));
    await page.click("#ld-queue [data-more]"); await page.waitForTimeout(80);
    const all = await page.$$eval("#ld-queue tbody tr", (r) => r.length);
    check(all === open_.length, `Leadership: "View all" lists all ${open_.length} waiting tasks`, `${all}`);
    const noCsm = D.accounts.filter((a) => a.tier === "Enterprise" && a.flags.some((f) => f.status === "No CSM assigned"));
    const nf = await page.textContent("#ld-nocsm-finding");
    check(noCsm.length ? nf.startsWith(`${noCsm.length} Enterprise account`) : /Every Enterprise account/.test(nf), `Leadership: ${noCsm.length} Enterprise account(s) with no CSM`, nf);
    // long tables fold to six rows, with a way to see the rest
    for (const [t, id] of [["lead", "ld-team"], ["team", "tm-cap"]]) {
      await tab(page, t);
      const st = await page.$eval("#" + id, (tb) => { const rows = [...tb.querySelectorAll("tr")].filter((r) => !r.querySelector("th")); const btn = document.querySelector(`[data-fold-for="${tb.id}"]`); return { n: rows.length, shown: rows.filter((r) => !r.hidden).length, btn: btn && btn.textContent }; });
      const okFold = st.n <= 7 ? st.shown === st.n : st.shown === 6 && st.btn === `Show all (${st.n})`;
      check(okFold, `${id}: a long table shows 6 rows and "Show all (${st.n})"`, JSON.stringify(st));
      if (st.n > 7) { await page.click(`[data-fold-for="${id}"]`); const shown = await page.$$eval(`#${id} tr`, (r) => r.filter((x) => !x.hidden && !x.querySelector("th")).length); check(shown === st.n, `${id}: "Show all" opens every row`, `${shown}`); }
    }
    check(!errors.length, "no script errors on the new Leadership panels", errors.join("; "));
    await page.close();
  }

  // --- Trends from the weekly history (history.py), 12 Nov 2026 ---
  {
    const H = D.history, W = H.weeks.length, last = W - 1;
    const byIdT = Object.fromEntries(D.accounts.map((a) => [a.account_id, a]));
    const offT = Object.fromEntries(H.off_book.map((a) => [a.account_id, a]));
    const accOf = (id) => byIdT[id] || offT[id] || {};
    check(W >= 20, `history: ${W} weekly snapshots`);
    check(H.weeks[last] === D.as_of, "history: the last week is the as-of date", H.weeks[last]);
    check(!JSON.stringify(H).match(/acc_\d+/g)?.some((id) => !byIdT[id]), "history: no account id outside today's book (those travel as ob_ keys)");
    const openNow = D.tasks.filter((t) => t.action_status === "Open").length;
    const openLast = H.tasks.filter((r) => r[0] === last && r[5] === "Open").length;
    check(openLast === openNow, `history: the last week's queue = today's (${openNow} not picked up)`, `${openLast}`);
    const ncNow = D.accounts.filter((a) => a.tier === "Enterprise" && a.flags.some((f) => f.status === "No CSM assigned")).length;
    const ncLast = H.flags.filter((r) => r[0] === last && r[3] === "No CSM assigned").length;
    check(ncLast === ncNow, `history: the last week's Enterprise accounts with no CSM = today's (${ncNow})`, `${ncLast}`);
    for (const width of [1300, 390]) {
      const { page, errors } = await open(browser, { width, scheme: "dark" });
      await tab(page, "lead");
      const bars = await page.$$eval("#ld-queue-trend g.bar", (gs) => gs.length);
      check(bars === W, `${width}px: the queue trend has one bar per week (${W})`, `${bars}`);
      const tot = await page.$$eval("#ld-queue-trend text.tot", (ts) => ts.map((t) => t.textContent));
      check(tot.length === 1 && +tot[0] === openNow, `${width}px: only today's bar carries its total (${openNow})`, JSON.stringify(tot));
      for (const id of ["ld-queue-trend", "ld-nocsm-trend"]) {
        const labels = await page.$$eval(`#${id} svg > text`, (ts) => ts.filter((t) => !/^\d+$/.test(t.textContent)).map((t) => { const b = t.getBoundingClientRect(); return { x: b.left, r: b.right, t: t.textContent, px: parseFloat(getComputedStyle(t).fontSize) * t.ownerSVGElement.getBoundingClientRect().width / t.ownerSVGElement.viewBox.baseVal.width }; }));
        const clash = labels.slice(1).filter((l, i) => l.x < labels[i].r + 2).map((l) => l.t);
        check(labels.length >= 3 && !clash.length, `${width}px ${id}: week labels don't touch (${labels.length})`, clash.join(", "));
        check(labels.length && labels[labels.length - 1].t === new Date(D.as_of + "T00:00:00Z").toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" }), `${width}px ${id}: the last label is today's week`, labels.map((l) => l.t).slice(-2).join(" | "));
        check(labels.every((l) => l.px >= 9.5), `${width}px ${id}: labels 10px or larger on screen`, labels.map((l) => l.px.toFixed(1)).slice(0, 3).join(", "));
      }
      // a click on today's bar lists every task not picked up
      await page.$eval(`#ld-queue-trend g.bar:nth-of-type(${W})`, (g) => g.dispatchEvent(new MouseEvent("click", { bubbles: true }))); await page.waitForTimeout(100);
      const listed = await listAll(page, "ld-queue-week");
      check(listed === openNow, `${width}px: today's bar lists the ${openNow} tasks`, `${listed}`);
      check(!errors.length, `${width}px: no script errors on the trends`, errors.join("; "));
      await page.close();
    }
    // a week with an account that has since left: counted, named only as such, opens nothing
    const goneWeek = H.tasks.find((r) => r[5] === "Open" && offT[r[1]]) || H.flags.find((r) => r[3] === "No CSM assigned" && offT[r[1]]);
    if (goneWeek) {
      const { page } = await open(browser);
      await tab(page, "lead");
      const isTask = goneWeek.length > 4, host = isTask ? "ld-queue" : "ld-nocsm";
      await page.$eval(`#${host}-trend g.bar:nth-of-type(${goneWeek[0] + 1})`, (g) => g.dispatchEvent(new MouseEvent("click", { bubbles: true }))); await page.waitForTimeout(100);
      const rows = await page.$$eval(`#${host}-week tbody tr`, (rs) => rs.map((r) => ({ t: r.textContent, click: r.classList.contains("click") })));
      const gone = rows.filter((r) => /since left|No CRM record/.test(r.t));
      check(gone.length && gone.every((r) => !r.click), "an account that has since left is listed as such and opens nothing", JSON.stringify(rows.slice(0, 3)));
      await page.close();
    }
    // the segment applies: Sophie's team, the week with most of its tasks waiting
    {
      const mine = (id) => accOf(id).manager === "mgr_sophie";
      const per = H.weeks.map((_, i) => H.tasks.filter((r) => r[0] === i && r[5] === "Open" && mine(r[1])).length);
      const wk = per.indexOf(Math.max(...per));
      const { page } = await open(browser);
      await tab(page, "lead"); await segment(page, { manager: "mgr_sophie" });
      await page.$eval(`#ld-queue-trend g.bar:nth-of-type(${wk + 1})`, (g) => g.dispatchEvent(new MouseEvent("click", { bubbles: true }))); await page.waitForTimeout(100);
      const listed = await listAll(page, "ld-queue-week");
      check(listed === per[wk], `Sophie's team: the week of ${H.weeks[wk]} lists her team's ${per[wk]} waiting tasks`, `${listed}`);
      const ncTeam = new Set(H.flags.filter((r) => r[3] === "No CSM assigned" && mine(r[1])).map((r) => r[1])).size;
      const nf = await page.textContent("#ld-nocsm-finding");
      check(ncTeam ? nf.includes(`${ncTeam} new Enterprise account`) : !/waited for one/.test(nf), `Sophie's team: ${ncTeam} new Enterprise accounts waited for a CSM this year`, nf);
      await page.close();
    }
  }

  // --- Business case: GRR and NRR, standard definitions, 12 Nov 2026 ---
  {
    const R = D.retention, Y = R.year, P = R.prior, F = R.forecast;
    const p1 = (x) => (100 * x).toFixed(1) + "%";
    const sumC = (xs, i) => xs.reduce((t, r) => t + r[i], 0);
    const start = sumC(R.cohort, 2), end = sumC(R.cohort, 3);
    const capped = R.cohort.reduce((t, r) => t + Math.min(r[2], r[3]), 0);
    check(Math.abs(start - Y.start_arr) < 1 && Math.abs(capped / start - Y.grr) < 1e-9 && Math.abs(end / start - Y.nrr) < 1e-9,
      `retention: the cohort rows add up to SQL's GRR ${p1(Y.grr)} and NRR ${p1(Y.nrr)}`);
    check(Y.grr <= 1 && Y.grr > 0.8 && Y.nrr > Y.grr, `retention: GRR under 100% and NRR above it (${p1(Y.grr)}, ${p1(Y.nrr)})`);
    check(!R.cohort.some((r) => /^acc_/.test(r[0]) && r[3] === 0), "retention: a churned account travels as an ob_ key, never its id");
    const { page, errors } = await open(browser);
    await tab(page, "case");
    const fnd = await page.textContent("#cs-finding");
    check(fnd.includes(`GRR ${p1(Y.grr)}`) && fnd.includes(`NRR ${p1(Y.nrr)}`) && (!P || fnd.includes(p1(P.grr))),
      "business case: the headline says this year's GRR and NRR and the year before's", fnd);
    const tiles = await page.innerText("#cs-tiles");
    const book = D.accounts.reduce((t, a) => t + a.arr, 0);
    check(tiles.includes(p1(Y.grr)) && tiles.includes(p1(Y.nrr)), "business case: the tiles match the export");
    // every clickable bar lists that many accounts
    const counts = { churned: Y.churned_n, contraction: Y.contraction_n, expansion: Y.expansion_n };
    const labels = await page.$$eval("#cs-bridge .hbar.click .lbl", (ls) => ls.map((l) => l.textContent));
    for (const [i, l] of labels.entries()) {
      await page.$$eval("#cs-bridge .hbar.click", (rs, k) => rs[k].click(), i); await page.waitForTimeout(80);
      const n = await listAll(page, "cs-list");
      const want = /Churn/.test(l) ? counts.churned : /Downsell/.test(l) ? counts.contraction : counts.expansion;
      check(n === want && l.includes(`(${want})`), `business case: "${l}" lists ${want} accounts`, `${n}`);
    }
    await page.$$eval("#cs-bridge .hbar.click", (rs) => rs[0].click()); await page.waitForTimeout(80);
    // a long list shows 10 rows and "Show all (n)", opens in full, and folds back (Nikodem, 12 Nov 2026)
    await page.$$eval("#cs-bridge .hbar.click", (rs) => rs[0].click()); await page.waitForTimeout(80);
    const fold = async () => page.$eval("#cs-list", (h) => ({ rows: h.querySelectorAll("tbody tr").length, btn: (h.querySelector("[data-all]") || {}).textContent }));
    const f1 = await fold();
    await page.click("#cs-list [data-all]"); await page.waitForTimeout(60);
    const f2 = await fold();
    await page.click("#cs-list [data-all]"); await page.waitForTimeout(60);
    const f3 = await fold();
    check(Y.churned_n > 11 ? (f1.rows === 10 && f1.btn === `Show all (${Y.churned_n})` && f2.rows === Y.churned_n && f2.btn === "Show fewer" && f3.rows === 10) : true,
      `a long list shows 10 of ${Y.churned_n}, opens in full and folds back`, JSON.stringify([f1, f2, f3]));
    const churnRows = await page.$$eval("#cs-list tbody tr", (rs) => rs.map((r) => [/since left/.test(r.textContent), r.classList.contains("click")]));
    check(churnRows.length && churnRows.every(([gone, click]) => !gone || !click), "business case: an account that left is listed as such and opens nothing");
    // the forecast: the data's rule and what happened match the export, and a click lists its accounts
    const fc = await page.innerText("#cs-fc");
    const money = (v) => Math.abs(v) >= 1e6 ? "€" + (v / 1e6).toFixed(1).replace(/\.0$/, "") + "M" : "€" + Math.round(v / 1000) + "k";
    check(fc.includes(money(F.data_loss)) && fc.includes(money(F.actual_loss)), `business case: forecast ${money(F.data_loss)} against ${money(F.actual_loss)} that happened`, fc.slice(0, 120));
    await page.$$eval("#cs-fc .hbar.click", (rs) => rs[0].click()); await page.waitForTimeout(80);
    const fcRows = await listAll(page, "cs-list");
    check(fcRows === R.renewals_ahead.filter((r) => r[3] > 0).length, `business case: "Data's rule" lists its ${fcRows} renewals`);
    // what a call is worth: the export's groups, and today's accounts not called
    const cw = await page.innerText("#cs-call");
    const g = Object.fromEntries(R.calls.map((c) => [c.grp, c]));
    check(cw.includes(p1(g.in_time.kept_share)) && cw.includes(p1(g.late_or_never.kept_share)), "business case: kept shares match the export", cw.slice(0, 120));
    await page.click("#cs-call-waiting"); await page.waitForTimeout(80);
    const wn = await listAll(page, "cs-list");
    check(wn === R.call_waiting.length, `business case: ${R.call_waiting.length} Act accounts not called yet are listed`, `${wn}`);
    // the segment applies: Enterprise only
    await segment(page, { tier: "Enterprise" });
    const ent = R.cohort.filter((r) => { const a = D.accounts.find((x) => x.account_id === r[0]) || (D.history.off_book.find((x) => x.account_id === r[0])); return a && a.tier === "Enterprise"; });
    const eg = ent.reduce((t, r) => t + Math.min(r[2], r[3]), 0) / sumC(ent, 2);
    const et = await page.innerText("#cs-tiles");
    check(et.includes(p1(eg)), `business case, Enterprise: GRR ${p1(eg)} from the Enterprise cohort`, et.slice(0, 80));
    check(/isn't split by segment/.test(await page.textContent("#cs-bridge-note")), "business case: the year before isn't split by segment, and says so");
    check(!errors.length, "no script errors on the business case", errors.join("; "));
    await page.close();
  }


  // --- UX review, batch 1 (12 Nov 2026): each finding gets a check ---------------------------
  console.log("\nUX batch 1: one 'called' rule, book ARR, a member's pool tasks, the timeline, phone, keyboard");
  {
    let { page, errors } = await open(browser, { width: 1300 });
    // one rule for "called": Leadership's trail = time_to_action (no 'any meeting' fallback)
    const actIds = D.accounts.filter((a) => a.flags.some((f) => f.flag === "churn" && f.status === "Act") && a.owner_id && D.team.some((t) => t.owner_id === a.owner_id)).map((a) => a.account_id);
    const calledN = actIds.filter((id) => D.time_to_action.some((r) => r.account_id === id && r.flag === "churn" && r.call_date)).length;
    await tab(page, "lead");
    const trail = await page.innerText("#ld-tiles .trail").catch(() => "");
    check(new RegExp(`\\b${calledN}\\b`).test(trail), `Leadership's "called" = the team report's rule (${calledN} of ${actIds.length} Act accounts)`, trail.replace(/\n/g, " | "));
    await tab(page, "team");
    const sub = await page.textContent("#view-team .sub");
    check(/QBR held in the 30 days after/.test(sub) && !/QBRs don't count/.test(sub), "CS team says a QBR after the signal counts, like every other tab", sub);
    // book ARR: CSM and pool books, of the whole book
    await tab(page, "lead");
    const lf = await page.textContent("#ld-finding");
    const whole = D.accounts.reduce((t, a) => t + (a.arr || 0), 0);
    check(/on CSM and pool books/.test(lf) && lf.includes(`whole book ${money(whole)}`), `Leadership names CSM and pool books and the whole book (${money(whole)})`, lf);
    if (D.retention) {
      await tab(page, "case");
      const rec = await page.textContent("#cs-recon");
      check(rec.startsWith(`Book ${money(whole)} =`) && /with a named CSM or pool member/.test(rec), "Business case reconciles the book: owned + Self-service", rec);
    }
    // a pool member sees pool-queue tasks on their own accounts, tagged
    const member = D.team.find((t) => t.owner_type === "member" && D.tasks.some((k) => {
      const a = D.accounts.find((x) => x.account_id === k.account_id), q = D.team.find((x) => x.owner_id === k.routed_to);
      return a && a.owner_id === t.owner_id && q && q.owner_type === "pool" && (k.action_status === "Open" || k.action_status === "Claimed"); }));
    if (member) {
      const poolTasks = D.tasks.filter((k) => { const a = D.accounts.find((x) => x.account_id === k.account_id), q = D.team.find((x) => x.owner_id === k.routed_to);
        return a && a.owner_id === member.owner_id && q && q.owner_type === "pool" && (k.action_status === "Open" || k.action_status === "Claimed"); });
      await segment(page, { owner: member.owner_id });
      await tab(page, "tasks");
      const shown = await page.$$eval("#tk-list [data-open]", (bs) => bs.map((b) => b.dataset.open));
      check(poolTasks.every((k) => shown.includes(k.account_id)), `${member.owner_name}: the ${poolTasks.length} pool-queue task(s) on their accounts are in Tasks`, `${shown.length} shown`);
      check(/Pool queue · your account/.test(await page.textContent("#tk-list")), `${member.owner_name}: pool-queue tasks are tagged "Pool queue · your account"`);
      await segment(page, {});
    } else console.log("  SKIP  no pool member with a pool-queue task on their accounts");
    // the timeline: the account at a glance at the top, plans above the brief
    const acc = D.accounts.find((a) => a.flags.some((f) => f.flag === "churn" && f.status === "Act") && a.tier !== "Self-service");
    await page.close();
    ({ page } = await open(browser, { width: 1300, hash: "#" + acc.account_id }));
    page.on("pageerror", (e) => errors.push(e.message));
    const headTxt = await page.textContent("#tl-head");
    check(headTxt.includes(acc.account_name) && headTxt.includes(money(acc.arr)) && /Renews/.test(headTxt) && /churn · Act/.test(headTxt),
      "Timeline opens with the account: name, ARR, renewal, its top flag", headTxt);
    const order = await page.evaluate(() => ["tl-head", "tl-next", "tl-handover", "tl-chart"].map((i) => document.getElementById(i))
      .filter((n) => !n.closest("[hidden]")).map((n) => n.getBoundingClientRect().top));
    check(order.every((t, i) => i === 0 || order[i - 1] < t), "Timeline order: account, plan and forecast, (the brief,) then the chart", JSON.stringify(order));
    // keyboard: tabs move with the arrow keys; a chart mark opens with Enter
    await tab(page, "overview");
    await page.focus("#tab-overview"); await page.keyboard.press("ArrowRight"); await page.waitForTimeout(150);
    const sel = await page.$eval("nav [aria-selected=true]", (b) => b.id);
    check(sel === "tab-lead" && (await page.evaluate(() => document.activeElement.id)) === "tab-lead", "Arrow Right moves to the next tab", sel);
    await tab(page, "overview");
    const stage = await page.$("#ov-sieve g.stage");
    const reach = stage && await stage.evaluate((g) => g.getAttribute("tabindex") === "0" && g.getAttribute("role") === "button" && !!g.getAttribute("aria-label"));
    check(!!reach, "A sieve stage can be reached with Tab, with a role and a label");
    if (stage) { const before = await page.textContent("#ov-list-title");
      await stage.focus(); await page.keyboard.press("Enter"); await page.waitForTimeout(200);
      const after = await page.textContent("#ov-list-title");
      check(after !== before && (await page.$$("#ov-list tbody tr")).length > 0, "Enter on a stage lists its accounts", `${before} -> ${after}`); }
    check(await page.evaluate(() => document.documentElement.lang === "en"), "the page declares its language (en)");
    check(!errors.length, "no script errors in batch 1 checks", errors.join("; "));
    await page.close();
    // phone: tabs wrap, filters fold, tables become cards
    const { page: ph, errors: pe } = await open(browser, { width: 390 });
    check(await ph.isVisible("#seg-toggle") && await ph.isHidden("#seg-manager"), "Phone: the filters fold behind one button");
    await ph.click("#seg-toggle"); await ph.waitForTimeout(80);
    check(await ph.isVisible("#seg-manager"), "Phone: the button opens them");
    const navH = await ph.$eval("nav[role=tablist]", (n) => [n.scrollWidth <= n.clientWidth + 1, n.getBoundingClientRect().height]);
    check(navH[0] && navH[1] > 40, "Phone: every tab is in sight (they wrap, no hidden scroll)", JSON.stringify(navH));
    await tab(ph, "lead");
    const card = await ph.$eval("#ld-team", (t) => t.classList.contains("cards") && !!t.querySelector("td[data-label]") && getComputedStyle(t.querySelector("tr.headrow, thead")).display === "none");
    check(card, "Phone: a table reads as cards, each value labelled with its column");
    check(!pe.length, "no script errors on the phone checks", pe.join("; "));
    await ph.close();
  }
  // --- Sortable tables (12 Nov 2026, Nikodem): click a heading to sort ---
  {
    const parse = (t) => { const m = t.match(/([−-])?€?(\d[\d,]*(?:\.\d+)?)\s?([kM])?/); return m ? parseFloat(m[2].replace(/,/g, "")) * (m[3] === "M" ? 1e6 : m[3] === "k" ? 1e3 : 1) : null; };
    const { page, errors } = await open(browser, { width: 1300 });
    await tab(page, "lead");
    const col = (sel, i) => page.$$eval(sel, (rs, i) => rs.filter((r) => !r.querySelector("th") && !r.classList.contains("group") && !r.classList.contains("pinned")).map((r) => r.cells[i] ? r.cells[i].textContent.trim() : ""), i);
    const head = (n) => `#ld-team tr:first-child th:nth-child(${n})`;
    const orig = await col("#ld-team tr", 1);
    await page.click(head(2)); await page.waitForTimeout(120);
    const desc = (await col("#ld-team tr", 1)).map(parse);
    check(desc.every((v, i) => i === 0 || desc[i - 1] >= v), "Leadership team table: Book ARR, first click = biggest first", desc.slice(0, 4).join(", "));
    const vis = await page.$$eval("#ld-team tr", (rs) => rs.filter((r) => !r.querySelector("th") && !r.hidden).length);
    check(vis === Math.min(6, desc.length), "a folded table shows the top 6 after the sort", `${vis}`);
    check(/▼/.test(await page.textContent(head(2))) && (await page.getAttribute(head(2), "aria-sort")) === "descending", "the heading shows ▼ and aria-sort descending");
    await page.click(head(2)); await page.waitForTimeout(120);
    const asc = (await col("#ld-team tr", 1)).map(parse);
    check(asc.every((v, i) => i === 0 || asc[i - 1] <= v), "second click = smallest first");
    await page.click(head(2)); await page.waitForTimeout(120);
    check(JSON.stringify(await col("#ld-team tr", 1)) === JSON.stringify(orig), "third click = the table's own order again");
    // a short list sorts the whole list, not just the rows on screen
    await page.click("#ld-queue th:nth-child(4)"); await page.waitForTimeout(80);   // Waiting: biggest first
    await page.click("#ld-queue th:nth-child(4)"); await page.waitForTimeout(80);   // then smallest first
    const youngest = Math.min(...D.tasks.filter((t) => t.action_status === "Open").map((t) => t.age_days));
    const first = await page.$eval("#ld-queue tbody tr td:last-child", (td) => td.textContent.trim());
    check(first === `${youngest} days`, `"Not picked up": smallest first finds the youngest of all ${D.tasks.filter((t) => t.action_status === "Open").length} tasks, not just the 5 shown`, first);
    // the choice survives a redraw (a segment change)
    await page.click(head(2)); await page.waitForTimeout(80);
    await segment(page, { region: "UK&I" }); await page.waitForTimeout(150);
    const after = (await col("#ld-team tr", 1)).map(parse);
    check(/▼/.test(await page.textContent(head(2))) && after.every((v, i) => i === 0 || after[i - 1] >= v), "a sort is kept when the segment changes");
    // what isn't sortable: form columns, long text, fixed-order tables
    await segment(page, {});
    await tab(page, "tasks");
    const dec = await page.$$eval("#tk-list th", (ths) => ths.filter((t) => /Decision/.test(t.textContent)).map((t) => t.classList.contains("sortable")));
    check(dec.length && !dec[0], "Tasks: the Decision column (forms) isn't sortable");
    await tab(page, "outcomes");
    check(!(await page.$$eval("#oc-value th.sortable, #oc-matrix th.sortable", (t) => t.length)), "Did it work?: fixed-order tables aren't sortable");
    check(!errors.length, "no script errors while sorting", errors.join("; "));
    await page.close();
    // My book: rows stay inside their group (Churn risk / Growth / No flags)
    const { page: p2, errors: e2 } = await open(browser, { width: 1300 });
    await tab(p2, "mybook"); await segment(p2, { owner: "csm_mia" });
    const groups = () => p2.$$eval("#mb-port tbody tr", (rs) => { const out = []; let g = null; for (const r of rs) { if (r.classList.contains("group")) g = r.textContent.trim().split(" (")[0]; else out.push([g, r.dataset.id]); } return out; });
    const before = await groups();
    await p2.click("#mb-port th:nth-child(1)"); await p2.waitForTimeout(100);
    const after2 = await groups();
    const byGroup = (xs) => JSON.stringify(Object.entries(xs.reduce((m, [g, id]) => ((m[g] = m[g] || []).push(id), m), {})).map(([g, ids]) => [g, ids.sort()]));
    check(before.length > 1 && byGroup(before) === byGroup(after2), "My book: a sort keeps every account inside its group", `${before.length} rows`);
    check(!e2.length, "no script errors sorting My book", e2.join("; "));
    await p2.close();
  }

  await browser.close();
  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
})();
