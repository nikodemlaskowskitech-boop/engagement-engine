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

Never trust the page (5 Nov 2026). The cockpit's forms check what people type,
but anyone with edit access can write to the page's database directly and skip
them. So every decision is checked again here, against the CRM and the CS team:
a known account and owner, an allowed value, a real date, an amount in range, a
role that matches the account (an owner on their own account, a manager on their
team's). A decision that fails is skipped and listed with the reason; it never
stops the run. Free text is cut to [cockpit] max_text characters, control
characters removed.

Dated on the run date, like Slack clicks. No decisions file: nothing to do.

    python cockpit_sync.py
"""

import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

from settings import SETTINGS

DATA = Path(__file__).resolve().parent / "data"
SOURCE = DATA / "cockpit_decisions.json"
TARGET = DATA / "engagement" / "cockpit.jsonl"
MAX_TEXT = SETTINGS.get("cockpit", {}).get("max_text", 200)

CALLS = {"renew", "downsize", "churn"}                  # a renewal forecast
STEP_TYPES = {"call", "qbr", "email", "internal"}       # a next step (the page's STEP_TYPES)
ROLES = {"owner", "manager"}
FLAGS = {"activation", "churn", "expansion", "qbr", "journey"}   # the task flags (cairnpath_db.py flag_names)
TEXT_FIELDS = ("kind", "account_id", "role", "by", "manager", "to_owner", "from_owner", "call", "type",
               "due", "decided_at", "flag", "status", "reason", "note", "as_of", "decided_by")
KINDS = {"assign", "handback", "forecast", "next_step", "handover_confirm"}   # handover_confirm: the page's own
ISO_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def clean(text):
    """Free text as the page should have sent it: a string, no control characters, cut short."""
    return CONTROL.sub("", str(text or "")).strip()[:MAX_TEXT]


def is_time(value):
    """A real moment, not just the right shape: "2026-13-45T99:99:99Z" is refused (audit F3)."""
    if not (isinstance(value, str) and ISO_TIME.match(value)):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def is_day(value):
    try:
        return isinstance(value, str) and len(value) == 10 and date.fromisoformat(value) is not None
    except ValueError:
        return False


def may(role, action):
    """What settings.toml [roles] lets a role do: "own", "team", or None (not at all).
    So changing [roles] changes what is accepted here too (ROLES.md), not only the page."""
    return SETTINGS.get("roles", {}).get(role, {}).get("edit", {}).get(action)


def problem(d, accounts, team, kinds=None):
    """Why this decision can't be used, or None when it can. accounts: {id: {"arr", "owners"}}
    (owners = the CSM, pool and pool member on the account); team: {owner_id: manager}."""
    if not isinstance(d, dict):
        return "not a decision"
    # every field the page sends as text must be text: a list or a dict here used to crash the
    # membership checks below and stop the whole run (audit F2)
    odd = [f for f in TEXT_FIELDS if d.get(f) is not None and not isinstance(d[f], str)]
    if odd:
        return f"{', '.join(odd)} isn't text"
    kind = d.get("kind")
    if kind not in KINDS:
        return f"unknown kind {str(kind)[:30]!r}"
    if kind == "handover_confirm":
        return None                                       # shown by the page itself; nothing to apply here
    acc = accounts.get(d.get("account_id"))
    if acc is None:
        return f"unknown account {str(d.get('account_id'))[:30]!r}"
    if not is_time(d.get("decided_at")):
        return "decided_at isn't a time"
    managers = set(team.values())
    if kind in ("assign", "handback"):
        if d.get("flag") not in FLAGS or not d.get("status"):
            return "not a known flag, or no status"
        if d.get("manager") not in managers:
            return f"{str(d.get('manager'))[:30]!r} isn't a manager"
        if not may("manager", kind):
            return f"managers may not {kind} ([roles.manager] edit)"
        if d["manager"] not in {team.get(o) for o in acc["owners"]}:      # audit F4: own team only
            return f"{d['manager']!r} doesn't manage this account's owner"
        if kind == "assign" and d.get("to_owner") not in team:
            return f"{str(d.get('to_owner'))[:30]!r} isn't in the CS team"
        return None
    # forecast and next step: who, and are they allowed on this account?
    if d.get("role") not in ROLES:
        return f"unknown role {str(d.get('role'))[:30]!r}"
    by = d.get("by")
    role = "manager" if d["role"] == "manager" else (kinds or {}).get(by, "csm")
    where = may(role, kind)
    if not where:
        return f"a {role} may not set a {kind} ([roles.{role}] edit)"
    if d["role"] == "manager" and by not in {team.get(o) for o in acc["owners"]}:
        return f"{str(by)[:30]!r} doesn't manage this account's owner"
    if d["role"] == "owner" and where == "own" and by not in acc["owners"]:
        return f"{str(by)[:30]!r} doesn't own this account"
    if d["role"] == "owner" and where == "team" and (by not in team or team[by] not in {team.get(o) for o in acc["owners"]}):
        return f"{str(by)[:30]!r} isn't in this account's team"
    if kind == "forecast":
        if d.get("call") not in CALLS:
            return f"unknown forecast {str(d.get('call'))[:30]!r}"
        amount = d.get("amount")
        if d["call"] == "downsize":
            if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not 0 < amount < acc["arr"]:
                return "a downsize amount must be a number between 0 and today's ARR"
        elif amount is not None:
            return f"a '{d['call']}' forecast takes no amount"
        if not clean(d.get("reason")):
            return "no reason"
        return None
    if d.get("type") not in STEP_TYPES:
        return f"unknown next step {str(d.get('type'))[:30]!r}"
    if not is_day(d.get("due")):
        return "due isn't a date"
    return None


def build(decisions, as_of, run_date, accounts, team, kinds=None):
    """The events to write, the decisions skipped (with why), and how many were on older data."""
    if not isinstance(decisions, list):
        return [], [("the file", "isn't a list of decisions")], 0
    good, skipped = [], []
    for d in decisions:
        try:
            why = problem(d, accounts, team, kinds)
        except Exception as e:                  # never let one entry stop the run (audit F2)
            why = f"unreadable ({type(e).__name__})"
        if why:
            label = f"{d.get('kind', '?')} {d.get('account_id', '?')}" if isinstance(d, dict) else "?"
            skipped.append((str(label)[:60], why))
        elif d["kind"] != "handover_confirm":
            good.append(d)

    # The latest decision per task wins (a manager can change their mind)
    latest, stale, forecasts = {}, 0, []
    for d in sorted(good, key=lambda d: d["decided_at"]):
        if d["kind"] in ("forecast", "next_step"):
            forecasts.append(d)
            continue
        if d.get("as_of") != as_of:
            stale += 1
            continue
        latest[(d["kind"], d["account_id"], d["flag"], d["status"])] = d

    events = []
    for (kind, account_id, flag, status), d in sorted(latest.items()):
        events.append({
            # the same decision always gets the same id, so a re-run writes the same file
            "event_id": f"cockpit-{kind}-{account_id}-{flag}-{status}".replace(" ", "_"),
            "event_name": "task_assigned" if kind == "assign" else "handback_approved",
            "event_type": "engagement",
            "timestamp": f"{run_date}T{d['decided_at'][11:19]}Z",     # the real time of day of the click
            "account_id": account_id,
            "user_id": None,
            "properties": {"owner_id": d["to_owner"] if kind == "assign" else d["manager"],
                           "topic": clean(flag) if kind == "assign" else "handback",
                           "outcome": None, "note": None, "slack_user": None,
                           "status": clean(status), "manager": d["manager"]},
            "context": {"source": "cockpit"},
        })
    for d in forecasts:                     # forecasts and next steps: every one, for the history
        plan = d["kind"] == "next_step"
        events.append({
            "event_id": f"cockpit-{d['kind']}-{d['account_id']}-{d['decided_at']}",
            "event_name": "next_step_set" if plan else "forecast_set",
            "event_type": "engagement",
            "timestamp": f"{run_date}T{d['decided_at'][11:19]}Z",
            "account_id": d["account_id"],
            "user_id": None,
            "properties": {"owner_id": d["by"], "topic": "next_step" if plan else "renewal_forecast",
                           "outcome": d["role"], "note": clean(d.get("note") if plan else d.get("reason")),
                           "slack_user": None, "status": d["type"] if plan else d["call"],
                           "manager": None, "amount": None if plan else d.get("amount"),
                           "due": d["due"] if plan else None},
            "context": {"source": "cockpit"},
        })
    return events, skipped, stale


def reference(con):
    """What a decision is checked against: each account's ARR and owners, each person's manager and kind."""
    accounts = {}
    for acc, arr, csm, pool, member in con.sql(
            "SELECT account_id, arr, csm_owner, cs_pool, pool_owner FROM crm").fetchall():   # arr: the crm view's
        accounts[acc] = {"arr": float(arr or 0), "owners": {o for o in (csm, pool, member) if o}}
    team = dict(con.sql("SELECT owner_id, manager FROM cs_team").fetchall())
    kinds = dict(con.sql("SELECT owner_id, owner_type FROM cs_team").fetchall())   # csm | member | pool: their [roles]
    return accounts, team, kinds


def main():
    if not SOURCE.exists():
        print("No data/cockpit_decisions.json: no cockpit decisions to apply")
        return 0
    from cairnpath_db import connect                  # DuckDB, only when there's something to apply
    con = connect()
    as_of, run_date = (str(d) for d in con.sql("SELECT as_of_date, as_of_date + 1 FROM as_of").fetchone())
    try:
        decisions = json.loads(SOURCE.read_text(encoding="utf-8"))
    except ValueError as e:                 # a broken file: say so, and run on without decisions
        decisions = None
        print(f"data/cockpit_decisions.json isn't valid JSON ({e}): no decisions applied this run")
    events, skipped, stale = build(decisions if decisions is not None else [], as_of, run_date, *reference(con))

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    if events:
        TARGET.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    elif TARGET.exists():
        TARGET.unlink()             # nothing applies any more (older data): no file at all
    assigned = sum(e["event_name"] == "task_assigned" for e in events)
    handbacks = sum(e["event_name"] == "handback_approved" for e in events)
    print(f"Cockpit decisions for the data as of {as_of}: {assigned} reassigned, "
          f"{handbacks} hand-backs approved, "
          f"{sum(e['event_name'] == 'forecast_set' for e in events)} renewal forecasts, "
          f"{sum(e['event_name'] == 'next_step_set' for e in events)} next steps"
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
    if skipped:                 # refused, not fatal: the rest of the run goes on
        print(f"SKIPPED {len(skipped)} decision(s) that failed the checks (written to the page's database "
              f"without going through its form?):")
        for label, why in skipped:
            print(f"  {label}: {why}")
    if events:
        print(f"Saved {TARGET.relative_to(DATA.parent)}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
