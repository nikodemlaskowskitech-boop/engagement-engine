# Data contract

What the engine needs to run on someone else's book of business: seven inputs,
their columns, what each column means, and which flags stop working without it.
Today `generate_events.py` writes the first six and the cockpit the seventh. A real team would export them from
their product analytics, CRM and CS tools instead, in this shape.

The rule of thumb: **the engine reads the CRM, it doesn't replace it.** Facts the
pipeline creates (actions, "covered by the QBR") go in its own action log, never
back into these inputs.

Before the first run on new data, check it: `python check_inputs.py <folder>`
reads every input below, reports what's missing or broken in plain English,
and ends with what the engine can and can't do with that data.

## At a glance

| Input | File | Required? | Without it |
| --- | --- | --- | --- |
| Product events | `data/events/*.jsonl` | Yes | Nothing runs |
| CRM accounts | `data/crm_accounts.csv` | Yes | Nothing runs (accounts with events but no row show as Handoff gap) |
| CS team | `data/cs_team.csv` | Yes, if any tier has people | Tasks can't name an owner or a manager; no escalations, no team report |
| Engagement history | `data/engagement/*.jsonl` | No | No journeys, no time to action, no "last touch"; flags still work |
| Onboarding holdout | `data/onboarding_holdout.csv` | No | Everyone gets onboarding emails; no welcomed vs. control report |
| Renewal outcomes | `data/renewal_outcomes.csv` | No | No backtest (recall, precision, warning time) |
| Cockpit decisions | `data/cockpit_decisions.json` | No | Managers can't reassign tasks or approve hand-backs from the cockpit (Slack still can); renewal forecasts stay in the cockpit only |

## 1. Product events

One JSON object per line, one file per day (any file name ending `.jsonl`).
Timestamps in UTC. The schema is declared in `cairnpath_db.py`, so a file that
breaks it fails loudly instead of quietly.

| Field | Type | Meaning |
| --- | --- | --- |
| event_id | text | Unique per event (a UUID) |
| event_name | text | What happened (list below) |
| event_type | text | `product` (a person did it) or `system` (the platform reports it) |
| timestamp | ISO 8601, UTC | When it happened |
| account_id | text | The customer account; must match the CRM |
| user_id | text | Product events only: who did it. Pseudonymous IDs are fine |
| properties | object | Per event, below |
| context | object | Optional (SDK version, source); not used by the flags |

Events the flags read:

| event_name | Used for | If missing |
| --- | --- | --- |
| `daily_event_volume` (properties.event_count) | Utilisation: the basis of churn, Shelfware and expansion | Required |
| `sdk_installed` | Activation: when the customer went live | Activation can't say Activated or Stalled |
| any product event with a user_id | Active users: activation, churn breadth, expansion breadth, champions | Required |
| `ingestion_error` (properties.error_code) | "Technical check first" on churn | That push to Act is off |
| `experiment_launched` | Underused add-ons in the QBR pack | An experiments add-on always reads as unused |

Go-live is never sent: it's the first event we see from the account.

Example:
```json
{"event_id": "524dbd76-33c7-4f58-abf0-493343b30489", "event_name": "sdk_installed", "event_type": "product", "timestamp": "2026-07-24T16:04:43Z", "account_id": "acc_203", "user_id": "acc_203_usr_01", "properties": {}, "context": {"sdk_version": "2.1.0"}}
{"event_id": "abd1b84d-0976-41e4-af25-453290fa8066", "event_name": "daily_event_volume", "event_type": "system", "timestamp": "2026-10-18T07:00:11Z", "account_id": "acc_299", "user_id": null, "properties": {"event_count": 16879}}
```

## 2. CRM accounts

One row per account, CSV with a header.

| Column | Type | Required? | Meaning |
| --- | --- | --- | --- |
| account_id | text | Yes | Same ID as in the events |
| account_name | text | Yes | Shown on every task |
| tier | text | Yes | `Enterprise`, `Scale` or `Self-service`; decides routing |
| region | text | Yes | Sub-region; for reports and the local email hour |
| contract_start | date | Yes | Activation window starts here |
| renewal_date | date | Yes | Expansion and Shelfware timing ("renewal in 8 weeks") |
| contracted_events | number | Yes | Annual committed volume: the bottom of the utilisation sum |
| add_ons | text | No | Paid add-ons, e.g. `experiments`; for the QBR pack |
| csm_owner | text | Enterprise | The named CSM's owner_id; empty = "No CSM assigned" |
| cs_pool | text | Scale | The pool's owner_id |
| last_qbr_date | date | No | QBR overdue |
| next_qbr_date | date | No | QBR coming up, and QBR first |
| arr | number | No | Annual recurring revenue (some CRMs call it annual order value). Missing = estimated from contracted volume x the list price in settings.toml [money], marked as an estimate. Name the column in [money] arr_column |

