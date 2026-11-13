"""
The public search's documents (search_export.py): the only text the demo site's search
will ever see, so it's checked three ways.

  - up to date: the committed file is exactly what search_export.py builds today;
  - private, checked independently of the redaction: no contact name, company, helpdesk
    organisation, email domain, email address or phone number of the stories or the
    corpus appears anywhere in it, and no field but account_id, id, kind, date, title, text;
  - complete and still useful: every document of the stories and the corpus is there,
    and searching the redacted file finds the same accounts as searching the raw text
    (top 10, the nine corpus questions).

Needs DuckDB, data/ and the corpus. Run: python tests/search_export_test.py
"""
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import documents                                    # noqa: E402
import handover                                     # noqa: E402
import search                                       # noqa: E402
import search_export                                # noqa: E402
from cairnpath_db import connect                    # noqa: E402
from settings import OUTPUTS, SETTINGS             # noqa: E402

CONF = SETTINGS["search"]
failed = 0


def check(ok, what, detail=""):
    global failed
    print(f"  {'PASS' if ok else 'FAIL'}  {what}" + (f"  ->  {str(detail)[:300]}" if detail and not ok else ""))
    failed += not ok


print("\nUp to date")
committed = search_export.OUT.read_text(encoding="utf-8")
check(committed == search_export.build(), f"the committed {search_export.OUT.name} is what search_export.py builds today")
rows = [json.loads(line) for line in committed.splitlines()]

print("\nPrivate (checked apart from the redaction)")
folders = [documents.STORIES, OUTPUTS / CONF["corpus_folder"]]
secret = set()
for folder in folders:
    with (folder / SETTINGS["handover"]["contacts_file"]).open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            secret |= {r["name"], r["email"]}
    for r in documents.directory(folder).values():
        secret |= {r["company"], r["helpdesk_org"], r["email_domain"]}
secret.discard("")
blob = committed
found = sorted(s for s in secret if s.lower() in blob.lower())
check(not found, f"none of {len(secret)} names, companies, organisations, domains and emails appears", found[:5])
check(not handover.EMAIL_RE.findall(blob) and not handover.phones(blob), "no email address or phone number at all",
      (handover.EMAIL_RE.findall(blob)[:3], handover.phones(blob)[:3]))
check(all(set(r) == set(search_export.FIELDS) for r in rows), "only account_id, id, kind, date, title, text (no source)")

print("\nComplete and still useful")
raw = connect()
n_raw = search.add_views(raw)
check(len(rows) == n_raw, f"every document of the stories and the corpus ({n_raw})", len(rows))
red = connect()
search.add_views(red, rows=rows)
key = json.loads((OUTPUTS / CONF["corpus_folder"] / CONF["key_file"]).read_text(encoding="utf-8"))
diff = []
for q in key["questions"]:
    a = {x["account_id"] for x in search.accounts(raw, q["question"], 10)} & set(q["accounts"])
    b = {x["account_id"] for x in search.accounts(red, q["question"], 10)} & set(q["accounts"])
    if a != b:
        diff.append((q["id"], len(a), len(b)))
check(not diff, "the redacted file finds the same right accounts as the raw text", diff)

print(f"\n{'All passed' if not failed else f'{failed} failed'}")
sys.exit(1 if failed else 0)
