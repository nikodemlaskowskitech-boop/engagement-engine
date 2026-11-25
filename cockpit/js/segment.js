  // ---------------------------------------------------------------- segment
  // The part of the book someone is looking at. Two sides: people (manager,
  // team = the region the owner sits in, owner) and accounts (region = where the
  // customer is, tier). They differ when a CSM covers an account in another
  // region. Every view except the single-account timeline draws only the
  // accounts in it. Remembered in this browser only.
  const REGIONS = ["UK&I", "DACH", "France & Benelux", "Nordics"], TIERS = ["Enterprise", "Scale", "Self-service"];
  const managerName = (m) => (m || "").replace(/^mgr_/, "").replace(/^./, (c) => c.toUpperCase());
  const MANAGERS = [...new Set(D.team.map((t) => t.manager))].sort();
  for (const m of MANAGERS) ownerName[m] = managerName(m) + " (manager)";
  const seg = { manager: "", team: "", owner: "", region: "", tier: "", acc: "" };   // acc: pinned accounts, comma-separated
  const teamById = Object.fromEntries(D.team.map((t) => [t.owner_id, t]));
  const homeOf = Object.fromEntries(D.team.map((t) => [t.owner_id, t.region]));   // owner -> the team they sit in
  const crossRegion = (a) => !!homeOf[a.owner_id] && homeOf[a.owner_id] !== a.region;
  const SEG_KEY = "cockpit-seg" + (PAGE ? "-" + PAGE.person : "");
  if (PAGE && PAGE.role === "manager") seg.manager = PAGE.person;                 // a manager opens on their team
  if (PAGE && (PAGE.role === "csm" || PAGE.role === "member")) seg.owner = PAGE.person;   // a CSM on their own book
  // the search page opens in this person's order (24 Nov 2026): a leader or manager by ARR, a CSM by best match
  if (PAGE && PAGE.role) document.addEventListener("click", (e) => {
    const a = e.target.closest && e.target.closest('a[href="search.html"]');
    if (a) a.setAttribute("href", "search.html?view=" + encodeURIComponent(PAGE.role));
  }, true);
  // arriving through the switcher: the person's own defaults, never a filter saved earlier
  const SWITCHED = (() => { try { const s = sessionStorage.getItem("cockpit-switched"); sessionStorage.removeItem("cockpit-switched"); return !!s; } catch (e) { return false; } })();
  if (!SWITCHED) try { const saved = JSON.parse(localStorage.getItem(SEG_KEY) || "{}"); for (const k in seg) if (typeof saved[k] === "string") seg[k] = saved[k]; } catch (e) { /* no storage here */ }
  const saveSeg = () => { try { localStorage.setItem(SEG_KEY, JSON.stringify(seg)); } catch (e) { /* no storage here */ } };
  const segOn = () => Object.values(seg).some(Boolean);
  // whose book is open: a named owner, or a pool (its team + the Scale tier)
  // An owner's work: tasks routed to them, plus, for a pool member, the tasks
  // on their own accounts still waiting in the pool queue (anyone may take those)
  const workOf = (o) => { const t = teamById[o] || {};
    return (D.tasks || []).filter((x) => x.routed_to === o || (t.owner_type === "member" && x.routed_to === t.pool && (byId[x.account_id] || {}).owner_id === o)); };
  const KIND_ORDER = { csm: 0, member: 1, pool: 2 };
  const kindName = (k) => ({ csm: "CSMs", member: "pool members", pool: "pools" }[k] || "owners");
  const segOwner = () => seg.owner || (seg.team && seg.tier === "Scale" ? (D.team.find((t) => t.owner_type === "pool" && t.region === seg.team) || {}).owner_id || "" : "");
  const pinned = () => seg.acc ? seg.acc.split(",").filter(Boolean) : [];
  // the segment without the pins (what the Find box offers), and with them (what every tab draws)
  const inSegBase = (a) => !!a && (!seg.manager || a.manager === seg.manager) && (!seg.team || homeOf[a.owner_id] === seg.team) && (!seg.region || a.region === seg.region)
    && (!seg.tier || a.tier === seg.tier) && (!seg.owner || a.owner_id === seg.owner);
  // pinned accounts win: while any are pinned, every tab shows exactly them, whatever the other filters say
  const inSeg = (a) => !!a && (seg.acc ? pinned().includes(a.account_id) : inSegBase(a));
  const inSegId = (id) => inSeg(byId[id]);
  const segName = () => {
    const people = seg.owner ? ownerName[seg.owner] : [seg.manager ? managerName(seg.manager) + "'s team" : "", seg.team ? seg.team + " team" : ""].filter(Boolean).join(", ");
    const n = pinned().length;
    const parts = [people, seg.region ? seg.region + " accounts" : "", seg.tier, n ? (n === 1 ? (byId[pinned()[0]] || {}).account_name || "1 pinned account" : n + " pinned accounts") : ""].filter(Boolean);
    return parts.length ? parts.join(", ") : "The whole book";
  };
  const fillSelect = (node, opts, value) => {
    node.innerHTML = ""; node.append(new Option("All", ""));
    for (const [v, l] of opts) node.append(new Option(l, v));
    node.value = opts.some(([v]) => v === value) ? value : "";
  };
  // Narrowing filters: each list offers only the values that still have
  // accounts once the other filters apply (Mia picked: only her manager, her
  // team, the regions and tiers of her accounts).
  const FACET = {
    manager: (a) => a.manager,
    team: (a) => homeOf[a.owner_id],
    owner: (a) => ["csm", "member"].includes((teamById[a.owner_id] || {}).owner_type) ? a.owner_id : null,   // named people only: a pool is Team + Scale
    region: (a) => a.region,
    tier: (a) => a.tier,
  };
  const facetOptions = (k) => {
    const others = Object.keys(FACET).filter((x) => x !== k && seg[x]);
    const vals = new Set();
    for (const a of D.accounts) if (others.every((x) => FACET[x](a) === seg[x])) { const v = FACET[k](a); if (v) vals.add(v); }
    return vals;
  };
  const LABEL = { manager: managerName, team: (v) => v, owner: (v) => ownerName[v] || v, region: (v) => v, tier: (v) => v };
  const ORDER = { manager: MANAGERS, team: REGIONS, region: REGIONS, tier: TIERS };
  function drawSegbar() {
    for (let pass = 0; pass < 2; pass++) {               // twice: a value dropped in pass 1 can widen the others
      for (const k of Object.keys(FACET)) {
        const vals = facetOptions(k);
        const order = ORDER[k] || D.team.map((t) => t.owner_id);
        fillSelect($("seg-" + k), order.filter((v) => vals.has(v)).map((v) => [v, LABEL[k](v)]), seg[k]);
        seg[k] = $("seg-" + k).value;                    // drop a value the others ruled out
      }
    }
    const n = D.accounts.filter(inSeg).length;
    const o = segOwner() && teamById[segOwner()];   // a named CSM, or a pool (team + Scale)
    $("seg-count").textContent = (segOn() ? `${n} of ${D.accounts.length} accounts` : `${n} accounts`)
      + (o ? ` · ${o.owner_name} sits in ${o.region}, manager ${managerName(o.manager)}` : "")
      + ((seg.manager || seg.team) && !seg.owner && !seg.tier ? " · Self-service has no owner, so it's left out" : "");
    $("seg-reset").hidden = !segOn();
    $("segbar").classList.toggle("on", segOn());
    // pinned accounts, one chip each: x unpins
    const pins = $("seg-pins"); pins.innerHTML = "";
    for (const id of pinned()) {
      const c = document.createElement("span"); c.className = "pin";
      c.textContent = (byId[id] || {}).account_name || id;
      const x = document.createElement("button"); x.className = "pin-x"; x.textContent = "×"; x.setAttribute("aria-label", `Unpin ${c.textContent}`);
      x.addEventListener("click", () => { seg.acc = pinned().filter((p) => p !== id).join(","); saveSeg(); redraw(); });
      c.append(x); pins.append(c);
    }
  }
  function redraw() { drawSegbar(); drawOnboarding(); drawTeam(); drawBook(); drawSieve(); drawRenewals(); drawOutcomes(); drawTasks(); drawMyBook(); drawLeadership(); drawCase();
    if (typeof drawPicks === "function") {
      drawPicks();
      // the segment changed and the open account isn't in it: open one that is
      if (sel.value && segOn() && !inSegId(sel.value) && picks.length) { sel.value = picks[0].id; drawTimeline(picks[0].id); }
    }
    if (sel.value) { drawNext(sel.value); drawForecast(sel.value); drawHandover(sel.value); } }   // who may plan or forecast depends on the segment
  for (const k of Object.keys(FACET)) $("seg-" + k).addEventListener("change", (e) => { seg[k] = e.target.value; saveSeg(); redraw(); });
  $("seg-reset").addEventListener("click", () => { for (const k in seg) seg[k] = ""; saveSeg(); redraw(); });
  // phone: the filters fold behind one button
  $("seg-toggle").addEventListener("click", () => {
    const open = $("segbar").classList.toggle("open"); $("seg-toggle").setAttribute("aria-expanded", String(open));
  });
  document.documentElement.lang = document.documentElement.lang || "en";
  // keyboard and screen readers: every clickable chart mark and row can be reached with Tab
  // and opened with Enter or Space, like a button (a pass after each redraw)
  const CLICKABLE = 'svg [style*="cursor:pointer"], svg [style*="cursor: pointer"], tr.click';
  const reachable = () => document.querySelectorAll(CLICKABLE).forEach((n) => {
    if (n.hasAttribute("tabindex")) return;
    n.setAttribute("tabindex", "0");
    if (n.tagName.toLowerCase() !== "tr") n.setAttribute("role", "button");
    if (!n.getAttribute("aria-label")) { const t = n.querySelector && n.querySelector("title, text"); if (t) n.setAttribute("aria-label", t.textContent.trim()); }
  });
  let reachQueued = false;
  new MutationObserver(() => { if (!reachQueued) { reachQueued = true; setTimeout(() => { reachQueued = false; reachable(); }, 0); } })
    .observe(document.body, { childList: true, subtree: true });
  document.addEventListener("keydown", (e) => {
    const n = e.target;
    if ((e.key === "Enter" || e.key === " ") && n instanceof Element && n.matches(CLICKABLE)) {
      e.preventDefault();
      if (n.tagName.toLowerCase() === "tr" && n.dataset.id && e.key === "Enter") return;   // account rows already open on Enter
      n.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    }
  });

  // tooltip
  const tip = $("tip");
  const showTip = (ev, html) => { tip.innerHTML = html; tip.hidden = false; moveTip(ev); };
  const moveTip = (ev) => {
    const w = tip.offsetWidth, h = tip.offsetHeight;
    let x = ev.clientX + 14, y = ev.clientY + 14;
    if (x + w > innerWidth - 8) x = ev.clientX - w - 14;
    if (y + h > innerHeight - 8) y = ev.clientY - h - 14;
    tip.style.left = Math.max(8, x) + "px"; tip.style.top = Math.max(8, y) + "px";
  };
  const hideTip = () => { tip.hidden = true; };
  const hover = (node, html) => {
    // the same words for a screen reader (the tooltip is pointer-only)
    if (!node.getAttribute("aria-label")) { const t = document.createElement("div"); t.innerHTML = String(html).replace(/<br\s*\/?>/gi, " · "); node.setAttribute("aria-label", t.textContent.trim()); }
    node.addEventListener("pointerenter", (e) => showTip(e, html));
    node.addEventListener("pointermove", moveTip);
    node.addEventListener("pointerleave", hideTip);
  };

  // tabs, with a bookmarkable #token per view
  // CS team folded into Operational (22 Nov 2026, Operational PR2): a page whose role folds the
  // tab keeps the section (roles.py [roles.*] fold) and shows it inside Operational, under its own band
  if (PAGE && !$("tab-team") && $("view-team") && $("op-team-slot")) {
    const sec = $("view-team");
    sec.className = "folded"; sec.removeAttribute("role"); sec.removeAttribute("aria-labelledby"); sec.hidden = false;
    $("op-team-slot").insertAdjacentHTML("beforeend", '<h2 class="band">The team: speed to call and room to take more</h2>');
    $("op-team-slot").append(sec);
  }
  // "Did a churn call help?" sits where the call is decided (22 Nov 2026, Operational PR3): on a
  // role page with Operational, under the funnel; "Did it work?" keeps the rules
  if (PAGE && $("oc-calls-panel") && $("op-calls-slot") && $("view-tasks") && !(PAGE.role === "csm" || PAGE.role === "member")) $("op-calls-slot").append($("oc-calls-panel"));
  const views = PAGE ? PAGE.tabs.slice() : ["overview", "lead", "mybook", "tasks", "outcomes", "timeline", "onboarding", "team", "book"];
  // counted, never typed: "Ten views" stayed after a tab moved to its own page (16 Nov 2026 review)
  $("n-views").textContent = ["One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve"][views.length - 1] || String(views.length);
  if (PAGE) {                                    // tabs this role doesn't have aren't in the file at all (roles.py)
    const nav = document.querySelector("nav[role=tablist]");
    for (const v of views) nav.append($("tab-" + v));
    const sw = $("page-switch");
    $("page-switch-wrap").hidden = false;
    if (PAGE.sandbox && PAGE.demo && PAGE.demo.leader_page) {      // public site: two doors, the list one level down
      const here = location.pathname.split("/").pop() || "index.html";
      const onLeader = PAGE.role === "leader", onCsm = here === PAGE.demo.csm_page;
      $("door-leader").href = PAGE.demo.leader_page + "#lead";
      $("door-csm").href = PAGE.demo.csm_page || "#";
      $("door-csm").title = PAGE.demo.csm_name ? `${PAGE.demo.csm_name}'s book` : "";
      if (onLeader) $("door-leader").setAttribute("aria-current", "page");
      if (onCsm) $("door-csm").setAttribute("aria-current", "page");
      $("doors").hidden = false;
      $("page-switch-label").textContent = onLeader || onCsm ? "" : "Viewing as";
    }
    // Switch person without leaving this page: a published artifact gives only its main page
    // the database, so the chosen person's file is fetched and written into this one (each file
    // still holds only that person's data). Offline, fetch can't read files: open it instead.
    // the account open on the timeline goes with the switch (its id only: the new page opens it
    // if it's on that page, else says so); the new page starts on its own defaults
    const carry = () => (current === "timeline" && sel.value ? "#" + sel.value : "");
    sw.addEventListener("change", async () => {
      if (!sw.value) return;
      try { sessionStorage.setItem("cockpit-switched", "1"); sessionStorage.setItem("cockpit-pins", seg.acc); } catch (e) { /* no storage here */ }
      if (PAGE.sandbox) { location.href = sw.value + carry(); return; }   // public site: no shared database to keep
      try {
        const r = await fetch(sw.value);
        if (!r.ok) throw new Error(String(r.status));
        const html = await r.text();
        if (window.__cockpitLeave) window.__cockpitLeave();
        try { history.replaceState(null, "", location.pathname + location.search + carry()); } catch (e) { /* not allowed in some frames */ }
        document.open(); document.write(html); document.close();
      } catch (e) { location.href = sw.value + carry(); }
    });
  }
  let current = views[0];
  const show = (v) => {
    if (!views.includes(v)) v = views[0];
    current = v;
    for (const w of views) {
      $("view-" + w).hidden = w !== v;
      $("tab-" + w).setAttribute("aria-selected", String(w === v));
      $("tab-" + w).tabIndex = w === v ? 0 : -1;               // one Tab stop; arrows move between tabs
    }
    try { history.replaceState(null, "", "#" + v); } catch (e) { /* not allowed in some frames */ }
    hideTip();
    if (v === "lead" && typeof drawLeadership === "function") drawLeadership();   // its trends draw at the panel's real width
  };
  document.querySelectorAll("nav button").forEach((b) => b.addEventListener("click", () => { $("tl-back").hidden = true; show(b.dataset.view); }));
  // arrow keys move between tabs (the tablist pattern): Left/Right, Home/End
  document.querySelector("nav[role=tablist]").addEventListener("keydown", (e) => {
    const tabs = [...document.querySelectorAll("nav [role=tab]")].filter((b) => !b.hidden && b.isConnected);
    const i = tabs.indexOf(document.activeElement); if (i < 0) return;
    const j = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: tabs.length - 1 }[e.key];
    if (j == null) return;
    e.preventDefault();
    const t = tabs[(j + tabs.length) % tabs.length]; t.focus(); t.click();
  });

