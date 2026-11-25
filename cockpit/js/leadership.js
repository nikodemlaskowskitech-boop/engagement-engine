  // ---------------------------------------------------------------- leadership: the one-pager
  // Churn (what is red, action taken, action planned), growth by value, and the
  // team's time in front of customers. Every list sorted by value at stake.
  let leadStages = [];
  const TRAIL_COLS = [
    { label: "Account", get: (a) => `${esc(a.account_name)}<small><br>${esc(a.owner || "")}</small>` },
    { label: "At risk", r: true, get: (a) => money(valueOf(a.flags.find((f) => f.flag === "churn"))) },
    { label: "Status", get: (a) => esc((a.flags.find((f) => f.flag === "churn") || {}).status || "") },
    { label: "Renewal", get: (a) => `${fmt(a.renewal_date)} ${yr4(a.renewal_date)}` },
    { label: "Action planned", get: (a) => { const p = planOf(a); return p ? planText(a, p) : '<span class="bad">none</span>'; } },
  ];
  // Trends on the Leadership page, from the weekly history: the same segment as the rest
  // of the page (an account that has since left counts in its team, region and tier).
  const histIn = (id) => byId[id] ? inSeg(byId[id]) : (!seg.acc && !!offBook[id] && inSegBase(offBook[id]));
  const weekLabel = (i) => fmt(HIST.weeks[i]);
  function drawLeadTrends(STALE, who) {
    const has = HIST.weeks.length > 1;
    $("ld-queue-trend-block").hidden = $("ld-nocsm-trend-block").hidden = !has;
    if (!has) return;
    const W = HIST.weeks.length, ago = Math.max(0, W - 5);                // four weeks back
    // 1. not picked up: tasks still Open that week, split by how long they had waited
    const open = HIST.tasks.filter((r) => r[5] === "Open" && histIn(r[1]));
    const qByWeek = HIST.weeks.map((_, i) => open.filter((r) => r[0] === i));
    const QCATS = [{ k: "fresh", name: `Waiting under ${STALE} days`, color: "var(--viz-other)" },
                   { k: "stale", name: `Waiting ${STALE}+ days`, color: "var(--warning)" }];
    const TASK_COLS = [
      { label: "Account", get: (t) => nameOf(t.account_id) },
      { label: "Task", get: (t) => `${esc(t.flag)} ${esc(t.status)}` },
      { label: "With", get: (t) => esc(who(t.routed_to)) },
      { label: "Waiting", r: true, get: (t) => `<span class="badge Open${t.age_days >= STALE ? " stale" : ""}">${t.age_days} days</span>` },
    ];
    const asTask = (r) => ({ account_id: r[1], flag: r[2], status: r[3], routed_to: r[4], age_days: r[6] });
    const tw = (id) => Math.min(440, $(id).clientWidth || 440);          // the panel's width (hidden: a default)
    stackChart($("ld-queue-trend"), { W: tw("ld-queue-trend"), H: 140, cats: QCATS, unit: "task", totals: "last",
      aria: "Tasks not picked up, week by week",
      bars: qByWeek.map((rs, i) => ({ label: weekLabel(i), tip: `Week ending ${fmtFull(HIST.weeks[i])}`,
        counts: { fresh: rs.filter((r) => r[6] < STALE).length, stale: rs.filter((r) => r[6] >= STALE).length },
        onClick: () => accountList($("ld-queue-week"), $("ld-queue-week-title"), `Not picked up, week ending ${fmtFull(HIST.weeks[i])}`,
          rs.map(asTask).sort((a, b) => b.age_days - a.age_days), TASK_COLS, false, "task") })) });
    stackLegend($("ld-queue-legend"), QCATS);
    $("ld-queue-week-title").textContent = "Click a week to list what was waiting"; $("ld-queue-week").innerHTML = "";
    const qNow = qByWeek[W - 1].length, qThen = qByWeek[ago].length, qMax = Math.max(...qByWeek.map((r) => r.length));
    const peak = qByWeek.findIndex((r) => r.length === qMax);
    $("ld-queue-finding").textContent += ` Four weeks ago: ${qThen}; the most in a week this year: ${qMax} (${weekLabel(peak)}).`;

    // 2. new Enterprise accounts with no CSM, each week
    const nc = HIST.flags.filter((r) => r[3] === "No CSM assigned" && histIn(r[1]));
    const ncByWeek = HIST.weeks.map((_, i) => nc.filter((r) => r[0] === i));
    const NCATS = [{ k: "n", name: "Enterprise accounts with no CSM", color: "var(--warning)" }];
    const NC_COLS = [
      { label: "Account", get: (a) => nameOf(a.account_id) },
      { label: "Region", get: (a) => esc((byId[a.account_id] || offBook[a.account_id] || {}).region || "") },
      { label: "Signed", r: true, get: (a) => byId[a.account_id] ? `${fmt(byId[a.account_id].contract_start)}<small><br>${a.days} days before</small>` : "–" },
    ];
    stackChart($("ld-nocsm-trend"), { W: tw("ld-nocsm-trend"), H: 140, cats: NCATS, unit: "account", totals: "last",
      aria: "Enterprise accounts with no CSM, week by week",
      bars: ncByWeek.map((rs, i) => ({ label: weekLabel(i), tip: `Week ending ${fmtFull(HIST.weeks[i])}`, counts: { n: rs.length },
        onClick: () => accountList($("ld-nocsm-week"), $("ld-nocsm-week-title"), `No CSM, week ending ${fmtFull(HIST.weeks[i])}`,
          rs.map((r) => ({ account_id: r[1], days: byId[r[1]] ? day(HIST.weeks[i]) - day(byId[r[1]].contract_start) : null })), NC_COLS, false) })) });
    stackLegend($("ld-nocsm-legend"), NCATS);
    $("ld-nocsm-week-title").textContent = "Click a week to list the accounts"; $("ld-nocsm-week").innerHTML = "";
    const everNc = new Set(nc.map((r) => r[1])).size;
    $("ld-nocsm-finding").textContent += everNc ? ` Since ${fmtFull(HIST.weeks[0])}, ${plural(everNc, "new Enterprise account")} waited for one.` : "";
  }
  // Business case (the CFO's page, leader only): GRR and NRR in the industry's words.
  // retention.py computes every account's part; the page sums the segment's accounts.
  const RET = D.retention || null;
  const pct1 = (x) => x == null || isNaN(x) ? "–" : (100 * x).toFixed(1) + "%";
  const pts = (x) => (x >= 0 ? "+" : "−") + Math.abs(100 * x).toFixed(1) + " pts";
  const accOfAny = (id) => byId[id] || offBook[id] || null;
  const inSegAny = (id) => byId[id] ? inSeg(byId[id]) : (!seg.acc && !!offBook[id] && inSegBase(offBook[id]));
  function caseList(title, items, cols, noun = "account") {
    $("cs-list-panel").hidden = false;
    accountList($("cs-list"), $("cs-list-title"), title, items, cols, false, noun);
    $("cs-list-panel").scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
  // a row of horizontal bars: [{label, from, to, color, value, onClick, tip, total}] on a 0..max scale
  function hbars(host, rowsIn, max) {
    host.innerHTML = "";
    const wrap = document.createElement("div"); wrap.className = "hbars"; host.append(wrap);
    for (const r of rowsIn) {
      if (r.sep) { const d = document.createElement("div"); d.className = "hbar-sep"; wrap.append(d); continue; }
      const row = document.createElement("div");
      row.className = "hbar" + (r.total ? " total" : "") + (r.onClick ? " click" : "");
      const left = 100 * Math.min(r.from, r.to) / max, width = Math.max(0.6, 100 * Math.abs(r.to - r.from) / max);
      const bar = r.noBar ? '<span class="track"></span>' : `<span class="track"><span class="fill" style="left:${left}%;width:${width}%;background:${r.color}"></span></span>`;
      row.innerHTML = `<span class="lbl">${r.label}</span>${bar}<span class="val">${r.value}</span>`;
      if (r.onClick) { row.tabIndex = 0; row.setAttribute("role", "button"); row.addEventListener("click", r.onClick); row.addEventListener("keydown", (e) => { if (e.key === "Enter") r.onClick(); }); }
      if (r.tip) hover(row, r.tip);
      wrap.append(row);
    }
  }
  // GRR and NRR by team (22 Nov 2026, the zoom): the same cohort split by the account's manager;
  // a team name zooms the whole page to it. Self-service has no team: its own row.
  const zoomTeam = (m) => { for (const k in seg) seg[k] = ""; seg.manager = m; saveSeg(); redraw(); };
  const zoomPerson = (o) => { seg.owner = o; saveSeg(); redraw(); };      // keeps the team: Whole book goes back up
  // Where the zoom is, and one click back up (23 Nov 2026, Nikodem: "I can't revert it to the top
  // view"): Whole book > a team > a person, on every panel you can drill into. A manager's page
  // starts at their team; a CSM's page has nothing above it, so no crumbs.
  function zoomCrumbs(host) {
    if (!host) return;
    const mgrPage = PAGE && PAGE.role === "manager", own = PAGE && (PAGE.role === "csm" || PAGE.role === "member");
    const m = seg.manager || (teamById[seg.owner] || {}).manager || "";
    const levels = [];
    if (!mgrPage && !own) levels.push(["Whole book", () => { for (const k in seg) seg[k] = ""; }]);
    if (m && !own) levels.push([`${managerName(m)}'s team`, () => { for (const k in seg) seg[k] = ""; seg.manager = m; }]);
    if (seg.owner && !own) levels.push([ownerName[seg.owner] || seg.owner, null]);
    const depth = levels.length - 1, up = levels[depth - 1];
    host.hidden = depth < 1 || (mgrPage && !seg.owner) || (!mgrPage && !m && !seg.owner);
    if (host.hidden) { host.innerHTML = ""; return; }
    host.innerHTML = `<button class="link" data-up>‹ Back to ${esc(up[0].toLowerCase() === "whole book" ? "the whole book" : up[0])}</button>` +
      `<span class="muted"> · ${levels.map(([l], i) => i < depth ? `<button class="link" data-lv="${i}">${esc(l)}</button>` : `<b>${esc(l)}</b>`).join(" › ")}</span>`;
    const go = (i) => { levels[i][1](); saveSeg(); redraw(); };
    host.querySelector("[data-up]").addEventListener("click", () => go(depth - 1));
    host.querySelectorAll("[data-lv]").forEach((b) => b.addEventListener("click", () => go(+b.dataset.lv)));
  }
  function drawTeamsRetention() {
    const sum = (xs, f) => xs.reduce((t, x) => t + (f(x) || 0), 0);
    // zoomed to one team (or a manager's own page): one row per person instead, the drill-down
    const mgr = seg.manager || (teamById[seg.owner] || {}).manager || "", byPerson = !!mgr;
    const teamOf = byPerson
      ? (id) => { const a = accOfAny(id); return a && a.tier !== "Self-service" ? (a.owner_id || "") : "ss"; }
      : (id) => { const a = accOfAny(id); return a && a.tier !== "Self-service" ? (a.manager || "") : "ss"; };
    const inTeam = (id) => { const a = accOfAny(id); return !!a && a.manager === mgr; };
    const all = (RET.cohort || []).filter((r) => !byPerson || inTeam(r[0]));
    const keys = [...new Set(all.map((r) => teamOf(r[0])))].sort((a, b) => (a === "ss") - (b === "ss") || (a ? managerName(a) : "~").localeCompare(b ? managerName(b) : "~"));
    const name = (k) => k === "ss" ? "Self-service (no team)" : byPerson ? (ownerName[k] || k || "No owner") : k ? `${managerName(k)}'s team` : "No manager";
    $("cs-teams-title").textContent = byPerson ? `${managerName(mgr)}'s team, by person` : "By team: is retention the same everywhere?";
    const rows = keys.map((k) => {
      const c = all.filter((r) => teamOf(r[0]) === k), st = sum(c, (r) => r[2]), en = sum(c, (r) => r[3]);
      const lost = sum(c.filter((r) => r[1] === "churned"), (r) => r[2]) + sum(c.filter((r) => r[1] === "contraction"), (r) => r[2] - r[3]);
      const ahead = (RET.renewals_ahead || []).filter((r) => (!byPerson || inTeam(r[0])) && teamOf(r[0]) === k);
      return { k, n: c.length, st, grr: st ? (st - lost) / st : null, nrr: st ? en / st : null, risk: sum(ahead, (r) => r[3]) };
    });
    const few = (r) => r.n < MIN_COMPARE;
    const teams = rows.filter((r) => r.k !== "ss" && !few(r) && r.grr != null).sort((a, b) => a.grr - b.grr);
    // by person: rates on 5 to 10 customers swing with one account, so the finding names money, not a rank
    const people = rows.filter((r) => r.k !== "ss" && r.st);
    const lostMost = people.map((r) => ({ ...r, lost: r.st * (1 - r.grr) })).sort((a, b) => b.lost - a.lost)[0];
    $("cs-teams-finding").textContent = byPerson
      ? (lostMost && lostMost.lost > 0 ? `${name(lostMost.k)}'s book lost the most this year: ${money(lostMost.lost)} of ${money(lostMost.st)} (GRR ${pct1(lostMost.grr)}, ${lostMost.n} customers). A conversation about those accounts, not a ranking.` : "No ARR lost on this team's accounts this year.")
      : teams.length >= 2
      ? `${name(teams[0].k)} kept ${pct1(teams[0].grr)} of its ARR (GRR), ${name(teams[teams.length - 1].k)} ${pct1(teams[teams.length - 1].grr)}: ${Math.abs(100 * (teams[teams.length - 1].grr - teams[0].grr)).toFixed(1)} points apart.`
      : "Too few teams with enough customers to compare.";
    const cur = byPerson ? seg.owner : seg.manager;
    $("cs-teams").innerHTML = `<table><thead><tr><th>${byPerson ? "Person" : "Team"}</th><th class="r">Customers on day one</th><th class="r">ARR on day one</th><th class="r">GRR</th><th class="r">NRR</th><th class="r">Renewals at risk, next two quarters</th></tr></thead><tbody>` +
      rows.map((r) => `<tr${r.k === cur ? ' class="on"' : ""}><td>${r.k && r.k !== "ss" ? `<button class="link" data-zoom="${esc(r.k)}">${esc(name(r.k))}</button>` : esc(name(r.k))}</td><td class="r num">${r.n}</td><td class="r num">${money(r.st)}</td>` +
        (few(r) && !byPerson ? `<td class="r muted" colspan="2">too few to tell</td>`
          : few(r) ? `<td class="r num muted">${pct1(r.grr)}</td><td class="r num muted">${pct1(r.nrr)}</td>` : `<td class="r num">${pct1(r.grr)}</td><td class="r num">${pct1(r.nrr)}</td>`) + `<td class="r num">${money(r.risk)}</td></tr>`).join("") + "</tbody></table>";
    zoomCrumbs($("cs-teams-crumbs"));
    $("cs-teams").querySelectorAll("[data-zoom]").forEach((b) => b.addEventListener("click", () => byPerson ? zoomPerson(b.dataset.zoom) : zoomTeam(b.dataset.zoom)));
    $("cs-teams-note").textContent = byPerson
      ? `The team's whole cohort, whatever the other filters. A person is the account's owner today: an account handed over counts with its new owner, so read it with the handovers in mind. Under ${MIN_COMPARE} customers the rates are greyed: one account moves them by 10 points or more. Click a person to zoom to their book. On a CSM's own page these stay team totals.`
      : `The whole book's cohort, whatever the filters, so teams compare. A team is its accounts' manager today: an account that moved teams counts where it is now. Fewer than ${MIN_COMPARE} customers: too few to tell. Click a team to zoom the whole page to it, and again on a person.`;
  }
  function drawCase() {
    if ($("ld-perf")) $("ld-perf").hidden = $("cs-method-panel").hidden = !RET;   // leader only: managers' pages carry no retention data
    if (!$("ld-perf") || !RET) return;
    const y = RET.year || {}, prior = RET.prior, fc = RET.forecast || {}, segd = segOn();
    // this year, for the segment: the cohort's accounts that are in it
    const cohort = (RET.cohort || []).filter((r) => inSegAny(r[0]));
    const sum = (xs, f) => xs.reduce((t, x) => t + (f(x) || 0), 0);
    const start = sum(cohort, (r) => r[2]), end = sum(cohort, (r) => r[3]);
    const byMove = (m) => cohort.filter((r) => r[1] === m);
    const churned = sum(byMove("churned"), (r) => r[2]), down = sum(byMove("contraction"), (r) => r[2] - r[3]), up = sum(byMove("expansion"), (r) => r[3] - r[2]);
    const grr = start ? (start - churned - down) / start : null, nrr = start ? end / start : null;
    const book = sum(D.accounts.filter(inSeg), (a) => a.arr);
    const point = book / 100;
    const ahead = (RET.renewals_ahead || []).filter((r) => inSegId(r[0]));
    const fData = sum(ahead, (r) => r[3]), fOwner = sum(ahead, (r) => r[4]), fUp = sum(ahead, (r) => r[5]);
    const withOutcome = ahead.filter((r) => r[6] != null), fActual = sum(withOutcome, (r) => r[6]);
    const ownerCalls = fc.owner_calls || 0;
    // coverage: the ARR that people own (CSMs and pool members; Self-service has no owner)
    const owned = D.accounts.filter(inSeg).filter((a) => ["csm", "member"].includes((teamById[a.owner_id] || {}).owner_type));
    const people = [...new Set(owned.map((a) => a.owner_id))], covered = sum(owned, (a) => a.arr);
    const COST = D.settings.cs_cost_per_head || 0;
    const who = segd ? segName() + ": " : "";
    $("cs-finding").textContent = start
      ? `${who}GRR ${pct1(grr)} this year${!segd && prior ? `, up from ${pct1(prior.grr)} the year before` : ""}; NRR ${pct1(nrr)}${!segd && prior ? ` (${pct1(prior.nrr)})` : ""}. Renewals at risk in the next two quarters: ${money(fData)}, ${(fData / Math.max(1, point)).toFixed(1)} points of GRR.`
      : `${segName()}: no customers in this segment on the first day of the year.`;
    $("cs-sub").textContent = `${cohort.length} customers on ${fmtFull(y.period_start || D.as_of)}, ${money(start)} ARR between them; new logos count in neither number. All of it licence revenue (committed volume and add-ons): no services in this book.`;
    $("cs-tiles").innerHTML =
      tile("Gross revenue retention", pct1(grr), !segd && prior ? `year before ${pct1(prior.grr)} (${pts(grr - prior.grr)})` : "this year, standard cohort", false) +
      tile("Net revenue retention", pct1(nrr), !segd && prior ? `year before ${pct1(prior.nrr)} (${pts(nrr - prior.nrr)})` : "this year, standard cohort", false) +
      tile("One point of GRR", money(point), `1% of today's ${money(book)} ARR`, false) +
      tile("Renewals at risk, next two quarters", money(fData), `${(fData / Math.max(1, point)).toFixed(1)} pts of GRR · ${plural(ahead.length, "renewal")}`, fData > 0) +
      tile("ARR per CS person", people.length ? money(covered / people.length) : "–", COST
        ? `one GRR point = ${(point / COST).toFixed(1)} people at ${money(COST)} a year`
        : `${money(covered)} with a named CSM or pool member, ${people.length} ${people.length === 1 ? "person" : "people"}`, false);
    drawTeamsRetention();
    $("cs-recon").textContent = `Book ${money(book)} = ${money(covered)} with a named CSM or pool member + ${money(book - covered)} Self-service and not yet owned.`;

    // 1. where the year's ARR went: a horizontal waterfall, this year (and the year before, whole book only)
    const MOVER_COLS = [
      { label: "Account", get: (r) => nameOf(r.account_id) },
      { label: "Tier", get: (r) => esc((accOfAny(r.account_id) || {}).tier || "") },
      { label: "ARR on day one", r: true, get: (r) => money(r.s0) },
      { label: "Now", r: true, get: (r) => money(r.s1) },
    ];
    const listMove = (m, title) => () => caseList(title, byMove(m).map((r) => ({ account_id: r[0], s0: r[2], s1: r[3] })).sort((a, b) => Math.abs(b.s1 - b.s0) - Math.abs(a.s1 - a.s0)), MOVER_COLS);
    const max = Math.max(1, nrr || 1, prior && !segd ? prior.nrr : 0) * 1.04;
    const LOST = "var(--critical)", GROW = "var(--good)", BASE = "var(--viz-other)", KEPT = "var(--ink-2)";
    const share = (v) => start ? v / start : 0;
    const rows = [
      { label: "Day one", from: 0, to: 1, color: BASE, value: `${money(start)} <small>100%</small>`, tip: `<b>The cohort's ARR on ${fmtFull(y.period_start || D.as_of)}</b><div class="muted">${plural(cohort.length, "customer")}</div>` },
      { label: `Churn (${byMove("churned").length})`, from: 1 - share(churned), to: 1, color: LOST, value: `−${money(churned)} <small>${(100 * share(churned)).toFixed(1)}%</small>`, onClick: listMove("churned", "Churned this year") },
      { label: `Downsell (${byMove("contraction").length})`, from: 1 - share(churned + down), to: 1 - share(churned), color: LOST, value: `−${money(down)} <small>${(100 * share(down)).toFixed(1)}%</small>`, onClick: listMove("contraction", "Renewed smaller this year") },
      { label: "GRR", from: 0, to: grr || 0, color: KEPT, value: `<b>${pct1(grr)}</b>`, total: true },
      { label: `Expansion (${byMove("expansion").length})`, from: grr || 0, to: (grr || 0) + share(up), color: GROW, value: `+${money(up)} <small>${(100 * share(up)).toFixed(1)}%</small>`, onClick: listMove("expansion", "Grew this year") },
      { label: "NRR", from: 0, to: nrr || 0, color: KEPT, value: `<b>${pct1(nrr)}</b>`, total: true },
    ];
    if (prior && !segd) {
      rows.push({ sep: true },
        { label: "Year before: GRR", from: 0, to: prior.grr, color: BASE, value: pct1(prior.grr), total: true, tip: `<b>The year before</b><div>Finance's quarterly ARR bridge: churn ${money(prior.churned_arr)}, downsell ${money(prior.contraction_arr)}, expansion ${money(prior.expansion_arr)} on ${money(prior.start_arr)}</div><div class="muted">Top line only: no accounts to list</div>` },
        { label: "Year before: NRR", from: 0, to: prior.nrr, color: BASE, value: pct1(prior.nrr), total: true });
    }
    hbars($("cs-bridge"), rows, max);
    $("cs-bridge-finding").textContent = start
      ? `Churn took ${(100 * share(churned)).toFixed(1)}% and downsell ${(100 * share(down)).toFixed(1)}%; expansion added ${(100 * share(up)).toFixed(1)}%.`
      : "No customers here on day one.";
    $("cs-bridge-note").textContent = segd
      ? "The year before is known only as finance's top line for the whole book, so it isn't split by segment."
      : `The year before comes from finance's quarterly ARR bridge, top line only. Worked out the same way, this year reads GRR ${pct1((RET.this_bridge || {}).grr)} and NRR ${pct1((RET.this_bridge || {}).nrr)}, so the comparison is fair. Click a bar to list its accounts.`;

    // 2. the next two quarters: the data's rule, the owners' forecasts and (demo) what happened
    const FC_COLS = [
      { label: "Account", get: (r) => `${nameOf(r.account_id)}<small><br>renews ${fmt(r.d)}</small>` },
      { label: "ARR", r: true, get: (r) => money(r.arr) },
      { label: "Data's rule", r: true, get: (r) => r.dl ? money(r.dl) : "–" },
      { label: "Owner", r: true, get: (r) => r.ol !== r.dl ? money(r.ol) : "–" },
      { label: "What happened", r: true, get: (r) => r.al == null ? "–" : r.al ? `−${money(r.al)}` : r.ag ? `+${money(r.ag)}` : "kept" },
    ];
    const asRow = (r) => ({ account_id: r[0], arr: r[1], d: r[2], dl: r[3], ol: r[4], up: r[5], al: r[6], ag: r[7] });
    const fmax = Math.max(1, fData, fOwner, fActual) * 1.08;
    hbars($("cs-fc"), [
      { label: "Up for renewal", noBar: true, from: 0, to: 0, color: BASE, value: `${money(sum(ahead, (r) => r[1]))} <small>${plural(ahead.length, "renewal")}</small>` },
      { label: "Data's rule", from: 0, to: fData, color: "var(--viz-renewal)", value: `${money(fData)} <small>${(fData / Math.max(1, point)).toFixed(1)} pts</small>`,
        onClick: () => caseList("Renewals at risk by the data's rule", ahead.filter((r) => r[3] > 0).map(asRow).sort((a, b) => b.dl - a.dl), FC_COLS) },
      { label: `Owners (${ownerCalls})`, from: 0, to: fOwner, color: "var(--accent)", value: `${money(fOwner)} <small>${(fOwner / Math.max(1, point)).toFixed(1)} pts</small>`,
        onClick: () => caseList("At risk with the owners' forecasts", ahead.filter((r) => r[4] > 0).map(asRow).sort((a, b) => b.ol - a.ol), FC_COLS) },
      { sep: true },
      { label: "What happened", from: 0, to: fActual, color: "var(--ink-2)", value: `${money(fActual)} <small>${(fActual / Math.max(1, point)).toFixed(1)} pts</small>`,
        onClick: () => caseList("What happened at these renewals", withOutcome.filter((r) => r[6] > 0 || r[7] > 0).map(asRow).sort((a, b) => (b.al || 0) - (a.al || 0)), FC_COLS) },
    ], fmax);
    $("cs-fc-finding").textContent = ahead.length
      ? `${money(fData)} of renewals at risk by the data's rule; in the demo's known outcomes for these renewals, ${money(fActual)} was really lost.`
      : "No renewals in the next two quarters in this segment.";
    $("cs-fc-note").textContent = `The data's rule counts the whole ARR at Act and the unused part at Watch and Shelfware. Owners' forecasts replace it where they set one (${ownerCalls} so far). "What happened" is the demo's own future (renewal outcomes generated with the data): it shows how predictable the number is, the point of a forecast. Upside at these renewals: ${money(fUp)}.`;

    // 3. what calling in time is worth: like for like, then on today's book
    const calls = (RET.call_accounts || []).filter((r) => inSegAny(r[0]));
    const grp = (g) => calls.filter((r) => r[1] === g);
    const kept = (xs) => { const b = sum(xs, (r) => r[3]); return b ? sum(xs, (r) => r[4]) / b : null; };
    const inT = grp("in_time"), late = grp("late_or_never"), kIn = kept(inT), kLate = kept(late);
    const waiting = (RET.call_waiting || []).filter((id) => inSegId(id)), waitArr = sum(waiting, (id) => (byId[id] || {}).arr);
    const value = kIn != null && kLate != null ? waitArr * Math.max(0, kIn - kLate) : null;
    const CALL_COLS = [
      { label: "Account", get: (r) => nameOf(r.account_id) },
      { label: "At renewal", get: (r) => esc(r.o) },
      { label: "ARR before", r: true, get: (r) => money(r.b) },
      { label: "After", r: true, get: (r) => money(r.a) },
    ];
    const listCalls = (xs, t) => () => caseList(t, xs.map((r) => ({ account_id: r[0], o: r[2], b: r[3], a: r[4] })).sort((p, q) => q.b - p.b), CALL_COLS);
    hbars($("cs-call"), [
      { label: `Reached in time (${inT.length})`, from: 0, to: kIn || 0, color: "var(--viz-call)", value: `${pct1(kIn)} <small>kept · ${grp("in_time").filter((r) => r[2] === "churned").length} churned</small>`, onClick: listCalls(inT, `Reached in time (${REACH_TEXT}), at their next renewal`) },
      { label: `Later or never (${late.length})`, from: 0, to: kLate || 0, color: BASE, value: `${pct1(kLate)} <small>kept · ${late.filter((r) => r[2] === "churned").length} churned</small>`, onClick: listCalls(late, "Called later or never, at their next renewal") },
    ], 1.04);
    const fewV = tooFew([inT.length, late.length]);
    const gPts = (v) => { const x = v / Math.max(1, point); return x > 0 && x < 0.1 ? "under 0.1 points" : `${x.toFixed(1)} points`; };
    $("cs-call-finding").textContent = value != null && fewV
      ? `Too few renewals after a churn task to compare: ${inT.length} reached in time (${REACH_TEXT}), ${late.length} later or never (at least ${MIN_COMPARE} each).`
      : value != null
      ? `Accounts reached in time (${REACH_TEXT}) kept ${pct1(kIn)} of their ARR at renewal, against ${pct1(kLate)}. Today ${plural(waiting.length, "account")} at Act (${money(waitArr)}) ${waiting.length === 1 ? "hasn't" : "haven't"} had a call: at those rates, calling them in time keeps about ${money(value)} more, ${gPts(value)} of GRR.`
      : "Not enough renewals after a churn task in this segment to compare.";
    if (value != null && fewV) $("cs-call").innerHTML = "";        // no bars for groups too small to read
    $("cs-call-note").textContent = `Like for like: every account that got a churn task this year and has renewed since, ${inT.length} against ${late.length}. Small groups and a planted effect in the demo data: this shows the method a CFO can check, never "we saved X".${D.settings.cs_cost_per_head ? "" : " The cost side shows once a cost per CS person is set (settings.toml [money] cs_cost_per_head)."}`;
    const wl = $("cs-call-waiting");
    wl.hidden = !waiting.length; wl.textContent = `List the ${plural(waiting.length, "account")} not called yet`;
    wl.onclick = () => caseList("At Act, no call yet", waiting.map((id) => byId[id]).filter(Boolean).sort((a, b) => b.arr - a.arr),
      [{ label: "Account", get: (a) => esc(a.account_name) }, { label: "Owner", get: (a) => esc(a.owner || "") }, { label: "ARR", r: true, get: (a) => money(a.arr) }]);

    $("cs-method").innerHTML = [
      "<b>GRR</b>: the ARR the customers of day one pay a year later, each capped at what they paid on day one, over what they paid on day one. Churn and downsell come off; expansion doesn't count. Never above 100%.",
      "<b>NRR</b>: the same with expansion counted. New logos are in neither: they weren't customers on day one.",
      "<b>This year</b> is worked out account by account from the CRM and its closed renewals (the ARR before and after each one). <b>The year before</b> is finance's quarterly ARR bridge, top line only.",
      "<b>One point of GRR</b> is 1% of today's ARR in this segment: the yardstick for any investment in retention.",
      "<b>ARR per CS person</b> (coverage): the ARR the CSMs and pool members own, over how many of them own it. It varies a lot by company, segment and level, so set your own cost per person (settings.toml [money] cs_cost_per_head) to read a GRR point in headcount.",
      "<b>All licence revenue</b>: committed volume and paid add-ons. Services would be reported apart, as licence is what retention protects.",
    ].map((t) => `<li>${t}</li>`).join("");
  }
  // Exposure, step 2 of the leadership assessment (21 Nov 2026): the risk's concentration, and the
  // accounts whose churn status moved over the last 13 weeks, worse and better side by side (good
  // news gets the same weight). From the weekly replay (history.py); an account not yet live 13
  // weeks ago is new, not "worse", and is left out.
  const MOVE_WEEKS = 13;
  function drawExposure(churn, atRisk, pool) {
    if (!$("ld-exposure")) return;
    const showList = (title, items) => {
      $("ld-trail-panel").hidden = false;
      accountList($("ld-trail-list"), $("ld-trail-title"), title, items, TRAIL_COLS, false);
      $("ld-trail-panel").scrollIntoView({ block: "nearest" });
    };
    // concentration
    const top = churn.slice(0, 10), topV = top.reduce((t, a) => t + (valueOf(a.flags.find((f) => f.flag === "churn")) || 0), 0);
    const b = $("ld-conc-list");
    if (!churn.length) { $("ld-conc-finding").textContent = "No Enterprise or Scale account at churn risk."; b.hidden = true; }
    else {
      $("ld-conc-finding").textContent = churn.length <= 10
        ? `All ${churn.length} accounts at risk carry ${money(atRisk)}; the biggest, ${churn[0].account_name}, ${Math.round(100 * (valueOf(churn[0].flags.find((f) => f.flag === "churn")) || 0) / atRisk)}% of it.`
        : `The 10 biggest of ${churn.length} accounts at risk carry ${money(topV)} of ${money(atRisk)} (${Math.round(100 * topV / atRisk)}%).`;
      b.hidden = false; b.textContent = `List the ${Math.min(10, churn.length)}`;
      b.onclick = () => showList(`The ${Math.min(10, churn.length)} biggest risks`, top);
    }
    // movements: Act 2, Watch or Shelfware 1, no churn flag 0
    const W = HIST.weeks || [], last = W.length - 1, base = last - MOVE_WEEKS;
    const host = $("ld-move");
    if (base < 0) { host.innerHTML = '<p class="note">Fewer than 13 weeks of history yet.</p>'; $("ld-move-note").textContent = ""; return; }
    const rank = {}, RANKS = { Act: 2, Watch: 1, Shelfware: 1 };
    for (const [w, id, f, st] of HIST.flags) if (f === "churn" && (w === base || w === last)) (rank[id] = rank[id] || {})[w] = RANKS[st] || 0;
    const baseDay = W[base], ui = D.util_dates.findIndex((d) => d >= baseDay);
    const live = (a) => ui >= 0 && a.util[ui] != null;
    const r = (a, w) => ((rank[a.account_id] || {})[w]) || 0;
    const moved = pool.filter(live).map((a) => ({ a, d: r(a, last) - r(a, base) })).filter((x) => x.d);
    const worse = moved.filter((x) => x.d > 0).map((x) => x.a).sort((x, y) => y.arr - x.arr);
    const better = moved.filter((x) => x.d < 0).map((x) => x.a).sort((x, y) => y.arr - x.arr);
    const WORD = ["no flag", "Watch", "Act"];
    const statusAt = (a, w) => { const v = r(a, w); if (v !== 1) return WORD[v];
      const row = HIST.flags.find((x) => x[0] === w && x[1] === a.account_id && x[2] === "churn"); return row ? row[3] : "Watch"; };
    const col = (title, xs, key) => `<div><h3 class="mini">${title}: ${xs.length} · ${money(xs.reduce((t, a) => t + a.arr, 0))} ARR</h3><ul class="sum-list">` +
      (xs.length ? xs.slice(0, 3).map((a) => `<li><span class="when">${money(a.arr)}</span><span><b>${esc(a.account_name)}</b><br><small>${esc(statusAt(a, base))} → ${esc(statusAt(a, last))} · ${esc(a.tier)}</small></span></li>`).join("") +
        (xs.length > 3 ? `<li><span></span><button class="link" data-move="${key}">View all (${xs.length})</button></li>` : "")
        : '<li><span></span><span class="muted">None.</span></li>') + `</ul></div>`;
    $("ld-move-title").textContent = `What moved since ${fmt(baseDay)}`;
    host.innerHTML = col("Got worse", worse, "worse") + col("Got better", better, "better");
    host.querySelectorAll("[data-move]").forEach((x) => x.addEventListener("click", () =>
      showList(x.dataset.move === "worse" ? `Got worse since ${fmt(baseDay)}` : `Got better since ${fmt(baseDay)}`, x.dataset.move === "worse" ? worse : better)));
    $("ld-move-note").textContent = `Churn status ${MOVE_WEEKS} weeks ago against today, from the weekly replay of the flags: Act above Watch and Shelfware above no flag. Enterprise and Scale only; accounts not yet live ${MOVE_WEEKS} weeks ago are new, not "worse", and are left out. Better is not saved: the renewal decides.`;
  }
  // The leader's one line on the managers' queues (step 3): only what is past its line (Act
  // accounts past their reach deadline, tasks waiting STALE+ days, Enterprise accounts with no
  // CSM), and whose team holds most of it. Pages with ops_lists keep the full panels instead.
  // the glossary: one name, one rule, one population each; a new number gets a row here first
  function drawGlossary() {
    const host = $("ld-glossary-list"); if (!host) return;
    const G = [
      ["ARR at risk", "Today's churn flags, by value: an account at Act counts its whole ARR, Watch and Shelfware the unused part. Enterprise and Scale unless \"whole book\" is said."],
      ["Renewals at risk, next two quarters", "Renewals due in the next 180 days, valued by the data's rule (or the owner's forecast where one is set). A different measure from ARR at risk: it depends on the renewal date."],
      ["At Act", "An account whose churn or expansion flag is at Act, Enterprise and Scale. A churn-only view says \"churn Act\"."],
      ["Reached / late", `Reached: a meeting with the customer since the signal (or up to ${D.settings.early_days ?? 14} days before it). Late: past the tier's days without one (${REACH_TEXT}). The same clock applies to a task.`],
      ["Enterprise and Scale ARR / whole book / starting ARR", "Accounts with a named CSM or pool / every live account, Self-service included / the customers on the first day of the year, the base of GRR and NRR."],
      ["Under the 40% usage line", "Live accounts whose 30-day utilisation is under 40% of their contract, whole book. Not the same as at risk: a flag needs a falling trend or a drop in users too."],
    ];
    host.innerHTML = G.map(([t, d]) => `<dt>${esc(t)}</dt><dd>${esc(d)}</dd>`).join("");
  }
  function drawOpsLine(late, stale, noCsm) {
    const full = !PAGE || PAGE.ops_lists !== false;
    $("ld-ops-reach").hidden = $("ld-ops-grid").hidden = !full;
    const line = $("ld-ops-line"); line.hidden = full || !(late.length || stale.length || noCsm.length);
    if (line.hidden) return;
    const mgrOf = (id) => (teamById[id] || {}).manager;
    const tally = {};
    for (const r of late) { const m = mgrOf(r.routed_to); if (m) tally[m] = (tally[m] || 0) + 1; }
    for (const t of stale) { const m = mgrOf(t.routed_to); if (m) tally[m] = (tally[m] || 0) + 1; }
    const top = Object.entries(tally).sort((a, b) => b[1] - a[1])[0];
    const total = late.length + stale.length;
    const bits = [];
    if (late.length) bits.push(`${plural(late.length, "account")} at Act past ${late.length === 1 ? "its" : "their"} reach deadline`);
    if (stale.length) bits.push(`${plural(stale.length, "task")} late (open past ${stale.length === 1 ? "its" : "their"} tier's days: ${REACH_TEXT})`);
    if (noCsm.length) bits.push(`${plural(noCsm.length, "Enterprise account")} with no CSM (${money(noCsm.reduce((t, a) => t + a.arr, 0))} ARR)`);
    if ($("ld-ops-go")) { $("ld-ops-go").hidden = !$("tab-tasks"); $("ld-ops-go").onclick = () => $("tab-tasks").click(); }
    $("ld-ops-finding").textContent = bits.join("; ") + "." +
      (top && total ? ` ${top[1]} of the ${total} late items sit with ${(ownerName[top[0]] || top[0]).replace(/ \(manager\)$/, "")}'s team.` : "");
  }
  // Signals (22 Nov 2026): every line is a weekly share with its denominator, from the replay of
  // the flags (history.py) and the usage series; "moved" = move_points or more over move_weeks.
  const SG = (D.settings && D.settings.signals) || {};
  function trendSvg(series, labels) {
    // one or two weekly lines of shares (%), the same scale; the end of each line labelled
    const W = fitW(420, 300), H = 120, L = 30, R = 70, T = 8, B = 20, n = series[0].length;
    const max = Math.max(10, ...series.flat().filter((v) => v != null)) * 1.15;
    const X = (i) => L + (i / Math.max(1, n - 1)) * (W - L - R), Y = (v) => T + (1 - v / max) * (H - T - B);
    const cols = ["var(--ink)", "var(--ink-3)"];
    let g = `<line x1="${L}" x2="${W - R}" y1="${Y(0)}" y2="${Y(0)}" stroke="var(--line)"/>` +
      `<text x="${L - 4}" y="${Y(0) + 4}" text-anchor="end" class="lbl">0%</text><text x="${L - 4}" y="${Y(max / 1.15) + 4}" text-anchor="end" class="lbl">${Math.round(max / 1.15)}%</text>` +
      `<text x="${L}" y="${H - 4}" class="lbl">${fmt(HIST.weeks[0])}</text><text x="${W - R}" y="${H - 4}" text-anchor="end" class="lbl">${fmt(HIST.weeks[n - 1])}</text>`;
    series.forEach((sr, k) => {
      const pts = sr.map((v, i) => (v == null ? null : `${X(i).toFixed(1)},${Y(v).toFixed(1)}`)).filter(Boolean).join(" ");
      const lastI = sr.map((v, i) => (v == null ? -1 : i)).filter((i) => i >= 0).pop();
      g += `<polyline points="${pts}" fill="none" stroke="${cols[k]}" stroke-width="2" stroke-linejoin="round"/>`;
      if (lastI != null && lastI >= 0) g += `<text x="${X(lastI) + 6}" y="${Y(sr[lastI]) + 4 + (k ? 10 : 0)}" class="lbl" fill="${cols[k]}">${series.length > 1 ? esc(labels[k]) + " " : ""}${Math.round(sr[lastI])}%</text>`;   // one line: the number is enough
    });
    return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(labels.join(" and "))}, weekly">${g}</svg>`;
  }
  const moved = (now, then) => (now == null || then == null ? "" : Math.abs(now - then) < (SG.move_points ?? 5) ? "steady" : now > then ? "up" : "down");
  function drawSignals() {
    if (!$("ld-signals") || !HIST.weeks || !HIST.weeks.length) return;
    const W = HIST.weeks, last = W.length - 1, base = Math.max(0, last - (SG.move_weeks ?? 13)), pool = D.accounts.filter(inSeg);
    const who = segOn() ? segName() + ": " : "";
    // 1. shelfware by signing cohort: new accounts against the book signed before them
    const [lo, hi] = SG.new_signed_days || [60, 200];
    const shelf = {};
    for (const [w, id, f, st] of HIST.flags) if (f === "churn" && st === "Shelfware") (shelf[w] = shelf[w] || new Set()).add(id);
    const at = (w) => { const d = day(W[w]), S = shelf[w] || new Set();
      const nw = pool.filter((a) => a.customer_since && d - day(a.customer_since) >= lo && d - day(a.customer_since) <= hi);
      const old = pool.filter((a) => a.customer_since && d - day(a.customer_since) > hi);
      return { n: nw.length, h: nw.filter((a) => S.has(a.account_id)).length, on: old.length, oh: old.filter((a) => S.has(a.account_id)).length }; };
    const cs = W.map((_, w) => at(w)), share = (h, n) => (n ? (100 * h) / n : null);
    const nS = cs.map((c) => share(c.h, c.n)), oS = cs.map((c) => share(c.oh, c.on)), C = cs[last];
    const mv = moved(nS[last], nS[base]);
    $("sg-cohort").textContent = C.n
      ? `${who}${C.h} of ${C.n} accounts signed ${lo}-${hi} days ago (${pct(C.h, C.n)}%) are Shelfware, against ${C.oh} of ${C.on} (${pct(C.oh, C.on)}%) of the book signed earlier. ` +
        (mv === "steady" ? `Steady since ${fmt(W[base])}.` : `${mv === "up" ? "Up" : "Down"} from ${Math.round(nS[base] ?? 0)}% on ${fmt(W[base])}.`)
      : `${who}no account signed ${lo}-${hi} days ago.`;
    $("sg-cohort-chart").innerHTML = trendSvg([nS, oS], ["new", "earlier"]);
    $("sg-cohort-note").textContent = `Possible explanations to check, not conclusions: who Sales signed, how onboarding went, or what was promised in the deal. ${(SG.planted || {}).cohort_shelfware || ""}`;
    // 2. under the 40% usage line, by week, the whole book (or the segment)
    const idx = W.map((we) => D.util_dates.findIndex((d) => d >= we));
    const us = idx.map((i) => { if (i < 0) return null; const live = pool.filter((a) => a.util[i] != null); return live.length ? { n: live.length, h: live.filter((a) => a.util[i] < 40).length } : null; });
    const uS = us.map((u) => (u ? share(u.h, u.n) : null)), U = us[last] || { n: 0, h: 0 };
    const mu = moved(uS[last], uS[base]);
    $("sg-usage").textContent = `${who}${U.h} of ${U.n} live accounts${segOn() ? "" : " in the whole book"} (${pct(U.h, U.n)}%) are under the line. ` +
      (mu === "steady" ? `Steady since ${fmt(W[base])}.` : `${mu === "up" ? "Up" : "Down"} from ${Math.round(uS[base] ?? 0)}% on ${fmt(W[base])}.`);
    $("sg-usage-chart").innerHTML = trendSvg([uS], ["under 40%"]);
    $("sg-usage-note").textContent = "30-day utilisation under 40% of the contract. Not the same as at risk: a flag needs a falling trend or fewer users too.";
    // 3. what customers raise: themes from the documents across the portfolio, with ARR and
    // "raised without action" (no CS meeting or workshop since the document)
    const R = D.research || {}, since = TODAY - (SG.theme_days ?? 90), byTheme = {};
    for (const a of pool) for (const t of ((R[a.account_id] || {}).themes || [])) {
      if (day(t.date) < since) continue;
      const met = (D.touches || []).some((x) => x.account_id === a.account_id && CS_TOUCH.has(x.event) && x.date >= t.date);
      const b = byTheme[t.label] = byTheme[t.label] || { accs: [], arr: 0, idle: [] };
      b.accs.push(a); b.arr += a.arr || 0; if (!met) b.idle.push(a);
    }
    const th = Object.entries(byTheme).sort((x, y) => y[1].arr - x[1].arr);
    const idleAll = new Set(th.flatMap(([, b]) => b.idle.map((a) => a.account_id)));
    $("sg-themes-finding").textContent = th.length
      ? `${who}${plural(new Set(th.flatMap(([, b]) => b.accs.map((a) => a.account_id))).size, "account")} raised something in the last ${SG.theme_days ?? 90} days; ${idleAll.size} with no CS meeting since.`
      : `${who}nothing raised in the documents in the last ${SG.theme_days ?? 90} days.`;
    $("sg-themes").innerHTML = th.slice(0, 5).map(([label, b]) => `<li><span class="when">${money(b.arr)}</span><span><b>${esc(label)}</b>: ${plural(b.accs.length, "account")}` +
      `${b.idle.length ? `, <span class="bad">${b.idle.length} raised without action</span>` : ""}<br><small>${b.accs.slice(0, 3).map((a) => `<button class="link" data-open-acc="${esc(a.account_id)}">${esc(a.account_name)}</button>`).join(", ")}${b.accs.length > 3 ? " and more" : ""}</small></span></li>`).join("");
    $("sg-themes-note").textContent = "Themes are keyword matches in tickets, emails and call notes, with their evidence on each account page: possible, not verdicts. In the demo, documents cover a few dozen accounts.";
    // 4. where the signals meet: a risk theme + usage under the line or a churn flag + a renewal soon
    const grow = new Set((D.settings && D.settings.research_growth_themes) || []);
    const cross = pool.filter((a) => {
      const risk = ((R[a.account_id] || {}).themes || []).some((t) => !grow.has(t.theme) && day(t.date) >= since);
      const u = (a.util || []).filter((v) => v != null).slice(-1)[0];
      const usage = (u != null && u < 40) || a.flags.some((f) => f.flag === "churn");
      return risk && usage && day(a.renewal_date) - TODAY >= 0 && day(a.renewal_date) - TODAY <= (SG.renewal_days ?? 120);
    }).sort((x, y) => (y.arr || 0) - (x.arr || 0));
    $("sg-cross-finding").textContent = cross.length
      ? `${who}${plural(cross.length, "account")} (${money(cross.reduce((t, a) => t + (a.arr || 0), 0))} ARR) raised a concern, show weak usage and renew within ${SG.renewal_days ?? 120} days.`
      : `${who}no account where a raised concern, weak usage and a near renewal meet.`;
    $("sg-cross").innerHTML = cross.slice(0, 5).map((a) => { const t = ((R[a.account_id] || {}).themes || []).find((x) => !grow.has(x.theme));
      return `<li><span class="when">${money(a.arr)}</span><span><button class="link" data-open-acc="${esc(a.account_id)}"><b>${esc(a.account_name)}</b></button><br><small>${esc(t ? t.label : "")} · renews ${fmt(a.renewal_date)} · ${esc(a.owner || "")}</small></span></li>`; }).join("");
    $("sg-cross-note").textContent = "Any one of these is common; together they ask for a conversation before the renewal does. A rule over facts on the page, not a prediction.";
    $("ld-signals").parentElement.querySelectorAll("[data-open-acc]").forEach((b) => b.addEventListener("click", () => openAccount(b.dataset.openAcc)));
  }
  // Growth for the leader (25 Nov 2026, whitespace step 3, Nikodem: "a top-level overview, key
  // accounts, where the opportunity is, are we doing anything to address it, the historical trend").
  // In play = an expansion signal or a growth hook not held back. Addressed = the account page's rule
  // (addressedIn): a meeting held or a next step planned since the signal began, or not a fit.
  // An unused add-on is a risk, not upside: its own row, never added to the euros in play.
  const GKINDS = [
    { k: "volume", name: "Volume above the contract", of: (a) => a.flags.find((x) => x.flag === "expansion"), worth: "upside" },
    { k: "addon", name: "Add-on hook", of: (a) => a.flags.find((x) => x.flag === "growth" && x.status === "Add-on hook"), worth: "list price" },
    { k: "people", name: "People hook", of: (a) => a.flags.find((x) => x.flag === "growth" && x.status === "People hook"), worth: "no price" },
    { k: "unused", name: "Add-on bought, not used", of: (a) => a.flags.find((x) => x.flag === "growth" && x.status === "Unused add-on"), worth: "at risk at renewal" },
  ];
  const isAddressed = (a, f) => f.status === "Not a fit" || !!addressedIn(a.account_id, flagSince(a.account_id, f.flag), "9999");
  const upsideOf = (a) => (valueOf(a.flags.find((x) => x.flag === "expansion")) || 0) +
    (valueOf(a.flags.find((x) => x.flag === "growth" && x.status === "Add-on hook")) || 0);
  function drawLeadGrowth(accs, person, acct, planCell) {
    const mine = accs.filter(person);
    const rows = GKINDS.map((g) => { const xs = mine.filter((a) => g.of(a));
      return { ...g, xs, n: xs.length, v: g.worth === "no price" ? null : xs.reduce((t, a) => t + (valueOf(g.of(a)) || 0), 0),
        done: xs.filter((a) => isAddressed(a, g.of(a))).length }; });
    const held = mine.filter((a) => { const f = a.flags.find((x) => x.flag === "growth"); return f && f.status.startsWith("Held"); });
    const inPlay = mine.filter((a) => GKINDS.slice(0, 3).some((g) => g.of(a)));
    const flagsIn = (a) => GKINDS.slice(0, 3).map((g) => g.of(a)).filter(Boolean);
    const doneAcc = inPlay.filter((a) => flagsIn(a).some((f) => isAddressed(a, f)));
    const up = rows[0].v, addon = rows[1].v;
    const pct = (n, d) => d ? `${n} of ${d} (${Math.round(100 * n / d)}%)` : "0";
    $("ld-growth-finding").textContent = inPlay.length
      ? `${segOn() ? segName() + ": " : ""}${money(up + addon)} in play across ${plural(inPlay.length, "account")} (${money(up)} volume upside, ${money(addon)} in add-ons at list price); ${pct(doneAcc.length, inPlay.length)} addressed.`
      : `${segName()}: no growth in play in this segment.`;
    $("ld-growth-mix").innerHTML = `<tr><th>Where it is</th><th class="r">Accounts</th><th class="r">Value</th><th class="r">Addressed</th></tr>` +
      rows.map((r) => `<tr class="click" tabindex="0" data-k="${r.k}"><td>${esc(r.name)}</td><td class="r num">${r.n}</td>` +
        `<td class="r num">${r.v == null ? '<span class="muted">no price</span>' : `${money(r.v)}<small><br>${esc(r.worth)}</small>`}</td>` +
        `<td class="r num">${r.n ? pct(r.done, r.n) : "–"}</td></tr>`).join("") +
      (held.length ? `<tr class="click" tabindex="0" data-k="held"><td colspan="4"><small class="muted">${plural(held.length, "growth hook")} held back (churn risk, data missing, or the basics first), not counted</small></td></tr>` : "");
    const MIX_COLS = [
      { label: "Account", get: acct },
      { label: "What", get: (a) => `<small>${esc((GKINDS.map((g) => g.of(a)).find(Boolean) || a.flags.find((x) => x.flag === "growth") || {}).reasons || "")}</small>` },
      { label: "State", get: (a) => stateCell(a) },
    ];
    $("ld-growth-mix-title").hidden = true; $("ld-growth-mix-list").innerHTML = "";
    $("ld-growth-mix").querySelectorAll("tr.click").forEach((tr) => {
      const go = () => { const r = rows.find((x) => x.k === tr.dataset.k);
        $("ld-growth-mix-title").hidden = false;
        accountList($("ld-growth-mix-list"), $("ld-growth-mix-title"), r ? r.name : "Held back", r ? r.xs : held, MIX_COLS, false); };
      tr.addEventListener("click", go); tr.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
    });
    // the state of an account's growth: the hook's state, else the expansion's (addressed or not)
    function stateCell(a) {
      const gs = growthState(a);
      if (gs && gs.k !== "held") return esc(gs.text);
      const e = a.flags.find((x) => x.flag === "expansion");
      if (!e) return gs ? esc(gs.text) : "–";
      const d = addressedIn(a.account_id, flagSince(a.account_id, "expansion"), "9999");
      return d ? (d.k === "raised" ? `raised ${fmt(d.date)}` : `planned${d.due ? ` for ${fmt(d.due)}` : ""}`) : '<span class="bad">not yet</span>';
    }
    // biggest first: volume upside plus an add-on's list price; a people hook has no price, so it sorts last
    const top = inPlay.slice().sort((a, b) => upsideOf(b) - upsideOf(a) || a.account_name.localeCompare(b.account_name));
    shortList($("ld-growth"), top, [
      { label: "Account", get: acct },
      { label: "In play", get: (a) => `<b>${upsideOf(a) ? money(upsideOf(a)) : '<span class="muted">no price</span>'}</b><small><br>${esc(flagsIn(a).map((f) => f.flag === "expansion" ? "Expansion " + f.status : f.status).join(" + "))} · ARR ${money(a.arr)}</small>` },
      { label: "Renewal", get: (a) => `${fmt(a.renewal_date)} ${yr4(a.renewal_date)}<small><br>${ahead(a.renewal_date)}</small>` },
      { label: "Addressed", get: stateCell },
      { label: "Action planned", get: planCell },
    ], "No growth in play in this segment.", 5);
    // the trend: accounts with growth in play each week, addressed by the end of that week or not.
    // The history keeps statuses, not values, so this counts accounts; the euros are today's only.
    const has = HIST.weeks.length > 1;
    $("ld-growth-trend-block").hidden = !has;
    $("ld-growth-note").textContent = `Volume upside: forecast usage above the contract at renewal. Add-on: its list price, an estimate, not a forecast. A people hook has no price. An add-on bought and not used is a renewal risk, so it is never counted as upside. Addressed: a meeting held or a next step planned since the signal began, or marked not a fit. The trend counts accounts (Enterprise and Scale): the weekly history keeps statuses, not values. On the demo data the growth hooks were planted in the last few months, so the early weeks are nearly empty.`;
    if (!has) return;
    const W = HIST.weeks.length;
    const inPlayRow = (r) => (r[2] === "expansion" || (r[2] === "growth" && ["Add-on hook", "People hook", "Not a fit"].includes(r[3]))) && histIn(r[1]) && (byId[r[1]] || offBook[r[1]] || {}).tier !== "Self-service";
    const byWeek = HIST.weeks.map(() => new Map());
    for (const r of HIST.flags) if (inPlayRow(r)) (byWeek[r[0]].get(r[1]) || byWeek[r[0]].set(r[1], []).get(r[1])).push(r);
    const weekDone = (i, id, rs) => rs.some((r) => r[3] === "Not a fit" || !!addressedIn(id, flagSince(id, r[2], i), HIST.weeks[i]));
    const per = byWeek.map((m, i) => [...m].map(([id, rs]) => ({ account_id: id, done: weekDone(i, id, rs), what: rs.map((r) => r[2] === "expansion" ? "Expansion " + r[3] : r[3]).join(" + ") })));
    const GCATS = [{ k: "done", name: "Addressed", color: "var(--good)" }, { k: "not", name: "Not yet", color: "var(--viz-pending)" }];
    const tw = Math.min(440, $("ld-growth-trend").clientWidth || 440);
    stackChart($("ld-growth-trend"), { W: tw, H: 140, cats: GCATS, unit: "account", totals: "last",
      aria: "Accounts with growth in play, addressed or not, week by week",
      bars: per.map((xs, i) => ({ label: weekLabel(i), tip: `Week ending ${fmtFull(HIST.weeks[i])}`,
        counts: { done: xs.filter((x) => x.done).length, not: xs.filter((x) => !x.done).length },
        onClick: () => accountList($("ld-growth-week"), $("ld-growth-week-title"), `Growth in play, week ending ${fmtFull(HIST.weeks[i])}`,
          xs.slice().sort((a, b) => a.done - b.done), [
            { label: "Account", get: (x) => nameOf(x.account_id) },
            { label: "What", get: (x) => esc(x.what) },
            { label: "Addressed", get: (x) => x.done ? "yes" : '<span class="bad">not yet</span>' },
          ], false) })) });
    stackLegend($("ld-growth-legend"), GCATS);
    $("ld-growth-week-title").textContent = "Click a week to list the accounts"; $("ld-growth-week").innerHTML = "";
    const now = per[W - 1], then = per[Math.max(0, W - 5)];
    const share = (xs) => xs.length ? `${xs.filter((x) => x.done).length} of ${xs.length}` : "none";
    $("ld-growth-finding").textContent += ` Four weeks ago: ${share(then)} addressed.`;
  }
  function drawLeadership() {
    if (!$("view-lead")) return;                     // not on this person's page
    const accs = D.accounts.filter(inSeg), person = (a) => a.tier !== "Self-service";
    const flagOf = (a, f) => a.flags.find((x) => x.flag === f);
    const churn = accs.filter((a) => flagOf(a, "churn") && person(a)).sort((a, b) => (valueOf(flagOf(b, "churn")) || 0) - (valueOf(flagOf(a, "churn")) || 0));
    const growth = accs.filter((a) => flagOf(a, "expansion") && person(a)).sort((a, b) => (valueOf(flagOf(b, "expansion")) || 0) - (valueOf(flagOf(a, "expansion")) || 0));
    const sum = (xs, f) => xs.reduce((t, x) => t + (f(x) || 0), 0);
    const hooksOpen = accs.filter((a) => person(a) && openHook(flagOf(a, "growth")));
    const atRisk = sum(churn, (a) => valueOf(flagOf(a, "churn"))), upside = sum(growth, (a) => valueOf(flagOf(a, "expansion")));
    const bookArr = sum(accs.filter(person), (a) => a.arr), wholeArr = sum(accs, (a) => a.arr);
    // Enterprise and Scale ARR, owned or not: the part with no owner yet is said, so it reconciles
    // with Business case and CS team, which count only accounts with a named CSM or pool member
    const unownedArr = sum(accs.filter((a) => person(a) && !["csm", "member"].includes((teamById[a.owner_id] || {}).owner_type)), (a) => a.arr);
    const act = churn.filter((a) => flagOf(a, "churn").status === "Act");
    // action taken: the team report's rule only (time_to_action: a churn call, or a QBR held after the signal)
    const called = (a) => { const t = D.time_to_action.find((r) => r.account_id === a.account_id && r.flag === "churn"); return !!(t && t.call_date); };   // the same rule as every tab: time_to_action
    const planned = (a) => !!planOf(a);
    const overdue = churn.filter((a) => { const p = planOf(a); return p && (planStatus(a, p) || {}).overdue; });
    const gaps = accs.filter((a) => { const f = (forecasts[a.account_id] || []).slice(-1)[0]; return f && forecastGap(a, f); });
    const actCalled = act.filter(called).length, actPlanned = act.filter(planned).length;
    $("ld-finding").textContent = churn.length
      ? `${segOn() ? segName() + ": " : ""}${money(atRisk)} of ${money(bookArr)} Enterprise and Scale ARR is at risk (${unownedArr ? `${money(unownedArr)} of it has no owner yet; ` : ""}whole book ${money(wholeArr)}); ${!act.length ? "no account is at churn Act." : act.length === actPlanned ? `all ${act.length} churn Act accounts have something planned.` : `${act.length - actPlanned} of ${act.length} churn Act accounts have nothing planned.`}`
      : `${segName()}: no CSM or pool account at churn risk.`;
    // not reached: Act accounts with no call, judged on the account (17 Nov 2026: a claimed task
    // with no call, or an Act account whose task never went out, hid inaction)
    $("ld-reach-days").textContent = REACH_TEXT;
    $("ld-reach-early").textContent = D.settings.early_days ?? 14;   // team_report's rule: an early call counts
    const liveTask = (id, flag) => D.tasks.filter((t) => t.account_id === id && t.flag === flag && t.status === "Act" && liveT(t)).slice(-1)[0];
    const unreached = (D.time_to_action || []).filter((r) => !r.call_date && inSegId(r.account_id) && person(byId[r.account_id] || {}))
      .map((r) => ({ ...r, s: reachOf(r.account_id, r.flag), task: liveTask(r.account_id, r.flag) })).sort((a, b) => a.s.left - b.s.left);
    const late = unreached.filter((r) => r.s.left < 0);
    $("ld-reach-finding").textContent = !unreached.length ? "Every account at Act has been reached."
      : `${unreached.length} account${unreached.length === 1 ? "" : "s"} at Act not reached yet, ${late.length} past ${late.length === 1 ? "its" : "their"} deadline` +
        // the task count covers every row above it, so the sentence matches the table (18 Nov 2026)
        (unreached.filter((r) => !r.task).length ? `; ${unreached.filter((r) => !r.task).length} of the ${unreached.length} have no live task to prompt anyone.` : ".");
    shortList($("ld-reach"), unreached, [
      { label: "Account", get: (r) => `${nameOf(r.account_id)}<small><br>${esc(tierOf(r.account_id) || "")}</small>` },
      { label: "Act", get: (r) => esc(r.flag) },
      { label: "With", get: (r) => esc(ownerName[r.routed_to] || r.routed_to) + `<small><br>${r.task ? "task " + esc(r.task.action_status.toLowerCase()) : "no live task"}</small>` },
      { label: "Reach by", r: true, get: (r) => reachBadge(r.s) },
    ], "Every account at Act has been reached.", 5);
    // not picked up: every task still Open, oldest first (amber past the stale line, like Tasks)
    const STALE = D.settings.stale_task_days || 14;
    $("ld-queue-stale").textContent = STALE;
    const waiting = D.tasks.filter((t) => t.action_status === "Open" && inSegId(t.account_id)).sort((a, b) => b.age_days - a.age_days);
    const stale = waiting.filter((t) => t.age_days >= STALE);
    const who = (id) => ownerName[id] || id;
    $("ld-queue-finding").textContent = waiting.length
      ? `${waiting.length} task${waiting.length === 1 ? "" : "s"} not picked up; ${stale.length} ${stale.length === 1 ? "has" : "have"} waited ${STALE}+ days, the oldest ${waiting[0].age_days} days (${who(waiting[0].routed_to)}).`
      : "Every task sent to a person has been picked up.";
    shortList($("ld-queue"), waiting, [
      { label: "Account", get: (t) => `${nameOf(t.account_id)}<small><br>${esc(byId[t.account_id]?.tier || "")}</small>` },
      { label: "Task", get: (t) => `${esc(t.flag)} ${esc(t.status)}` },
      { label: "With", get: (t) => esc(who(t.routed_to)) + (t.assigned_by ? `<small><br>given by ${esc(who(t.assigned_by))}</small>` : "") },
      { label: "Waiting", r: true, get: (t) => `<span class="badge Open${t.age_days >= STALE ? " stale" : ""}">${t.age_days} days</span>` },
    ], "Nothing waiting: every task sent to a person has been picked up.", 5);
    // new Enterprise accounts with no CSM: the activation flag's "No CSM assigned"
    const noCsm = accs.filter((a) => a.tier === "Enterprise" && a.flags.some((f) => f.status === "No CSM assigned"))
      .map((a) => ({ ...a, waitDays: day(D.as_of) - day(a.contract_start) })).sort((a, b) => b.waitDays - a.waitDays);
    $("ld-nocsm-finding").textContent = noCsm.length
      ? `${noCsm.length} Enterprise account${noCsm.length === 1 ? "" : "s"} with no CSM: ${money(sum(noCsm, (a) => a.arr))} ARR, the longest ${noCsm[0].waitDays} days since signing.`
      : "Every Enterprise account in this segment has a CSM.";
    shortList($("ld-nocsm"), noCsm, [
      { label: "Account", get: (a) => `${esc(a.account_name)}<small><br>${esc(a.region)}</small>` },
      { label: "Signed", get: (a) => `${fmt(a.contract_start)}<small><br>${a.waitDays} days ago</small>` },
      { label: "ARR", r: true, get: (a) => money(a.arr) },
    ], "Every Enterprise account in this segment has a CSM.", 5);
    drawDataGaps(accs, who);
    drawLeadTrends(STALE, who);
    drawGlossary();
    drawSignals();
    // one clock for a late task, the same as Tasks: its tier's days to reach the customer (22 Nov 2026, definitions pass)
    drawOpsLine(late, waiting.filter((t) => t.age_days >= REACH(tierOf(t.account_id))), noCsm);
    // the trail: four stages, each a cairn whose colour and height say how urgent it is
    const pct = (n, d) => d ? `${n} of ${d} (${Math.round(100 * n / d)}%)` : `${n}`;
    const notCalled = act.filter((a) => !called(a)), noPlan = act.filter((a) => !planned(a));
    const stages = [
      { key: "risk", v: churn.length, l: "accounts at risk", r: `${money(atRisk)} of ${money(bookArr)} ARR`, list: churn,
        lvl: churn.length > act.length ? "attention" : churn.length ? "act" : "ok",
        u: churn.length > act.length ? `${churn.length - act.length} at Watch or Shelfware` : churn.length ? "all at Act" : "nothing at risk" },
      { key: "act", v: act.length, l: "at Act", r: pct(act.length, churn.length), list: act,
        lvl: act.length ? "act" : "ok", u: act.length ? `reach within ${REACH_SHORT}` : "none at Act" },
      { key: "called", v: actCalled, l: "called", r: act.length ? pct(actCalled, act.length) : "no Act account", list: act.filter(called),
        lvl: notCalled.length ? "act" : "ok", u: notCalled.length ? `${notCalled.length} still to call` : "all called", gap: notCalled },
      { key: "planned", v: actPlanned, l: "with a next step", r: (act.length ? pct(actPlanned, act.length) : "no Act account") + (overdue.length ? ` · ${overdue.length} overdue` : " · none overdue"),
        list: act.filter(planned), lvl: noPlan.length || overdue.length ? "act" : "ok", u: noPlan.length ? `${noPlan.length} with no plan` : overdue.length ? `${overdue.length} overdue` : "all planned", gap: noPlan },
    ];
    const CAIRN = { act: "cairn-red", attention: "cairn-amber", ok: "cairn-green" };
    const LEVEL = { act: "act now", attention: "needs attention", ok: "on track" };
    leadStages = stages;
    $("ld-tiles").innerHTML =
      `<div class="trail" role="group" aria-label="From the signal to a plan"><svg class="path" aria-hidden="true" viewBox="0 0 1000 40" preserveAspectRatio="none"><path d="M0 20 C150 0 260 40 360 20 S590 0 650 20 S850 40 1000 20"></path></svg>` +
      stages.map((st) => `<button type="button" class="wp ${st.lvl}" data-stage="${st.key}" aria-label="${st.v} ${st.l}, ${LEVEL[st.lvl]}: ${esc(st.u)}. Show the accounts">` +
        `<svg aria-hidden="true" viewBox="0 0 40 50"><use href="#${CAIRN[st.lvl]}"></use></svg>` +
        `<span class="v">${st.v}</span> <span class="l">${st.l}</span> <span class="r">${st.r}</span> <span class="u">${esc(st.u)}</span></button>`).join("") +
      `</div><div class="tiles">` +
      tile("ARR at risk", money(atRisk), `${churn.length} accounts · ${act.length} at Act`, act.length > 0) +
      tile("Upside", money(upside), `${growth.length} accounts with an expansion flag` + (hooksOpen.length ? ` · ${plural(hooksOpen.length, "growth hook")} open` : ""), false) +
      tile("Forecast ≠ data", gaps.length, "owner's forecast a quarter of the ARR from the data", gaps.length > 0) + `</div>`;
    $("ld-trail-panel").hidden = true;
    drawExposure(churn, atRisk, accs.filter(person));
    $("ld-tiles").querySelectorAll(".wp").forEach((b) => b.addEventListener("click", () => {
      const st = leadStages.find((x) => x.key === b.dataset.stage);
      const items = st.gap && st.gap.length ? st.gap : st.list;           // the stages that fall short list who's missing
      const title = st.gap && st.gap.length ? st.u : `${st.v} ${st.l}`;
      $("ld-trail-panel").hidden = false;
      accountList($("ld-trail-list"), $("ld-trail-title"), title[0].toUpperCase() + title.slice(1), items, TRAIL_COLS, false);
      $("ld-trail-panel").scrollIntoView({ block: "nearest" });
    }));
    const lastCust = (id) => { const t = (touchesBy[id] || []).filter((x) => ["meeting_held", "email_sent", "workshop_attended"].includes(x.event)).slice(-1)[0];
      return t ? `${esc(topicName(t.topic))} <small>${fmt(t.date)}</small>` : '<span class="muted">none in 90 days</span>'; };
    const planCell = (a) => { const p = planOf(a); return p ? planText(a, p) : '<span class="bad">none</span>'; };
    const fcCell = (a) => { const f = (forecasts[a.account_id] || []).slice(-1)[0]; return f ? esc(callText(f)) + (forecastGap(a, f) ? ' <span class="bad">≠ data</span>' : "") : '<span class="muted">none</span>'; };
    const acct = (a) => `${esc(a.account_name)}<small><br>${esc(a.owner || "")}</small>`;
    $("ld-churn-finding").textContent = churn.length ? `${act.length} Act, ${churn.length - act.length} Watch or Shelfware; ${overdue.length} next step${overdue.length === 1 ? " is" : "s are"} overdue.` : "";
    shortList($("ld-churn"), churn, [
      { label: "Account", get: acct },
      { label: "At risk", get: (a) => `<b>${money(valueOf(flagOf(a, "churn")))}</b><small><br>${esc(flagOf(a, "churn").status)}</small>` },
      { label: "Renewal", get: (a) => `${fmt(a.renewal_date)} ${yr4(a.renewal_date)}<small><br>${ahead(a.renewal_date)}</small>` },
      { label: "Action taken", get: (a) => lastCust(a.account_id) },
      { label: "Action planned", get: planCell },
      { label: "Forecast", get: fcCell },
    ], "No account at churn risk in this segment.");
    drawLeadGrowth(accs, person, acct, planCell);
    // team: meetings in 30 days and share of the book met, against the median of the same kind
    const people = D.team.filter((t) => (!seg.manager || t.manager === seg.manager) && (!seg.team || t.region === seg.team) && (!seg.owner || t.owner_id === seg.owner));
    const stat = (t) => { const book = accountsOf(t.owner_id);
      const m = D.touches.filter((x) => x.event === "meeting_held" && x.owner_id === t.owner_id && day(x.date) > RUN - 31).length;
      return { t, book: book.length, arr: sum(book, (a) => a.arr), risk: sum(book, (a) => valueOf(flagOf(a, "churn"))), meetings: m, met: book.filter((a) => met30(a.account_id)).length }; };
    const all = D.team.map(stat), med = {};
    for (const k of Object.keys(KIND_ORDER)) { const g = all.filter((r) => r.t.owner_type === k); med[k] = { m: peerMed(k, "meetings", () => median(g.map((r) => r.meetings))), s: peerMed(k, "met_all", () => median(g.map((r) => r.book ? r.met / r.book : 0))) }; }
    // people only: a pool is a queue, its meetings are its members'
    const rows = all.filter((r) => people.includes(r.t) && r.t.owner_type !== "pool").sort((x, y) => KIND_ORDER[x.t.owner_type] - KIND_ORDER[y.t.owner_type]);
    const pc = (x) => Math.round(100 * x);               // compare what the reader sees
    const GAP = D.settings.below_median_points ?? 10;      // a point or two under the median is noise on a small book
    const low = rows.filter((r) => r.book && pc(r.met / r.book) <= pc(med[r.t.owner_type].s) - GAP);
    $("ld-team-finding").textContent = rows.length ? `${low.length} of ${rows.length} owners met a share of their book in 30 days ${GAP}+ points below the median for their kind.` : "Nobody on the CS team in this segment.";
    queueMicrotask(() => foldRows($("ld-team")));
    $("ld-team").innerHTML = `<tr><th>Owner</th><th class="r">Book ARR</th><th class="r">At risk</th><th class="r">Meetings, 30 d</th><th class="r">Book met, 30 d</th></tr>` + rows.map((r) => {
      const share = r.book ? r.met / r.book : 0, m = med[r.t.owner_type];
      return `<tr><td>${esc(r.t.owner_name)}<small><br>${esc(r.t.region)}</small></td><td class="r num">${money(r.arr)}</td><td class="r num">${money(r.risk)}</td>` +
        `<td class="r num">${r.meetings} <small>(median ${m.m})</small></td><td class="r num${r.book && pc(share) <= pc(m.s) - GAP ? " bad" : ""}">${r.met} of ${r.book} (${Math.round(100 * share)}%) <small>(median ${Math.round(100 * m.s)}%)</small></td></tr>`; }).join("");
  }

