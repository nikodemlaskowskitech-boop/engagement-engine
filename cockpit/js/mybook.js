  let mbPortView = "all";                              // My book's portfolio: All | Churn | Growth
  function drawMyBook() {
    if (!$("view-mybook")) return;                     // not on this person's page
    // a pool's book = its team + the Scale tier (pools aren't in the Owner list)
    const o = segOwner(), name = ownerName[o] || o;
    $("mb-pick").innerHTML = "";
    $("mb-body").hidden = !o;
    if (!o) {
      $("mb-finding").textContent = "Pick an owner to see their book.";
      const owners = D.team.filter((t) => !seg.manager || t.manager === seg.manager);
      $("mb-pick").innerHTML = owners.map((t) => `<button class="chip" data-o="${esc(t.owner_id)}">${esc(t.owner_name)}</button>`).join("");
      $("mb-pick").querySelectorAll(".chip").forEach((b) => b.addEventListener("click", () => {
        const t = teamById[b.dataset.o];
        if (t.owner_type === "pool") { seg.owner = seg.region = ""; seg.team = t.region; seg.tier = "Scale"; }
        else { seg.owner = t.owner_id; seg.team = seg.region = seg.tier = ""; }
        saveSeg(); redraw(); }));
      return;
    }
    const peers = peersOf(o), okind = (teamById[o] || {}).owner_type, kind = kindName(okind) + (PM_TEAM ? " in the team" : "");
    const med = (f) => { const m = median(peers.map(f)); return m == null ? "–" : Math.round(m * 10) / 10; };

    // 0. where I stand, in value (18 Nov 2026, Nikodem: the leader's KPIs are the CSM's, on their
    // own book, same definitions, so the numbers roll up; value, not volume; ARR a fact, never
    // the only driver). The book's figures are the reference.
    const ref = bookRef(), fdays = ref.forecast_days || 180;
    const kAccs = accountsOf(o), kArr = kAccs.reduce((t, a) => t + (a.arr || 0), 0);
    const churnOf = (a) => a.flags.find((x) => x.flag === "churn");
    const kRisk = kAccs.reduce((t, a) => t + (valueOf(churnOf(a)) || 0), 0);
    const kAct = kAccs.filter((a) => (churnOf(a) || {}).status === "Act");
    const kActArr = kAct.reduce((t, a) => t + (valueOf(churnOf(a)) || 0), 0);
    const kReached = kAct.filter((a) => { const s = reachOf(a.account_id, "churn"); return s && s.reached; })
      .reduce((t, a) => t + (valueOf(churnOf(a)) || 0), 0);
    const metArr = (as) => as.filter((a) => met30(a.account_id)).reduce((t, a) => t + (a.arr || 0), 0);
    const kMet = metArr(kAccs);
    const medMetV = peerMed(okind, "met_value", () => median(peers.map((p) => { const as = accountsOf(p), t = as.reduce((s, a) => s + (a.arr || 0), 0);
      return t ? metArr(as) / t : null; }).filter((x) => x != null)));
    const ups = kAccs.filter((a) => a.renewal_date && day(a.renewal_date) > RUN && day(a.renewal_date) <= RUN + fdays);
    const upArr = ups.reduce((t, a) => t + (a.arr || 0), 0);
    const loss = ups.reduce((t, a) => { const fc = (forecasts[a.account_id] || []).slice(-1)[0]; return t + (fc ? expectedLoss(a, fc) : dataRisk(a)); }, 0);
    const grr = upArr ? (upArr - loss) / upArr : null;
    const p100 = (x) => (x == null ? "–" : `${Math.round(100 * x)}%`);
    // growth apart from churn, as the leader reports them (20 Nov 2026): the upside the expansion
    // flags put in play, never netted against the risk
    const expOf = (a) => a.flags.find((x) => x.flag === "expansion");
    const kUp = kAccs.reduce((t, a) => t + (valueOf(expOf(a)) || 0), 0);
    const kUpN = kAccs.filter((a) => expOf(a)).length;
    const kUpAct = kAccs.filter((a) => (expOf(a) || {}).status === "Act").reduce((t, a) => t + (valueOf(expOf(a)) || 0), 0);
    // growth hooks beside it, labelled apart: an add-on's list price is a guess, not a forecast
    const kHooks = kAccs.filter((a) => openHook(growthOf(a)));
    const kHookUp = kHooks.filter((a) => growthOf(a).status === "Add-on hook").reduce((t, a) => t + (valueOf(growthOf(a)) || 0), 0);
    $("mb-kpi").innerHTML =
      tile("My book", money(kArr), `${plural(kAccs.length, "account")}${ref.arr ? ` · ${pct(kArr, ref.arr)}% of the book` : ""}`, false) +
      tile("ARR at risk", money(kRisk), `${kArr ? pct(kRisk, kArr) : 0}% of my ARR` +
        (kActArr ? ` · ${money(kReached)} of ${money(kActArr)} at Act reached in time` : " · none at Act"), kActArr > kReached) +
      tile("Upside in play", money(kUp), (kUpN ? `${plural(kUpN, "account")} with a growth signal${kUpAct ? ` · ${money(kUpAct)} at Act` : ""}${kUp ? "" : " · none forecast above contract yet"}` : "no growth signal") +
        (kHooks.length ? ` · ${plural(kHooks.length, "growth hook")} open${kHookUp ? ` (${money(kHookUp)} in add-ons, list price)` : ""}` : ""), false) +
      tile("ARR met in 30 days", p100(kArr ? kMet / kArr : null), `${money(kMet)} of ${money(kArr)} · median for ${kind}: ${p100(medMetV)}`,
        medMetV != null && kArr && kMet / kArr < medMetV) +
      tile(`Renewals, next ${fdays} days`, grr == null ? "–" : `${p100(grr)} kept`,
        upArr ? `forecast: ${money(upArr - loss)} of ${money(upArr)} · the book: ${p100(ref.forecast_grr)}` : "no renewal in the window",
        grr != null && ref.forecast_grr != null && grr < ref.forecast_grr);
    $("mb-kpi-note").textContent = `The leader's numbers, on this book, with the same definitions: they add up to the book's. At risk: Act counts the whole ARR, Watch and Shelfware the unused part. Met: a meeting in the last 30 days, counted by the account's ARR, not by the number of meetings. Renewals: the owner's forecast where set, else the data's, as on the leader's page.`;

    // 1. workload
    const mine = (p) => workOf(p).filter(LIVE);
    const live = mine(o).sort((a, b) => b.age_days - a.age_days);
    const open = live.filter((t) => t.action_status === "Open");
    const old = live.filter((t) => t.age_days >= REACH(tierOf(t.account_id))), given = live.filter((t) => t.assigned_from);
    const qbr30 = accountsOf(o).filter((a) => a.next_qbr_date && day(a.next_qbr_date) >= RUN && day(a.next_qbr_date) < RUN + 30);
    $("mb-finding").textContent = `${name}: ${money(kArr)} ARR, ${money(kRisk)} of it at risk; ${p100(kArr ? kMet / kArr : null)} of the ARR met in the last 30 days.`;
    $("mb-work").innerHTML =
      tile("Open", open.length, `median for ${kind}: ${PM ? (peerMed(okind, "open") ?? "–") : med((p) => mine(p).filter((t) => t.action_status === "Open").length)}`, false) +
      tile("Claimed, not done", live.length - open.length, "taken in Slack, no meeting yet", false) +
      tile("Past the days to reach", old.length, `of ${live.length} on the plate (${REACH_SHORT})`, old.length > 0) +
      tile("Given to me by a manager", given.length, given.length ? `from ${[...new Set(given.map((t) => ownerName[t.assigned_from] || t.assigned_from))].join(", ")}` : "none", false) +
      tile("QBRs in the next 30 days", qbr30.length, `of ${plural(accountsOf(o).filter((a) => a.tier === "Enterprise").length, "Enterprise account")}`, false);
    shortList($("mb-work-list"), live, [
      { label: "Oldest first", get: (t) => nameOf(t.account_id) },
      { label: "Task", get: (t) => `${esc(t.flag)} · ${esc(t.status)}<small><br>${esc(t.action)}</small>` },
      { label: "Status", get: (t) => badge(t) },
      { label: "Age", r: true, get: (t) => `${t.age_days} d` },
    ], "No open or claimed tasks.");
    // the week ahead in one line, on a CSM's page (the full list is in My work)
    if (QUEUE && READY && $("mb-week")) {
      const w = weekAhead(o), warr = [...new Set(w.map((x) => x.a.account_id))].reduce((t, id) => t + ((byId[id] || {}).arr || 0), 0);
      $("mb-week").hidden = false;
      $("mb-week").innerHTML = (w.length ? `Next ${WEEK_DAYS} days: ${plural(w.length, "item")} on ${money(warr)} of ARR (${[...new Set(w.map((x) => x.what.startsWith("Reach by") ? "reach deadline" : x.what))].join(", ")}). `
        : `Nothing booked or due in the next ${WEEK_DAYS} days. `) + '<button class="link" id="mb-week-go">See My work</button>';
      $("mb-week-go").addEventListener("click", () => $("tab-tasks").click());
    }

    // 2. engagement
    const accs = accountsOf(o), ids = new Set(accs.map((a) => a.account_id));
    const share = (p) => { const as = accountsOf(p); return as.length ? as.filter((a) => met30(a.account_id)).length / as.length : null; };
    const m30 = accs.filter((a) => met30(a.account_id)).length;
    $("mb-eng-finding").textContent = `${name} met ${money(kMet)} of ${money(kArr)} ARR (${p100(kArr ? kMet / kArr : null)}) in the last 30 days, ${m30} of ${plural(accs.length, "account")}; the median for ${kind} is ${p100(medMetV)}.`;
    // each week: the ARR of the accounts met, each account once, under its most important kind
    // of meeting that week (QBR, then a call, then anything else): value, not a count of meetings
    const RANK = { qbr: 0, call: 1, other: 2 };
    const weeks = D.week_ends, counts = weeks.map((we) => {
      const best = {};
      for (const t of D.touches) if (t.event === "meeting_held" && ids.has(t.account_id) && day(t.date) > day(we) - 7 && day(t.date) <= day(we)) {
        const k = MEET_KIND(t.topic); if (!(t.account_id in best) || RANK[k] < RANK[best[t.account_id]]) best[t.account_id] = k; }
      const c = { qbr: 0, call: 0, other: 0 };
      for (const id in best) c[best[id]] += (byId[id] || {}).arr || 0;
      return c;
    });
    const start = (we) => new Date((day(we) - 6) * 864e5).toISOString().slice(0, 10);
    const meetingsIn = (we) => D.touches.filter((t) => t.event === "meeting_held" && ids.has(t.account_id) && day(t.date) > day(we) - 7 && day(t.date) <= day(we)).sort((a, b) => a.date.localeCompare(b.date));
    stackChart($("mb-eng-chart"), { W: 560, cats: MEET, unit: "meeting", fmtV: money, totals: isPhone() ? "last" : "all", aria: `ARR met per week for ${name}`,
      bars: weeks.map((we, i) => ({ label: fmt(start(we)), tip: `Week commencing ${fmt(start(we))}`, counts: counts[i],
        onClick: () => accountList($("mb-eng-list"), $("mb-eng-title"), `Week commencing ${fmt(start(we))}`, meetingsIn(we), MEETING_COLS, false, "meeting") })) });
    $("mb-eng-title").textContent = "Click a week to list its meetings"; $("mb-eng-list").innerHTML = "";
    stackLegend($("mb-eng-legend"), MEET);
    const quiet = accs.map((a) => ({ ...a, last: lastTouch(a.account_id) }))
      .filter((a) => !a.last || day(a.last.date) <= RUN - 31)
      .sort((a, b) => (a.last ? day(a.last.date) : 0) - (b.last ? day(b.last.date) : 0));
    const myCalls = D.time_to_action.filter((r) => ids.has(r.account_id) && r.call_date).map((r) => r.days_to_call);
    const teamCalls = (p) => median(D.time_to_action.filter((r) => r.routed_to === p && r.call_date).map((r) => r.days_to_call));
    const callsMed = PM ? (peerMed(okind, "calls") ?? "–") : (() => { const m = median(peers.map(teamCalls).filter((x) => x != null)); return m == null ? "–" : Math.round(m * 10) / 10; })();
    $("mb-eng-tiles").innerHTML =
      tile("Gone quiet", money(quiet.reduce((t, a) => t + (a.arr || 0), 0)), `of ARR: ${quiet.length} of ${plural(accs.length, "account")}`, quiet.length > 0) +
      tile("Days from signal to call", myCalls.length ? median(myCalls) : "–", `median for ${kind}: ${callsMed}`, false);
    shortList($("mb-quiet"), quiet, [
      { label: "Account", get: (a) => esc(a.account_name) },
      { label: "Last touch", get: (a) => a.last ? `${fmtFull(a.last.date)} <small>${esc(topicName(a.last.topic))}</small>` : '<span class="muted">none on record</span>' },
    ], "Every account had a touch in the last 30 days.");

    // 3. portfolio, risk first: churn, then growth, then the rest
    const fl = (a, f) => a.flags.find((x) => x.flag === f);
    const lastMeeting = (id, topics) => { const ms = (touchesBy[id] || []).filter((t) => t.event === "meeting_held" && (!topics || topics.includes(t.topic))); return ms.length ? ms[ms.length - 1] : null; };
    const CHURN_ORDER = { Act: 0, Shelfware: 1, Watch: 2 };
    // sorted by value at stake: the biggest ARR at risk first, the biggest upside first
    const byValue = (f) => (a, b) => (valueOf(fl(b, f)) || 0) - (valueOf(fl(a, f)) || 0) || byDate(a.renewal_date, b.renewal_date);
    const churn = accs.filter((a) => fl(a, "churn")).sort(byValue("churn"));
    // growth: an expansion signal, or a growth hook (25 Nov 2026); the flag the row shows: expansion first
    const gFlag = (a) => fl(a, "expansion") ? "expansion" : fl(a, "growth") ? "growth" : null;
    const gVal = (a) => valueOf(fl(a, gFlag(a))) || 0;
    const byGrowth = (a, b) => gVal(b) - gVal(a) || byDate(a.renewal_date, b.renewal_date);
    const growth = accs.filter((a) => !fl(a, "churn") && gFlag(a)).sort(byGrowth);
    const rest = accs.filter((a) => !fl(a, "churn") && !gFlag(a)).sort((a, b) => (b.arr || 0) - (a.arr || 0));
    const sumOf = (xs, f) => xs.reduce((t, x) => t + (f(x) || 0), 0);
    const atRisk = sumOf(churn, (a) => valueOf(fl(a, "churn"))), bookArr = sumOf(accs, (a) => a.arr);
    $("mb-port-finding").textContent = `${money(atRisk)} at risk sits on ${churn.length} of ${plural(accs.length, "account")}; the biggest first below.`;
    // the last thing CS did on the account, whatever it was, with its date and age
    // customer-facing actions only: a task claimed or reassigned is internal, not contact
    const CS_ACTION = { meeting_held: (t) => topicName(t.topic).replace(/^follow up$/, "follow-up call"), email_sent: (t) => `email: ${topicName(t.topic)}`,
      workshop_attended: (t) => `workshop: ${topicName(t.topic)}` };
    const lastActionCell = (id) => {
      const acts = (touchesBy[id] || []).filter((t) => CS_ACTION[t.event]);
      if (!acts.length) return '<span class="muted">nothing on record</span>';
      const t = acts[acts.length - 1], ago = Math.round(RUN - day(t.date));   // RUN = the demo's today
      return `${esc(CS_ACTION[t.event](t))}<small><br>${fmtFull(t.date)} · ${ago ? plural(ago, "day") + " ago" : "today"}</small>`;
    };
    const row = (a, f, topics) => {
      const flag = f ? fl(a, f) : null, m = f ? lastMeeting(a.account_id, topics) : null;
      const sev = flag ? (f === "churn" ? (flag.status === "Act" ? "critical" : "warning") : severity(flag)) : "";
      const gs = f === "growth" ? growthState(a) : null;
      const v = flag ? valueOf(flag) : null, fc = (forecasts[a.account_id] || []).slice(-1)[0], gap = fc && forecastGap(a, fc);
      const valueCell = `<b>${money(v != null ? v : a.arr)}</b><small><br>${v != null ? (f === "churn" || (flag && flag.status === "Unused add-on") ? "at risk" : "upside") + " of " + money(a.arr) : "ARR"}` +
        (fc ? `<br>${fc.role === "manager" ? "Manager" : "Owner"}: ${esc(callText(fc))}${gap ? ' <span class="bad">≠ data</span>' : ""}` : "") + "</small>";
      return `<tr class="click" tabindex="0" data-id="${esc(a.account_id)}"><td>${esc(a.account_name)}<small><br>${esc(a.tier)}</small></td><td class="num">${valueCell}</td><td>${spark(a.util)}</td>` +
        `<td>${fmtFull(a.renewal_date)}<small><br>in ${Math.round(day(a.renewal_date) - RUN)} d</small></td>` +
        `<td>${flag ? `<div class="flag ${sev}"><span class="pill">${esc(flag.status)}</span><p>${esc(flag.reasons)}</p>${gs ? `<p><small>Growth: ${esc(gs.text)}</small></p>` : ""}</div>` : '<span class="muted">no flags</span>'}${insightHtml(a.account_id, f)}</td>` +
        `<td>${lastActionCell(a.account_id)}${f && !m ? `<small class="muted"><br>no ${f === "churn" ? "churn" : "growth"} call or QBR yet</small>` : ""}</td>` +
        `<td>${(() => { const p = planOf(a); if (p) return planText(a, p) + (p.note ? `<small><br>${esc(p.note)}</small>` : "");
          return f ? `<span class="${flag && flag.status === "Act" ? "bad" : "muted"}">no next step recorded</span>` : ""; })()}</td></tr>`;
    };
    let restOpen = accs.length <= (D.settings.open_no_flags_up_to ?? 15);
    const restIns = rest.filter((a) => insightsOf(a.account_id).length).length;   // no flag, but the documents say something   // a small book: every account in sight
    // All | Churn | Growth (20 Nov 2026, Nikodem: the leader reports growth and churn apart)
    const PV = [["all", "All"], ["churn", "Churn"], ["growth", "Growth"]];
    const upAll = sumOf(growth, (a) => valueOf(fl(a, "expansion"))) + sumOf(churn, (a) => valueOf(fl(a, "expansion")));
    const gAll = accs.filter((a) => gFlag(a)), hookAccs = accs.filter((a) => openHook(fl(a, "growth")));
    const hookUp = sumOf(hookAccs.filter((a) => fl(a, "growth").status === "Add-on hook"), (a) => valueOf(fl(a, "growth")));
    const portText = { all: $("mb-port-finding").textContent,
      churn: $("mb-port-finding").textContent,
      growth: `${money(upAll)} of upside in ${name}'s book, across ${plural(accs.filter((a) => fl(a, "expansion")).length, "account")}` +
        (hookAccs.length ? `, and ${plural(hookAccs.length, "growth hook")} open (${money(hookUp)} in add-ons, list price)` : "") +
        `; growth stays with CS, Sales signs.` };
    const drawPortView = () => {
      $("mb-port-view").innerHTML = PV.map(([k, l]) => `<button class="chip${mbPortView === k ? " on" : ""}" aria-pressed="${mbPortView === k}" data-pv="${k}">${l}</button>`).join("");
      $("mb-port-view").querySelectorAll("[data-pv]").forEach((b) => b.addEventListener("click", () => { mbPortView = b.dataset.pv; drawPortView(); drawPort(); }));
      $("mb-port-finding").textContent = portText[mbPortView];
    };
    const drawPort = () => {
      $("mb-port").innerHTML = `<table><thead><tr><th>Account</th><th>Value</th><th>Utilisation, 90 days</th><th>Renewal</th><th>Why</th><th>Last action</th><th>Action planned</th></tr></thead><tbody>` +
        (mbPortView !== "growth" ? `<tr class="group"><td colspan="7">Churn risk (${churn.length})</td></tr>` + churn.map((a) => row(a, "churn", ["churn_call", "qbr"])).join("") : "") +
        // Growth view: every account with an expansion signal, including those also at churn risk
        (mbPortView !== "churn" ? `<tr class="group"><td colspan="7">Growth (${mbPortView === "growth" ? gAll.length : growth.length})</td></tr>` +
          (mbPortView === "growth" ? gAll.slice().sort(byGrowth) : growth).map((a) => row(a, gFlag(a), ["growth_call", "qbr"])).join("") : "") +
        (mbPortView === "all" ? `<tr class="group"><td colspan="7">No flags (${rest.length})${restIns ? ` · ${restIns} with an insight` : ""} <button class="link" data-rest>${restOpen ? "hide" : "show"}</button></td></tr>` + (restOpen ? rest.map((a) => row(a, null)).join("") : "") : "") +
        "</tbody></table>";
      wireInsights($("mb-port"));
      $("mb-port").querySelectorAll("tr.click").forEach((tr) => { tr.addEventListener("click", () => openAccount(tr.dataset.id)); tr.addEventListener("keydown", (e) => { if (e.key === "Enter") openAccount(tr.dataset.id); }); });
      const b = $("mb-port").querySelector("[data-rest]"); if (b) b.addEventListener("click", (e) => { e.stopPropagation(); restOpen = !restOpen; drawPort(); });
    };
    drawPortView();
    drawPort();

    // 4. pipeline: what's booked in the next 8 weeks, on top of today's plate
    const items = [];
    for (const a of accs) {
      if (a.next_qbr_date) items.push({ account_id: a.account_id, k: "qbr", date: a.next_qbr_date, what: "QBR" });
      items.push({ account_id: a.account_id, k: "renewal", date: a.renewal_date, what: "Renewal" });
    }
    // follow-ups go to people only on Scale (Self-service gets an email, Enterprise none)
    for (const t of live) if (PROGRAMME.has(t.status) && t.sent_on && (teamById[t.routed_to] || {}).owner_type === "pool") {
      const due = new Date((day(t.sent_on) + FOLLOW_DAYS) * 864e5).toISOString().slice(0, 10);
      items.push({ account_id: t.account_id, k: "followup", date: due, what: `Follow-up: ${t.status}` });
    }
    const wk = [...Array(8)].map((_, i) => ({ from: RUN + 7 * i, items: [] }));
    for (const it of items) { const w = Math.floor((day(it.date) - RUN) / 7); if (w >= 0 && w < 8) wk[w].items.push(it); }
    const total = wk.reduce((s, w) => s + w.items.length, 0);
    const watching = accs.filter((a) => a.flags.some((f) => f.status === "Watch")).length;
    $("mb-pipe-finding").textContent = `${name} has ${plural(total, "booked engagement")} in the next 8 weeks, on top of ${plural(live.length, "task")} on the plate today.`;
    const listWeek = (w) => {
      $("mb-pipe-title").textContent = `Week of ${fmt(new Date(w.from * 864e5).toISOString().slice(0, 10))}: ${plural(w.items.length, "engagement")}`;
      shortList($("mb-pipe-list"), [...w.items].sort((a, b) => a.date.localeCompare(b.date)), [
        { label: "Account", get: (it) => nameOf(it.account_id) }, { label: "What", get: (it) => esc(it.what) }, { label: "Date", r: true, get: (it) => fmt(it.date) },
      ], "Nothing booked this week.");
    };
    const wkStart = (w) => new Date(w.from * 864e5).toISOString().slice(0, 10);
    stackChart($("mb-pipe"), { W: 880, cats: PIPE, unit: "engagement", aria: `Booked engagements per week for ${name}`,
      bars: wk.map((w) => ({ label: fmt(wkStart(w)), tip: `Week commencing ${fmt(wkStart(w))}`, onClick: () => listWeek(w),
        counts: Object.fromEntries(PIPE.map((p) => [p.k, w.items.filter((it) => it.k === p.k).length])) })) });
    // Watch accounts are potential work, not bookings: named beside the chart, never in its totals
    stackLegend($("mb-pipe-legend"), PIPE, `Not in the chart: ${plural(watching, "account")} on Watch that may need a call.`);
    listWeek(wk.find((w) => w.items.length) || wk[0]);
  }

  // first paint: every view drawn, the one in the link shown
  drawPicks();
  const firstPick = picks.length ? picks[0].id : D.accounts[0].account_id;
  sel.value = firstPick; drawTimeline(firstPick);
  READY = true;
  redraw();
  const relayout = () => { redraw(); if (sel.value) drawTimeline(sel.value); };
  if (PHONE_MQ.addEventListener) PHONE_MQ.addEventListener("change", relayout); else if (PHONE_MQ.addListener) PHONE_MQ.addListener(relayout);
  // a link like ...#<account id> (from Slack's Open timeline) opens that account
