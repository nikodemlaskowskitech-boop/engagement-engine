"""
One home for the unstructured data (documents.py, ARCHITECTURE.md fix D, 13 Nov 2026).

  - each system joins by its own key: the helpdesk by organisation, the mail archive
    by email domain, the call recorder by CRM account ID; no document lands on the
    wrong account;
  - every document has the same shape and a citation ID, stable per kind, oldest first;
  - handover.py reads them through documents.py (one way, not two);
  - the DuckDB table holds every document once, with its account.

No model, no AWS. Needs DuckDB. Run: python tests/documents_test.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import documents                                  # noqa: E402
import handover                                   # noqa: E402
from cairnpath_db import connect                  # noqa: E402

failed = 0


def check(ok, what, detail=""):
    global failed
    print(f"  {'PASS' if ok else 'FAIL'}  {what}" + (f"  ->  {str(detail)[:300]}" if detail and not ok else ""))
    failed += not ok


d = documents.directory()
print("\nEach system joins by its own key")
for acc, entry in d.items():
    docs = documents.for_account(acc)
    check(docs and all(set(x) == {"kind", "source", "date", "title", "text", "id"} for x in docs),
          f"{acc}: {len(docs)} documents, all the same shape")
    others = [e["email_domain"] for a, e in d.items() if a != acc]
    check(all("@" + entry["email_domain"] in x["text"] and not any("@" + o in x["text"].split("\n\n")[0] for o in others)
              for x in docs if x["kind"] == "E"), f"{acc}: its emails carry its domain, not another account's")
    check(all(f"CRM account: {acc}" in x["text"] for x in docs if x["kind"] == "C"), f"{acc}: its calls carry its CRM ID")
    for k in "SEC":
        ids = [x["id"] for x in docs if x["kind"] == k]
        check(ids == [f"{k}{i}" for i in range(1, len(ids) + 1)], f"{acc}: {k} citations numbered 1, 2, ... oldest first")
    check([x["date"] for x in docs] == sorted(x["date"] for x in docs), f"{acc}: oldest first")
check(documents.for_account("acc_without_story") == [], "an account with no story: no documents, no error")

print("\nOne way to read them")
check(not any(hasattr(handover, f) for f in ("tickets", "emails", "calls")),
      "handover.py has no readers of its own any more")
acc = next(iter(d))
check(handover.gather(acc)[4] == documents.for_account(acc), "handover.py's documents are documents.py's")

print("\nThe DuckDB table")
con = connect()
n = documents.add_table(con)
total = sum(len(documents.for_account(a)) for a in d)
check(n == total and con.sql("SELECT count(*) FROM documents").fetchone()[0] == total, f"every document once ({total})")
check(con.sql("SELECT count(DISTINCT account_id || id) FROM documents").fetchone()[0] == total,
      "account + citation ID is unique")
check(dict(con.sql("SELECT column_name, column_type FROM (DESCRIBE documents)").fetchall()).get("date") == "DATE",
      "dates are dates (searchable by period)")

print(f"\n{'All passed' if not failed else f'{failed} failed'}")
sys.exit(1 if failed else 0)
