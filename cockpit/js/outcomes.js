  // ---------------------------------------------------------------- did it work? (backtest)
  const OUT_COLS = [
    { label: "Account", get: (r) => nameOf(r.account_id) },
    { label: "Tier", get: (r) => esc(r.tier) },
    { label: "Flag", get: (r) => esc(r.flag_status || "None") },
    { label: "Outcome", get: (r) => esc(r.outcome) },
    { label: "Renewal", r: true, get: (r) => fmt(r.renewal_date) },
    { label: "Warning", r: true, get: (r) => (r.flagged && r.warning_days != null ? `${r.warning_days} d` : "—") },
    { label: "Usage now", r: true, get: (r) => (r.util_now != null ? `${Math.round(r.util_now)}%` : "—") },
  ];
  function listOutcomes(title, items, short) { accountList($("oc-list"), $("oc-list-title"), title, items, OUT_COLS, short); }
  // Below manager level the page holds only the team's totals, never a row per account:
  // with account assignment driving commission, per-owner results invite comparing luck of the draw.
  function drawOutcomesTotal() {
    if ($("oc-rules-panel")) $("oc-rules-panel").hidden = true;   // team totals: the rules are the managers' call
    if ($("oc-why-panel")) $("oc-why-panel").hidden = true;
    const T = D.outcomes_total, LATE = D.settings.too_late_days;
    $("oc-late").textContent = LATE;
    for (const id of ["oc-list", "oc-list-title"]) $(id).innerHTML = "";
    if (!T.renewals) { for (const id of ["oc-stats", "oc-matrix", "oc-warning", "oc-calls", "oc-value", "oc-value-finding", "oc-rules", "oc-rules-note"]) $(id).innerHTML = "";
      $("oc-finding").textContent = "Your team: no renewals in the six months after the data."; return; }
    $("oc-finding").textContent = `Your team: the churn flags caught ${T.tp} of the ${T.lost} accounts that churned or shrank at renewal (${pct(T.tp, T.lost)}%), a median of ${T.median_warning ?? "—"} days before. Team totals only: results by owner are for managers.`;
    $("oc-stats").innerHTML = [
      ["Caught (recall)", `${T.tp} <small>of ${T.lost} · ${pct(T.tp, T.lost)}%</small>`],
      ["Right when flagged (precision)", `${T.tp} <small>of ${T.flagged} · ${pct(T.tp, T.flagged)}%</small>`],
      ["Median warning", `${T.median_warning ?? "—"} <small>days</small>`],
      ["Too late to act", `${T.too_late} <small>of ${T.warned} under ${LATE} days</small>`],
    ].map(([k, v]) => `<div class="stat"><div class="k">${k}</div><div class="v">${v}</div></div>`).join("");
    const cell = (n, name, cls) => `<td><span class="cellbtn ${cls}"><span class="v">${n}</span><span class="k">${name}</span></span></td>`;
    $("oc-matrix").innerHTML = `<table class="matrix"><thead><tr><th></th><th>Lost at renewal</th><th>Kept or grew</th></tr></thead><tbody>
      <tr><th>Flagged</th>${cell(T.tp, "Caught", "good")}${cell(T.fp, "False alarm", "warning")}</tr>
      <tr><th>Not flagged</th>${cell(T.fn, "Missed", "critical")}${cell(T.tn, "Quiet, kept", "neutral")}</tr></tbody></table>`;
    $("oc-warning").innerHTML = `<table class="nosort"><thead><tr><th>Days of warning</th><th class="r">Caught</th></tr></thead><tbody>` +
      T.warning_bins.map((b) => `<tr><td>${b.to == null ? b.from + "+" : b.from + "–" + b.to}</td><td class="r num">${b.n}</td></tr>`).join("") + "</tbody></table>";
    const fewT = tooFew(T.calls.map((r) => r.n));
    if (fewT) $("oc-calls-note").textContent = tooFewNote(T.calls.map((r) => callGroup(r.group, r.n))).replace(" Click a row to see the accounts.", "");
    $("oc-calls").innerHTML = `<table class="nosort"><thead><tr><th>Act accounts</th><th class="r">Accounts</th><th class="r">Churned</th><th class="r">Kept whole</th></tr></thead><tbody>` +
      T.calls.map((r) => fewT && !r.small ? `<tr><td>${esc(r.group)}</td><td class="r num">${r.n}</td><td class="r muted" colspan="2">too few to tell</td></tr>` : r.small ? `<tr><td>${esc(r.group)}</td><td class="r num">${r.n}</td><td class="r muted" colspan="2">${r.n < T.min_group ? `fewer than ${T.min_group}: not shown` : "not shown: it would give away the other row"}</td></tr>`
        : `<tr><td>${esc(r.group)}</td><td class="r num">${r.n}</td><td class="r num">${r.churned} (${pct(r.churned, r.n)}%)</td><td class="r num">${r.kept} (${pct(r.kept, r.n)}%)</td></tr>`).join("") + "</tbody></table>";
    if (!T.value.length) { $("oc-value").innerHTML = ""; $("oc-value-finding").textContent = "The renewal outcomes have no renewal_arr column, so the value comparison is off."; return; }
    $("oc-value-finding").textContent = "";
    $("oc-value").innerHTML = `<table class="nosort"><thead><tr><th>Status today</th><th class="r">Renewals</th><th class="r">ARR</th><th class="r">At risk (rule)</th><th class="r">Really lost</th><th class="r">Lost / at risk</th></tr></thead><tbody>` +
      T.value.map((g) => g.small ? `<tr><td>${g.group === "All renewals" ? "<b>All renewals</b>" : esc(g.group)}</td><td class="r num">${g.n}</td><td class="r muted" colspan="4">${g.n < T.min_group ? `fewer than ${T.min_group}: not shown` : "not shown: it would give away a small group"}</td></tr>` : `<tr><td>${g.group === "All renewals" ? "<b>All renewals</b>" : esc(g.group)}</td><td class="r num">${g.n}</td><td class="r num">${money(g.arr)}</td><td class="r num">${g.at_risk ? money(g.at_risk) : "–"}</td><td class="r num">${money(g.lost)}</td><td class="r num">${g.at_risk ? pct(g.lost, g.at_risk) + "%" : "–"}</td></tr>`).join("") + "</tbody></table>";
  }
  // "Did it work?" as the page for rule changes (22 Nov 2026): one line per rule, its evidence
  // against the bar in settings [backtest], and the setting to change when it misses
  function drawRules(nLost, nTp, nFlagged, warn, R) {
    const B = D.settings.rule_bars || {}, LATE = D.settings.too_late_days;
    const late = warn.filter((w) => w < LATE).length;
    const few = (n) => n < 10;
    const rows = [
      ["Churn flags catch the losses", `${nTp} of ${nLost} (${pct(nTp, nLost)}%)`, `${B.min_recall}%+`, few(nLost) ? null : pct(nTp, nLost) >= B.min_recall, "loosen [churn] thresholds: a higher utilisation line, a smaller WAU drop"],
      ["A flag means a real loss", `${nTp} of ${nFlagged} (${pct(nTp, nFlagged)}%)`, `${B.min_precision}%+`, few(nFlagged) ? null : pct(nTp, nFlagged) >= B.min_precision, "tighten [churn] thresholds, or keep them and accept more calls"],
      ["Warning comes early enough", `${late} of ${warn.length} under ${LATE} days (${pct(late, warn.length)}%)`, `${B.max_too_late}% or less`, few(warn.length) ? null : pct(late, warn.length) <= B.max_too_late, "an earlier signal: a shorter trend window in [churn]"],
      // ARR at risk vs lost is not a rule to tune (22 Nov 2026, Nikodem): at Act CS is meant to
      // save part of it, so lost < at risk is the work showing, not a miscalibration. It stays below
      // as a lesson-learned table.
    ];
    $("oc-rules").innerHTML = `<table class="nosort"><thead><tr><th>Rule</th><th class="r">Evidence</th><th class="r">Bar</th><th>Verdict</th></tr></thead><tbody>` +
      rows.map(([k, ev, bar, ok, change]) => `<tr><td>${esc(k)}</td><td class="r num">${ev}</td><td class="r">${esc(bar)}</td><td>${ok == null ? '<span class="muted">too few to tell</span>' : ok ? "Keep" : `<b>Change</b>: ${esc(change)}`}</td></tr>`).join("") + "</tbody></table>";
    const changes = rows.filter((r) => r[3] === false).length;
    $("oc-rules-note").textContent = `${segOn() ? segName() + ": " : ""}${changes ? `${changes} of ${rows.length} rules miss their bar.` : "every rule meets its bar."} The bars are in settings.toml [backtest]; a change shows here after the next run. Fewer than 10 accounts behind a line: too few to tell.`;
  }
  // Why they left (22 Nov 2026, the data lab): the closed-lost reason, the ARR it took, and how
  // many of those accounts the churn flags had flagged beforehand
  function drawLossReasons(lost) {
    const panel = $("oc-why-panel");
    const W = lost.filter((r) => r.reason);
    panel.hidden = !W.length;
    if (!W.length) return;
    const by = {};
    for (const r of W) (by[r.reason] = by[r.reason] || []).push(r);
    const rows = Object.entries(by).map(([k, xs]) => ({ k, xs, arr: xs.reduce((t, r) => t + (r.lost_arr || 0), 0), seen: xs.filter((r) => r.flagged).length }))
      .sort((a, b) => b.arr - a.arr || b.xs.length - a.xs.length);
    const blind = rows.filter((r) => r.xs.length >= 3).sort((a, b) => a.seen / a.xs.length - b.seen / b.xs.length)[0];
    $("oc-why-finding").textContent = `${segOn() ? segName() + ": " : ""}"${rows[0].k.replace(/^./, (c) => c.toUpperCase())}" took the most, ${money(rows[0].arr)} across ${rows[0].xs.length} ${rows[0].xs.length === 1 ? "account" : "accounts"}` +
      (blind && blind.seen < blind.xs.length ? `; the flags saw "${blind.k}" coming least often, ${blind.seen} of ${blind.xs.length}.` : ".");
    $("oc-why").innerHTML = `<table class="nosort"><thead><tr><th>Reason</th><th class="r">Accounts</th><th class="r">ARR lost</th><th class="r">Flagged beforehand</th></tr></thead><tbody>` +
      rows.map((r) => `<tr class="click" tabindex="0" data-k="${esc(r.k)}"><td>${esc(r.k)}</td><td class="r num">${r.xs.length}</td><td class="r num">${money(r.arr)}</td><td class="r num">${r.seen} of ${r.xs.length}${r.xs.length >= 3 ? ` (${pct(r.seen, r.xs.length)}%)` : ""}</td></tr>`).join("") + "</tbody></table>";
    $("oc-why").querySelectorAll("tr.click").forEach((tr) => tr.addEventListener("click", () => listOutcomes(`Lost: ${tr.dataset.k}`, by[tr.dataset.k])));
  }
  function drawOutcomes() {
    if (!$("view-outcomes")) return;                     // not on this person's page
    if (D.outcomes_total) return drawOutcomesTotal();
    const R = D.renewals.filter((r) => inSegId(r.account_id)), LATE = D.settings.too_late_days;
    $("oc-late").textContent = LATE;
    if (!R.length) {
      for (const id of ["oc-stats", "oc-matrix", "oc-warning", "oc-calls", "oc-list", "oc-value", "oc-value-finding", "oc-rules", "oc-rules-note", "oc-why", "oc-why-finding"]) $(id).innerHTML = "";
      $("oc-finding").textContent = D.renewals.length ? `${segName()}: no renewals in the six months after the data.`
        : "No renewal outcomes yet: run .\\run_all.ps1 -Regenerate to create them.";
      return;
    }
    const lost = R.filter((r) => r.lost_revenue), flagged = R.filter((r) => r.flagged);
    const tp = R.filter((r) => r.flagged && r.lost_revenue), fn = R.filter((r) => !r.flagged && r.lost_revenue);
    const fp = R.filter((r) => r.flagged && !r.lost_revenue), tn = R.filter((r) => !r.flagged && !r.lost_revenue);
    const warn = tp.map((r) => r.warning_days).filter((w) => w != null), med = warn.length ? Math.round(median(warn)) : null;
    $("oc-finding").textContent = `${segOn() ? segName() + ": the" : "The"} churn flags caught ${tp.length} of the ${lost.length} accounts that churned or shrank at renewal (${pct(tp.length, lost.length)}%), a median of ${med} days before.`;
    const stats = [
      ["Caught (recall)", `${tp.length} <small>of ${lost.length} · ${pct(tp.length, lost.length)}%</small>`],
      ["Right when flagged (precision)", `${tp.length} <small>of ${flagged.length} · ${pct(tp.length, flagged.length)}%</small>`],
      ["Median warning", `${med ?? "—"} <small>days</small>`],
      ["Too late to act", `${warn.filter((w) => w < LATE).length} <small>of ${warn.length} under ${LATE} days</small>`],
    ];
    $("oc-stats").innerHTML = stats.map(([k, v]) => `<div class="stat"><div class="k">${k}</div><div class="v">${v}</div></div>`).join("");
    drawRules(lost.length, tp.length, flagged.length, warn, R);
    drawLossReasons(lost);

    // the 2x2: flagged or not, lost or kept
    const cell = (items, name, cls) => `<td><button class="cellbtn ${cls}" data-name="${name}"><span class="v">${items.length}</span><span class="k">${name}</span></button></td>`;
    $("oc-matrix").innerHTML = `<table class="matrix"><thead><tr><th></th><th>Lost at renewal</th><th>Kept or grew</th></tr></thead><tbody>
      <tr><th>Flagged</th>${cell(tp, "Caught", "good")}${cell(fp, "False alarm", "warning")}</tr>
      <tr><th>Not flagged</th>${cell(fn, "Missed", "critical")}${cell(tn, "Quiet, kept", "neutral")}</tr></tbody></table>`;
    const groups = { Caught: tp, "False alarm": fp, Missed: fn, "Quiet, kept": tn };
    $("oc-pinbar").hidden = true;
    document.querySelectorAll("#oc-matrix .cellbtn").forEach((b) => {
      const items = groups[b.dataset.name];
      // hover: who is in it; click: the list (it stays selected) and a way to carry them to every tab
      const names = items.slice(0, 6).map((r) => nameOf(r.account_id)).join("<br>");
      hover(b, `<b>${b.dataset.name}: ${items.length} account${items.length === 1 ? "" : "s"}</b>` + (items.length ? `<br>${names}${items.length > 6 ? `<br>and ${items.length - 6} more` : ""}<br>Click to list them` : ""));
      b.setAttribute("aria-pressed", "false");
      b.addEventListener("click", () => {
        document.querySelectorAll("#oc-matrix .cellbtn").forEach((x) => { x.classList.toggle("on", x === b); x.setAttribute("aria-pressed", String(x === b)); });
        listOutcomes(b.dataset.name, items);
        const pin = $("oc-pin");
        $("oc-pinbar").hidden = !items.length;
        pin.textContent = `Show only these ${items.length} account${items.length === 1 ? "" : "s"} on every tab, starting with Leadership`;
        pin.onclick = () => { seg.acc = items.map((r) => r.account_id).join(","); saveSeg(); redraw(); show("lead"); window.scrollTo(0, 0); };
        $("oc-list-title").scrollIntoView({ behavior: "smooth", block: "start" });
      });
    });

    // warning time, in 30-day buckets
    const edges = [0, 30, 60, 90, 120, 150, 180], host = $("oc-warning"); host.innerHTML = "";
    const bins = edges.map((e, i) => ({ from: e, to: edges[i + 1], items: tp.filter((r) => r.warning_days != null && r.warning_days >= e && (edges[i + 1] == null || r.warning_days < edges[i + 1])) }));
    const W = fitW(340, 300), H = 170, maxN = Math.max(1, ...bins.map((b) => b.items.length)), bw = W / bins.length;
    const svg = el("svg", { viewBox: `0 0 ${W} ${H + 34}`, role: "img", "aria-label": "Warning time histogram" }, host);
    bins.forEach((b, i) => {
      const h = ((H - 20) * b.items.length) / maxN, x = i * bw + 6;
      const g = el("g", { style: "cursor:pointer" }, svg);
      el("rect", { x, y: H - h, width: bw - 12, height: h, rx: 2, fill: b.from < LATE ? "var(--critical)" : "var(--viz-measure)" }, g);
      el("text", { x: x + (bw - 12) / 2, y: H - h - 5, "text-anchor": "middle", class: "val" }, g).textContent = b.items.length;
      el("text", { x: x + (bw - 12) / 2, y: H + 16, "text-anchor": "middle" }, svg).textContent = b.to == null ? `${b.from}+` : isPhone() ? `${b.from}–` : `${b.from}–${b.to}`;
      g.addEventListener("click", () => listOutcomes(`Caught with ${b.to == null ? b.from + "+" : b.from + "–" + b.to} days of warning`, b.items));
    });
    el("text", { x: W / 2, y: H + 32, "text-anchor": "middle" }, svg).textContent = "days of warning";

    // calls: Act accounts with a person, churn with and without a churn call
    const act = R.filter((r) => r.flag_status === "Act" && r.tier !== "Self-service");
    const rowsC = [["Churn call held", act.filter((r) => r.had_churn_call)], ["No churn call", act.filter((r) => !r.had_churn_call)]];
    const fewC = tooFew(rowsC.map(([, xs]) => xs.length));
    if (fewC) $("oc-calls-note").textContent = tooFewNote(rowsC.map(([k, xs]) => callGroup(k, xs.length)));
    $("oc-calls").innerHTML = `<table class="nosort"><thead><tr><th>Act accounts</th><th class="r">Accounts</th><th class="r">Churned</th><th class="r">Kept whole</th></tr></thead><tbody>` +
      rowsC.map(([k, xs]) => fewC ? `<tr class="click" data-k="${k}"><td>${k}</td><td class="r num">${xs.length}</td><td class="r muted" colspan="2">too few to tell</td></tr>` : `<tr class="click" data-k="${k}"><td>${k}</td><td class="r num">${xs.length}</td><td class="r num">${xs.filter((r) => r.outcome === "churned").length} (${pct(xs.filter((r) => r.outcome === "churned").length, xs.length)}%)</td><td class="r num">${xs.filter((r) => !r.lost_revenue).length} (${pct(xs.filter((r) => !r.lost_revenue).length, xs.length)}%)</td></tr>`).join("") + "</tbody></table>";
    document.querySelectorAll("#oc-calls tr.click").forEach((tr) => tr.addEventListener("click", () => listOutcomes(tr.dataset.k, rowsC.find(([k]) => k === tr.dataset.k)[1])));

    // value: the data's ARR at risk next to the ARR really lost, by status
    const V = R.filter((r) => r.renewal_arr != null);
    if (!V.length) { $("oc-value").innerHTML = ""; $("oc-value-finding").textContent = "The renewal outcomes have no renewal_arr column, so the value comparison is off."; }
    else {
      const sumV = (xs, k) => xs.reduce((t, r) => t + (r[k] || 0), 0);
      const groups = ["Act", "Watch", "Shelfware", "No churn flag"].map((g) => [g, V.filter((r) => (r.flag_status || "No churn flag") === g)]).filter(([, xs]) => xs.length);
      groups.push(["All renewals", V]);
      const fl = V.filter((r) => r.flagged), lostF = sumV(fl, "lost_arr"), ruleF = sumV(fl, "data_at_risk"), wholeF = sumV(fl, "arr");
      const lostAll = sumV(V, "lost_arr");
      $("oc-value-finding").textContent = fl.length ? `Of ${money(lostAll)} really lost at renewal, ${money(lostF)} was on accounts the churn flags had flagged and ${money(lostAll - lostF)} on accounts with no churn flag. For the flagged ones the rule said ${money(ruleF)} at risk (their whole ARR: ${money(wholeF)}).` : "No flagged account renewed in the period.";
      $("oc-value").innerHTML = `<table class="nosort"><thead><tr><th>Status today</th><th class="r">Renewals</th><th class="r">ARR</th><th class="r">At risk (rule)</th><th class="r">Really lost</th><th class="r">Lost / at risk</th></tr></thead><tbody>` +
        groups.map(([g, xs]) => { const ar = sumV(xs, "data_at_risk"), lo = sumV(xs, "lost_arr");
          return `<tr class="click" tabindex="0" data-k="${g}"><td>${g === "All renewals" ? "<b>All renewals</b>" : esc(g)}</td><td class="r num">${xs.length}</td><td class="r num">${money(sumV(xs, "arr"))}</td><td class="r num">${ar ? money(ar) : "–"}</td><td class="r num">${money(lo)}</td><td class="r num">${ar ? pct(lo, ar) + "%" : "–"}</td></tr>`; }).join("") + "</tbody></table>";
      document.querySelectorAll("#oc-value tr.click").forEach((tr) => tr.addEventListener("click", () => listOutcomes(tr.dataset.k, groups.find(([k]) => k === tr.dataset.k)[1])));
    }
    listOutcomes("Missed", fn, true);
  }

