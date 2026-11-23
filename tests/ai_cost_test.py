"""
The AI cost demo (ai_cost.py, 23 Nov 2026): runs on the demo corpus with no model call
and no key, and its numbers hold together.

  - one row per (account, question) of the corpus key, none calling a model;
  - RAG never sends more than everything, and picks at most [ask] top_k documents;
  - the cost adds up from the [llm] prices, the answer at its cap;
  - the projection grows with the history for "everything" and stays put for RAG;
  - the ledger's real calls are read when there are some (a temporary ledger here).

    python tests/ai_cost_test.py
"""
import csv
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import ai_cost                                      # noqa: E402
import llm                                          # noqa: E402

failed = 0


def check(ok, what, detail=""):
    global failed
    print(f"  {'PASS' if ok else 'FAIL'}  {what}" + (f"  ->  {detail}" if detail and not ok else ""))
    failed += not ok


def no_model(*a, **k):
    raise AssertionError("the cost demo called a model")


llm.call = no_model
import embed                                        # noqa: E402
embed.embed_query = no_model

tmp = Path(tempfile.mkdtemp())
llm.LEDGER = tmp / "ai_spend.csv"
print("\nThe demo on the corpus, no model call")
rows, text = ai_cost.main(tmp / "ai_cost_demo.txt")
check(len(rows) == len(ai_cost.pairs()) and len(rows) >= 30, f"one row per account and question ({len(rows)})")
check(all(r["rag_in"] <= r["all_in"] for r in rows), "RAG never sends more than everything")
check(all(r["picked"] <= int(ai_cost.CONF["top_k"]) and r["picked"] <= r["documents"] for r in rows),
      "RAG picks at most [ask] top_k of the account's documents")
check(abs(ai_cost.cost(1_000_000, 0) - ai_cost.PRICE_IN * 1e6) < 1e-9 and abs(ai_cost.cost(0, 1_000_000) - ai_cost.PRICE_OUT * 1e6) < 1e-9,
      "a million tokens in or out costs the [llm] price")
proj = [l for l in text.splitlines() if l.strip().split(" ")[0].isdigit() and "documents" in l]
alls = [int(l.split("everything")[1].split("tokens")[0].replace(",", "")) for l in proj]
rags = [int(l.split("RAG")[1].split("tokens")[0].replace(",", "")) for l in proj]
check(len(proj) == len(ai_cost.CONF["cost_demo_sizes"]) and alls == sorted(alls) and alls[0] < alls[-1] and len(set(rags[1:])) == 1,
      "the projection: everything grows with the history, RAG stays put", "; ".join(proj))
check("none yet" in text and (tmp / "ai_cost_demo.txt").exists(), "no real calls yet: it says so, and writes its report")

print("\nThe real calls, from the ledger")
with llm.LEDGER.open("w", encoding="utf-8", newline="") as f:
    w = csv.DictWriter(f, fieldnames=llm.COLUMNS)
    w.writeheader()
    for i in range(3):
        w.writerow({c: "" for c in llm.COLUMNS} | {"feature": "ask_account", "input_tokens": 700 + i, "output_tokens": 150, "cost_usd": "0.003"})
    w.writerow({c: "" for c in llm.COLUMNS} | {"feature": "handover", "input_tokens": 9000, "output_tokens": 900, "cost_usd": "0.03"})
_, text = ai_cost.main(tmp / "again.txt")
check("Real calls so far (ledger, ask_account): 3, median 701 tokens in and 150 out" in text,
      "the ask_account calls only, with their real counts", [l for l in text.splitlines() if l.startswith("Real")])

print(f"\n{'All passed' if not failed else f'{failed} FAILED'}")
sys.exit(1 if failed else 0)
