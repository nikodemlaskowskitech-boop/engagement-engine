  // ---------------------------------------------------------------- book of business
  let bkM = "risk";
  document.querySelectorAll("#bk-measure button").forEach((b) => b.addEventListener("click", () => {
    bkM = b.dataset.m; document.querySelectorAll("#bk-measure button").forEach((x) => x.setAttribute("aria-pressed", String(x === b))); drawBook();
  }));
  // Capacity: each owner's whole book and open tasks against the median of
  // owners of the same kind. People filters apply; account filters don't.
  function drawCapacity() {
    const people = D.team.filter((t) => (!seg.manager || t.manager === seg.manager) && (!seg.team || t.region === seg.team) && (!seg.owner || t.owner_id === seg.owner));
    const all = D.team.map((t) => ({ t, accs: accountsOf(t.owner_id).length,
      open: workOf(t.owner_id).filter((x) => x.action_status === "Open").length,
      away: D.accounts.filter((a) => a.owner_id === t.owner_id && crossRegion(a)).length }));
    const med = {};
    for (const k of Object.keys(KIND_ORDER)) { const g = all.filter((r) => r.t.owner_type === k); med[k] = { accs: peerMed(k, "accs", () => median(g.map((r) => r.accs))), open: peerMed(k, "open", () => median(g.map((r) => r.open))) }; }
    const rows = all.filter((r) => people.includes(r.t)).sort((x, y) => KIND_ORDER[x.t.owner_type] - KIND_ORDER[y.t.owner_type] || x.open - y.open);
    const room = (r) => r.t.owner_type !== "pool" && r.open < med[r.t.owner_type].open;   // a queue has no room; people do
    const cell = (v, m) => `${v} <small>(median ${m})</small>`;
    queueMicrotask(() => foldRows($("tm-cap")));
    $("tm-cap").innerHTML = `<tr><th>Owner</th><th>Team</th><th class="r">Accounts</th><th class="r">Open tasks</th><th class="r">Other region</th><th></th></tr>` + rows.map((r) =>
      `<tr><td>${esc(r.t.owner_name)}</td><td>${esc(r.t.region)}</td><td class="r num">${cell(r.accs, med[r.t.owner_type].accs)}</td><td class="r num">${cell(r.open, med[r.t.owner_type].open)}</td><td class="r num">${r.away || "–"}</td><td>${room(r) ? "room" : ""}</td></tr>`).join("");
    const withRoom = rows.filter(room);
    $("tm-cap-finding").textContent = !rows.length ? "Nobody on the CS team in this segment."
      : withRoom.length ? `${withRoom.length} of ${rows.length} owners have fewer open tasks than the median: ${withRoom.slice(0, 3).map((r) => r.t.owner_name + " (" + r.open + ")").join(", ")}${withRoom.length > 3 ? " and more" : ""}.`
      : rows.length === 1 ? `${rows[0].t.owner_name} has ${rows[0].open} open task${rows[0].open === 1 ? "" : "s"}, not below the median for ${kindName(rows[0].t.owner_type)} (${med[rows[0].t.owner_type].open}).`
      : `None of the ${rows.length} owners is below the median of open tasks.`;
  }

  function drawBook() {
    if (!$("view-book")) return;                     // not on this person's page
    const regions = REGIONS, tiers = TIERS;
    const weeks = D.week_ends.filter((we) => day(we) >= day(D.util_dates[0]));
    const idx = weeks.map((we) => D.util_dates.indexOf(we));
    const test = bkM === "risk" ? (u) => u < 40 : (u) => u >= 80;
    $("bk-title").textContent = bkM === "risk" ? "Share of live accounts under 40% utilisation, by week" : "Share of live accounts at 80% utilisation and up, by week";
    const rows = [];
    for (const r of regions) for (const t of tiers) {
      const accs = D.accounts.filter((a) => a.region === r && a.tier === t && inSeg(a));
      if (!accs.length) continue;                       // outside the segment
      const cells = idx.map((i) => { const live = accs.filter((a) => a.util[i] != null); const hit = live.filter((a) => test(a.util[i])); return { live: live.length, hit: hit.length, accs: hit.map((a) => ({ ...a, u: a.util[i] })) }; });
      rows.push({ r, t, n: accs.length, cells });
    }
    const last = rows.map((x) => x.cells[x.cells.length - 1]), first = rows.map((x) => x.cells[0]);
    const tot = (cs) => cs.reduce((s, c) => ({ live: s.live + c.live, hit: s.hit + c.hit }), { live: 0, hit: 0 });
    const L = tot(last), F = tot(first);
    $("bk-finding").textContent = bkM === "risk"
      ? `${segOn() ? segName() + ": " : ""}${L.hit} of ${L.live} live accounts${segOn() ? "" : " in the whole book"} (${pct(L.hit, L.live)}%) sit under the 40% usage line today, up from ${F.hit} of ${F.live} (${pct(F.hit, F.live)}%) on ${fmt(weeks[0])}.`
      : `${segOn() ? segName() + ": " : ""}${L.hit} of ${L.live} live accounts (${pct(L.hit, L.live)}%) are at 80% of their contract or more today, from ${F.hit} of ${F.live} (${pct(F.hit, F.live)}%) on ${fmt(weeks[0])}.`;
    // shelfware by tier, counts next to shares (small segments mislead)
    $("bk-shelf").textContent = tiers.filter((t) => D.accounts.some((a) => a.tier === t && inSeg(a))).map((t) => {
      const accs = D.accounts.filter((a) => a.tier === t && inSeg(a));
      const hit = accs.filter((a) => a.flags.some((f) => f.flag === "churn" && f.status === "Shelfware")).length;
      return `${t}: ${hit} of ${accs.length} (${pct(hit, accs.length)}%)`;
    }).join(" · ");
    // accounts covered by an owner from another team (people side vs. account side)
    const cross = D.accounts.filter((a) => inSeg(a) && crossRegion(a));
    accountList($("bk-cross"), $("bk-cross-title"), "Covered from another region", cross, [
      { label: "Account", get: (a) => esc(a.account_name) },
      { label: "Customer in", get: (a) => esc(a.region) },
      { label: "Owner", get: (a) => esc(a.owner) + " · " + esc(homeOf[a.owner_id]) },
      { label: "Tier", get: (a) => esc(a.tier) },
    ], true);
    const shareMax = Math.max(0.01, ...rows.flatMap((x) => x.cells.map((c) => (c.live ? c.hit / c.live : 0))));
    const ramp = ["var(--seq-0)", "var(--seq-1)", "var(--seq-2)", "var(--seq-3)", "var(--seq-4)", "var(--seq-5)"];
    const host = $("bk-heat"); host.innerHTML = "";
    $("bk-cell-title").textContent = "Click a cell to list its accounts"; $("bk-cell-list").innerHTML = "";
    if (!rows.length) { $("bk-finding").textContent = `${segName()}: no accounts.`; return; }
    const ph = isPhone(), W = fitW(880, 640), LBL = ph ? 186 : 250, TX = ph ? 70 : 116, cw = (W - LBL) / weeks.length, ch = 24, H = rows.length * (ch + 3) + 54;
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Book of business heatmap", style: "min-width:640px" }, host);
    weeks.forEach((we, j) => { if (!ph || j % 2 === 0) el("text", { x: LBL + j * cw + cw / 2, y: 12, "text-anchor": "middle" }, svg).textContent = fmt(we); });
    rows.forEach((x, i) => {
      const y = 22 + i * (ch + 3);
      if (i === 0 || rows[i - 1].r !== x.r) el("text", { x: 0, y: y + 16, class: "lbl", "font-weight": 600 }, svg).textContent = ph ? x.r.replace("France & Benelux", "F&B") : x.r;
      el("text", { x: TX, y: y + 16, class: "lbl" }, svg).textContent = `${x.t} (${x.n})`;
      x.cells.forEach((c, j) => {
        const s = c.live ? c.hit / c.live : 0;
        const fill = c.live === 0 ? "var(--bg)" : s === 0 ? ramp[0] : ramp[Math.min(5, 1 + Math.floor((s / shareMax) * 4.999))];
        const r = el("rect", { x: LBL + j * cw + 1, y, width: cw - 2, height: ch, rx: 2, fill, class: "cell", style: c.live ? "cursor:pointer" : "" }, svg);
        if (c.live) r.addEventListener("click", () => accountList($("bk-cell-list"), $("bk-cell-title"), `${x.r} · ${x.t}, ${bkM === "risk" ? "under 40%" : "at 80% and up"} on ${fmt(weeks[j])}`,
          [...c.accs].sort((a, b) => (bkM === "risk" ? a.u - b.u : b.u - a.u)), [
            { label: "Account", get: (a) => esc(a.account_name) },
            { label: "Owner", get: (a) => esc(a.owner || "None") },
            { label: "Utilisation", r: true, get: (a) => `${a.u}%` },
          ]));
        if (c.live) {
          const t = el("text", { x: LBL + j * cw + cw / 2, y: y + 16, "text-anchor": "middle", class: "val" }, svg);
          t.textContent = c.hit ? pct(c.hit, c.live) + "%" : "–";
          t.style.fill = inkOn(fill);
          t.style.pointerEvents = "none";
        }
        hover(r, `<b>${esc(x.r)} · ${x.t}</b><br>Week ending ${fmt(weeks[j])}<br>${c.hit} of ${c.live} live accounts (${pct(c.hit, c.live)}%) ${bkM === "risk" ? "under 40%" : "at 80% and up"}` + (c.live ? "<br>Click to list them" : ""));
      });
    });
    // legend
    const ly = H - 18;
    el("text", { x: LBL, y: ly + 10 }, svg).textContent = "0%";
    ramp.forEach((c, k) => el("rect", { x: LBL + 24 + k * 26, y: ly, width: 24, height: 12, rx: 2, fill: c }, svg));
    el("text", { x: LBL + 24 + 6 * 26 + 4, y: ly + 10 }, svg).textContent = pct(shareMax, 1) + "% of the row";
  }

