# Reference: every file in detail

The full description of each file and each dated decision, moved here from
CLAUDE.md on 13 Nov 2026 (ARCHITECTURE.md fix G) so CLAUDE.md can stay short.
CLAUDE.md has the rules, the shape and a one-line map; this page has the detail.
Read a file's entry before changing that file, and update the entry in the same PR.

## Files

- `DATA_CONTRACT.md`: the inputs, their columns and meaning, which
  are required, what stops working without each, and what is still
  hard-coded. Update it whenever an input or a column changes.
- `PRIVACY.md`: how the handover briefs keep people's details from the model,
  step by step (gather, identify by position, label, clean, model, check,
  restore), where each piece lives (laptop / AWS), how the tests prove it and
  what they can't see, the known gaps and the upgrades in order of value
  (leak check, contact list, PII detector, ...). Update it whenever redaction,
  its inputs or where its outputs live change.
- `THREAT_MODEL.md`: one page: what we protect, the ways in, each threat with
  its control, the test that proves it and what's left, and the risks we
  accept on purpose. Update it when a new way in is added (a public address,
  a secret, a new place data goes).
- `ROLES.md`: the role matrix (leader, manager, CSM, pool member): the team is
  the boundary (everyone under one manager), full visibility inside it, limits on
  actions; what each role sees, changes and opens on, why (dated decisions), what
  a page must never contain, and where to change it (settings.toml [roles]).
  Spec for the per-person pages (6 Nov 2026), built the independent way: tests by
  a separate agent from the spec only, then the build, then a separate review.
  Update it whenever a role, a tab or an edit right changes.
- `ARCHITECTURE.md`: the architecture review of 13 Nov 2026: the shape today (a
  diagram), what five weeks of organic growth cost us (every step re-reads 485 MB
  of events; steps talk through hand-typed CSV paths; rules leak into the page; one
  export shaped for one page; no home for unstructured data; scripts built three
  ways; this file a changelog), and a decision per fix: Do now (one DuckDB file per
  run with daily roll-ups, tables between steps and one pipeline list, a facts
  layer facts.py, one documents table, a short CLAUDE.md), Later (rules out of the
  page tab by tab, add_views + main() everywhere, the cockpit split when the Ask box
  comes) or Not worth it (another database, a framework, Step Functions, a new
  generator). Order: measure step times, then A+B, C+D, G, then Phase 9. Update it
  when a fix lands or a decision changes.
- `AI_COST.md`: every way to cut the cost of a model over unstructured text, each
  with a decision (Done / Planned / Not now), the reason, and for "Not now" what
  would change our mind (13 Nov 2026: call only on change and the output cap done;
  text cleanup, RAG, prompt caching where it pays, Haiku extraction stored as rows
  planned; batch and layered summaries not now). Update it when a technique is
  built or a decision changes.
- `DEMO.md`: the 3-minute walkthrough (what to click and say, numbers as of the
  demo data; check them after a regenerate), linked from the README.
- `ENGAGEMENT_LOGIC.md`: the logic by tier as Mermaid diagrams (GitHub
  renders them): coverage table, overview, Self-service journey, Scale
  queue and hand-back, Enterprise QBR first, action status. Update it when
  routing, follow-ups or escalations change.
- `check_inputs.py`: checks a folder of inputs against the data contract
  before anything runs (plain Python, no DuckDB): missing columns, duplicate
  IDs, unreadable dates, tiers or regions not in settings, accounts in the
  events but not the CRM, too few days of data; ends with what the engine can
  and can't do with this data. `python check_inputs.py [folder]`. ERROR = exit
  1 (run_all stops), WARN = runs with gaps. First step of run_all.
- Your own data: `settings.local.toml` (git-ignored; copy
  `settings.local.example.toml`) overrides settings.toml key by key. [data]
  says where the inputs live (folder and file names) and [columns.crm] /
  [columns.team] map a company's column names to the engine's (a RENAME in
  the crm and cs_team views). The input folder is only read; outputs always
  go to data/. settings.py exposes INPUTS, OUTPUTS, DEMO and input_path();
  no CS history = an empty engagement view; check_flags skips off the demo.
- `settings.toml` + `settings.py`: every threshold and time window in one
  place (activation, churn, expansion, QBR, journeys, team report, email
  hour, follow-ups), plus how the engine fits a company: [tiers] maps the
  CRM's tier names onto the three coverage models, [utilisation] says which
  usage event and CRM commitment column define utilisation, [regions] the
  time zones, [slack] and [slack.users] the workspace (channels, who gets
  cards or DMs, member IDs, the cockpit link), [llm] the provider, model and
  price, [aws] the profile, region and account,
  [money] the currency, ARR column, list price and value rules.
  The crm view (cairnpath_db.py) applies [tiers] and
  [utilisation], so the SQL downstream always speaks the engine's names
  (Self-service, Scale, Enterprise; contracted_events). Scripts read
  `SETTINGS[...]`; never hard-code a number in a script. One pipeline list
  (13 Nov 2026, ARCHITECTURE.md fix B): [pipeline] steps, each where = both /
  laptop / aws, when = regenerate / actions, optional; run_all.ps1 reads it
  (`python settings.py --steps laptop [regenerate] [actions]`, optional steps
  marked "?") and settings.py derives [aws] engine_steps and
  engine_optional_steps from it, so the two can't drift. The files one step hands
  to the next have names in [outputs]: `output_file(name)` / `out_sql(name)`,
  never a path typed into SQL. `tests/pipeline_test.py`: both lists from one
  source, every step a real script, no typed hand-off path left.
