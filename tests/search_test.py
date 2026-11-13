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

Then the honest number: the same on tests/heldout/, a set written by a separate agent\nthat never saw the synonym list or the corpus, each question asked three ways.\n\nNeeds DuckDB, data/ and the corpus (python generate_documents.py).
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
def recall_table(synonyms):
    """Per question: the right accounts in the top k, how many of the top are right, traps."""
    out = []
    for q in key["questions"]:
        top = [a["account_id"] for a in search.accounts(con, q["question"], k, synonyms=synonyms)]
        out.append({"right": [a for a in top if a in q["accounts"]], "top": top,
                    "traps": [a for a in top if a in q["traps"]]})
    return out


exact, wide = recall_table(False), recall_table(True)
wanted = sum(len(q["accounts"]) for q in key["questions"])
print(f"\n  {'question':<62} {'keywords':>12} {'+ synonyms':>12}  traps (with synonyms)")
for q, e, w in zip(key["questions"], exact, wide):
    cell = lambda r: f"{len(r['right'])}/{len(q['accounts'])} of {len(r['top'])}"   # noqa: E731
    print(f"  {q['question']:<62} {cell(e):>12} {cell(w):>12}  {', '.join(w['traps']) or '-'}")
r_exact = sum(len(e["right"]) for e in exact) / wanted
r_wide = sum(len(w["right"]) for w in wide) / wanted
p_wide = sum(len(w["right"]) for w in wide) / max(1, sum(len(w["top"]) for w in wide))
print(f"\n  Recall, keywords alone: {sum(len(e['right']) for e in exact)} of {wanted} ({r_exact:.0%})")
print(f"  Recall, with synonyms:  {sum(len(w['right']) for w in wide)} of {wanted} ({r_wide:.0%}); "
      f"precision {p_wide:.0%} (right accounts among those returned)")
recall = r_wide
check(r_wide >= r_exact, "synonyms never lose an account keywords found overall")
check(all(set(e["right"]) <= set(w["right"]) for e, w in zip(exact, wide)),
      "per question too: every account found by keywords is still found with synonyms")
check(recall >= float(CONF["min_recall"]),
      f"recall {recall:.0%} at least [search] min_recall ({float(CONF['min_recall']):.0%}): no step backwards")

print("\nThe held-out set: written apart from the vocabulary (the fair number)")
# tests/heldout/ was written by a separate agent that never saw the synonym list, the
# corpus or this search. Each question is asked three ways: as written, and two rewordings.
HELD = ROOT / "tests" / "heldout"
hkey = json.loads((HELD / "key.json").read_text(encoding="utf-8"))
con_h = connect()
search.add_views(con_h, [HELD])
asks = [(q, text) for q in hkey["questions"] for text in [q["question"], *q.get("variants", [])]]
found = {False: 0, True: 0}
wanted_h = 0
print(f"\n  {'question (as asked)':<70} {'keywords':>9} {'+ synonyms':>11}")
for q, text in asks:
    row = []
    for syn in (False, True):
        top = [a["account_id"] for a in search.accounts(con_h, text, k, synonyms=syn)]
        right = len([a for a in top if a in q["accounts"]])
        found[syn] += right
        row.append(f"{right}/{len(q['accounts'])}")
    wanted_h += len(q["accounts"])
    print(f"  {text[:70]:<70} {row[0]:>9} {row[1]:>11}")
h_exact, h_wide = found[False] / wanted_h, found[True] / wanted_h
print(f"\n  Held-out recall, keywords alone: {found[False]} of {wanted_h} ({h_exact:.0%})")
print(f"  Held-out recall, with synonyms:  {found[True]} of {wanted_h} ({h_wide:.0%})")
check(h_wide >= h_exact, "on the held-out set too, synonyms find at least what keywords find")
check(h_wide >= float(CONF["min_heldout_recall"]),
      f"held-out recall {h_wide:.0%} at least [search] min_heldout_recall ({float(CONF['min_heldout_recall']):.0%})")

print("\nRedacted results: the only form that may leave the laptop")
import csv as _csv                                   # noqa: E402
import handover                                      # noqa: E402
for label, con_r, folder, qs in (("corpus", con, OUTPUTS / CONF["corpus_folder"], key["questions"]),
                                 ("held-out", con_h, HELD, hkey["questions"])):
    with (folder / SETTINGS["handover"]["contacts_file"]).open(encoding="utf-8") as f:
        people = {r["name"] for r in _csv.DictReader(f)}
    with (folder / "directory.csv").open(encoding="utf-8") as f:
        companies = {r["company"] for r in _csv.DictReader(f)}
    leaks, lost, held_all, n_hits = [], [], [], 0
    for q in qs:
        for text in [q["question"], *q.get("variants", [])]:
            hits = search.search(con_r, text, 30)
            safe, held = search.redacted(con_r, hits)
            n_hits += len(safe)
            held_all += held
            if {h["account_id"] for h in safe} | {b["account_id"] for b in held} != {h["account_id"] for h in hits}:
                lost.append(text)
            for h in safe:
                blob = " ".join((h["title"], h["line"], h["text"]))
                bad = ([n for n in people if n in blob] + [c for c in companies if c in blob]
                       + handover.EMAIL_RE.findall(blob) + handover.phones(blob))
                if bad:
                    leaks.append((h["account_id"], h["id"], bad))
    check(not leaks, f"{label}: {n_hits} redacted results, no name, company, email or phone in any", leaks[:3])
    check(not lost, f"{label}: every account is either in the results or held back, none silently dropped", lost[:3])
    check(not held_all, f"{label}: nothing held back (each pair the leak check stopped was looked at once)",
          sorted({(b["account_id"], f) for b in held_all for f in b["found"]})[:5])

# A name nobody listed, only in a document's body: redaction can't know it, the leak check must
tmp_r = Path(tempfile.mkdtemp())
for sub in ("emails", "calls"):
    (tmp_r / sub).mkdir()
(tmp_r / "directory.csv").write_text("account_id,company,email_domain,helpdesk_org,handover_from,handover_to\n"
                                     "acc_r,Rook Labs,rook.example,Rook Labs Ltd,,\n", encoding="utf-8")
(tmp_r / SETTINGS["handover"]["support_file"]).write_text(
    "ticket_id,organization,requester_name,requester_email,created_at,updated_at,priority,type,status,subject,description,latest_comment\n"
    "R-1,Rook Labs Ltd,Pat Doe,pat.doe@rook.example,2026-10-01,2026-10-02,P3,question,open,Invoice dispute,"
    "\"We dispute the overage invoice. Please loop in Ingrid Solberg from our legal team.\",noted\n", encoding="utf-8")
con_r = connect()
search.add_views(con_r, [tmp_r])
safe, held = search.redacted(con_r, search.search(con_r, "invoice dispute"))
check(not safe and held and "Ingrid Solberg" in held[0]["found"],
      "a name nobody listed, in a body: the account is held back, not sent", (safe, held))
check(all("Pat Doe" not in str(h) for h in safe), "the requester (known by position) never appears")

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
