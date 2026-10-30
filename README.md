# Cairnpath engagement engine

A working demo of a signal-triggered customer success engine. Product usage
data goes in; flags come out that tell Customer Success **who needs attention,
why, and who should act**.

Cairnpath is a fictional product analytics SaaS with about 500 European
customers across three tiers. The data is generated, but the rules are the
kind a real CS team runs.

Why it exists: a CRM is built for sales. It knows opportunities and contracts,
not how customers use the product or what CS did about it, so a CS team at
scale ends up with a book of business it can't see. This engine joins usage
and CS engagement into one view of the success book of business, and tells
each CSM, pool and manager where to act.

It doesn't replace CSMs. It lets a CS organisation scale: automation covers
the many accounts that only need a nudge, so people's time goes where it
changes the outcome. Think of it as an engagement funnel, measured at every
stage: signal (flags) -> task (actions) -> action taken (action status, time
to action) -> outcome at renewal (backtest).

## What it does today

| Flag | Looks for | Who acts |
| --- | --- | --- |
| Activation | New customers who are activated, stalled or silent, plus gaps in our own records (no CRM entry, no CSM assigned) | Automation, regional pools, named CSMs, CS ops |
| Churn risk | Utilisation falling for two months, weekly active users dropping, and rising ingestion errors as a sign of a technical cause; plus Shelfware: live 60+ days and never above 40% utilisation (the rollout never happened) | Same routing, with Watch and Act levels; Shelfware asks for an adoption review (Self-service: a use-case inspiration email) |
| Expansion | Accounts heading over their contract with a growing team, plus volume rising without users (often over-tracking) | CSMs and pools investigate and build the growth case; Sales only signs |
| QBR | Enterprise QBRs in the next 30 days (with a facts pack per account) and QBRs overdue with nothing booked | Named CSMs |

Every flag shows the numbers behind it (for example *"Utilisation 52%, then
39%, then 27%"*), who it is routed to and what to do next.

## Run it

Windows / PowerShell, from the repo root. The quick way, after the one-time
setup below: `.\run_all.ps1` pulls the latest code and runs everything in
order (`-Regenerate` for fresh data, `-Actions` to post to Slack); the output
goes to `data\last_run.txt`. Step by step:

```
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python generate_events.py   # ~500 accounts, 90 days of events
python check_inputs.py      # are the inputs complete? what will and won't work
# your own data instead of the fake one: see "Running on your own data" in DATA_CONTRACT.md
python explore.py           # the book of business at a glance
python flags.py             # activation, churn, expansion and QBR flags, plus work queues
python check_flags.py       # test the flags against the generator's answer key
python backtest.py          # did the flags see churn coming? recall, precision, warning time
python journeys.py          # follow-ups based on the last CS touch (unopened emails, no meeting in 30 days)
python team_report.py       # how fast each CSM and pool acts on churn and expansion Act accounts
python onboarding_report.py # does the getting-started email help? welcomed vs. control group
python export_views.py      # the views page: open data/views.html in a browser
node tests/cockpit_test.js  # optional: tests the cockpit in a headless browser (needs Node + Playwright)
python actions.py           # tasks to Slack: digests, plus cards with buttons for the demo owners
python listener.py          # in a second window: handles the buttons; clicks flow back as events           # post new tasks to Slack and log each action once (run it twice: the second run adds nothing)
python qbr.py               # facts pack for each QBR in the next 30 days
python brief.py             # LLM-written QBR brief per pack (needs ANTHROPIC_API_KEY in .env)
```

To tune the engine, edit `settings.toml` (every threshold and time window,
with a comment on each), then rerun `flags.py` and `check_flags.py`.

## How each tier is handled

[ENGAGEMENT_LOGIC.md](ENGAGEMENT_LOGIC.md) shows the logic as diagrams: who
covers each tier, the Self-service email journey, the Scale shared queue with
its hand-back, Enterprise's QBR-first rule, and what "done" means.

## Running it on your own data

[DATA_CONTRACT.md](DATA_CONTRACT.md) lists the five inputs (events, CRM, CS team,
engagement history, onboarding holdout), their columns, and which flags stop
working when one is missing.

## Design choices

- **SQL computes the facts; the LLM only writes them up.** The model gets a
  facts pack with pseudonymised users, never raw data. Every number in its
  draft is checked against the pack, and anything it can't trace is listed
  as "unverified" for the CSM to check. Real user IDs go back in afterwards,
  outside the model.
- **Every alert explains itself** and comes with an action. Alerts that only
  show a score get ignored.
- **Actions happen once only.** Flags are raised again on every run, so
  `actions.py` checks an action log before doing anything and records what
  it did. A daily run never emails the same customer twice, and facts the
  pipeline creates ("welcome sent") stay out of the CRM.
- **The rules are tested against an answer key.** The generator records
  which behaviour it gave each account; `check_flags.py` checks that every
  flag finds the accounts it should and lists each miss and false alarm.
  It caught a 2-person account flagged as churn risk because one user had a
  quiet week ("-48%"), so a drop now has to be at least one whole user.
- **False alarms are treated as a bug.** Rising errors on their own flagged
  41 healthy accounts, so they now only count alongside a usage signal. A
  straight-line forecast flagged 32 healthy accounts as "heading over the
  cap", so forecasts now only count for accounts already at 80% and
  clearly climbing.
- **It's a CSM cockpit, not an org-wide tool.** Customer messaging triggered
  by usage or plan limits belongs to Product. When a utilisation flag fires,
  the CS job is to investigate and analyse, so churn and expansion never
  email customers; Self-service rows go to a report for pattern analysis.
- **Automation supplements CSMs; it doesn't replace them.** Rules catch the
  long tail across hundreds of accounts. They will always miss edge cases:
  healthy accounts that dip, and churners that look fine until they leave.
  Catching more of those is a constant effort as CS scales, and it's the
  main reason the top tier keeps a named CSM, whose judgement covers what
  the signals miss.

## Honest limits of the demo

The data is generated with clear account "personalities", so good and bad
accounts separate more cleanly than in real life. The flags look more
accurate here than they would on real customer data.
