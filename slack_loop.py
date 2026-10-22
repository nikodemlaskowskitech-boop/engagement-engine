"""
Shared pieces of the Slack loop, used by actions.py (posts task cards) and
listener.py (handles the clicks).

The loop:  flag -> task card in Slack -> someone clicks -> an engagement event
is appended (source "slack") -> the next pipeline run sees it, like any other
CS touch. Nothing is ever edited in place: every click is a new event.

Secrets come from .env (git-ignored), never printed:
  SLACK_BOT_TOKEN   xoxb-...  the bot: post and update its own messages, DM
  SLACK_APP_TOKEN   xapp-...  Socket Mode: the listener's connection to Slack
  SLACK_DEMO_USER   U...      the person who plays "Anna" in the demo
"""

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from settings import SETTINGS

REPO = Path(__file__).resolve().parent
DATA = REPO / "data"
SLACK = SETTINGS["slack"]

# Which kind of call a "Log call" click records, by flag
CALL_TOPIC = {"churn": "churn_call", "expansion": "growth_call", "activation": "activation_call",
              "journey": "follow_up", "qbr": "check_in"}

OUTCOMES = {"reached_at_risk": "Reached: at risk", "reached_fine": "Reached: all fine",
            "no_answer": "No answer"}


def load_env(*names):
    """Read the named settings from .env into the environment (never printed)."""
    env = REPO / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8-sig").splitlines():
            name, _, value = line.partition("=")
            if name.strip() in names and value.strip():
                os.environ[name.strip()] = value.strip().strip('"').strip("'")
    return [os.environ.get(n) for n in names]


def interactive(owner_id):
    """Does this owner get task cards with buttons (instead of the digest)?"""
    return owner_id in SLACK["interactive_owners"]


def card_channel(owner_id, demo_user):
    """DM owners' cards go to the demo user's DMs; everyone else's to the team channel."""
    return demo_user if owner_id in SLACK["dm_owners"] else SLACK["team_channel"]


def flag_label(flag, status):
    """'churn *Act*', but just '*QBR coming up*' when the status already names the flag."""
    return f"*{status}*" if flag.lower() in status.lower() else f"{flag} *{status}*"


def card_blocks(task, owner_name, last_touch=None):
    """One task as a Slack card (Block Kit): what, why, what to do, and three buttons."""
    name = task["account_name"] or "(not in the CRM)"
    ref = json.dumps({"a": task["account_id"], "f": task["flag"], "s": task["status"],
                      "o": task["routed_to"], "n": name})     # carried by every button
    touch = (f"Last touch: {last_touch[0]}, {last_touch[1]}" if last_touch else "No CS touch on record")
    link = f"{SLACK['cockpit_url']}#{task['account_id']}"
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text":
            f"*{name}* ({task['account_id']}) · {flag_label(task['flag'], task['status'])}\n"
            f"{task['reasons']}\n→ {task['action']}"}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": f"For *{owner_name}* · {touch}"}]},
        {"type": "actions", "block_id": "task_actions", "elements": [
            {"type": "button", "action_id": "claim", "text": {"type": "plain_text", "text": "I'll take it"},
             "value": ref},
            {"type": "button", "action_id": "log_call", "style": "primary",
             "text": {"type": "plain_text", "text": "Log call"}, "value": ref},
            {"type": "button", "action_id": "open_timeline", "url": link,
             "text": {"type": "plain_text", "text": "Open timeline"}},
        ]},
    ]


def run_date():
    """The pipeline's "today": the day after the last day of data (never the clock's date)."""
    from cairnpath_db import connect
    return connect().sql("SELECT as_of_date + 1 FROM as_of").fetchone()[0]


def append_event(day, account_id, event_name, owner_id, topic, slack_user, outcome=None, note=None):
    """Append one click as an engagement event in data/engagement/<run date>.jsonl.
    Dated on the pipeline's run date, at the real time of day of the click."""
    now = datetime.now(timezone.utc)
    event = {
        "event_id": str(uuid.uuid4()),
        "event_name": event_name,
        "event_type": "engagement",
        "timestamp": f"{day}T{now:%H:%M:%S}Z",
        "account_id": account_id,
        "user_id": None,
        "properties": {"owner_id": owner_id, "topic": topic, "outcome": outcome,
                       "note": note, "slack_user": slack_user},
        "context": {"source": "slack"},
    }
    path = DATA / "engagement" / f"{day}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")
    return event
