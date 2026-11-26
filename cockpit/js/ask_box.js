  // ---------------------------------------------------------------- the Ask box (26 Nov 2026)
  // The public site only (PAGE.ask: AWS told the engine where it is). A CS login signs in on the
  // team's own sign-in page, asks one question about the account open here, and gets an answer
  // from the engine's facts and the account's redacted documents, every sentence with its
  // source, checked. Sign-in: the code flow with PKCE, so the page holds no secret. The token
  // stays in this tab (sessionStorage, gone when the tab closes) and goes to the Ask API only.
  // Nothing typed here is kept, here or there: the function stores counts, never text.
  // Function declarations only, above the first draw: the account page draws before this part
  // of the script has run, and a const here wouldn't exist yet ("before initialization").
  function askConf() { return (PAGE && PAGE.ask) || null; }
  function askGet(k) { try { return sessionStorage.getItem("ask:" + k); } catch (e) { return null; } }
  function askSet(k, v) { try { if (v == null) sessionStorage.removeItem("ask:" + k); else sessionStorage.setItem("ask:" + k, v); } catch (e) { /* private mode */ } }
  function b64url(bytes) { return btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, ""); }
  function askToken() {
    const t = askGet("token"), until = +askGet("until") || 0;
    return t && Date.now() < until ? t : null;
  }
  function askWho() {
    try { const p = JSON.parse(atob(askToken().split(".")[1].replace(/-/g, "+").replace(/_/g, "/"))); return p.email || "a CS login"; }
    catch (e) { return "a CS login"; }
  }
  // to the team's sign-in page; it comes back to the site's address with a one-time code
  async function askSignIn(id) {
    const ASK = askConf();
    const verifier = b64url(crypto.getRandomValues(new Uint8Array(32)));
    const challenge = b64url(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier)));
    const state = b64url(crypto.getRandomValues(new Uint8Array(16)));
    askSet("verifier", verifier); askSet("state", state);
    askSet("back", (location.pathname.split("/").pop() || "index.html") + "|" + id);
    location.assign(ASK.login + "/oauth2/authorize?" + new URLSearchParams({
      response_type: "code", client_id: ASK.client_id, redirect_uri: ASK.redirect, scope: "openid email",
      state, code_challenge: challenge, code_challenge_method: "S256" }));
  }
  // back from the sign-in page: the code becomes a token, then the page and account we came from
  async function askReturn() {
    const ASK = askConf();
    const q = new URLSearchParams(location.search), code = q.get("code");
    if (!ASK || !code) return false;
    const [page, id] = (askGet("back") || "").split("|");
    const ok = q.get("state") && q.get("state") === askGet("state");    // our own request, not someone else's link
    if (ok) {
      try {
        const r = await fetch(ASK.login + "/oauth2/token", { method: "POST",
          headers: { "Content-Type": "application/x-www-form-urlencoded" },
          body: new URLSearchParams({ grant_type: "authorization_code", client_id: ASK.client_id, code,
                                      redirect_uri: ASK.redirect, code_verifier: askGet("verifier") || "" }) });
        const j = await r.json();
        if (j.id_token) { askSet("token", j.id_token); askSet("until", String(Date.now() + ((j.expires_in || 3600) - 60) * 1000)); }
      } catch (e) { /* no token: the box offers the sign-in again */ }
    }
    askSet("verifier", null); askSet("state", null); askSet("back", null);
    if (id) askSet("open", id);
    location.replace(page && /^[\w.-]+\.html$/.test(page) ? page : location.pathname);   // the code never stays in the address
    return true;
  }
  function askSignOut(id) {
    askSet("token", null); askSet("until", null);
    drawAsk(id);
  }
  function drawAsk(id) {
    const ASK = askConf();
    const host = $("tl-ask");
    if (!host) return;
    if (!ASK || !id || !byId[id]) { host.hidden = true; host.innerHTML = ""; return; }
    host.hidden = false;
    const tok = askToken();
    let x = `<h2>Ask about this account</h2>`;
    if (!tok) {
      x += `<p class="finding small">One question about ${esc(byId[id].account_name)}, answered from the engine's facts and its own tickets, emails and call notes, every sentence with its source.</p>` +
        `<p><button class="small primary" id="ask-signin">Sign in to ask</button></p>` +
        `<p class="note">For the CS team and invited reviewers: the admin makes each login, and a login sees one person's page. ${ASK.daily} questions a day each. Nothing you type is kept.</p>`;
      host.innerHTML = x;
      $("ask-signin").addEventListener("click", () => askSignIn(id));
      return;
    }
    x += `<div class="fc-form"><input id="ask-q" type="text" maxlength="${ASK.max_chars}" placeholder="e.g. Why might they downsize at renewal?" aria-label="Your question">` +
      `<button class="small primary" id="ask-go">Ask</button></div>` +
      `<div id="ask-answer" role="status"></div>` +
      `<p class="note">Signed in as ${esc(askWho())} · <button class="link" id="ask-out">Sign out</button>. People in the documents are labels (Customer A, CS 1): nothing can turn them back. Check a source before acting on it.</p>`;
    host.innerHTML = x;
    $("ask-go").addEventListener("click", () => askNow(id));
    $("ask-q").addEventListener("keydown", (e) => { if (e.key === "Enter") askNow(id); });
    $("ask-out").addEventListener("click", () => askSignOut(id));
  }
  async function askNow(id) {
    const ASK = askConf();
    const q = ($("ask-q").value || "").trim(), out = $("ask-answer"), go = $("ask-go");
    if (!q) return;
    go.disabled = true; out.innerHTML = `<p class="note">Asking… about ten seconds.</p>`;
    let r = null, j = {};
    try {
      r = await fetch(ASK.url, { method: "POST", headers: { "Content-Type": "application/json", Authorization: "Bearer " + askToken() },
                                 body: JSON.stringify({ account_id: id, question: q }) });
      try { j = await r.json(); } catch (e) { j = {}; }
    } catch (e) { r = null; }
    go.disabled = false;
    if (r && r.status === 401) { askSignOut(id); return; }            // the sign-in ran out: offer it again
    if (!r || !r.ok) {
      out.innerHTML = `<p class="note bad">${esc((j && (j.error || j.message)) || "No answer this time. Try again in a minute.")}</p>`;
      return;
    }
    const c = j.checks || {}, issues = [
      ...(c.unknown_citations || []).length ? [`citations to sources that don't exist: ${c.unknown_citations.join(", ")}`] : [],
      ...(c.unverified_numbers || []).length ? [`numbers found in no source: ${c.unverified_numbers.join(", ")}`] : [],
      ...(c.uncited_sentences || []).length ? [`${plural(c.uncited_sentences.length, "sentence")} without a source`] : [],
      ...(c.label_mixups || []).length ? [`a person's label used for the account`] : []];
    out.innerHTML = `<div class="ask-answer"><p>${esc(j.answer || "").replace(/\n+/g, "</p><p>")}</p></div>` +
      `<p class="note"><b>Sources:</b> ` + (j.sources || []).map((s) => `[${esc(s.id)}] ${esc(s.title)}${s.date ? ` (${fmtFull(s.date)})` : ""}`).join(" · ") + `</p>` +
      (issues.length ? `<p class="note bad"><b>Unverified, check before relying on it:</b> ${esc(issues.join("; "))}.</p>`
                     : `<p class="note">Every sentence cites a source; every number traces back to one.</p>`) +
      `<p class="note">Data as of ${fmtFull(j.as_of)}. ${plural(j.questions_left_today, "question")} left today.</p>`;
  }
  // on load: back from the sign-in page, or back on the account the sign-in started from
  if (askConf()) {
    askReturn().then((left) => {
      const id = !left && askGet("open");
      if (id && byId[id]) { askSet("open", null); openAccount(id); const h = $("tl-ask"); if (h) h.scrollIntoView({ block: "start" }); }
    });
  }
