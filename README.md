# Cairnpath engagement engine

A working demo of a signal-triggered customer success engine. Product usage
data goes in; flags come out that tell Customer Success **who needs attention,
why, and who should act**.

Cairnpath is a fictional product analytics SaaS with about 500 European
customers across three tiers. The data is generated, but the rules are the
kind a real CS team runs.

## What it does today

| Flag | Looks for | Who acts |
| --- | --- | --- |
| Activation | New customers who are activated, stalled or silent, plus gaps in our own records (no CRM entry, no CSM assigned) | Automation, regional pools, named CSMs, CS ops |
| Churn risk | Utilisation falling for two months, weekly active users dropping, and rising ingestion errors as a sign of a technical cause | Same routing, with Watch and Act levels |

Every flag shows the numbers behind it (for example *"Utilisation 52%, then
39%, then 27%"*), who it is routed to and what to do next.

## Run it

Windows / PowerShell, from the repo root:

```
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python generate_events.py   # ~500 accounts, 90 days of events
python explore.py           # the book of business at a glance
python flags.py             # activation and churn flags, plus work queues
```

## Design choices

- **SQL computes the facts.** Every number a CSM sees comes from a query, not
  a model.
- **Every alert explains itself** and comes with an action. Alerts that only
  show a score get ignored.
- **False alarms are treated as a bug.** Rising errors on their own flagged
  41 healthy accounts, so they now only count alongside a usage signal.
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
