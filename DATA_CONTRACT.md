# Data contract

What the engine needs to run on someone else's book of business: five inputs,
their columns, what each column means, and which flags stop working without it.
Today `generate_events.py` writes all five. A real team would export them from
their product analytics, CRM and CS tools instead, in this shape.

The rule of thumb: **the engine reads the CRM, it doesn't replace it.** Facts the
pipeline creates (actions, "covered by the QBR") go in its own action log, never
back into these inputs.

## At a glance

| Input | File | Required? | Without it |
| --- | --- | --- | --- |
| Product events | `data/events/*.jsonl` | Yes | Nothing runs |
| CRM accounts | `data/crm_accounts.csv` | Yes | Nothing runs (accounts with events but no row show as Handoff gap) |
| CS team | `data/cs_team.csv` | Yes, if any tier has people | Tasks can't name an owner or a manager; no escalations, no team report |
| Engagement history | `data/engagement/*.jsonl` | No | No journeys, no time to action, no "last touch"; flags still work |
| Onboarding holdout | `data/onboarding_holdout.csv` | No | Everyone gets onboarding emails; no welcomed vs. control report |

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

Example:
```csv
account_id,account_name,tier,region,contract_start,renewal_date,contracted_events,add_ons,csm_owner,cs_pool,last_qbr_date,next_qbr_date
acc_003,Driftwood Banking,Enterprise,UK&I,2026-02-10,2027-02-10,300000000,experiments,csm_anna,,2026-07-01,2026-10-26
```

## 3. CS team

| Column | Meaning |
| --- | --- |
| owner_id | Matches csm_owner / cs_pool in the CRM |
| owner_name | Shown on tasks |
| owner_type | `csm` or `pool` |
| region | Their region |
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

Topics that matter: `getting_started` (journeys), `churn_call`, `growth_call`,
`activation_call` (time to action), `qbr` (QBR first and slips), `check_in`.
`context.source` says who wrote the row: `cs_history`, `actions.py` or `slack`.

## 5. Onboarding holdout (optional)

The control group of the onboarding programme: these accounts get no onboarding
emails, so the welcomed vs. control report can measure the effect.

```csv
account_id,held_out_since
acc_074,2026-08-09
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

Each event's `event_id` goes into an external-ID field, so a re-run never
creates duplicates. The direction stays one-way for CS facts: the engine is
the record for engagement, and the CRM gets a copy so sales and leadership see
it where they already work.

## Still hard-coded (next step: settings)

To run on another company's data today you'd also need to change code for:
- **Tier names and routing** (`Enterprise`/`Scale`/`Self-service`, who gets what).
- **The utilisation definition**: committed event volume. A seat-based product
  would divide active seats by bought seats; a usage-billed one, spend by commit.
- **Region list and time zones** (email hour, in `actions.py`).

These move into `settings.toml` next, so a new team edits one file, not the SQL.
