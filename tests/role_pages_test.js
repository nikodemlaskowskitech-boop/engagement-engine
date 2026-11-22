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
  const full = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "data", "views_data.json"), "utf8"));   // the whole export, to recompute from
  console.log("\nMy work (18 Nov 2026): a CSM's queue, by cash at stake per day left");
  {   // the leader: Operational per team, the backlog grouped by team, no decisions (22 Nov 2026)
    const { p } = await open("leader.html", "lead", true);
    await p.click("#tab-tasks"); await p.waitForTimeout(200);
    const first = await p.$$eval("#tk-funnel-rows thead th", (ths) => ths[0] && ths[0].textContent.replace(/[▲▼↕]/g, "").trim());
    const groups = await p.$$eval("#tk-list tr.group", (trs) => trs.map((t) => t.textContent));
    const forms = await p.$$eval("#tk-list select, #tk-list button.primary", (x) => x.length);
    check((await p.textContent("#tab-tasks")).trim() === "Operational" && first === "Team" && groups.length >= 1 && groups.every((g) => /team|CS ops/.test(g)) && !forms,
      "leader.html: Operational per team, the backlog grouped by team, no reassigning at book level", JSON.stringify({ first, groups, forms }));
    // PR2: signals that fired, coverage in value, CS team folded in (22 Nov 2026)
    const sigRows = await p.$$eval("#op-sig tbody tr", (trs) => trs.length);
    const teamIn = !!(await p.$("#view-tasks #op-team-slot #tm-cap")) && !(await p.$("#tab-team"));
    const cov = await p.$("#op-cov [data-cov]");
    const want = cov && +((await cov.evaluate((b) => b.nextElementSibling.textContent)).match(/of (\d+) account/) || [])[1];
    if (cov) { await cov.click(); await p.waitForTimeout(150); }
    const listed = await p.$$eval("#op-cov-list tbody tr", (trs) => trs.length);
    check(sigRows > 0 && teamIn && cov && listed === want && /biggest gap|had a meeting/.test(await p.textContent("#op-cov-finding")),
      "leader.html: Operational has signals that fired, coverage in value (a cell lists its accounts) and the CS team inside it", JSON.stringify({ sigRows, teamIn, want, listed }));
    // PR3: the churn-call evidence under Operational, "Did it work?" as the rules, Programmes
    check(!!(await p.$("#view-tasks #op-calls-slot #oc-calls table")) && (await p.textContent("#tab-onboarding")).trim() === "Programmes",
      "leader.html: \"Did a churn call help?\" sits in Operational; Onboarding is Programmes");
    await p.click("#tab-outcomes"); await p.waitForTimeout(150);
    const rules = await p.$$eval("#oc-rules tbody tr", (trs) => trs.map((tr) => [...tr.cells].map((c) => c.textContent)));
    const agree = rules.every(([, ev, bar, v]) => {
      if (/too few/.test(v)) return true;
      const got = +(ev.match(/\((\d+)%\)/) || [])[1], ok = /^Keep/.test(v);
      const m = bar.match(/^(\d+)%\+$/), le = bar.match(/^(\d+)% or less$/), band = bar.match(/^(\d+)–(\d+)%$/);
      return ok === (m ? got >= +m[1] : le ? got <= +le[1] : band ? got >= +band[1] && got <= +band[2] : ok);
    });
    check(rules.length === 3 && agree && !(await p.$("#view-outcomes #oc-calls-panel")), "leader.html: Did it work? gives each rule keep or change, matching its evidence and bar", JSON.stringify(rules));
    await p.click("#tab-lead"); await p.waitForTimeout(100);
    const go = await p.$("#ld-ops-go");
    if (go && await go.isVisible()) { await go.click(); await p.waitForTimeout(100);
      check((await p.getAttribute("#tab-tasks", "aria-selected")) === "true", "leader.html: the ops line opens Operational"); }
    await p.close();
  }
  for (const f of ["csm_anna.html", "csm_tom.html", "csm_mia.html", "pm_jonas.html", "mgr_sophie.html"]) {
    const m = meta(f), { p } = await open(f, "tasks", true);
    await p.click("#tab-tasks"); await p.waitForTimeout(150);
    const label = (await p.textContent("#tab-tasks")).trim();
    if (m.role === "manager") {          // managers and the leader keep the task table and its decisions
      const heads = await p.$$eval("#tk-list thead th", (ths) => ths.map((t) => t.textContent.replace(/[▲▼↕]/g, "").trim()));
      check(label === "Operational" && heads.includes("Decision") && !heads.includes("At stake"), `${f}: "Operational", the manager's table`, `${label} ${heads}`);
      // the funnel (22 Nov 2026): each stage a subset of the one above, per person at team level,
      // a stage click lists exactly the tasks that stopped before it
      const fn = await p.$$eval("#tk-funnel g.stage text.val", (ts) => ts.map((t) => +t.textContent));
      const persons = await p.$$eval("#tk-funnel-rows thead th", (ths) => ths[0] && ths[0].textContent.replace(/[▲▼↕]/g, "").trim());
      const sumNeeded = await p.$$eval("#tk-funnel-rows tbody tr", (trs) => trs.reduce((t, tr) => t + (+tr.cells[1].textContent), 0));
      check(fn.length === 5 && fn.every((v, i) => !i || v <= fn[i - 1]) && persons === "Person" && sumNeeded === fn[0],
        `${f}: the funnel narrows stage by stage, one row per person adding up (${fn.join(" > ")})`, JSON.stringify({ fn, persons, sumNeeded }));
      await p.click('#tk-funnel g.stage[data-key="picked"]'); await p.waitForTimeout(150);
      const listed = await p.$$eval("#tk-list tbody tr:not(.group)", (trs) => trs.length);
      const title = await p.textContent("#tk-title");
      check(listed === fn[1] - fn[2] && /Stopped before "picked up": \d+/.test(title),
        `${f}: "picked up" lists the ${fn[1] - fn[2]} tasks that stopped before it`, `${listed} ${title}`);
      await p.click("#tk-stage-clear"); await p.waitForTimeout(100);
      check(/^Backlog: \d+/.test(await p.textContent("#tk-title")), `${f}: "Show the backlog" goes back to the list`);
      await p.close(); continue;
    }
    const rows = await p.$$eval("table.queue tbody tr:not(.detail)", (trs) => trs.map((tr) => tr.querySelector("[data-open]").dataset.open));
    if (!rows.length) {                  // nothing open: the page says so, still under "My work"
      check(label === "My work" && /No tasks here|nothing to pick up/.test(await p.textContent("#view-tasks")), `${f}: "My work", nothing open, and it says so`, label);
      await p.close(); continue;
    }
    const data = await p.evaluate(() => JSON.parse(document.getElementById("views-data").textContent));
    const byId = Object.fromEntries(data.accounts.map((a) => [a.account_id, a]));
    const heads = await p.$$eval("table.queue thead th", (ths) => ths.map((t) => t.textContent.trim()));
    check(label === "My work" && heads.join() === "Account,ARR,At stake,Why,Reach by,Last,Next,Status" && !(await p.$("table.queue th.sortable")),
      `${f}: "My work", the queue's eight columns, in a fixed order (no re-sorting)`, `${label} | ${heads}`);
    check(!(await p.isVisible("#tk-banner")) || !/Manager/.test(await p.textContent("#tk-banner")), `${f}: no manager's instruction on a CSM's page`);
    // the order: cash at stake / days left (reach deadline at Act, else renewal), ARR breaks ties
    const day = (d) => Date.parse(d + "T00:00:00Z") / 864e5;
    const reach = (data.settings.reach_days || {});
    const key = (id) => {
      const a = byId[id] || {}, t = data.tasks.find((x) => x.account_id === id && ["Open", "Claimed"].includes(x.action_status)) || {};
      const fl = (a.flags || []).find((x) => x.flag === t.flag) || {};
      const ta = (data.time_to_action || []).find((x) => x.account_id === id && x.flag === t.flag);
      const left = t.status === "Act" && ta && !ta.call_date ? (reach[a.tier] || data.settings.reach_days_other) - (day(data.as_of) - day(ta.signal_date))
        : a.renewal_date ? day(a.renewal_date) - day(data.as_of) : 365;
      return (fl.value || 0) / Math.max(1, left);
    };
    const keys = rows.map(key);
    check(rows.length > 0 && keys.every((k, i) => i === 0 || keys[i - 1] >= k - 1e-9), `${f}: ${rows.length} rows, the most cash at stake per day left first`, JSON.stringify(rows.map((r, i) => [r, Math.round(keys[i])])));
    const last = await p.$("[data-last]");
    if (last) {
      await last.click(); await p.waitForTimeout(100);
      const det = await p.$eval("table.queue tr.detail", (d) => d.textContent).catch(() => "");
      await last.click(); await p.waitForTimeout(100);
      check(/^Last: .+, \d+ \w+/.test(det) && /Outcome:/.test(det) && !(await p.$("table.queue tr.detail")),
        `${f}: Last opens that engagement under its row (kind, date, outcome) and a second click closes it`, det.slice(0, 90));
    }
    const qIns = await p.$$eval("table.queue tbody tr:not(.detail)", (trs) => trs.map((tr) => [tr.querySelector("[data-open]").dataset.open, tr.querySelectorAll("[data-insight]").length]));
    const qBad = qIns.filter(([id, n]) => n !== ((((full.research || {})[id] || {}).themes || []).length ? 1 : 0));
    check(!qBad.length, `${f}: the queue shows an insight beside "Why" where the account's documents have a theme (${qIns.filter(([, n]) => n).length} of ${qIns.length})`, JSON.stringify(qBad));
    const nothing = await p.$$eval("table.queue .badge.nothing", (b) => b.length);
    const noPlan = rows.filter((id) => { const a = byId[id] || {}; return !(data.next_steps || []).some((s) => s.account_id === id) && !(a.next_qbr_date > data.as_of); }).length;
    check(nothing === noPlan, `${f}: "nothing planned" exactly where there's no next step and no QBR booked (${nothing})`, `${nothing} vs ${noPlan}`);
    await p.close();
  }
  console.log("\nMy book in value (18 Nov 2026): the leader's KPIs on the owner's book, same definitions");
  const eurTxt = (v) => { const a = Math.abs(v); return a >= 1e6 ? "€" + (v / 1e6).toFixed(1).replace(/\.0$/, "") + "M" : a >= 1e3 ? "€" + Math.round(v / 1e3) + "k" : "€" + Math.round(v); };
  for (const f of ["csm_anna.html", "csm_tom.html"]) {
    const m = meta(f), { p } = await open(f, "mybook", true);
    await p.click("#tab-mybook"); await p.waitForTimeout(200);
    const tiles = await p.$$eval("#mb-kpi .tile", (ts) => ts.map((t) => ({ k: t.querySelector(".k").textContent, v: t.querySelector(".v").textContent, r: t.querySelector(".ref").textContent })));
    // recomputed here from the whole export, independently of the page
    const mine = full.accounts.filter((a) => a.owner_id === m.person);
    const arr = mine.reduce((t, a) => t + (a.arr || 0), 0);
    const risk = mine.reduce((t, a) => t + (((a.flags || []).find((x) => x.flag === "churn") || {}).value || 0), 0);
    const day = (d) => Date.parse(d + "T00:00:00Z") / 864e5, run = day(full.as_of), win = full.retention.forecast.days;
    const fcs = {}; for (const x of full.forecasts || []) (fcs[x.account_id] = fcs[x.account_id] || []).push(x);
    const ups = mine.filter((a) => day(a.renewal_date) > run && day(a.renewal_date) <= run + win);
    const up = ups.reduce((t, a) => t + a.arr, 0);
    const loss = ups.reduce((t, a) => { const fc = (fcs[a.account_id] || []).slice(-1)[0], r = ((a.flags || []).find((x) => x.flag === "churn") || {}).value || 0;
      return t + (!fc ? r : fc.call === "churn" ? a.arr : fc.call === "downsize" && fc.amount != null ? Math.max(0, a.arr - fc.amount) : 0); }, 0);
    const bookGrr = 1 - full.retention.forecast.owner_loss / full.retention.forecast.up_for_renewal;
    check(tiles[0].v === eurTxt(arr) && tiles[0].r.startsWith(`${mine.length} account`), `${f}: My book ${eurTxt(arr)}, ${mine.length} accounts`, JSON.stringify(tiles[0]));
    check(tiles[1].v === eurTxt(risk), `${f}: ARR at risk ${eurTxt(risk)} (Act whole, Watch and Shelfware the unused part)`, JSON.stringify(tiles[1]));
    check(!up || (tiles[4].v === `${Math.round(100 * (up - loss) / up)}% kept` && tiles[4].r.includes(`the book: ${Math.round(100 * bookGrr)}%`)),
      `${f}: renewals in ${win} days, ${up ? Math.round(100 * (up - loss) / up) + "% kept" : "none"}, against the book's ${Math.round(100 * bookGrr)}% (retention.py's own forecast)`, JSON.stringify(tiles[4]));
    check(/median for CSMs: \d+%/.test(tiles[3].r), `${f}: met in 30 days, by ARR, against the CSMs' median by ARR`, tiles[3].r);
    const upside = mine.reduce((t, a) => t + (((a.flags || []).find((x) => x.flag === "expansion") || {}).value || 0), 0);
    check(tiles[2].k === "Upside in play" && tiles[2].v === eurTxt(upside), `${f}: upside in play ${eurTxt(upside)}, apart from the risk`, JSON.stringify(tiles[2]));
    const nGrow = mine.filter((a) => (a.flags || []).some((x) => x.flag === "expansion")).length;
    check(nGrow ? tiles[2].r.startsWith(`${nGrow} account`) : tiles[2].r === "no growth signal",
      `${f}: the upside tile counts the same ${nGrow} account(s) the Growth view lists, even at €0 upside`, tiles[2].r);
    // insights beside "Why": the first theme from the account's documents, risk first on a churn row, growth
    // first on a growth row; none without documents; the flag's status is the export's, untouched
    await p.click("#mb-port-view [data-pv=all]"); await p.waitForTimeout(100);
    const showRest = await p.$("#mb-port [data-rest]"); if (showRest && (await showRest.textContent()) === "show") { await showRest.click(); await p.waitForTimeout(100); }
    const rows = await p.$$eval("#mb-port tr.click", (rs) => rs.map((r) => ({ id: r.dataset.id, group: (() => { let g = r.previousElementSibling; while (g && !g.classList.contains("group")) g = g.previousElementSibling; return g ? g.textContent.trim() : ""; })(),
      pill: (r.querySelector(".flag .pill") || {}).textContent || null, ins: [...r.querySelectorAll("[data-insight]")].map((b) => b.textContent) })));
    const grow = new Set(full.settings.research_growth_themes), R = full.research || {};
    let bad = [], seen = 0;
    for (const r of rows) {
      const a = mine.find((x) => x.account_id === r.id), side = /^Growth/.test(r.group) ? "expansion" : /^Churn/.test(r.group) ? "churn" : null;
      const th = ((R[r.id] || {}).themes || []).slice().sort((x, y) => side === "expansion" ? grow.has(y.theme) - grow.has(x.theme) : grow.has(x.theme) - grow.has(y.theme));
      const flag = side && (a.flags || []).find((x) => x.flag === side);
      if (th.length) seen++;
      if (r.ins.length !== (th.length ? 1 : 0) || (th.length && !r.ins[0].startsWith("Insight" + th[0].label))) bad.push(`${r.id}: ${JSON.stringify(r.ins)} vs ${th.map((t) => t.label)}`);
      if (flag && r.pill !== flag.status) bad.push(`${r.id}: status ${r.pill} vs ${flag.status}`);
    }
    check(!bad.length, `${f}: ${seen} row(s) carry an insight from their documents, one each, the flag's status untouched`, bad.join("; "));
    if (seen) {
      await p.click("#mb-port [data-insight]"); await p.waitForTimeout(250);
      check((await p.getAttribute("#tab-timeline", "aria-selected")) === "true" && !(await p.$eval("#tl-research", (e) => e.hidden)),
        `${f}: an insight opens the account at its research`, "");
      await p.click("#tab-mybook"); await p.waitForTimeout(150);
    }
    for (const [k, want] of [["growth", (r) => r.every((x) => /Growth/.test(x) || x === "")], ["churn", (r) => !r.some((x) => /^Growth/.test(x))]]) {
      await p.click(`#mb-port-view [data-pv=${k}]`); await p.waitForTimeout(100);
      const groups = await p.$$eval("#mb-port tr.group td", (t) => t.map((x) => x.textContent.trim()));
      check(want(groups) && groups.length, `${f}: the portfolio's ${k} view shows only ${k === "growth" ? "growth" : "churn"} groups`, groups.join(" | "));
    }
    await p.close();
  }
  console.log("\nMy week (20 Nov 2026): the next N days on the owner's accounts, in value");
  for (const f of ["csm_anna.html", "csm_tom.html", "csm_mia.html"]) {
    const m = meta(f), { p } = await open(f, "tasks", true);
    await p.click("#tab-tasks"); await p.waitForTimeout(200);
    const win = full.settings.my_week_days, day = (d) => Date.parse(d + "T00:00:00Z") / 864e5, run = day(full.as_of) + 1;
    const inWin = (d) => d && day(d) > run && day(d) <= run + win;
    const mine = full.accounts.filter((a) => a.owner_id === m.person);
    const steps = {}; for (const x of full.next_steps || []) steps[x.account_id] = x;      // the export is oldest first
    const want = [];
    for (const a of mine) {
      if (inWin(a.next_qbr_date)) want.push(["QBR", a.account_id]);
      if (inWin(a.renewal_date)) want.push(["Renewal", a.account_id]);
      if (steps[a.account_id] && inWin(steps[a.account_id].due)) want.push(["step", a.account_id]);
    }
    const title = await p.textContent("#tk-week-title"), fnd = await p.textContent("#tk-week-finding");
    const shown = +((title.match(/: (\d+)$/) || [])[1] ?? -1);
    const calendar = want.length, deadlines = shown - calendar;      // the rest are reach deadlines (Act tasks)
    const arr = [...new Set(want.map(([, id]) => id))].reduce((t, id) => t + (full.accounts.find((a) => a.account_id === id).arr || 0), 0);
    check(await p.isVisible("#tk-week-panel") && title.startsWith(`Next ${win} days`) && deadlines >= 0,
      `${f}: "Next ${win} days" above the queue: ${calendar} QBRs, renewals and next steps from the data, plus ${deadlines} reach deadline(s)`, `${title} | want ${JSON.stringify(want)}`);
    check(shown === 0 ? /Nothing booked or due/.test(fnd) : (deadlines > 0 || fnd.startsWith(eurTxt(arr))),
      `${f}: the finding is in value (${shown ? eurTxt(arr) + " of ARR" : "nothing booked"})`, fnd);
    await p.click("#tab-mybook"); await p.waitForTimeout(150);
    const line = await p.textContent("#mb-week");
    await p.click("#mb-week-go"); await p.waitForTimeout(150);
    check(/Next \d+ days|Nothing booked/.test(line) && (await p.getAttribute("#tab-tasks", "aria-selected")) === "true",
      `${f}: My book carries the week in one line, and "See My work" opens it`, line);
    await p.close();
  }
  console.log("\nResearch on the account page (20 Nov 2026): the redacted documents, the person's accounts only");
  {
    const m = meta("csm_anna.html"), { p } = await open("csm_anna.html", "timeline", true);
    const pageData = await p.evaluate(() => JSON.parse(document.getElementById("views-data").textContent));
    const mineIds = new Set(pageData.accounts.map((a) => a.account_id));
    check(Object.keys(pageData.research || {}).every((id) => mineIds.has(id)) && Object.keys(pageData.research).length > 0,
      `csm_anna.html: research for her own accounts only (${Object.keys(pageData.research).length} of ${Object.keys(full.research).length})`);
    const withThemes = Object.keys(pageData.research).find((id) => pageData.research[id].themes.length);
    await p.goto(p.url().split("#")[0] + "#" + withThemes); await p.reload(); await p.waitForTimeout(700);   // a hash alone doesn't reload
    const txt = await p.textContent("#tl-research");
    const R = pageData.research[withThemes];
    check(await p.isVisible("#tl-research") && txt.includes(R.themes[0].label) && txt.includes(R.themes[0].line.slice(0, 40)) && /check the source/.test(txt),
      `${withThemes}: its possible themes with the evidence line and a "check the source" note`, txt.slice(0, 160));
    // the account page in the CSM's order (21 Nov 2026): why now, then what to do, then the history
    const order = await p.$$eval("#view-timeline > .panel:not([hidden]), #view-timeline > .grid2", (els) => els.map((e) => e.id || e.className));
    check(order[0] === "tl-why" && /tl-plan/.test(order[1]), `${withThemes}: Why now comes first, then the plan and forecast`, JSON.stringify(order));
    const fA = pageData.accounts.find((a) => a.account_id === withThemes).flags;
    const why = await p.textContent("#tl-why");
    check(fA.every((f) => why.includes(f.status) && why.includes(f.reasons.slice(0, 30)) && (!f.action || why.includes(f.action.slice(0, 30)))) && why.includes(R.themes[0].label),
      `${withThemes}: Why now has every flag's reasons and to-do, and the insight from documents`, why.slice(0, 200));
    const calm = pageData.accounts.find((a) => !a.flags.length && (new Date(a.renewal_date) - new Date(pageData.as_of)) / 864e5 > 120);
    if (calm) { await p.goto(p.url().split("#")[0] + "#" + calm.account_id); await p.reload(); await p.waitForTimeout(600);
      check(/No open flags/.test(await p.textContent("#tl-why")) && !(await p.$("#tl-sum-line .bad")),
        `${calm.account_id}: a healthy account far from renewal reads calm: no alarm in red`, await p.textContent("#tl-sum-line")); }
    const none = pageData.accounts.find((a) => !pageData.research[a.account_id]);
    if (none) { await p.goto(p.url().split("#")[0] + "#" + none.account_id); await p.reload(); await p.waitForTimeout(600);
      check(/No tickets, emails or call notes in the last/.test(await p.textContent("#tl-research")), `${none.account_id}: says plainly there are no documents`); }
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
  console.log("\nA teammate's account: read only, and it says who can act (UX review 12 Nov 2026: no dead-end 'act as' buttons)");
  {
    const { p } = await open("csm_lukas.html", "acc_024", true);
    const next = await p.textContent("#tl-next"), fc = await p.textContent("#tl-forecast");
    const actAs = await p.$$eval("[data-actas]", (x) => x.length);
    check(!actAs && /Read only: Mia/.test(next) && /Read only: Mia/.test(fc) && !(await p.$("#fc-save")),
      "csm_lukas.html: Mia's acc_024 shows 'Read only: Mia … can …', no act-as buttons, no form", `${actAs} buttons; ${next.slice(0, 120)}`);
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
    // wait for the new page itself, not a fixed time: under load 600 ms read a half-drawn page
    // (18 Nov 2026, a flaky FAIL). Done = the page says it's that person and its tabs are drawn.
    const switchTo = async (f) => {
      const want = f.replace(/\.html$/, "");
      await p.selectOption("#page-switch", f);
      await p.waitForFunction((w) => {
        try { return JSON.parse(document.getElementById("page-meta").textContent).person === w
                     && document.readyState === "complete" && !!document.querySelector('nav button[aria-selected="true"]'); }
        catch (e) { return false; }
      }, want, { timeout: 15000 });
      await p.waitForTimeout(150);                 // the page's own first draw after load
    };
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
    // the walkthrough: an open account goes with the switch while it's on the new page
    const shown = () => p.evaluate(() => ({ view: (document.querySelector('nav button[aria-selected="true"]') || {}).dataset?.view,
      acc: document.getElementById("tl-account").value, note: document.getElementById("carry-note").hidden ? "" : document.getElementById("carry-note").textContent,
      owner: document.getElementById("seg-owner").value, manager: document.getElementById("seg-manager").value, region: document.getElementById("seg-region").value }));
    await switchTo("leader.html");
    await p.click("#tab-timeline"); await p.fill("#tl-find", "acc_024"); await p.keyboard.press("Enter"); await p.waitForTimeout(300);
    await p.evaluate(() => localStorage.setItem("cockpit-seg-csm_mia", JSON.stringify({ owner: "", region: "Nordics" })));   // an old filter of Mia's
    await switchTo("mgr_sophie.html"); let r = await shown();
    check(r.view === "timeline" && r.acc === "acc_024" && !r.note, "leader -> Sophie: Yarrow Studio (acc_024) stays open (it's her team's)", JSON.stringify(r));
    await switchTo("csm_mia.html"); r = await shown();
    check(r.view === "timeline" && r.acc === "acc_024" && r.owner === "csm_mia" && r.region === "",
      "-> Mia: still open, and her page starts on its defaults (an old saved filter ignored)", JSON.stringify(r));
    await switchTo("mgr_james.html"); r = await shown();
    check(r.view === "tasks" && r.note.includes("acc_024") && r.note.includes("isn't on") && r.manager === "mgr_james"
      && !(await p.evaluate(() => document.documentElement.innerHTML.includes("Yarrow Studio"))),
      "-> James: a note says acc_024 isn't on his page, it opens on his first tab, and its name isn't anywhere in the page", JSON.stringify(r));
    await p.click("#tab-timeline"); await p.click("#tab-tasks"); await switchTo("csm_tom.html"); r = await shown();
    check(r.view === "mybook" && !r.note && r.owner === "csm_tom", "not on the timeline: the next page simply opens on its defaults", JSON.stringify(r));
    // pinned accounts: Find in the bar pins; every tab shows exactly the pins; they survive tabs and switches
    const pin = async (id) => { await p.fill("#seg-find", id); await p.waitForTimeout(150); await p.keyboard.press("Enter"); await p.waitForTimeout(250); };
    const pins = () => p.$$eval("#seg-pins .pin", (c) => c.map((x) => x.firstChild.textContent));
    await switchTo("csm_mia.html");
    const mia = meta("csm_mia.html"), teammate = mia.accounts.find((a) => !mia.edit.forecast.includes(a));
    await pin(mia.edit.forecast[0]); await pin(teammate);
    await p.click("#tab-mybook"); await p.click("#tab-tasks");          // a CSM has three tabs (no Book of business)
    const count = await p.textContent("#seg-count");
    const taskAccs = await p.$$eval("#tk-list [data-open]", (b) => [...new Set(b.map((x) => x.dataset.open))]);
    check((await pins()).length === 2 && count.startsWith("2 of") && taskAccs.every((a) => [mia.edit.forecast[0], teammate].includes(a)),
      "Mia pins her account and a teammate's: two chips, '2 of 98', the Tasks tab only on those two (still after changing tab)", JSON.stringify({ pins: await pins(), count, taskAccs }));
    await p.click("#seg-pins .pin-x"); await p.waitForTimeout(150);
    check((await pins()).length === 1, "x unpins one", JSON.stringify(await pins()));
    await pin(mia.edit.forecast[0]);                      // back to two: one of Sophie's team's
    await switchTo("mgr_james.html"); r = await shown();
    check((await pins()).length === 0 && r.note.includes("2 of 2 pinned accounts aren't on James's page"),
      "-> James: the two pins (Sophie's team's) stay behind, and the note says so", JSON.stringify({ pins: await pins(), note: r.note }));
    await switchTo("mgr_sophie.html");
    await pin(mia.edit.forecast[0]); await pin(teammate);
    await switchTo("csm_lukas.html");
    check((await pins()).length === 2 && !(await shown()).note, "Sophie -> Lukas (same team): both pins travel, no note", JSON.stringify(await pins()));
    check(!errors.length, "no script error in the walkthrough", errors.join(" / "));
    await p.close(); server.close();
  }
  await b.close();
  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
})();
