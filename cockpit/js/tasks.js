  // ---------------------------------------------------------------- tasks: the manager's queue
  // Every task sent to a person (action_status), filtered by the segment. A
  // manager acts on their own team's tasks: give one to someone else, or
  // approve a hand-back. Decisions live in the published page's database
  // (collection "decisions", one document per task and kind); cockpit_sync.py
  // turns them into engagement events on the next run. Without the database
  // (the offline file, or no permission) the list is read-only.
  const TASK_STATES = ["Open", "Claimed", "Done", "Resolved"];
  const tkShow = new Set(["Open", "Claimed"]);
  const TASKS = D.tasks || [];
  const taskInSeg = (t) => {
    const a = byId[t.account_id];
    // CS ops items with no account in the CRM (handoff gaps) belong to no team: every manager sees them
    if (seg.acc) return pinned().includes(t.account_id);       // pinned accounts win, as in inSeg
    return (!seg.manager || t.manager === seg.manager || (t.manager == null && !seg.owner && !seg.team)) && (!seg.owner || t.routed_to === seg.owner || poolTaskOf(t, seg.owner))
      && (!seg.team || (teamById[t.routed_to] && teamById[t.routed_to].region === seg.team))
      && (!seg.region || (a && a.region === seg.region)) && (!seg.tier || (a && a.tier === seg.tier));
  };
  // a pool member's work: tasks routed to them, plus pool-queue tasks on their own accounts
  const poolTaskOf = (t, owner) => {
    const o = teamById[owner], q = teamById[t.routed_to], a = byId[t.account_id];
    return !!(o && o.owner_type === "member" && q && q.owner_type === "pool" && a && a.owner_id === owner);
  };
  const docId = (kind, t) => [kind, t.account_id, t.flag, t.status].join("~").replace(/[^A-Za-z0-9_.~:@+-]/g, "-");
  const canAssign = (t) => !!teamById[t.routed_to] && (t.action_status === "Open" || t.action_status === "Claimed");
  const canApprove = (t) => t.status === "Follow-up not picked up" && t.action_status === "Open";
  function assignSelect(t) {
    // the manager's own team first, then everyone else; never the current owner
    const mine = D.team.filter((o) => o.manager === seg.manager && o.owner_id !== t.routed_to);
    const rest = D.team.filter((o) => o.manager !== seg.manager && o.owner_id !== t.routed_to);
    const group = (label, os) => os.length ? `<optgroup label="${esc(label)}">` + os.map((o) => `<option value="${esc(o.owner_id)}">${esc(o.owner_name)}</option>`).join("") + "</optgroup>" : "";
    return `<select data-assign="${esc(docId("assign", t))}" aria-label="Give this task to"><option value="">Give to…</option>` +
      group("Your team", mine) + group("Other teams", rest) + "</select>";
  }
  function decisionCell(t) {
    const live = dbState === "ready" && (PAGE ? PAGE.role === "manager" && t.manager === PAGE.person : seg.manager && t.manager === seg.manager);
    const a = decisions[docId("assign", t)], h = decisions[docId("handback", t)];
    if (canApprove(t) && t.routed_to === (PAGE ? PAGE.person : seg.manager) && mayEdit("handback", t.account_id)) {
      if (h) return `<span class="pending">Hand-back approved: back to the pool on the next run</span> ${live ? `<button class="link" data-undo="${esc(docId("handback", t))}">Undo</button>` : ""}`;
      return live ? `<button class="small primary" data-approve="${esc(docId("handback", t))}">Approve hand-back</button>` : "—";
    }
    if (!canAssign(t) || !mayEdit("assign", t.account_id)) return "—";
    if (a && a.to_owner !== t.routed_to)
      return `<span class="pending">Going to ${esc(ownerName[a.to_owner] || a.to_owner)} on the next run</span> ${live ? `<button class="link" data-undo="${esc(docId("assign", t))}">Undo</button>` : ""}`;
    return live ? `<div class="act">${assignSelect(t)}</div>` : "—";
  }
  // the flag's ask today, under a live task whose text has fallen behind it (16 Nov 2026 review:
  // Driftwood's task still said "agree a ramp plan" while its flag said "call this week")
  const nowAsk = (t) => {
    const f = ((byId[t.account_id] || {}).flags || []).find((x) => x.flag === t.flag);
    // only when the ask got more urgent: a deadline in the flag's text that the task's lacks (a
    // reworded but unchanged ask is noise); a different status (Watch -> Act) always shows
    const URGENT = /this week|act now|renewal in|renewal within|overage/i;
    const sharper = f && (f.status !== t.status || (URGENT.test(f.action) && !URGENT.test(t.action)));
    return f && f.action && liveT(t) && sharper && !t.action.endsWith(f.action) ? `<small class="nowask"><br><b>Now:</b> ${esc(f.status === t.status ? "" : f.status + ": ")}${esc(f.action)}</small>` : "";
  };
  // a CSM's or pool member's page shows My work, the queue (18 Nov 2026)
  const QUEUE = !!(PAGE && (PAGE.role === "csm" || PAGE.role === "member"));
  if (QUEUE && $("tab-tasks")) {                     // a CSM's page: the tab is their work, not "Tasks"
    $("tab-tasks").textContent = "My work";
    const tnote = document.querySelector("#view-tasks .panel .note");
    if (tnote) tnote.textContent = "At stake: the money the flag puts at risk (Act: the whole ARR; Watch and Shelfware: the unused part) or in play (expansion upside). \"Nothing planned\": no next step and no QBR booked.";
    const sub = document.querySelector("#view-tasks .sub");
    if (sub) sub.textContent = "What to pick up, the most cash at stake per day left first: to the reach deadline at Act, else to the renewal. ARR is there as a fact and breaks ties. Click Last or Next to see that engagement.";
  }
  // ---------------------------------------------------------------- Operational: the funnel (22 Nov 2026)
  // Each task from "a person is needed" to "done": every stage is a subset of the one above
  // (a later stage implies the earlier ones). Done = the right meeting happened (team_report's
  // rule); "aim checked" is a share of Done beside it, not a stage. Resolved tasks (the flag
  // went away before anyone met) leave the funnel and are counted in the note.
  const metAfter = (t) => (D.touches || []).some((x) => x.account_id === t.account_id && x.event === "meeting_held" && x.date >= t.sent_on);
  const ownedBy = (t) => (byId[t.account_id] && poolTaskOf(t, byId[t.account_id].owner_id)) ? byId[t.account_id].owner_id : t.routed_to;
  const FUNNEL = [
    { key: "needed", label: "A person is needed", test: () => true },
    { key: "named", label: "Has a named owner", test: (t) => { const o = teamById[t.routed_to]; return !!(o && o.owner_type !== "pool") || !!t.assigned_from; } },   // a pool queue is nobody yet (the opening line's rule)
    { key: "picked", label: "Picked up", test: (t) => t.action_status === "Claimed" },
    { key: "met", label: "Met the customer", test: metAfter },
    { key: "done", label: "Done (the right meeting)", test: (t) => t.action_status === "Done" },
  ];
  // the furthest stage a task reached: each stage counts every task at it or beyond
  const reachIdx = (t) => { let k = 0; FUNNEL.forEach((st, i) => { if (st.test(t)) k = i; }); return k; };
  const aimChecked = (t) => t.action_status === "Done" && (D.outcome_checks || []).some((c) => c.account_id === t.account_id && String(c.decided_at).slice(0, 10) >= t.sent_on);
  let tkStage = null;                                    // a clicked stage: the backlog lists the tasks that stopped before it
  function funnelCounts(T) {
    const n = FUNNEL.map((_, i) => T.filter((t) => reachIdx(t) >= i).length);
    return { n, aim: T.filter(aimChecked).length };
  }
  function drawFunnel(T) {
    const panel = $("tk-funnel-panel");
    if (!panel) return;
    panel.hidden = QUEUE || !TASKS.length;
    if (panel.hidden) return;
    const F = T.filter((t) => t.action_status !== "Resolved"), resolved = T.length - F.length, { n, aim } = funnelCounts(F);
    // the biggest drop between two stages: where the work gets stuck
    let worst = 1;
    for (let i = 1; i < FUNNEL.length; i++) if (n[i - 1] - n[i] > n[worst - 1] - n[worst]) worst = i;
    $("tk-funnel-finding").textContent = !F.length ? `${segName()}: no task needed a person.`
      : `${segName()}: ${n[4]} of ${n[0]} tasks (${pct(n[4], n[0])}%) ended in the right meeting; the biggest drop is before "${FUNNEL[worst].label.toLowerCase()}" (${n[worst - 1] - n[worst]} stopped there).`;
    const host = $("tk-funnel"); host.innerHTML = "";
    const ph = isPhone(), W = fitW(880), LBL = ph ? 0 : 230, BAR = ph ? W - 90 : W - LBL - 170, RH = ph ? 44 : 32, top = n[0] || 1;
    const svg = el("svg", { viewBox: `0 0 ${W} ${FUNNEL.length * RH + 6}`, role: "img", "aria-label": "From signal to outcome, stage by stage" }, host);
    FUNNEL.forEach((st, i) => {
      const y = 4 + i * RH, w = Math.max(2, (BAR * n[i]) / top), x0 = ph ? 4 : LBL, by = ph ? y + 20 : y + 5, ty = ph ? y + 32 : y + 19;
      const g = el("g", { class: "stage", "data-key": st.key, style: "cursor:pointer" }, svg);
      el("text", { x: 4, y: ph ? y + 13 : y + 19, class: "lbl" }, g).textContent = st.label;
      el("rect", { x: x0, y: by, width: w, height: ph ? 16 : RH - 12, rx: 3, fill: "var(--viz-measure)", opacity: tkStage && tkStage !== st.key ? 0.55 : 1 }, g);
      el("text", { x: x0 + w + 6, y: ty, class: "val" }, g).textContent = n[i];
      const share = i ? (ph ? `${pct(n[i], n[i - 1])}%` : `${pct(n[i], n[i - 1])}% of the stage above`) : "";
      if (i === FUNNEL.length - 1) el("text", { x: W - 4, y: ty, "text-anchor": "end" }, g).textContent = ph ? `${share} · aim ${aim}` : `${share} · aim checked ${aim} of ${n[i]}`;
      else if (share) el("text", { x: W - 4, y: ty, "text-anchor": "end" }, g).textContent = share;
      g.addEventListener("click", () => { tkStage = tkStage === st.key ? null : st.key; drawTasks(); });
      hover(g, `<b>${esc(st.label)}</b><br>${n[i]} tasks${i ? `, ${n[i - 1] - n[i]} stopped before this stage` : ""}<br>Click to list ${i ? "the ones that stopped before it" : "them all"}`);
    });
    // per team (the director) or per person (a team lead): the same stages as shares of "needed"
    const byTeam = !seg.manager && !seg.owner, byPerson = !!seg.manager && !seg.owner;
    const rowsHost = $("tk-funnel-rows");
    if (!byTeam && !byPerson) rowsHost.innerHTML = "";
    else {
      const keyOf = (t) => byTeam ? (t.manager || "") : ownedBy(t);
      const nameOfKey = (k) => byTeam ? (k ? `${managerName(k)}'s team` : "No team (CS ops)") : (ownerName[k] || k);
      const keys = [...new Set(F.map(keyOf))].sort((a, b) => nameOfKey(a).localeCompare(nameOfKey(b)));
      const cell = (v, of) => `${v} <small>(${pct(v, of)}%)</small>`;
      rowsHost.innerHTML = `<table><thead><tr><th>${byTeam ? "Team" : "Person"}</th>${FUNNEL.map((st) => `<th class="r">${esc(st.label)}</th>`).join("")}<th class="r">Aim checked</th></tr></thead><tbody>` +
        keys.map((k) => { const c = funnelCounts(F.filter((t) => keyOf(t) === k));
          return `<tr><td>${byTeam && k ? `<button class="link" data-zoom="${esc(k)}">${esc(nameOfKey(k))}</button>` : esc(nameOfKey(k))}</td>${c.n.map((v, i) => `<td class="r num">${i ? cell(v, c.n[0]) : v}</td>`).join("")}<td class="r num">${c.aim} of ${c.n[4]}</td></tr>`; }).join("") + "</tbody></table>";
    }
    rowsHost.querySelectorAll("[data-zoom]").forEach((b) => b.addEventListener("click", () => { for (const k in seg) seg[k] = ""; seg.manager = b.dataset.zoom; saveSeg(); redraw(); }));
    zoomCrumbs($("tk-funnel-crumbs"));
    $("tk-funnel-note").textContent = `Each stage counts the tasks at it or beyond. Named owner: sent to a CSM or pool member, not left in a pool queue (a row per person counts a pool-queue task on that person's account). Met: any meeting on the account since the task was sent; Done: the meeting the flag asked for. Aim checked: the owner said whether the next step did its job (started 22 Nov 2026, so few so far).` +
      (resolved ? ` ${resolved} ${resolved === 1 ? "task" : "tasks"} resolved on ${resolved === 1 ? "its" : "their"} own (the flag went away first) ${resolved === 1 ? "is" : "are"} left out.` : "") +
      (byTeam ? " The director acts through the team lead: click a team to zoom in." : "");
  }
  // ---------------------------------------------------------------- Operational PR2 (22 Nov 2026)
  // Signals that fired: a signal fires in the week an account first shows that flag and status
  // (absent the week before), from the weekly replay (history.py), Enterprise and Scale only.
  // Coverage in value: the ARR of accounts with a meeting in the last cov_days, by tier and stage.
  const OPS = (D.settings && D.settings.operational) || {};
  const SIG_WIN = OPS.signal_weeks || 4, COV_DAYS = OPS.coverage_days || 60;
  const SIG_KINDS = [
    ["churn", "Act", "Churn Act", "plan for the extra calls"], ["churn", "Shelfware", "Shelfware", "plan the adoption reviews"],
    ["churn", "Watch", "Churn Watch", "keep an eye on them"], ["expansion", "Act", "Expansion Act", "plan the growth conversations"],
    ["expansion", "Watch", "Expansion Watch", "plan the tracking checks"], ["activation", "Stalled", "Activation stalled", "plan the onboarding calls"],
    ["activation", "Silent", "Activation silent", "plan the onboarding calls"], ["qbr", "QBR overdue", "QBR overdue", "book the QBRs"],
  ];
  function firedBy() {
    // week index -> Set of "id|flag|status"; a signal fires where it wasn't the week before
    const byWeek = HIST.weeks.map(() => new Set());
    for (const [w, id, f, st] of HIST.flags) byWeek[w] && byWeek[w].add(`${id}|${f}|${st}`);
    const fired = [];
    byWeek.forEach((set, w) => { if (w) for (const k of set) if (!byWeek[w - 1].has(k)) { const [id, f, st] = k.split("|"); fired.push({ w, id, f, st }); } });
    return fired;
  }
  let FIRED = null;
  function drawSignalsFired() {
    if (!HIST.weeks || HIST.weeks.length < 2 * SIG_WIN + 1) { $("op-sig").innerHTML = '<p class="note">Too few weeks of history to compare.</p>'; return; }
    FIRED = FIRED || firedBy();
    const last = HIST.weeks.length - 1, person = (id) => byId[id] && byId[id].tier !== "Self-service" && inSegId(id);
    const F = FIRED.filter((x) => person(x.id));
    const now = (k) => F.filter((x) => x.f === k[0] && x.st === k[1] && x.w > last - SIG_WIN).length;
    const before = (k) => F.filter((x) => x.f === k[0] && x.st === k[1] && x.w > last - 2 * SIG_WIN && x.w <= last - SIG_WIN).length;
    const rows = SIG_KINDS.map((k) => ({ k, n: now(k), b: before(k) })).filter((r) => r.n || r.b);
    const up = rows.filter((r) => r.n - r.b >= Math.max(3, r.b * 0.5)).sort((x, y) => (y.n - y.b) - (x.n - x.b));
    $("op-sig-finding").textContent = !rows.length ? `${segName()}: no signal fired in the last ${SIG_WIN * 2} weeks.`
      : up.length ? `${segName()}: ${up[0].k[2]} fired ${up[0].n} times in the last ${SIG_WIN} weeks, against ${up[0].b} in the ${SIG_WIN} before: ${up[0].k[3]}.`
      : `${segName()}: ${rows.reduce((t, r) => t + r.n, 0)} signals in the last ${SIG_WIN} weeks, against ${rows.reduce((t, r) => t + r.b, 0)} in the ${SIG_WIN} before: no kind jumped.`;
    $("op-sig").innerHTML = `<table><thead><tr><th>Signal</th><th class="r">Last ${SIG_WIN} weeks</th><th class="r">The ${SIG_WIN} before</th><th class="r">Change</th></tr></thead><tbody>` +
      rows.map((r) => `<tr><td>${esc(r.k[2])}</td><td class="r num">${r.n}</td><td class="r num">${r.b}</td><td class="r num">${r.n === r.b ? "–" : (r.n > r.b ? "+" : "−") + Math.abs(r.n - r.b)}</td></tr>`).join("") + "</tbody></table>";
    $("op-sig-note").textContent = `New this week: the account shows the flag and status for the first time since the week before (from the weekly replay, Enterprise and Scale, weeks to ${fmt(HIST.weeks[last])}). An account can fire again after it cleared. "Plan for": a kind up by 3 or more and by half.`;
  }
  const COV_STAGES = [
    ["act", "Churn Act", (a) => (churnFlag(a) || {}).status === "Act"],
    ["shelf", "Shelfware", (a) => (churnFlag(a) || {}).status === "Shelfware"],
    ["watch", "Churn Watch", (a) => (churnFlag(a) || {}).status === "Watch"],
    ["exp", "Expansion", (a) => a.flags.some((f) => f.flag === "expansion")],
    ["none", "No churn or expansion flag", () => true],
  ];
  const stageOfAcc = (a) => COV_STAGES.find(([, , t]) => t(a))[0];
  const metIn = (id) => (D.touches || []).some((x) => x.account_id === id && x.event === "meeting_held" && x.date <= D.as_of && day(D.as_of) - day(x.date) <= COV_DAYS);
  function drawCoverage() {
    $("op-cov-days").textContent = COV_DAYS;
    const A = D.accounts.filter((a) => inSeg(a) && a.tier !== "Self-service");
    const tiers = ["Enterprise", "Scale"].filter((t) => A.some((a) => a.tier === t));
    const cellOf = (st, tier) => A.filter((a) => a.tier === tier && stageOfAcc(a) === st);
    const sum = (xs) => xs.reduce((t, a) => t + (a.arr || 0), 0);
    const act = A.filter((a) => stageOfAcc(a) === "act"), actMet = act.filter((a) => metIn(a.account_id));
    const all = sum(A), met = sum(A.filter((a) => metIn(a.account_id)));
    // the biggest gap: the flagged cell with the most ARR not met
    const gaps = COV_STAGES.filter(([k]) => k !== "none").flatMap(([k, label]) => tiers.map((t) => {
      const c = cellOf(k, t), m = c.filter((a) => metIn(a.account_id)); return { label, t, gap: sum(c) - sum(m), m: m.length, n: c.length }; }))
      .filter((g) => g.gap > 0).sort((x, y) => y.gap - x.gap);
    const g = gaps[0];
    $("op-cov-finding").textContent = !A.length ? `${segName()}: no Enterprise or Scale accounts.`
      : g ? `${segName()}: the biggest gap is ${g.label} on ${g.t}, ${money(g.gap)} of ARR with no meeting in ${COV_DAYS} days (${g.m} of ${g.n} ${g.n === 1 ? "account" : "accounts"} met).`
      : act.length ? `${segName()}: ${money(sum(actMet))} of the ${money(sum(act))} at churn Act (${pct(sum(actMet), sum(act))}%) had a meeting in ${COV_DAYS} days; ${money(met)} of ${money(all)} (${pct(met, all)}%) across Enterprise and Scale.`
      : `${segName()}: ${money(met)} of ${money(all)} ARR (${pct(met, all)}%) had a meeting in ${COV_DAYS} days.`;
    $("op-cov").innerHTML = `<table class="nosort"><thead><tr><th>Stage</th>${tiers.map((t) => `<th class="r">${esc(t)}</th>`).join("")}</tr></thead><tbody>` +
      COV_STAGES.map(([k, label]) => `<tr><td>${esc(label)}</td>${tiers.map((t) => { const c = cellOf(k, t), m = c.filter((a) => metIn(a.account_id));
        return `<td class="r num">${c.length ? `<button class="link" data-cov="${k}|${esc(t)}">${money(sum(m))} of ${money(sum(c))}</button><small><br>${m.length} of ${c.length} accounts (${pct(sum(m), sum(c))}%)</small>` : "–"}</td>`; }).join("")}</tr>`).join("") + "</tbody></table>";
    $("op-cov").querySelectorAll("[data-cov]").forEach((b) => b.addEventListener("click", () => {
      const [k, t] = b.dataset.cov.split("|"), items = cellOf(k, t).sort((x, y) => (y.arr || 0) - (x.arr || 0));
      $("op-cov-title").hidden = false;
      accountList($("op-cov-list"), $("op-cov-title"), `${COV_STAGES.find((x) => x[0] === k)[1]}, ${t}`, items, [
        { label: "Account", get: (a) => nameOf(a.account_id) }, { label: "Owner", get: (a) => ownerOf(a.account_id) },
        { label: "ARR", r: true, get: (a) => money(a.arr) },
        { label: `Met in ${COV_DAYS} days`, get: (a) => metIn(a.account_id) ? "Yes" : '<span class="muted">No</span>' }]);
    }));
  }
  function drawOpsPr2() {
    const host = $("op-pr2");
    if (!host) return;
    host.hidden = QUEUE;
    if (QUEUE) return;
    drawSignalsFired(); drawCoverage();
  }
  // Promises (23 Nov 2026, commitments step 2): every commitment in scope, the late ones first.
  // My work: the ones I owe (my accounts); Operational: the team's or the book's, by person or team.
  const PROMISE_DAYS = 90;                                   // closed ones count over this window
  function promisesInScope() {
    const ids = new Set((D.commitments || []).map((c) => c.account_id).concat(comLive.map((d) => d.account_id)));
    return [...ids].filter((id) => byId[id] && inSeg(byId[id])).flatMap(commitmentsOf)
      .filter((c) => !c.result || TODAY - day(String(c.closed_at).slice(0, 10)) <= PROMISE_DAYS);
  }
  function drawPromises() {
    const panel = $("op-promises-panel");
    if (!panel) return;
    const P = promisesInScope(), st = new Map(P.map((c) => [c, comState(c)]));
    panel.hidden = !P.length && !QUEUE;
    if (panel.hidden) return;
    const open = P.filter((c) => !c.result).sort((x, y) => day(x.due) - day(y.due) || (x.account_id < y.account_id ? -1 : 1));
    const late = open.filter((c) => st.get(c).k === "late"), soon = open.filter((c) => st.get(c).k === "soon");
    const kept = P.filter((c) => c.result === "kept").length, dropped = P.filter((c) => c.result === "dropped").length;
    const oldest = late.length ? TODAY - day(late[0].due) : 0;
    $("op-promises-title").textContent = QUEUE ? "Promises you owe" : "Promises to customers";
    $("op-promises-finding").textContent = !open.length
      ? `${segName()}: no open promise${kept + dropped ? `; ${kept} of ${kept + dropped} closed in ${PROMISE_DAYS} days were kept` : ""}.`
      : `${segName()}: ${open.length} open ${open.length === 1 ? "promise" : "promises"}, ${late.length} late${late.length ? ` (the oldest ${oldest} days)` : ""}` +
        `${soon.length ? `, ${soon.length} due this week` : ""}; ${kept} of ${kept + dropped} closed in ${PROMISE_DAYS} days were kept` +
        `${kept + dropped ? ` (${pct(kept, kept + dropped)}%)` : ""}.`;
    // by team at book level, by person under a manager; none on a person's own page
    const byTeam = !QUEUE && !seg.manager && !seg.owner, byPerson = !QUEUE && !!seg.manager && !seg.owner;
    const rowsHost = $("op-promises-rows");
    if (!byTeam && !byPerson) rowsHost.innerHTML = "";
    else {
      const keyOf = (c) => byTeam ? ((byId[c.account_id] || {}).manager || "") : c.by;
      const nameOfKey = (k) => byTeam ? (k ? `${managerName(k)}'s team` : "No team") : (ownerName[k] || managerName(k) || k);
      const keys = [...new Set(P.map(keyOf))].sort((a, b) => nameOfKey(a).localeCompare(nameOfKey(b)));
      rowsHost.innerHTML = `<table><thead><tr><th>${byTeam ? "Team" : "Person"}</th><th class="r">Open</th><th class="r">Late</th><th class="r">Kept</th><th class="r">Dropped</th></tr></thead><tbody>` +
        keys.map((k) => { const xs = P.filter((c) => keyOf(c) === k), n = (f) => xs.filter(f).length;
          return `<tr><td>${esc(nameOfKey(k))}</td><td class="r num">${n((c) => !c.result)}</td><td class="r num">${n((c) => st.get(c).k === "late")}</td>` +
            `<td class="r num">${n((c) => c.result === "kept")}</td><td class="r num">${n((c) => c.result === "dropped")}</td></tr>`; }).join("") + "</tbody></table>";
    }
    // the open ones, late first: two at a glance, the rest one click away (a dashboard summarises)
    const show = (all) => `<ul class="sum-list">` + (all ? open : open.slice(0, QUEUE ? 5 : 2)).map((c) => `<li><span class="when"><span class="chip ${st.get(c).k}">${esc(st.get(c).t)}</span></span>` +
      `<span><b>${esc(c.what)}</b><br><small><button class="link" data-open="${esc(c.account_id)}">${nameOf(c.account_id)}</button> · by ${fmt(c.due)}` +
      `${QUEUE ? "" : ` · ${esc(ownerName[c.by] || managerName(c.by) || "")}`}</small></span></li>`).join("") + `</ul>` +
      (!all && open.length > (QUEUE ? 5 : 2) ? `<button class="link" id="op-promises-all">View all (${open.length})</button>` : "");
    const host = $("op-promises");
    const paint = (all) => {
      host.innerHTML = open.length ? show(all) : "";
      host.querySelectorAll("[data-open]").forEach((b) => b.addEventListener("click", () => openAccount(b.dataset.open)));
      if ($("op-promises-all")) $("op-promises-all").addEventListener("click", () => paint(true));
    };
    paint(false);
    $("op-promises-note").textContent = "A promise is logged on Slack's call form or on the account page, and closed there (Kept or Dropped). Late: its date has passed and it's still open." +
      (QUEUE ? " Open an account to close one." : "");
  }
  function drawTasks() {
    if (!$("view-tasks")) return;                     // not on this person's page
    const T = TASKS.filter(taskInSeg);
    const n = Object.fromEntries(TASK_STATES.map((s) => [s, T.filter((t) => t.action_status === s).length]));
    const open = T.filter((t) => t.action_status === "Open");
    const pooled = open.filter((t) => teamById[t.routed_to] && teamById[t.routed_to].owner_type === "pool").length;
    $("tk-finding").textContent = !TASKS.length
      ? "No tasks sent yet: run .\\run_all.ps1 -Actions to send them, and the cockpit shows what became of each."
      : `${segName()}: ${n.Open} open${n.Claimed ? ` and ${n.Claimed} claimed` : ""} ${n.Open + n.Claimed === 1 ? "task" : "tasks"}, ${open.filter((t) => t.age_days >= REACH(tierOf(t.account_id))).length} of them late (past ${REACH_TEXT})` +
        (pooled ? `; ${pooled} in a pool queue with nobody named.` : ".");
    // what the viewer can do here
    const banner = $("tk-banner");
    const msg = !TASKS.length ? "" : saveError ? saveError
      : dbState === "none" ? "This copy is read-only: decisions are saved in the published cockpit."
      : dbState === "loading" ? "Loading saved decisions…"
      : !seg.manager && !QUEUE ? "Pick your name under Manager (top of the page) to give tasks to someone else or approve hand-backs." : "";
    banner.setAttribute("role", saveError ? "alert" : "status");   // a failed save is announced at once
    banner.textContent = msg; banner.hidden = !msg;
    // status chips, with counts
    $("tk-status").innerHTML = TASK_STATES.map((s) => `<button class="chip" data-s="${s}" aria-pressed="${tkShow.has(s)}">${s} <span class="num">${n[s]}</span></button>`).join("");
    $("tk-status").querySelectorAll(".chip").forEach((b) => b.addEventListener("click", () => {
      tkShow.has(b.dataset.s) ? tkShow.delete(b.dataset.s) : tkShow.add(b.dataset.s); drawTasks();
    }));
    drawFunnel(T); drawOpsPr2(); drawPromises();
    const stI = tkStage && !QUEUE ? FUNNEL.findIndex((s) => s.key === tkStage) : -1;
    const items = stI >= 0
      ? T.filter((t) => t.action_status !== "Resolved" && (stI === 0 || reachIdx(t) === stI - 1))
      : T.filter((t) => tkShow.has(t.action_status));
    $("tk-title").textContent = stI > 0 ? `Stopped before "${FUNNEL[stI].label.toLowerCase()}": ${items.length}` : `Backlog: ${items.length}`;
    if (stI >= 0) $("tk-title").insertAdjacentHTML("beforeend", ' <button class="link" id="tk-stage-clear">Show the backlog</button>');
    if ($("tk-stage-clear")) $("tk-stage-clear").addEventListener("click", () => { tkStage = null; drawTasks(); });
    const host = $("tk-list");
    if (QUEUE) drawWeek();                            // the week ahead shows even with nothing to pick up
    if (QUEUE && !items.length) {                     // an empty queue: My work's own words, not the manager's
      $("tk-title").textContent = "My work: 0";
      $("tk-finding").textContent = `${segName()}: nothing to pick up.`;
    }
    if (!items.length) { host.innerHTML = '<p class="note">No tasks here.</p>'; return; }
    if (QUEUE) return drawQueue(items, host);        // a CSM's or pool member's page: My work
    const ownerCell = (t) => esc(ownerName[t.routed_to] || t.routed_to) +
      (teamById[t.routed_to] && teamById[t.routed_to].owner_type === "pool" && byId[t.account_id] && teamById[byId[t.account_id].owner_id]
        && teamById[byId[t.account_id].owner_id].owner_type === "member"
        ? `<small class="pooltag"><br>Pool queue · ${seg.owner === byId[t.account_id].owner_id ? "your" : esc(ownerName[byId[t.account_id].owner_id]) + "'s"} account</small>` : "") +
      (t.assigned_from ? `<small><br>given by ${esc(managerName(t.assigned_by))}, was with ${esc(ownerName[t.assigned_from] || t.assigned_from)}</small>` : "");
    // the CSM's order (cash at stake per day left, ARR breaks ties); the director's book is grouped by team
    const ordered = items.slice().sort((x, y) => urgency(y) - urgency(x)
      || ((byId[y.account_id] || {}).arr || 0) - ((byId[x.account_id] || {}).arr || 0) || (x.account_id < y.account_id ? -1 : 1));
    const grouped = !seg.manager && !seg.owner && !seg.acc;
    const teams = grouped ? [...new Set(ordered.map((t) => t.manager || ""))].sort((a, b) => (a ? managerName(a) : "~").localeCompare(b ? managerName(b) : "~")) : [null];
    const row = (t) => `<tr><td><button class="link" data-open="${esc(t.account_id)}">${nameOf(t.account_id)}</button><small><br>${esc(byId[t.account_id] ? byId[t.account_id].tier : "not in the CRM")}</small></td>` +
        `<td>${esc(byId[t.account_id] ? byId[t.account_id].region : "—")}</td>` +
        `<td>${esc(t.flag)} · ${esc(t.status)}<small><br>${esc(t.action)}</small>${nowAsk(t)}</td>` +
        `<td>${ownerCell(t)}</td><td class="r num">${t.age_days} d</td>` +
        `<td>${t.status === "Act" && liveT(t) ? reachBadge(reachOf(t.account_id, t.flag)) : "–"}</td>` +
        `<td>${badge(t)}${slackBtn(t)}</td><td>${decisionCell(t)}</td></tr>`;
    host.innerHTML = `<table><thead><tr><th>Account</th><th>Region</th><th>Task</th><th>Owner</th><th class="r">Age</th><th>Reach by</th><th>Status</th><th>Decision</th></tr></thead><tbody>` +
      teams.map((m) => { const g = m === null ? ordered : ordered.filter((t) => (t.manager || "") === m);
        return (m === null ? "" : `<tr class="group"><td colspan="8">${m ? esc(managerName(m)) + "'s team" : "No team (CS ops)"} (${g.length})</td></tr>`) + g.map(row).join(""); }).join("") + "</tbody></table>";
    const byDoc = Object.fromEntries(items.flatMap((t) => [[docId("assign", t), t], [docId("handback", t), t]]));
    host.querySelectorAll("[data-open]").forEach((b) => b.addEventListener("click", () => openAccount(b.dataset.open)));
    host.querySelectorAll("select[data-assign]").forEach((s) => s.addEventListener("change", () => { if (s.value) decide("assign", byDoc[s.dataset.assign], s.value, s); }));
    host.querySelectorAll("[data-approve]").forEach((b) => b.addEventListener("click", () => decide("handback", byDoc[b.dataset.approve], null, b)));
    host.querySelectorAll("[data-undo]").forEach((b) => b.addEventListener("click", () => undo(b.dataset.undo, b)));
    wireSlack(host, 8);
  }
