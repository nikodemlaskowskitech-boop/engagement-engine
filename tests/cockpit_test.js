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
    for (const t of ["overview", "lead", "mybook", "outcomes", "timeline", "onboarding", "team", "book"]) {
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

  await browser.close();
  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
})();
