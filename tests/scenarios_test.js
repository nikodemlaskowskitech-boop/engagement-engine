// Scenario tests: the 23 acceptance scenarios, automated. Each one plays a
// role (Mia the CSM, Priya the pool member, Sophie the manager, the CS leader,
// an admin), works out the expected result from the data itself, and checks
// the cockpit against it. Nobody needs to know the data by heart.
//
//   node tests/scenarios_test.js     (after python export_views.py)
//
// Writes data/scenario_results.json ({ s01: {status, note}, ... }), which
// Claude copies into the acceptance page. Status: pass, fail or manual (needs
// Slack or a person). Exit 1 on any fail. Needs Node + Playwright (Chromium).

const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");
const { chromium } = require("playwright");

const ROOT = path.resolve(__dirname, "..");
const PAGE = "file://" + path.join(ROOT, "data", "views.html");
const D = JSON.parse(fs.readFileSync(path.join(ROOT, "data", "views_data.json"), "utf8"));
const results = {};
const record = (id, ok, note) => { results[id] = { status: ok === null ? "manual" : ok ? "pass" : "fail", note };
  console.log(`  ${ok === null ? "MANUAL" : ok ? "PASS  " : "FAIL  "} ${id}  ${note}`); };

// --- what the data says (the expected results) ------------------------------------
const CUR = D.settings.currency === "EUR" ? "€" : (D.settings.currency || "") + " ";
const money = (v) => Math.abs(v) >= 1e6 ? CUR + (v / 1e6).toFixed(1).replace(/\.0$/, "") + "M"
  : Math.abs(v) >= 1000 ? CUR + Math.round(v / 1000) + "k" : CUR + Math.round(v);
const flag = (a, f) => a.flags.find((x) => x.flag === f);
const value = (a, f) => (flag(a, f) || {}).value || 0;
const team = Object.fromEntries(D.team.map((t) => [t.owner_id, t]));
const byId = Object.fromEntries(D.accounts.map((a) => [a.account_id, a]));
const booked = (a) => a.next_qbr_date && a.next_qbr_date > D.as_of;
const workOf = (o) => D.tasks.filter((x) => x.routed_to === o || (team[o].owner_type === "member" && x.routed_to === team[o].pool && (byId[x.account_id] || {}).owner_id === o));

const FAKE_DB = `(() => { const docs = {}; let cb = null;
  const snap = () => ({ docs: Object.entries(docs).map(([id, v]) => ({ id, data: () => v })) });
  const db = { collection: () => ({ onSnapshot: (f) => { cb = f; f(snap()); },
    doc: (id) => ({ set: async (v) => { docs[id] = v; cb && cb(snap()); }, delete: async () => { delete docs[id]; cb && cb(snap()); } }) }) };
  window.__docs = docs; window.claude = { use: async (n) => n === "db" ? db : n === "user" ? { id: async () => "tester" } : null }; })();`;

async function open(browser, opts = {}) {
  const page = await browser.newPage({ viewport: { width: opts.width || 1300, height: 900 }, colorScheme: opts.scheme || "light" });
  page.setDefaultTimeout(5000);
  const errors = []; page.on("pageerror", (e) => errors.push(e.message));
  if (opts.db) await page.addInitScript(FAKE_DB);
  await page.addInitScript(() => { try { localStorage.clear(); } catch (e) { /* none */ } });
  await page.goto(PAGE + (opts.hash || "")); await page.waitForTimeout(700);
  page.errors = errors; return page;
}
const tab = async (p, t) => { await p.click("#tab-" + t); await p.waitForTimeout(150); };
const seg = async (p, values) => { await p.click("#seg-reset").catch(() => {}); for (const [k, v] of Object.entries(values)) { await p.selectOption("#seg-" + k, v); await p.waitForTimeout(120); } };
const text = (p, s) => p.textContent(s);
const goTo = async (p, id) => { await tab(p, "timeline"); await p.fill("#tl-find", id); await p.keyboard.press("Enter"); await p.waitForTimeout(250); };
const due = (days) => new Date(Date.parse(D.as_of) + days * 864e5).toISOString().slice(0, 10);