Example:
```csv
account_id,account_name,tier,region,contract_start,renewal_date,contracted_events,add_ons,csm_owner,cs_pool,last_qbr_date,next_qbr_date,arr
acc_003,Driftwood Banking,Enterprise,UK&I,2026-02-10,2027-02-10,300000000,experiments,csm_anna,,2026-07-01,2026-10-26,135000
```

## 3. CS team

| Column | Meaning |
| --- | --- |
| owner_id | Matches csm_owner / cs_pool in the CRM |
| owner_name | Shown on tasks |
| owner_type | `csm` or `pool` |
| region | The region they sit in: their team. Not always the region of their accounts (a CSM can cover a customer elsewhere) |
| manager | Who gets the escalations (e.g. `mgr_james`) |

```csv
owner_id,owner_name,owner_type,region,manager
csm_anna,Anna Whitfield,csm,UK&I,mgr_james
```

## 4. Engagement history (optional)

Same JSON shape as the events, `event_type` = `engagement`. This is what CS did
and how customers responded; the pipeline appends to it (emails it sends,
Slack clicks), so it grows over time.

This is usually the hardest input to get. In most companies the CRM holds it,
but sales-shaped: emails auto-logged by a plugin, calls as generic "activities",
no topic, no outcome. Good enough to start: **meetings and emails with account,
date and owner**, plus a topic where one exists (often from the activity subject
or type). Without a topic, journeys still work, but time to action can't tell a
churn call from a check-in. Going forward, the pipeline's own actions and Slack
clicks write proper engagement events, so the history gets better every day the
engine runs.

| event_name | properties | Meaning |
| --- | --- | --- |
| `email_sent` | owner_id, topic | An email went out (`automated` or a person) |
| `email_opened` | owner_id, topic | The customer opened it (user_id = who) |
| `workshop_attended` | owner_id, topic | A customer user attended |
| `meeting_held` | owner_id, topic (+ outcome, note, slack_user from Slack) | A call or meeting took place |
| `task_claimed` | owner_id, topic, slack_user | Someone took a task in Slack |
| `task_assigned` | owner_id (the new owner), topic (the flag), status, manager | A manager gave a task to someone else in the cockpit |
| `handback_approved` | owner_id (the manager), topic `handback`, slack_user or manager | A manager approved handing a follow-up back to the pool |
| `next_step_set` | owner_id (who), topic `next_step`, status (call, qbr, email, internal), due (date), note, outcome (owner or manager) | The next step planned on an account ("action planned"). Internal |
| `forecast_set` | owner_id (who), topic `renewal_forecast`, status (renew, downsize, churn), amount (value expected at renewal), note (reason), outcome (owner or manager) | An owner or their manager set the account's renewal forecast in the cockpit. Internal: never a customer touch |

Topics that matter: `getting_started` (journeys), `churn_call`, `growth_call`,
`activation_call` (time to action), `qbr` (QBR first and slips), `check_in`.
`context.source` says who wrote the row: `cs_history`, `actions.py`, `slack` or `cockpit`.

## 5. Onboarding holdout (optional)

The control group of the onboarding programme: these accounts get no onboarding
emails, so the welcomed vs. control report can measure the effect.

```csv
account_id,held_out_since
acc_074,2026-08-09
```

## 6. Renewal outcomes (optional, for the backtest)

What happened at each renewal: the CRM's closed renewals, exported. Without it
the engine still runs; you just can't measure how well the flags predict.

```csv
account_id,renewal_date,outcome
acc_003,2027-02-10,downsized
```

`outcome` is one of `renewed`, `expanded`, `downsized`, `churned`.

## 7. Cockpit decisions (optional)

