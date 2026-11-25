(function () {
  "use strict";
  const D = JSON.parse(document.getElementById("views-data").textContent);
  // One page per person (ROLES.md): which role, which tabs, and what this person may change.
  // The data above already holds only what the role may see; this decides what the page offers.
  const PAGE = (() => { try { return JSON.parse(document.getElementById("page-meta").textContent); } catch (e) { return null; } })();
  const mayEdit = (action, id) => !PAGE || ((PAGE.edit || {})[action] || []).includes(id);
  const PAGE_ACCOUNTS = new Set(PAGE ? PAGE.accounts : []);
  const $ = (id) => document.getElementById(id);
  const NS = "http://www.w3.org/2000/svg";
  // Phone layout (review 10 Nov 2026): a chart is drawn in a fixed coordinate box and scaled to
  // its panel, so an 880-wide chart on a 300px phone panel shrank its text to 4px. On a phone
  // every chart is drawn in a box about as wide as the panel (text stays at its real size) with
  // a shorter layout; crossing the breakpoint redraws everything.
  const PHONE_MQ = window.matchMedia ? matchMedia("(max-width: 640px)") : { matches: false };
  const isPhone = () => PHONE_MQ.matches;
  const fitW = (desk, phone = 320) => (isPhone() ? phone : desk);
  const el = (tag, attrs = {}, parent) => {
    const n = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    if (parent) parent.appendChild(n);
    return n;
  };
  // a missing date (a CRM cell the engine couldn't read: flags.py data_gaps, 24 Nov 2026) is NaN
  // days, so it falls out of every window and comparison, and reads "date unknown"
  const day = (s) => s ? Date.UTC(+s.slice(0, 4), +s.slice(5, 7) - 1, +s.slice(8, 10)) / 864e5 : NaN;
  const yr4 = (s) => s ? s.slice(0, 4) : "";
  const byDate = (x, y) => (x || "9999").localeCompare(y || "9999");   // unknown dates last
  const TL_DAYS = 90;
  // days to reach an Act account's customer, by tier: one rule for the countdown, the escalation
  // and every "in time" in the analysis (settings.toml [journeys] reach_days, 17 Nov 2026)
  const REACH = (tier) => ((D.settings.reach_days || {})[tier] ?? D.settings.reach_days_other ?? 14);
  const REACH_TEXT = Object.entries(D.settings.reach_days || {}).map(([tr, d], i) => (i ? `${d} for ${tr}` : `${d} days for ${tr}`)).join(", ");
  const tierOf = (id) => (byId[id] || {}).tier;
  // the account's clock to reach the customer: from its signal, by its tier's days; judged on the
  // account (a call or meeting logged), never on its task (a claim isn't contact). 17 Nov 2026.
  function reachOf(id, flag) {
    const r = (D.time_to_action || []).find((x) => x.account_id === id && x.flag === flag);
    if (!r) return null;
    const n = REACH(tierOf(id));
    if (r.call_date) return { reached: r.call_date, n };
    return { left: n - (day(D.as_of) - day(r.signal_date)), n, signal: r.signal_date };
  }
  const liveT = (t) => t.action_status === "Open" || t.action_status === "Claimed";
  const reachBadge = (s) => !s ? "–" : s.reached ? `<span class="badge Done">reached ${fmt(s.reached)}</span>`
    : s.left >= 0 ? `<span class="badge Open">${s.left} d left</span>` : `<span class="badge overdue">overdue ${-s.left} d</span>`;
  const REACH_SHORT = Object.entries(D.settings.reach_days || {}).map(([tr, d]) => `${d} d ${tr}`).join(", ");
  // a with/without comparison shows rates only when every group has [cockpit] min_compare accounts
  // (16 Nov 2026 review: "3 of 5 churned with a call, 0 of 1 without" read as "calls don't work")
  const MIN_COMPARE = (D.settings && D.settings.min_compare) || 10;
  const tooFew = (groups) => groups.some((n) => n < MIN_COMPARE);
  const callGroup = (g, n) => `${n} ${/no churn call/i.test(g) ? "without a churn call" : /churn call/i.test(g) ? "with a churn call" : g.toLowerCase()}`;
  const tooFewNote = (parts) => `Too few to tell: ${parts.join(", ")}. A rate needs at least ${MIN_COMPARE} accounts in each group; with fewer, chance decides it. Click a row to see the accounts.`;                                    // the account chart's window, ending on the data's date
  const fmt = (s) => s ? new Date(day(s) * 864e5).toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" }) : "date unknown";
  // A person's page holds only their team, so it can't work out a book-wide median itself:
  // roles.py computes them once (D.peer_medians, by kind of owner) and the page uses those.
  const PM = D.peer_medians || null;
  const peerMed = (kind, key, fallback) => PM ? ((PM[kind] || {})[key] ?? null) : fallback();
  const PM_TEAM = PM && PM.scope === "team";
  const median = (xs) => { if (!xs.length) return null; const a = [...xs].sort((x, y) => x - y), m = a.length >> 1; return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2; };
  $("n-accounts").textContent = D.accounts.length;   // from the export, never typed in
  const pct = (a, b) => (b ? Math.round((100 * a) / b) : 0);
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  // money: value guides the work, it's never a CSM's credit
  const CUR = D.settings.currency === "EUR" ? "€" : (D.settings.currency || "") + " ";
  const money = (v) => v == null || isNaN(v) ? "–" : Math.abs(v) >= 1e6 ? CUR + (v / 1e6).toFixed(1).replace(/\.0$/, "") + "M"
    : Math.abs(v) >= 1000 ? CUR + Math.round(v / 1000) + "k" : CUR + Math.round(v);
  const valueOf = (f) => (f && typeof f.value === "number" ? f.value : null);
  // set just before the main draw: a view drawn earlier (the saved-decisions loader draws Tasks
  // and the timeline while the page is still being set up) leaves out what needs later helpers
  let READY = false;
  // the whole book's reference figures: precomputed for a person's page (roles.book_ref), else
  // from the full export (the in-house page), with retention.py's own forecast (18 Nov 2026)
  const bookRef = () => D.book_ref || (() => { const f = (D.retention || {}).forecast || {};
    return { arr: D.accounts.reduce((t, a) => t + (a.arr || 0), 0), forecast_days: f.days,
             forecast_grr: f.up_for_renewal ? 1 - (f.owner_loss || 0) / f.up_for_renewal : null }; })();
  // The page's database (published cockpit only). Declared up here because the
  // timeline and My book read it too, not just the Tasks tab.
  let db = null, me = null, dbState = "loading", decisions = {}, saveError = "";
  // Renewal forecasts, account -> oldest first. The export carries the ones
  // already synced; the published page replaces them with its live database.
  let forecasts = {};
  const HANDOVER = D.handovers || {};                  // AI handover briefs (handover.py), by account
  let handoverConfirms = {};                           // confirms from the page's database, by account
  const groupForecasts = (list) => { const m = {}; for (const f of list) (m[f.account_id] = m[f.account_id] || []).push(f);
    for (const k in m) m[k].sort((x, y) => String(x.decided_at).localeCompare(String(y.decided_at))); return m; };
  forecasts = groupForecasts(D.forecasts || []);
  // Next steps ("action planned"), account -> oldest first; the latest is the plan
  let nextSteps = groupForecasts(D.next_steps || []);
  // the four kinds of work (22 Nov 2026): a flag's to-do declined with a reason, and whether a next
  // step achieved its aim; the export carries the synced ones, the published page its live database
  let declines = groupForecasts(D.declines || []), checks = groupForecasts(D.outcome_checks || []);
  // defined early (23 Nov 2026): the account page draws commitments at load, before the rest of the script ran
  const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
  const WORK = (D.settings && D.settings.work) || { decline_reasons: {}, intended_outcomes: {}, outcome_results: {} };
  // a recommendation is declined while its flag is at the same status, until a snooze date passes
  // commitments (22 Nov 2026): the export's (Slack's and the synced cockpit's), plus the live
  // database's; a cockpit commitment is named by the same id cockpit_sync.py gives its event
  let comLive = [], comClose = [];
  const comKey = (d) => `cockpit-commitment-${d.account_id}-${d.decided_at}`;
  function commitmentsOf(id) {
    const base = (D.commitments || []).filter((c) => c.account_id === id && !(dbState === "ready" && c.via === "cockpit")).map((c) => ({ ...c }));
    const live = comLive.filter((d) => d.account_id === id).map((d) => ({ key: comKey(d), account_id: id, made_at: d.decided_at, by: d.by,
      what: d.what, due: d.due, source: d.source, doc: d.doc || null, via: "cockpit" }));
    const all = base.concat(live);
    for (const c of all) { const cl = comClose.filter((x) => x.key === c.key).slice(-1)[0]; if (cl) Object.assign(c, { result: cl.result, closed_at: cl.decided_at, closed_by: cl.by }); }
    return all.sort((x, y) => String(x.due).localeCompare(String(y.due)));
  }
  const comState = (c) => c.result ? { k: c.result, t: (WORK.commitment_results || {})[c.result] || c.result }
    : day(c.due) < TODAY ? { k: "late", t: `Late ${TODAY - day(c.due)} d` }
    : day(c.due) - TODAY <= 7 ? { k: "soon", t: day(c.due) === TODAY ? "Due today" : `Due in ${day(c.due) - TODAY} d` } : { k: "open", t: "Open" };
  const declinedOf = (id, flag, status) => {
    const d = (declines[id] || []).filter((x) => x.flag === flag && x.status === status).slice(-1)[0];
    return d && !(d.reason === "later" && d.due && day(d.due) <= TODAY) ? d : null;
  };
  const STEP_TYPES = { call: "Customer call", qbr: "QBR", email: "Email", internal: "Internal review" };
  // the plan for an account: the owner's next step, else a QBR booked in the CRM
  const planOf = (a) => {
    const s = (nextSteps[a.account_id] || []).slice(-1)[0];
    if (s) return { kind: "step", type: STEP_TYPES[s.type] || s.type, due: s.due, note: s.note, by: s.by, role: s.role, set_on: s.as_of, intended: s.intended, decided_at: s.decided_at };
    if (a.next_qbr_date && a.next_qbr_date > D.as_of) return { kind: "crm", type: "QBR", due: a.next_qbr_date, note: "booked in the CRM" };
    return null;
  };
  const CALLS = { renew: "Renew in full", downsize: "Downsize", churn: "Churn" };
  const callText = (f) => f.call === "downsize" && f.amount != null ? `Downsize to ${money(f.amount)}` : CALLS[f.call] || f.call;
  // what the person expects to lose at renewal, next to what the data says
  const expectedLoss = (a, f) => f.call === "churn" ? a.arr : f.call === "downsize" && f.amount != null ? Math.max(0, a.arr - f.amount) : 0;
  const dataRisk = (a) => valueOf(a.flags.find((x) => x.flag === "churn")) || 0;
  const forecastGap = (a, f) => {
    const mine = expectedLoss(a, f), data = dataRisk(a);
    return Math.abs(mine - data) >= (D.settings.forecast_gap_share ?? 0.25) * a.arr ? { mine, data } : null;   // [money] forecast_gap_share: worth a conversation
  };

  $("asof").textContent = "Data as of " + fmt(D.as_of) + " 2026";
  const byId = Object.fromEntries(D.accounts.map((a) => [a.account_id, a]));
  // The weekly history (history.py): accounts no longer in the book (they left, or never had a
  // CRM row) come under keys of their own (ob_1, ...) with only their tier, region and owner
  const HIST = D.history || { weeks: [], off_book: [], flags: [], tasks: [] };
  const offBook = Object.fromEntries((HIST.off_book || []).map((a) => [a.account_id, a]));
  const ownerName = Object.fromEntries(D.team.map((t) => [t.owner_id, t.owner_name]));
  ownerName.automated = "Automated email";
  ownerName.cs_ops = "CS ops";
  Object.assign(ownerName, D.settings.data_owner_names || {});   // owners outside the team ([data_quality])