- `generate_events.py`: ~500 fake accounts with personalities (healthy,
  declining, slipping (volume sliding, users steady: one signal, churn Watch),
  power_user, new_customer, shelfware, stalled, never_onboarded,
  handoff_gap) mixed by weight (tuned 12 Nov 2026 so today reads like a real book:
  about 5% at churn Act, 69% healthy; STORY_ACCOUNTS and STORY_RENEWALS pin the three
  handover stories' accounts to their personality and renewal date) (new customers sign all through the window
  and ramp their volume up over the first month; shelfware is flat at about
  a quarter of the contract;
  stalled and silent ones are recent), 365 days of events (12 Nov 2026: was 90),
  the CRM table and the CS team table. Each personality's story plays out over the
  last ARC_DAYS (90); before that the account sits at its normal level, so today's
  flags read as before. The year before: new logos (FIRST_YEAR_SHARE of the existing
  accounts whose term started during the year signed then, onboarding and ramping
  up, with the welcome email or the control group), and EPISODES (110) dips (start
  by EPISODE_LAST_START; a dip thins the team out faster than the volume, so it
  reaches Act): the owner's style decides whether and when they call; called within
  14 days the account recovers 80% of the time, otherwise 55% (a planted effect,
  like the welcome email's). Tuned 12 Nov 2026 to a believable top line: GRR about
  89% and NRR about 102% on the customers of day one (the generator prints both).
  In-year renewals of accounts that stayed renew flat, expand or downsize
  (RENEWAL_CHANGE_ODDS by tier; a dip makes a downsize likelier; renewal_history.csv
  has renewal_arr and arr_before). The year before the data: data/arr_bridge_history.csv,
  a quarterly ARR bridge, top line only (starting ARR, new logo, expansion,
  contraction, churn, ending ARR; [PRIOR_YEAR] GRR 87%, NRR 99%), ending on day
  one's ARR. Recovered: the dip sits on one of today's healthy accounts. Not
  recovered: a lost account (same tier, owner and region) that churns at its
  renewal and leaves the book: CRM status = churned, renewal_date = the day it left,
  no events after. Quarterly QBRs across the year. CRM customer_since (first signed)
  and csm_since: a new Enterprise logo's CSM comes 1-6 days after signing, now and
  then [SLOW_HANDOFF] 18-40 days (its kickoff waits for the CSM; own generator,
  SEED + 13). Writes data/renewal_history.csv
  (the renewals inside the year: renewed, or churned for the lost accounts). Tasks
  and growth calls for a personality's story only look at the last 90 days
  (visible_day story_only), so a power user above 80% all year didn't become a
  growth task in November. Three Enterprise accounts are covered from
  another region (CROSS_REGION_COVER), so team and region differ. About 4 in 10 accounts have a champion (one user far more
  active than the rest). Product use follows a working week (quiet
  weekends); event volume doesn't (the customer's end users never stop).
  Seeded, so output is repeatable.
  Also writes the CS history to `data/engagement/` (own random generator):
  getting-started emails and opens, fortnightly Scale workshops, QBRs on
  the CRM dates, kickoffs, check-ins, churn and growth calls and follow-ups, each owner with their
  own speed, reacting to the day each account's problem shows in its
  numbers. 1 in 5 new Self-service/Scale accounts is a control group (no
  email; planted effect: SDK in after 5 days, not 2). Working hours, local
  time, no weekend meetings.
  Also writes `data/onboarding_holdout.csv`: the control group as a
  real-world fact (the programme's own list), which every sender checks.
  Also writes `data/renewal_outcomes.csv` (the renewals after the data
  ends, with renewal_arr = what each renewed at, for backtest.py; a real team
  exports closed renewals from the CRM).
  Also writes `data/answer_key.csv` (each account's personality and
  welcome group): for testing only, the flags must never read it.
- `cairnpath_db.py`: shared DuckDB setup. `connect()` returns a connection
  with the views events (schema declared explicitly), engagement (the CS
  history, same schema), crm (today's book: churned accounts left out), crm_all
  (with them, so their old events aren't a handoff gap), cs_team, as_of
  and utilisation, monthly_usage (utilisation and users per 30-day month; month 1
  has a start, as_of - 90), weekly_users (active users this week vs. the average of
  the last 13 weeks; the churn flag's breadth signal),
  qbr_schedule (QBR coming up / overdue), qbr_slips (flags handed to a
  QBR that didn't happen, from the action log), action_status (every task
  sent to a person: Open, Claimed, Done or Resolved, and its age; routed_to
  is whoever has it now, after a manager's reassignment; today's book only),
  daily_util_30d (30-day
  utilisation on every day, rounded like the flags), last_touch (last CS touch,
  last meeting, getting-started email opened or not, per account).
  Every script uses it; define views here, once.
- `ingest.py`: the events store (13 Nov 2026, ARCHITECTURE.md fix A): reads the
  product events once per run, with the agreed schema, into one Parquet file
  (data/<[data] store_folder>/events.parquet) plus a stamp of the input files it
  came from (name, size, time changed). connect() reads the store when the stamp
  matches the event files, otherwise the JSON (slower, never stale). Runs after
  cockpit_sync in run_all and first in [aws] engine_steps; the engine Lambda
  never uploads the store. `tests/store_test.py`: same rows and types as the JSON,
  a new, edited or removed file or a missing stamp means the JSON again.
- `facts.py`: the facts layer (13 Nov 2026, ARCHITECTURE.md fix C): the questions the
  product answers, asked one way. Building blocks the cockpit export uses for the
  whole book (accounts with util, weekly users and flags with their value; touches;
  forecasts; next steps; tasks; time_to_action), and the questions the MCP server and
  the Ask box will ask: account(id) (the cockpit's facts plus "called"), at_risk(owner /
  manager / region / tier, biggest ARR at risk first), timeline(id) (newest first),
  tasks(owner) (a pool member's include pool-queue tasks on their accounts). Plain
  dicts, dates as text; only IDs reach the SQL (_id). `tests/facts_test.py`: each
  answer equals the cockpit's for the same accounts.
- `documents.py`: one home for the unstructured data (13 Nov 2026, fix D): support
  tickets, emails and call notes, each joined by its own key (helpdesk organisation,
  email domain, CRM account ID via stories/directory.csv), in one shape (kind, source,
  date, title, text, citation id S1/E1/C1, oldest first). for_account(id),
  all_documents(), add_table(con) (a DuckDB table "documents"). Raw text: redact
  before anything leaves (handover.py does). handover.py reads them only through it.
  File names in [handover]. `tests/documents_test.py`: the joins, the shape, the
  citations, one way to read them, the table.
- `generate_documents.py`: the search corpus (Phase 9, 13 Nov 2026). Support tickets,
  emails and call notes for 30 accounts of the demo book (Enterprise and Scale with a
  named owner, not the three story accounts), written to data/<[search] corpus_folder>/
  in the shape of stories/ (directory.csv, support_cases.csv, emails/, calls/,
  contacts.csv), so documents.py reads them the same way. Each account's documents fit
  its personality: declining talks budget cuts, a competitor (Lumen Analytics, Northbeam
  Insights) or a champion leaving, taken in turn; slipping missing data; shelfware a
  rollout that never happened; power users hitting the limit; new customers stuck
  starting; half the time a second topic. Each topic is written three ways (ticket, email,
  call) in different words, two of them per account, so a search has to match meaning,
  not just the question's words. Every account gets two routine documents (password reset,
  webinar thanks), and three healthy accounts a trap (a question's words without its
  meaning: "budget approved", "looked at a competitor two years ago", an invoice copy).
  Writes [search] key_file: nine CS questions in a CS person's words, the accounts that
  answer each and the traps; for tests only, search never reads it. Our people are side
  "cs" in contacts.csv (redaction labels them CS 1). Reads the generator's answer key
  (personalities): a generator, never an engine step; in [pipeline] with -Regenerate,
  laptop only. Own seed (7). `tests/corpus_test.py`: seeded, read through documents.py,
  every key account has a document on its topic, routine documents everywhere, no trap
  is an answer, every person named in contacts.csv on the right side.
- `search.py`: account search (Phase 9, 13 Nov 2026). add_views(con) loads the documents
  (stories/ and, when generated, the search corpus) through documents.add_table (which
  now takes a list of folders) and indexes them: doc_terms (each stemmed word's count per
  document), doc_len, doc_stats. search(con, question, k, account_id) ranks documents by
  BM25 ([search] bm25_k1 1.2, bm25_b 0.75): rare words count more, a long document is
  marked down, repeats saturate; each hit has its citation ID, the words matched and the
  line that matched best. accounts(con, question, k): each account once, by its best
  document. Plain SQL, not DuckDB's fts extension: the extension downloads on first use,
  which the sandbox can't and the AWS engine shouldn't (no code fetched at run time).
  Words: lower case, split on non-letters, [search] stop_words out ("account" and
  "customer" included: every CS question has them), a Porter-lite stem (-ing, -ed, -ies,
  -s, then -e). Only letters, digits and spaces reach the SQL; an account ID must be one.
  Raw text: results stay on the laptop; anything that leaves gets redacted first.
  Baseline, 13 Nov 2026: recall 19 of 37 accounts (51%) in the top 10 over the nine
  questions; perfect where people use the question's words (onboarding, security, a
  champion leaving), 0 of 6 on competitors and on budget pressure, where the documents
  say "benchmark Lumen Analytics" or "the CFO is going through every subscription", and
  it fell for the traps there.
  Synonyms, same day: [search.synonyms], a CS-owned vocabulary (a question word -> the
  words it also stands for, stemmed into a table "synonyms"), each counting [search]
  synonym_weight (0.6) of the question's own word, so a document using the question's
  words still ranks first; search(..., synonyms=False) and `--exact` turn it off.
  Recall 37 of 37 (100%), precision 42% in a top 10 (most questions have 3 to 6 answers).
  Optimistic: the list was written by someone who had seen the corpus, generic words but
  not blind; a held-out set written apart is the fair test, before vectors are compared.
  Traps: the budget ones rank below all six answers; the competitor trap still ranks
  above three of them.
  Held-out, same day: tests/heldout/ (18 accounts acc_h01-h18, 75 documents, 4 traps,
  key.json with each question and two rewordings) written by a separate agent that never
  saw the vocabulary, the corpus or the search, in the stories/ format; committed, fixed.
  Recall over 27 wordings x 3 accounts: keywords 58 of 81 (72%), synonyms 73 of 81 (90%).
  The nine original questions: 12 of 27 on keywords, 27 of 27 with synonyms, so the
  vocabulary carries to new documents. The misses are all on rewordings, whose words the
  vocabulary isn't keyed on ("broken tracking", "a bill being contested"): the gap
  vectors should close. [search] min_heldout_recall 0.85.
  Redacted results, same day: redacted(con, hits) -> (safe, held_back). Per account,
  handover.cleaner() (the brief's redaction, split out of redact(), byte for byte the
  same on the three stories) with that folder's contacts (table doc_folders: which
  folder an account's documents came from; handover.contacts takes a folder), then
  handover.leak_check(); an account with anything person-shaped left is held back with
  what was found, for a person, never sent. Keys never returned. `--redacted` on the CLI.
  First run: 14 (account, name) pairs held back; 3 were two real people missed by
  redaction ("Mr Brenner" twice, "Ms Vogt": PRIVACY.md fix H, [handover] titles, also
  in people_in), 11 were eight non-person names, added to leak_check_allow. Test: 729 results, no leak, none dropped.
  The three story accounts aren't in the key, so Nettle Foods
  (a real overage dispute and competitor) shows as "not right". [search] min_recall 0.9
  keeps a bad edit to the list from passing.
  `tests/search_test.py`: the recall table, and BM25's mechanics on made-up documents.
- `search_export.py`: the public search's documents (13 Nov 2026, option 2 of the demo
  search: a search function in AWS that only ever sees redacted text). Every document of
  the stories and the corpus through search.redacted() (the brief's redaction, then the
  leak check), written as one JSON line each (account_id, id, kind, date, title, text;
  no source: file names and email IDs carry the company's name) to [search]
  site_documents_file, committed beside the function's code: the function carries it and
  has no access to the data bucket. A held-back account = nothing written, exit 1.
  `--check`: exit 1 when the committed file is stale. documents.add_table and
  search.add_views take rows= for it. 156 documents, 66 KB.
  Decided the same day, with numbers: no score cut-off. Weak matches score close to
  real ones (held-out: a 40% cut-off took precision 31% -> 40% but lost 5 real
  accounts), so the site shows [search] site_top_k = 5 with each matching line, date
  and ticket status, and the visitor judges. Held-out: first result right 16 of 27,
  top 5 finds 61 of 81 (75%); search_test prints both.
  `tests/search_export_test.py`: the committed file is today's; no contact name, email,
  company, organisation or domain (320 strings) and no email or phone, checked apart
  from the redaction; every document there; the redacted file finds the same right
  accounts as the raw text.
- `infra/lambdas/search/` (13 Nov 2026): the public search on the demo site. handler.py: GET
  ?q= (empty or over [aws] search_max_chars: 400; not GET: 405), BM25 with synonyms over its
  bundled documents.jsonl (search_export.py's file), top [search] site_top_k accounts with
  their best document (citation, kind, date, title, matching line), the words matched and
  the other matching IDs; Cache-Control public for [aws] search_cache_seconds. The question
  is never logged (errors by type only). bundle.py bundle_search(): handler, search.py,
  documents.py, settings, the file, DuckDB (SEARCH_PACKAGES); no handover.py (search.py
  imports it only inside redacted()). data_stack.py, inside the site block: the Search
  function (no policy of its own: AWS's basic logs role only; not exempt from the data
  perimeter, so the bucket refuses it), its own HTTP API (GET /search, throttle [aws]
  search_rate_per_second 2 / search_burst 5, an access log without query string), and a
  CloudFront behavior /search to that API (HTTPS only, GET/HEAD, cache policy keyed on q
  only, no cookies or headers forwarded, the site's security headers). Same address as the
  pages, so the CSP's connect-src 'self' already allows it. Outputs SearchFunction and
  SearchUrl. `tests/search_lambda_test.py`: the real bundle in its own Python: only those
  files; answers, citations from the file, cacheable, same order as the laptop; 400/405/500;
  a planted name never in the logs, even on an error. infra_test.py and security_check.py
  check the rest (THREAT_MODEL.md 20-22).
- `site_search.html` (13 Nov 2026): the public site's search page, written by
  `roles.py --site` (write_search_page) as [search] site_page = search.html, beside the
  cockpit pages, so the engine publishes it with them (ENGINE_FILES has the template).
  Night trail look, self-contained. The disclaimer ("all companies, people and numbers in
  this demo are fictional and generated"), a search box, [search] site_examples as chips,
  results from /search (account, kind, citation, date, title and the matching line with
  the matched words highlighted, other matches on the account), ?q= in the address so a
  question can be shared, friendly messages for an empty question, no match, the rate
  limit (429), an unreachable search and a saved copy (file://). "What this searches, and
  what it can't do": finds and ranks, not answers; words plus the CS vocabulary, not
  meaning; no dates, amounts or "not"; labels instead of names; top 5 and the held-out
  numbers in words (update them when the search changes); nothing typed is stored. The
  cockpit's sandbox banner now carries the same disclaimer and a link to it.
  `tests/site_test.js` (search section): served with the real content policy, /search
  answered by the real handler over the real file: chips, results citing documents in the
  file, highlights, shareable ?q=, empty, 429, no match, phone width, no script error;
  every cockpit page's banner links to it.
- `mcp_server.py` (13 Nov 2026): the engine as MCP tools (mcp 2.2.0, MCPServer; pinned in
  requirements.txt). Local: the client (Claude Desktop) starts it and talks over stdio, no
  network address. Five read-only tools (ToolAnnotations read_only_hint): who_am_i,
  account (facts.account; utilisation trimmed to the last 30 days, weekly users for 13
  weeks), at_risk (facts.at_risk with owner/manager/region/tier/status filters), timeline
  (facts.timeline, limit), search_documents (search.search, then search.redacted; held-back
  accounts counted, not sent). One person's view: [mcp] person (an owner ID or "leader"),
  cut by roles.slice_export like their cockpit page; an account outside it is refused with
  ToolError (the reason reaches the model; any other error's text stays on the server, the
  SDK's rule). [mcp] max_results caps every list. `--check` prints the tools and a sample.
  Claude Desktop config: "cairnpath": {"command": "<repo>\\.venv\\Scripts\\python.exe",
  "args": ["<repo>\\mcp_server.py"]}. `tests/mcp_server_test.py`: tools and annotations;
  the leader's facts equal the cockpit's; as a CSM, another team's account refused and none
  in at_risk or search; search results without contact names, emails, phones, companies;
  a real stdio round trip started from another folder.
- `tests/long_history_test.py`: a year of data must read like 90 days did (12 Nov
  2026: two windows had no start, so a year of data made every account look like
  it had lost most of its users, and "month 1" summed ten months). A perfectly flat
  account over 365 days in a temporary folder: months 1 to 3 equal, weekly users
  10 against an average of 10; crm leaves out a churned account and crm_all keeps
  it. Fails on the old code. Run it on any PR that touches cairnpath_db.py or a
  window in the flags.
- `history.py`: the flags and the task queue week by week across the data (12 Nov
  2026; run after flags.py, before export_views.py; in run_all and engine_steps).
  It replays them: each week [history] step_days back from the as-of date (while
  [history] min_days of data lie behind), the inputs are cut back to that day
  (events, engagement and the action log up to it; the CRM as it stood: signed by
  then via customer_since, a churned account still in until its renewal_date, a
  term renewed later put back a year, no CSM before csm_since, QBRs from the
  meetings held) and the same SQL runs (flags.add_views, action_status). It stops
  if the replay of the last day differs from today's flag files. Writes
  data/history_flags.csv and data/history_tasks.csv (Open and Claimed only).
  Contract size, ARR, tier and owners are today's. Real use would store each run's
  snapshot instead (a CRM change can't be replayed); the replay is the demo's
  backfill. cairnpath_db.py reads the action log and today's flags through views
  (action_log, open_flags) so history.py can swap them.
- `retention.py`: GRR and NRR by the standard definitions (12 Nov 2026, from a
  senior CS leader's feedback: tie the work to the top line). Cohort = the
  customers of day one at their ARR that day; GRR = what they pay now, each capped
  at day one's ARR, over day one's ARR; NRR counts expansion; new logos in
  neither. ARR on any day from the renewal history (arr_before) and today's CRM
  (views arr_segments, macros arr_on(t) and retention_between(t0, t1),
  retention_year, retention_accounts). The year before from finance's ARR bridge
  (input arr_bridge_history.csv, top line only; bridge_rates_prior), this year's
  own bridge too (arr_bridge_this, bridge_rates_this) for a fair comparison. The
  forecast: renewals in the next [retention] forecast_days, the data's loss rule
  (account_value), the owners' forecasts and the upside, in GRR points of today's
  book, against what happened (backtest: the demo's outcomes)
  (retention_forecast, retention_forecast_accounts). What a call is worth: every
  account with a churn Act task this year that has renewed since, called within
  [journeys] act_for_days or not, ARR kept at renewal (call_effect_accounts,
  call_effect), and today's Act accounts with no call at those rates (call_waiting,
  call_value; needs team_report.add_views). export_views.py calls add_views and
  exports "retention" (the cohort per account, so the page can work out any
  segment); run_all prints it. Licence revenue only (no services in the book).
  Speed (12 Nov 2026: the AWS run went from 330 to 600 of its 900 seconds): the
  period's dates are read once and written into the views, and the forecast reads
  flags.py's account_value.csv and the outcomes file, not the views that rebuild
  every flag from a year of events.
  `tests/retention_test.py`: five made-up accounts with a known answer (GRR 65%,
  NRR 77.5%), the bridge rates, empty-safe views.
- `tests/history_test.py`: three made-up accounts over a year (a renewal, one that
  left, a new Enterprise logo whose CSM came late): the renewal gets no activation
  status (it was "Activated"/"Stalled" before the fix), the one that left is never
  a handoff gap, the new logo is absent before it signed and "No CSM assigned"
  with an Open CS ops task (and its age) only until its CSM came.
- `explore.py`: DuckDB summaries by tier, region and CSM, plus CS
  engagement week by week and meetings by owner.
- `flags.py`: the activation, churn and expansion flags. One row per
  flagged account, with reasons, routed_to and action; saved to
  `data/flags_activation.csv`, `data/flags_churn.csv` and
  `data/flags_expansion.csv`; the QBR flag to `data/flags_qbr.csv`.
  Also the value at stake per churn and expansion row (view account_value,
  `data/account_value.csv`, with its basis): ARR at risk per status from
  [money.at_risk] in settings (a share of ARR, or "unused" = ARR x (100% -
  utilisation)); today the whole ARR at Act (they may leave), the unused part
  at Watch and Shelfware (they shrink to what they use at renewal); opportunity =
  forecast utilisation at renewal above 100% x ARR (Watch at a share).
  Value guides the work; it's never a CSM's credit.
- `check_flags.py`: tests the flags against the answer key (run `flags.py`
  first): share of each personality found, false alarms, and every miss by
  name. Re-run it after any change to a flag rule.
- `backtest.py`: did the churn flags see it coming? (run `flags.py` first)
  Against `data/renewal_outcomes.csv` (what happened at each renewal in the
  180 days after the data: renewed, expanded, downsized, churned; written by
  the generator with its own random generator, including surprise churn no
  flag can see, and better odds for declining accounts that got a churn
  call): recall, precision, warning time (the first day of the current run
  under the churn line to renewal; under [backtest] too_late_days = too late), misses by name, and
  churn with vs. without a churn call (Act accounts only: mixing flag types
  confounds it), and the sieve read like a funnel (churn signals -> to
  automation or a person -> Act -> called -> called within 14 days ->
  renewing -> kept whole), and value: the data's ARR at risk and the owners'
  latest forecasts next to the ARR really lost (ARR - renewal_arr, an optional
  outcomes column; the generator shrinks a downsize to about the account's
  usage, so it shows the method, not proof). Runs after team_report.py (uses time_to_action).
  Not done yet: replaying the flags as of an earlier date (needs a longer
  data window).
- `journeys.py`: rules on the last touch (run `flags.py` first). "Email
  never opened": Self-service stalled/silent, getting-started email 5+ days
  old and unopened -> CS ops checks the contact. "No meeting in 30 days":
  churn or expansion Act (Scale/Enterprise) for 14+ days with no meeting
  in 30 days, and no QBR booked in the next 30 days ->
  escalated to the owner's manager (one Slack message per manager).
  "Follow-up not picked up": a Scale follow-up named to a person, still
  Open after 5 days -> their manager approves handing it back to the pool
  ("Approve hand-back" button; actions.py then sends it to the pool).
  "Reassigned, not picked up": a task a manager reassigned in the cockpit,
  still Open after 5 days -> back to that manager. Saved
  to `data/flags_journey.csv`, same columns as the flags.
- `team_report.py`: time to action by owner and manager (run `flags.py`
  and `journeys.py` first). Signal date = the first day of the account's
  current run over the flag's line (30-day utilisation, rebuilt from usage; an
  earlier dip it recovered from doesn't count; the lines come from settings); call date =
  first churn, growth or activation call (check-ins don't count; a QBR
  counts when the signal came in the 30 days before it, column via);
  a call up to 14 days before the signal counts as 0 days ("called
  early"). Also escalations and meetings in 90 days. Action status (view
  action_status in cairnpath_db.py): each task Open, Claimed (Slack),
  Done (the right meeting since it was sent: churn -> churn call or QBR,
  expansion -> growth call or QBR, QBR -> the QBR, activation and
  escalations -> any meeting; a check-in never closes a churn task) or
  Resolved (the flag went away; the only way CS ops tasks close), with its
  age. Managers see unclaimed pool tasks and CS ops items, oldest first.
  Outcome not measured yet.
- `onboarding_report.py`: welcomed vs. control (run `generate_events.py`
  first). New Self-service/Scale accounts that got the getting-started email
  vs. the onboarding holdout: SDK installed, median days to SDK and to first
  chart (speed), activated in 14 days for accounts 14+ days old (outcome),
  overall and by tier, counts next to percentages. Planted effect: shows the
  method, doesn't prove emails work.
- `export_views.py`: everything the views need, to `data/views_data.json`
  (run `generate_events.py`, `flags.py` and `journeys.py` first). Reuses the
  reports' SQL through `team_report.add_views(con)` and
  `onboarding_report.add_views(con)`, so the page and the reports agree.
  Counts only, never real user IDs. Also builds `data/views.html` from
  `views_template.html` (the data goes into its `__VIEWS_DATA__` slot): nine
  tabs. Overview (the engagement sieve: churn signals stage by stage, and
  renewals in the next 180 days by today's flag), Leadership (the one-pager:
  ARR at risk with action taken, renewal, action planned and forecast, biggest
  first; growth by upside; Act accounts called and planned; forecasts ≠ data;
  meetings and share of the book met in 30 days per owner against the median
  of the same kind), My book (one owner's
  morning page: workload, engagement against the median of owners of the same
  kind, the portfolio risk first with action taken and planned, and booked
  engagements over the next 8 weeks: QBRs, renewals, Scale follow-ups), Tasks (the manager's queue:
  every task sent to a person, its status and age; in the published cockpit a
  manager gives one to someone else or approves a hand-back, saved in the
  page's database), Did it work? (the backtest:
  caught / missed / false alarm, warning time, calls, ARR at risk vs. really
  lost by status), account timeline,
  onboarding, CS team (with capacity: each owner's whole book and open
  tasks against the median, for cross-region pickup), book of business (with
  accounts covered from another region). Every bar, cell and stage lists its
  accounts on click, and a row opens that account's timeline, so the logic can
  be checked by hand. A segment bar filters by people (manager, team = the region the owner
  sits in, owner) and by accounts (region = where the customer is, tier); it filters
  every tab; on the timeline it picks the example chips (and opens an account
  in the segment when the segment changes; one opened from outside is marked
  "Outside <segment>"). The lists narrow each other: each offers only
  values that still have accounts under the other filters. Its Find box (and the timeline's) searches
  accounts by name or ID, the segment's first. The Owner list has named people only: a pool is
  Team + the Scale tier. Values: ARR per account, ARR at risk and upside per
  flag (My book sorts by them). Renewal forecast on the timeline: the data's
  number, then the owner's or their manager's call (renew, downsize to an
  amount, churn) with a reason, saved in the page's database with its history;
  a quarter of the ARR apart from the data = flagged "≠ data". Action planned
  on the timeline too: the owner's (or manager's) next step with a date, else a
  QBR booked in the CRM; overdue when the date passes with no meeting since it
  was set; shown in My book's portfolio. The timeline opens with a summary:
  everything done (customer touches, tasks sent, plans, forecasts), newest
  first, next to what's coming up (next step, booked QBR, renewal with its
  forecast). Handover brief on the timeline (accounts with a finished brief
  from handover.py, exported as handovers): the brief as a draft, each
  citation a tag that opens that source as the model saw it (redacted), what to
  check before confirming, and Confirm (optional note for the new owner) for
  the outgoing owner or their manager, saved in the page's database
  (handover_confirm, pointing at the brief's version = its written_at); a
  rewrite after that makes it a draft again. Also exports next_steps,
  forecasts (forecast_set events), churn_signals, renewals
  (backtest.py's view) and tasks (action_status, with each task's manager).
  Opens in any browser offline. The page only draws; the numbers come from
  the export. Look "Night trail" (10 Nov 2026, chosen from mockups on the
  "Cairnpath cockpit look" canvas): dark by default whatever the device says
  (a warm forest black, cream text, stone greys, a sand accent; no slate and teal),
  Fraunces headlines over Inter, map contours behind the header, and the logo: a
  cairn of four stones with a trail cut into them (stone colours only, never a
  status colour). Cairns as urgency markers: red top + 4 stones = act now, amber
  + 3 = needs attention, green + 2 = healthy (height and colour, never colour
  alone). A warm-paper light set stays behind data-theme="light". Leadership opens
  with the trail: at risk -> at Act -> called -> with a next step, one cairn each,
  its urgency in colour, height and words ("13 still to call"); a click lists the
  accounts (who is missing, where a stage falls short). Then ARR at risk, Upside
  and Forecast ≠ data as tiles. Colours in three groups that never mix (10 Nov
  2026): brand (the look), status (--good/--warning/--serious/--critical, state
  only) and data (--viz-qbr sage, --viz-call lavender, --viz-renewal slate blue,
  --viz-other stone: one colour per engagement type on every chart, validated with
  the dataviz checker on the dark surface). stackChart() is the reference chart
  (My book's meetings and pipeline): bottom to top other/renewal, QBR, calls and
  follow-ups (blue never touches violet), a 2px gap between segments, totals on
  top, a segment's count inside only when it fits and the bar has more than one,
  dashed recessive gridlines, a darker tooltip with every category and the total,
  the legend in stack order; Watch accounts named beside the pipeline, never in it.
  New charts follow it.
  Review fixes (10 Nov 2026, separate reviewer): other meetings a darker stone
  (#5E665D: the old one was too close to sage), a timeline meeting coloured by its
  type, no status colour as a category (dot plot: called = lavender, churn a circle,
  expansion a square), tooltips always dark (light theme too), heatmap labels
  ink chosen by contrast, the CS team heatmap has a key with its max. Status
  meanings: not called = red; the Called column neutral; an Open task neutral, amber
  once it has waited [cockpit] stale_task_days (14); Claimed neutral, Done green.
  cockpit_test.js checks each.
  Phone (10 Nov 2026): under 640px every chart is drawn at about the panel's width
  (fitW(): text stays 10px+, not an 880 box shrunk to 4px), with a shorter layout
  (labels above bars, fewer week labels, no numbers inside stacked segments); the book
  heatmap keeps 640 and scrolls inside its panel. Crossing the breakpoint redraws.
  On desktop only QBR and call segments carry an inside number (cats' `inside`).
  Honest charts (10 Nov 2026): every chart lists what it counts on click (My book's
  meetings, the CS team heatmap: meetings; the book heatmap and onboarding bars:
  accounts); the dot plot's last tick reads "20+" and equal values stack in columns
  of three instead of drifting onto the next day.
  Light theme: the engagement colours pass on paper as they are; touches and the
  measure blues darken there (3:1+ on the paper, cockpit_test checks every --viz).
  Re-review (10 Nov 2026, the same separate reviewer): 8 of 10 fixed, then the rest:
  heatmap labels pick white where cream falls under 4.5:1 (light theme), inside counts
  4.8:1, stone #636B62 (3:1 on the panel, still 15+ from sage), the onboarding control
  group a dim stone (was a near-twin of "welcomed"), Leadership's "book met" red only
  [cockpit] below_median_points (10) under the median, phone: a 4th equal dot is "+n",
  the last week label end-anchored, warning buckets say where they start.
  Did it work?'s 2x2 (10 Nov 2026, Nikodem): hover names a cell's accounts, a click
  lists them, keeps the cell selected and scrolls to the list, and "Show only these N
  on every tab" pins them (seg.acc) and opens Leadership. Team-totals cells are counts,
  not links (no pointer). The header no longer clips: Find's list shows over the page.
  Time to call (CS team, redesigned 10 Nov 2026 with Nikodem): one axis, days since the
  signal (square-root scale, ticks 0/3/7/14/30/60+): called = a filled mark on the day of
  the call; not called = an open ring at how long it has waited, neutral while in time, red
  past the target ([journeys] act_for_days, exported as call_target_days); no "no call"
  column. Groups labelled (CSMs about 8 accounts, pools about 40); each row ends with
  "median N d · n waiting, n late". Marks take the first free lane (middle, above, below),
  so none touch or cross a row line; a 4th in one spot is "+n".
  Leadership, after the trail (12 Nov 2026, Nikodem): "Not picked up" (every task
  still Open, oldest first, amber from [cockpit] stale_task_days, 5 rows then View all)
  and "New Enterprise accounts with no CSM" (the activation flag's No CSM assigned,
  days since signing, ARR). An unowned account sits in no team, so only the leader's
  page lists it. Long tables fold to 6 rows with "Show all (n)" (foldRows(): the
  Leadership team table, CS team capacity); shortList() takes how many rows to show.
  Business case tab (12 Nov 2026, the leader's only: settings [roles.leader] tabs
  "case"; roles.py drops "retention" from every other page): the CFO's page,
  Leadership stays operational. Headline GRR and NRR against the year before,
  tiles (GRR, NRR, one point of GRR = 1% of today's ARR, at risk in the next two
  quarters in GRR points, ARR per CS person = coverage; with [money]
  cs_cost_per_head set, a GRR point in headcount; 0 = not shown), where the year's ARR went (a horizontal waterfall: day
  one, churn, downsell, GRR, expansion, NRR, then the year before; red = lost,
  green = growth, stone = totals), the next two quarters (data's rule, owners,
  what happened), what calling in time is worth (kept share in time vs. late, and
  today's Act accounts not called, as GRR points), and the definitions. hbars()
  draws the bars (phone: label and value over a full-width bar). The segment bar
  applies (the cohort per account); the year before is whole-book only, and says
  so. Every bar lists its accounts; one that left shows as such and opens nothing.
  Sortable tables (12 Nov 2026, Nikodem): a click on a heading sorts the table by it;
  numbers and money biggest first, then smallest, then the table's own order again;
  dates soonest first; short text A to Z. Columns of long text or forms, and the
  fixed-order tables (class nosort, the 2x2), don't sort. markSortable() works on any
  table (a MutationObserver marks each one as it's drawn); rows stay in their group
  (tr.group) and median rows (tr.pinned) at the end; a folded table folds again
  (table._refold); shortList and accountList sort their whole list, not the rows on
  screen (sortedItems). The choice is kept per place on the page across redraws.
  Long lists fold (12 Nov 2026, Nikodem): a clicked list (accountList) shows its first
  LIST_ROWS (10) and "Show all (n)", which becomes "Show fewer" (back to 10, the title
  kept in sight); example lists show 2. Every fold in the page works both ways.
  Trends under both (12 Nov 2026): the year week by week from the history (export
  key history: weeks, flags [week, id, flag, status] without Activated/Activating,
  tasks [week, id, flag, status, routed_to, action_status, age]); tasks not picked
  up split under/over stale_task_days (stone, amber), Enterprise with no CSM
  (amber); only today's bar carries its total; the latest week always labelled;
  a click lists that week. An account no longer in the book travels as ob_N
  (off_book: tier, region, owner, manager; never its id or name): counted in its
  segment, listed as "An account that has since left", opens nothing. roles.py
  keeps the off-book rows the same scope rule would put on the page.
  UX review, batch 1 (12 Nov 2026, agreed with Nikodem): "called" is one rule on
  every tab (time_to_action: a churn, growth or activation call from 14 days before
  the signal, or a QBR held in the 30 days after it; Leadership lost its "any meeting
  in 30 days" fallback). Book ARR: Leadership says "€X of €Y on CSM and pool books
  (whole book €Z)", the Business case reconciles the book (named owner + Self-service).
  A pool member's Tasks include pool-queue tasks on their own accounts, tagged "Pool
  queue · your account" (poolTaskOf). A person's page never offers "act as": it says
  who can act ("Read only: Mia Hofer can …, or Sophie (their manager)"). The timeline
  opens with the account (name, tier, owner, ARR, renewal, top flag), then the summary,
  then plan and forecast, then the handover brief (folded: "Read the brief"). Phone:
  tabs wrap, the filters fold behind a "Filters" button, every data table reads as
  cards (labelCells: data-label from the heading row; not the 2x2 or matrices).
  Keyboard: clickable chart marks and rows take Tab, Enter and Space (role button,
  aria-label from the tooltip), arrow keys move between tabs, lang="en", save errors
  role="alert". run_all runs "handover.py --dry-run" then export_views.py again, so a
  brief whose context moved on shows "Out of date" on the laptop too (a warning if it
  fails, like in AWS).
- `roles.py`: one cockpit page per person (ROLES.md, settings.toml [roles]),
  called by export_views.py, or `python roles.py` on data/views_data.json. Cuts
  the export to the person's accounts (the team = everyone under one manager;
  Self-service for managers in their team's regions; the leader the whole book),
  takes the role's missing tabs out of the HTML, swaps "Did it work?" for team
  totals below manager level (no row per account), and writes a page-meta block
  (person, role, tabs, accounts, edit rights per action). The page reads it to
  open on the role's first tab and to offer a form only where the person may act.
  Writes data/pages/<owner_id>.html, leader.html and index.html (the landing
  page, with a switcher to every page: demo only, see THREAT_MODEL.md).
  `python roles.py --site` (an engine step in AWS) writes the same pages in
  sandbox mode to data/site/ for the public demo site: forms save in the visitor's
  browser only (localStorage, "Clear my changes"), a banner says so, and the
  switcher simply opens the other page's address. `tests/site_test.js` serves them
  with the site's real content policy. Published,
  only the main page gets the database, so the switcher fetches the chosen file and
  writes it into the main page (stopping the old page's database listener first);
  offline, where fetch can't read files, it opens the file instead.
  An account open on the timeline goes with the switch (its id in the #): opened
  if it's on the new page, else a note says it isn't; every switch starts the new
  page on its own segment defaults. Pinned accounts (seg.acc): the bar's Find pins an account as a
  chip; while any are pinned every tab shows exactly them; they travel with a switch
  (ids only), the ones not on the new page counted in the note.
  Medians ("against the median") come from peer_medians(), on the whole export,
  numbers only: [roles] peer_median = "book" (default) or "team"; the leader always
  the book's. My book lists "No flags" accounts open for books of [cockpit]
  open_no_flags_up_to or fewer. `tests/peer_median_test.py`: both modes.
  `tests/roles_test.py`: written from ROLES.md alone by a separate agent (raw
  HTML: no out-of-scope id or name, meta against the spec, cockpit_sync refusals);
  `tests/role_pages_test.js`: the pages in a browser (no script error, tabs, forms
  only where allowed). Run both on any PR that touches roles.py, the template,
  export_views.py or [roles]; also cockpit_test.js for the full page.
- `actions.py`: turns flag rows into actions, once only (run `flags.py`
  first). Checks `data/action_log.csv` by account, flag and status, then
  logs each new action. Queue tasks for CSMs, pools and CS ops post to
  Slack #cs-queue, one message per owner; customer emails stay simulated
  and are previewed in #email-preview. Webhooks in `.env`
  (SLACK_WEBHOOK_QUEUE, SLACK_WEBHOOK_EMAIL): secrets, like the API key.
  An action is logged only after its post worked, so failures retry.
  Holdout accounts get no onboarding emails (logged as "held out", never
  sent) and their queue tasks carry a note not to send one by hand.
  Each customer email is also written as an email_sent event to
  `data/engagement/<run date>.jsonl`. `generate_events.py` replaces the log
  with the new data's own task history (write_task_history: tasks sent on the
  day each planted problem showed, some taken in Slack, two QBRs that slipped),
  so tasks have real ages and Open/Claimed/Done/Resolved all occur.
  Reads the journey rows too, and shows each account's last CS touch under
  every Slack task. Owners in settings.toml [slack] interactive_owners get one
  card per task with buttons (via the bot token, one a second) instead of
  the digest. QBR first: churn/expansion rows of an account with a QBR in
  the next 30 days are logged "covered by the QBR" and named on its QBR task.
  Follow-ups ([follow_ups] in settings): a programme flag (Stalled, Silent,
  Shelfware) on Self-service or Scale still standing 30 days after the first
  send gets one follow-up with a different angle (status "<status>
  (follow-up)"), then stops. Self-service: an email; Scale: a task for the
  Slack user who took the first one (never a new person; nobody took it =
  the pool, visible to the manager). Fatigue cap: one automated email per
  account per 14 days across all programmes; capped emails aren't logged,
  so they go out on a later run. A follow-up the manager handed back goes
  to the whole pool once ("<status> (follow-up, pool)"). Reassigned in the
  cockpit (task_assigned): the task goes to the new owner once ("Given to you
  by ..."), as a DM card ([slack] dm_reassigned); the old owner gets a "no
  longer yours" DM and, across teams, the new owner's manager an FYI (logged
  with channel notice). Its follow-up later goes to that person too.
  `none` and `report` rows aren't actions. The log only grows; delete it
  to start over.
- `cockpit_sync.py`: managers' decisions from the cockpit (Claude copies the
  page's database to `data/cockpit_decisions.json` before a run) -> events in
  `data/engagement/cockpit.jsonl`, rewritten each run, so nothing doubles.
  Decisions on older data (another as-of) are skipped; renewal forecasts and
  next steps are all kept (forecast_set, next_step_set: the history). First step of run_all.
  Never trust the page (5 Nov 2026): every decision is checked again against the
  crm and cs_team views (known account and owner, allowed values, real dates,
  a downsize between 0 and ARR, an owner only on their own accounts, a manager
  only on their team's, a reason on every forecast); a bad one is skipped and
  listed with why, never a crash; free text cut to [cockpit] max_text, control
  characters removed. The rights come from settings.toml [roles] (may(): a role
  without an action in its edit list is refused, "own" or "team" decides on which
  accounts; cs_team owner_type picks csm or member). Who decided stays
  self-declared (THREAT_MODEL.md).
  `tests/cockpit_sync_test.py`: no DuckDB, a small made-up CRM.
- `pull_clicks.py`: first step of run_all. Gathers the Slack clicks the AWS
  listener saved under [aws] slack_clicks_prefix into
  data/engagement/slack_clicks.jsonl (oldest first; rewritten only when a
  click is new; same name as the engine's merge in AWS, so an upload never
  doubles a click). combine() is shared with the engine Lambda (bundled): no
  click is dated after the run date (last events/<date>.jsonl + 1), so a
  click on a card from before buttons carried the run date, dated by the
  listener's clock, is brought back to it. Read-only on AWS. Not signed in or offline: says so and
  the run carries on. `tests/pull_clicks_test.py`: stand-in S3.
- `slack_loop.py`: shared Slack pieces (task cards, .env tokens, appending
  click events). `listener.py`: Socket Mode listener (`python listener.py`,
  leave it running): "I'll take it" -> task_claimed, "Log call" -> a form ->
  meeting_held with the flag's call topic, "Open timeline" -> the cockpit
  page at `#acc_xxx`. Clicks are dated on the run date. Which owners get
  cards (and which get DMs) is in settings.toml [slack]; everyone else keeps
  the digest. Tokens in .env: SLACK_BOT_TOKEN (xoxb), SLACK_APP_TOKEN (xapp),
  SLACK_DEMO_USER, SLACK_SIGNING_SECRET (for AWS). The same clicks are
  handled in AWS by the Slack listener Lambda (infra/), with the same pieces
  from slack_loop.py (make_event, claimed_blocks, call_form, form_answers,
  call_logged_blocks, handback_blocks); the Slack app talks to one listener at
  a time (Socket Mode on = the laptop, a Request URL = AWS). actions.py puts
  the run date on every button ("d"), so either listener dates a click on it.
- `tests/cockpit_test.js`: the cockpit test (Node + Playwright, a headless
  browser). Opens data/views.html like a person would: every tab in light and
  dark, desktop and phone (no script errors, no sideways scroll); numbers agree
  with the export and across tabs (ARR at risk, Act count, open tasks, ARR
  lost); with Owner = Mia every account on every tab is hers or marked
  outside, and the filters narrow; saving a next step, a forecast and a
  reassignment against a stand-in database, including the refusals; edge cases
  (Self-service, no owner, a tiny segment, a pool's book); the handover panel
  (only where a brief exists, every citation a real source, real names back in
  the brief, none in 'What the model saw', a citation opens its source, only
  the outgoing owner or manager confirms, a rewrite after a confirm is a draft
  again). Exit 1 on any FAIL.
- `tests/scenarios_test.js`: the 23 acceptance scenarios, automated by role
  (Mia the CSM, Priya the pool member, Sophie the manager, the leader, admin,
  anyone). Each works out its expected result from the export and checks the
  cockpit against it (riskiest account first, the same number on every tab, a
  plan or forecast shows everywhere, sorted lists, clicked numbers list that
  many accounts, an export with a missing column is stopped, phone width,
  dark-mode contrast). Writes data/scenario_results.json (pass, fail, manual);
  Claude copies it into the acceptance page ("Cockpit Acceptance Tests"
  artifact), where Nikodem adds what only a person can judge.
- `qbr.py`: QBR facts packs (run `flags.py` first). Writes
  `data/qbr_facts.json` (pseudonymised) and `data/qbr_user_key.csv` (the
  real user IDs; never sent to an LLM).
- `llm.py`: the one place that knows who writes the briefs (settings.toml
  [llm] provider): `bedrock` (Amazon Bedrock in our AWS account, signed in with
  the [aws] profile via `aws login --profile cairnpath`; no API key; model =
  the EU inference profile in bedrock_model) or `anthropic` (the Anthropic API,
  ANTHROPIC_API_KEY in `.env`). brief.py and handover.py ask it for a client.
  The AI spend ledger (5 Nov 2026): every model call goes through
  `llm.call(client, feature, account_id, ...)`, which checks the feature's
  monthly budget first ([llm.monthly_budget_usd.laptop] and [....aws]: each place its
  own share, as their ledgers can't see each other (audit 6 Nov 2026); qbr_brief, handover; real
  calendar month, UTC; at or over it = OverBudget, no call; a feature with no
  budget is refused too), then appends one row to data/ai_spend.csv ([llm]
  ledger_file: time, month, feature, account, provider, model, tokens, cost at
  the [llm] prices, laptop or aws, stop reason, seconds = how long the call took,
  13 Nov 2026; an older ledger gets the column, its old rows left empty). Our
  estimate, not Anthropic's bill. `python llm.py --spend`: this month by feature
  against its budget, with the median and slowest call. The ledger is the metrics
  half of LLM observability; no traces or prompt logs (on purpose: privacy).
  Over budget, brief.py and handover.py exit 1 naming the accounts (in AWS: a
  warning in run.json). The engine Lambda brings outputs/ai_spend.csv back
  before its steps and uploads it only when a call added rows; to_parquet.py
  makes it an Athena table (ai_spend; skipped while no call has been made).
  `tests/ai_spend_test.py`: a stand-in model, a temporary ledger.
- `brief.py`: QBR briefs from the facts packs (run `qbr.py` first; the provider
  comes from llm.py: Bedrock by default, or `ANTHROPIC_API_KEY` in the
  git-ignored `.env` for the anthropic provider). Writes
  `data/briefs/<account_id>.md`. `python brief.py acc_037` for one account.
  The API key lives only in `.env`: never print it, log it or commit it.
- `handover.py`: AI handover brief for a new owner (provider from llm.py;
  `--dry-run` skips the model). Reads three silos for the accounts in
  `stories/directory.csv`, each joined by its own key: support cases
  (`stories/support_cases.csv`, by helpdesk organisation name), emails
  (`stories/emails/*.eml`, by email domain), call transcripts
  (`stories/calls/*.txt`, by CRM account ID), plus the engine's facts from
  `data/views_data.json`. Redacts before the model: people -> Customer A /
  CS 1, emails and phone numbers masked, company -> "the customer". The model
  must cite a source ([S1], [E2], [C1], [DATA]) for every claim; the check lists
  unknown citations, uncited sentences and numbers in no source (a number
  written as a word in a source, "thirteen weeks", backs "13 weeks"); real names go
  back in after. Writes `data/handover/<acc>_sent.txt` (exactly what the model
  saw), `_key.json` (the redaction key: local only, never sent), `_check.json`
  and `<acc>.md` (a draft until the outgoing owner confirms it). File names
  in settings.toml [handover]. Not in run_all (it costs a few cents a run).
  Only a finished answer is saved: a reply cut short (stop_reason max_tokens)
  or empty writes no brief, just the reason in `_check.json` (brief.py too).
  Cache: a brief is rewritten only when its context changed since the last one
  ([handover] rewrite_on: documents new, edited or removed, by their own IDs;
  a flag's status; a newly booked QBR; the owner; the contract: renewal date and
  ARR, 12 Nov 2026), or the prompt or model
  changed. Daily number moves and scheduled calls don't count. The fingerprint
  and the reason for the last rewrite sit in `<acc>_meta.json`; `--force`
  rewrites anyway, `--dry-run` says what would happen.
  Contact list (PRIVACY.md upgrade A, optional): `stories/contacts.csv`
  ([handover] contacts_file: account_id, name, email, side), the CRM's
  contact export; each listed person is found anywhere in the text, even by
  first name. A first name shared by two people becomes "[name]"; one that's
  also a word ([handover] common_word_names) stays a word in "Will you..." or
  "12 May". National phone numbers are masked too; "The customer" is
  restored at a sentence start.
  Leak check (PRIVACY.md upgrade C): before the model call, leak_check() scans
  the exact text to be sent for anything person-shaped ("First Last" that
  isn't a label, role, greeting, date, product word or on [handover]
  leak_check_allow; those words are dropped first and two capitalised words
  still in a row count, so "Finance Director Hannah Weiss" is caught: audit
  6 Nov 2026), a name from the key, an email or a phone number; any hit
  stops the call for that account (no brief), lists what it found, and
  handover.py exits 1 (in AWS: a warning in run.json).
- `stories/`: three hand-written account stories (Yarrow Studio: champion
  leaving and a budget cut; Nettle Foods: expansion with a competitor and an
  overage dispute; Driftwood Labs: shelfware with a renewal in two weeks),
  plus `contacts.csv` (the CRM's contacts; Hannah Weiss at Nettle Foods is
  named only in an email body).
  `stories/answer_key.json` holds what a good brief must find and must not
  claim; for testing only, never shown to the model.
- `tests/handover_test.py`: privacy first (no real name, first name, email,
  phone or company in any `_sent.txt`, made fresh by the test itself with a
  dry run into a temporary folder, never the last run's files; runs without
  a key; a missing one FAILs), then quality once
  briefs exist (planted facts found, forbidden claims, citation checks). FAIL
  when a brief is missing after a run, empty, not a finished answer, finds
  under half the planted facts or claims a forbidden one. Run it
  on every PR that touches handover.py or stories/.
- `tests/leak_check_test.py`: the leak check with a stand-in model (no key,
  copies of the stories): the stories as they are pass; a name planted only in
  an email body slips past the redaction and stops the call; missed emails,
  phones and key names are caught; roles aren't people; the allow list works;
  --dry-run blocks too. Same PRs as handover_test.py.
- `tests/contacts_test.py`: the contact list (no model): a contact named only
  in an email body is labelled (without the list, the leak check stops it); a
  first-name-only mention is labelled; two Hannahs give [name]; no list = as
  before. Same PRs as handover_test.py.
- `tests/redaction_fixes_test.py`: PRIVACY.md fixes D (first names that are
  words: [handover] common_word_names), E (national phone numbers) and F
  ("The customer" restored), on made-up text. Same PRs as handover_test.py.
- `tests/handover_cache_test.py`: the cache, with a stand-in model that counts
  its calls (no key, no cost; temporary copies, real briefs untouched): no
  call when nothing changed or only daily numbers moved; a call for a flag
  status change, a booked QBR, a new email (even with a repeated subject and
  date), a reopened ticket and --force; none on --dry-run. Same PRs as above.
- `infra/`: the AWS side as code (CDK, Python). `app.py` reads settings.toml
  [aws] (account, region) and builds `data_stack.py`: one S3 bucket for the
  engine's data, private (all four public-access blocks, ACLs off), encrypted
  with our own KMS key ([aws] kms_key_alias: rotated yearly, Retain, deletion
  waits [aws] kms_deletion_window_days, S3 Bucket Key on; its policy refuses use
  outside S3 except to the admins, never key management: no lockout; roles get
  the key through the bucket grants, the Slack listener GenerateDataKey only),
  HTTPS only, versioned (old versions expire after [aws]
  keep_old_versions_days), kept if the stack is deleted (Retain), name left to
  AWS, tag project=cairnpath. Plus the check Lambda (CheckInputs, Python 3.13
  on arm64): wakes only when upload.py writes its marker ([aws] upload_marker,
  under inputs/), copies inputs/ to its /tmp, runs the same check_inputs.py
  (bundled by `infra/bundle.py` into infra/build/: the handler from
  `infra/lambdas/check_inputs/`, check_inputs.py, settings.py, settings.toml;
  never settings.local.toml) and writes `reports/check_inputs/latest.txt`,
  `latest.json` (status OK / WARN / ERROR, counts) and a dated copy of each.
  A data ERROR is a result, not a crash. May only read inputs/* and write
  reports/check_inputs/* (so it can't trigger itself); logs kept 30 days;
  memory, timeout and disk in [aws]. Plus the engine Lambda (RunEngine, same
  runtime; [aws] engine_max_runs = 1 would make a second run wait its turn, but
  a new account has 10 slots and AWS keeps 10 free, so it's 0 = off until the
  quota increase to 1000 asked on 6 Nov 2026 is approved):
  wakes when the check writes reports/check_inputs/latest.json, and
  if its status is ERROR writes outputs/run.json SKIPPED and stops (the gate).
  Otherwise it copies the engine's code (bundle.py ENGINE_FILES) and a data/
  folder into /tmp/work (inputs/ plus state/action_log.csv), runs [aws]
  engine_steps in order (run_all.ps1 minus the laptop-only cockpit_sync,
  check_flags and actions), and uploads every file the steps wrote to
  outputs/, then last_run.txt and run.json (status OK / FAILED, step times),
  run.json last. A failing step: FAILED, then it raises (no retry). Reads
  inputs/*, state/*, the check summary; writes only outputs/*. DuckDB for Lambda
  (Linux arm64, Python 3.13, version from requirements.txt) is fetched by pip
  with --platform at synth time, cached in infra/build/ (no Docker);
  CAIRNPATH_SKIP_DEPS=1 skips it for tests. Handover briefs in AWS, only on
  request (5 Nov 2026): no automatic run calls the model. The steps run
  export_views.py, then "handover.py --dry-run" (free: marks each brief out of
  date in <acc>_stale.json when its context changed; rewritten as no changes,
  never deleted, since the engine may not delete), then export_views.py again
  (the cockpit shows "Out of date" with why). Only an event with {"briefs":
  true} swaps in the real [aws] briefs_step (handover.py), and only the admin
  can send one: `request_briefs.py` (S3 events carry Records and are refused,
  the schedule's input is fixed). Steps may carry arguments. Both are in [aws]
  engine_optional_steps: if one fails (model API down, leak check) or there's
  no key, the run is still OK, with a warning in run.json. The key: Parameter Store SecureString ([aws]
  api_key_parameter, filled by put_key.py), read at run time, only on a briefs
  request, and passed to the briefs step only (never in the Lambda's settings, never printed); the
  engine may only ssm:GetParameter that one parameter. Provider in AWS: [aws]
  briefs_llm_provider (llm.py honours CAIRNPATH_LLM_PROVIDER). The cache
  outputs/handover/ is downloaded first and only changed files go back;
  *_key.json (redaction keys) never leave the Lambda; stories/ is bundled
  without answer_key.json and README.md. Libraries for Lambda: bundle.py
  LAMBDA_PACKAGES (duckdb, anthropic), versions from requirements.txt.
  Plus a daily safety net: EventBridge Scheduler ([aws] engine_schedule and
  engine_schedule_timezone) starts the engine with {"trigger": "schedule"};
  if the last outputs/run.json is OK and used the same input check and engine
  code (a fingerprint of the bundled scripts and settings, in run.json "code"),
  it stops (SKIPPED "up to date", run.json untouched), otherwise it runs (a
  missed or failed event-driven run is caught up, and a deploy with new engine
  code runs at the next schedule). Its role may only invoke the engine.
  Plus Athena: a Glue database ([aws] glue_database) filled by a Glue crawler
  that reads outputs/tables/ (one target per [aws] athena_tables entry, tables
  made at folder level 4 so same-shaped flag tables stay separate; its role
  is our own narrow policy, not AWS's AWSGlueServiceRole, which may write and delete
  any aws-glue-* path in any bucket: audit 6 Nov 2026; run it by
  hand once and after a column change: `aws glue start-crawler`), and an
  Athena workgroup ([aws] athena_workgroup, settings enforced, results to
  athena-results/ encrypted with our KMS key and deleted after [aws] athena_results_keep_days,
  a query scanning over [aws] athena_scan_limit_mb is cancelled).
  Plus the Slack listener: an HTTP API (API Gateway, one route POST
  /slack/actions, rate limit [aws] slack_rate_per_second / slack_burst, an
  access log without bodies) in front of the SlackListener Lambda
  (`infra/lambdas/slack/`, bundled with slack_loop.py, settings.py,
  settings.toml; no libraries). It checks Slack's signature (HMAC-SHA256 of
  "v0:<timestamp>:<body>" with the signing secret, constant-time compare) and
  refuses with 401 anything unsigned, wrongly signed or older than [aws]
  slack_max_age_seconds, before any other work. Then it does what listener.py
  does and saves each click as its own file, [aws]
  slack_clicks_prefix/<run date>/<event_id>.json (state/clicks/; the event_id is
  made from Slack's own click id, so a replayed request overwrites, never doubles:
  audit 6 Nov 2026). Its role may
  only write there and read its two Parameter Store secrets ([aws]
  slack_signing_secret_parameter, slack_bot_token_parameter; put_key.py
  --slack). The engine merges the clicks into
  data/engagement/slack_clicks.jsonl before its steps; clicks don't wake it,
  but the daily schedule runs when a click came in after the last run. The
  address is printed after a deploy (SlackRequestUrl) for the Slack app's
  Interactivity settings.
  Plus the data perimeter (zero trust, 4 Nov 2026): two Deny statements in
  the bucket policy. DenyOutsideThisAccount: any identity outside [aws]
  account_id, for everything. DenyDataToAnyoneElse: reading, writing,
  deleting and listing the data for anyone but the four workload roles
  (check, engine, Slack listener, crawler), [aws] admin_role_names (any IAM
  path) and break_glass_users, whatever their own permissions say. AWS
  services acting for the bucket are exempt (aws:PrincipalIsAWSService).
  Bucket-level settings (the policy itself) are deliberately not denied, so
  an admin can always fix a mistake: no lockout.
  Plus GuardDuty on trial ([aws] guardduty, 30-day free trial from 5 Nov 2026
  to about 5 Dec 2026, charged after): one detector, only S3 data events and
  Lambda network activity on; EKS, EC2 malware, RDS and runtime plans off.
  false + cdk deploy removes it (findings too: save them first).
  `guardduty_report.py` (read-only; `--samples` makes sample findings,
  `--save` writes data/guardduty_report.md): real findings by severity and
  type, samples left out, and the cost so far by feature, no console needed.
  `tests/guardduty_report_test.py`: a stand-in GuardDuty; with botocore
  installed, the request values are checked against AWS's own API model.
  Plus IAM Access Analyzer (5 Nov 2026, free): one ACCOUNT analyzer ([aws]
  access_analyzer_name) that reads every resource policy in the account and
  lists what another account or the public could reach; the paid "unused
  access" analyzer is left out. `access_report.py` (read-only, `--save` writes
  data/access_report.md): active findings, public first, with who can reach
  each and under which condition. `tests/access_report_test.py`: a stand-in
  analyzer; with botocore installed, every request is validated against AWS's
  API model.
  Plus Cost Anomaly Detection (5 Nov 2026, free): a custom monitor on this
  account (not AWS's one-per-account services monitor, which AWS may have made
  already) and a daily email when a day's unexpected cost reaches [aws]
  cost_alert_threshold_usd. The address, [aws] cost_alert_email, is personal:
  it lives in settings.local.toml (the repo is public); empty = not built.
  Plus the public demo site (6 Nov 2026, [aws] site): CloudFront in front of a
  second bucket of its own (never the data bucket): private (all four public-access
  blocks, ACLs off), S3-managed encryption (the pages are public on purpose, so
  CloudFront gets no grant on our data key), HTTPS only, Retain; CloudFront is its
  only reader, through an origin access control. GET and HEAD only, index.html as
  the root, [aws] site_price_class, and a response headers policy: HSTS a year,
  no framing, no MIME guessing, a content security policy (data_stack.SITE_CSP:
  the page and its Google fonts, no form posts). Its bucket policy denies writes
  and deletes to everyone but the engine and the admins. The engine role may only
  put pages and list names there. After a good run the engine copies
  data/site/*.html (flat) to the site bucket, only pages whose MD5 changed, with
  Cache-Control max-age [aws] site_cache_seconds, never to outputs/, never
  deleting; a failed run sends nothing. Address: the SiteUrl output.
  Its own address (optional, option B 6 Nov 2026): [aws] site_domain plus
  site_certificate_arn give CloudFront that name (TLS 1.2+); the domain and its DNS
  are at Cloudflare (two records by hand: the certificate's validation CNAME and
  the domain's CNAME to SiteCloudFrontDomain, both "DNS only"). `site_cert.py`
  asks ACM in us-east-1 for the certificate (CloudFront's rule; a script, not CDK,
  because a us-east-1 stack would need a bootstrap in a region the SCPs keep
  closed), prints both records, `--wait`s until issued and prints the two settings
  lines. `tests/site_cert_test.py`: stand-in ACM.
  Nikodem deploys from `infra/` (`cdk
  bootstrap` once, then `cdk diff` and `cdk deploy`, all with `--profile
  cairnpath`); steps in `infra/README.md`.
- `tests/infra_test.py`: checks the template before anything reaches AWS
  (each safety setting above, account and region from settings, the bucket
  name output; for each Lambda: runtime, sizes from settings, its one trigger,
  retries, least privilege and never writing where a trigger listens; Athena:
  database name, the crawler's targets, table level and read-only role, the
  workgroup's enforced results location, encryption and scan cap, results
  expiry, nothing waking on outputs/ or athena-results/; the Slack listener:
  its role (write clicks only, its two secrets only), one route, throttling,
  access log, only this API may call it, nothing waking on state/; the data
  perimeter: both statements, the exempt roles and admins, bucket settings left
  open; GuardDuty: one detector with only the plans we need, or none when off;
  Cost Anomaly Detection: a custom monitor and a daily email from settings, or
  nothing without an address, and no address in settings.toml;
  the public site: its own private bucket (blocks, SSE-S3, Retain), CloudFront the
  only reader through an OAC, HTTPS only, GET/HEAD, the headers and the CSP, the
  write perimeter, the engine's put-and-list-only rights, nothing when site = false;
  Access Analyzer: one free account analyzer, named from settings).
  Exit 1 on any FAIL.
- `tests/slack_lambda_test.py`: the Slack listener's handler from its real
  bundle, with requests signed like Slack signs them (made-up secret) against
  stand-in S3, Parameter Store and Slack: unsigned, wrongly signed, tampered
  stale and future-dated requests refused with nothing saved; a replay saved once; each button's event and card
  update; a bad run date can't leave state/clicks/; Slack down = click still
  saved; secrets read once and never printed. Exit 1 on any FAIL.
- `to_parquet.py`: the last engine step. Writes each [aws] athena_tables
  entry as data/tables/<name>/<name>.parquet (Athena: one folder = one table):
  the flag and account value CSVs, tasks (from views_data.json) and the crm and
  cs_team views (engine column names). Parquet keeps types, reads only the
  columns a query needs, and has no trouble with commas inside the reasons.
- `tests/check_lambda_test.py`: builds the Lambda's folder as the deploy does
  and runs its handler on the demo inputs through a stand-in S3: every input
  checked, the marker skipped, reports only under reports/check_inputs/, then a
  CRM file missing a required column gives ERROR (no crash). Needs data/.
- `tests/engine_lambda_test.py`: the engine Lambda's bundle holds every repo
  module the steps import; the gate (check ERROR = SKIPPED, nothing computed);
  a run against a stand-in S3 (inputs and action log in, only new files out,
  all under outputs/, run.json last, work folder cleaned); the schedule (runs
  when there's no good run yet, a newer check or a failed last run; otherwise
  SKIPPED "up to date" with no writes; the gate still applies); the briefs
  (the key only reaches the optional step, read decrypted from Parameter Store,
  in no log; the cache uploaded only when changed; redaction keys never
  uploaded; no key or a failing model = OK with a warning; stories bundled
  without the answer key); a failing step gives
  FAILED and raises. With DuckDB installed it runs the real steps and compares
  the flags with the laptop's data/; without, stand-in steps. Needs data/.
- `upload.py`: copies the inputs named in [data] to the data bucket ([aws]
  bucket, under [aws] inputs_prefix, same folder layout). Only new or changed
  files go up (local MD5 vs. the object's ETag, or, with the KMS key where the
  ETag isn't the MD5, the "md5" label each upload carries), so reruns send
  nothing and versioning records real changes only. `--all` sends everything
  again (files from before the key get it). Checks the bucket is ours
  (ExpectedBucketOwner = [aws] account_id). Never the answer key or the
  engine's outputs, except the action log (data/action_log.csv, the tasks
  already sent), which goes to [aws] state_prefix for the engine Lambda. Demo data only: a company's own exports
  (settings.local.toml) are refused, they stay on the laptop. After a real
  change it writes the upload-complete marker last, which wakes the check
  Lambda once per batch. Prints the first 10 files, then a count. `--dry-run`
  says what would go. Not in run_all yet.
  Sign in first (`aws login --profile cairnpath`).
- `security_check.py`: live security checks, read-only (`python
  security_check.py` after `aws login`). Finds the deployed roles and functions
  in the CairnpathData stack and asks the IAM policy simulator, with the
  bucket's and the KMS key's real policies, 50 questions (4 on the public search role, 13 Nov 2026: it
  can't read data, change the site, read the API key or decrypt; 7 on the site bucket, with
  its own policy: only the engine writes, it can't read or delete, the Slack listener
  and an s3:* identity can't change a page): things each role must NOT be able to do
  (the engine writing its inputs or the check's verdict, deleting, reading
  another secret, anything over plain HTTP; the check writing where it's
  triggered or reading the API key; the schedule starting the check or
  reading data; the crawler writing (even an aws-glue-* path) or reading raw inputs; the Slack listener
  reading any data, writing anything but clicks or reading the API key; the
  engine reading Slack's bot token; the perimeter: an identity in the account
  with s3:* on everything, simulated as the schedule role plus an extra
  allow-all policy, still can't read or write the data; the key: the engine
  calling KMS directly, the Slack listener decrypting, the schedule using it)
  and controls that must work (the admin, by its real ARN, still reads the data
  and can fix the bucket and key policies; the engine decrypts and the listener
  encrypts through S3; so a check that denies everything can't pass). infra_test.py
  checks the template; this checks what's deployed. Exit 1 on any FAIL.
  `tests/security_check_test.py`: stand-in CloudFormation, simulator and S3:
  roles told from same-prefix functions, the bucket policy and plain HTTP
  included, a leaking role caught, an all-deny answer caught, key questions
  with the key's policy and kms:ViaService, a key without its rule caught.
- `put_key.py`: stores ANTHROPIC_API_KEY from .env in Parameter Store as a
  SecureString ([aws] api_key_parameter), for the briefs in AWS. Never typed
  into a command, never printed; rerun after rotating the key. `--slack`
  does the same for SLACK_SIGNING_SECRET and SLACK_BOT_TOKEN (both or
  neither) for the Slack listener in AWS.
  `tests/put_key_test.py`: stand-in Parameter Store, made-up key: stored as a
  SecureString with overwrite, never printed in whole or in part.
- `request_briefs.py`: the only way the model runs in AWS. Starts the engine
  function (name from the stack's EngineFunction output) asynchronously with
  {"trigger": "briefs", "briefs": true}, then waits for that run's run.json
  and says which briefs were rewritten (`--no-wait` returns at once). Admin
  only (aws login). `tests/request_briefs_test.py`: stand-in CloudFormation,
  Lambda and S3; with botocore, the invoke checked against AWS's API model.
- `tests/upload_test.py`: a stand-in S3 (no AWS, no cost): first run sends
  every input, a rerun nothing, then only a changed and a new file; keys use
  "/"; the marker written once per real change, last, never on a rerun or
  dry run; the answer key and outputs stay local; own data refused; another
  account's bucket is refused. Exit 1 on any FAIL.


## Status and history (was "Planned next" until 13 Nov 2026)

- Move to AWS (Phase 6). The briefs can run on Bedrock (llm.py), signed in
  with `aws login`; code, login and libraries work (the request reaches
  Bedrock), but the account's quota is 0: AWS declined the increase on 4 Nov
  2026 (new account, no usage or billing history yet; ask again after the next
  billing cycle, early December 2026). Until then a laptop keeps the API key:
  settings.local.toml with [llm] provider = "anthropic"; in AWS the briefs
  read it from Parameter Store (put_key.py; free, unlike Secrets Manager, and
  a static third-party key needs no rotation service). The AWS setup (2 Nov 2026): a Builder ID organization
  (management …3914, identity delegated admin …6241, Future Unicorn …9557);
  its SCPs open only eu-north-1 fully; its Identity Center has no permission
  sets, so the CLI uses the Builder ID session in the management account
  (profile cairnpath, settings.toml [aws]); workloads sit in the management
  account for now, a deliberate exception (all in code, so they can move).
  The CDK app with the data bucket is in infra/, deployed 3 Nov 2026
  (CairnpathData, bucket name in settings.toml [aws]); upload.py copies the
  inputs there, the check Lambda checks each upload (reports/check_inputs/),
  and the engine Lambda runs flags to cockpit export after a clean check
  (outputs/). Warehouse decided 3 Nov 2026: DuckDB in Lambda runs the engine
  (one SQL dialect, same numbers as the laptop); Athena + Glue tables over
  outputs/ for ad-hoc questions (built 3 Nov 2026: crawler, Glue database,
  workgroup), a daily schedule as a safety net, and the handover briefs in AWS
  (key in Parameter Store, cache in outputs/handover/). The Slack listener on
  API Gateway (signature checked, clicks to state/clicks/) built 4 Nov 2026.
  The laptop pulls the clicks first in run_all (pull_clicks.py). Redaction
  upgrades C (leak check) and A (contact list) done 4 Nov 2026; B (a PII
  detector) deferred the same day: no data to make the case, Comprehend isn't
  in eu-north-1 and reads English and Spanish only, Presidio is heavy
  (PRIVACY.md, spec Feature requests). Zero trust: the data perimeter (bucket
  policy) built 4 Nov 2026; GuardDuty on its free trial from 5 Nov 2026 (keep
  or switch off before about 5 Dec); IAM Access Analyzer 5 Nov 2026; our own KMS key
  5 Nov 2026; Cost Anomaly Detection 5 Nov 2026 (AWS spend only: it never
  sees the Anthropic API bill); the AI spend ledger with budgets per feature
  5 Nov 2026; GitHub push protection and a $10 Anthropic spend limit 5 Nov
  2026; cockpit decisions checked on our side and AI briefs only on the
  admin's request (request_briefs.py) 5 Nov 2026; Access Analyzer baseline 0.
  Phase 7 done. Phase 8: README rewritten 5 Nov 2026 (architecture diagram,
  one run step by step, controls and cost tables); demo walkthrough (DEMO.md),
  CV line and interview story 6 Nov 2026. The public demo site (CloudFront,
  sandbox pages) 6 Nov 2026.
- Parked ideas: maturity assessment score, customer journey stage and
  special account flags as extra QBR inputs. AI to assist CS (5 Nov 2026,
  Nikodem thinking it over; spec Feature requests): assist, not replace, e.g.
  a draft email for one account for the CSM to review and send, mixed signals
  explained, a brainstorm with the account's context; kept apart from a bot
  that acts (a Self-service inbox agent). Demo: run once against an answer
  key, save transcripts and scorecard.
- AI on unstructured data (13 Nov 2026, planner Phase 9; asked in nearly every
  interview): RAG first, cheap: account search over emails, tickets and call notes
  with DuckDB full-text search (redacted chunks with citations, a generated corpus
  of about 30 accounts with an answer key and a recall test), then "ask about this
  account" from the laptop (its own ask_account budget), vectors next to keywords,
  a Cairnpath MCP server (the engine's facts and the search as tools, local first,
  same redaction and role rules), an AI cost demo (thread cleanup, prompt caching
  where it pays, Haiku extraction stored as rows; AI_COST.md has each decision), and last a live Ask box in the cockpit (a new public address: sign-in,
  rate limit, budget per person, THREAT_MODEL.md). About $0.013 a question at
  Sonnet prices; RAG doesn't suit the handover brief (no narrow question).
- Handover brief, long histories (needs years of data we don't have): keep a
  stored account-history layer (e.g. the first contract years: trends, key
  events), confirmed by a person, and mine fresh insight only from activity
  since the last renewal; re-read everything from scratch now and then, so
  errors in the stored layer don't compound.
- Engagement: history, new email_sent events, the first journeys and the
  team report (time to action) and the welcomed vs. control report exist;
  the views page is built; next, outcome after a call and the move to AWS.
