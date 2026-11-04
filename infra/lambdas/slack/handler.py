"""
Lambda: the Slack listener in AWS. Slack posts every button click and form to
one public address (API Gateway); this function answers it.

The address is public, so the first thing it does is check that the request
really came from Slack. Slack signs every request with the app's signing
secret: an HMAC-SHA256 of "v0:<timestamp>:<body>". We compute the same with our
copy of the secret and compare. A wrong or missing signature, or a timestamp
more than [aws] slack_max_age_seconds old (someone replaying a request they
caught), gets 401 and nothing else happens: no S3 write, no Slack call.

Then it does what listener.py does on the laptop, with the same pieces from
slack_loop.py (same events, same card updates, same form):
  I'll take it       -> task_claimed event; the card shows who took it
  Log call           -> opens the call form
  (form submitted)   -> meeting_held event with the flag's call topic; card updated
  Approve hand-back  -> handback_approved event; card updated
  Open timeline      -> a link: nothing to record
Each event is written as one file, state/clicks/<run date>/<event_id>.json:
one file per click, so two clicks at once never overwrite each other. The
engine reads them on its next run. The run date comes from the card itself
(actions.py puts it on every button).

Secrets (signing secret, bot token) come from Parameter Store at the first
request and stay in memory while the function is warm. Never printed. Its role
may write state/clicks/* and read those two parameters, nothing else.
"""

import base64
import hashlib
import hmac
import json
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from slack_loop import (CALL_TOPIC, OUTCOMES, call_form, call_logged_blocks, claimed_blocks, form_answers,
                        handback_blocks, make_event)

BUCKET = os.environ.get("BUCKET", "")
OWNER = os.environ.get("ACCOUNT_ID", "")
CLICKS = os.environ.get("CLICKS_PREFIX", "state/clicks/")
SIGNING_PARAMETER = os.environ.get("SIGNING_SECRET_PARAMETER", "")
TOKEN_PARAMETER = os.environ.get("BOT_TOKEN_PARAMETER", "")
MAX_AGE = int(os.environ.get("MAX_AGE_SECONDS", "300"))
SLACK_API = "https://slack.com/api/"
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_secrets = {}          # kept while the Lambda is warm: one Parameter Store read per cold start


def _s3():
    import boto3
    return boto3.client("s3")


def _ssm():
    import boto3
    return boto3.client("ssm")


def secret(ssm, name):
    if name not in _secrets:
        _secrets[name] = ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]
    return _secrets[name]


def verified(headers, body, signing_secret, now=None):
    """(True, "") when Slack signed this exact body recently; otherwise (False, why)."""
    headers = {k.lower(): v for k, v in (headers or {}).items()}
    ts, sig = headers.get("x-slack-request-timestamp", ""), headers.get("x-slack-signature", "")
    if not (ts.isdigit() and sig):
        return False, "no signature"
    if abs((now or time.time()) - int(ts)) > MAX_AGE:
        return False, "too old (replayed?)"
    expected = "v0=" + hmac.new(signing_secret.encode(), f"v0:{ts}:{body}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):        # constant time: no timing hints
        return False, "bad signature"
    return True, ""


def slack(method, token, payload, opener=None):
    """Call the Slack Web API. Returns Slack's answer (the token is never printed)."""
    req = urllib.request.Request(SLACK_API + method, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {token}",
                                          "Content-Type": "application/json; charset=utf-8"})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=2.5) as resp:
            answer = json.loads(resp.read())
    except Exception as error:                  # Slack slow or down: the click is saved anyway
        print(f"  Slack {method} failed: {type(error).__name__}")
        return {}
    if not answer.get("ok"):
        print(f"  Slack {method} failed: {answer.get('error')}")
    return answer


def save(s3, ref, event_name, topic, user, outcome=None, note=None):
    """One click, one file under state/clicks/<run date>/."""
    day = ref.get("d") if DAY.match(str(ref.get("d", ""))) else datetime.now(timezone.utc).date().isoformat()
    event = make_event(day, ref["a"], event_name, ref["o"], topic, user, outcome, note)
    s3.put_object(Bucket=BUCKET, Key=f"{CLICKS}{day}/{event['event_id']}.json", Body=json.dumps(event).encode(),
                  ContentType="application/json", ExpectedBucketOwner=OWNER)
    return event


def answer(code, body=""):
    return {"statusCode": code, "headers": {"Content-Type": "text/plain"}, "body": body}


def handler(event, context, s3=None, ssm=None, opener=None, now=None):
    ssm = ssm or _ssm()
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")
    ok, why = verified(event.get("headers"), body, secret(ssm, SIGNING_PARAMETER), now)
    if not ok:
        print(f"  refused: {why}")                   # never the body: it isn't trusted
        return answer(401, "unauthorized")

    s3 = s3 or _s3()
    payload = json.loads(urllib.parse.parse_qs(body).get("payload", ["{}"])[0])
    user = payload.get("user", {}).get("id")
    token = secret(ssm, TOKEN_PARAMETER)

    if payload.get("type") == "block_actions":
        act = (payload.get("actions") or [{}])[0]
        kind = act.get("action_id")
        if kind == "open_timeline":                 # a link button: Slack opens the page itself
            return answer(200)
        ref = json.loads(act.get("value") or "{}")
        channel, ts = payload.get("channel", {}).get("id"), payload.get("message", {}).get("ts")
        blocks = payload.get("message", {}).get("blocks", [])
        if kind == "claim":
            save(s3, ref, "task_claimed", ref["f"], user)
            slack("chat.update", token, {"channel": channel, "ts": ts, "text": f"{ref['n']}: taken",
                                         "blocks": claimed_blocks(blocks, user)}, opener)
            print(f"  claimed     {ref['a']}  {ref['n']}")
        elif kind == "log_call":                    # the form must open within 3 s of the click
            slack("views.open", token, {"trigger_id": payload.get("trigger_id"),
                                        "view": call_form(ref, channel, ts)}, opener)
        elif kind == "approve_handback":
            save(s3, ref, "handback_approved", "handback", user)
            slack("chat.update", token, {"channel": channel, "ts": ts, "text": f"{ref['n']}: hand-back approved",
                                         "blocks": handback_blocks(blocks, user)}, opener)
            print(f"  hand-back   {ref['a']}  {ref['n']}  (approved)")
        return answer(200)

    if payload.get("type") == "view_submission" and payload.get("view", {}).get("callback_id") == "log_call_form":
        view = payload["view"]
        ref = json.loads(view.get("private_metadata") or "{}")
        outcome, note = form_answers(view)
        if outcome not in OUTCOMES:
            return answer(200)
        topic = CALL_TOPIC.get(ref["f"], "follow_up")
        save(s3, ref, "meeting_held", topic, user, outcome, note)
        slack("chat.update", token, {"channel": ref["channel"], "ts": ref["ts"], "text": f"{ref['n']}: call logged",
                                     "blocks": call_logged_blocks(ref, user, outcome, note)}, opener)
        print(f"  call logged {ref['a']}  {ref['n']}  ({topic}, {OUTCOMES[outcome]})")
        return answer(200)                         # an empty 200 closes the form

    return answer(200)                             # anything else: nothing to do
