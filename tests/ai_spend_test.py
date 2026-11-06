"""
AI spend ledger test: is every model call recorded, and does the monthly budget
stop a call before it reaches the model?

    python tests/ai_spend_test.py

No API key and no cost: a stand-in model counts its calls. The ledger goes to a
temporary folder, never the real data/ai_spend.csv. The handover part needs
data/views_data.json (run .\\run_all.ps1 first). Exit 1 on any FAIL.
"""

import csv
import json
import os
import re
import shutil
import sys
import tempfile
import types
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

calls = []
BRIEF = "## Where things stand\n- Usage is low. [DATA]\n"


class _Messages:
    def create(self, **kw):
        calls.append(kw)
        return types.SimpleNamespace(stop_reason="end_turn", content=[types.SimpleNamespace(type="text", text=BRIEF)],
                                     usage=types.SimpleNamespace(input_tokens=10_000, output_tokens=1_000))


_fake = lambda **kw: types.SimpleNamespace(messages=_Messages())
sys.modules["anthropic"] = types.SimpleNamespace(Anthropic=_fake, AnthropicBedrock=_fake)

import llm        # noqa: E402

llm.load_api_key = lambda: None
tmp = Path(tempfile.mkdtemp())
llm.LEDGER = tmp / "ai_spend.csv"
client = _fake()
failed = 0


def check(ok, name, detail=""):
    global failed
    failed += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ->  {detail}" if not ok and detail else ""))


def rows():
    with llm.LEDGER.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


print("\nThe ledger: one row per call")
llm.BUDGETS = {"qbr_brief": 1.00, "handover": 1.00}
os.environ.pop("AWS_LAMBDA_FUNCTION_NAME", None)
llm.call(client, "qbr_brief", "acc_001", model="m", max_tokens=10, messages=[])
r = rows()
expected = llm.cost(10_000, 1_000)              # at the [llm] prices
check(len(r) == 1 and r[0]["feature"] == "qbr_brief" and r[0]["account_id"] == "acc_001"
      and r[0]["input_tokens"] == "10000" and r[0]["output_tokens"] == "1000", "records feature, account and tokens", r)
check(abs(float(r[0]["cost_usd"]) - expected) < 1e-9 and expected > 0,
      f"cost from the [llm] prices (${expected:.3f} for 10k in, 1k out)", r[0]["cost_usd"])
check(r[0]["where"] == "laptop" and r[0]["month"] == llm.this_month() and r[0]["stop_reason"] == "end_turn",
      "says where it ran, which month, and how the answer ended")
os.environ["AWS_LAMBDA_FUNCTION_NAME"] = "RunEngine"
llm.call(client, "handover", "acc_012", model="m", max_tokens=10, messages=[])
os.environ.pop("AWS_LAMBDA_FUNCTION_NAME")
check(rows()[-1]["where"] == "aws", "inside the engine Lambda it says aws")
check(llm.LEDGER.read_text(encoding="utf-8").count("time_utc") == 1 and len(rows()) == 2,
      "the header is written once; rows are added, never rewritten")

print("\nThe budget: checked before the call")
llm.BUDGETS = {"qbr_brief": expected * 2, "handover": 1.00}
before = len(calls)
llm.call(client, "qbr_brief", "acc_002", model="m", max_tokens=10, messages=[])   # reaches 2x: at the budget
try:
    llm.call(client, "qbr_brief", "acc_003", model="m", max_tokens=10, messages=[])
    check(False, "a feature at its budget is refused")
except llm.OverBudget as e:
    check(len(calls) == before + 1 and "qbr_brief" in str(e) and "budget" in str(e),
          "a feature at its budget is refused, and the model is never called", str(e))
check(len(rows()) == 3, "a refused call writes no row (nothing was spent)")
llm.call(client, "handover", "acc_012", model="m", max_tokens=10, messages=[])
check(len(calls) == before + 2, "another feature's budget is its own: handover still runs")
with llm.LEDGER.open("a", encoding="utf-8", newline="") as f:      # a big bill last month
    csv.DictWriter(f, fieldnames=llm.COLUMNS).writerow({**rows()[0], "month": "2000-01", "cost_usd": "999",
                                                       "feature": "handover"})
