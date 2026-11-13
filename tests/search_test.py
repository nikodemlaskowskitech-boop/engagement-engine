"""
Account search (search.py): does it find the accounts a CS person asks about?

Against the generated corpus and its answer key (generate_documents.py), for each of
the nine questions: recall (share of the right accounts in the top [search] top_k),
precision (share of the top that is right) and the traps it fell for. Prints the
table: it's the baseline the next steps (synonyms, vectors) must beat.

Then the mechanics, on made-up documents with known answers:
  - BM25: a rare word outranks a common one; a short note outranks a long one with
    the same words; more of the question's words ranks higher;
  - stems: "evaluating" finds "evaluate", "invoices" finds "invoice";
  - one account's search returns only that account; same question, same order;
  - only letters and digits reach the SQL.

Needs DuckDB, data/ and the corpus (python generate_documents.py).
Run: python tests/search_test.py
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import search                                       # noqa: E402
from cairnpath_db import connect                    # noqa: E402
from settings import OUTPUTS, SETTINGS             # noqa: E402

CONF = SETTINGS["search"]
failed = 0


def check(ok, what, detail=""):
    global failed
    print(f"  {'PASS' if ok else 'FAIL'}  {what}" + (f"  ->  {str(detail)[:300]}" if detail and not ok else ""))
    failed += not ok


print("\nRecall on the corpus (the baseline)")
con = connect()
n = search.add_views(con)
check(n > 100, f"{n} documents indexed (stories and corpus)")
key = json.loads((OUTPUTS / CONF["corpus_folder"] / CONF["key_file"]).read_text(encoding="utf-8"))
k = int(CONF["top_k"])
total_right = total_wanted = 0
print(f"\n  {'question':<64} {'recall':>8} {'precision':>10}  traps in top {k}")
for q in key["questions"]:
    top = [a["account_id"] for a in search.accounts(con, q["question"], k)]
    right = [a for a in top if a in q["accounts"]]
    traps = [a for a in top if a in q["traps"]]
    total_right += len(right)
    total_wanted += len(q["accounts"])
    prec = f"{len(right)}/{len(top)}" if top else "-"
    print(f"  {q['question']:<64} {len(right):>3}/{len(q['accounts']):<4} {prec:>10}  {', '.join(traps) or '-'}")
recall = total_right / total_wanted
print(f"\n  Overall recall: {total_right} of {total_wanted} accounts ({recall:.0%})")
check(recall >= float(CONF["min_recall"]),
      f"overall recall {recall:.0%} at least [search] min_recall ({float(CONF['min_recall']):.0%}): no step backwards")

print("\nMechanics, on made-up documents")
tmp = Path(tempfile.mkdtemp())
(tmp / "emails").mkdir()
(tmp / "calls").mkdir()
(tmp / "directory.csv").write_text("account_id,company,email_domain,helpdesk_org,handover_from,handover_to\n"
                                   "acc_a,Alpha,alpha.example,Alpha Ltd,,\nacc_b,Beta,beta.example,Beta Ltd,,\n"
                                   "acc_c,Gamma,gamma.example,Gamma Ltd,,\n", encoding="utf-8")
rows = [  # ticket_id, org, subject, description
    ("A-1", "Alpha Ltd", "Invoice question", "The invoice for March is wrong, says the team."),
    ("A-2", "Alpha Ltd", "Team update", "The team likes the team dashboards and the team digest."),
    ("B-1", "Beta Ltd", "Invoices", "Two invoices arrived. " + "We also discussed dashboards, funnels, cohorts and many other things at length. " * 6),
    ("B-2", "Beta Ltd", "Vendor review", "Procurement is evaluating other vendors this quarter, the team says."),
    ("C-1", "Gamma Ltd", "Team", "The team is growing."),
    ("C-2", "Gamma Ltd", "Overage invoice dispute", "We dispute the overage invoice."),
]
with (tmp / SETTINGS["handover"]["support_file"]).open("w", encoding="utf-8", newline="") as f:
    import csv
    w = csv.writer(f)
    w.writerow(["ticket_id", "organization", "requester_name", "requester_email", "created_at", "updated_at",
                "priority", "type", "status", "subject", "description", "latest_comment"])
    for tid, org, subject, desc in rows:
        w.writerow([tid, org, "Pat Doe", "pat@x.example", "2026-10-01", "2026-10-02", "P3", "question", "open",
                    subject, desc, "noted"])
con2 = connect()
search.add_views(con2, [tmp])
ids = lambda hits: [(h["account_id"], h["id"]) for h in hits]  # noqa: E731
hits = search.search(con2, "invoice")
check(("acc_b", "S1") in ids(hits) and ids(hits).index(("acc_a", "S1")) < ids(hits).index(("acc_b", "S1")),
      "a short note outranks a long one with the same word", ids(hits))
hits = search.search(con2, "team overage")      # team: 4 of 6 documents (one says it 5 times); overage: 1
check(ids(hits)[0] == ("acc_c", "S2"), "a rare word (overage) counts more than a common one, even said 5 times (team)", ids(hits))
hits = search.search(con2, "overage invoice dispute")
check(ids(hits)[0] == ("acc_c", "S2"), "more of the question's words ranks higher", ids(hits))
check(("acc_b", "S2") in ids(search.search(con2, "evaluate vendor")), "stems: evaluate finds evaluating, vendor finds vendors")
check(("acc_b", "S1") in ids(search.search(con2, "invoice")), "stems: invoice finds invoices")
only = search.search(con2, "invoice", account_id="acc_c")
check(only and all(h["account_id"] == "acc_c" for h in only), "one account's search: only that account")
check(search.search(con2, "invoice team") == search.search(con2, "invoice team"), "same question, same order")
search.search(con2, "'; DROP TABLE documents; --")
check(search._clean("x'; DROP TABLE documents; --") == "x  DROP TABLE documents", "quotes and semicolons never reach the SQL",
      search._clean("x'; DROP TABLE documents; --"))
check(con2.sql("SELECT count(*) FROM documents").fetchone()[0] == 6, "the documents table is still there")
try:
    search.search(con2, "invoice", account_id="acc_a' OR '1'='1")
    check(False, "an account ID with a quote is refused")
except ValueError:
    check(True, "an account ID with a quote is refused")
check(search.search(con2, "the and of") == [], "a question of stop words only: nothing, not everything")

print(f"\n{'All passed' if not failed else f'{failed} failed'}")
sys.exit(1 if failed else 0)
