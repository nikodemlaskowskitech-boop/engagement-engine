  // ---------------------------------------------------------------- My work (18 Nov 2026)
  // A CSM's or pool member's page: the queue to pick up from, ordered by the cash at stake per day
  // left (to the reach deadline at Act, else to the renewal), the account's ARR beside it as a
  // fact and the tie-break. Last and Next open that CS engagement in place, not more columns.
  const stakeOf = (t) => {
    const f = ((byId[t.account_id] || {}).flags || []).find((x) => x.flag === t.flag);
    return { v: (f && f.value) || 0, basis: f && f.value_basis ? f.value_basis.split(":")[0] : "", up: t.flag === "expansion" };
  };
  const daysLeft = (t) => {
    const s = t.status === "Act" && liveT(t) ? reachOf(t.account_id, t.flag) : null;
    if (s && !s.reached) return s.left;
    const a = byId[t.account_id];
    return a && a.renewal_date ? day(a.renewal_date) - day(D.as_of) : 365;
  };
  const urgency = (t) => stakeOf(t).v / Math.max(1, daysLeft(t));      // overdue counts as the last day
  const CS_TOUCH = new Set(["meeting_held", "workshop_attended"]);      // CS engagements, not automated emails
  const lastOf = (id) => (D.touches || []).filter((x) => x.account_id === id && CS_TOUCH.has(x.event) && x.date <= D.as_of)
    .sort((x, y) => (x.date < y.date ? -1 : x.date > y.date ? 1 : 0)).slice(-1)[0];
  const kindOf = (x) => x.event === "workshop_attended" ? `Workshop (${topicName(x.topic)})` : topicName(x.topic);
  function engagementDetail(x, plan) {
    const OUT = (D.settings && D.settings.meeting_outcomes) || {};
    if (plan) return `<b>Next: ${esc(plan.type)}, ${fmt(plan.due)}</b>` +
      (plan.kind === "crm" ? " · booked in the CRM" : ` · set by ${esc(ownerName[plan.by] || plan.by || "the owner")}`) +
      (plan.note && plan.kind !== "crm" ? `<br>${esc(plan.note)}` : "");
    return `<b>Last: ${esc(kindOf(x))}, ${fmt(x.date)}</b> · ${esc(ownerName[x.owner_id] || x.owner_id || "—")}` +
      `<br>Outcome: ${x.outcome ? esc(OUT[x.outcome] || x.outcome) : '<span class="muted">none logged</span>'}` +
      (x.note ? `<br>${esc(x.note)}` : ' · <span class="muted">no note logged</span>');
  }
  // My week (20 Nov 2026, Nikodem: "my current workload with the upcoming engagements, tied to
  // the view where I pick up"): what's coming in the next [cockpit] my_week_days days on the
  // owner's accounts, by date, each with its ARR: QBRs and renewals from the CRM, the owner's
  // next steps, and Act tasks' reach deadlines. In value, like the rest of the CSM side.
  const WEEK_DAYS = (D.settings && D.settings.my_week_days) || 14;
  function weekAhead(o) {
    // the owner's accounts, looked up here: this runs in the first draw, before accountsOf exists
    const isPool = (teamById[o] || {}).owner_type === "pool";
    const accs = D.accounts.filter((a) => (isPool ? a.pool : a.owner_id) === o), out = [], inWin = (d) => d && day(d) > RUN && day(d) <= RUN + WEEK_DAYS;
    for (const a of accs) {
      if (inWin(a.next_qbr_date)) out.push({ date: a.next_qbr_date, what: "QBR", a });
      if (inWin(a.renewal_date)) out.push({ date: a.renewal_date, what: "Renewal", a });
      const s = (nextSteps[a.account_id] || []).slice(-1)[0];
      if (s && inWin(s.due)) out.push({ date: s.due, what: STEP_TYPES[s.type] || s.type, a, note: s.note });
    }
    for (const t of (D.tasks || []).filter((t) => liveT(t) && t.status === "Act" && accs.some((a) => a.account_id === t.account_id))) {
      const s = reachOf(t.account_id, t.flag);
      if (s && !s.reached && s.left >= 0 && s.left <= WEEK_DAYS) {
        const d = new Date((RUN + Math.max(1, s.left)) * 864e5).toISOString().slice(0, 10);
        out.push({ date: d, what: `Reach by (${t.flag} Act)`, a: byId[t.account_id] || { account_id: t.account_id, arr: 0 } });
      }
    }
    return out.sort((x, y) => x.date.localeCompare(y.date) || (y.a.arr || 0) - (x.a.arr || 0));
  }
  function drawWeek() {
    const o = READY ? segOwner() : null, panel = $("tk-week-panel");
    if (!panel || !o) { if (panel) panel.hidden = true; return; }
    const w = weekAhead(o), arrIn = new Set(w.map((x) => x.a.account_id));
    const arr = [...arrIn].reduce((t, id) => t + ((byId[id] || {}).arr || 0), 0);
    panel.hidden = false;
    $("tk-week-title").textContent = `Next ${WEEK_DAYS} days: ${w.length}`;
    const n = (k) => w.filter((x) => x.what === k).length;
    const hooks = new Set(w.filter((x) => !x.what.startsWith("Reach by") && openHook(growthOf(x.a)) && growthState(x.a).k === "open").map((x) => x.a.account_id)).size;
    $("tk-week-finding").textContent = !w.length ? `Nothing booked or due in the next ${WEEK_DAYS} days.`
      : `${money(arr)} of ARR has something booked or due: ${[["QBR", "QBR"], ["Renewal", "renewal"]].filter(([k]) => n(k)).map(([k, l]) => plural(n(k), l)).concat(
          w.some((x) => x.what.startsWith("Reach by")) ? [plural(w.filter((x) => x.what.startsWith("Reach by")).length, "reach deadline")] : [],
          w.some((x) => !["QBR", "Renewal"].includes(x.what) && !x.what.startsWith("Reach by")) ? [plural(w.filter((x) => !["QBR", "Renewal"].includes(x.what) && !x.what.startsWith("Reach by")).length, "next step")] : []).join(", ")}.` +
        (hooks ? ` ${plural(hooks, "account")} with a growth hook to raise there.` : "");
    shortList($("tk-week"), w, [
      { label: "When", get: (x) => `${fmt(x.date)}<small><br>in ${Math.round(day(x.date) - RUN)} d</small>` },
      { label: "What", get: (x) => `${esc(x.what)}${x.note ? `<small><br>${esc(x.note)}</small>` : ""}` +
          // a growth hook rides on this engagement (25 Nov 2026): the prep reminder, not a task
          (!x.what.startsWith("Reach by") && openHook(growthOf(x.a)) && growthState(x.a).k === "open"
            ? `<small><br><span class="flag good inline"><span class="pill">growth hook to raise</span></span>${esc(growthOf(x.a).status)}</small>` : "") },
      { label: "Account", get: (x) => `<button class="link" data-open="${esc(x.a.account_id)}">${nameOf(x.a.account_id)}</button>` },
      { label: "ARR", r: true, get: (x) => money(x.a.arr) },
    ], "", 5);
    if (!w.length) $("tk-week").innerHTML = "";        // the finding already says so
    $("tk-week").querySelectorAll("[data-open]").forEach((b) => b.addEventListener("click", () => openAccount(b.dataset.open)));
  }
  // ---------------------------------------------------------------- Slack made visible (23 Nov 2026)
  // The card each open task gets in Slack, drawn from D.slack_cards: Block Kit built by the same
  // code that posts it (slack_loop.card_blocks), so the preview can't drift. Its buttons play the
  // loop here: in Slack a click is saved to the engine; in this preview it stays on this page.
  const SLACK_CARDS = D.slack_cards || {};
  const SLACK_EMOJI = { raising_hand: "✋", no_entry_sign: "🚫", telephone_receiver: "📞", handshake: "🤝", leftwards_arrow_with_hook: "↩️" };
  const mrkdwn = (t) => esc(t).replace(/\*([^*\n]+)\*/g, "<b>$1</b>").replace(/(^|\s)_([^_\n]+)_/g, "$1<i>$2</i>")
    .replace(/:([a-z_]+):/g, (m, k) => SLACK_EMOJI[k] || m).replace(/\n/g, "<br>");
  const slackKey = (t) => `${t.account_id}|${t.flag}|${t.status}`;
  const slackBtn = (t) => SLACK_CARDS[slackKey(t)] ? `<br><button class="link" data-slack="${esc(slackKey(t))}" aria-expanded="false"><small>Slack card</small></button>` : "";
  const slackYou = () => PAGE && ownerName[PAGE.person] ? ownerName[PAGE.person].split(" ")[0] : "you";
  const slackPlayed = {};                                    // key -> the card after a click, this page only
  function slackCardHtml(key) {
    const blocks = slackPlayed[key] || SLACK_CARDS[key];
    const ref = JSON.parse(((SLACK_CARDS[key].find((b) => b.type === "actions") || { elements: [] }).elements.find((e) => e.value) || {}).value || "{}");
    const body = blocks.map((b) => b.type === "section" ? `<div class="sec">${mrkdwn(b.text.text)}</div>`
      : b.type === "context" ? `<div class="ctx">${b.elements.map((e) => mrkdwn(e.text)).join(" ")}</div>`
      : b.type === "actions" ? `<div class="acts">${b.elements.map((e) => `<button type="button" class="${e.style === "primary" ? "primary" : ""}" data-act="${esc(e.action_id)}">${esc(e.text.text)}</button>`).join("")}</div>` : "").join("");
    return `<p class="slack-label">A preview of the card ${esc(ownerName[ref.o] || "the owner")} gets in Slack. Try the buttons: in Slack a click is saved to the engine and lands on the account's timeline; here it stays on this page.</p>` +
      `<div class="slack-prev" data-key="${esc(key)}"><div class="who"><i>C</i><b>Cairnpath engine</b> <small>app · ${fmt(D.as_of)}</small></div>${body}<div class="slack-form"></div></div>`;
  }
  function slackAfter(key, kind, ref, a = {}) {
    // the card after a click, as slack_loop's claimed_blocks / call_logged_blocks / declined_blocks draw it
    const head = { type: "section", text: { type: "mrkdwn", text: `*${ref.n}* (${ref.a}) · ${ref.f.toLowerCase() === ref.s.toLowerCase() ? `*${ref.s}*` : `${ref.f} *${ref.s}*`}` } };
    const open = { type: "actions", elements: [{ action_id: "open_timeline", text: { text: "Open timeline" } }] };
    const ctx = (text) => ({ type: "context", elements: [{ text }] });
    const who = `@${slackYou()}`, outs = (D.settings && D.settings.meeting_outcomes) || {}, why = (D.settings && D.settings.decline_labels) || {};
    if (kind === "claim") {
      const blocks = (slackPlayed[key] || SLACK_CARDS[key]).map((b) => b.type === "actions" ? { ...b, elements: b.elements.filter((e) => e.action_id !== "claim") } : b);
      const i = blocks.findIndex((b) => b.type === "actions");
      blocks.splice(i, 0, ctx(`:raising_hand: Taken by ${who}`));
      return blocks;
    }
    if (kind === "log_call") return [head, ctx(`:telephone_receiver: ${ref.f === "qbr" ? "QBR" : "Call"} logged by ${who}: *${outs[a.outcome] || a.outcome}*` +
      (a.note ? ` · _${a.note}_` : "") + (a.promise ? `\n:handshake: Committed: ${a.promise}, by ${a.by}` : "")), open];
    if (kind === "decline") return [head, ctx(`:no_entry_sign: Not needed, says ${who}: *${why[a.reason] || a.reason}${a.reason === "later" && a.until ? ` until ${a.until}` : ""}*` + (a.note ? ` · _${a.note}_` : "")), open];
    if (kind === "approve_handback") return [...SLACK_CARDS[key].filter((b) => b.type !== "actions"), ctx(`:leftwards_arrow_with_hook: Hand-back approved by ${who}: back to the pool`)];
    return null;
  }
  function wireSlack(host, cols) {
    const paint = (box, key) => {
      box.innerHTML = slackCardHtml(key);
      const card = box.querySelector(".slack-prev"), ref = JSON.parse(((SLACK_CARDS[key].find((b) => b.type === "actions") || { elements: [] }).elements.find((e) => e.value) || {}).value || "{}");
      const form = card.querySelector(".slack-form");
      card.querySelectorAll("[data-act]").forEach((b) => b.addEventListener("click", () => {
        const act = b.dataset.act;
        if (act === "open_timeline") return openAccount(ref.a);
        if (act === "claim" || act === "approve_handback") { slackPlayed[key] = slackAfter(key, act, ref); return paint(box, key); }
        const outs = (D.settings && D.settings.meeting_outcomes) || {}, why = (D.settings && D.settings.decline_labels) || {};
        form.innerHTML = act === "log_call"
          ? `<label>How did it go? <select name="outcome">${Object.entries(outs).map(([k, v]) => `<option value="${esc(k)}">${esc(v)}</option>`).join("")}</select></label>` +
            `<label>Note <input name="note" maxlength="200" placeholder="What was said, in a line"></label>` +
            `<label>Did we promise something? <input name="promise" maxlength="120" placeholder="e.g. Send the adoption plan"></label>` +
            `<label>By <input name="by" type="date"></label><button class="primary" type="submit">Submit</button>`
          : `<label>Why not needed? <select name="reason">${Object.entries(why).map(([k, v]) => `<option value="${esc(k)}">${esc(v)}</option>`).join("")}</select></label>` +
            `<label>Until (for "later") <input name="until" type="date"></label><label>Note <input name="note" maxlength="200"></label><button class="primary" type="submit">Submit</button>`;
        form.innerHTML = `<form>${form.innerHTML}</form>`;
        form.querySelector("form").addEventListener("submit", (ev) => {
          ev.preventDefault();
          const a = Object.fromEntries(new FormData(ev.target).entries());
          if (act === "log_call" && a.promise && !a.by) { ev.target.querySelector("[name=by]").focus(); return; }   // a promise needs its date, as in Slack
          slackPlayed[key] = slackAfter(key, act, ref, a); paint(box, key);
        });
      }));
    };
    host.querySelectorAll("[data-slack]").forEach((b) => b.addEventListener("click", () => {
      const tr = b.closest("tr"), next = tr && tr.nextElementSibling;
      if (next && next.classList.contains("detail") && next.dataset.which === "slack") { next.remove(); b.setAttribute("aria-expanded", "false"); return; }
      if (next && next.classList.contains("detail")) next.remove();
      const d = document.createElement("tr"); d.className = "detail"; d.dataset.which = "slack";
      d.innerHTML = `<td colspan="${cols}"></td>`; tr.after(d); b.setAttribute("aria-expanded", "true");
      paint(d.firstChild, b.dataset.slack);
    }));
  }
  function drawQueue(items, host) {
    // a recommendation marked not needed leaves the queue while its flag stays the same (22 Nov 2026)
    const off = items.filter((t) => declinedOf(t.account_id, t.flag, t.status));
    items = items.filter((t) => !off.includes(t));
    const Q = items.slice().sort((x, y) => urgency(y) - urgency(x)
      || ((byId[y.account_id] || {}).arr || 0) - ((byId[x.account_id] || {}).arr || 0) || (x.account_id < y.account_id ? -1 : 1));
    $("tk-title").textContent = `My work: ${Q.length}`;
    const live = Q.filter(liveT), stake = live.reduce((n, t) => n + (stakeOf(t).up ? 0 : stakeOf(t).v), 0);
    const up = live.reduce((n, t) => n + (stakeOf(t).up ? stakeOf(t).v : 0), 0);
    const late = live.filter((t) => { const s = t.status === "Act" ? reachOf(t.account_id, t.flag) : null; return s && !s.reached && s.left < 0; }).length;
    // the note on what was marked not needed stays when that emptied the queue (25 Nov 2026: a
    // CSM's last to-do marked not needed left "nothing to pick up" and no trace of it)
    const offNote = off.length ? ` ${plural(off.length, "recommendation")} marked not needed, on ${off.length === 1 ? "its" : "their"} account page.` : "";
    $("tk-finding").textContent = (!live.length ? `${segName()}: nothing to pick up.`
      : `${segName()}: ${live.length} to work, ${money(stake)} at risk${up ? ` and ${money(up)} of upside` : ""}` +
        (late ? `; ${late} past ${late === 1 ? "its" : "their"} days to reach.` : ".")) + offNote;
    host.innerHTML = `<table class="queue keep-order"><thead><tr><th>Account</th><th class="r">ARR</th><th class="r">At stake</th><th>Why</th><th>Reach by</th><th>Last</th><th>Next</th><th>Status</th></tr></thead><tbody>` +
      Q.map((t, i) => {
        const a = byId[t.account_id] || {}, st = stakeOf(t), last = lastOf(t.account_id), plan = a.account_id ? planOf(a) : null;
        const s = t.status === "Act" && liveT(t) ? reachOf(t.account_id, t.flag) : null;
        return `<tr data-row="${i}"><td><button class="link" data-open="${esc(t.account_id)}">${nameOf(t.account_id)}</button><small><br>${esc(a.tier || "not in the CRM")}</small></td>` +
          `<td class="r num">${a.arr ? money(a.arr) : "—"}</td>` +
          `<td class="r num">${st.v ? money(st.v) : "—"}<small><br>${esc(st.up ? "upside" : st.basis.toLowerCase())}</small></td>` +
          `<td>${esc(t.flag)} · ${esc(t.status)}<small><br>${esc(t.action)}</small>${nowAsk(t)}${insightHtml(t.account_id, t.flag)}</td>` +
          `<td>${s ? reachBadge(s) : a.renewal_date ? `<small>renewal ${fmt(a.renewal_date)}</small>` : "–"}</td>` +
          `<td>${last ? `<button class="link" data-last="${i}" aria-expanded="false">${fmt(last.date)}<small><br>${esc(kindOf(last))}</small></button>` : '<span class="muted">none on record</span>'}</td>` +
          `<td>${plan ? `<button class="link" data-next="${i}" aria-expanded="false">${esc(plan.type)}<small><br>${fmt(plan.due)}</small></button>` : '<span class="badge nothing">nothing planned</span>'}</td>` +
          `<td>${badge(t)}${slackBtn(t)}</td></tr>`;
      }).join("") + "</tbody></table>";
    host.querySelectorAll("[data-open]").forEach((b) => b.addEventListener("click", () => openAccount(b.dataset.open)));
    wireInsights(host);
    wireSlack(host, 8);
    // Last / Next: the engagement opens in a row under its task, a second click closes it
    host.querySelectorAll("[data-last], [data-next]").forEach((b) => b.addEventListener("click", () => {
      const i = Number(b.dataset.last ?? b.dataset.next), t = Q[i], tr = b.closest("tr"), open = tr.nextElementSibling;
      const which = b.dataset.last != null ? "last" : "next";
      host.querySelectorAll("[aria-expanded=true]").forEach((x) => x.setAttribute("aria-expanded", "false"));
      if (open && open.classList.contains("detail")) { const same = open.dataset.which === which; open.remove(); if (same) return; }
      const a = byId[t.account_id] || {};
      const d = document.createElement("tr"); d.className = "detail"; d.dataset.which = which;
      d.innerHTML = `<td colspan="8">${which === "last" ? engagementDetail(lastOf(t.account_id)) : engagementDetail(null, planOf(a))}</td>`;
      tr.after(d); b.setAttribute("aria-expanded", "true");
    }));
  }
  async function decide(kind, t, to, control) {
    control.disabled = true; saveError = "";
    try {
      await db.collection("decisions").doc(docId(kind, t)).set({
        kind, account_id: t.account_id, flag: t.flag, status: t.status,
        from_owner: t.routed_to, to_owner: to, manager: seg.manager,
        as_of: D.as_of, decided_at: new Date().toISOString(), decided_by: me,
      });
    } catch (e) {
      saveError = e && e.code === "invalid_argument" ? "You can see these tasks but not change them: ask the cockpit's owner for Contributor access."
        : "That decision wasn't saved. Try again in a moment.";
      control.disabled = false; drawTasks();
    }
  }
  async function undo(id, control) {
    control.disabled = true;
    try { await db.collection("decisions").doc(id).delete(); } catch (e) { saveError = "The undo wasn't saved. Try again in a moment."; drawTasks(); }
  }
  // the database answers later (or never, in the offline file): the list draws first
  let unsubscribe = null;                       // stop listening before the switcher swaps the page
  window.__cockpitLeave = () => { try { if (typeof unsubscribe === "function") unsubscribe(); } catch (e) { /* gone already */ } };
  // The public demo site (roles.py --site, PAGE.sandbox): no shared database, so decisions are kept
  // in this browser's localStorage with the same few calls the page makes on the real one. Each
  // visitor gets a private sandbox; nothing is sent anywhere. Blocked storage = no forms, as offline.
  const SANDBOX_KEY = "cockpit-sandbox-decisions";
  function sandboxDb() {
    const read = () => {
      let v = null;
      try { v = JSON.parse(localStorage.getItem(SANDBOX_KEY) || "{}"); } catch (e) { /* unreadable: start empty */ }
      return v && typeof v === "object" && !Array.isArray(v) ? v : {};
    };
    localStorage.setItem(SANDBOX_KEY, JSON.stringify(read()));                  // throws here if storage is blocked
    const listeners = new Set();
    const snap = () => ({ docs: Object.entries(read()).map(([id, v]) => ({ id, data: () => v })) });
    const emit = () => { const s = snap(); for (const l of listeners) l(s); };
    const write = (f) => { const all = read(); f(all); localStorage.setItem(SANDBOX_KEY, JSON.stringify(all)); setTimeout(emit, 0); };
    addEventListener("storage", (e) => { if (e.key === SANDBOX_KEY || e.key === null) emit(); });   // another tab
    return {
      clear: () => { localStorage.removeItem(SANDBOX_KEY); emit(); },
      collection: () => ({
        doc: (id) => ({ set: async (v) => write((all) => { all[id] = v; }), delete: async () => write((all) => { delete all[id]; }) }),
        onSnapshot: (cb) => { listeners.add(cb); setTimeout(() => cb(snap()), 0); return () => listeners.delete(cb); },
      }),
    };
  }
  (async () => {
    if (PAGE && PAGE.sandbox) {
      try { db = sandboxDb(); me = "sandbox:" + PAGE.person; } catch (e) { db = null; }
      if (db) {
        $("sandbox-note").hidden = false;
        $("sandbox-clear").addEventListener("click", () => db.clear());
      }
    }
    const use = window.claude && window.claude.use;
    if (!db && !use) { dbState = "none"; drawTasks(); if (sel.value) { drawNext(sel.value); drawForecast(sel.value); drawHandover(sel.value); } return; }
    if (!db) try {
      db = await window.claude.use("db");
      const user = await window.claude.use("user");
      me = user ? await user.id() : null;
    } catch (e) { db = null; }
    if (!db) { dbState = "none"; drawTasks(); if (sel.value) { drawNext(sel.value); drawForecast(sel.value); drawHandover(sel.value); } return; }
    dbState = "ready";
    unsubscribe = db.collection("decisions").onSnapshot((snap) => {
      decisions = {}; const fs = [];
      for (const d of snap.docs) {
        const v = d.data(); if (!v) continue;
        if (PAGE && !PAGE_ACCOUNTS.has(v.account_id)) continue;     // review 6 Nov: never another team's decisions
        if (["forecast", "next_step", "handover_confirm", "decline", "outcome_check", "commitment", "commitment_close"].includes(v.kind)) fs.push(v);   // kept whatever the data
        else if (v.as_of === D.as_of) decisions[d.id] = v;           // task decisions on older data: ignored
      }
      forecasts = groupForecasts(fs.filter((v) => v.kind === "forecast"));
      nextSteps = groupForecasts(fs.filter((v) => v.kind === "next_step"));
      handoverConfirms = groupForecasts(fs.filter((v) => v.kind === "handover_confirm"));
      declines = groupForecasts(fs.filter((v) => v.kind === "decline"));
      checks = groupForecasts(fs.filter((v) => v.kind === "outcome_check"));
      comLive = fs.filter((v) => v.kind === "commitment"); comClose = fs.filter((v) => v.kind === "commitment_close");
      if (sel.value && byId[sel.value] && $("tl-why")) drawWhy(byId[sel.value]);
      drawTasks(); drawMyBook(); drawLeadership(); if (sel.value) { drawNext(sel.value); drawForecast(sel.value); drawHandover(sel.value); }
    }, () => { dbState = "none"; drawTasks(); if (sel.value) { drawNext(sel.value); drawForecast(sel.value); drawHandover(sel.value); } });
    drawTasks(); if (sel.value) { drawNext(sel.value); drawForecast(sel.value); drawHandover(sel.value); }
  })();


