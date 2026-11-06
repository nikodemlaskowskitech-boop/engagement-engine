"""
Slack listener Lambda test: does the AWS listener refuse what it should, and
record clicks like listener.py does? No AWS, no Slack, no cost.

    python tests/slack_lambda_test.py

Builds the Lambda's folder as the deploy does (infra/bundle.py), then sends its
handler requests signed like Slack signs them, with a made-up signing secret,
against a stand-in S3, Parameter Store and Slack API.
  - refused (401, nothing written, Slack not called): no signature, a wrong
    one, a body changed after signing, a request older than 5 minutes
  - each button: the right event in state/clicks/<run date>/, the card updated
    or the form opened; Open timeline records nothing
  - a run date that isn't a date can't move the file out of state/clicks/
  - Slack down: the click is still saved; secrets read once, never printed
Exit 1 on any FAIL.
"""

import base64
import contextlib
import hashlib
import hmac
import io
import json
import os
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "infra")]

from bundle import SLACK_FILES, bundle_slack     # noqa: E402
from settings import SETTINGS                    # noqa: E402

AWS = SETTINGS["aws"]
OWNER, CLICKS = str(AWS["account_id"]), AWS["slack_clicks_prefix"]
SIGNING = "8e1f0c4b2a9d7e6f5c3b1a0d9e8f7c6b"         # made up
TOKEN = "xoxb-1111-2222-zzTESTzzNOTrealQQ"           # made up
DAY = "2026-10-19"

failed = 0


def check(ok, name, detail=""):
    global failed
    failed += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ->  {detail}" if not ok and detail else ""))


class FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, ContentType=None, ExpectedBucketOwner=None):
        if ExpectedBucketOwner != OWNER:
            raise PermissionError("wrong or missing ExpectedBucketOwner")
        self.objects[Key] = Body


class FakeSSM:
    def __init__(self):
        self.reads = []

    def get_parameter(self, Name, WithDecryption=False):
        self.reads.append((Name, WithDecryption))
        value = {AWS["slack_signing_secret_parameter"]: SIGNING, AWS["slack_bot_token_parameter"]: TOKEN}[Name]
        return {"Parameter": {"Value": value}}


class FakeSlack:
    """Stands in for urllib's urlopen: records each Web API call."""

    def __init__(self, down=False):
        self.calls, self.down = [], down

    def __call__(self, req, timeout=None):
        if self.down:
            raise TimeoutError("slack is slow")
        self.calls.append((req.full_url.rsplit("/", 1)[-1], req.headers.get("Authorization"), json.loads(req.data)))
        return io.BytesIO(json.dumps({"ok": True}).encode())


# --- build the bundle exactly as the deploy does, and load its handler ---
build = Path(tempfile.mkdtemp())
out = bundle_slack(build / "slack")
os.environ.update({"BUCKET": "test-bucket", "ACCOUNT_ID": OWNER, "CLICKS_PREFIX": CLICKS,
                   "SIGNING_SECRET_PARAMETER": AWS["slack_signing_secret_parameter"],
                   "BOT_TOKEN_PARAMETER": AWS["slack_bot_token_parameter"],
                   "MAX_AGE_SECONDS": str(AWS["slack_max_age_seconds"])})
sys.path.insert(0, str(out))
for name in ("settings", "slack_loop"):              # load them from the bundle, not the repo
    sys.modules.pop(name, None)
import handler                                       # noqa: E402

print("\nThe bundle")
files = sorted(p.name for p in out.iterdir() if p.is_file())
check(files == sorted(["handler.py", *SLACK_FILES]), "holds the handler and slack_loop's few files, nothing else", files)
check(Path(handler.__file__).parent == out and Path(sys.modules["slack_loop"].__file__).parent == out,
      "runs from the bundle alone (no repo file needed)")

REF = {"a": "acc_037", "f": "churn", "s": "Act", "o": "csm_anna", "n": "Yarrow Studio", "d": DAY}
CARD = [{"type": "section", "text": {"type": "mrkdwn", "text": "*Yarrow Studio*"}},
        {"type": "actions", "block_id": "task_actions", "elements": [
            {"type": "button", "action_id": "claim", "value": json.dumps(REF)},
            {"type": "button", "action_id": "log_call", "value": json.dumps(REF)},
            {"type": "button", "action_id": "open_timeline", "url": "https://example.invalid/#acc_037"}]}]


def click(action_id, ref=REF, action_ts="1762420000.123456"):
    return {"type": "block_actions", "user": {"id": "U_ANNA"}, "trigger_id": "trig-1",
            "channel": {"id": "C_QUEUE"}, "message": {"ts": "111.222", "blocks": CARD},
            "actions": [{"action_id": action_id, "value": json.dumps(ref), "action_ts": action_ts}]}


