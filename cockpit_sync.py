"""
Managers' decisions from the cockpit, turned into engagement events.

In the published cockpit a manager can give a task to someone else ("Assign
to") or approve a hand-back. The page saves each decision in its own small
database. This script reads those decisions from data/cockpit_decisions.json
(today Claude copies them there from the page; with a real backend the
pipeline would read the store itself) and writes them as engagement events,
the same way Slack clicks are written:

  task_assigned       properties.owner_id = the new owner, topic = the flag,
                      status = the task's status, manager = who decided
  handback_approved   the same event Slack's "Approve hand-back" writes
  forecast_set        a CSM's or manager's renewal forecast for an account:
                      status = renew | downsize | churn, amount = the value
                      they expect at renewal, note = their reason,
                      owner_id = who, outcome = their role (owner | manager)
  next_step_set       the next step an owner or manager planned ("action
                      planned"): status = call | qbr | email | internal,
                      due = its date, note, owner_id = who, outcome = role

They go to data/engagement/cockpit.jsonl, rewritten on every run from the
whole decisions file, so running twice never doubles anything. Decisions made
on older data (another as-of date) are skipped: after new data they mean
nothing. Forecasts are kept whatever the data: they're about the renewal, like
a CRM's forecast field, and every one is kept so the history shows. Then actions.py sends a reassigned task to its new owner, once.

Dated on the run date, like Slack clicks. No decisions file: nothing to do.

    python cockpit_sync.py
"""

import json
import sys
from pathlib import Path

from cairnpath_db import connect

sys.stdout.reconfigure(encoding="utf-8")

DATA = Path(__file__).resolve().parent / "data"
SOURCE = DATA / "cockpit_decisions.json"
TARGET = DATA / "engagement" / "cockpit.jsonl"

if not SOURCE.exists():
    print("No data/cockpit_decisions.json: no cockpit decisions to apply")
    sys.exit(0)

con = connect()
as_of, run_date = (str(d) for d in con.sql("SELECT as_of_date, as_of_date + 1 FROM as_of").fetchone())
decisions = json.loads(SOURCE.read_text(encoding="utf-8"))

# The latest decision per task wins (a manager can change their mind)
latest = {}
stale = 0
forecasts = []
for d in sorted(decisions, key=lambda d: d.get("decided_at", "")):
    if d.get("kind") in ("forecast", "next_step"):
        forecasts.append(d)
        continue
    if d.get("as_of") != as_of:
        stale += 1
        continue
    if d.get("kind") not in ("assign", "handback"):
        continue
    latest[(d["kind"], d["account_id"], d["flag"], d["status"])] = d

events = []
for (kind, account_id, flag, status), d in sorted(latest.items()):
    clock = d.get("decided_at", "")[11:19] or "12:00:00"      # the real time of day of the click
    events.append({
        # the same decision always gets the same id, so a re-run writes the same file
        "event_id": f"cockpit-{kind}-{account_id}-{flag}-{status}".replace(" ", "_"),
        "event_name": "task_assigned" if kind == "assign" else "handback_approved",
        "event_type": "engagement",
        "timestamp": f"{run_date}T{clock}Z",
        "account_id": account_id,
        "user_id": None,
        "properties": {"owner_id": d["to_owner"] if kind == "assign" else d["manager"],
                       "topic": flag if kind == "assign" else "handback",
                       "outcome": None, "note": None, "slack_user": None,
                       "status": status, "manager": d["manager"]},
        "context": {"source": "cockpit"},
    })

for d in forecasts:                     # forecasts and next steps: every one, for the history
    clock = d.get("decided_at", "")[11:19] or "12:00:00"
    plan = d["kind"] == "next_step"
    events.append({
        "event_id": f"cockpit-{d['kind']}-{d['account_id']}-{d.get('decided_at', '')}",
        "event_name": "next_step_set" if plan else "forecast_set",
        "event_type": "engagement",
        "timestamp": f"{run_date}T{clock}Z",
        "account_id": d["account_id"],
        "user_id": None,
        "properties": {"owner_id": d["by"], "topic": "next_step" if plan else "renewal_forecast",
                       "outcome": d["role"], "note": d.get("note") if plan else d.get("reason"),
                       "slack_user": None, "status": d["type"] if plan else d["call"],
                       "manager": None, "amount": None if plan else d.get("amount"),
                       "due": d.get("due") if plan else None},
        "context": {"source": "cockpit"},
    })

TARGET.parent.mkdir(parents=True, exist_ok=True)
if events:
    TARGET.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
elif TARGET.exists():
    TARGET.unlink()             # nothing applies any more (older data): no file at all
assigned = sum(e["event_name"] == "task_assigned" for e in events)
handbacks = sum(e["event_name"] == "handback_approved" for e in events)
print(f"Cockpit decisions for the data as of {as_of}: {assigned} reassigned, "
      f"{handbacks} hand-backs approved, "
      f"{sum(d['kind'] == 'forecast' for d in forecasts)} renewal forecasts, "
      f"{sum(d['kind'] == 'next_step' for d in forecasts)} next steps"
      + (f" ({stale} from older data skipped)" if stale else ""))
for e in events:
    p = e["properties"]
    if e["event_name"] == "next_step_set":
        print(f"  {e['account_id']}  next step  {p['status']} by {p['due']}  (by {p['owner_id']}, {p['outcome']})")
        continue
    if e["event_name"] == "forecast_set":
        amount = f" {p['amount']:,.0f}" if p["amount"] is not None else ""
        print(f"  {e['account_id']}  forecast   {p['status']}{amount}  (by {p['owner_id']}, {p['outcome']})")
        continue
    what = f"to {p['owner_id']}" if e["event_name"] == "task_assigned" else "hand-back approved"
    print(f"  {e['account_id']}  {p['topic']:<10} {p['status']:<28} {what}  (by {p['manager']})")
if events:
    print(f"Saved {TARGET.relative_to(DATA.parent)}")
