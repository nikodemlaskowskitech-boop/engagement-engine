"""
Contact list test: with the CRM's contacts, is a person found anywhere in the
text, not only in headers? PRIVACY.md upgrade A. No model, no key: it checks
the redacted text (exactly what the model would see).

    python tests/contacts_test.py

Works on copies of the stories in a temporary folder. Needs
data/views_data.json (run .\\run_all.ps1 first).
  - Hannah Weiss, named only in an email body, becomes a label (and the leak
    check is clean), but only because she's on the contact list: without it,
    the leak check stops the brief
  - a contact named by first name only ("ask Hannah") gets the label too
  - two people with the same first name: a bare first name becomes [name],
    never the wrong person's label
  - no contact list at all: the briefs still work as before
Exit 1 on any FAIL.
"""

import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import handover   # noqa: E402

failed = 0
ACC = "acc_012"                                   # Nettle Foods: Hannah Weiss is only in an email body


def check(ok, name, detail=""):
    global failed
    failed += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ->  {detail}" if not ok and detail else ""))


def fresh():
    tmp = Path(tempfile.mkdtemp())
    shutil.copytree(REPO / "stories", tmp / "stories")
    handover.STORIES = tmp / "stories"
    return tmp


def redacted(acc=ACC):
    entry, account, team, facts, docs = handover.gather(acc)
    sent, key = handover.redact(entry, team, facts, docs)
    return sent, key, handover.leak_check(sent, key)


print("\nContact list: people found by name, wherever they appear")
tmp = fresh()
sent, key, leaks = redacted()
label = next((k for k, v in key.items() if v == "Hannah Weiss"), None)
check(label is not None and "Hannah" not in sent and f"I'll bring {label} from" in sent,
      f"Hannah Weiss (only in an email body) is on the contact list, so she becomes {label}", label)
check(leaks == [], "...and the leak check is clean: the brief can be written", leaks)

(tmp / "stories" / handover.CONF["contacts_file"]).unlink()
sent, key, leaks = redacted()
check("Hannah Weiss" in leaks, "without the contact list, the leak check stops that brief (C backs A up)", leaks)

tmp = fresh()
eml = next((tmp / "stories" / "emails").glob("*nettle-foods_pricing*"))
eml.write_text(eml.read_text(encoding="utf-8").rstrip() + "\n\nPS: ask Hannah for the PO number.\n", encoding="utf-8")
sent, key, leaks = redacted()
label = next(k for k, v in key.items() if v == "Hannah Weiss")
check(f"ask {label} for the PO" in sent and "Hannah" not in sent,
      "a contact named by first name only ('ask Hannah') gets her label too")

tmp = fresh()
with (tmp / "stories" / handover.CONF["contacts_file"]).open("a", encoding="utf-8") as f:
    f.write("acc_012,Hannah Brooks,hannah.brooks@nettlefoods.example,customer\n")
eml = next((tmp / "stories" / "emails").glob("*nettle-foods_pricing*"))
eml.write_text(eml.read_text(encoding="utf-8").rstrip() + "\n\nHannah Brooks from legal is copied too. Ask Hannah.\n",
               encoding="utf-8")
sent, key, leaks = redacted()
weiss = next(k for k, v in key.items() if v == "Hannah Weiss")
brooks = next(k for k, v in key.items() if v == "Hannah Brooks")
check(f"{brooks} from legal" in sent and f"bring {weiss} from" in sent,
      "two Hannahs: each full name gets its own label")
check("Ask [name]." in sent and "Hannah" not in sent,
      "...and a bare 'Hannah' becomes [name]: never the wrong person's label", sent[-200:])

for acc in handover.directory():
    tmp = fresh()
    (tmp / "stories" / handover.CONF["contacts_file"]).unlink()
    if acc == ACC:                               # Hannah needs the list; take her out to compare like for like
        eml = next((tmp / "stories" / "emails").glob("*nettle-foods_pricing*"))
        eml.write_text(eml.read_text(encoding="utf-8").replace("Hannah Weiss", "a colleague"), encoding="utf-8")
    sent, key, leaks = redacted(acc)
    check(leaks == [] and len(key) > 1, f"{acc}: no contact list at all still works, as before")

print("\nFAILED" if failed else "\nOK")
sys.exit(1 if failed else 0)