def request(payload, secret=SIGNING, age=0, tamper=False, b64=True, headers=True):
    body = "payload=" + urllib.parse.quote(json.dumps(payload))
    ts = str(int(time.time()) - age)
    sig = "v0=" + hmac.new(secret.encode(), f"v0:{ts}:{body}".encode(), hashlib.sha256).hexdigest()
    if tamper:
        body = body.replace("U_ANNA", "U_EVIL")
    sent = base64.b64encode(body.encode()).decode() if b64 else body
    hdrs = {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig} if headers else {}
    return {"headers": hdrs, "body": sent, "isBase64Encoded": b64}


def run(event, slack=None):
    s3, ssm, slack = FakeS3(), FakeSSM(), slack or FakeSlack()
    printed = io.StringIO()
    with contextlib.redirect_stdout(printed):
        result = handler.handler(event, None, s3=s3, ssm=ssm, opener=slack)
    return result, s3, ssm, slack, printed.getvalue()


def events(s3):
    return {k: json.loads(v) for k, v in s3.objects.items()}


print("\nRefused before anything happens (401, nothing saved, Slack not called)")
for title, ev in [("no signature at all", request(click("claim"), headers=False)),
                  ("signed with the wrong secret", request(click("claim"), secret="not-the-secret")),
                  ("body changed after Slack signed it", request(click("claim"), tamper=True)),
                  ("signed 6 minutes ago (a replayed request)", request(click("claim"), age=360)),
                  # audit 6 Nov 2026: a timestamp in the future is as suspicious as an old one
                  ("signed 6 minutes in the future", request(click("claim"), age=-360))]:
    result, s3, _, slack, _ = run(ev)
    check(result["statusCode"] == 401 and not s3.objects and not slack.calls, title, result)
result, s3, _, _, _ = run(request(click("claim"), age=60))
check(result["statusCode"] == 200 and len(s3.objects) == 1, "a minute old is fine (clocks differ a little)")
result, s3, _, _, _ = run(request(click("claim"), b64=False))
check(result["statusCode"] == 200 and len(s3.objects) == 1, "accepted whether or not API Gateway base64-encodes the body")

print("\nEach button does what listener.py does")
result, s3, ssm, slack, printed = run(request(click("claim")))
(key, ev), = events(s3).items() or [(None, {})]
check(result["statusCode"] == 200 and key and key.startswith(f"{CLICKS}{DAY}/") and key.endswith(f"{ev.get('event_id')}.json"),
      "I'll take it: one file, state/clicks/<run date>/<event id>.json", key)
check(ev.get("event_name") == "task_claimed" and ev.get("timestamp", "").startswith(DAY)
      and ev["properties"]["owner_id"] == "csm_anna" and ev["properties"]["topic"] == "churn"
      and ev["properties"]["slack_user"] == "U_ANNA" and ev["context"]["source"] == "slack",
      "...a task_claimed event dated on the card's run date, like the laptop's", ev)
method, auth, sent = slack.calls[0] if slack.calls else (None, None, {})
check(method == "chat.update" and auth == f"Bearer {TOKEN}" and sent.get("channel") == "C_QUEUE"
      and "Taken by <@U_ANNA>" in json.dumps(sent.get("blocks"))
      and "claim" not in json.dumps([e["action_id"] for b in sent["blocks"] for e in b.get("elements", []) if "action_id" in e]),
      "...the card shows who took it, and loses the I'll take it button", sent)

result, s3, _, slack, _ = run(request(click("log_call")))
method, _, sent = slack.calls[0] if slack.calls else (None, None, {})
check(result["statusCode"] == 200 and not s3.objects and method == "views.open" and sent.get("trigger_id") == "trig-1"
      and sent["view"]["callback_id"] == "log_call_form",
      "Log call: opens the form, records nothing yet")

meta = json.loads(sent["view"]["private_metadata"]) if sent else {}
submit = {"type": "view_submission", "user": {"id": "U_ANNA"},
          "view": {"callback_id": "log_call_form", "private_metadata": json.dumps(meta), "state": {"values": {
              "outcome": {"value": {"selected_option": {"value": "reached_at_risk"}}},
              "note": {"value": {"value": "budget cut confirmed"}}}}}}
result, s3, _, slack, _ = run(request(submit))
ev = next(iter(events(s3).values()), {})
check(result["statusCode"] == 200 and result["body"] == "" and ev.get("event_name") == "meeting_held"
      and ev["properties"]["topic"] == "churn_call" and ev["properties"]["outcome"] == "reached_at_risk"
      and ev["properties"]["note"] == "budget cut confirmed" and ev["timestamp"].startswith(DAY),
      "Form sent: a meeting_held churn_call with outcome and note; an empty 200 closes the form", ev)
