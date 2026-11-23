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
# an email logged by two people is two files with one Message-ID: one document (23 Nov 2026)
import email as _email                                             # noqa: E402
ids = {str(_email.message_from_bytes(p.read_bytes())["Message-ID"]).strip() for p in (CORPUS / "emails").glob("*.eml")}
n_copies = len(list((CORPUS / "emails").glob("*.eml"))) - len(ids)
check(n_copies > 0, f"some emails are logged twice, as in a real CRM ({n_copies})")
n_files = len(ids) + len(list((CORPUS / "calls").glob("*.txt")))
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
from generate_documents import TEMPLATES, WORDINGS      # noqa: E402
subjects = {topic: [w[0] for w in t["ticket"]] for topic, t in WORDINGS.items()}


def topics_of(account_id):
    found = set()
    for d in by_acc.get(account_id, []):
        for topic in TEMPLATES:
            if topic.replace("_", "-") + "-" in d["source"] or any(d["title"].endswith(sub.split("{")[0].strip()) for sub in subjects[topic]):
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

print("\nEvery call note is a logged meeting (14 Nov 2026)")
from generate_documents import CALL_TOPIC, MEETINGS_FILE      # noqa: E402
meetings = [json.loads(line) for line in (OUTPUTS / "engagement" / MEETINGS_FILE).read_text(encoding="utf-8").splitlines()]
calls = [d for d in docs if d["kind"] == "C"]
logged = {(m["account_id"], m["timestamp"][:10]) for m in meetings}
check(len(meetings) == len(calls) and all((c["account_id"], c["date"]) in logged for c in calls),
      f"each of the {len(calls)} call notes has its meeting (same account, same day), and no meeting is extra",
      [(c["account_id"], c["date"]) for c in calls if (c["account_id"], c["date"]) not in logged][:3])
from datetime import date as _date                              # noqa: E402
check(all(_date.fromisoformat(m["timestamp"][:10]).weekday() < 5 for m in meetings), "no meeting at a weekend")
check(all(m["properties"]["topic"] in set(CALL_TOPIC.values()) and m["context"]["source"] == "cs_history"
          and m["event_name"] == "meeting_held" for m in meetings), "topics from the engagement schema, written as CS history")
signed = {r["account_id"]: r["customer_since"] or r["contract_start"] for r in book.values()}
early = [(d["account_id"], d["date"]) for d in docs if d["date"] <= signed[d["account_id"]]]
check(not early, "no document from before the customer signed (bug found by hand 16 Nov 2026: four call notes were)", early)
check(all(m["timestamp"][:10] > signed[m["account_id"]] for m in meetings), "...so no meeting before it either")
owners = {r["account_id"]: r["csm_owner"] or r["pool_owner"] for r in book.values()}
check(all(m["properties"]["owner_id"] == owners[m["account_id"]] for m in meetings),
      "each meeting is the account owner's (a Scale account's named pool member)")

print("\nPeople")
with (CORPUS / SETTINGS["handover"]["contacts_file"]).open(encoding="utf-8") as f:
    contacts = list(csv.DictReader(f))
known = {r["name"] for r in contacts}
team = {r["owner_name"] for r in csv.DictReader((OUTPUTS / "cs_team.csv").open(encoding="utf-8"))}
# handover.py labels side "cs" as CS 1, anything else as Customer A: our people must say "cs"
# our people: the CS team, and (23 Nov 2026) the implementation leads, Cairnpath staff outside the CS team
check(all((r["side"] == "cs") == (r["name"] in team or r["email"].endswith("@cairnpath.example")) for r in contacts),
      "our people (the CS team, the implementation leads) are side 'cs', customers 'customer'",
      [r for r in contacts if (r["side"] == "cs") != (r["name"] in team)][:3])
names = set()
for d in docs:
    names |= set(re.findall(r"^\[\d\d:\d\d:\d\d\] ([^:]+):", d["text"], re.M))
    names |= set(re.findall(r"(?:From|To|Cc): ([^<\n]+) <", d["text"]))
    names |= set(re.findall(r"by ([A-Z]\w+ [A-Z]\w+) <", d["text"]))
names = {n.strip() for n in names}
check(names and names <= known, f"all {len(names)} people in the documents are in contacts.csv", names - known)

print("\nCases (23 Nov 2026): one problem, however many documents")
bad, merged = [], []
for c in key["cases"]:
    mine = documents.for_account(c["account_id"], CORPUS)
    got = [x for x in mine if x["source"] in c["files"]]
    case = {x["case"] for x in got}
    extra = [x["source"] for x in mine if x["case"] in case and x["source"] not in c["files"]]
    if len(got) != len(c["files"]) or len(case) != 1:
        bad.append(c)
    if extra:
        merged.append((c["account_id"], extra))
check(key["cases"] and not bad, f"each of the answer key's {len(key['cases'])} cases is one case in documents.py "
      "(the email, its replies, the ticket they quote, the call logged against it)", bad[:2])
check(not merged, "...and nothing else joins it: no merge by guess", merged[:2])
check(any(len(c["files"]) >= 4 for c in key["cases"]), "some cases run to 4+ documents (worth grouping)")
team = [x for a in documents.directory(CORPUS) for x in documents.for_account(a, CORPUS)]
check({"Support", "CS", "Implementation"} <= {x["team"] for x in team}, "three teams write: Support, CS and Implementation")
check(any(x["logged_by"] == ["CS", "Implementation"] for x in team), "...and an email logged by both is kept once, with both")

print(f"\n{'All passed' if not failed else f'{failed} failed'}")
sys.exit(1 if failed else 0)
