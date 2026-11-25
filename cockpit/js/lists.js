  // ---------------------------------------------------------------- shared: account lists
  // A clickable list under a chart: the accounts behind a bar or a cell. A row
  // opens that account's timeline, so every number can be checked by hand.
  // Opening an account from a list jumps to its timeline; a "Back to ..."
  // link returns to the tab (and the spot on it) the click came from.
  let backTo = null;
  function openAccount(id) {
    if (current !== "timeline") backTo = { view: current, y: window.scrollY };
    sel.value = id; drawTimeline(id); pressChips(id); show("timeline"); window.scrollTo(0, 0);
    const b = $("tl-back");
    if (backTo) { b.textContent = "← Back to " + $("tab-" + backTo.view).textContent; b.hidden = false; }
  }
  $("tl-back").addEventListener("click", () => {
    if (!backTo) return;
    const { view, y } = backTo; backTo = null; $("tl-back").hidden = true;
    show(view); window.scrollTo(0, y);
  });
  // say what a click on a row does before it happens
  document.addEventListener("pointerover", (e) => { const tr = e.target.closest && e.target.closest("tr.click"); if (tr && !tr.title) tr.title = "Open this account's timeline"; });
  // short = true for the list a view opens with: two example rows and "View all (n)"
  // (a summary, not a dump); a click on a bar or cell asks for the list, so it gets all of it
  const LIST_ROWS = 10;     // a clicked list shows this many rows first (Nikodem, 12 Nov 2026)
  function accountList(host, titleEl, title, items, cols, short, noun = "account") {
    titleEl.textContent = `${title}: ${items.length} ${noun}${items.length === 1 ? "" : "s"}`;
    if (!items.length) { host.innerHTML = '<p class="note">No accounts here.</p>'; return; }
    // a new list starts in its own order, folded: the first LIST_ROWS (2 for an example list)
    // and "Show all (n)", which turns into "Show fewer" once open
    if (host._listTitle !== title) { delete SORTS[sortKey(host)]; host._listTitle = title; host._open = false; }
    const n = short ? 2 : LIST_ROWS;
    host.classList.add("managed-sort");
    const base = items;
    items = sortedItems(host, base, cols);
    const head = cols.map((c) => `<th${c.r ? ' class="r"' : ""}>${c.label}</th>`).join("");
    // an account that's no longer in the book has no timeline: its row opens nothing
    const shown = host._open || items.length <= n + 1 ? items : items.slice(0, n);   // one hidden row isn't worth a click
    const body = shown.map((it) => (byId[it.account_id] ? `<tr class="click" tabindex="0" data-id="${esc(it.account_id)}">` : "<tr>") +
      cols.map((c) => `<td${c.r ? ' class="r num"' : ""}>${c.get(it)}</td>`).join("") + "</tr>").join("");
    host.innerHTML = `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>` +
      (items.length > n + 1 ? `<button class="link" data-all aria-expanded="${!!host._open}">${host._open ? "Show fewer" : `Show all (${items.length})`}</button>` : "");
    const all = host.querySelector("[data-all]");
    if (all) all.addEventListener("click", () => { host._open = !host._open; accountList(host, titleEl, title, base, cols, short, noun);
      if (!host._open) titleEl.scrollIntoView({ block: "nearest" }); });   // folding back: keep the list's title in sight
    markSortable(host.querySelector("table"), () => accountList(host, titleEl, title, base, cols, short, noun));
    host.querySelectorAll("tr.click").forEach((tr) => {
      tr.addEventListener("click", () => openAccount(tr.dataset.id));
      tr.addEventListener("keydown", (e) => { if (e.key === "Enter") openAccount(tr.dataset.id); });
    });
  }
  const nameOf = (id) => byId[id] ? esc(byId[id].account_name)
    : offBook[id] ? `<span class="muted">${offBook[id].why === "left" ? "An account that has since left" : "No CRM record (handoff gap)"}</span>` : esc(id);
  const MEETING_COLS = [
    { label: "Account", get: (t) => nameOf(t.account_id) },
    { label: "Meeting", get: (t) => esc(topicName(t.topic)) },
    { label: "Held by", get: (t) => esc(ownerName[t.owner_id] || t.owner_id || "") },
    { label: "Date", r: true, get: (t) => fmt(t.date) },
  ];
  const ownerOf = (id) => esc(byId[id] ? (byId[id].owner || "None") : "");
  const churnFlag = (a) => (a.flags.find((f) => f.flag === "churn") || null);
  // one meaning per colour: red = act now, amber = needs attention, neutrals for the rest
  // a task's status badge: Done green, Open neutral until it has waited STALE days (then amber:
  // needs attention), Claimed and Resolved neutral (decision 10 Nov 2026)
  const STALE = D.settings.stale_task_days || 14;
  // text on a coloured fill: near-black on light fills, cream on dark ones, chosen from the fill's
  // real luminance (a CSS variable is read from the page, so it works in both themes)
  const inkOn = (fill) => {
    const m = /var\((--[\w-]+)\)/.exec(fill), hex = m ? getComputedStyle(document.documentElement).getPropertyValue(m[1]).trim() : fill;
    const c = /^#([0-9a-f]{6})$/i.exec(hex); if (!c) return "var(--ink)";
    const lin = (i) => { const v = parseInt(c[1].slice(i, i + 2), 16) / 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
    const L = 0.2126 * lin(0) + 0.7152 * lin(2) + 0.0722 * lin(4);
    const onCream = 0.81 / (L + 0.05), onDark = (L + 0.05) / 0.056;   // cream #E9E3D5 (L 0.76), near-black #0E1311
    if (onDark >= onCream) return "#0E1311";
    return onCream >= 4.5 ? "#E9E3D5" : "#FFFFFF";                      // a mid blue on paper needs pure white (re-review 10 Nov)
  };
  const badge = (t) => `<span class="badge ${esc(t.action_status)}${t.action_status === "Open" && t.age_days >= STALE ? " stale" : ""}"` +
    `${t.action_status === "Open" && t.age_days >= STALE ? ` title="Open for ${t.age_days} days"` : ""}>${esc(t.action_status)}</span>`;
  // Shelfware needs attention like Watch (amber, as on every pill), told apart by its hatching
  const STATUS_COLOR = { Act: "var(--critical)", Watch: "var(--warning)", Shelfware: "url(#hatch-warning)", "No churn flag": "var(--neutral-fill)" };

