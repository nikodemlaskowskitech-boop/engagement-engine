  // Account search: type part of a name or an ID; accounts in the current
  // segment come first, then flagged ones. Enter or a click opens the timeline.
  function accountSearch(input, list) {
    let items = [], active = -1;
    const close = () => { list.hidden = true; input.setAttribute("aria-expanded", "false"); active = -1; };
    const render = () => {
      const q = input.value.trim().toLowerCase();
      const rank = (a) => (inSegBase(a) ? 0 : 2) + (a.flags.length ? 0 : 1);     // the segment's accounts first
      // the bar's Find offers every account on the page not pinned yet; the timeline's, every account
      const pool = input.id === "seg-find" ? D.accounts.filter((a) => !pinned().includes(a.account_id)) : D.accounts;
      items = pool.filter((a) => !q || a.account_name.toLowerCase().includes(q) || a.account_id.includes(q))
        .sort((a, b) => rank(a) - rank(b) || a.account_name.localeCompare(b.account_name)).slice(0, 8);
      list.innerHTML = items.length ? items.map((a, i) => `<li role="option" id="${input.id}-o${i}" data-id="${esc(a.account_id)}" aria-selected="${i === active}">${esc(a.account_name)}` +
        `<small>${esc(a.account_id)} · ${esc(a.tier)} · ${esc(a.region)}${a.owner ? " · " + esc(a.owner) : ""}${a.flags.length ? " · " + a.flags.map((f) => f.flag + " " + f.status).join(", ") : ""}</small></li>`).join("")
        : `<li class="empty">No account matches</li>`;
      list.hidden = false; input.setAttribute("aria-expanded", "true");
      if (active >= 0) input.setAttribute("aria-activedescendant", `${input.id}-o${active}`); else input.removeAttribute("aria-activedescendant");
    };
    const pick = (id) => {
      close(); input.value = ""; input.blur();
      if (input.id !== "seg-find") return openAccount(id);
      // the bar's Find pins the account: every tab narrows to the pinned ones, until unpinned
      if (!pinned().includes(id)) { seg.acc = [...pinned(), id].join(","); saveSeg(); redraw(); }
      if (current === "timeline") openAccount(id);
    };
    input.addEventListener("input", () => { active = -1; render(); });
    input.addEventListener("focus", render);
    input.addEventListener("blur", () => setTimeout(close, 150));
    input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); if (list.hidden) render(); active = (active + (e.key === "ArrowDown" ? 1 : -1) + items.length) % Math.max(1, items.length); render(); }
      else if (e.key === "Enter") { e.preventDefault(); const a = items[active >= 0 ? active : 0]; if (a) pick(a.account_id); }
      else if (e.key === "Escape") close();
    });
    list.addEventListener("mousedown", (e) => { const li = e.target.closest("li[data-id]"); if (li) { e.preventDefault(); pick(li.dataset.id); } });
  }
  accountSearch($("seg-find"), $("seg-find-list"));
  accountSearch($("tl-find"), $("tl-find-list"));

  // pins carried over from another person's page: keep the ones on this page, count the rest
  let carriedNote = "";
  if (SWITCHED) {
    let carried = [];
    try { carried = (sessionStorage.getItem("cockpit-pins") || "").split(",").filter(Boolean); sessionStorage.removeItem("cockpit-pins"); } catch (e) { /* no storage here */ }
    const kept = carried.filter((id) => byId[id]);
    seg.acc = kept.join(","); saveSeg();
    if (carried.length > kept.length && PAGE)
      carriedNote = `${carried.length - kept.length} of ${carried.length} pinned accounts aren't on ${PAGE.name}'s page, so they were left behind.`;
    redraw();
  }
  const hashToken = (location.hash || "").replace("#", "");
  if (carriedNote) { $("carry-note").textContent = carriedNote; $("carry-note").hidden = false; }
  if (byId[hashToken]) { sel.value = hashToken; drawTimeline(hashToken); show("timeline"); }
  else if (PAGE && /^acc_[A-Za-z0-9_-]+$/.test(hashToken)) {     // an account from another page, or a Slack link
    const note = $("carry-note");
    note.textContent = `${hashToken} isn't on ${PAGE.name}'s page: it belongs to another team, so it was never put in this file.` + (carriedNote ? " " + carriedNote : "");
    note.hidden = false;
    try { history.replaceState(null, "", location.pathname + location.search); } catch (e) { /* not allowed in some frames */ }
    show(views[0]);
  }
  else show(hashToken);

  // The public demo site: who built it and why, and a 3-minute tour (16 Nov 2026, demo review: a
  // visitor saw "a nice dashboard", never who built it or why, nor where to click first).
  // Text and links from settings.toml [demo]; the in-house cockpit has neither.
  const DEMO = PAGE && PAGE.sandbox && PAGE.demo;
  // a walkthrough on the leader's page and on a CSM's (21 Nov 2026, Nikodem: "a popup walkthrough
  // for the CSM and leader page"): the same panel, steps that light up the part they explain
  const csmTour = !!($("view-mybook") && $("view-tasks"));
  const tourOk = !!(DEMO && $("view-timeline") && ($("view-lead") || csmTour));
  if (DEMO) {
    const w = $("who-strip");
    w.innerHTML = `Built by <b>${esc(DEMO.author)}</b><span class="why">, ${esc(DEMO.why)}</span> ` +
      (tourOk ? `<button type="button" class="linkish" id="tour-start">${csmTour ? "Take the 2-minute tour" : "Take the 3-minute tour"}</button> · ` : "") +
      (DEMO.about && DEMO.about.mine ? `<button type="button" class="linkish" id="about-open" aria-expanded="false" aria-controls="about">About this project</button> · ` : "") +
      `<a href="${esc(DEMO.repo_url)}#readme" target="_blank" rel="noopener">How it works (GitHub)</a>`;
    w.hidden = false;
    // About this project (18 Nov 2026, recruiter review): in place under the strip, never a gate
    if (DEMO.about && DEMO.about.mine) {
      const A = DEMO.about, box = $("about"), btn = $("about-open");
      box.innerHTML = [["The problem", A.problem], ["The approach", A.approach], ["What I did", A.mine],
                       ["The technology", A.technology], ["The limits", A.limits]]
        .filter(([, t]) => t).map(([h, t]) => `<div><h3>${esc(h)}</h3><p>${esc(t)}</p></div>`).join("")
        + `<button type="button" class="linkish" id="about-close">Close</button>`;
      const open = (on) => { box.hidden = !on; btn.setAttribute("aria-expanded", String(on)); if (on) box.querySelector("h3").scrollIntoView({ block: "nearest" }); };
      btn.addEventListener("click", () => open(box.hidden));
      $("about-close").addEventListener("click", () => { open(false); btn.focus(); });
      if (/[?&]about\b/.test(location.search)) open(true);        // a link straight to it: ?about
    }
  }
  if (tourOk) {
    // the account with the most ARR at risk at churn Act: one account, end to end
    const churnAct = D.accounts.filter((a) => a.tier !== "Self-service" && a.flags.some((f) => f.flag === "churn" && f.status === "Act"))
      .sort((a, b) => (valueOf(b.flags.find((f) => f.flag === "churn")) || 0) - (valueOf(a.flags.find((f) => f.flag === "churn")) || 0));
    const pick = churnAct[0];
    // One account through the system (18 Nov 2026, recruiter review: "show me one customer going
    // through it"): picked by rule, never by name, so it holds after a regenerate. A churn signal,
    // its task, a call on or after the signal and inside the tier's days, and a known renewal;
    // a renewal kept in full first, then the biggest. The outcome is shown as it is, good or bad.
    const ren = Object.fromEntries((D.renewals || []).map((r) => [r.account_id, r]));
    const J = (D.churn_signals || []).map((s) => ({ s, a: byId[s.account_id], r: ren[s.account_id],
        t: D.tasks.filter((x) => x.account_id === s.account_id && x.flag === "churn").sort((x, y) => (x.sent_on < y.sent_on ? -1 : 1))[0] }))
      .filter(({ s, a, r, t }) => a && a.tier !== "Self-service" && r && t && s.call_date && s.call_date >= s.signal_date
              && s.days_to_call <= REACH(a.tier))
      .sort((x, y) => (y.r.outcome === "renewed") - (x.r.outcome === "renewed") || (y.r.arr || 0) - (x.r.arr || 0))[0];
    const yr = (d) => fmt(d) + (d.slice(0, 4) !== D.as_of.slice(0, 4) ? " " + d.slice(0, 4) : "");
    const journey = J && [
      ["Signal", `${fmt(J.s.signal_date)}: churn ${J.s.status}. ${J.s.reasons.split(";")[0]}`],
      ["Task", `${fmt(J.t.sent_on)}: to ${ownerName[J.t.routed_to] || J.t.routed_to}, "${J.t.action}"`],
      ["Action", `${fmt(J.s.call_date)}: ${J.s.via === "qbr" ? "a QBR" : "a call"} with the customer`],
      ["Reached", `in ${J.s.days_to_call} day${J.s.days_to_call === 1 ? "" : "s"}, inside the ${REACH(J.a.tier)} days set for ${J.a.tier}`],
      // the outcome is after the data ends (as-of), read looking back, like "Did it work?": said so,
      // or "renews in 104 days" on the page and "downsized" here read as a contradiction
      ["Renewal", `${yr(J.r.renewal_date)}, after the data ends: ${J.r.outcome}, ${money(J.r.arr)} to ${money(J.r.renewal_arr)}`],
      ["Measured", `flagged ${J.r.warning_days} days ahead, called, ${J.r.outcome}. A call is no guarantee; the engine measures either way.`],
    ];
    // a CSM's day (Nikodem's route, 18 Nov 2026): where I stand, what's coming, what to pick up,
    // know the account, act. The account shown: the biggest flagged one with documents, else the biggest flagged.
    const me = (PAGE && PAGE.name) || "This CSM";
    const flagged = D.accounts.filter((a) => a.flags.some((f) => ["churn", "expansion"].includes(f.flag)))
      .sort((a, b) => (!!((D.research || {})[b.account_id]) - !!((D.research || {})[a.account_id])) || (b.arr || 0) - (a.arr || 0));
    const mine = flagged[0];
    const CSM_STEPS = [
      { v: "mybook", el: "mb-kpi", h: `Where ${me} stands`, p: `${me}'s book in the leader's own measures: ARR, ARR at risk, the upside kept apart from the risk, ARR met in the last 30 days and renewals kept. Same definitions as the leader's page, so these numbers add up to it. Value, never a count of calls.` },
      { v: "mybook", el: "mb-port-view", h: "Every account, by value at stake", p: "Why each one is flagged, in words; the last and next CS engagement; and an insight from its tickets, emails or call notes. Churn and growth apart, as the leader reports them. An insight is context: it never changes a status." },
      { v: "tasks", el: "tk-week-panel", h: "What's coming", p: "The next 14 days, in value: QBRs, renewals and tasks due, so nothing arrives as a surprise." },
      { v: "tasks", el: "tk-list", h: "What to pick up", p: "The queue, sorted by cash at stake per day left: a big renewal next month comes before a small one this week. Each row says what to do now, and shows the last and next engagement in place." },
      { v: "tasks", slack: true, h: "It arrives in Slack too", p: `Each task also reaches ${me} as a card in Slack: why, what to do, and buttons to take it, log the call (with any promise and its date) or say it's not needed. A click there lands on the account's timeline, tagged "via Slack". Try the buttons here: this preview is drawn from the card the engine posts.` },
      mine && { v: "timeline", acc: mine.account_id, el: "tl-why", h: `Know the account: ${mine.account_name}`, p: "Why now comes first: each flag in words, the money at stake and what to do, beside what the documents say. Then the plan and the renewal forecast, the history with what came of each meeting, the research and the usage." },
      { h: "It rolls up", p: `What ${me} does here is what the leader's page reports: same measures, same accounts. Open it, or ask the documents in plain words.`,
        links: [DEMO.leader_page && ["The leader's page", DEMO.leader_page], ["Ask the documents", "search.html"]].filter(Boolean) },
    ].filter(Boolean);
    const STEPS = csmTour ? CSM_STEPS : [
      J ? { v: "timeline", acc: J.a.account_id, h: `One account through the system: ${J.a.account_name}`,
            p: `From signal to renewal outcome, the way every account moves through the engine. The data ends ${fmt(D.as_of)}; the outcome is read looking back from six months later, as "Did it work?" does.`,
            list: journey }
        : pick && { v: "timeline", acc: pick.account_id, h: `One account, end to end: ${pick.account_name}`, p: "Usage, every CS touch, why the engine flagged it (in words, never a score), the plan and the renewal forecast." },
      // the leader's four places, each step tied to a leader's task (23 Nov 2026, the design review):
      // report up, see where it differs, see what's coming, run the work, change the rules
      views.includes("lead") && RET && { v: "lead", el: "cs-tiles", h: "Report up: Performance",
        p: "What the board asks: gross and net retention, the renewals at risk in the next two quarters and the forecast. Value first, never a count of calls. Click any number to list its accounts." },
      views.includes("lead") && RET && { v: "lead", el: "cs-teams-panel", h: "Is it the same everywhere?",
        p: "The same retention by team. Click a team and every number on the page becomes that team's; click again on a person. Small numbers are greyed, and the headline names money, not a rank." },
      views.includes("lead") && { v: "lead", el: "ld-signals", h: "What's coming: the signals",
        p: "Patterns across accounts before they reach renewal: a new cohort not ramping up, themes customers raise with no action yet. One trend is planted in the demo data, and says so." },
      views.includes("tasks") && { v: "tasks", el: "tk-funnel-panel", h: "Operational: is the work moving?",
        p: "Every task the engine sent to a person, from \"a person is needed\" to the right meeting, per team. Click a stage to list the tasks that stopped before it; the director acts through the team lead." },
      views.includes("tasks") && Object.keys(SLACK_CARDS).length && { v: "tasks", slack: true, h: "Where the work happens: Slack",
        p: "The engine doesn't wait for anyone to open a dashboard: each task reaches its owner as a Slack card, and the clicks (taken, call logged, not needed, a promise) come back as events on the timeline, tagged \"via Slack\". Try this card's buttons: it's drawn from the one the engine posts." },
      views.includes("tasks") && { v: "tasks", el: "op-promises-panel", h: "Where the time goes, and what we owe",
        p: "Promises made to customers and how many are late, then which signals fired this month and the ARR met in 60 days by stage: is effort following the money?" },
      views.includes("outcomes") && { v: "outcomes", el: "oc-rules-panel", h: "Did it work? Keep or change the rules",
        p: "Today's flags held against what really happened at renewal, turned into decisions: each rule against its bar, keep or change, and why customers left, including what no usage signal can see." },
      { h: "A page per person, and the documents", p: "Each person gets their own page, holding only their team's data. Open a CSM's morning page, or ask the tickets, emails and call notes in plain words.",
        links: [DEMO.csm_page && [`${DEMO.csm_name}'s page (a CSM)`, DEMO.csm_page], ["Ask the documents", "search.html"]].filter(Boolean) },
    ].filter(Boolean);
    let at = 0;
    const tour = $("tour");
    const go = (i) => {
      at = Math.max(0, Math.min(STEPS.length - 1, i));
      const s = STEPS[at];
      if (s.acc) { sel.value = s.acc; drawTimeline(s.acc); }
      document.querySelectorAll(".tour-focus").forEach((e) => e.classList.remove("tour-focus"));
      document.body.classList.add("touring");
      if (s.v) {
        show(s.v);
        if (s.slack) {                                   // open the first task's Slack card, once
          const b = document.querySelector("#tk-list [data-slack]");
          if (b && b.getAttribute("aria-expanded") !== "true") b.click();
        }
        const slackCard = s.slack && document.querySelector("#tk-list .slack-prev");
        const el = slackCard || ((s.el && $(s.el) && !$(s.el).hidden) ? $(s.el) : $("view-" + s.v));
        if (el !== $("view-" + s.v)) el.classList.add("tour-focus");
        el.scrollIntoView({ behavior: "smooth", block: "start" });
      }
      tour.innerHTML = `<div class="step">Step ${at + 1} of ${STEPS.length}</div><h3>${esc(s.h)}</h3><p>${esc(s.p)}</p>` +
        (s.list ? `<ol class="journey">${s.list.map(([k, t]) => `<li><b>${esc(k)}</b> ${esc(t)}</li>`).join("")}</ol>` : "") +
        (s.links ? `<p>${s.links.map(([l, href]) => `<a href="${esc(href)}">${esc(l)}</a>`).join(" · ")}</p>` : "") +
        `<div class="row">${at ? '<button type="button" class="small" data-tour="back">Back</button>' : ""}` +
        `${at < STEPS.length - 1 ? '<button type="button" class="small" data-tour="next">Next</button>' : ""}` +
        `<button type="button" class="linkish end" data-tour="close">${at < STEPS.length - 1 ? "Close" : "Done"}</button></div>`;
      tour.hidden = false; document.body.classList.add("touring");
      const first = tour.querySelector('[data-tour="next"]') || tour.querySelector('[data-tour="close"]');
      if (first) first.focus({ preventScroll: true });
    };
    tour.addEventListener("click", (e) => {
      const b = e.target.closest("[data-tour]"); if (!b) return;
      if (b.dataset.tour === "next") go(at + 1); else if (b.dataset.tour === "back") go(at - 1); else end();
    });
    const end = () => { tour.hidden = true; document.body.classList.remove("touring"); document.querySelectorAll(".tour-focus").forEach((e) => e.classList.remove("tour-focus")); };
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !tour.hidden) end(); });
    $("tour-start").addEventListener("click", () => go(0));
    if (/[?&]tour\b/.test(location.search)) go(0);         // a link can open the tour: index.html?tour
  }
})();
</script>
