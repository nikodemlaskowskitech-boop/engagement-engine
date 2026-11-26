// The Ask box on the public site (Node + Playwright, 26 Nov 2026), its sign-in round trip with
// stand-ins for the team's sign-in page and the Ask API: no AWS, no model. The pages are built
// the way the engine builds them in AWS (roles.py, sandbox, the deploy's ASK_* settings) and
// served under the site's content policy with the two addresses the deploy adds.
// Checks: no box without those settings (the laptop); "Sign in to ask" before a login; the
// sign-in request is ours (code flow, PKCE S256, our client, our return address, a random
// state); back from it the code becomes a token, the code leaves the address and we land on
// the account we started from; a question goes with the token and comes back with its answer,
// sources and checks; a refusal shows its reason; an expired sign-in offers it again; a link
// with someone else's state is ignored; the token stays in this tab only (never localStorage).
//   node tests/ask_box_test.js
const { chromium } = require("playwright");
const fs = require("fs");
const http = require("http");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");
const ROOT = path.join(__dirname, "..");
const BASE_CSP = fs.readFileSync(path.join(ROOT, "infra", "data_stack.py"), "utf8")
  .match(/SITE_CSP = \(([\s\S]*?)\)\n/)[1].match(/"([^"]*)"/g).map((s) => s.slice(1, -1)).join("");
const API = "https://ask.example.test", LOGIN = "https://login.example.test", CLIENT = "test-client-123";
let passed = 0, failed = 0;
const check = (ok, name, detail = "") => { ok ? passed++ : failed++; console.log(`  ${ok ? "PASS" : "FAIL"}  ${name}${!ok && detail ? "  ->  " + String(detail).slice(0, 300) : ""}`); };

