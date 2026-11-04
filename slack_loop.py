"""
Shared pieces of the Slack loop, used by actions.py (posts task cards) and
listener.py (handles the clicks).

The loop:  flag -> task card in Slack -> someone clicks -> an engagement event
is appended (source "slack") -> the next pipeline run sees it, like any other
CS touch. Nothing is ever edited in place: every click is a new event.

Two listeners share the pieces below (what a click records, how the card and
the form look), so they behave the same: listener.py on the laptop (Socket
Mode) and the Slack Lambda in AWS (infra/lambdas/slack/handler.py, behind
API Gateway). The Slack app talks to one of them at a time.

Secrets come from .env (git-ignored), never printed:
  SLACK_BOT_TOKEN   xoxb-...  the bot: post and update its own messages, DM
  SLACK_APP_TOKEN   xapp-...  Socket Mode: the laptop listener's connection to Slack
  SLACK_SIGNING_SECRET      the AWS listener: proves a request really came from Slack
                            (put_key.py copies it and the bot token to Parameter Store)
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
              "journey": "follow_up", "qbr": "qbr"}      # a QBR card logs the QBR itself

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


def dm_target(owner_id, demo_user):
    """Where a DM for this owner goes: their own Slack user if settings.toml
    [slack.users] knows them, otherwise the demo user (who plays everyone)."""
    return SLACK.get("users", {}).get(owner_id) or demo_user


def card_channel(owner_id, demo_user):
    """Owners with a Slack user, and the demo's DM owners, get their cards as DMs;
    everyone else's go to the team channel."""
    if owner_id in SLACK.get("users", {}) or owner_id in SLACK["dm_owners"]:
        return dm_target(owner_id, demo_user)
    return SLACK["team_channel"]


def flag_label(flag, status):
    """'churn *Act*', but just '*QBR coming up*' when the status already names the flag."""
    return f"*{status}*" if flag.lower() in status.lower() else f"{flag} *{status}*"


def card_blocks(task, owner_name, last_touch=None, day=None):
    """One task as a Slack card (Block Kit): what, why, what to do, and three buttons.
    day = the pipeline's run date, carried by the buttons so a click is dated on it
    wherever it's handled (the AWS listener has no data to work it out from)."""
    name = task["account_name"] or "(not in the CRM)"
    ref = {"a": task["account_id"], "f": task["flag"], "s": task["status"], "o": task["routed_to"], "n": name}
    if day:
        ref["d"] = str(day)
    ref = json.dumps(ref)                                     # carried by every button
    touch = (f"Last touch: {last_touch[0]}, {last_touch[1]}" if last_touch else "No CS touch on record")
    link = f"{SLACK['cockpit_url']}#{task['account_id']}"
    if task["status"] == "Follow-up not picked up":
        # a manager's approval, not a task to work: approve, or look first
        buttons = [{"type": "button", "action_id": "approve_handback", "style": "primary",
                    "text": {"type": "plain_text", "text": "Approve hand-back"}, "value": ref}]
    else:
        buttons = [
            {"type": "button", "action_id": "claim", "text": {"type": "plain_text", "text": "I'll take it"},
             "value": ref},
            {"type": "button", "action_id": "log_call", "style": "primary",
             "text": {"type": "plain_text", "text": "Log QBR" if task["flag"] == "qbr" else "Log call"},
             "value": ref}]
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text":
            f"*{name}* ({task['account_id']}) · {flag_label(task['flag'], task['status'])}\n"
            f"{task['reasons']}\n→ {task['action']}"}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": f"For *{owner_name}* · {touch}"}]},
        {"type": "actions", "block_id": "task_actions", "elements": buttons + [
            {"type": "button", "action_id": "open_timeline", "url": link,
             "text": {"type": "plain_text", "text": "Open timeline"}},
        ]},
    ]


def run_date():
    """The pipeline's "today": the day after the last day of data (never the clock's date)."""
    from cairnpath_db import connect
    return connect().sql("SELECT as_of_date + 1 FROM as_of").fetchone()[0]


def make_event(day, account_id, event_name, owner_id, topic, slack_user, outcome=None, note=None):
    """One click as an engagement event, dated on the pipeline's run date at the
    real time of day of the click."""
    now = datetime.now(timezone.utc)
    return {
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


def append_event(day, account_id, event_name, owner_id, topic, slack_user, outcome=None, note=None):
    """Append one click as an engagement event in data/engagement/<run date>.jsonl (the laptop)."""
    event = make_event(day, account_id, event_name, owner_id, topic, slack_user, outcome, note)
    path = DATA / "engagement" / f"{day}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")
    return event


# --- what each click does to the card (both listeners) ---

def claimed_blocks(blocks, user):
    """The card after "I'll take it": who took it; Log call stays (claiming isn't acting)."""
    blocks = [dict(b) for b in blocks]
    for b in blocks:
        if b.get("block_id") == "task_actions":
            b["elements"] = [e for e in b["elements"] if e["action_id"] != "claim"]
            blocks.insert(blocks.index(b), {"type": "context", "elements": [
                {"type": "mrkdwn", "text": f":raising_hand: Taken by <@{user}>"}]})
            break
    return blocks


def call_form(ref, channel, ts):
    """The "Log call" form. It carries the card's place, to update the card afterwards."""
    meta = json.dumps({**ref, "channel": channel, "ts": ts})
    return {
        "type": "modal", "callback_id": "log_call_form", "private_metadata": meta,
        "title": {"type": "plain_text", "text": "Log the QBR" if ref["f"] == "qbr" else "Log a call"},
        "submit": {"type": "plain_text", "text": "Log it"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn",
                                         "text": f"*{ref['n']}* ({ref['a']}) · {flag_label(ref['f'], ref['s'])}"}},
            {"type": "input", "block_id": "outcome", "label": {"type": "plain_text", "text": "Outcome"},
             "element": {"type": "static_select", "action_id": "value", "options": [
                 {"text": {"type": "plain_text", "text": label}, "value": key}
                 for key, label in OUTCOMES.items()]}},
            {"type": "input", "block_id": "note", "optional": True,
             "label": {"type": "plain_text", "text": "Note"},
             "element": {"type": "plain_text_input", "action_id": "value", "multiline": True}},
        ]}


def form_answers(view):
    """(outcome, note) from a submitted call form."""
    values = view["state"]["values"]
    return values["outcome"]["value"]["selected_option"]["value"], values["note"]["value"].get("value") or None


def call_logged_blocks(ref, user, outcome, note):
    """The card after a call is logged: the buttons give way to what happened (the bot
    can't read messages, by design, so the card is rebuilt from what we know)."""
    what = "QBR" if ref["f"] == "qbr" else "Call"
    text = f":telephone_receiver: {what} logged by <@{user}>: *{OUTCOMES[outcome]}*" + (f" · _{note}_" if note else "")
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{ref['n']}* ({ref['a']}) · {flag_label(ref['f'], ref['s'])}"}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": text}]},
        {"type": "actions", "elements": [{"type": "button", "action_id": "open_timeline",
                                          "text": {"type": "plain_text", "text": "Open timeline"},
                                          "url": f"{SLACK['cockpit_url']}#{ref['a']}"}]},
    ]


def handback_blocks(blocks, user):
    """The manager's card after "Approve hand-back": the button gives way to who approved."""
    blocks = [b for b in blocks if b.get("block_id") != "task_actions"]
    blocks.append({"type": "context", "elements": [
        {"type": "mrkdwn", "text": f":leftwards_arrow_with_hook: Hand-back approved by <@{user}>: back to the pool"}]})
    return blocks
