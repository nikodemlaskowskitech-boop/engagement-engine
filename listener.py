"""
The Slack listener: handles clicks on the task cards actions.py posts.

It runs in Socket Mode: this script opens a connection out to Slack and
Slack sends the clicks down it. Nothing on this computer is exposed to the
internet, and no public address is needed. (On AWS this becomes an API
Gateway endpoint plus a Lambda, which then has to check Slack's request
signature itself.)

What each button does:
  I'll take it    -> a task_claimed event; the card shows who took it
  Log call        -> a small form (outcome + note) -> a meeting_held event with
                     the right topic (churn_call, growth_call, ...); the card
                     shows the call. The next pipeline run sees the call: the
                     escalation stops, the team report counts it, the
                     timeline shows it.
  Open timeline   -> opens the account on the cockpit page (a link; nothing
                     to record)

Every click is appended to data/engagement/<run date>.jsonl with source
"slack". Nothing is edited in place.

Needs SLACK_BOT_TOKEN and SLACK_APP_TOKEN in .env. Run:  python listener.py
Leave it running while you click; stop it with Ctrl+C.
"""

import json
import sys

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from slack_loop import CALL_TOPIC, OUTCOMES, SLACK, append_event, load_env, run_date

sys.stdout.reconfigure(encoding="utf-8")

bot_token, app_token = load_env("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN")
if not (bot_token and app_token):
    sys.exit("SLACK_BOT_TOKEN and SLACK_APP_TOKEN are needed in .env (see slack_loop.py)")

# Socket Mode clicks arrive over our own outgoing connection, so there are no
# incoming web requests to check. Slack's request signatures (the "signing
# secret") only matter for a public endpoint, which comes with AWS in Phase 6.
app = App(token=bot_token, request_verification_enabled=False)
DAY = run_date()     # every click is dated on the pipeline's run date


@app.action("claim")
def claim(ack, body, client):
    ack()                                           # Slack wants an answer within 3 seconds
    ref = json.loads(body["actions"][0]["value"])
    user = body["user"]["id"]
    append_event(DAY, ref["a"], "task_claimed", ref["o"], ref["f"], user)
    # keep the Log call button: claiming isn't acting
    blocks = body["message"]["blocks"]
    for b in blocks:
        if b.get("block_id") == "task_actions":
            b["elements"] = [e for e in b["elements"] if e["action_id"] != "claim"]
            blocks.insert(blocks.index(b), {"type": "context", "elements": [
                {"type": "mrkdwn", "text": f":raising_hand: Taken by <@{user}>"}]})
            break
    client.chat_update(channel=body["channel"]["id"], ts=body["message"]["ts"],
                       blocks=blocks, text=f"{ref['n']}: taken")
    print(f"  claimed     {ref['a']}  {ref['n']}")


@app.action("log_call")
def log_call(ack, body, client):
    ack()
    ref = json.loads(body["actions"][0]["value"])
    # the form needs to know which card to update afterwards
    meta = json.dumps({**ref, "channel": body["channel"]["id"], "ts": body["message"]["ts"]})
    client.views_open(trigger_id=body["trigger_id"], view={
        "type": "modal", "callback_id": "log_call_form", "private_metadata": meta,
        "title": {"type": "plain_text", "text": "Log a call"},
        "submit": {"type": "plain_text", "text": "Log it"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn",
                                         "text": f"*{ref['n']}* ({ref['a']}) · {ref['f']} {ref['s']}"}},
            {"type": "input", "block_id": "outcome", "label": {"type": "plain_text", "text": "Outcome"},
             "element": {"type": "static_select", "action_id": "value", "options": [
                 {"text": {"type": "plain_text", "text": label}, "value": key}
                 for key, label in OUTCOMES.items()]}},
            {"type": "input", "block_id": "note", "optional": True,
             "label": {"type": "plain_text", "text": "Note"},
             "element": {"type": "plain_text_input", "action_id": "value", "multiline": True}},
        ]})


@app.view("log_call_form")
def log_call_submitted(ack, body, view, client):
    ack()
    ref = json.loads(view["private_metadata"])
    values = view["state"]["values"]
    outcome = values["outcome"]["value"]["selected_option"]["value"]
    note = values["note"]["value"].get("value") or None
    user = body["user"]["id"]
    topic = CALL_TOPIC.get(ref["f"], "follow_up")
    append_event(DAY, ref["a"], "meeting_held", ref["o"], topic, user, outcome, note)
    # update the original card: the buttons give way to what happened (the bot
    # can't read messages, by design, so the card is rebuilt from what we know)
    text = f":telephone_receiver: Call logged by <@{user}>: *{OUTCOMES[outcome]}*" + (f" · _{note}_" if note else "")
    client.chat_update(channel=ref["channel"], ts=ref["ts"], text=f"{ref['n']}: call logged", blocks=[
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{ref['n']}* ({ref['a']}) · {ref['f']} *{ref['s']}*"}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": text}]},
        {"type": "actions", "elements": [{"type": "button", "action_id": "open_timeline",
                                          "text": {"type": "plain_text", "text": "Open timeline"},
                                          "url": f"{SLACK['cockpit_url']}#{ref['a']}"}]},
    ])
    print(f"  call logged {ref['a']}  {ref['n']}  ({topic}, {OUTCOMES[outcome]})")


@app.action("open_timeline")
def open_timeline(ack):
    ack()                                           # a link button: Slack opens it, we just say "seen"


if __name__ == "__main__":
    print(f"Listening for clicks (run date {DAY}). Ctrl+C to stop.")
    SocketModeHandler(app, app_token).start()