What managers decided in the published cockpit's Tasks tab. The page keeps them
in its own small database; today Claude copies them to this file before a run
(with a real backend, the pipeline reads the store directly). `cockpit_sync.py`
turns them into `task_assigned` and `handback_approved` events in
`data/engagement/cockpit.jsonl`; decisions made on older data (another
`as_of`) are skipped.

```json
[{"kind": "assign", "account_id": "acc_018", "flag": "churn", "status": "Act",
  "from_owner": "pool_dach", "to_owner": "csm_lukas", "manager": "mgr_sophie",
  "as_of": "2026-10-18", "decided_at": "2026-10-23T11:21:07Z", "decided_by": "<viewer id>"}]
```

`kind` is `assign` (with `to_owner`) or `handback`. The latest decision per task wins.

Renewal forecasts (`kind` `forecast`) and next steps (`kind` `next_step`, with
`type`, `due`, `note`, `by`, `role`) sit in the same file. Every one is kept
(the history shows how a forecast moved) and none is skipped for older data:
like a CRM forecast field, it's about the renewal, not today's numbers.

```json
[{"kind": "forecast", "account_id": "acc_024", "call": "downsize", "amount": 150000,
  "reason": "Budget cut, keeps the core team", "by": "csm_mia", "role": "owner",
  "data_at_risk": 234400, "as_of": "2026-10-18", "decided_at": "2026-10-30T10:00:00Z"}]
```

## What the engine writes

Never into the inputs above: flags (`data/flags_*.csv`), the action log
(`data/action_log.csv`, which also drives QBR slips), new engagement events
(appended, never edited), QBR packs and briefs, and the views page.

## CRM write-back (designed, not built)

The demo has no CRM, so nothing is sent back. With a real one it's a small,
safe step, because every engagement event already carries what a CRM activity
needs:

| Engagement event | CRM record | Fields |
| --- | --- | --- |
| `meeting_held` | Activity (meeting or call) on the account | date, owner, subject = topic, description = outcome + note |
| `email_sent` | Activity (email) | date, owner (or "automated"), subject = topic |
| `task_claimed` | Task owner update | who took it, when |
| `task_assigned` | Task owner update | the new owner, the manager who decided, when |
| `forecast_set` | Renewal opportunity forecast | category (renew, downsize, churn), amount, reason, who |

Each event's `event_id` goes into an external-ID field, so a re-run never
creates duplicates. The direction stays one-way for CS facts: the engine is
the record for engagement, and the CRM gets a copy so sales and leadership see
it where they already work.

## Running on your own data

1. Copy `settings.local.example.toml` to `settings.local.toml` (git-ignored, so
   a pull never overwrites it). Anything in it replaces the same key in
   `settings.toml`.
2. Under `[data]`, point `inputs` at the folder with your exports and name
   your files (`crm_file`, `team_file`, `events_glob`, and the optional
   `engagement_glob`, `holdout_file`, `outcomes_file`).
3. Under `[columns.crm]` and `[columns.team]`, map your column names to the
   engine's (`"Account ID" = "account_id"`). Only the ones that differ.
4. Map your tier names under `[tiers]` and your regions under `[regions]`.
5. Run `python check_inputs.py`: it reads the same settings and tells you
   what's missing and what the engine can and can't do with your data.

Your export folder is only ever read. Everything the engine produces (flags,
the action log, new engagement events, the views) goes to `data/`. Without CS
history the flags still run; journeys and time to action stay empty. Without
an answer key, `check_flags.py` is skipped: the backtest against your closed
renewals is the real test.

## Fitting it to your company (settings.toml)

Without touching the SQL:
- **Tier names** (`[tiers]`): map your CRM's tiers onto the three coverage
  models (tech touch, pooled, named CSM). Reports use the engine's names; your
  own stays in the `crm_tier` column.
- **What utilisation means** (`[utilisation]`): the daily usage event and the
  CRM column with the annual commitment. Volume-based or spend-based both fit.
- **Regions and time zones** (`[regions]`): for sending emails at a local hour.
- Every threshold and time window.

Still fixed: the events' JSON field names (event_name, timestamp, account_id,
user_id and the usage amount in `properties.event_count`: convert your
events to this shape once, e.g. in the warehouse export), and seat-based
utilisation (active seats / bought seats), which would need a different
formula. The cockpit shows the engine's tier names (your own stay in
`crm_tier`).
