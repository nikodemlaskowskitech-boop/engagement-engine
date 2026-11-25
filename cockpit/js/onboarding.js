  // ---------------------------------------------------------------- onboarding
  const GROUPS = [{ key: "welcomed", name: "Welcomed", color: "var(--viz-touch)" }, { key: "control (no email)", name: "Control (no email)", color: "var(--viz-other)" }];   // a baseline is a receding neutral
  if ($("ob-legend")) for (const g of GROUPS) { const s = document.createElement("span"); s.append(legendSwatch("circle", g.color), g.name); $("ob-legend").appendChild(s); }   // only where the Onboarding tab is on the page
  let obTier = "all";
  document.querySelectorAll("#ob-tier button").forEach((b) => b.addEventListener("click", () => {
    obTier = b.dataset.tier; document.querySelectorAll("#ob-tier button").forEach((x) => x.setAttribute("aria-pressed", String(x === b))); drawOnboarding();
  }));
  function obStats(rows) {
    const inst = rows.filter((r) => r.sdk_date);
    const old = rows.filter((r) => r.old_enough);
    const act = old.filter((r) => r.sdk_date && day(r.sdk_date) < day(r.contract_start) + 14 && r.users_first_14d >= 2);
    return { n: rows.length, inst: inst.length, chart: rows.filter((r) => r.first_chart_date).length,
      mSdk: median(inst.map((r) => day(r.sdk_date) - day(r.contract_start))),
      mChart: median(rows.filter((r) => r.first_chart_date).map((r) => day(r.first_chart_date) - day(r.contract_start))),
      old: old.length, act: act.length };
  }
  function drawOnboarding() {
    if (!$("view-onboarding")) return;                     // not on this person's page
    const rows = D.onboarding.filter((r) => (obTier === "all" || r.tier === obTier) && inSegId(r.account_id));
    const S = Object.fromEntries(GROUPS.map((g) => [g.key, obStats(rows.filter((r) => r.welcome_group === g.key))]));
    const w = S["welcomed"], c = S["control (no email)"];
    if (w.mSdk == null || c.mSdk == null) $("ob-finding").textContent = `${segName()}: too few new accounts to compare (${w.n} welcomed, ${c.n} in the control group).`;
    else {
      // the share that activates, said as it is (23 Nov 2026): "about the same" only within 5 points,
      // and too few to tell under MIN_COMPARE accounts in either group
      const wp = pct(w.act, w.old), cp = pct(c.act, c.old), counts = `${w.act} of ${w.old} vs ${c.act} of ${c.old}`;
      const tail = w.old < MIN_COMPARE || c.old < MIN_COMPARE ? `; too few to compare how many activate (${counts}).`
        : Math.abs(wp - cp) <= 5 ? `, but about the same share activate: ${wp}% vs ${cp}% (${counts}).`
        : wp > cp ? `, and more activate: ${wp}% vs ${cp}% (${counts}).` : `, but fewer activate: ${wp}% vs ${cp}% (${counts}).`;
      $("ob-finding").textContent = `${segOn() ? segName() + ": the" : "The"} email gets accounts started ${Math.round(c.mSdk - w.mSdk)} days sooner (median ${w.mSdk} vs ${c.mSdk} days to SDK)` + tail;
    }

    // histogram: accounts per day from signing to SDK install, one row per group
    const host = $("ob-dots"); host.innerHTML = "";
    $("ob-list-title").textContent = "Click a bar to list its accounts"; $("ob-list").innerHTML = "";
    const OB_COLS = [
      { label: "Account", get: (r) => nameOf(r.account_id) },
      { label: "Tier", get: (r) => esc(r.tier) },
      { label: "Signed", r: true, get: (r) => fmt(r.contract_start) },
      { label: "SDK", r: true, get: (r) => (r.sdk_date ? `day ${day(r.sdk_date) - day(r.contract_start)}` : "not yet") },
      { label: "Users in 14 days", r: true, get: (r) => r.users_first_14d ?? "–" },
    ];
    const obList = (title, items) => accountList($("ob-list"), $("ob-list-title"), title, items, OB_COLS);
    const ph = isPhone(), W = fitW(640), L = ph ? 0 : 150, R = ph ? 4 : 16, rowH = ph ? 120 : 86, H = 2 * rowH + 40, maxD = 14;
    const slot = (W - L - R) / (maxD + 1);
    const X = (d) => L + Math.min(d, maxD) * slot;
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Days from signing to SDK install by group" }, host);
    const hist = (g) => { const h = Array(maxD + 1).fill(0); rows.filter((r) => r.welcome_group === g.key && r.sdk_date).forEach((r) => h[Math.min(maxD, day(r.sdk_date) - day(r.contract_start))]++); return h; };
    const H2 = GROUPS.map(hist), top = Math.max(1, ...H2.flat());
    for (let d = 0; d <= maxD; d += 2) el("text", { x: X(d) + slot / 2, y: 2 * rowH + 24, "text-anchor": "middle", class: "val" }, svg).textContent = d === maxD ? "14+" : d;
    el("text", { x: (L + W - R) / 2, y: H - 2, "text-anchor": "middle" }, svg).textContent = "days from signing to SDK install";
    GROUPS.forEach((g, gi) => {
      const base = 10 + (gi + 1) * rowH - 8, st = S[g.key];
      if (ph) {                                           // phone: the group's numbers on one line above its bars
        const t = el("text", { x: 0, y: base - rowH + 26, class: "lbl" }, svg); t.textContent = g.name;
        el("text", { x: 0, y: base - rowH + 42 }, svg).textContent = `${st.inst} of ${st.n} installed` + (st.mSdk == null ? "" : `, median ${st.mSdk} days`);
      } else {
        el("text", { x: 0, y: base - rowH / 2, class: "lbl" }, svg).textContent = g.name;
        el("text", { x: 0, y: base - rowH / 2 + 16 }, svg).textContent = `${st.inst} of ${st.n} installed`;
        el("text", { x: 0, y: base - rowH / 2 + 32 }, svg).textContent = st.mSdk == null ? "" : `median ${st.mSdk} days`;
      }
      el("line", { x1: L, x2: W - R, y1: base, y2: base, stroke: "var(--rule)" }, svg);
      H2[gi].forEach((v, d) => {
        if (!v) return;
        const h = Math.max(3, (v / top) * (rowH - (ph ? 66 : 26))), x = X(d) + (ph ? 1.5 : 3), w = slot - (ph ? 3 : 6);
        const bar = el("path", { d: `M${x} ${base}V${base - h + 3}Q${x} ${base - h} ${x + 3} ${base - h}H${x + w - 3}Q${x + w} ${base - h} ${x + w} ${base - h + 3}V${base}Z`, fill: g.color, class: "bar", style: "cursor:pointer" }, svg);
        const when = d === maxD ? "14+ days" : `${d} day${d === 1 ? "" : "s"}`;
        bar.addEventListener("click", () => obList(`${g.name}: SDK in ${when} after signing`,
          rows.filter((r) => r.welcome_group === g.key && r.sdk_date && Math.min(maxD, day(r.sdk_date) - day(r.contract_start)) === d)));
        el("text", { x: x + w / 2, y: base - h - 4, "text-anchor": "middle", class: "val" }, svg).textContent = v;
        hover(bar, `<b>${g.name}</b><br>${v} account${v === 1 ? "" : "s"} installed the SDK ${when} after signing<br>Click to list them`);
      });
    });

    // outcome bars
    const fh = $("ob-funnel");
    const stages = [["Signed", (s) => s.n, (s) => s.n], ["SDK installed", (s) => s.inst, (s) => s.n], ["First chart", (s) => s.chart, (s) => s.n], ["Activated in 14 days", (s) => s.act, (s) => s.old]];
    const reached = [() => true, (r) => r.sdk_date, (r) => r.first_chart_date,
      (r) => r.old_enough && r.sdk_date && day(r.sdk_date) < day(r.contract_start) + 14 && r.users_first_14d >= 2];
    fh.innerHTML = "";
    const FW = fitW(320, 300), rowF = 54, FH = stages.length * rowF + 6;
    const fsvg = el("svg", { viewBox: `0 0 ${FW} ${FH}`, role: "img", "aria-label": "Onboarding outcome by group" }, fh);
    stages.forEach(([label, num, den], si) => {
      const y = si * rowF;
      el("text", { x: 0, y: y + 12, class: "lbl" }, fsvg).textContent = label;
      GROUPS.forEach((g, gi) => {
        const s = S[g.key], n = num(s), d = den(s), p = d ? n / d : 0;
        const by = y + 18 + gi * 14, bw = Math.max(2, p * (FW - 110));
        el("rect", { x: 0, y: by, width: FW - 110, height: 10, rx: 2, fill: "var(--bg)" }, fsvg);
        const hit = el("g", { class: "obbar", style: "cursor:pointer" }, fsvg);
        el("rect", { x: 0, y: by - 2, width: FW, height: 14, fill: "transparent" }, hit);          // the whole row is the target
        const bar = el("rect", { x: 0, y: by, width: bw, height: 10, rx: 2, fill: g.color }, hit);
        el("text", { x: FW - 104, y: by + 9, class: "val" }, hit).textContent = `${n} of ${d} (${pct(n, d)}%)`;
        hover(hit, `<b>${g.name}</b> · ${label}<br>${n} of ${d} accounts (${pct(n, d)}%)<br>Click to list them`);
        hit.addEventListener("click", () => obList(`${g.name}: ${label.toLowerCase()}`, rows.filter((r) => r.welcome_group === g.key && reached[si](r))));
      });
    });
  }

