"""
The Slack listener: handles clicks on the task cards actions.py posts.

It runs in Socket Mode: this script opens a connection out to Slack and
Slack sends the clicks down it. Nothing on this computer is exposed to the
internet, and no public address is needed. (On AWS this becomes an API
Gateway endpoint plus a Lambda, which then has to check Slack's request
signature itself: infra/lambdas/slack/handler.py. Both use the same pieces
from slack_loop.py; the Slack app talks to one of them at a time.)

What each button does:
  I'll take it    -> a task_claimed event; the card shows who took it
  Log call        -> a small form (outcome + note) -> a meeting_held event with
                     the right topic (churn_call, growth_call, ...; "Log QBR"
                     on a QBR card records the QBR itself); the card
                     shows the call. The next pipeline run sees the call: the
                     escalation stops, the team report counts it, the
                     timeline shows it.
  Approve hand-back -> (manager cards for "Follow-up not picked up") a
                     handback_approved event; the next actions.py run sends
                     the follow-up to the whole pool
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

from slack_loop import (CALL_TOPIC, OUTCOMES, append_event, call_form, call_logged_blocks, claimed_blocks,
                        form_answers, handback_blocks, load_env, run_date)

sys.stdout.reconfigure(encoding="utf-8")

bot_token, app_token = load_env("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN")
if not (bot_token and app_token):
    sys.exit("SLACK_BOT_TOKEN and SLACK_APP_TOKEN are needed in .env (see slack_loop.py)")

# Socket Mode clicks arrive over our own outgoing connection, so there are no
# incoming web requests to check. Slack's request signatures (the "signing
# secret") only matter for a public endpoint: the AWS listener checks them.
app = App(token=bot_token, request_verification_enabled=False)
DAY = run_date()     # every click is dated on the pipeline's run date


@app.action("claim")
def claim(ack, body, client):
    ack()                                           # Slack wants an answer within 3 seconds
    ref = json.loads(body["actions"][0]["value"])
    user = body["user"]["id"]
    append_event(DAY, ref["a"], "task_claimed", ref["o"], ref["f"], user)
    client.chat_update(channel=body["channel"]["id"], ts=body["message"]["ts"],
                       blocks=claimed_blocks(body["message"]["blocks"], user), text=f"{ref['n']}: taken")
    print(f"  claimed     {ref['a']}  {ref['n']}")


@app.action("log_call")
def log_call(ack, body, client):
    ack()
    ref = json.loads(body["actions"][0]["value"])
    client.views_open(trigger_id=body["trigger_id"],
                      view=call_form(ref, body["channel"]["id"], body["message"]["ts"]))


@app.view("log_call_form")
def log_call_submitted(ack, body, view, client):
    ack()
    ref = json.loads(view["private_metadata"])
    outcome, note = form_answers(view)
    user = body["user"]["id"]
    topic = CALL_TOPIC.get(ref["f"], "follow_up")
    append_event(DAY, ref["a"], "meeting_held", ref["o"], topic, user, outcome, note)
    client.chat_update(channel=ref["channel"], ts=ref["ts"], text=f"{ref['n']}: call logged",
                       blocks=call_logged_blocks(ref, user, outcome, note))
    print(f"  call logged {ref['a']}  {ref['n']}  ({topic}, {OUTCOMES[outcome]})")


@app.action("approve_handback")
def approve_handback(ack, body, client):
    ack()
    ref = json.loads(body["actions"][0]["value"])
    user = body["user"]["id"]
    # the next actions.py run sends the follow-up to the whole pool
    append_event(DAY, ref["a"], "handback_approved", ref["o"], "handback", user)
    client.chat_update(channel=body["channel"]["id"], ts=body["message"]["ts"],
                       blocks=handback_blocks(body["message"]["blocks"], user), text=f"{ref['n']}: hand-back approved")
    print(f"  hand-back   {ref['a']}  {ref['n']}  (approved)")


@app.action("open_timeline")
def open_timeline(ack, body):
    ack()                                           # a link button: Slack opens the page itself
    # Looking isn't acting, so nothing is recorded; just show it in the window
    url = body["actions"][0].get("url", "")
    print(f"  opened      {url.rsplit('#', 1)[-1]}  (timeline, nothing recorded)")


if __name__ == "__main__":
    print(f"Listening for clicks (run date {DAY}). Ctrl+C to stop.")
    SocketModeHandler(app, app_token).start()
