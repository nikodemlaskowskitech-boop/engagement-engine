  // ---------------------------------------------------------------- my book: one owner's morning page
  // "What do I do today, and why?" for one CSM or pool: workload, engagement,
  // the portfolio risk first, and what's coming in the next 8 weeks. Comparisons
  // are against the median of owners of the same kind (CSMs with CSMs, pools with
  // pools), never against named colleagues.
  const RUN = day(D.as_of) + 1;                                  // the pipeline's "today"
  const FOLLOW_DAYS = D.settings.follow_up_days || 30;
  const PROGRAMME = new Set(["Stalled", "Silent", "Shelfware"]);
  const LIVE = (t) => t.action_status === "Open" || t.action_status === "Claimed";
  // a pool's book is every Scale account in its queue; a person's is what they own
  const accountsOf = (o) => (teamById[o] || {}).owner_type === "pool" ? D.accounts.filter((a) => a.pool === o) : D.accounts.filter((a) => a.owner_id === o);
  const peersOf = (o) => D.team.filter((t) => t.owner_type === (teamById[o] || {}).owner_type).map((t) => t.owner_id);
  const MEET_KIND = (topic) => topic === "qbr" ? "qbr" : ["churn_call", "growth_call", "activation_call", "follow_up"].includes(topic) ? "call" : "other";
  // one colour per meaning on this page: QBR blue, other meetings aqua, problem calls and
  // follow-ups violet; routine in two teal steps. Never blue next to violet.
  // calm on dark and light: one highlight colour for what needs a conversation (problem calls,
  // follow-ups), two neutrals for the routine cadence. Order keeps violet between the greys.
  // bottom to top, the same order in the legend; one colour per engagement type on every chart
  // inside: the segment may show its own count (QBRs and calls, the work that matters; never "other")
  const MEET = [{ k: "other", name: "Kickoff, check-in, other", color: "var(--viz-other)" }, { k: "qbr", name: "QBR", color: "var(--viz-qbr)", inside: true }, { k: "call", name: "Churn, growth or activation call, follow-up", color: "var(--viz-call)", inside: true }];
  const PIPE = [{ k: "renewal", name: "Renewal", color: "var(--viz-renewal)" }, { k: "qbr", name: "QBR booked", color: "var(--viz-qbr)", inside: true }, { k: "followup", name: "Follow-up due", color: "var(--viz-call)", inside: true }];
  // The reference stacked bar chart (10 Nov 2026): one bar per week, categories bottom to top in
  // `cats` order with a 2px gap in the surface colour between segments, the total on top, a
  // segment's count inside it only when it fits, recessive gridlines, every week labelled when
  // there's room, and a tooltip with the full breakdown in the same colours. bars: [{label,
  // tip, counts:{k:n}, onClick?}]. Returns nothing; draws into host.
  // fmtV (18 Nov 2026, "value, not volume"): bars in euros, labelled with money(); counts otherwise
  const stackChart = (host, { bars, cats, W, H = 170, aria, unit, totals = "all", fmtV = null }) => {
    host.innerHTML = "";
    W = fitW(W);                                                         // a phone draws at its own width
    const L = fmtV ? 46 : 30, T = 18, B = 24, plotH = H - T - B, n = bars.length, bw = (W - L) / n, gap = Math.min(10, bw * 0.18);
    const show = (v) => (fmtV ? fmtV(v) : v);
    const total = (b) => cats.reduce((t, c) => t + (b.counts[c.k] || 0), 0);
    const max = Math.max(1, ...bars.map(total));
    const nice = (x) => { const p = 10 ** Math.floor(Math.log10(x)), m = x / p; return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10) * p; };
    const step = fmtV ? nice(max / 4) : max <= 4 ? 1 : max <= 10 ? 2 : Math.ceil(max / 5);   // a few round gridlines
    const top = Math.ceil(max / step) * step, yOf = (v) => T + plotH - (plotH * v) / top;
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": aria, class: "stack" }, host);
    for (let v = step; v <= top; v += step) {
      el("line", { x1: L, x2: W, y1: yOf(v), y2: yOf(v), class: "grid" }, svg);
      el("text", { x: L - 6, y: yOf(v) + 4, "text-anchor": "end" }, svg).textContent = show(v);
    }
    el("line", { x1: L, x2: W, y1: yOf(0), y2: yOf(0), class: "base" }, svg);
    const ticks = [], tots = [];
    const every = bw >= 46 ? 1 : bw >= 26 ? 2 : bw >= 14 ? 3 : Math.ceil(48 / bw);   // a year of weeks: a label every ~48px
    bars.forEach((b, i) => {
      const g = el("g", { class: "bar", style: b.onClick ? "cursor:pointer" : "" }, svg), x = L + i * bw + gap / 2, w = bw - gap;
      let y = yOf(0);
      const parts = cats.filter((c) => b.counts[c.k]).length;          // one segment: the total on top says it all
      for (const c of cats) {
        const v = b.counts[c.k] || 0; if (!v) continue;
        const h = (plotH * v) / top;
        el("rect", { x, y: y - h + 2, width: w, height: Math.max(1, h - 2), fill: c.color, class: "fill", rx: 2 }, g);   // 2px gap below each segment
        if (c.inside && !fmtV && !isPhone() && parts > 1 && h >= 16 && w >= 18) el("text", { x: x + w / 2, y: y - h / 2 + 5, "text-anchor": "middle", class: "seg" }, g).textContent = v;
        y -= h;
      }
      const t = total(b);
      if (t && totals === "all") tots.push({ v: t, el: Object.assign(el("text", { x: x + w / 2, y: y - 4, "text-anchor": "middle", class: "tot" }, g), { textContent: show(t) }) });
      if (t && totals === "last" && i === n - 1) el("text", { x: x + w, y: y - 4, "text-anchor": "end", class: "tot" }, g).textContent = show(t);   // a long trend: today's total only
      el("rect", { x: L + i * bw, y: T - 14, width: bw, height: plotH + 14, fill: "transparent" }, g);    // hit target: the whole column
      if ((n - 1 - i) % every === 0) { const lastOne = i === n - 1;     // counted from today, so the latest week always has its label (end-anchored, never cut)
        const tk = el("text", { x: lastOne ? Math.min(W, L + i * bw + bw / 2 + 20) : L + i * bw + bw / 2, y: H - 6, "text-anchor": lastOne ? "end" : "middle" }, svg);
        tk.textContent = b.label; ticks.push(tk); }
      hover(g, `<b>${b.tip}</b>` + [...cats].reverse().map((c) => `<div class="row"><span class="sw" style="background:${c.color}"></span><span>${c.name}</span><b>${show(b.counts[c.k] || 0)}</b></div>`).join("") +
        `<div class="row tot"><span></span><span>Total</span><b>${fmtV ? show(t) : plural(t, unit)}</b></div>` + (b.onClick ? '<div class="muted">Click to list them</div>' : ""));
      if (b.onClick) g.addEventListener("click", b.onClick);
    });
    // totals that would touch the one before (euros are wider than counts) go; the hover keeps the value
    // the larger value keeps its label
    let prev = null;
    for (const t of tots) {
      const bx = t.el.getBBox();
      if (prev && bx.width && prev.bx.x + prev.bx.width + 3 > bx.x && Math.abs(prev.bx.y - bx.y) < bx.height) {
        if (t.v > prev.v) { prev.el.remove(); prev = { ...t, bx }; } else t.el.remove();
      } else prev = { ...t, bx };
    }
    // the latest week's label is end-anchored at the edge: if it would touch the one before, that one goes
    if (ticks.length > 1) { const a = ticks[ticks.length - 2].getBBox(), z = ticks[ticks.length - 1].getBBox();
      if (a.width && a.x + a.width + 4 > z.x) ticks[ticks.length - 2].remove(); }
  };
  const stackLegend = (host, cats, aside) => {
    host.innerHTML = "";
    for (const c of cats) { const sp = document.createElement("span"); sp.append(legendSwatch("square", c.color), c.name); host.append(sp); }
    if (aside) { const sp = document.createElement("span"); sp.className = "aside"; sp.textContent = aside; host.append(sp); }
  };
  const lastTouch = (id) => { const t = touchesBy[id]; return t && t.length ? t[t.length - 1] : null; };
  const met30 = (id) => (touchesBy[id] || []).some((t) => t.event === "meeting_held" && day(t.date) > RUN - 31);
  const fmtFull = (s) => s ? fmt(s) + " " + s.slice(0, 4) : fmt(s);
  // Usage trend for one account: the sparkline uses the account's own range (at least 20
  // points tall, so a small wobble isn't blown up), and a trend marker says the change over
  // 60 days in words: a drop of 5+ points is a red down arrow, a rise a green up arrow.
  const TREND_DAYS = 60, TREND_MIN = 5;
  function trend(util) {
    const v = util.filter((u) => u != null);
    if (v.length < 2) return null;
    const back = util.length - 1 - TREND_DAYS, then = util.slice(Math.max(0, back)).find((u) => u != null);
    return { now: v[v.length - 1], change: v[v.length - 1] - then, days: Math.min(TREND_DAYS, util.length - 1 - util.indexOf(then)) };
  }
  function trendMarker(util) {
    const t = trend(util);
    if (!t) return '<span class="muted">no trend yet</span>';
    const d = Math.round(t.change);
    if (Math.abs(d) < TREND_MIN) return `<span class="trend flat" title="${d >= 0 ? "+" : ""}${d} points in ${t.days} days">→ flat</span>`;
    return `<span class="trend ${d < 0 ? "down" : "up"}" title="${d > 0 ? "+" : ""}${d} points in ${t.days} days"><b>${d < 0 ? "▼" : "▲"}</b> ${Math.abs(d)} pts</span>`;
  }
  function spark(util) {
    const v = util.map((u, i) => [i, u]).filter(([, u]) => u != null);
    if (v.length < 2) return '<span class="muted">no usage yet</span>';
    const vals = v.map(([, u]) => u), lo0 = Math.min(...vals), hi0 = Math.max(...vals);
    const span = Math.max(20, hi0 - lo0), mid = (lo0 + hi0) / 2, lo = mid - span / 2, hi = mid + span / 2;
    const W = 90, H = 22, n = util.length - 1, Y = (u) => H - 3 - ((u - lo) / (hi - lo)) * (H - 6);
    const pts = v.map(([i, u]) => `${((i / n) * (W - 4)).toFixed(1)},${Y(u).toFixed(1)}`).join(" ");
    const last = v[v.length - 1];
    const line40 = 40 >= lo && 40 <= hi ? `<line x1="0" x2="${W}" y1="${Y(40)}" y2="${Y(40)}" stroke="var(--ink-3)" stroke-dasharray="2 2" stroke-width="0.8"/>` : "";
    return `<span class="sparkcell"><svg class="spark" viewBox="0 0 ${W} ${H}" aria-hidden="true">${line40}<polyline points="${pts}" fill="none" stroke="var(--ink-2)" stroke-width="1.5" stroke-linejoin="round"/><circle cx="${((last[0] / n) * (W - 4)).toFixed(1)}" cy="${Y(last[1]).toFixed(1)}" r="2.5" fill="var(--ink)"/></svg><span class="num">${last[1]}%</span> ${trendMarker(util)}</span>`;
  }
  const tile = (k, v, ref, alert) => `<div class="tile${alert ? " alert" : ""}"><div class="k">${k}</div><div class="v">${v}</div><div class="ref">${ref}</div></div>`;
  // Sortable tables (12 Nov 2026, Nikodem): click a column heading to sort by it. Numbers
  // and money: biggest first, then smallest first, then back to the table's own order.
  // Dates: soonest first, then latest. Short text (names, regions, statuses): A to Z, then
  // Z to A. Columns of long text (reasons, actions) or form controls aren't sortable. Rows
  // stay inside their group (My book's Churn / Growth / No flags); a folded table folds
  // again after the sort; a list showing its first rows sorts the whole list, not just
  // what's on screen. The choice is kept while the page redraws (until it's reloaded).
  const SORTS = {};
  const sortKey = (el) => (el.closest("[id]") || {}).id || "";
  const MONTHS = { jan: 0, feb: 1, mar: 2, apr: 3, may: 4, jun: 5, jul: 6, aug: 7, sep: 8, sept: 8, oct: 9, nov: 10, dec: 11 };
  function cellValue(text) {
    const t = (text || "").replace(/\s+/g, " ").trim();
    const share = t.match(/\((-?\d+(?:\.\d+)?)%\)/);                       // "8 of 8 (100%)": the share
    if (share) return { kind: "num", v: +share[1] };
    const d = t.match(/^(\d{1,2}) ([A-Z][a-z]{2,3})(?![a-z])\.?(?: ?(\d{4})(?!\d))?/);  // "15 Nov 2026", "15 Sept"
    if (d && MONTHS[d[2].toLowerCase()] != null) return { kind: "date", v: Date.UTC(+(d[3] || new Date(RUN * 864e5).getUTCFullYear()), MONTHS[d[2].toLowerCase()], +d[1]) };
    const m = t.match(/^([−+-])?€?\s?(\d[\d,]*(?:\.\d+)?)\s?([kM])?/);     // "€1.5M", "−€627k", "13", "74 days", "65%"
    if (m) { const v = parseFloat(m[2].replace(/,/g, "")) * (m[3] === "M" ? 1e6 : m[3] === "k" ? 1e3 : 1); return { kind: "num", v: m[1] && m[1] !== "+" ? -v : v }; }
    if (t === "–" || t === "—" || t === "") return { kind: "none" };
    return { kind: "text", v: t.toLowerCase() };
  }
  // what kind of column it is, from its cells: numbers, dates, short text, or not sortable
  function columnKind(cells) {
    if (cells.some((c) => c.querySelector("select, input, textarea"))) return null;
    const vals = cells.map((c) => cellValue(c.textContent)).filter((v) => v.kind !== "none");
    if (!vals.length) return null;
    for (const k of ["num", "date"]) if (vals.filter((v) => v.kind === k).length >= 0.6 * vals.length) return k;
    const avg = cells.reduce((t, c) => t + c.textContent.trim().length, 0) / cells.length;
    return avg <= 28 ? "text" : null;
  }
  const compare = (kind, dir) => (a, b) => {
    if (a.kind !== kind && b.kind !== kind) return 0;
    if (a.kind !== kind) return 1;                                     // blanks last, either way
    if (b.kind !== kind) return -1;
    const c = kind === "text" ? a.v.localeCompare(b.v) : a.v - b.v;
    return dir === "desc" ? -c : c;
  };
  const firstDir = (kind) => kind === "num" ? "desc" : "asc";
  // a list's items in the order chosen on its heading (shortList, accountList)
  function sortedItems(host, items, cols) {
    const st = SORTS[sortKey(host)];
    if (!st || !cols[st.col]) return items;
    const tmp = document.createElement("div");
    const keyed = items.map((it, i) => { tmp.innerHTML = cols[st.col].get(it); return { it, i, v: cellValue(tmp.textContent) }; });
    return keyed.sort((a, b) => compare(st.kind, st.dir)(a.v, b.v) || a.i - b.i).map((x) => x.it);
  }
  // headings become buttons; managed lists redraw themselves, other tables sort their rows in place
  function markSortable(table, redraw) {
    // keep-order: a list whose order is the point (My work); it still becomes cards on a phone
    if (!table || table.classList.contains("nosort") || table.classList.contains("keep-order") || table.classList.contains("matrix")) return;
    const head = headRow(table);
    if (!head) return;
    const ths = [...head.cells];
    const body = [...table.querySelectorAll("tbody tr")].filter((tr) => tr !== head && !tr.classList.contains("group") && !tr.classList.contains("pinned"));
    if (body.length < 2) return;
    const key = sortKey(table), st = SORTS[key];
    ths.forEach((th, col) => {
      const kind = columnKind(body.map((tr) => tr.cells[col]).filter(Boolean));
      if (!kind || !th.textContent.trim()) return;
      th.classList.add("sortable"); th.tabIndex = 0; th.dataset.kind = kind;
      const on = st && st.col === col;
      th.setAttribute("aria-sort", on ? (st.dir === "desc" ? "descending" : "ascending") : "none");
      th.querySelector(".sortmark")?.remove();
      const mark = document.createElement("span"); mark.className = "sortmark"; mark.setAttribute("aria-hidden", "true");
      mark.textContent = on ? (st.dir === "desc" ? " ▼" : " ▲") : " ↕"; th.append(mark);
      const go = () => {
        const cur = SORTS[key];
        const first = firstDir(kind), second = first === "desc" ? "asc" : "desc";
        if (!cur || cur.col !== col) SORTS[key] = { col, kind, dir: first };
        else if (cur.dir === first) SORTS[key] = { col, kind, dir: second };
        else delete SORTS[key];                                          // third click: the table's own order
        if (redraw) redraw(); else sortRows(table);
      };
      th.onclick = go;
      th.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } };
    });
    if (!redraw && st) sortRows(table, true);
  }
  // the heading row: the table's thead, or a first row of th cells (tables filled row by row)
  const headRow = (table) => table.tHead ? table.tHead.rows[0]
    : [...table.rows].find((tr) => tr.cells.length && [...tr.cells].every((c) => c.tagName === "TH")) || null;
  function sortRows(table, quiet) {
    const st = SORTS[sortKey(table)], tb = table.tBodies[0], head = headRow(table);
    const rows = [...tb.rows].filter((tr) => tr !== head);
    rows.forEach((tr, i) => { if (tr.dataset.ord == null) tr.dataset.ord = i; });
    // blocks between group rows ("Churn risk (5)") sort on their own
    const blocks = [[]];
    for (const tr of rows) { if (tr.classList.contains("group")) blocks.push([tr], []); else blocks[blocks.length - 1].push(tr); }
    const out = [];
    for (const b of blocks) {
      if (b.length === 1 && b[0].classList.contains("group")) { out.push(b[0]); continue; }
      const pinned = b.filter((tr) => tr.classList.contains("pinned"));       // a median row stays at its block's end
      const keyed = b.filter((tr) => !pinned.includes(tr)).map((tr) => ({ tr, v: st ? cellValue(tr.cells[st.col] ? tr.cells[st.col].textContent : "") : null }));
      keyed.sort((x, y) => (st ? compare(st.kind, st.dir)(x.v, y.v) : 0) || x.tr.dataset.ord - y.tr.dataset.ord);
      out.push(...keyed.map((k) => k.tr), ...pinned);
    }
    out.forEach((tr) => tb.append(tr));
    if (table._refold) table._refold();
    if (!quiet) markSortable(table);
  }
  // phone: every data table reads as a list of cards, each cell with its column's name
  // (data-label from the heading row; the 2x2 and the matrices keep their grid)
  function labelCells(table) {
    if (!table.isConnected || table.classList.contains("nosort") || table.classList.contains("matrix")) return;
    const head = headRow(table); if (!head) return;
    const names = [...head.cells].map((th) => (th.firstChild && th.firstChild.nodeType === 3 ? th.firstChild.textContent : th.textContent).replace(/[▲▼↕]/g, "").trim());
    head.classList.add("headrow");
    for (const tr of table.rows) {
      if (tr === head) continue;
      let col = 0;
      for (const td of tr.cells) { const span = td.colSpan || 1; if (span === 1 && td.tagName === "TD" && names[col]) td.dataset.label = names[col]; col += span; }
    }
    table.classList.add("cards");
  }
  // any table drawn anywhere (and drawn again on a redraw) gets sortable headings,
  // keeping the order chosen for its place on the page
  new MutationObserver((muts) => {
    for (const m of muts) for (const n of m.addedNodes) {
      if (n.nodeType !== 1) continue;
      const tables = n.tagName === "TABLE" ? [n] : [...n.querySelectorAll("table")];
      for (const t of tables) { if (!t.closest(".managed-sort")) setTimeout(() => markSortable(t), 0); setTimeout(() => labelCells(t), 0); }
      if (["TR", "TBODY", "THEAD"].includes(n.tagName)) { const t = n.closest("table"); if (t) setTimeout(() => labelCells(t), 0); }
      // a table filled in place (not the rows a sort moves)
      if (n.tagName === "TBODY" || n.tagName === "THEAD") { const t = n.closest("table"); if (t && !t.closest(".managed-sort")) setTimeout(() => markSortable(t), 0); }
    }
  }).observe(document.body, { childList: true, subtree: true });

  // a list that shows two rows and a "View all (n)" that opens the rest in place
  function foldRows(table, n = 6) {
    const wrap = table.closest(".tablewrap") || table, old = wrap.nextElementSibling;
    if (old && old.dataset.foldFor === table.id) old.remove();
    const bodyRows = () => [...table.querySelectorAll("tr")].filter((tr) => !tr.querySelector("th"));
    const rows = bodyRows();
    if (rows.length <= n + 1) return;                       // one hidden row isn't worth a click
    let open = false;
    const btn = document.createElement("button");
    btn.className = "link"; btn.dataset.foldFor = table.id;
    // rows read again each time: a sort may have reordered them (the first n after the sort show)
    const set = () => { bodyRows().forEach((tr, i) => { tr.hidden = !open && i >= n; }); btn.textContent = open ? "Show fewer" : `Show all (${rows.length})`; btn.setAttribute("aria-expanded", String(open)); };
    table._refold = set;
    btn.addEventListener("click", () => { open = !open; set(); });
    wrap.after(btn); set();
  }

  // Data missing (flags.py data_gaps, 24 Nov 2026): accounts whose own data has a gap, so the
  // flags that need it can't run. Those hiding a risk we can still see first, then by ARR.
  function drawDataGaps(accs, who) {
    const RISK = "Risk hidden";
    const rows = accs.filter((a) => a.flags.some((f) => f.flag === "data")).map((a) => ({ ...a,
      gaps: a.flags.filter((f) => f.flag === "data" && f.status !== RISK), risk: a.flags.find((f) => f.flag === "data" && f.status === RISK) }))
      .sort((x, y) => (!!y.risk - !!x.risk) || (y.arr - x.arr) || (x.account_id < y.account_id ? -1 : 1));
    const risky = rows.filter((a) => a.risk), arr = rows.reduce((t, a) => t + (a.arr || 0), 0);
    $("ld-data-finding").textContent = rows.length
      ? `${rows.length} of ${accs.length} accounts have a gap in their own data (${money(arr)} ARR the flags can only partly see)` +
        (risky.length ? `; ${risky.length} of them hide a risk we can still see, so their owner has it too.` : ".")
      : "No gaps: every account in this segment has the data its flags need.";
    shortList($("ld-data"), rows, [
      { label: "Account", get: (a) => `${esc(a.account_name)}<small><br>${esc(a.tier)}</small>` },
      { label: "Missing", get: (a) => a.gaps.map((f) => esc(f.status)).join("<br>") +
          (a.risk ? `<small><br><span class="flag warning inline"><span class="pill">risk hidden</span></span>${esc(a.risk.reasons.split(", and ").slice(1).join(", and "))}</small>` : "") },
      { label: "Fix by", get: (a) => [...new Set(a.gaps.map((f) => who(f.routed_to)))].map(esc).join("<br>") },
      { label: "ARR", r: true, get: (a) => money(a.arr) },
    ], "No gaps: every account in this segment has the data its flags need.", 5);
    const kinds = {};
    for (const a of rows) for (const f of a.gaps) { const k = (kinds[f.status] ||= { n: 0, arr: 0 }); k.n++; k.arr += a.arr; }
    $("ld-data-note").textContent = (Object.keys(kinds).length ? Object.entries(kinds).sort((x, y) => y[1].arr - x[1].arr)
        .map(([k, v]) => `${k}: ${v.n} (${money(v.arr)})`).join(" · ") + ". " : "") +
      "A missing contract volume, renewal date or pool, or a volume feed that stopped while the product didn't: the flags " +
      "that need it show the gap instead of guessing. The data's owner gets each one in their daily Slack digest; the " +
      "account's owner only when the gap hides a risk still visible (users falling, errors rising, a churn or expansion flag already open).";
  }

  function shortList(host, items, cols, empty, n = 2) {
    let all = false;
    host.classList.add("managed-sort");
    const base = items;
    const draw = () => {
      if (!items.length) { host.innerHTML = `<p class="note">${empty}</p>`; return; }
      items = sortedItems(host, base, cols);
      const rows = all ? items : items.slice(0, n);
      host.innerHTML = `<table><thead><tr>${cols.map((c) => `<th${c.r ? ' class="r"' : ""}>${c.label}</th>`).join("")}</tr></thead><tbody>` +
        rows.map((it) => `<tr class="click" tabindex="0" data-id="${esc(it.account_id)}">` + cols.map((c) => `<td${c.r ? ' class="r num"' : ""}>${c.get(it)}</td>`).join("") + "</tr>").join("") + "</tbody></table>" +
        (items.length > n ? `<button class="link" data-more>${all ? "Show fewer" : `View all (${items.length})`}</button>` : "");
      host.querySelectorAll("tr.click").forEach((tr) => { tr.addEventListener("click", () => openAccount(tr.dataset.id)); tr.addEventListener("keydown", (e) => { if (e.key === "Enter") openAccount(tr.dataset.id); }); });
      const more = host.querySelector("[data-more]"); if (more) more.addEventListener("click", () => { all = !all; draw(); });
      markSortable(host.querySelector("table"), () => draw());
    };
    draw();
  }