method, _, sent = slack.calls[0] if slack.calls else (None, None, {})
check(method == "chat.update" and sent.get("channel") == "C_QUEUE" and sent.get("ts") == "111.222"
      and "Call logged by <@U_ANNA>" in json.dumps(sent.get("blocks")),
      "...and the original card shows the call")

hb = {**REF, "f": "journey", "s": "Follow-up not picked up", "o": "mgr_sophie"}
result, s3, _, slack, _ = run(request(click("approve_handback", hb)))
ev = next(iter(events(s3).values()), {})
check(ev.get("event_name") == "handback_approved" and ev["properties"]["topic"] == "handback"
      and slack.calls and "Hand-back approved by <@U_ANNA>" in json.dumps(slack.calls[0][2]),
      "Approve hand-back: a handback_approved event, the card says who approved")

result, s3, _, slack, _ = run(request(click("open_timeline")))
check(result["statusCode"] == 200 and not s3.objects and not slack.calls, "Open timeline: nothing recorded (looking isn't acting)")

print("\nSafety")
bad = {**REF, "d": "../../inputs/crm_accounts"}
result, s3, _, _, _ = run(request(click("claim", bad)))
key = next(iter(s3.objects), "")
check(key.startswith(CLICKS) and ".." not in key and "inputs" not in key,
      "a run date that isn't a date can't move the file out of state/clicks/", key)
old = {k: v for k, v in REF.items() if k != "d"}
result, s3, _, _, _ = run(request(click("claim", old)))
check(next(iter(s3.objects), "").startswith(CLICKS), "a card from before run dates were on buttons still works")
result, s3, _, slack, printed = run(request(click("claim")), slack=FakeSlack(down=True))
check(result["statusCode"] == 200 and len(s3.objects) == 1 and "TimeoutError" in printed,
      "Slack slow or down: the click is saved anyway, the failure logged", printed)
check(not any(v in printed or v[:10] in printed or v[-8:] in printed for v in (SIGNING, TOKEN)),
      "...and that failure message holds no part of a secret (audit 6 Nov 2026)", printed)

print("\nThe same click twice (a request replayed inside the 5-minute window, audit 6 Nov 2026)")
s3, ssm, slack = FakeS3(), FakeSSM(), FakeSlack()
same = request(click("claim"))
with contextlib.redirect_stdout(io.StringIO()):
    for _ in range(2):
        handler.handler(same, None, s3=s3, ssm=ssm, opener=slack)
check(len(s3.objects) == 1, "is saved once: the file is named after Slack's own click id, so a replay overwrites it",
      list(s3.objects))
with contextlib.redirect_stdout(io.StringIO()):
    handler.handler(request(click("claim", action_ts="1762420099.000001")), None, s3=s3, ssm=ssm, opener=slack)
check(len(s3.objects) == 2, "a real second click (its own action_ts) is still a second event", list(s3.objects))
form = {**submit, "view": {**submit["view"], "id": "V_123"}}
s3 = FakeS3()
with contextlib.redirect_stdout(io.StringIO()):
    for _ in range(2):
        handler.handler(request(form), None, s3=s3, ssm=ssm, opener=slack)
check(len(s3.objects) == 1, "a call form submitted twice (the same view id) is one call", list(s3.objects))
check(handler.click_id(form) == handler.click_id(json.loads(json.dumps(form))) and handler.click_id({"type": "x"}) is None,
      "the click id comes from Slack's ids only; without them, a fresh id (as before)")

handler._secrets.clear()
s3, ssm, slack, printed = FakeS3(), FakeSSM(), FakeSlack(), io.StringIO()
with contextlib.redirect_stdout(printed):
    for _ in range(3):
        handler.handler(request(click("claim")), None, s3=s3, ssm=ssm, opener=slack)
    handler.handler(request(click("claim"), secret="nope"), None, s3=s3, ssm=ssm, opener=slack)
check(len(ssm.reads) == 2 and all(decrypt for _, decrypt in ssm.reads),
      "each secret read once (decrypted), then kept while the Lambda is warm", ssm.reads)
text = printed.getvalue()
check(not any(v in text or v[:10] in text or v[-8:] in text for v in (SIGNING, TOKEN)) and "U_EVIL" not in text,
      "never prints a secret or any part of one, nor a refused request's body", text)

print("\nFAILED" if failed else "\nOK")
sys.exit(1 if failed else 0)
