"""
Redaction small fixes test (PRIVACY.md D, E, F): no model, no key, no stories
needed; it runs the real redact() and restore() on made-up text.

    python tests/redaction_fixes_test.py

  D  a first name that's also a word ("Will", "May") stays a word where it
     plainly is one ("Will you...", "12 May"), and is still the person elsewhere
  E  national phone numbers (UK, French, German) are masked; dates, versions,
     amounts and short IDs are not
  F  "The customer" at the start of a sentence gets the company name back
Exit 1 on any FAIL.
"""

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import handover   # noqa: E402

failed = 0


def check(ok, name, detail=""):
    global failed
    failed += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ->  {detail}" if not ok and detail else ""))


ENTRY = {"account_id": "acc_test", "company": "Acme Foods", "helpdesk_org": "Acme Foods Ltd",
         "email_domain": "acme.example"}
handover.contacts = lambda account_id: [{"name": "Will Turner", "side": "customer"},
                                        {"name": "May Chen", "side": "customer"}]


def redact(text):
    docs = [{"id": "E1", "kind": "E", "title": "Notes", "date": "2026-10-01", "text": text}]
    sent, key = handover.redact(ENTRY, [], "", docs)
    return sent.split("\n", 1)[1] if "\n" in sent else sent, key


print("\nD: first names that are also everyday words")
text = ("Will Turner and May Chen joined the call on 12 May. Will you send the logs? "
        "May we meet in May 2026? Will said the budget is cut. Thanks, May")
sent, key = redact(text)
will = next(k for k, v in key.items() if v == "Will Turner")
may = next(k for k, v in key.items() if v == "May Chen")
check("Will you send the logs?" in sent and "May we meet" in sent,
      "a question starting 'Will you' or 'May we' keeps its word", sent)
check("on 12 May." in sent and "in May 2026" in sent, "a month next to a number stays a month", sent)
check(f"{will} said the budget" in sent and f"Thanks, {may}" in sent,
      "...and the same names are still the person everywhere else", sent)
check("Will Turner" not in sent and "May Chen" not in sent, "full names always replaced")

print("\nE: national phone numbers")
for number in ("020 7946 0958", "01632 960123", "06 12 34 56 78", "(0)89 1234 5678", "0612345678",
               "+49 89 5550 2231"):
    sent, _ = redact(f"Call me on {number}.")
    check(number not in sent and "[phone]" in sent, f"{number} masked", sent)
for keep in ("on 2026-10-05", "SDK 3.0.1", "EUR 0.45 per event", "ticket 0042", "PO 0123 4567"):
    sent, _ = redact(f"Noted: {keep}.")
    check(keep in sent and "[phone]" not in sent, f"'{keep}' left alone (not a phone number)", sent)
check(handover.leak_check("ring 020 7946 0958", {}) == ["020 7946 0958"],
      "the leak check knows national numbers too")

print("\nF: restore at the start of a sentence")
key = {"the customer": "Acme Foods", "Customer A": "Will Turner"}
out = handover.restore("The customer wants pricing. Customer A agreed; the customer pays.", key)
check(out == "Acme Foods wants pricing. Will Turner agreed; Acme Foods pays.",
      "'The customer' and 'the customer' both get the company name back", out)

print("\nG: a surname after a title (found by the search held-out set, 13 Nov 2026)")
sent, key = redact("Dear Mr Turner, thanks. Dr. Chen and Ms Turner agree. Mr Smith too. Turner Street is fine.")
check(f"Dear {will}, thanks." in sent and f"{may} and {will} agree" in sent,
      "Mr/Ms/Dr + a known surname becomes that person's label", sent)
check("Mr Smith" in sent, "a title before a name nobody knows stays (the leak check's job)", sent)
check("Turner Street" in sent, "a surname alone, without a title, is left alone (could be a word or a street)", sent)

print("\nThe number check reads numbers written as words (found 5 Nov 2026)")
check(not handover.check("Users rose over 13 weeks [DATA].", "Weekly active users: 26 thirteen weeks ago, 38 last week.",
                         {"DATA"})["unverified_numbers"],
      "'13 weeks' in the brief is backed by 'thirteen weeks' in a source")
check(handover.numbers_in("twenty-five users, Forty days, one hundred") >= {"25", "40"},
      "compound and capitalised number words count too")
check(handover.check("Usage fell 37% [DATA].", "Usage fell sharply.", {"DATA"})["unverified_numbers"] == ["37"],
      "a number in no source is still flagged")
check("someone" in handover.as_digits("someone often"), "words that only contain a number word stay as they are")

print("\nFAILED" if failed else "\nOK")
sys.exit(1 if failed else 0)
