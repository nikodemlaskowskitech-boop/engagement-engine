"""
Ask about one account (ask.py), with a stand-in model: no key, no cost, a temporary ledger.

  - privacy: what the model gets (facts, documents and the question itself) has no
    contact name, email, phone or company; a name nobody listed, typed into the question,
    stops the call before anything is sent;
  - retrieval: the documents chosen are the ones about the question; an account without
    documents is answered from its facts alone;
  - the answer: real names back in after the model, never before; a made-up citation and
    a number in no source are flagged; a cut-off answer is refused, not shown;
  - money: the call goes in the ledger as "ask_account"; over budget = no call; AWS has no
    budget for it, so it can't run there;
  - an account ID with a quote never reaches the SQL.

Needs DuckDB, data/ (views_data.json) and the corpus. Run: python tests/ask_test.py
"""
import csv
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import ask                                          # noqa: E402
import documents                                    # noqa: E402
import handover                                     # noqa: E402
import llm                                          # noqa: E402
from settings import SETTINGS                       # noqa: E402

failed = 0


def check(ok, what, detail=""):
    global failed
    print(f"  {'PASS' if ok else 'FAIL'}  {what}" + (f"  ->  {str(detail)[:300]}" if detail and not ok else ""))
    failed += not ok


class StandIn:
    """Answers like the model would; remembers what it was sent."""
    def __init__(self, text, stop="end_turn"):
        self.text, self.stop, self.sent = text, stop, []
        self.messages = self

    def create(self, **kw):
        self.sent.append(kw)
        return types.SimpleNamespace(stop_reason=self.stop, content=[types.SimpleNamespace(type="text", text=self.text)],
                                     usage=types.SimpleNamespace(input_tokens=3_000, output_tokens=200))


def refused(fn):
    """Runs fn; returns the message it exits with, or None if it didn't."""
    try:
        fn()
    except SystemExit as e:
        return str(e)
    return None


tmp = Path(tempfile.mkdtemp())
llm.LEDGER = tmp / "ai_spend.csv"
llm.BUDGETS = {**llm.BUDGETS, "ask_account": 1.00}
ACC = "acc_012"                                     # a story account: Nettle Foods, Hannah Weiss in an email body
with (documents.STORIES / SETTINGS["handover"]["contacts_file"]).open(encoding="utf-8") as f:
    people = [r for r in csv.DictReader(f) if r["account_id"] == ACC]
company = documents.directory()[ACC]["company"]

print("\nPrivacy: what the model gets")
r = ask.ask(ACC, "Is Hannah Weiss unhappy about the overage invoice?", dry_run=True)
sent = r["sent"] + "\n" + r["asked"]
leaks = [p["name"] for p in people if p["name"] in sent] + [p["email"] for p in people if p["email"] in sent]
check(not leaks and company not in sent and not handover.EMAIL_RE.findall(sent) and not handover.phones(sent),
      "no contact name, email, phone or the company in the facts, the documents or the question", leaks)
check("Hannah" not in r["asked"] and "Customer" in r["asked"], "a name typed into the question is replaced by its label",
      r["asked"])
model = StandIn("Fine [DATA].")
msg = refused(lambda: ask.ask(ACC, "Did Ingrid Solberg sign off the budget?", client=model))
check(msg and "Not sent" in msg and "Ingrid Solberg" in msg and not model.sent,
      "a name nobody listed, typed into the question: stopped before anything is sent", msg)

print("\nRetrieval: the documents about the question")
ids = [s["id"] for s in r["sources"]]
titles = " ".join(s["title"].lower() for s in r["sources"])
check(ids[0] == "DATA" and 1 < len(ids) <= 1 + int(SETTINGS["ask"]["top_k"]) and "invoice" in titles,
      f"the facts plus the matching documents ({', '.join(ids)}), the invoice ones among them", ids)
other = ask.ask("acc_300", "Why is this account flagged?", dry_run=True)
check([s["id"] for s in other["sources"]] == ["DATA"] and "the customer" in other["sent"],
      "an account without documents: answered from its facts alone, its name still hidden")

print("\nThe answer")
first = r["asked"].split(" unhappy")[0].replace("Is ", "")       # the label the question got, e.g. Customer C
model = StandIn(f"{first} disputes the overage invoice of 7,640 EUR [E1, E2]. They also asked about 999 seats [E9].")
res = ask.ask(ACC, "Is Hannah Weiss unhappy about the overage invoice?", client=model)
check(model.sent and "Hannah Weiss" not in str(model.sent), "the model was called, without the real name")
check("Hannah Weiss" in res["answer"] and first not in res["answer"], "the real name is back in the answer, after the model",
      res["answer"])
check(res["checks"]["unknown_citations"] == ["E9"], "a citation to a source that doesn't exist is flagged", res["checks"])
check("999" in res["checks"]["unverified_numbers"] and "7640" not in res["checks"]["unverified_numbers"],
      "a number in no source is flagged; one in a source isn't", res["checks"])
check(model.sent[0]["system"] == ask.SYSTEM_PROMPT and model.sent[0]["max_tokens"] == int(SETTINGS["ask"]["max_output_tokens"]),
      "its own instructions and output cap from settings")
msg = refused(lambda: ask.ask(ACC, "What happened?", client=StandIn("Half an ans", stop="max_tokens")))
check(msg and "No answer" in msg, "a cut-off answer is refused, not shown", msg)

print("\nMoney")
with llm.LEDGER.open(encoding="utf-8") as f:
    rows = list(csv.DictReader(f))
check(rows and all(x["feature"] == "ask_account" and x["account_id"] == ACC for x in rows),
      f"every call is in the ledger as ask_account ({len(rows)})")
llm.BUDGETS["ask_account"] = 0.0
model = StandIn("x [DATA].")
try:
    ask.ask(ACC, "What happened?", client=model)
    check(False, "over budget: refused")
except llm.OverBudget:
    check(not model.sent, "over budget: refused before any call")
check("ask_account" not in SETTINGS["llm"]["monthly_budget_usd"].get("aws", {}),
      "no budget in AWS: it can't run there (llm.call refuses a feature without one)")

print("\nOnly IDs reach the SQL")
msg = refused(lambda: ask.ask("acc_1' OR '1'='1", "x", dry_run=True))
check(msg and "not an account ID" in msg, "an account ID with a quote is refused", msg)

print(f"\n{'All passed' if not failed else f'{failed} failed'}")
sys.exit(1 if failed else 0)
