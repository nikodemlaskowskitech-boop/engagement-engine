"""
The Ask box's function (infra/lambdas/ask/handler.py, 26 Nov 2026), with a bucket in memory and
a stand-in model: no AWS, no key, no cost.

  - who: no sign-in = refused; a login linked to nobody = refused; an account not on that
    person's page = refused (the cockpit's own cut), and the leader sees every account;
  - what: a bad account ID, an empty or a too-long question: refused before anything runs;
  - the answer: from the redacted documents and the export (ask.ask_redacted), with its
    sources and checks; our team's names back in, a customer still a label;
  - money: the daily cap per login (the next question refused, the count kept by login and
    day), the monthly budget (refused, no call), its own ledger written back to the bucket;
  - privacy: nothing written to the bucket holds the question or the answer;
  - the bundle carries the redacted file and no raw document.

Needs data/views_data.json (run_all.ps1). Run: python tests/ask_lambda_test.py
"""
import io
import json
import os
import sys
import tempfile
import types
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "infra"))
OWNER = "111122223333"
os.environ.update({"BUCKET": "data-bucket", "ACCOUNT_ID": OWNER,
                   "ASK_LEDGER": str(Path(tempfile.mkdtemp()) / "ledger.csv")})
import bundle                                               # noqa: E402
from settings import SETTINGS                               # noqa: E402

OUT = bundle.bundle_ask(Path(tempfile.mkdtemp()) / "ask", deps=False)
sys.path.insert(0, str(OUT))
import handler                                              # noqa: E402
import llm                                                  # noqa: E402

failed = 0


def check(ok, what, detail=""):
    global failed
    print(f"  {'PASS' if ok else 'FAIL'}  {what}" + (f"  ->  {str(detail)[:300]}" if detail and not ok else ""))
    failed += not ok


class Missing(Exception):
    def __init__(self):
        self.response = {"Error": {"Code": "NoSuchKey"}}


class FakeS3:
    def __init__(self, objects):
        self.objects, self.writes = dict(objects), []

    def _owner(self, owner):
        if owner != OWNER:
            raise PermissionError("wrong or missing ExpectedBucketOwner")

    def head_object(self, Bucket, Key, ExpectedBucketOwner=None):
        self._owner(ExpectedBucketOwner)
        if Key not in self.objects:
            raise Missing()
        return {"ETag": '"%d"' % hash(self.objects[Key])}

    def get_object(self, Bucket, Key, ExpectedBucketOwner=None):
        self._owner(ExpectedBucketOwner)
        if Key not in self.objects:
            raise Missing()
        return {"Body": io.BytesIO(self.objects[Key])}

    def put_object(self, Bucket, Key, Body, ExpectedBucketOwner=None, **kw):
        self._owner(ExpectedBucketOwner)
        self.objects[Key] = Body
        self.writes.append(Key)


class StandIn:
    def __init__(self, text):
        self.text, self.sent = text, []
        self.messages = self

    def create(self, **kw):
        self.sent.append(kw)
        return types.SimpleNamespace(stop_reason="end_turn", content=[types.SimpleNamespace(type="text", text=self.text)],
                                     usage=types.SimpleNamespace(input_tokens=3_000, output_tokens=150))


export_bytes = (ROOT / "data" / "views_data.json").read_bytes()
export = json.loads(export_bytes)
ACC = "acc_177"                                            # a Scale account with documents (DACH)
acct = next(a for a in export["accounts"] if a["account_id"] == ACC)
member = next(t for t in export["team"] if t["owner_id"] == acct["owner_id"])          # the account's own owner
other = next(t for t in export["team"] if t["owner_type"] == "csm" and t["manager"] != acct["manager"])   # another team
CONF = SETTINGS["ask"]
NOW = datetime(2026, 11, 26, 10, tzinfo=timezone.utc)


def call(body, person=None, sub="login-1", s3=None, client=None, raw=None):
    claims = {} if person is None else {"sub": sub, "custom:person": person}
    event = {"requestContext": {"authorizer": {"jwt": {"claims": claims}}},
             "body": raw if raw is not None else json.dumps(body)}
    r = handler.handler(event, None, s3=s3, client=client or StandIn("Fine [DATA]."), now=NOW)
    return r["statusCode"], json.loads(r["body"])


def bucket():
    return FakeS3({"outputs/views_data.json": export_bytes})


Q = {"account_id": ACC, "question": "Why might they downsize at renewal?"}
llm.BUDGETS = {}

