  // ---------------------------------------------------------------- team
  function teamRows(TTA) {
   return D.team.filter((t) => TTA.some((r) => r.routed_to === t.owner_id)).map((o) => {
    const rs = TTA.filter((r) => r.routed_to === o.owner_id);
    const called = rs.filter((r) => r.call_date);
    return { o, rs, n: rs.length, called: called.length, med: median(called.map((r) => r.days_to_call)), early: rs.filter((r) => r.called_early).length, esc: rs.filter((r) => r.escalated).length };
   }).sort((a, b) => (a.o.owner_type === b.o.owner_type ? (a.med ?? 99) - (b.med ?? 99) || a.o.owner_name.localeCompare(b.o.owner_name) : a.o.owner_type === "csm" ? -1 : 1));
  }

  function drawTeam() {
    if (!$("view-team")) return;                     // not on this person's page
    const all = D.time_to_action.filter((r) => inSegId(r.account_id)), called = all.filter((r) => r.call_date);
    const ownerRows = teamRows(all);
    const csm = ownerRows.filter((r) => r.o.owner_type === "csm"), pool = ownerRows.filter((r) => r.o.owner_type === "pool");
    const sum = (rs, k) => rs.reduce((s, r) => s + r[k], 0);
    $("tm-finding").textContent = !all.length ? `${segName()}: no account at churn or expansion Act with a CSM or a pool.` : `${segOn() ? segName() + ": " : ""}${all.length - called.length} of ${all.length} accounts at Act (${all.filter((r) => r.flag === "churn").length} churn, ${all.filter((r) => r.flag === "expansion").length} expansion) have had no call yet, ${all.filter((a) => !a.call_date && day(D.as_of) - day(a.signal_date) >= REACH(tierOf(a.account_id))).length} of them past their tier's target (${REACH_TEXT}): ${sum(pool, "n") - sum(pool, "called")} with the Scale pools, ${sum(csm, "n") - sum(csm, "called")} with named CSMs.`;

    // dot plot (redesigned 10 Nov 2026): one axis, days since the signal, for every Act account.
    // Called = a filled mark on the day of the call. Not called yet = an open ring at how long it
    // has waited so far: neutral while there's still time, red past the target (the same 14 days
    // after which journeys.py escalates). The axis is stretched near zero (square root) so the
    // calls in the first two weeks don't crowd, while a 60-day wait still fits.
    const host = $("tm-dots"); host.innerHTML = "";
    const TARGET_OF = (a) => REACH(tierOf(a.account_id)), ASOF = day(D.as_of);
    const TARGETS = [...new Set(ownerRows.flatMap((r) => r.rs.map(TARGET_OF)))].sort((x, z) => x - z);
    const waited = (a) => ASOF - day(a.signal_date);
    const val = (a) => (a.call_date ? Math.max(0, a.days_to_call) : waited(a));
    const hi = Math.max(...TARGETS, 14, ...ownerRows.flatMap((r) => r.rs.map(val)));
    const maxD = hi <= 30 ? 30 : hi <= 60 ? 60 : 90;
    const TICKS = [0, 3, 7, 14, 30, 60, 90].filter((t) => t <= maxD);
    const ph = isPhone(), W = fitW(760, 330), L = ph ? 0 : 150, R = ph ? 8 : 190;
    const X = (d) => L + 6 + Math.sqrt(Math.min(d, maxD) / maxD) * (W - L - R - 12);
    // rows: a header per group, then one row per owner (on a phone the name and summary sit above the dots)
    const lab = ph ? 30 : 0, rowH = 32 + lab, headH = 22, top = 20;   // top: room for the target's label
    const groups = [["Named CSMs", csm, "about 8 accounts each"], ["Scale pools", pool, "a regional book of about 40"]].filter(([, rs]) => rs.length);
    let y = top; const layout = [];
    const spans = [];                                    // each group's rows, for its own tier's target line
    for (const [g, rs, note] of groups) {
      layout.push({ head: `${g} (${note})`, y }); y += headH;
      const y0 = y; for (const r of rs) { layout.push({ r, y }); y += rowH; }
      const tg = [...new Set(rs.flatMap((r) => r.rs.map(TARGET_OF)))];
      spans.push({ y0: y0 - headH, y1: y, targets: tg.length ? tg : [REACH(g === "Named CSMs" ? "Enterprise" : "Scale")] });
    }
    const yEnd = y, H = yEnd + 40;
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Days since the signal per Act account: called on day N, or still waiting N days" }, host);
    for (const t of TICKS) {
      el("line", { x1: X(t), x2: X(t), y1: top, y2: yEnd, stroke: "var(--rule)", "stroke-dasharray": TARGETS.includes(t) ? "none" : "2 4" }, svg);
      el("text", { x: X(t), y: yEnd + 16, "text-anchor": "middle", class: "val" }, svg).textContent = t === maxD ? `${t}+` : t;
    }
    // the targets, one per tier: past its tier's line an account still waiting is late
    const tiersAt = (d) => Object.entries(D.settings.reach_days || {}).filter(([, v]) => v === d).map(([tr]) => tr).join(", ");
    for (const s of spans) for (const T of s.targets) {
      el("line", { x1: X(T), x2: X(T), y1: s.y0 + 4, y2: s.y1, stroke: "var(--ink-3)", "stroke-width": 1.5 }, svg);
      el("text", { x: X(T) + 4, y: s.y0 + 15, "text-anchor": "start", class: "val" }, svg).textContent = `${T}-day target (${tiersAt(T) || "its tier"})`;
    }
    el("text", { x: (X(0) + X(maxD)) / 2, y: yEnd + 32, "text-anchor": "middle" }, svg).textContent = "days since the signal (stretched near zero)";
    for (const it of layout) {
      if (it.head) { el("text", { x: 0, y: it.y + 15, class: "lbl", "font-weight": 600 }, svg).textContent = it.head; continue; }
      const r = it.r, cyRow = it.y + lab + 16;
      const wait = r.rs.filter((a) => !a.call_date), late = wait.filter((a) => waited(a) >= TARGET_OF(a));
      const summary = `${r.med == null ? "no calls" : "median " + r.med + " d"} · ${wait.length} waiting`;
      el("line", { x1: 0, x2: W, y1: it.y + rowH, y2: it.y + rowH, stroke: "var(--rule)", "stroke-opacity": 0.5 }, svg);
      const name = ph ? r.o.owner_name.replace(" Scale team", " pool") : r.o.owner_name;
      el("text", { x: 0, y: ph ? it.y + 13 : cyRow + 4, class: "lbl" }, svg).textContent = name;
      const st = el("text", { x: ph ? 0 : W, y: ph ? it.y + 26 : cyRow + 4, "text-anchor": ph ? "start" : "end", class: "val" }, svg);
      st.textContent = summary;
      if (late.length) { const t = el("tspan", {}, st); t.textContent = `, ${late.length} late`; t.style.fill = "var(--critical)"; t.style.fontWeight = "600"; }
      // each mark takes the first lane (middle, above, below) where it touches nothing already drawn;
      // with all three taken (only on a phone, in practice) it shows as "+n" beside the middle one
      const placed = [], extra = {};
      for (const a of [...r.rs].sort((x, z) => val(x) - val(z))) {
        const v = val(a), x0 = X(v);
        // on a desktop a crowded day opens more columns to the right; a phone has no room, so "+n"
        let spot = null;
        for (let col = 0; col < (ph ? 1 : 4) && !spot; col++) {
          const x = x0 + col * 10, lane = [0, -9, 9].find((o) => !placed.some((q) => q.o === o && Math.abs(q.x - x) < 10));
          if (lane !== undefined) spot = { x, o: lane };
        }
        if (!spot) { const near = placed.find((q) => q.o === 0 && Math.abs(q.x - x0) < 10) || placed[placed.length - 1]; extra[near.x] = (extra[near.x] || 0) + 1; continue; }
        placed.push(spot);
        const cyo = spot.o, col = (spot.x - x0) / 10;
        const cx = X(v) + col * 10, cy = cyRow + cyo;
        const isGrowth = a.flag === "expansion", isLate = !a.call_date && v >= TARGET_OF(a);
        const m = a.call_date
          ? (isGrowth ? el("rect", { x: cx - 4.5, y: cy - 4.5, width: 9, height: 9, rx: 1, fill: "var(--viz-call)", stroke: "var(--surface)", "stroke-width": 1.5 }, svg)
                      : el("circle", { cx, cy, r: 4.5, fill: "var(--viz-call)", stroke: "var(--surface)", "stroke-width": 1.5 }, svg))
          : el("circle", { cx, cy, r: 4.5, fill: "var(--surface)", stroke: isLate ? "var(--critical)" : "var(--ink-3)", "stroke-width": 2, class: isLate ? "late" : "waiting" }, svg);
        const nm = byId[a.account_id]?.account_name || a.account_id;
        hover(m, `<b>${esc(nm)}</b> · ${a.flag} Act<br>Signal ${fmt(a.signal_date)}<br>` + (a.call_date
          ? (a.via === "qbr" ? `Covered by the QBR on ${fmt(a.call_date)}, ${a.days_to_call} days later` : a.called_early ? `Called ${fmt(a.call_date)}, before the signal` : `Called ${fmt(a.call_date)}, ${a.days_to_call} days later`)
          : `No call yet: waiting ${v} days` + (isLate ? `, past its ${TARGET_OF(a)}-day target (${tierOf(a.account_id)})` : "")) + (v > maxD ? ` (shown at ${maxD}+)` : "") + (a.escalated ? "<br>Escalated to the manager" : ""));
        m.style.cursor = "pointer";
        m.addEventListener("click", () => openAccount(a.account_id));
      }
      for (const [x, n] of Object.entries(extra)) el("text", { x: +x + 8, y: cyRow + 4, class: "val" }, svg).textContent = `+${n}`;
    }
    $("tm-legend").innerHTML = "";
    for (const [shape, color, name] of [["circle", "var(--viz-call)", "Churn Act, called on that day"], ["square", "var(--viz-call)", "Expansion Act, called on that day"], ["circle-open", "var(--ink-3)", "Not called yet, still in time"], ["circle-open", "var(--critical)", `Not called, waiting past its tier's target`]]) {
      const s = document.createElement("span"); s.append(legendSwatch(shape, color), name); $("tm-legend").appendChild(s);
    }
    drawCapacity();
    { const s = document.createElement("span"); s.textContent = "Click a dot to open its timeline."; $("tm-legend").appendChild(s); }

    // owner table
    const head = `<tr><th>Owner</th><th class="r">Act</th><th class="r">Called</th><th class="r">Median days</th><th class="r">Escalated</th></tr>`;
    const line = (r) => `<tr><td>${esc(r.o.owner_name)}</td><td class="r num">${r.n}</td><td class="r num">${r.called} (${pct(r.called, r.n)}%)</td><td class="r num">${r.med == null ? "–" : r.med}</td><td class="r num">${r.esc || "–"}</td></tr>`;
    // each group ends with its median, so every owner's number has a reference
    const medRow = (rs) => { if (rs.length < 2) return ""; const m = (f) => { const v = median(rs.map(f).filter((x) => x != null)); return v == null ? "–" : Math.round(v * 10) / 10; };
      return `<tr class="pinned"><td><small>Median</small></td><td class="r num"><small>${m((r) => r.n)}</small></td><td class="r num"><small>${m((r) => pct(r.called, r.n))}%</small></td><td class="r num"><small>${m((r) => r.med)}</small></td><td class="r num"><small>${m((r) => r.esc)}</small></td></tr>`; };
    if (PAGE && PAGE.outcomes === "team_total") {          // below manager level: no owner-by-owner comparison
      $("tm-table").innerHTML = `<tr><td class="muted">Call speed by owner is for managers (ROLES.md). The dots above show your team's accounts.</td></tr>`;
    } else $("tm-table").innerHTML = head + `<tr class="group"><td colspan="5">Named CSMs</td></tr>` + csm.map(line).join("") + medRow(csm) + `<tr class="group"><td colspan="5">Scale pools</td></tr>` + pool.map(line).join("") + medRow(pool);

    // meetings per week heat grid
    const wh = $("tm-weeks"); wh.innerHTML = "";
    const weeks = D.week_ends;
    const team = D.team.filter((t) => (!seg.manager || t.manager === seg.manager) && (!seg.team || t.region === seg.team) && (!seg.owner || t.owner_id === seg.owner)
      && t.owner_type !== "pool"                       // people hold meetings; a pool is a queue
      && (!seg.tier || (seg.tier === "Enterprise" ? t.owner_type === "csm" : seg.tier === "Scale" ? t.owner_type === "member" : false)));
    const counts = team.map((t) => weeks.map((we) => D.touches.filter((x) => x.event === "meeting_held" && x.owner_id === t.owner_id && day(x.date) > day(we) - 7 && day(x.date) <= day(we)).length));
    const max = Math.max(1, ...counts.flat());
    $("tm-weeks-title").textContent = "Click a cell to list its meetings"; $("tm-weeks-list").innerHTML = "";
    if (!team.length) { wh.innerHTML = '<p class="note">Nobody on the CS team covers this segment.</p>'; return; }
    const GW = fitW(360, 310), GL = isPhone() ? 96 : 120, cw = (GW - GL) / weeks.length, ch = 20, GH = team.length * (ch + 3) + 46;
    const gs = el("svg", { viewBox: `0 0 ${GW} ${GH}`, role: "img", "aria-label": "Meetings per week by owner" }, wh);
    const ramp = ["var(--seq-0)", "var(--seq-1)", "var(--seq-2)", "var(--seq-3)", "var(--seq-4)", "var(--seq-5)"];
    team.forEach((t, i) => {
      const y = i * (ch + 3);
      el("text", { x: 0, y: y + 14, class: "lbl" }, gs).textContent = t.owner_name.replace(" Scale team", " pool");
      weeks.forEach((we, j) => {
        const v = counts[i][j];
        const c = v === 0 ? ramp[0] : ramp[Math.min(5, 1 + Math.floor(((v - 1) / Math.max(1, max - 1)) * 4.999))];
        const r = el("rect", { x: GL + j * cw + 1, y, width: cw - 2, height: ch, rx: 2, fill: c, class: "cell", style: "cursor:pointer" }, gs);
        hover(r, `<b>${esc(t.owner_name)}</b><br>Week ending ${fmt(we)}: ${v} meeting${v === 1 ? "" : "s"}<br>Click to list them`);
        r.addEventListener("click", () => accountList($("tm-weeks-list"), $("tm-weeks-title"), `${t.owner_name}, week ending ${fmt(we)}`,
          D.touches.filter((x) => x.event === "meeting_held" && x.owner_id === t.owner_id && day(x.date) > day(we) - 7 && day(x.date) <= day(we)).sort((a, b) => a.date.localeCompare(b.date)), MEETING_COLS, false, "meeting"));
      });
    });
    [0, 6, 12].forEach((j, k) => { el("text", { x: k === 2 ? GW : GL + j * cw + cw / 2, y: GH - 26, "text-anchor": k === 2 ? "end" : "middle" }, gs).textContent = fmt(weeks[j]); });
    // the key: the ramp from none to the busiest week (review 10 Nov: "darker means more" was backwards in dark mode)
    const ky = GH - 14;
    el("text", { x: GL - 6, y: ky + 9, "text-anchor": "end" }, gs).textContent = "0";
    const kw = isPhone() ? 12 : 18;
    ramp.forEach((c, k) => el("rect", { x: GL + k * kw, y: ky, width: kw - 2, height: 10, rx: 2, fill: c }, gs));
    el("text", { x: GL + 6 * kw + 4, y: ky + 9 }, gs).textContent = `${max} meeting${max === 1 ? "" : "s"} a week`;
  }

