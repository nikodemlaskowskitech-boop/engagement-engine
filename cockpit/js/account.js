  // ---------------------------------------------------------------- timeline
  const TOUCH = {
    email_sent: { name: "Email sent", color: "var(--viz-touch)", shape: "circle-open" },
    email_opened: { name: "Email opened", color: "var(--viz-touch)", shape: "circle" },
    workshop_attended: { name: "Workshop attended", color: "var(--viz-touch)", shape: "square" },
    meeting_held: { name: "Meeting held", color: "var(--viz-other)", shape: "diamond" },   // coloured by type when drawn
  };
  const PROBLEM = new Set(["churn_call", "growth_call", "activation_call", "follow_up"]);
  const topicName = (t) => (t || "").replace(/_/g, " ");
  const drawShape = (g, shape, x, y, color, r = 5) => {
    if (shape === "circle" || shape === "circle-open")
      return el("circle", { cx: x, cy: y, r, fill: shape === "circle" ? color : "var(--surface)", stroke: color, "stroke-width": 2 }, g);
    if (shape === "square") return el("rect", { x: x - r + 0.5, y: y - r + 0.5, width: 2 * r - 1, height: 2 * r - 1, rx: 1.5, fill: color, stroke: "var(--surface)", "stroke-width": 1.5 }, g);
    return el("path", { d: `M${x} ${y - r - 1}L${x + r + 1} ${y}L${x} ${y + r + 1}L${x - r - 1} ${y}Z`, fill: color, stroke: "var(--surface)", "stroke-width": 1.5 }, g);
  };
  const legendSwatch = (shape, color) => {
    const s = document.createElementNS(NS, "svg"); s.setAttribute("viewBox", "0 0 12 12");
    drawShape(s, shape, 6, 6, color, 4); return s;
  };
  const touchesBy = {};
  for (const t of D.touches) (touchesBy[t.account_id] ||= []).push(t);

  const flagName = (f) => f.flag === "data" ? "data missing" : f.flag;   // the pill's word for a flag
  const severity = (f) => {
    if (f.flag === "churn") return f.status === "Act" ? "critical" : "warning";
    if (f.flag === "expansion") return f.status === "Act" ? "good" : "warning";
    if (f.flag === "journey") return "serious";
    // data missing (flags.py data_gaps): amber when it hides a risk we can still see, else information
    if (f.flag === "data") return f.status === "Risk hidden" ? "warning" : "neutral";
    if (f.flag === "activation") return ["Stalled", "Silent", "Handoff gap", "No CSM assigned"].includes(f.status) ? "warning" : "good";
    // growth hooks (flags.py growth_hooks): an unused add-on is a renewal risk; a hook is growth; held back, information
    if (f.flag === "growth") return f.status === "Unused add-on" ? "warning" : f.status.startsWith("Held") ? "neutral" : "good";
    return "neutral";
  };

  // Growth hooks (flags.py view growth, 25 Nov 2026): "no hook, no pitch". A hook is open unless
  // held back or marked not a fit; since = the first week of its current run (history.py), so
  // "addressed" means raised in a meeting since then, a next step planned since then, or not a fit.
  const GROWTH = D.settings.growth || { not_a_fit: {}, pause_days: 180 };
  const growthOf = (a) => (a.flags || []).find((x) => x.flag === "growth");
  const openHook = (f) => !!f && !f.status.startsWith("Held") && f.status !== "Not a fit";
  // the first week of the run a flag is in at week w (default: now); not flagged that week = that day
  function flagSince(id, flag, w = HIST.weeks.length - 1) {
    const on = new Set(HIST.flags.filter((r) => r[1] === id && r[2] === flag).map((r) => r[0]));
    if (!on.has(w)) return w === HIST.weeks.length - 1 ? D.as_of : HIST.weeks[w];
    while (w > 0 && on.has(w - 1)) w--;
    return HIST.weeks[w];
  }
  const growthSince = (id) => flagSince(id, "growth");
  // addressed between two dates (one rule for the account page and the leader's trend):
  // a meeting held, or a next step planned
  function addressedIn(id, since, end = D.as_of) {
    const met = (touchesBy[id] || []).filter((t) => t.event === "meeting_held" && t.date >= since && t.date <= end).slice(-1)[0];
    if (met) return { k: "raised", date: met.date };
    const plan = (nextSteps[id] || []).filter((p) => { const d = (p.decided_at || "").slice(0, 10); return d >= since && d <= end; }).slice(-1)[0];
    return plan ? { k: "planned", date: (plan.decided_at || "").slice(0, 10), due: plan.due } : null;
  }
  function growthState(a) {
    const f = growthOf(a); if (!f) return null;
    if (f.status === "Not a fit") return { k: "notfit", text: "not a fit", f };
    if (f.status.startsWith("Held")) return { k: "held", text: f.status.replace("Held: ", "held back: "), f };
    const since = growthSince(a.account_id), done = addressedIn(a.account_id, since, "9999");
    if (done && done.k === "raised") return { k: "raised", text: `raised ${fmt(done.date)}`, f, since };
    if (done) return { k: "planned", text: `planned${done.due ? ` for ${fmt(done.due)}` : ""}`, f, since };
    return { k: "open", text: f.routed_to === "none" ? "rides on the next engagement" : "open", f, since };
  }

  // accounts worth a look: the stories the reports surface
  const tta = Object.fromEntries(D.time_to_action.map((r) => [r.account_id, r]));
  // Picked from the segment, so with Mia selected the chips are Mia's accounts.
  let picks = [];
  function computePicks() {
    picks = [];
    const pool = D.accounts.filter(inSeg);
    const pickBy = (fn, why) => { const a = pool.find((x) => fn(x) && !picks.some((p) => p.id === x.account_id)); if (a) picks.push({ id: a.account_id, why }); };
    const churnF = (a) => a.flags.find((f) => f.flag === "churn");
    pickBy((a) => HANDOVER[a.account_id], "Handover brief");
    pickBy((a) => tta[a.account_id] && !tta[a.account_id].call_date && tta[a.account_id].flag === "churn", "Declining, never called");
    pickBy((a) => tta[a.account_id] && tta[a.account_id].days_to_call === 0 && tta[a.account_id].called_early && tta[a.account_id].flag === "churn", "Called before the signal");
    pickBy((a) => a.flags.some((f) => f.flag === "journey" && f.status === "No meeting in 30 days"), "Escalated to a manager");
    pickBy((a) => a.tier !== "Self-service" && a.flags.some((f) => f.flag === "churn" && f.status === "Shelfware"), "Shelfware, never ramped");
    pickBy((a) => a.flags.some((f) => f.flag === "expansion" && f.status === "Act") && a.flags.some((f) => f.flag === "qbr"), "Growing, QBR due");
    pickBy((a) => a.flags.some((f) => f.flag === "expansion" && f.status === "Act"), "Growing");
    pickBy((a) => churnF(a) && churnF(a).status === "Act", "At Act");
    pickBy((a) => a.util.some((u) => u != null) && !a.flags.length && a.tier !== "Self-service", "Healthy, no flags");
    picks = picks.slice(0, 6);
  }
  function drawPicks() {
    computePicks();
    const host = $("tl-picks"); host.innerHTML = "";
    for (const p of picks) {
      const b = document.createElement("button"); b.className = "chip"; b.id = "pick-" + p.id;
      b.textContent = `${byId[p.id].account_name}: ${p.why}`;
      b.addEventListener("click", () => { sel.value = p.id; drawTimeline(p.id); });
      host.appendChild(b);
    }
    if (sel.value) pressChips(sel.value);
  }

  const sel = $("tl-account");
  const sorted = [...D.accounts].sort((a, b) => a.account_name.localeCompare(b.account_name));
  for (const tier of ["Enterprise", "Scale", "Self-service"]) {
    const og = document.createElement("optgroup"); og.label = tier;
    for (const a of sorted.filter((x) => x.tier === tier)) {
      const o = document.createElement("option"); o.value = a.account_id;
      o.textContent = `${a.account_name} (${a.account_id})${a.flags.length ? " · " + a.flags.length + " flag" + (a.flags.length > 1 ? "s" : "") : ""}`;
      og.appendChild(o);
    }
    sel.appendChild(og);
  }
  sel.addEventListener("change", () => drawTimeline(sel.value));

  const TOUCH_ORDER = ["email_sent", "email_opened", "workshop_attended", "meeting_held"];
  for (const k of TOUCH_ORDER) {
    if (k === "meeting_held") {                       // one swatch per meeting type, as on My book
      for (const [col, nm] of [["var(--viz-qbr)", "QBR"], ["var(--viz-call)", "Call or follow-up"], ["var(--viz-other)", "Other meeting"]]) {
        const s = document.createElement("span"); s.append(legendSwatch("diamond", col), nm); $("tl-legend").appendChild(s);
      }
      continue;
    }
    const s = document.createElement("span"); s.append(legendSwatch(TOUCH[k].shape, TOUCH[k].color), TOUCH[k].name); $("tl-legend").appendChild(s);
  }
  { const s = document.createElement("span"); const sv = document.createElementNS(NS, "svg"); sv.setAttribute("viewBox", "0 0 12 12");
    el("line", { x1: 0, y1: 6, x2: 12, y2: 6, stroke: "var(--viz-measure)", "stroke-width": 2 }, sv); s.append(sv, "30-day utilisation"); $("tl-legend").appendChild(s);
    const s2 = document.createElement("span"); const sv2 = document.createElementNS(NS, "svg"); sv2.setAttribute("viewBox", "0 0 12 12");
    el("rect", { x: 2, y: 3, width: 8, height: 9, rx: 1, fill: "var(--viz-measure-soft)" }, sv2); s2.append(sv2, "Weekly active users"); $("tl-legend").appendChild(s2); }

  function drawTimeline(id) {
    const a = byId[id]; if (!a) return;
    sumAll = false;                                      // a new account starts with the short list
    pressChips(id);
    const host = $("tl-chart"); host.innerHTML = ""; host.classList.remove("fade"); void host.offsetWidth; host.classList.add("fade");
    const ph = isPhone(), W = fitW(720, 330), H = 330, L = ph ? 36 : 44, R = ph ? 6 : 16, top = 12;
    // the last 90 days, counted from the data's own date (it was a fixed 21 July from the
    // 90-day era: with a year of data, older points were drawn off the chart, 14 Nov 2026)
    const x1 = day(D.as_of) + 1, x0 = x1 - TL_DAYS;
    const inWin = (d) => day(d) >= x0;
    const X = (d) => L + ((d - x0) / (x1 - x0)) * (W - L - R);
    const uTop = top, uH = 150, wTop = 188, wH = 64, tY = 292;
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `Usage and CS touches for ${a.account_name}` }, host);

    // utilisation panel
    const uMax = Math.max(100, ...a.util.filter((u) => u != null)) ;
    const top100 = Math.ceil(uMax / 20) * 20;
    const UY = (u) => uTop + uH - (u / top100) * uH;
    for (let g = 0; g <= top100; g += 20) {
      el("line", { x1: L, x2: W - R, y1: UY(g), y2: UY(g), stroke: "var(--rule)", "stroke-width": 1 }, svg);
      el("text", { x: L - 6, y: UY(g) + 4, "text-anchor": "end", class: "val" }, svg).textContent = g + "%";
    }
    for (const [v, label] of [[40, ph ? "churn 40%" : "churn line 40%"], [80, ph ? "expansion 80%" : "expansion line 80%"]]) {
      el("line", { x1: L, x2: W - R, y1: UY(v), y2: UY(v), stroke: "var(--ink-3)", "stroke-width": 1, "stroke-dasharray": "4 4" }, svg);
      el("text", { x: L + 4, y: UY(v) - 4, "text-anchor": "start" }, svg).textContent = label;   // left: the line's last value is on the right
    }
    const pts = a.util.map((u, i) => (u == null || !inWin(D.util_dates[i]) ? null : [X(day(D.util_dates[i])), UY(u)])).filter(Boolean);
    const firstU = D.util_dates.find((d, i) => a.util[i] != null && inWin(d));
    if (pts.length) {
      el("path", { d: "M" + pts.map((p) => p.join(" ")).join("L"), fill: "none", stroke: "var(--viz-measure)", "stroke-width": 2, "stroke-linejoin": "round" }, svg);
      const last = pts[pts.length - 1], lastU = a.util.filter((u) => u != null).slice(-1)[0];
      el("circle", { cx: last[0], cy: last[1], r: 4, fill: "var(--viz-measure)", stroke: "var(--surface)", "stroke-width": 2 }, svg);
      el("text", { x: last[0] - 8, y: last[1] - 9, "text-anchor": "end", class: "val" }, svg).textContent = lastU + "%";
      // hover band for daily values
      const hit = el("rect", { x: X(day(firstU)), y: uTop, width: X(x1) - X(day(firstU)), height: uH, fill: "transparent" }, svg);
      const cross = el("line", { y1: uTop, y2: uTop + uH, stroke: "var(--ink-3)", "stroke-width": 1, visibility: "hidden" }, svg);
      hit.addEventListener("pointermove", (e) => {
        const r = svg.getBoundingClientRect(), sx = ((e.clientX - r.left) / r.width) * W;
        let best = 0, bd = 1e9;
        D.util_dates.forEach((d, i) => { const dd = Math.abs(X(day(d)) - sx); if (dd < bd && a.util[i] != null && inWin(d)) { bd = dd; best = i; } });
        cross.setAttribute("x1", X(day(D.util_dates[best]))); cross.setAttribute("x2", X(day(D.util_dates[best]))); cross.setAttribute("visibility", "visible");
        showTip(e, `<b>${fmt(D.util_dates[best])}</b><br>30-day utilisation <b>${a.util[best]}%</b>`);
      });
      hit.addEventListener("pointerleave", () => { cross.setAttribute("visibility", "hidden"); hideTip(); });
    } else {
      el("text", { x: (L + W - R) / 2, y: uTop + uH / 2, "text-anchor": "middle", class: "lbl" }, svg).textContent = "No usage yet";
    }
    // only when the line starts late (a newer account, or the data's first 30 days)
    if (pts.length && day(firstU) > x0 + 3) el("text", { x: X(x0) + 2, y: uTop + uH - 6 }, svg).textContent =
      (ph ? "no 30-day average before " : "before " ) + fmt(firstU) + (ph ? "" : ": no 30-day average yet");

    // weekly active users
    const wMax = Math.max(1, ...a.wau);
    const WY = (v) => wTop + wH - (v / wMax) * wH;
    el("text", { x: L - 6, y: wTop + 4, "text-anchor": "end", class: "val" }, svg).textContent = wMax;
    el("text", { x: L - 6, y: wTop + wH + 4, "text-anchor": "end", class: "val" }, svg).textContent = 0;
    el("line", { x1: L, x2: W - R, y1: wTop + wH, y2: wTop + wH, stroke: "var(--rule)" }, svg);
    D.week_ends.forEach((we, i) => {
      const xe = X(day(we) + 1), xs = Math.max(L, X(day(we) - 6));
      const v = a.wau[i], y = WY(v);
      if (v > 0) {
        const r = el("path", { d: `M${xs + 1} ${wTop + wH}V${y + 3}Q${xs + 1} ${y} ${xs + 4} ${y}H${xe - 4}Q${xe - 1} ${y} ${xe - 1} ${y + 3}V${wTop + wH}Z`, fill: "var(--viz-measure-soft)" }, svg);
        hover(r, `<b>Week ending ${fmt(we)}</b><br>${v} weekly active user${v === 1 ? "" : "s"}`);
      }
    });
    el("text", { x: L, y: wTop - 6, class: "lbl" }, svg).textContent = "Weekly active users";

    // touch strip
    el("line", { x1: L, x2: W - R, y1: tY, y2: tY, stroke: "var(--rule)" }, svg);
    el("text", { x: L, y: tY - 16, class: "lbl" }, svg).textContent = "CS touches";
    const rows = { email_sent: -7, email_opened: -7, workshop_attended: 0, meeting_held: 7 };
    for (const t of (touchesBy[id] || [])) {
      const k = TOUCH[t.event]; if (!k || !inWin(t.date)) continue;      // older touches: in the summary, not on the chart
      const cx = X(day(t.date) + 0.5), cy = tY + rows[t.event];
      const strong = t.event === "meeting_held" && PROBLEM.has(t.topic);
      const MCOL = { qbr: "var(--viz-qbr)", call: "var(--viz-call)", other: "var(--viz-other)" };   // the same as My book's charts
      const m = drawShape(svg, k.shape, cx, cy, t.event === "meeting_held" ? MCOL[MEET_KIND(t.topic)] : k.color, strong ? 6 : 5);
      const owner = ownerName[t.owner_id] || t.owner_id;
      const src = t.source === "actions.py" ? " · sent by the engine" : t.source === "slack" ? " · logged in Slack" : "";
      hover(m, `<b>${k.name}</b> · ${esc(topicName(t.topic))}<br>${fmt(t.date)} · ${esc(owner)}${src}`);
      const hitc = el("circle", { cx, cy, r: 10, fill: "transparent" }, svg);
      hover(hitc, `<b>${k.name}</b> · ${esc(topicName(t.topic))}<br>${fmt(t.date)} · ${esc(owner)}${src}`);
    }
    // contract start and renewal markers
    for (const [d, label] of [[a.contract_start, "signed"], [a.renewal_date, "renewal"]]) {
      if (!d) continue; const dd = day(d); if (dd < x0 || dd > x1) continue;
      el("line", { x1: X(dd), x2: X(dd), y1: uTop, y2: tY + 14, stroke: "var(--ink-2)", "stroke-width": 1, "stroke-dasharray": "2 3" }, svg);
      el("text", { x: X(dd) + 4, y: uTop + 10, class: "lbl" }, svg).textContent = label + " " + fmt(d);
    }
    // month ticks
    const months = [], s0 = new Date(x0 * 864e5);       // the first of each month inside the window
    for (let mo = s0.getUTCMonth() + 1; ; mo++) {        // Date.UTC rolls month 12 into January
      const m = new Date(Date.UTC(s0.getUTCFullYear(), mo, 1));
      if (m.getTime() / 864e5 >= x1) break;
      months.push(m.toISOString().slice(0, 10));
    }
    for (const m of months) {
      el("line", { x1: X(day(m)), x2: X(day(m)), y1: tY + 14, y2: tY + 19, stroke: "var(--ink-3)" }, svg);
      el("text", { x: X(day(m)), y: tY + 32, "text-anchor": "middle" }, svg).textContent = fmt(m);
    }

    // facts
    const contracted = (a.contracted_events / 1e6).toLocaleString("en-GB") + "M events a year";
    const facts = [["Tier", a.tier], ["Region", a.region], ["Owner", a.owner || (a.tier === "Self-service" ? "No CSM (Self-service)" : "Not assigned")],
      ["Contract", fmt(a.contract_start) + " " + yr4(a.contract_start) + " to " + fmt(a.renewal_date) + " " + yr4(a.renewal_date)],
      ["Commitment", contracted], ["ARR", money(a.arr) + (a.arr_estimated ? " (estimate from list price)" : "")], ["Add-ons", a.add_ons || "None"]];
    $("tl-facts").innerHTML = facts.map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join("");
    const t = tta[id];
    if (t) $("tl-facts").innerHTML += `<dt>Time to call</dt><dd>${t.call_date ? (t.called_early ? "Called before the signal" : t.days_to_call + " days after the signal") : '<span class="bad">No call since ' + fmt(t.signal_date) + "</span>"}</dd>`;
    // the account at a glance, above everything else
    const RANK = { critical: 4, serious: 3, warning: 2, good: 1 };
    drawWhy(a, RANK);
    const topFlag = a.flags.slice().sort((x, y) => (RANK[severity(y)] || 0) - (RANK[severity(x)] || 0))[0];
    const days = day(a.renewal_date) - (day(D.as_of) + 1);
    $("tl-head").innerHTML = `<h2>${esc(a.account_name)}</h2>` +
      `<span class="fact">${esc(a.tier)} · ${esc(a.region)}</span>` +
      `<span class="fact">Owner <b>${esc(a.owner || (a.tier === "Self-service" ? "none (Self-service)" : "not assigned"))}</b></span>` +
      `<span class="fact">ARR <b>${money(a.arr)}</b></span>` +
      (a.renewal_date ? `<span class="fact">Renews <b>${fmt(a.renewal_date)} ${yr4(a.renewal_date)}</b> (${days >= 0 ? "in " + days + " days" : days + " days ago"})</span>`
        : `<span class="fact">Renewal <b>date unknown</b> (not readable in the CRM)</span>`) +
      (topFlag ? `<span class="flag ${severity(topFlag)} inline"><span class="pill">${esc(flagName(topFlag))} · ${esc(topFlag.status)}</span></span>` : `<span class="fact">No open flags</span>`);
    const tc = (touchesBy[id] || []).filter((t) => day(t.date) > day(D.as_of) - TL_DAYS).length;   // the chart's window, as the sentence says
    drawNext(id);
    drawForecast(id);
    drawHandover(id);
    $("tl-find").placeholder = `${a.account_name} (${a.account_id}): search another account`;
    $("tl-finding").textContent = (segOn() && !inSeg(a) ? `Outside ${segName()}: ` : "") + `${a.account_name}: ${a.flags.length ? a.flags.length + " open flag" + (a.flags.length > 1 ? "s" : "") : "no open flags"}, ${tc} CS touch${tc === 1 ? "" : "es"} in ${TL_DAYS} days.`;
  }
  // Why now (21 Nov 2026, the CSM's route: "do I know this account?"): before the history, each
  // open flag in words with its money and what the rules say to do, then what the documents say
  // (an insight, never a status). A healthy account gets one calm line on its usage instead.
  function drawWhy(a, RANK = { critical: 4, serious: 3, warning: 2, good: 1 }) {
    const fl = a.flags.slice().sort((x, y) => (RANK[severity(y)] || 0) - (RANK[severity(x)] || 0));
    const ins = insightsOf(a.account_id, (fl[0] || {}).flag).slice(0, 2);
    const insHtml = ins.length ? `<div><span class="flag neutral inline"><span class="pill">Insight</span></span>` +
      ins.map((t) => `<p><b>${esc(t.label)}</b> · ${esc((KIND[t.kind] || t.kind).toLowerCase())} ${esc(t.id)}, ${fmt(t.date)}<br><small>"${esc(t.line)}"</small></p>`).join("") +
      `<p><small>From the documents: context to check, not a forecast. <button class="link" data-goto-research>See the research</button></small></p></div>` : "";
    let body;
    if (fl.length) {
      body = fl.map((f) => `<div><span class="flag ${severity(f)} inline"><span class="pill">${esc(flagName(f))} · ${esc(f.status)}</span></span>` +
        `<p>${esc(f.reasons)}</p>` +
        (valueOf(f) != null ? `<p><b>${money(valueOf(f))} ${f.flag === "churn" ? "ARR at risk" : f.status === "Unused add-on" ? "at risk at renewal" : "upside"}</b> · ${esc(f.value_basis)}</p>` : "") +
        (f.flag === "growth" && growthState(a) ? `<p><small>Growth: ${esc(growthState(a).text)}${growthState(a).since ? ` · hook seen since ${fmt(growthState(a).since)}` : ""}</small></p>` : "") +
        (f.action ? todoHtml(a, f) : "") + `</div>`).join("") + insHtml;
    } else {
      // last value against 30 days before, from the chart's own series
      const last = (xs) => { for (let i = xs.length - 1; i >= 0; i--) if (xs[i] != null) return [xs[i], i]; return [null, -1]; };
      const [u, ui] = last(a.util || []), [w, wi] = last(a.wau || []);
      const u30 = ui >= 0 ? (a.util || []).slice(0, Math.max(0, ui - 29)).filter((v) => v != null).slice(-1)[0] : null;
      const wAvg = (a.wau || []).filter((v) => v != null).slice(-13); const wMean = wAvg.length ? wAvg.reduce((x, y) => x + y, 0) / wAvg.length : null;
      const trend = (now, then) => (now == null || then == null ? "" : Math.abs(now - then) < 5 ? ", steady" : now > then ? ", rising" : ", falling");
      body = `<div><span class="flag good inline"><span class="pill">No open flags</span></span>` +
        `<p>${u != null ? `Utilisation ${Math.round(u)}%${trend(u, u30)}` : "No usage data yet"}${w != null ? `; ${Math.round(w)} weekly active users${wMean != null ? (Math.abs(w - wMean) < Math.max(1, wMean * 0.1) ? ", steady" : w > wMean ? ", above their average" : ", below their average") : ""}` : ""}.</p>` +
        `<p class="todo"><b>To do:</b> nothing flagged. Keep the cadence and set a renewal forecast before the renewal.</p></div>` + insHtml;
    }
    $("tl-why").innerHTML = `<h2>Why now</h2><div class="wn">${body}</div>`;
    wireDeclines(a);
    const r = $("tl-why").querySelector("[data-goto-research]");
    if (r) r.addEventListener("click", () => { const x = $("tl-research"); if (x && !x.hidden) x.scrollIntoView({ behavior: "smooth", block: "start" }); });
  }
  // A flag's to-do is the engine's recommendation (22 Nov 2026). The owner (or their team lead)
  // can mark it not needed, with a reason; it then leaves My work until the flag changes or a
  // snooze date passes. The leader sees declines by rule, which tells noise from neglect.
  function todoHtml(a, f) {
    const d = declinedOf(a.account_id, f.flag, f.status);
    if (d) return `<p class="todo declined"><b>To do:</b> <s>${esc(f.action)}</s><br><small>${f.flag === "growth" ? "Not a fit" : "Not needed"}: ${esc(GROWTH.not_a_fit[d.reason] || WORK.decline_reasons[d.reason] || d.reason)}` +
      `${d.reason === "later" && d.due ? ` until ${fmt(d.due)}` : ""}${d.note ? ` · ${esc(d.note)}` : ""} (${esc(ownerName[d.by] || managerName(d.by) || d.by)}, ${whenText(d.decided_at)})</small></p>`;
    const can = dbState === "ready" && forecastRole(a, "decline");
    // a growth hook already raised or planned: the to-do becomes closing the loop (25 Nov 2026)
    const gs = f.flag === "growth" ? growthState(a) : null;
    if (gs && (gs.k === "raised" || gs.k === "planned"))
      return `<p class="todo"><b>To do:</b> ${gs.k === "raised" ? `Raised at the meeting on ${esc(gs.text.replace("raised ", ""))}: what came of it? Plan the next step, or mark it not a fit.`
        : `Next step ${esc(gs.text)}: see it through, then say what came of it.`}` +
        (can ? ` <button class="link" data-decline="${esc(f.flag)}|${esc(f.status)}">Not a fit?</button>` : "") + `</p><div class="decline-form" hidden></div>`;
    return `<p class="todo"><b>To do:</b> ${esc(f.action)}` +
      (can && f.status !== "Not a fit" && !f.status.startsWith("Held") ? ` <button class="link" data-decline="${esc(f.flag)}|${esc(f.status)}">${f.flag === "growth" ? "Not a fit?" : "Not needed?"}</button>` : "") + `</p><div class="decline-form" hidden></div>`;
  }
  function wireDeclines(a) {
    $("tl-why").querySelectorAll("[data-decline]").forEach((b) => b.addEventListener("click", () => {
      const [flag, status] = b.dataset.decline.split("|"), form = b.closest("p").nextElementSibling;
      form.hidden = false; b.hidden = true;
      // a growth hook takes the "not a fit" reasons, and pauses for pause_days by itself (no snooze date)
      const REASONS = flag === "growth" ? GROWTH.not_a_fit : WORK.decline_reasons;
      form.innerHTML = `<div class="fc-form"><select class="dc-reason" aria-label="Why isn't it needed?">${Object.entries(REASONS).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("")}</select>` +
        `<input class="dc-due" type="date" min="${new Date((TODAY + 1) * 864e5).toISOString().slice(0, 10)}" aria-label="Snooze until" hidden>` +
        `<input class="dc-note" type="text" maxlength="200" placeholder="A note (needed for Other)" aria-label="Note">` +
        `<button class="small primary dc-save">Save</button><button class="small dc-cancel">Cancel</button></div><p class="note dc-msg" role="alert"></p>`;
      const q = (c) => form.querySelector(c);
      q(".dc-reason").addEventListener("change", () => { q(".dc-due").hidden = q(".dc-reason").value !== "later"; });
      q(".dc-cancel").addEventListener("click", () => { form.hidden = true; b.hidden = false; });
      q(".dc-save").addEventListener("click", async () => {
        const reason = q(".dc-reason").value, due = q(".dc-due").value, note = q(".dc-note").value.trim(), msg = (t) => { q(".dc-msg").textContent = t; };
        if (reason === "later" && (!due || day(due) <= TODAY)) return msg("Pick the date it should come back.");
        if (reason === "other" && !note) return msg("Say why in the note.");
        const w = forecastRole(a, "decline"); if (!w) return;
        q(".dc-save").disabled = true;
        const at = new Date().toISOString();
        try {
          await db.collection("decisions").doc(`decline~${a.account_id}~${at.replace(/[^0-9]/g, "")}`).set({
            kind: "decline", account_id: a.account_id, flag, status, reason, ...(reason === "later" ? { due } : {}), note,
            by: w.by, role: w.role, as_of: D.as_of, decided_at: at, decided_by: me });
        } catch (e) { q(".dc-save").disabled = false; msg("That wasn't saved. Try again in a moment."); }
      });
    }));
  }
  // Commitments: what we promised this customer, by when, and whether we kept it. The owner (or
  // team lead) adds one, tracks one found in the documents, and closes it; everyone else reads.
  function drawCommitments(id) {
    const a = byId[id], host = $("tl-commit"); if (!a || !host) return;
    const list = commitmentsOf(id), R = (D.research || {})[id] || {}, can = dbState === "ready" && forecastRole(a, "commitment");
    const tracked = new Set(list.filter((c) => c.doc).map((c) => `${c.doc}|${String(c.what).slice(0, 60)}`));
    const found = (R.promises || []).filter((p) => !tracked.has(`${p.id}|${p.line.slice(0, 60)}`));
    host.hidden = a.tier === "Self-service" && !list.length && !found.length;
    if (host.hidden) return;
    const st = list.map(comState), late = st.filter((x) => x.k === "late").length, open = st.filter((x) => ["open", "soon", "late"].includes(x.k)).length;
    const SRC = WORK.commitment_sources || {};
    let h = `<h2>Commitments: what we promised</h2><p class="finding small">${list.length
      ? `${plural(open, "open commitment")}${late ? `, ${late} late` : ""}; ${st.filter((x) => x.k === "kept").length} kept, ${st.filter((x) => x.k === "dropped").length} dropped.`
      : "No promise on record for this account."}</p>`;
    if (list.length) h += `<ul class="sum-list">` + list.map((c, i) => { const x = st[i];
      return `<li><span class="when"><span class="chip ${x.k}">${esc(x.t)}</span></span><span><b>${esc(c.what)}</b><br><small>by ${fmt(c.due)} · ${esc((SRC[c.source] || c.source || "").toLowerCase())}` +
        `${c.doc ? ` (${esc(c.doc)})` : ""} · ${esc(ownerName[c.by] || managerName(c.by))}${c.result ? `, closed ${whenText(c.closed_at)}` : ""}</small>` +
        (can && !c.result ? `<br><button class="small" data-close="${esc(c.key)}" data-result="kept">Kept</button> <button class="small" data-close="${esc(c.key)}" data-result="dropped">Dropped</button>` : "") + `</span></li>`; }).join("") + `</ul>`;
    if (can) h += `<div class="fc-form com-add"><input id="com-what" type="text" maxlength="200" placeholder="We committed to…" aria-label="What was promised">` +
      `<input id="com-due" type="date" aria-label="By"><select id="com-src" aria-label="Where">${Object.entries(SRC).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("")}</select>` +
      `<button class="small primary" id="com-save">Add</button></div><p class="note" id="com-msg" role="alert"></p>`;
    if (found.length) h += `<h3 class="mini">Possible promises in the documents</h3><ul class="sum-list">` + found.map((p, i) =>
      `<li><span class="when">${fmt(p.date)}<br><small>${esc((KIND[p.kind] || p.kind).toLowerCase())} ${esc(p.id)}</small></span><span>"${esc(p.line)}"` +
      (can ? `<br><button class="link" data-track="${i}">Track it</button>` : "") + `</span></li>`).join("") + `</ul>` +
      `<p class="note">Found by keywords in what our side wrote (an email we sent, our lines in a call): possible promises, not verdicts. Tracking one makes it count.</p>`;
    host.innerHTML = h;
    if (!can) return;
    const save = async (doc) => {
      const at = new Date().toISOString();
      await db.collection("decisions").doc(`${doc.kind}~${id}~${at.replace(/[^0-9]/g, "")}`).set({ ...doc, account_id: id,
        by: can.by, role: can.role, as_of: D.as_of, decided_at: at, decided_by: me });
    };
    host.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", async () => {
      b.disabled = true;
      try { await save({ kind: "commitment_close", key: b.dataset.close, result: b.dataset.result }); } catch (e) { b.disabled = false; }
    }));
    $("com-save").addEventListener("click", async () => {
      const what = $("com-what").value.trim(), due = $("com-due").value, msg = (t) => { $("com-msg").textContent = t; };
      if (!what) return msg("Say what was promised.");
      if (!due) return msg("Pick the date it's due.");
      $("com-save").disabled = true;
      try { await save({ kind: "commitment", what, due, source: $("com-src").value }); }
      catch (e) { $("com-save").disabled = false; msg("That wasn't saved. Try again in a moment."); }
    });
    host.querySelectorAll("[data-track]").forEach((b) => b.addEventListener("click", async () => {
      const p = found[Number(b.dataset.track)];
      // due: a fortnight after the document by default; the owner can close or re-add with another date
      const due = new Date((Math.max(day(p.date) + 14, TODAY)) * 864e5).toISOString().slice(0, 10);
      b.disabled = true;
      try { await save({ kind: "commitment", what: p.line.slice(0, 200), due, source: p.kind === "E" ? "email" : "call", doc: p.id }); }
      catch (e) { b.disabled = false; }
    }));
  }
  // Next step ("action planned"): what the owner will do next and by when.
  // Overdue when the date passes with no meeting since it was set. A QBR
  // booked in the CRM counts as the plan until the owner records one.
  const TODAY = day(D.as_of) + 1;                        // the pipeline's "today"
  const planStatus = (a, p) => {
    if (!p) return null;
    const left = Math.round(day(p.due) - TODAY);
    if (left >= 0) return { overdue: false, text: left === 0 ? "today" : `in ${left} d` };
    const met = (touchesBy[a.account_id] || []).some((t) => t.event === "meeting_held" && t.date >= (p.set_on || p.due));
    return met ? { overdue: false, text: "done (meeting held)" } : { overdue: true, text: `${-left} d overdue` };
  };
  const planText = (a, p) => { const st = planStatus(a, p);
    return p ? `${esc(p.type)}, ${fmt(p.due)}${st ? ` <span class="${st.overdue ? "bad" : "muted"}">${st.text}</span>` : ""}` +
      (p.intended ? `<small><br>Aim: ${esc(WORK.intended_outcomes[p.intended] || p.intended)}</small>` : "") : ""; };
  // Summary: everything done on the account, newest first (customer touches,
  // tasks sent, plans and forecasts), next to what's coming up.
  const DONE_TEXT = {
    meeting_held: (t) => [`Meeting (${topicName(t.topic)})`, ownerName[t.owner_id] || t.owner_id, true],
    email_sent: (t) => [`Email sent (${topicName(t.topic)})`, t.owner_id === "automated" ? "automated" : ownerName[t.owner_id] || t.owner_id, true],
    workshop_attended: (t) => [`Workshop attended (${topicName(t.topic)})`, "", true],
    email_opened: (t) => [`Customer opened: ${topicName(t.topic)}`, "", false],
    task_claimed: (t) => ["Task taken in Slack", ownerName[t.owner_id] || "", false],
    task_assigned: (t) => [`Task given to ${ownerName[t.owner_id] || t.owner_id}`, "", false],
    handback_approved: () => ["Hand-back to the pool approved", "", false],
  };
  // cockpit entries carry the real date, which is after the data ends
  const ago = (d) => { const n = Math.round(TODAY - day(d)); return n < 0 ? "in the cockpit" : n === 0 ? "today" : `${n} d ago`; };
  const ahead = (d) => { const n = Math.round(day(d) - TODAY); return n < 0 ? `${-n} d ago` : n === 0 ? "today" : `in ${n} d`; };
  let sumAll = false;
  function drawSummary(id) {
    const a = byId[id]; if (!a) return;
    const done = [];
    const same = {};                                     // one line per touch: three people at one workshop = one line, "3 people"
    for (const t of touchesBy[id] || []) {
      const f = DONE_TEXT[t.event]; if (!f) continue;
      const k = [t.date, t.event, t.topic, t.owner_id].join("|");
      if (same[k]) { same[k].n++; same[k].what = same[k].base + ` · ${same[k].n} people`; continue; }
      const [what, who, customer] = f(t);
      const OUT = (D.settings && D.settings.meeting_outcomes) || {};
      const said = t.event === "meeting_held" && (t.outcome || t.note)                 // what came of it, when logged
        ? [t.outcome ? (OUT[t.outcome] || t.outcome) : "", t.note || ""].filter(Boolean).join(" · ") : "";
      // logged in Slack (23 Nov 2026): a meeting's outcome comes only from Slack's call form, and a
      // click from Slack carries its source; the tag shows where the loop closes
      const slack = t.source === "slack" || (t.event === "meeting_held" && !!t.outcome);
      done.push(same[k] = { date: t.date, at: t.date, what, base: what, n: 1, who: [who, said].filter(Boolean).join(" · "), customer, slack });
    }
    for (const t of TASKS.filter((x) => x.account_id === id && x.sent_on))
      done.push({ date: t.sent_on, at: t.sent_on, what: `Task sent: ${t.flag} ${t.status}`, who: `${ownerName[t.routed_to] || t.routed_to} · now ${t.action_status}`, customer: false });
    for (const s of nextSteps[id] || []) done.push({ date: String(s.decided_at).slice(0, 10), at: String(s.decided_at), what: `Next step planned: ${STEP_TYPES[s.type] || s.type} by ${fmt(s.due)}`, who: s.role === "manager" ? managerName(s.by) + " (manager)" : ownerName[s.by] || s.by, customer: false });
    // the four kinds of work in the history too (22 Nov 2026): a to-do marked not needed, an aim checked
    for (const d of declines[id] || []) done.push({ date: String(d.decided_at).slice(0, 10), at: String(d.decided_at), what: `Marked not needed: the ${d.flag} ${d.status} to-do (${(WORK.decline_reasons[d.reason] || d.reason).toLowerCase()})`, who: ownerName[d.by] || managerName(d.by) + " (manager)", customer: false });
    for (const c of checks[id] || []) done.push({ date: String(c.decided_at).slice(0, 10), at: String(c.decided_at), what: `Aim checked: ${WORK.intended_outcomes[c.intended] || c.intended}: ${(WORK.outcome_results[c.result] || c.result).toLowerCase()}`, who: ownerName[c.by] || managerName(c.by) + " (manager)", customer: false });
    for (const f of forecasts[id] || []) done.push({ date: String(f.decided_at).slice(0, 10), at: String(f.decided_at), what: `Forecast: ${callText(f)}`, who: (f.role === "manager" ? managerName(f.by) + " (manager)" : ownerName[f.by] || f.by) + (f.reason ? ` · ${f.reason}` : ""), customer: false });
    done.sort((x, y) => y.at.localeCompare(x.at));       // full timestamps: two saves on one day stay in order
    const SHORT = 3;                                     // the history comes after why now and what to do: three rows, then View all
    const shown = sumAll ? done : done.slice(0, SHORT);
    $("tl-done").innerHTML = done.length ? shown.map((d) => `<li><span class="when">${fmt(d.date)}<br>${ago(d.date)}</span><span>${d.customer ? "<b>" + esc(d.what) + "</b>" : esc(d.what)}${d.slack ? ' <span class="badge Open" title="Logged from the task card in Slack">via Slack</span>' : ""}${d.who ? `<br><small>${esc(d.who)}</small>` : ""}</span></li>`).join("") +
      (done.length > SHORT ? `<li><span></span><button class="link" id="tl-done-all">${sumAll ? "Show fewer" : `View all (${done.length})`}</button></li>` : "")
      : '<li><span></span><span class="muted">Nothing on record in the last 90 days.</span></li>';
    const b = $("tl-done-all"); if (b) b.addEventListener("click", () => { sumAll = !sumAll; drawSummary(id); });
    // coming up: the plan, a booked QBR, the renewal and the forecast for it
    const p = planOf(a), fc = (forecasts[id] || []).slice(-1)[0], up = [];
    if (p) up.push({ date: p.due, what: `${p.kind === "step" ? "Next step" : "Next step (from the CRM)"}: ${p.type}`, who: p.note || "", overdue: (planStatus(a, p) || {}).overdue });
    if (a.next_qbr_date && a.next_qbr_date > D.as_of && !(p && p.kind === "crm")) up.push({ date: a.next_qbr_date, what: "QBR booked", who: "in the CRM" });
    up.push({ date: a.renewal_date, what: `Renewal: ${money(a.arr)}`, who: fc ? `Forecast: ${callText(fc)}` : dataRisk(a) ? `Data: ${money(dataRisk(a))} at risk` : "No forecast yet" });
    up.sort((x, y) => byDate(x.date, y.date));
    $("tl-coming").innerHTML = up.map((u) => `<li><span class="when">${fmt(u.date)}<br><span class="${u.overdue ? "bad" : ""}">${u.overdue ? (planStatus(a, p) || {}).text : u.date ? ahead(u.date) : "fix it in the CRM"}</span></span><span><b>${esc(u.what)}</b>${u.who ? `<br><small>${esc(u.who)}</small>` : ""}</span></li>`).join("");
    const lastC = done.find((d) => d.customer);
    const noPlan = !p && a.tier !== "Self-service";
    // red only when a missing plan matters: an open flag, a renewal within 90 days, or 60+ days
    // without a customer action; otherwise a plain note (21 Nov 2026: a healthy account read as an alarm)
    const urgent = a.flags.length || day(a.renewal_date) - TODAY <= 90 || !lastC || TODAY - day(lastC.date) >= 60;
    $("tl-sum-line").innerHTML = `${lastC ? `Last customer action: ${esc(lastC.what.replace(/^./, (c) => c.toLowerCase()))}, ${ago(lastC.date)}.` : "No customer action in 90 days."} ` +
      (p ? `Next: ${esc(p.type.toLowerCase())} on ${fmt(p.due)} (${(planStatus(a, p) || {}).text}).` : noPlan ? (urgent ? '<span class="bad">No next step planned.</span>' : '<span class="muted">No next step planned yet.</span>') : "");
  }

  // Who may plan or forecast is read from the segment bar (in a real tool:
  // the sign-in). One click sets it: the account's owner (a named CSM, or a
  // pool = its team + Scale) or the owner's manager.
  function actAsHtml(a, what) {
    const o = teamById[a.owner_id];
    if (!o) return `<p class="note">No owner assigned yet: CS ops assigns one before anyone can ${what}.</p>`;
    // a person's page: no acting as someone else; say who can
    if (PAGE) return `<p class="note">Read only: ${esc(o.owner_name)}${o.owner_type === "pool" ? " (pool)" : ""} can ${what} here${o.manager ? `, or ${esc(managerName(o.manager))} (their manager)` : ""}.</p>`;
    return `<p class="note">To ${what}, act as:</p><div class="fc-form">` +
      `<button class="small" data-actas="owner" data-acc="${esc(a.account_id)}">${esc(o.owner_name)}${o.owner_type === "pool" ? " (pool)" : ""}</button>` +
      (o.manager ? `<button class="small" data-actas="manager" data-acc="${esc(a.account_id)}">${esc(managerName(o.manager))} (manager)</button>` : "") +
      `</div>`;
  }
  function actAs(kind, id) {
    const o = teamById[byId[id].owner_id];
    for (const k in seg) seg[k] = "";
    if (kind === "manager") seg.manager = o.manager;
    else if (o.owner_type === "pool") { seg.team = o.region; seg.tier = "Scale"; }   // a pool's book
    else seg.owner = o.owner_id;
    saveSeg(); redraw();
  }
  for (const host of ["tl-next", "tl-forecast"])
    $(host).addEventListener("click", (e) => { const b = e.target.closest("[data-actas]"); if (b) actAs(b.dataset.actas, b.dataset.acc); });

  function drawNext(id) {
    const a = byId[id], host = $("tl-next"); if (!a) return;
    drawSummary(id);                                     // the summary reads the same plan
    drawCommitments(id);                                 // and the promises beside it
    if (a.tier === "Self-service") { host.innerHTML = ""; return; }
    const hist = nextSteps[id] || [], p = planOf(a);
    let h = `<h3>Action planned</h3>`;
    h += p ? `<p><b>${planText(a, p)}</b>${p.kind === "step" ? ` · ${p.role === "manager" ? esc(managerName(p.by)) + " (manager)" : esc(ownerName[p.by] || p.by)}` : ""}${p.note ? `<br><small>${esc(p.note)}</small>` : ""}</p>`
      : `<p class="${a.flags.some((f) => f.flag === "churn" && f.status === "Act") ? "bad" : "muted"}">No next step recorded.</p>`;
    // did it achieve its aim? (22 Nov 2026): asked of the owner once the date has come, answered once;
    // the team lead sees the answer, nothing to approve
    if (p && p.kind === "step" && p.intended) {
      const done = (checks[id] || []).filter((c) => String(c.decided_at) > String(p.decided_at)).slice(-1)[0];
      const aim = esc(WORK.intended_outcomes[p.intended] || p.intended);
      const whoCheck = forecastRole(a, "outcome_check");
      if (done) h += `<p class="aim-done">Did it achieve its aim? <b>${esc(WORK.outcome_results[done.result] || done.result)}</b> <small>(${whenText(done.decided_at)})${done.note ? " · " + esc(done.note) : ""}</small></p>`;
      else if (day(p.due) <= TODAY && whoCheck && dbState === "ready")
        h += `<div class="aim-ask"><p>Did it achieve its aim: <b>${aim}</b>?</p><div class="fc-form">` +
          Object.entries(WORK.outcome_results).map(([k, v]) => `<button class="small" data-aim="${k}">${esc(v)}</button>`).join("") +
          `<input id="aim-note" type="text" maxlength="200" placeholder="What happened (optional)" aria-label="What happened"></div><p class="note" id="aim-msg" role="alert"></p></div>`;
      else if (day(p.due) <= TODAY) h += `<p class="note">Aim: ${aim}, not checked yet.</p>`;
    }
    const who = forecastRole(a, "next_step");
    if (dbState !== "ready") h += `<p class="note">Next steps are recorded in the published cockpit.</p>`;
    else if (!who) h += actAsHtml(a, "plan a next step");
    else h += `<div class="fc-form"><select id="ns-type" aria-label="Next step">${Object.entries(STEP_TYPES).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select>` +
      `<input id="ns-due" type="date" min="${new Date(TODAY * 864e5).toISOString().slice(0, 10)}" aria-label="Due date">` +
      `<select id="ns-aim" aria-label="What should this achieve?"><option value="">What should this achieve? (optional)</option>${Object.entries(WORK.intended_outcomes).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("")}</select>` +
      `<input id="ns-note" type="text" maxlength="200" placeholder="What and with whom (optional)" aria-label="Note">` +
      `<button class="small primary" id="ns-save">Plan it</button></div><p class="note" id="ns-msg" role="alert"></p>`;
    if (hist.length > 1) h += `<ul class="fc-hist">` + hist.slice(0, -1).reverse().slice(0, 3).map((s) =>
      `<li>${whenText(s.decided_at)} · ${esc(STEP_TYPES[s.type] || s.type)}, ${fmt(s.due)}${s.note ? " · " + esc(s.note) : ""}</li>`).join("") + "</ul>";
    host.innerHTML = h;
    host.querySelectorAll("[data-aim]").forEach((b) => b.addEventListener("click", async () => {
      const w = forecastRole(a, "outcome_check"); if (!w) return;
      host.querySelectorAll("[data-aim]").forEach((x) => { x.disabled = true; });
      const at = new Date().toISOString();
      try {
        await db.collection("decisions").doc(`outcome_check~${id}~${at.replace(/[^0-9]/g, "")}`).set({
          kind: "outcome_check", account_id: id, intended: p.intended, result: b.dataset.aim, note: $("aim-note").value.trim(),
          by: w.by, role: w.role, as_of: D.as_of, decided_at: at, decided_by: me });
      } catch (e) {
        host.querySelectorAll("[data-aim]").forEach((x) => { x.disabled = false; });
        $("aim-msg").textContent = "That answer wasn't saved. Try again in a moment.";
      }
    }));
    if (!who || dbState !== "ready") return;
    $("ns-save").addEventListener("click", async () => {
      const due = $("ns-due").value, msg = (t) => { $("ns-msg").textContent = t; };
      if (!due || day(due) < TODAY) return msg(`Pick a date from ${fmt(new Date(TODAY * 864e5).toISOString().slice(0, 10))} on.`);
      $("ns-save").disabled = true;
      const at = new Date().toISOString();
      try {
        await db.collection("decisions").doc(`next_step~${id}~${at.replace(/[^0-9]/g, "")}`).set({
          kind: "next_step", account_id: id, type: $("ns-type").value, due, note: $("ns-note").value.trim(),
          ...($("ns-aim").value ? { intended: $("ns-aim").value } : {}),
          by: who.by, role: who.role, as_of: D.as_of, decided_at: at, decided_by: me });
      } catch (e) {
        $("ns-save").disabled = false;
        msg(e && e.code === "invalid_argument" ? "You can see this cockpit but not change it: ask its owner for Contributor access." : "That next step wasn't saved. Try again in a moment.");
      }
    });
  }

  // Renewal forecast: the data's number, then the owner's (or manager's) call
  // with a reason, and its history. Like a CRM forecast field; the gap between
  // the person and the data is worth a conversation.
  const whenText = (s) => { const d = String(s || "").slice(0, 10); return d.length === 10 ? fmt(d) : ""; };
  const forecastRole = (a, action = "forecast") => {
    if (PAGE) {                                              // a person's page: their rights, nothing else
      if (!mayEdit(action, a.account_id)) return null;
      return { by: PAGE.person, role: PAGE.role === "manager" ? "manager" : "owner" };
    }
    const o = segOwner();
    if (o && (o === a.owner_id || (a.pool && o === a.pool))) return { by: o, role: "owner" };   // the owner, or their pool
    if (seg.manager && seg.manager === a.manager) return { by: seg.manager, role: "manager" };
    return null;
  };
  function drawForecast(id) {
    const a = byId[id], host = $("tl-forecast"); if (!a) return;
    if (a.tier === "Self-service") { host.innerHTML = ""; return; }   // no owner to forecast it
    const ch = a.flags.find((f) => f.flag === "churn"), ex = a.flags.find((f) => f.flag === "expansion");
    const hist = forecasts[id] || [], last = hist[hist.length - 1];
    let h = `<h3>Renewal forecast</h3><p>Data: ${ch ? `<b>${money(valueOf(ch))} at risk</b> of ${money(a.arr)} <small>(${esc(ch.value_basis || "")})</small>` : `no churn risk, ${money(a.arr)} renewing`}` +
      (ex && valueOf(ex) ? ` · ${money(valueOf(ex))} upside` : "") + "</p>";
    if (last) {
      h += `<p><b>${esc(callText(last))}</b> · ${last.role === "manager" ? esc(managerName(last.by)) + " (manager override)" : esc(ownerName[last.by] || last.by)}, ${whenText(last.decided_at)}<br><small>${esc(last.reason || "")}</small></p>`;
      const g = forecastGap(a, last);
      if (g) h += `<p class="gap">${g.mine > g.data ? "The person sees more risk than the data" : "The data sees more risk than the person"}: ${money(g.mine)} expected lost vs. ${money(g.data)} at risk.</p>`;
    } else h += `<p class="muted">No forecast from the owner yet.</p>`;
    const who = forecastRole(a);
    if (dbState !== "ready") h += `<p class="note">Forecasts are recorded in the published cockpit.</p>`;
    else if (!who) h += actAsHtml(a, "record a forecast");
    else h += `<div class="fc-form"><select id="fc-call" aria-label="Forecast">${Object.entries(CALLS).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select>` +
      `<label id="fc-amt" hidden>to ${esc(CUR.trim())} <input id="fc-amount" type="number" min="0" step="1000" inputmode="numeric"></label>` +
      `<input id="fc-reason" type="text" maxlength="200" placeholder="Reason (required)" aria-label="Reason">` +
      `<button class="small primary" id="fc-save">Save${who.role === "manager" ? " as manager" : ""}</button></div><p class="note" id="fc-msg" role="alert"></p>`;
    if (hist.length > 1) h += `<ul class="fc-hist">` + hist.slice(0, -1).reverse().slice(0, 4).map((f) =>
      `<li>${whenText(f.decided_at)} · ${esc(f.role === "manager" ? managerName(f.by) + " (manager)" : ownerName[f.by] || f.by)}: ${esc(callText(f))}${f.reason ? " · " + esc(f.reason) : ""}</li>`).join("") +
      (hist.length > 5 ? `<li>and ${hist.length - 5} earlier</li>` : "") + "</ul>";
    host.innerHTML = h;
    if (!who || dbState !== "ready") return;
    const call = $("fc-call"), amt = $("fc-amount");
    call.addEventListener("change", () => { $("fc-amt").hidden = call.value !== "downsize"; });
    $("fc-save").addEventListener("click", async () => {
      const reason = $("fc-reason").value.trim(), amount = call.value === "downsize" ? Number(amt.value) : null;
      const msg = (t) => { $("fc-msg").textContent = t; };
      if (!reason) return msg("Add a reason: it's what a manager reads in the forecast call.");
      if (call.value === "downsize" && !(amount > 0 && amount < a.arr)) return msg(`A downsize is between 0 and today's ARR (${money(a.arr)}).`);
      $("fc-save").disabled = true;
      const at = new Date().toISOString();
      try {
        await db.collection("decisions").doc(`forecast~${id}~${at.replace(/[^0-9]/g, "")}`).set({
          kind: "forecast", account_id: id, call: call.value, amount, reason, by: who.by, role: who.role,
          data_at_risk: dataRisk(a), as_of: D.as_of, decided_at: at, decided_by: me });
      } catch (e) {
        $("fc-save").disabled = false;
        msg(e && e.code === "invalid_argument" ? "You can see this cockpit but not change it: ask its owner for Contributor access." : "That forecast wasn't saved. Try again in a moment.");
      }
    });
  }
  // Handover brief: what the AI wrote for the new owner, from emails, tickets and
  // calls. A draft until the outgoing owner (or their manager) confirms it; a
  // rewrite after that makes it a draft again. Every citation opens the source
  // as the model saw it (redacted), so a claim can be checked in one click.
  function briefHtml(md, ids) {
    const inline = (t) => esc(t).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
      .replace(/\[((?:[A-Z]+\d*)(?:,\s*[A-Z]+\d*)*)\]/g, (m, g) => g.split(/,\s*/).map((c) =>
        ids.has(c) ? `<button class="cite" data-src="${c}" title="Show source ${c}">${c}</button>` : `<span class="bad">[${c}?]</span>`).join(""));
    let h = "", list = "";
    const close = () => { if (list) { h += `</${list}>`; list = ""; } };
    for (const line of md.split("\n")) {
      const t = line.trim(); let m;
      if (!t) { close(); continue; }
      if ((m = t.match(/^#{1,4}\s+(.*)/))) { close(); h += `<h4>${inline(m[1])}</h4>`; }
      else if ((m = t.match(/^[-*]\s+(.*)/))) { if (list !== "ul") { close(); h += "<ul>"; list = "ul"; } h += `<li>${inline(m[1])}</li>`; }
      else if ((m = t.match(/^\d+\.\s+(.*)/))) { if (list !== "ol") { close(); h += "<ol>"; list = "ol"; } h += `<li>${inline(m[1])}</li>`; }
      else { close(); h += `<p>${inline(t)}</p>`; }
    }
    close(); return h;
  }
  // Research (20 Nov 2026, Nikodem: tickets belong to research, summarised; most don't matter
  // to account management): what the documents say, from research.py over the redacted
  // documents only. Possible themes with their evidence line and source, for a person to judge.
  const KIND = { S: "Ticket", E: "Email", C: "Call note" };
  // Insights beside "Why" (20 Nov 2026, Nikodem: "we treat them as insights rather than forecasts"):
  // the account's first possible theme from its documents, growth themes first on a growth row,
  // risk themes first otherwise. Context for the owner: it never changes a status or a value.
  function insightsOf(id, side) {
    const R = (D.research || {})[id]; if (!R) return [];
    const g = new Set((D.settings && D.settings.research_growth_themes) || []), up = (t) => (g.has(t.theme) ? 1 : 0);
    return R.themes.slice().sort((x, y) => (side === "expansion" ? up(y) - up(x) : up(x) - up(y)));
  }
  function insightHtml(id, side) {
    const ts = READY ? insightsOf(id, side) : []; if (!ts.length) return "";
    const t = ts[0], more = ts.length - 1;
    return `<button class="link insight" data-insight="${esc(id)}" title="${esc(`${KIND[t.kind] || t.kind} ${t.id}: ${t.line}`)}">` +
      `<span class="pill">Insight</span>${esc(t.label)}<small> · ${esc((KIND[t.kind] || t.kind).toLowerCase())}, ${fmt(t.date)}${more ? ` · +${more} more` : ""}</small></button>`;
  }
  // an insight opens the account at its research
  function wireInsights(host) {
    host.querySelectorAll("[data-insight]").forEach((b) => b.addEventListener("click", (e) => {
      e.stopPropagation(); openAccount(b.dataset.insight);
      const r = $("tl-research"); if (r && !r.hidden) r.scrollIntoView({ block: "start" });
    }));
  }
  function drawResearch(id) {
    const host = $("tl-research");
    if (!host || !id || !READY) { if (host) host.hidden = true; return; }
    const R = (D.research || {})[id], days = (D.settings && D.settings.research_days) || 180;
    host.hidden = false;
    let x = `<h2>Research: what the documents say</h2>`;
    if (!R) {
      // nothing on record: one line, no method note (21 Nov 2026: an empty box at full size)
      host.innerHTML = x + `<p class="note">No tickets, emails or call notes in the last ${days} days. In the demo, documents exist for a few dozen accounts only.</p>`;
      return;
    } else {
      x += `<p class="finding small">${plural(R.tickets, "ticket")}${R.tickets ? ` (${R.open_tickets} open)` : ""}, ${plural(R.emails, "email")} and ${plural(R.calls, "call note")} in the last ${days} days` +
        (R.themes.length ? `; ${plural(R.themes.length, "possible theme")} worth a look.` : "; nothing in them matches the themes the engine looks for.") + `</p>`;
      if (R.themes.length) x += `<ul class="research">` + R.themes.map((t) =>
        `<li><b>${esc(t.label)}</b> <small>${esc(KIND[t.kind] || t.kind)} ${esc(t.id)}, ${fmtFull(t.date)}</small><br><q>${esc(t.line)}</q></li>`).join("") + `</ul>`;
    }
    x += `<p class="note">Possible themes from a keyword search over the redacted documents (people and companies replaced: Customer A, CS 1), each with the line that matched: check the source before acting.` +
      (PAGE && PAGE.sandbox ? ` <a href="search.html">Ask the documents</a>.` : "") + `</p>`;
    host.innerHTML = x;
  }
  function drawHandover(id) {
    drawResearch(id);                                   // the research sits under the handover brief
    const host = $("tl-handover"), h = HANDOVER[id], a = byId[id];
    if (!h || !a) { host.hidden = true; host.innerHTML = ""; return; }
    host.hidden = false;
    const ids = new Set(h.sources.map((x) => x.id));
    const conf = (handoverConfirms[id] || []).filter((c) => c.by === h.from || (c.role === "manager" && c.by === a.manager)).slice(-1)[0];
    const current = conf && conf.version === h.version;          // confirmed this very version
    const who = (() => { const r = forecastRole(a, "handover_confirm"); return r && (r.role === "manager" || r.by === h.from) ? r : null; })();
    const status = current ? `<span class="badge Done">Confirmed</span>`
      : conf ? `<span class="badge Draft">Draft: rewritten since it was confirmed</span>` : `<span class="badge Draft">Draft</span>`;
    let x = `<div class="ho-head"><h2>Handover brief: ${esc(ownerName[h.from] || h.from)} → ${esc(ownerName[h.to] || h.to)}</h2>${status}</div>`;
    x += `<p class="note">Written by AI from ${h.sources.length - 1} documents (support cases, emails, call transcripts) and the engine's data` +
      (h.written_as_of ? `, on data as of ${fmt(h.written_as_of)}` : "") + `. Click a source tag to see what the model read.` +
      (h.why && h.why.length ? ` Last written because: ${esc(h.why.join("; "))}.` : "") + `</p>`;
    // out of date: the engine's free daily check (handover.py --dry-run) saw the context move on.
    // Briefs are rewritten only when the admin asks (request_briefs.py), never by a click here.
    if (h.stale && h.stale.length) x += `<div class="ho-check ho-stale"><b>Out of date</b>: ` +
      `${/^it was written before/.test(h.stale[0]) ? "" : "since it was written, "}${esc(h.stale.join("; "))}. Briefs are rewritten only when the admin asks for it.</div>`;
    if (conf) x += `<p class="note">${current ? "Confirmed" : "Last confirmed"} by ${esc(conf.role === "manager" ? managerName(conf.by) + " (manager)" : ownerName[conf.by] || conf.by)}, ${whenText(conf.decided_at)}${conf.note ? ` · “${esc(conf.note)}”` : ""}</p>`;
    // the brief itself is long: folded until asked for (the checks stay in sight)
    x += `<details class="ho-fold" id="ho-fold"><summary>Read the brief</summary><div class="ho-brief">${briefHtml(h.brief, ids)}</div></details>`;
    x += h.unverified.length ? `<div class="ho-check"><b>Check before confirming</b><ul>${h.unverified.map((u) => `<li>${esc(u)}</li>`).join("")}</ul></div>`
      : `<p class="note">Every sentence cites a source and every number traces back to one.</p>`;
    // the confirm: the outgoing owner, or their manager
    x += `<div class="forecast">`;
    if (current) x += `<p class="muted">This version is confirmed. A rewrite (new emails, tickets or calls, a flag change) makes it a draft again.</p>`;
    else if (dbState !== "ready") x += `<p class="note">The outgoing owner confirms the brief in the published cockpit.</p>`;
    else if (!who) x += actAsHtml(a, "confirm the brief");
    else x += `<div class="fc-form"><input id="ho-note" type="text" maxlength="200" placeholder="A note for ${esc(ownerName[h.to] || "the new owner")} (optional)" aria-label="Note for the new owner">` +
      `<button class="small primary" id="ho-confirm">Confirm${who.role === "manager" ? " as manager" : ""}</button></div><p class="note" id="ho-msg" role="alert"></p>`;
    x += `</div>`;
    // what the model saw: redacted, source by source
    const parts = h.sent.split(/\n\s*---\s*\n/).map((p) => { const m = p.trim().match(/^\[([A-Z]+\d*)\]\s*([^\n]*)\n?([\s\S]*)$/); return m ? { id: m[1], title: m[2], text: m[3] } : null; }).filter(Boolean);
    x += `<details class="ho-sent" id="ho-sent"><summary>What the model saw (names, emails and phone numbers already replaced)</summary>` +
      parts.map((p) => `<div class="ho-src" id="ho-src-${p.id}"><b>[${p.id}]</b> ${esc(p.title)}<pre>${esc(p.text.replace(/\n\s*\n(\s*\n)+/g, "\n\n"))}</pre></div>`).join("") + `</details>`;
    host.innerHTML = x;
    if (!who || current || dbState !== "ready") return;
    $("ho-confirm").addEventListener("click", async () => {
      $("ho-confirm").disabled = true;
      const at = new Date().toISOString();
      try {
        await db.collection("decisions").doc(`handover_confirm~${id}~${at.replace(/[^0-9]/g, "")}`).set({
          kind: "handover_confirm", account_id: id, version: h.version, from: h.from, to: h.to,
          note: $("ho-note").value.trim(), by: who.by, role: who.role, as_of: D.as_of, decided_at: at, decided_by: me });
      } catch (e) {
        $("ho-confirm").disabled = false;
        $("ho-msg").textContent = e && e.code === "invalid_argument" ? "You can see this cockpit but not change it: ask its owner for Contributor access." : "The confirm wasn't saved. Try again in a moment.";
      }
    });
  }
  $("tl-handover").addEventListener("click", (e) => {
    const act = e.target.closest("[data-actas]"); if (act) return actAs(act.dataset.actas, act.dataset.acc);
    const c = e.target.closest(".cite"); if (!c) return;
    const box = $("ho-src-" + c.dataset.src); if (!box) return;
    $("ho-sent").open = true;
    document.querySelectorAll(".ho-src.hit").forEach((b) => b.classList.remove("hit"));
    box.classList.add("hit"); box.scrollIntoView({ block: "center", behavior: "smooth" });
  });

  function pressChips(id) { document.querySelectorAll("#tl-picks .chip").forEach((c) => c.setAttribute("aria-pressed", String(c.id === "pick-" + id))); }