(async () => {
  const browser = await chromium.launch();
  const run = async (id, fn) => { try { await fn(); } catch (e) { record(id, false, "error: " + e.message.split("\n")[0]); } };

  // ======================= CSM: Mia Hofer =========================================
  console.log("\nCSM: Mia Hofer");
  const mia = D.accounts.filter((a) => a.owner_id === "csm_mia");
  const miaChurn = mia.filter((a) => flag(a, "churn")).sort((a, b) => value(b, "churn") - value(a, "churn"));
  await run("s01", async () => {
    const p = await open(browser); await seg(p, { owner: "csm_mia" }); await tab(p, "mybook");
    const work = await text(p, "#mb-work"), first = await p.$eval("#mb-port tr.click", (tr) => tr.dataset.id);
    const ok = work.includes("median for CSMs") && first === miaChurn[0].account_id;
    record("s01", ok, `riskiest first: expected ${miaChurn[0].account_name} (${money(value(miaChurn[0], "churn"))}), got ${byId[first].account_name}; workload compared with the CSM median`);
    await p.close();
  });
  await run("s02", async () => {
    const p = await open(browser); await seg(p, { owner: "csm_mia" });
    const risk = money(miaChurn.reduce((t, a) => t + value(a, "churn"), 0));
    await tab(p, "mybook"); const mb = await text(p, "#mb-port-finding");
    await tab(p, "lead"); const ld = await text(p, "#ld-finding");
    record("s02", mb.startsWith(risk) && ld.includes(risk), `ARR at risk ${risk} in My book and on Leadership`); await p.close();
  });
  const target = miaChurn.find((a) => flag(a, "churn").status === "Act" && !booked(a)) || miaChurn[0];
  await run("s03", async () => {
    const p = await open(browser, { db: true }); await seg(p, { owner: "csm_mia" }); await goTo(p, target.account_id);
    await p.fill("#ns-due", due(10)); await p.fill("#ns-note", "Scenario call"); await p.click("#ns-save"); await p.waitForTimeout(250);
    const tl = await text(p, "#tl-next"), sum = await text(p, "#tl-sum-line");
    await tab(p, "mybook"); const row = await p.$eval(`#mb-port tr[data-id="${target.account_id}"]`, (tr) => tr.textContent);
    await tab(p, "lead"); const more = await p.$("#ld-churn [data-more]"); if (more) await more.click();
    const lead = await p.$eval(`#ld-churn tr[data-id="${target.account_id}"]`, (tr) => tr.textContent).catch(() => "");
    const ok = tl.includes("Scenario call") && sum.includes("Next: customer call") && row.includes("Customer call") && lead.includes("Customer call");
    record("s03", ok, `next step on ${target.account_name} shows on the timeline, summary, My book and Leadership`); await p.close();
  });
  await run("s04", async () => {
    const p = await open(browser, { db: true }); await seg(p, { owner: "csm_mia" }); await goTo(p, target.account_id);
    await p.click("#fc-save"); await p.waitForTimeout(100); const refused = (await text(p, "#fc-msg")).includes("reason");
    await p.selectOption("#fc-call", "renew"); await p.fill("#fc-reason", "Scenario: sponsor confident"); await p.click("#fc-save"); await p.waitForTimeout(250);
    const saved = (await text(p, "#tl-forecast")).includes("Renew in full");
    const gapExpected = value(target, "churn") >= (D.settings.forecast_gap_share ?? 0.25) * target.arr;
    await tab(p, "mybook"); const row = await p.$eval(`#mb-port tr[data-id="${target.account_id}"]`, (tr) => tr.textContent);
    const ok = refused && saved && row.includes("≠ data") === gapExpected;
    record("s04", ok, `no reason refused; 'renew in full' saved; '≠ data' ${gapExpected ? "shown" : "not shown"} as expected (data says ${money(value(target, "churn"))} at risk)`); await p.close();
  });
  await run("s05", async () => {
    const p = await open(browser); await seg(p, { owner: "csm_mia" }); await tab(p, "mybook");
    const row = await p.$("#mb-port tr.click"); const id = await row.evaluate((tr) => tr.dataset.id);
    await row.scrollIntoViewIfNeeded(); const y = await p.evaluate(() => scrollY); await row.click(); await p.waitForTimeout(250);
    const touches = D.touches.filter((t) => t.account_id === id && ["meeting_held", "email_sent", "workshop_attended"].includes(t.event)).length;
    const bold = await p.$$eval("#tl-done li b", (b) => b.length); const allBtn = await p.$("#tl-done-all");
    const boldOk = allBtn ? bold === Math.min(touches, bold) && bold > 0 : bold === touches;
    await p.click("#tl-back"); await p.waitForTimeout(250);
    const back = (await p.isVisible("#view-mybook")) && Math.abs((await p.evaluate(() => scrollY)) - y) < 5;
    record("s05", boldOk && back, `summary of ${byId[id].account_name} lists its customer actions in bold (${touches} in 90 days); Back returns to the same spot`); await p.close();
  });
  await run("s06", async () => {
    const p = await open(browser); await seg(p, { owner: "csm_mia" }); await p.click("#seg-find"); await p.waitForTimeout(150);
    const offered = await p.$$eval("#seg-find-list li[data-id]", (l) => l.map((x) => x.dataset.id));
    const ok = offered.length === mia.length && offered.every((x) => byId[x].owner_id === "csm_mia");
    record("s06", ok, `Find offers ${offered.length} accounts, all Mia's (she has ${mia.length})`); await p.close();
  });

  // ======================= Pool member: Priya Shah ================================
  console.log("\nPool member: Priya Shah");
  const priya = D.accounts.filter((a) => a.owner_id === "pm_priya");
  await run("s07", async () => {
    const p = await open(browser); await seg(p, { owner: "pm_priya" }); await tab(p, "mybook");
    const port = await text(p, "#mb-port-finding"), work = await text(p, "#mb-work");
    const live = workOf("pm_priya").filter((t) => ["Open", "Claimed"].includes(t.action_status)).length;
    const ok = port.includes(`of ${priya.length} accounts`) && work.includes("median for pool members") && (await text(p, "#mb-finding")).includes(`${live} task`);
    record("s07", ok, `${priya.length} accounts in her book; ${live} tasks on her plate (queue tasks on her accounts); compared with pool members`); await p.close();
  });
  await run("s08", async () => {
    const acc = priya.find((a) => flag(a, "churn")) || priya[0];
    const p = await open(browser, { db: true }); await seg(p, { owner: "pm_priya" }); await goTo(p, acc.account_id);
    await p.fill("#ns-due", due(7)); await p.click("#ns-save"); await p.waitForTimeout(200);
    await p.fill("#fc-reason", "Scenario"); await p.click("#fc-save"); await p.waitForTimeout(200);
    const docs = await p.evaluate(() => Object.values(window.__docs));
    const tasks = D.tasks.filter((t) => t.account_id === acc.account_id);
    const ok = docs.length === 2 && docs.every((d) => d.by === "pm_priya") && tasks.every((t) => t.routed_to === "pool_uki" || t.routed_to.startsWith("mgr_") || t.routed_to === "cs_ops");
    record("s08", ok, `plan and forecast on ${acc.account_name} saved as Priya; its ${tasks.length} task(s) still go to the UK&I queue`); await p.close();
  });

  // ======================= Manager: Sophie ========================================
  console.log("\nManager: Sophie");
  const sophiePeople = D.team.filter((t) => t.manager === "mgr_sophie");
  await run("s09", async () => {
    const p = await open(browser); await seg(p, { manager: "mgr_sophie" });
    const teams = (await p.$$eval("#seg-team option", (o) => o.map((x) => x.value))).filter(Boolean);
    const owners = (await p.$$eval("#seg-owner option", (o) => o.map((x) => x.value))).filter(Boolean);
    const expTeams = [...new Set(sophiePeople.map((t) => t.region))].sort();
    const ok = JSON.stringify(teams.slice().sort()) === JSON.stringify(expTeams) && owners.every((o) => team[o].manager === "mgr_sophie");
    record("s09", ok, `Team offers ${teams.join(", ")}; Owner offers only her ${owners.length} people`); await p.close();
  });
  await run("s10", async () => {
    const p = await open(browser, { db: true }); await seg(p, { manager: "mgr_sophie" }); await tab(p, "tasks");
    const sel = await p.$("#tk-list select[data-assign]");
    if (!sel) { record("s10", null, "no open task of Sophie's team to give away in this data"); await p.close(); return; }
    const other = await sel.$$eval("optgroup[label='Other teams'] option", (o) => o.map((x) => x.value));
    await sel.selectOption(other[0]); await p.waitForTimeout(250);
    const doc = (await p.evaluate(() => Object.values(window.__docs)))[0] || {};
    const ok = (await text(p, "#tk-list")).includes("on the next run") && doc.to_owner === other[0] && doc.manager === "mgr_sophie";
    record("s10", ok ? null : false, ok ? `given to ${team[other[0]].owner_name} (${team[other[0]].region}) and saved; the Slack notices need a run with -Actions: check by hand` : "the reassignment didn't save"); await p.close();
  });
  await run("s11", async () => {
    const p = await open(browser); await seg(p, { manager: "mgr_sophie" }); await tab(p, "team");
    const rows = await p.$$eval("#tm-cap tr", (trs) => trs.slice(1).map((tr) => [...tr.cells].map((c) => c.textContent)));
    const poolRoom = rows.some((r) => / team$/.test(r[0]) && r[5] === "room");
    const opens = rows.every((r) => { const o = D.team.find((t) => t.owner_name === r[0]); return o && parseInt(r[3]) === workOf(o.owner_id).filter((t) => t.action_status === "Open").length; });
    record("s11", !poolRoom && opens, `${rows.length} rows; open tasks match the task list for each; no pool marked 'room'`); await p.close();
  });
  await run("s12", async () => {
    const p = await open(browser); await seg(p, { manager: "mgr_sophie" }); await tab(p, "tasks");
    const shown = await p.$$eval("#tk-list tbody tr", (trs) => trs.filter((tr) => tr.textContent.includes("No meeting in 30 days")).length);
    const exp = D.tasks.filter((t) => t.status === "No meeting in 30 days" && t.manager === "mgr_sophie" && ["Open", "Claimed"].includes(t.action_status)).length;
    record("s12", shown === exp, `${shown} 'No meeting in 30 days' escalations shown, ${exp} expected`); await p.close();
  });

  // ======================= CS leader ==============================================
  console.log("\nCS leader");
  const people = D.accounts.filter((a) => a.tier !== "Self-service");
  const act = people.filter((a) => (flag(a, "churn") || {}).status === "Act");
  await run("s13", async () => {
    const p = await open(browser); await tab(p, "lead"); const tiles = await text(p, "#ld-tiles");
    const risk = money(people.reduce((t, a) => t + value(a, "churn"), 0));
    const called = act.filter((a) => { const t = D.time_to_action.find((r) => r.account_id === a.account_id && r.flag === "churn"); return t ? !!t.call_date : false; }).length;
    const ok = tiles.includes(risk) && tiles.includes(`${act.length} at Act`) && tiles.includes("Act: next step planned") && tiles.includes("Forecast ≠ data");
    record("s13", ok, `tiles show ${risk} at risk, ${act.length} at Act, called and planned counts, forecasts ≠ data (data: about ${called} Act accounts called)`); await p.close();
  });
  await run("s14", async () => {
    const p = await open(browser); await tab(p, "lead");
    for (const h of ["#ld-churn", "#ld-growth"]) { const m = await p.$(h + " [data-more]"); if (m) await m.click(); }
    const order = async (h, f) => (await p.$$eval(h + " tr.click", (t) => t.map((x) => x.dataset.id))).map((id) => value(byId[id], f));
    const sorted = (xs) => xs.every((v, i) => i === 0 || xs[i - 1] >= v);
    const c = await order("#ld-churn", "churn"), g = await order("#ld-growth", "expansion");
    record("s14", sorted(c) && sorted(g) && c.length && g.length, `churn list (${c.length}) by ARR at risk, growth list (${g.length}) by upside, biggest first`); await p.close();
  });
  await run("s15", async () => {
    const p = await open(browser); await tab(p, "outcomes"); await p.click("#oc-value tr[data-k='Act']"); await p.waitForTimeout(200);
    const exp = D.renewals.filter((r) => r.flag_status === "Act" && r.renewal_arr != null).length;
    record("s15", (await text(p, "#oc-list-title")).includes(`${exp} account`), `the Act row lists its ${exp} renewals`); await p.close();
  });
  await run("s16", async () => {
    const act = D.renewals.filter((r) => r.flag_status === "Act" && r.renewal_arr != null);
    const lost = act.reduce((t, r) => t + r.lost_arr, 0), arr = act.reduce((t, r) => t + r.arr, 0);
    const now = Math.round(100 * lost / arr), share = Math.round(100 * lost / arr) / 100, after = Math.round(100 * lost / (share * arr));
    record("s16", null, `today Act loses ${now}% of what the rule puts at risk; Act = ${share.toFixed(2)} in [money.at_risk] would bring it to ${after}%. Try it in settings.local.toml if you want to see it move`);
  });

  // ======================= Admin and CS ops =======================================
  console.log("\nAdmin and CS ops");
  await run("s17", async () => {
    const log = path.join(ROOT, "data", "last_run.txt");
    if (!fs.existsSync(log)) { record("s17", null, "no data/last_run.txt here: Claude checks it on your laptop after each run"); return; }
    const t = fs.readFileSync(log, "utf8"), done = t.match(/DONE: (\d+) of (\d+)/), pass = (t.match(/PASS/g) || []).length;
    record("s17", !!done && done[1] === done[2] && !/FAIL|Traceback/.test(t), `last run: ${done ? done[0] : "not finished"}, ${pass} PASS`);
  });
  await run("s18", async () => {
    // someone else's export with a column missing: the input check must name it
    const tmp = fs.mkdtempSync(path.join(require("os").tmpdir(), "export-"));
    const crm = fs.readFileSync(path.join(ROOT, "data", "crm_accounts.csv"), "utf8").split("\n");
    const cols = crm[0].split(","), drop = cols.indexOf("renewal_date");
    fs.writeFileSync(path.join(tmp, "crm_accounts.csv"), crm.map((l) => l.split(",").filter((_, i) => i !== drop).join(",")).join("\n"));
    fs.copyFileSync(path.join(ROOT, "data", "cs_team.csv"), path.join(tmp, "cs_team.csv"));
    let out = "", code = 0;
    try { out = execFileSync(process.platform === "win32" ? "python" : "python3", [path.join(ROOT, "check_inputs.py"), tmp], { encoding: "utf8" }); }
    catch (e) { out = (e.stdout || "") + (e.stderr || ""); code = e.status; }
    const ok = code === 1 && /missing column.*renewal_date/.test(out) && out.includes("What the engine can do");
    record("s18", ok, `an export without renewal_date: the input check stops (exit ${code}), names the column and says what still works`);
  });
  record("s19", null, "needs Slack: run .\\run_all.ps1 -Actions twice; the second run should say 'Already done, skipped' and post nothing new");
  await run("s20", async () => {
    const p = await open(browser); await tab(p, "tasks");
    const banner = await text(p, "#tk-banner"), t = D.accounts.find((a) => a.tier === "Enterprise" && a.owner_id);
    await goTo(p, t.account_id); const tl = await text(p, "#tl-next");
    record("s20", banner.includes("read-only") && tl.includes("published cockpit") && !p.errors.length, "the file opens without the page database: every tab works, saving says it happens in the published cockpit"); await p.close();
  });

  // ======================= Anyone =================================================
  console.log("\nAnyone");
  await run("s21", async () => {
    const p = await open(browser, { width: 390 }); const wide = [];
    await seg(p, { owner: "csm_mia" });
    for (const t of ["mybook", "lead", "timeline", "tasks"]) { await tab(p, t); const w = await p.evaluate(() => document.documentElement.scrollWidth); if (w > 391) wide.push(`${t} ${w}px`); }
    const usable = await p.isVisible("#seg-owner");
    record("s21", !wide.length && usable, wide.length ? "scrolls sideways: " + wide.join(", ") : "at phone width no tab scrolls sideways and the filters are usable"); await p.close();
  });
  await run("s22", async () => {
    // contrast of text against its background, dark mode, on the busiest tabs
    const p = await open(browser, { scheme: "dark" }); const low = [];
    for (const t of ["lead", "mybook", "outcomes", "timeline"]) {
      await tab(p, t);
      low.push(...await p.evaluate((t) => {
        const lum = (c) => { const m = c.match(/\d+(\.\d+)?/g).map(Number).slice(0, 3).map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }); return 0.2126 * m[0] + 0.7152 * m[1] + 0.0722 * m[2]; };
        const bgOf = (el) => { while (el) { const b = getComputedStyle(el).backgroundColor; if (b && !/rgba\(.*, 0\)|transparent/.test(b)) return b; el = el.parentElement; } return getComputedStyle(document.body).backgroundColor; };
        const out = [];
        for (const el of document.querySelectorAll(`#view-${t} p, #view-${t} td, #view-${t} h2, #view-${t} small, #view-${t} .k, #view-${t} .v`)) {
          if (!el.offsetParent || !el.textContent.trim()) continue;
          const a = lum(getComputedStyle(el).color), b = lum(bgOf(el)), r = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
          if (r < 3) out.push(`${t}: "${el.textContent.trim().slice(0, 30)}" ${r.toFixed(1)}:1`);
        }
        return out.slice(0, 3);
      }, t));
    }
    record("s22", !low.length, low.length ? "hard to read in dark mode: " + low.join("; ") : "all text on Leadership, My book, Did it work? and the timeline has at least 3:1 contrast in dark mode"); await p.close();
  });
  await run("s23", async () => {
    const p = await open(browser); const bad = [];
    for (const key of ["act", "nocall"]) {
      const n = parseInt(await p.$eval(`#ov-sieve g.stage[data-key="${key}"] text.val`, (t) => t.textContent));
      await p.click(`#ov-sieve g.stage[data-key="${key}"] rect`); await p.waitForTimeout(150);
      const title = await text(p, "#ov-list-title"); if (!title.includes(`${n} account`)) bad.push(`sieve ${key}: ${n} vs "${title}"`);
    }
    await tab(p, "outcomes");
    const btn = await p.$("#oc-matrix .cellbtn"); const v = parseInt(await btn.$eval(".v", (x) => x.textContent));
    await btn.click(); await p.waitForTimeout(150); const title = await text(p, "#oc-list-title");
    if (!title.includes(`${v} account`)) bad.push(`matrix: ${v} vs "${title}"`);
    record("s23", !bad.length, bad.length ? bad.join("; ") : "clicked numbers on Overview and Did it work? list exactly that many accounts"); await p.close();
  });

  await browser.close();
  fs.writeFileSync(path.join(ROOT, "data", "scenario_results.json"), JSON.stringify(results, null, 1));
  const n = Object.values(results).reduce((c, r) => (c[r.status] = (c[r.status] || 0) + 1, c), {});
  console.log(`\n${n.pass || 0} pass, ${n.fail || 0} fail, ${n.manual || 0} need a person (Slack or a choice). Saved data/scenario_results.json`);
  process.exit(n.fail ? 1 : 0);
})();
