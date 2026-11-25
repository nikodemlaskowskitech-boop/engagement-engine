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
        top = [a["account_id"] for a in search.accounts(con, q["question"], k, synonyms=synonyms, mode="keywords")]
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
# Per question, synonyms find at least as many right accounts as keywords alone. Not "every
# account keywords found": in a top 10, the synonyms' extra matches can push one out while
# bringing two in (25 Nov 2026, 45 accounts: capacity went from 4 to 6 of 8, swapping one)
swaps = [(q["id"], sorted(set(e["right"]) - set(w["right"]))) for q, e, w in zip(key["questions"], exact, wide)
         if set(e["right"]) - set(w["right"])]
check(all(len(w["right"]) >= len(e["right"]) for e, w in zip(exact, wide)),
      "per question too: synonyms find at least as many right accounts as keywords"
      + (f" (swapped out by stronger matches: {swaps})" if swaps else ""))
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
        top = [a["account_id"] for a in search.accounts(con_h, text, k, synonyms=syn, mode="keywords")]
        right = len([a for a in top if a in q["accounts"]])
        found[syn] += right
        row.append(f"{right}/{len(q['accounts'])}")
    wanted_h += len(q["accounts"])
    print(f"  {text[:70]:<70} {row[0]:>9} {row[1]:>11}")
h_exact, h_wide = found[False] / wanted_h, found[True] / wanted_h
print(f"\n  Held-out recall, keywords alone: {found[False]} of {wanted_h} ({h_exact:.0%})")
print(f"  Held-out recall, with synonyms:  {found[True]} of {wanted_h} ({h_wide:.0%})")
# What a visitor of the demo site sees: the top [search] site_top_k, and whether the first is right
site_k = int(CONF["site_top_k"])
first = top_site = 0
for q, text in asks:
    top = [a["account_id"] for a in search.accounts(con_h, text, site_k, mode="keywords")]
    first += bool(top) and top[0] in q["accounts"]
    top_site += len([a for a in top if a in q["accounts"]])
print(f"  The demo site's view: first result right for {first} of {len(asks)} questions; "
      f"its top {site_k} finds {top_site} of {wanted_h} ({top_site / wanted_h:.0%})")
check(h_wide >= h_exact, "on the held-out set too, synonyms find at least what keywords find")
check(h_wide >= float(CONF["min_heldout_recall"]),
      f"held-out recall {h_wide:.0%} at least [search] min_heldout_recall ({float(CONF['min_heldout_recall']):.0%})")

print("\nMeaning (vectors, 22 Nov 2026): keywords, meaning and both, side by side")
# Only with the real model's vectors (python embed.py on the laptop, signed in to AWS): the
# stand-in's vectors measure nothing. Each question is one small Bedrock call. The held-out
# documents get vectors of their own, from their redacted text, in their own file.
import embed                                          # noqa: E402
from settings import output_file                      # noqa: E402
real = [r for r in embed.load().values() if r["model"] == SETTINGS["embed"]["model"]]
if SETTINGS["embed"]["provider"] != "bedrock" or not real:
    print("  skipped: no vectors from the real model here (on the laptop: python embed.py, then this test)")