print("\nWho may ask")
s3 = bucket()
check(call(Q, s3=s3)[0] == 401, "no sign-in: refused")
check(call(Q, person="csm_nobody", s3=s3)[0] == 403, "a login linked to nobody in the CS team: refused")
code, body = call(Q, person=other["owner_id"], s3=s3)
check(code == 403 and "on your page" in body["error"], f"an account not on that person's page ({other['owner_id']}): refused", body)
check(not s3.writes, "nothing written for a refused question")

print("\nWhat may be asked")
for bad, why in (({"account_id": "acc_1' --", "question": "x"}, "a bad account ID"),
                 ({"account_id": ACC, "question": "  "}, "an empty question"),
                 ({"account_id": ACC, "question": "x" * (int(CONF["question_chars"]) + 1)}, "a question over the limit")):
    check(call(bad, person=member["owner_id"], s3=s3)[0] == 400, f"{why}: refused before anything runs")
check(call(None, person=member["owner_id"], s3=s3, raw="{not json")[0] == 400, "a body that isn't JSON: refused")

print("\nThe answer")
s3 = bucket()
model = StandIn("Our team 1 heard Customer B say the contract must shrink at renewal [C1, DATA].")
code, body = call(Q, person=member["owner_id"], s3=s3, client=model)
check(code == 200 and model.sent, "a question on the person's own page is answered", body)
sent = json.dumps(model.sent)
check(acct["account_name"] not in sent and not [t["owner_name"] for t in export["team"] if t["owner_type"] != "pool" and t["owner_name"] in sent],
      "the model got no company and no team member's name")
check("Customer B" in body.get("answer", "") and "Our team 1" not in body.get("answer", ""),
      "our team's label is a name again; a customer stays a label", body.get("answer"))
check(body.get("sources") and body["sources"][0]["id"] == "DATA" and "checks" in body and body["as_of"] == export["as_of"],
      "with its sources, its checks and the data's as-of date")
code, _ = call(Q, person="leader", sub="login-leader", s3=s3, client=StandIn("Fine [DATA]."))
check(code == 200, "the leader may ask about any account")

print("\nMoney")
usage_key = CONF["usage_key"].format(month="2026-11")
usage = json.loads(s3.objects[usage_key])
check(usage.get("login-1", {}).get("2026-11-26") == 1 and usage.get("login-leader", {}).get("2026-11-26") == 1,
      "each question counted by login and day", usage)
usage["login-1"]["2026-11-26"] = int(CONF["daily_questions_per_person"])
s3.objects[usage_key] = json.dumps(usage).encode()
model = StandIn("x [DATA].")
code, body = call(Q, person=member["owner_id"], s3=s3, client=model)
check(code == 429 and not model.sent, f"over the daily cap ({CONF['daily_questions_per_person']}): refused, no call", body)
ledger = s3.objects.get(CONF["ledger_key"], b"").decode()
check(ledger.count("ask_account") == 2, "its own ledger, written back: one line per call", ledger[-200:])
check(float(SETTINGS["llm"]["monthly_budget_usd"]["aws"]["ask_account"]) > 0, "a monthly budget in AWS for ask_account")
s3 = bucket()
llm_budget = SETTINGS["llm"]["monthly_budget_usd"]["aws"]["ask_account"]
SETTINGS["llm"]["monthly_budget_usd"]["aws"]["ask_account"] = 0.0
model = StandIn("x [DATA].")
code, body = call(Q, person=member["owner_id"], s3=s3, client=model)
SETTINGS["llm"]["monthly_budget_usd"]["aws"]["ask_account"] = llm_budget
check(code == 429 and "budget" in body["error"] and not model.sent, "over the month's budget: refused, no call", body)

print("\nPrivacy: only counts are kept")
s3 = bucket()
secret_q = "Is the champion called Wilhelmina leaving? zebra-quokka"
model = StandIn("Not in the sources: nothing about that [DATA]. zebra-answer")
call({"account_id": ACC, "question": secret_q}, person=member["owner_id"], s3=s3, client=model)
stored = b" ".join(v for k, v in s3.objects.items() if k != "outputs/views_data.json")
check(b"zebra" not in stored and b"Wilhelmina" not in stored, "nothing in the bucket holds the question or the answer",
      [k for k in s3.objects])

print("\nThe bundle")
names = {p.name for p in OUT.iterdir()}
check({"handler.py", "ask.py", "documents.jsonl", "cockpit"} <= names, "the handler, ask.py, the redacted file, the cockpit's parts")
check(not (OUT / "stories").exists() and "answer_key.json" not in names and (OUT / "documents.jsonl").read_bytes()
      == (ROOT / SETTINGS["search"]["site_documents_file"]).read_bytes(), "no story folder, no answer key: only the redacted file")

print(f"\n{'All passed' if not failed else f'{failed} failed'}")
sys.exit(1 if failed else 0)
