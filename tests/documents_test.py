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
    check(docs and all(set(x) == {"kind", "source", "date", "title", "text", "id", "case", "team", "logged_by"} for x in docs),
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

print("\nCases (23 Nov 2026): one problem across tickets, emails and calls, linked by certain keys")
by = {x["title"]: x for x in documents.for_account("acc_012")}
check(by["Email: Re: Overage invoice INV-30418"]["case"] == by["Email: Overage invoice INV-30418"]["case"] == "E1",
      "a 'Re:' reply joins its email's case, named by the oldest (E1)")
check(len({x["case"] for x in documents.for_account("acc_012")}) == 6, "...and nothing else merges on that account (7 documents, 6 cases)")
check(all(x["team"] == "Support" for x in documents.for_account("acc_012") if x["kind"] == "S"), "tickets come from Support")

import shutil, tempfile                                            # noqa: E401,E402
from email.message import EmailMessage                             # noqa: E402
tmp = Path(tempfile.mkdtemp()) / "stories"
shutil.copytree(documents.STORIES, tmp)
dom = d["acc_156"]["email_domain"]


def mail(name, subject, body, mid, day, **headers):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Message-ID"] = f"Ana <ana@{dom}>", "CS <cs@cairnpath.example>", subject, mid
    m["Date"] = f"{day} 10:00:00 +0000"
    for k, v in headers.items():
        m[k.replace("_", "-")] = v
    m.set_content(body)
    (tmp / documents.CONF["email_folder"] / name).write_bytes(bytes(m))


mail("a.eml", "Tracking gaps", "Events from the Android app stopped on Monday.", "<a@x>", "Wed, 01 Jul 2026")
mail("b.eml", "Tracking gaps - update", "Support says it's the SDK version. See ticket DL-118.", "<b@x>",
     "Thu, 02 Jul 2026", In_Reply_To="<a@x>", X_Logged_By="CS")
mail("b2.eml", "Tracking gaps - update", "Support says it's the SDK version. See ticket DL-118.", "<b@x>",
     "Thu, 02 Jul 2026", In_Reply_To="<a@x>", X_Logged_By="Implementation", X_Team="CS")
mail("c.eml", "Lunch next week?", "Shall we meet in town?", "<c@x>", "Fri, 03 Jul 2026")
call = (tmp / documents.CONF["calls_folder"] / "2026-07-06_x.txt")
call.write_text("Title: x - SDK fix\nDate: 2026-07-06 10:00\nCRM account: acc_156\n"
                "Participants: CS 9 (Cairnpath, Implementation), Ana (x, Lead)\nRelated case: DL-118\n\n"
                "[00:00:30] Ana: The new SDK fixed it.\n", encoding="utf-8")
docs = documents.for_account("acc_156", tmp)
t = {x["title"]: x for x in docs}
ticket = next(x for x in docs if "DL-118" in x["title"])
check(len([x for x in docs if x["title"] == "Email: Tracking gaps - update"]) == 1
      and t["Email: Tracking gaps - update"]["logged_by"] == ["CS", "Implementation"],
      "an email logged by the CSM and by Implementation: one email, both teams in logged_by")
check(t["Email: Tracking gaps - update"]["case"] == t["Email: Tracking gaps"]["case"] == ticket["case"]
      == t["Call: x - SDK fix"]["case"],
      "one case: the email, its reply (In-Reply-To), the ticket it quotes and the call logged against it")
check(t["Call: x - SDK fix"]["team"] == "Implementation", "a call with Implementation on it is Implementation's")
check(t["Email: Lunch next week?"]["case"] not in {ticket["case"]} and t["Email: Lunch next week?"]["case"] == t["Email: Lunch next week?"]["id"],
      "an email sharing no key stays its own case (same account, same week: never merged by guess)")
check(ticket["case"] == min((x for x in docs if x["case"] == ticket["case"]), key=lambda x: (x["date"], x["id"]))["id"],
      "a case is named by its oldest document's citation")

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
