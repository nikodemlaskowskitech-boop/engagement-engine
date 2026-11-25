  // ---------------------------------------------------------------- overview: the sieve
  const person = (r) => r.tier !== "Self-service";
  const atAct = (r) => person(r) && r.status === "Act";
  const STAGES = [
    { key: "all", label: "Churn signals", depth: 0, test: () => true },
    { key: "auto", label: "To automation (Self-service)", depth: 1, parent: "all", test: (r) => !person(r) },
    { key: "person", label: "To a person (Scale, Enterprise)", depth: 1, parent: "all", test: person },
    { key: "act", label: "At Act: a call is needed", depth: 2, parent: "person", test: atAct },
    { key: "nocall", label: "Not called yet", depth: 3, parent: "act", test: (r) => atAct(r) && !r.call_date, color: "var(--critical)" },
    { key: "called", label: "Called", depth: 3, parent: "act", test: (r) => atAct(r) && r.call_date },
    // the days per tier are in the note: the long label ran into its bar (18 Nov 2026)
    { key: "fast", label: "Reached in time", depth: 4, parent: "called", test: (r) => atAct(r) && r.call_date && r.days_to_call <= REACH(r.tier) },
  ];
  const SIG_COLS = [
    { label: "Account", get: (r) => nameOf(r.account_id) },
    { label: "Tier", get: (r) => esc(r.tier) },
    { label: "Owner", get: (r) => ownerOf(r.account_id) },
    { label: "Flag", get: (r) => esc(r.status) },
    { label: "Why", get: (r) => esc(r.reasons) },
    { label: "Called", r: true, get: (r) => r.call_date ? `${fmt(r.call_date)} <small>(${r.days_to_call} d)</small>` : "—" },
  ];
  function listStage(st, short) {
    const items = D.churn_signals.filter((r) => st.test(r) && inSegId(r.account_id));
    accountList($("ov-list"), $("ov-list-title"), st.label, items, SIG_COLS, short);
    document.querySelectorAll("#ov-sieve g.stage").forEach((g) => g.setAttribute("opacity", g.dataset.key === st.key ? 1 : 0.55));
  }
  function drawSieve() {
    if (!$("view-overview")) return;                     // not on this person's page
    const S = D.churn_signals.filter((r) => inSegId(r.account_id)), total = S.length || 1, n = {};
    for (const st of STAGES) n[st.key] = S.filter(st.test).length;
    const act = n.act, fast = n.fast;
    $("ov-finding").textContent = act
      ? `${segOn() ? segName() + ": " : ""}${n.nocall} of the ${act} accounts at churn Act (${pct(n.nocall, act)}%) haven't been reached yet.`
      : `${segOn() ? segName() + ": " : ""}no account at churn Act.`;
    if ($("ov-reach-days")) $("ov-reach-days").textContent = REACH_TEXT;
    const host = $("ov-sieve"); host.innerHTML = "";
    // desktop: label, bar, share in one row; phone: the label above its bar, the share as "n%"
    const ph = isPhone(), W = fitW(880), LBL = ph ? 0 : 300, BAR = ph ? W - 90 : W - LBL - 150, RH = ph ? 44 : 34;
    const svg = el("svg", { viewBox: `0 0 ${W} ${STAGES.length * RH + 6}`, role: "img", "aria-label": "Engagement sieve" }, host);
    STAGES.forEach((st, i) => {
      const y = 4 + i * RH, w = Math.max(2, (BAR * n[st.key]) / total), by = ph ? y + 20 : y + 5, ty = ph ? y + 32 : y + 19, x0 = ph ? 4 + st.depth * 10 : LBL;
      const g = el("g", { class: "stage", "data-key": st.key, style: "cursor:pointer" }, svg);
      el("text", { x: 4 + st.depth * (ph ? 10 : 18), y: ph ? y + 13 : y + 19, class: "lbl" }, g).textContent = (st.depth ? "└ " : "") + st.label;
      el("rect", { x: x0, y: by, width: Math.min(w, W - x0 - 70), height: ph ? 16 : RH - 12, rx: 3, fill: st.color || "var(--viz-measure)" }, g);
      el("text", { x: x0 + Math.min(w, W - x0 - 70) + 6, y: ty, class: "val" }, g).textContent = n[st.key];
      if (st.parent) el("text", { x: W - 4, y: ty, "text-anchor": "end" }, g).textContent = ph ? `${pct(n[st.key], n[st.parent])}%` : `${pct(n[st.key], n[st.parent])}% of the stage above`;
      g.addEventListener("click", () => listStage(st));
      hover(g, `<b>${esc(st.label)}</b><br>${n[st.key]} accounts${st.parent ? `, ${pct(n[st.key], n[st.parent])}% of the stage above` : ""}<br>Click to list them`);
    });
    listStage(STAGES.find((s) => s.key === "nocall"), true);
  }

  // ---------------------------------------------------------------- overview: renewals ahead
  const REN_ORDER = ["Act", "Shelfware", "Watch", "No churn flag"];
  function drawRenewals() {
    if (!$("view-overview")) return;                     // not on this person's page
    const start = day(D.as_of), buckets = [0, 1, 2, 3, 4, 5].map((b) => ({ from: start + 1 + 30 * b, to: start + 30 * (b + 1), items: [] }));
    for (const a of D.accounts.filter(inSeg)) {
      const d = day(a.renewal_date), b = Math.floor((d - start - 1) / 30);
      if (!(b >= 0 && b <= 5)) continue;                 // NaN too: a renewal date the CRM didn't give
      const cf = churnFlag(a);
      buckets[b].items.push({ ...a, churn: cf ? cf.status : "No churn flag", why: cf ? cf.reasons : "" });
    }
    const host = $("ov-renewals"); host.innerHTML = "";
    // the bars show only renewals with a churn flag, on their own scale; the healthy majority
    // would otherwise fill 85% of every bar. "of N renewing" above each bar keeps the reference.
    const risky = (k) => k.items.filter((a) => a.churn !== "No churn flag");
    const ph = isPhone(), W = fitW(880), H = 190, B = 34, L = ph ? 4 : 40, cw = (W - L) / 6, maxN = Math.max(1, ...buckets.map((k) => risky(k).length));
    const svg = el("svg", { viewBox: `0 0 ${W} ${H + 40}`, role: "img", "aria-label": "Renewals by month and flag" }, host);
    buckets.forEach((k, i) => {
      const x = L + i * cw + (ph ? 4 : 14), bw = cw - (ph ? 8 : 28);
      let y = H;
      const g = el("g", { style: "cursor:pointer" }, svg);
      for (const s of REN_ORDER.filter((x) => x !== "No churn flag")) {
        const c = k.items.filter((a) => a.churn === s).length;
        if (!c) continue;
        const h = ((H - B) * c) / maxN; y -= h;
        el("rect", { x, y, width: bw, height: Math.max(1, h - 2), fill: STATUS_COLOR[s] }, g);   // 2px gap below
      }
      el("text", { x: x + bw / 2, y: y - 20, "text-anchor": "middle", class: "val" }, g).textContent = risky(k).length;
      el("text", { x: x + bw / 2, y: y - 6, "text-anchor": "middle" }, g).textContent = ph ? `of ${k.items.length}` : `of ${k.items.length} renewing`;
      el("rect", { x, y: 0, width: bw, height: H, fill: "transparent" }, g);      // the whole column is clickable
      const from = new Date(k.from * 864e5).toISOString().slice(0, 10), to = new Date(k.to * 864e5).toISOString().slice(0, 10);
      el("text", { x: x + bw / 2, y: H + 18, "text-anchor": "middle" }, svg).textContent = ph ? fmt(from) : `${fmt(from)} – ${fmt(to)}`;
      if (ph) el("text", { x: x + bw / 2, y: H + 32, "text-anchor": "middle" }, svg).textContent = "– " + fmt(to);
      const counts = REN_ORDER.map((s) => `${s}: ${k.items.filter((a) => a.churn === s).length}`).join("<br>");
      hover(g, `<b>Renewing ${fmt(from)} – ${fmt(to)}</b><br>${counts}<br>Click to list them`);
      g.addEventListener("click", () => {
        const items = [...k.items].sort((a, b) => REN_ORDER.indexOf(a.churn) - REN_ORDER.indexOf(b.churn) || byDate(a.renewal_date, b.renewal_date));
        accountList($("ov-list"), $("ov-list-title"), `Renewing ${fmt(from)} – ${fmt(to)}`, items, [
          { label: "Account", get: (a) => esc(a.account_name) },
          { label: "Tier", get: (a) => esc(a.tier) },
          { label: "Owner", get: (a) => esc(a.owner || "None") },
          { label: "Churn flag", get: (a) => esc(a.churn) },
          { label: "Why", get: (a) => esc(a.why) },
          { label: "Renewal", r: true, get: (a) => fmt(a.renewal_date) },
        ]);
      });
    });
    el("line", { x1: L, x2: W, y1: H, y2: H, stroke: "var(--rule)" }, svg);
    const lg = $("ov-ren-legend"); lg.innerHTML = "";
    for (const s of REN_ORDER.filter((x) => x !== "No churn flag")) {
      const sp = document.createElement("span"); sp.append(legendSwatch("square", STATUS_COLOR[s]), s); lg.append(sp);
    }
    { const sp = document.createElement("span"); sp.textContent = "Bars: renewals with a churn flag. Click a month to list all its renewals, risk first."; lg.append(sp); }
  }