check(llm.spent("handover") < 1, "last month's spend doesn't count against this month", llm.spent("handover"))
try:
    llm.call(client, "something_new", "acc_001", model="m", max_tokens=10, messages=[])
    check(False, "a feature with no budget is refused")
except llm.OverBudget:
    check(True, "a feature with no budget in settings is refused: every feature must have a limit")
check("handover" in llm.spend_report() and "qbr_brief" in llm.spend_report(),
      "python llm.py --spend lists each feature against its budget")

print("\nBudgets split by place (audit 6 Nov 2026): the laptop and AWS can't see each other's ledger")
with llm.LEDGER.open("a", encoding="utf-8", newline="") as f:      # AWS spent a lot this month
    csv.DictWriter(f, fieldnames=llm.COLUMNS).writerow({**rows()[0], "where": "aws", "cost_usd": "999",
                                                       "feature": "qbr_brief", "month": llm.this_month()})
check(llm.spent("qbr_brief") < 1, "AWS's spend doesn't use up the laptop's budget", llm.spent("qbr_brief"))
os.environ["AWS_LAMBDA_FUNCTION_NAME"] = "RunEngine"
check(llm.spent("qbr_brief") >= 999, "...and counts against AWS's own", llm.spent("qbr_brief"))
os.environ.pop("AWS_LAMBDA_FUNCTION_NAME")
real = llm._BUDGETS
llm._BUDGETS = {"laptop": {"qbr_brief": 1.0}, "aws": {"qbr_brief": 2.0}}
check(llm.budgets_here("laptop") == {"qbr_brief": 1.0} and llm.budgets_here("aws") == {"qbr_brief": 2.0},
      "each place reads its own section")
llm._BUDGETS = {"qbr_brief": 3.0}
check(llm.budgets_here("laptop") == llm.budgets_here("aws") == {"qbr_brief": 3.0},
      "an old flat section (one amount per feature) still works, for both places")
llm._BUDGETS = real
places = {p: llm.budgets_here(p) for p in ("laptop", "aws")}
check(all(set(b) == {"qbr_brief", "handover"} for b in places.values()),
      "settings.toml gives the laptop and AWS a budget for both features", places)
total = sum(v for b in places.values() for v in b.values())
check(total <= 10, f"both places together (${total:.2f}) stay under the $10 Anthropic spend limit")

print("\nNothing goes around the ledger")
for script in ("brief.py", "handover.py"):
    source = (REPO / script).read_text(encoding="utf-8")
    check("messages.create" not in source and re.search(r"llm\.call\(", source),
          f"{script} calls the model only through llm.call")
settings = (REPO / "settings.toml").read_text(encoding="utf-8")
check(all(re.search(rf"^{f}\s*=", settings, re.M) for f in ("qbr_brief", "handover")),
      "both features have a budget in settings.toml [llm.monthly_budget_usd]")

views = REPO / "data" / "views_data.json"
if not views.exists():
    print("  SKIP  handover over budget (no data/views_data.json: run .\\run_all.ps1 first)")
else:
    import handover   # noqa: E402
    work = Path(tempfile.mkdtemp())
    shutil.copytree(REPO / "stories", work / "stories")
    shutil.copy(views, work / "views_data.json")
    handover.STORIES, handover.OUT, handover.VIEWS = work / "stories", work / "out", work / "views_data.json"
    acc = next(iter(handover.directory()))
    llm.BUDGETS = {"qbr_brief": 1.00, "handover": 0.0}
    before = len(calls)
    handover.run(acc, False, True)
    check(len(calls) == before and handover.OVER_BUDGET == [acc] and not (handover.OUT / f"{acc}.md").exists(),
          "handover.py over budget: no model call, no brief, and the account is listed (the run exits 1)")
    llm.BUDGETS = {"qbr_brief": 1.00, "handover": 1.00}
    handover.OVER_BUDGET.clear()
    handover.run(acc, False, True)
    check(len(calls) == before + 1 and rows()[-1]["feature"] == "handover" and rows()[-1]["account_id"] == acc,
          "within budget: one call, recorded against the account")
    shutil.rmtree(work)

shutil.rmtree(tmp)
print("\nFAILED" if failed else "\nOK")
sys.exit(1 if failed else 0)
