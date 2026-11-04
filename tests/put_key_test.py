"""
put_key.py test: the key goes to Parameter Store encrypted, and is never printed.

    python tests/put_key_test.py

No AWS: a stand-in Parameter Store, and a made-up key in the environment
(your real .env is not read: the key is already set). Exit 1 on any FAIL.
"""

import contextlib
import io
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
FAKE = "sk-ant-test-not-a-real-key-0123456789"
FAKE_SIGNING = "9f3c1ad07be24e58c6d1f0a4b7e9c2d5"   # made up, shaped like the real thing
FAKE_BOT = "xoxb-1111-2222-zzTESTzzNOTrealQQ"
os.environ["ANTHROPIC_API_KEY"] = FAKE

import llm         # noqa: E402
import put_key     # noqa: E402

llm.load_api_key = lambda: None        # never read the real .env in a test
fake_env = {"SLACK_SIGNING_SECRET": FAKE_SIGNING, "SLACK_BOT_TOKEN": FAKE_BOT}
put_key.load_env = lambda *names: [fake_env.get(n) for n in names]


class FakeSSM:
    def __init__(self):
        self.calls = []

    def put_parameter(self, **kw):
        self.calls.append(kw)
        return {"Version": 3}


failed = 0


def check(ok, name, detail=""):
    global failed
    failed += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ->  {detail}" if not ok and detail else ""))


print("\nput_key.py: the API key into Parameter Store")
ssm, out = FakeSSM(), io.StringIO()
with contextlib.redirect_stdout(out):
    version = put_key.store(ssm)
call = ssm.calls[0] if ssm.calls else {}
check(call.get("Name") == put_key.NAME and call.get("Value") == FAKE, "stores the key under [aws] api_key_parameter")
check(call.get("Type") == "SecureString" and call.get("Overwrite") is True,
      "as an encrypted SecureString, replacing the old one (key rotation)")
check(FAKE not in out.getvalue() and FAKE[:12] not in out.getvalue() and FAKE[-6:] not in out.getvalue(),
      "never prints the key, or any part of it", out.getvalue())
check(version == 3 and "version 3" in out.getvalue(), "says which version it stored")

print("\nput_key.py --slack: the Slack listener's two secrets")
ssm, out = FakeSSM(), io.StringIO()
with contextlib.redirect_stdout(out):
    put_key.store_slack(ssm)
stored = {c["Name"]: c for c in ssm.calls}
check(stored.get(put_key.AWS["slack_signing_secret_parameter"], {}).get("Value") == FAKE_SIGNING
      and stored.get(put_key.AWS["slack_bot_token_parameter"], {}).get("Value") == FAKE_BOT,
      "stores each under its [aws] parameter name")
check(all(c["Type"] == "SecureString" and c["Overwrite"] is True for c in ssm.calls) and len(ssm.calls) == 2,
      "both as encrypted SecureStrings, replacing the old ones")
text = out.getvalue()
check(not any(v in text or v[:10] in text or v[-6:] in text for v in (FAKE_SIGNING, FAKE_BOT)),
      "never prints either secret, or any part of one", text)
fake_env.pop("SLACK_BOT_TOKEN")
ssm, out = FakeSSM(), io.StringIO()
try:
    with contextlib.redirect_stdout(out):
        put_key.store_slack(ssm)
    stopped = False
except SystemExit as stop:
    stopped = "SLACK_BOT_TOKEN" in str(stop)
check(stopped and not ssm.calls, "one missing: stops and stores neither (no half-done pair)")

print("\nFAILED" if failed else "\nOK")
sys.exit(1 if failed else 0)
