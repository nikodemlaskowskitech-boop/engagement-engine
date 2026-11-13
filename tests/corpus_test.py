"""
The search corpus (generate_documents.py): the documents a search will be tested on
must be right before the search is.

  - seeded: two runs write the same files, byte for byte;
  - documents.py reads every file (one way to read documents, ARCHITECTURE.md fix D);
  - every account the answer key names for a question has a document on that topic,
    and every question has accounts to find (at least 2);
  - every account has routine documents too, and no trap account is an answer;
  - every person in a document is in contacts.csv (so redaction can find them);
  - the corpus accounts are real accounts of today's book.

Runs the generator itself (needs data/ from generate_events.py). No DuckDB.
Run: python tests/corpus_test.py
"""
import csv
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import documents                                   # noqa: E402
from settings import OUTPUTS, SETTINGS            # noqa: E402

CORPUS = OUTPUTS / SETTINGS["search"]["corpus_folder"]
failed = 0


def check(ok, what, detail=""):
    global failed
    print(f"  {'PASS' if ok else 'FAIL'}  {what}" + (f"  ->  {str(detail)[:300]}" if detail and not ok else ""))
    failed += not ok


def fingerprint():
    return {str(p.relative_to(CORPUS)): hashlib.md5(p.read_bytes()).hexdigest()
            for p in sorted(CORPUS.rglob("*")) if p.is_file()}


def generate():
    subprocess.run([sys.executable, "generate_documents.py"], cwd=ROOT, check=True, capture_output=True)


print("\nSeeded")
generate()
first = fingerprint()
generate()
check(first == fingerprint() and first, f"two runs, the same {len(first)} files byte for byte")

print("\nRead one way")
docs = documents.all_documents(CORPUS)
n_files = len(list((CORPUS / "emails").glob("*.eml"))) + len(list((CORPUS / "calls").glob("*.txt")))
with (CORPUS / SETTINGS["handover"]["support_file"]).open(encoding="utf-8") as f:
    n_files += sum(1 for _ in csv.DictReader(f))
check(len(docs) == n_files, f"documents.py reads every document ({len(docs)})", (len(docs), n_files))
by_acc = {}
for d in docs:
    by_acc.setdefault(d["account_id"], []).append(d)

print("\nThe answer key")
key = json.loads((CORPUS / SETTINGS["search"]["key_file"]).read_text(encoding="utf-8"))
# the generator names each file after its topic; tickets carry the topic's subject
for q in key["questions"]:
    check(len(q["accounts"]) >= 2, f"{q['id']}: {len(q['accounts'])} accounts to find")
    check(not set(q["accounts"]) & set(q["traps"]), f"{q['id']}: no trap account is an answer")

# A topic's documents: emails and calls by file name, tickets by their subject
from generate_documents import TEMPLATES      # noqa: E402
subjects = {topic: t["ticket"][0] for topic, t in TEMPLATES.items()}


def topics_of(account_id):
    found = set()
    for d in by_acc.get(account_id, []):
        for topic in TEMPLATES:
            if topic.replace("_", "-") + "-" in d["source"] or d["title"].endswith(subjects[topic].split("{")[0].strip()):
                found.add(topic)
    return found


for q in key["questions"]:
    missing = [a for a in q["accounts"] if q["id"] not in topics_of(a)]
    check(not missing, f"{q['id']}: every account in the key has a document on it", missing)

print("\nRoutine documents and the book")
routine = {a for a, ds in by_acc.items() if any("routine-" in d["source"] or "Password" in d["title"]
                                               or "billing address" in d["title"] or "Time zone" in d["title"]
                                               for d in ds)}
check(routine == set(by_acc), "every account has routine documents", set(by_acc) - routine)
with (OUTPUTS / "crm_accounts.csv").open(encoding="utf-8") as f:
    book = {r["account_id"]: r for r in csv.DictReader(f)}
check(all(a in book and book[a]["status"] != "churned" for a in by_acc), "every corpus account is in today's book")
check(len(by_acc) == 30, f"{len(by_acc)} accounts")

print("\nPeople")
with (CORPUS / SETTINGS["handover"]["contacts_file"]).open(encoding="utf-8") as f:
    contacts = list(csv.DictReader(f))
known = {r["name"] for r in contacts}
team = {r["owner_name"] for r in csv.DictReader((OUTPUTS / "cs_team.csv").open(encoding="utf-8"))}
# handover.py labels side "cs" as CS 1, anything else as Customer A: our people must say "cs"
check(all((r["side"] == "cs") == (r["name"] in team) for r in contacts), "our people are side 'cs', customers 'customer'",
      [r for r in contacts if (r["side"] == "cs") != (r["name"] in team)][:3])
names = set()
for d in docs:
    names |= set(re.findall(r"^\[\d\d:\d\d:\d\d\] ([^:]+):", d["text"], re.M))
    names |= set(re.findall(r"(?:From|To|Cc): ([^<\n]+) <", d["text"]))
    names |= set(re.findall(r"by ([A-Z]\w+ [A-Z]\w+) <", d["text"]))
names = {n.strip() for n in names}
check(names and names <= known, f"all {len(names)} people in the documents are in contacts.csv", names - known)

print(f"\n{'All passed' if not failed else f'{failed} failed'}")
sys.exit(1 if failed else 0)