else:
    every_h = [{"account_id": a, "id": i, "kind": kd, "date": str(d), "title": t, "line": ""}
               for a, i, kd, d, t in con_h.sql("SELECT account_id, id, kind, date, title FROM documents").fetchall()]
    red_h, held_h = search.redacted(con_h, every_h)
    held_path = output_file("vectors").with_name("vectors_heldout.jsonl")
    embed.build(path=held_path, docs=[{f: d[f] for f in ("account_id", "id", "title", "text")} for d in red_h])
    rows = []
    for label, con_m, qs, path in (("corpus", con, [(q, q["question"]) for q in key["questions"]], None),
                                   ("held-out", con_h, asks, held_path)):
        search.VECTORS_FILE = path
        con_m.sql("DROP TABLE IF EXISTS doc_vectors")
        got = {}
        for mode in ("keywords", "meaning", "hybrid"):
            got[mode] = sum(len([a for a in (x["account_id"] for x in search.accounts(con_m, text, k, mode=mode))
                                 if a in q["accounts"]]) for q, text in qs)
        want = sum(len(q["accounts"]) for q, _ in qs)
        rows.append((label, want, got))
        print(f"  {label:<9} keywords {got['keywords']}/{want} ({got['keywords'] / want:.0%})   "
              f"meaning {got['meaning']}/{want} ({got['meaning'] / want:.0%})   "
              f"hybrid {got['hybrid']}/{want} ({got['hybrid'] / want:.0%})")
    search.VECTORS_FILE = None
    print(f"  ([embed] mode today: {SETTINGS['embed']['mode']}; held back by the leak check, held-out: {len(held_h)})")
    # the floors for hybrid (23 Nov 2026, the mode since): never below keywords, on either set,
    # and the held-out bar; a search that fell back to keywords measured nothing, so it fails
    check(not search._fell_back, "every question reached the model (no fall-back to keywords)",
          "sign in: aws login --profile cairnpath")
    for label, want, got in rows:
        check(got["hybrid"] >= got["keywords"], f"{label}: hybrid finds at least what keywords find ({got['hybrid']} vs {got['keywords']})")
    held = rows[1]
    check(held[2]["hybrid"] / held[1] >= float(CONF["min_heldout_recall"]),
          f"held-out: hybrid recall {held[2]['hybrid'] / held[1]:.0%} at least [search] min_heldout_recall ({float(CONF['min_heldout_recall']):.0%})")

    print("\nThe floor (23 Nov 2026): how similar is a right answer, and how similar is nonsense?")
    # Meaning always has a nearest document. The floor ([embed] min_similarity) has to sit above
    # what an off-topic question reaches and below what a right document reaches.
    off = json.loads((ROOT / "tests" / "offtopic.json").read_text(encoding="utf-8"))["questions"]
    search.VECTORS_FILE = held_path
    con_h.sql("DROP TABLE IF EXISTS doc_vectors")
    nonsense = []
    for text in off:
        top = search.meaning(con_h, text, 1, floor=-1)
        nonsense.append(top[0]["score"] if top else -1)
    right = []                                     # each held-out ask: its best-scoring right document
    for q, text in asks:
        hits = [h for h in search.meaning(con_h, text, 50, floor=-1) if h["account_id"] in q["accounts"]]
        right.append(hits[0]["score"] if hits else -1)
    search.VECTORS_FILE = None
    worst_off, s_right = max(nonsense), sorted(right)
    print(f"  off-topic ({len(off)}): best document's similarity {min(nonsense):.3f} to {worst_off:.3f}")
    print(f"  held-out ({len(right)} asks): best right document {s_right[0]:.3f} (lowest), "
          f"{s_right[len(s_right) // 4]:.3f} (a quarter below), {s_right[len(s_right) // 2]:.3f} (median)")
    suggest = round(worst_off + 0.02, 2)
    kept = sum(1 for x in right if x >= suggest)
    print(f"  suggested [embed] min_similarity = {suggest}: every off-topic question finds nothing by meaning, "
          f"{kept} of {len(right)} held-out asks still reach a right document by meaning")
    floor = float(SETTINGS["embed"].get("min_similarity", 0))
    if floor > 0:
        search.VECTORS_FILE = held_path
        con_h.sql("DROP TABLE IF EXISTS doc_vectors")
        leaks = [t for t in off if search.meaning(con_h, t, 5)]
        after = sum(len([a for a in (x["account_id"] for x in search.accounts(con_h, text, k, mode="hybrid"))
                         if a in q["accounts"]]) for q, text in asks)
        search.VECTORS_FILE = None
        check(not leaks, f"with the floor ({floor}): no off-topic question finds anything by meaning", leaks)
        check(after >= held[2]["keywords"], f"...and hybrid still finds at least what keywords find on the held-out set "
                                            f"({after} vs {held[2]['keywords']})")
    else:
        print("  (no floor set yet: [embed] min_similarity = 0)")

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