(async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "askbox-"));
  const server = http.createServer((req, res) => {
    const name = path.basename(decodeURIComponent(req.url.split(/[?#]/)[0])) || "index.html";
    const file = path.join(dir, name);
    if (!fs.existsSync(file)) { res.writeHead(404); return res.end(); }
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8",
                         "Content-Security-Policy": BASE_CSP.replace("connect-src 'self'", `connect-src 'self' ${API} ${LOGIN}`) });
    res.end(fs.readFileSync(file));
  }).listen(0);
  const site = `http://127.0.0.1:${server.address().port}/`;
  const build = (env) => execFileSync("python3", ["-c", `import json, pathlib, roles
from settings import OUTPUTS
roles.build(json.loads((OUTPUTS / "views_data.json").read_text(encoding="utf-8")), out=pathlib.Path(${JSON.stringify(dir)}), sandbox=True)`],
    { cwd: ROOT, env: { ...process.env, ...env }, encoding: "utf8" });
  const meta = (f) => JSON.parse(fs.readFileSync(path.join(dir, f), "utf8").match(/<script id="page-meta" type="application\/json">([\s\S]*?)<\/script>/)[1]);

  console.log("\nOnly where AWS said where the Ask box is");
  build({ ASK_URL: "", ASK_CLIENT_ID: "", ASK_LOGIN_URL: "", ASK_REDIRECT: "" });
  check(!meta("csm_anna.html").ask, "no ASK_* settings (the laptop): no Ask box on the pages");
  build({ ASK_URL: API + "/ask", ASK_CLIENT_ID: CLIENT, ASK_LOGIN_URL: LOGIN, ASK_REDIRECT: site });
  const m = meta("csm_anna.html");
  check(m.ask && m.ask.url === API + "/ask" && m.ask.client_id === CLIENT && m.ask.redirect === site && m.ask.daily > 0,
        "with them: the page knows where to ask and sign in, and the daily cap", JSON.stringify(m.ask));
  const ACC = m.accounts.find((a) => true);

  const b = await chromium.launch();
  const ctx = await b.newContext(), p = await ctx.newPage(), errors = [];
  p.on("pageerror", (e) => errors.push(e.message));
  p.on("console", (x) => { if (x.type() === "error" && !/net::ERR_|fonts|Failed to load resource/.test(x.text())) errors.push(x.text()); });   // the refusals below are on purpose
  let authorize = null, tokenBody = null, asked = [], answer = { status: 200 };
  // the team's sign-in page: it sends the browser back with a code (as Cognito does)
  await ctx.route(LOGIN + "/oauth2/authorize**", (route) => {
    authorize = new URL(route.request().url());
    const back = `${authorize.searchParams.get("redirect_uri")}?code=one-time-code&state=${authorize.searchParams.get("state")}`;
    route.fulfill({ status: 302, headers: { Location: back } });
  });
  const cors = { "Access-Control-Allow-Origin": site.slice(0, -1), "Access-Control-Allow-Headers": "Authorization, Content-Type",
                 "Access-Control-Allow-Methods": "POST" };
  const fake = "h." + Buffer.from(JSON.stringify({ email: "reviewer@example.com" })).toString("base64url") + ".s";
  await ctx.route(LOGIN + "/oauth2/token", (route) => {
    if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers: cors });
    tokenBody = new URLSearchParams(route.request().postData());
    route.fulfill({ status: 200, headers: { ...cors, "Content-Type": "application/json" },
                    body: JSON.stringify({ id_token: fake, expires_in: 3600 }) });
  });
  await ctx.route(API + "/ask", (route) => {
    if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers: cors });
    asked.push({ auth: route.request().headers()["authorization"], body: JSON.parse(route.request().postData()) });
    const body = answer.status === 200
      ? { account_id: ACC, as_of: "2026-10-18", answer: "They asked to reduce the contract at renewal [C1].",
          sources: [{ id: "DATA", title: "the engine's own data" }, { id: "C1", title: "Call: account review", date: "2026-08-03" }],
          checks: { unknown_citations: [], unverified_numbers: ["999"], uncited_sentences: [] }, questions_left_today: 19 }
      : { error: answer.error };
    route.fulfill({ status: answer.status, headers: { ...cors, "Content-Type": "application/json" }, body: JSON.stringify(body) });
  });

  console.log("\nBefore signing in");
  const open = async (file) => {
    await p.goto(site + file); await p.waitForTimeout(400);
    await p.click("#tab-timeline");
    await p.evaluate((id) => { const s = document.getElementById("tl-account"); s.value = id; s.dispatchEvent(new Event("change")); }, ACC);
    await p.waitForTimeout(300);
  };
  await open("csm_anna.html");
  check(await p.isVisible("#tl-ask") && await p.isVisible("#ask-signin"), `account ${ACC}: "Sign in to ask"`);

  console.log("\nThe sign-in round trip");
  await p.click("#ask-signin"); await p.waitForTimeout(1200);
  const a = authorize ? Object.fromEntries(authorize.searchParams) : {};
  check(a.response_type === "code" && a.client_id === CLIENT && a.redirect_uri === site && a.code_challenge_method === "S256"
        && (a.code_challenge || "").length >= 43 && (a.state || "").length >= 16 && a.scope === "openid email",
        "the request is ours: code flow, PKCE (S256), our client and return address, a random state", JSON.stringify(a));
  check(tokenBody && tokenBody.get("grant_type") === "authorization_code" && tokenBody.get("code") === "one-time-code"
        && (tokenBody.get("code_verifier") || "").length >= 43 && !tokenBody.get("client_secret"),
        "back: the one-time code becomes a token with the PKCE verifier, no secret", tokenBody && tokenBody.toString());
  await p.waitForTimeout(600);
  check(!p.url().includes("code=") && new URL(p.url()).pathname.endsWith("/csm_anna.html"), "the code left the address, and we're back on the page we started from", p.url());
  check(await p.inputValue("#tl-account") === ACC && await p.isVisible("#ask-q"), "...on the same account, with the question box");
  const stores = await p.evaluate(() => ({ local: Object.keys(localStorage).filter((k) => k.startsWith("ask:")), session: Object.keys(sessionStorage) }));
  check(!stores.local.length && stores.session.includes("ask:token") && !stores.session.includes("ask:verifier"),
        "the token stays in this tab only (never localStorage); the verifier is gone", JSON.stringify(stores));

  console.log("\nAsking");
  await p.fill("#ask-q", "Why might they downsize at renewal?"); await p.click("#ask-go"); await p.waitForTimeout(500);
  check(asked.length === 1 && asked[0].auth === "Bearer " + fake && asked[0].body.account_id === ACC
        && asked[0].body.question === "Why might they downsize at renewal?", "the question goes with the sign-in token, for this account", JSON.stringify(asked[0]));
  const shown = await p.textContent("#ask-answer");
  check(shown.includes("reduce the contract") && shown.includes("[C1] Call: account review") && shown.includes("999")
        && shown.includes("19 questions left today"), "the answer, its sources, what's unverified, and the questions left", shown);
  answer = { status: 429, error: "That's today's 20 questions. More tomorrow." };
  await p.click("#ask-go"); await p.waitForTimeout(400);
  check((await p.textContent("#ask-answer")).includes("today's 20 questions"), "a refusal shows its reason");
  answer = { status: 401, error: "Unauthorized" };
  await p.click("#ask-go"); await p.waitForTimeout(400);
  check(await p.isVisible("#ask-signin"), "an expired sign-in: the box offers it again");

  console.log("\nA link someone else made");
  const ctx2 = await b.newContext(), p2 = await ctx2.newPage();
  let swapped = false;
  await ctx2.route(LOGIN + "/oauth2/token", (route) => { swapped = true; route.fulfill({ status: 200, body: "{}" }); });
  await p2.goto(site + "index.html?code=stolen&state=not-ours"); await p2.waitForTimeout(800);
  check(!swapped && !p2.url().includes("code="), "a code with a state we didn't make is never swapped for a token, and leaves the address", p2.url());
  check(!errors.length, "no script error", errors.join(" | "));
  await b.close(); server.close();
  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
})();
