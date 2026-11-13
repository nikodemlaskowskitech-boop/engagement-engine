"""
The public search's documents (Phase 9, 13 Nov 2026): every demo document (the three
stories and the search corpus), redacted on the laptop, in one file the public
search function carries with it.

Why a file built here and not a search over the data in AWS: the search on the demo
site is a public address, so it should never touch anything but text that already
passed the redaction and the leak check. The raw documents, the contact lists and the
redaction keys stay on the laptop; the function in AWS gets this file and nothing
else (no access to the data bucket at all).

Each line: account_id, id (the citation), kind, date, title, text, all redacted
through search.redacted() (the handover brief's redaction, then the leak check). No
source field: file names and email IDs carry the company's name. If the leak check
holds back any account, nothing is written and the script exits 1.

The demo data is seeded, so the file is the same every time; it's committed next to
the function's code ([search] site_documents_file) and tests/search_export_test.py
checks the committed file is what this script makes today.

    python search_export.py            # (re)write the file
    python search_export.py --check    # exit 1 if the committed file is out of date
"""
import json
import sys

import search
from cairnpath_db import connect
from settings import REPO, SETTINGS

OUT = REPO / SETTINGS["search"]["site_documents_file"]
FIELDS = ("account_id", "id", "kind", "date", "title", "text")


def build(con=None):
    """The redacted documents as text (one JSON line each, in a stable order), or exit 1
    if the leak check holds anything back."""
    con = con or connect()
    search.add_views(con)
    every = [{"account_id": a, "id": i, "kind": k, "date": str(d), "title": t, "line": ""}
             for a, i, k, d, t in con.sql(
                 "SELECT account_id, id, kind, date, title FROM documents ORDER BY account_id, id").fetchall()]
    safe, held = search.redacted(con, every)
    if held:
        for b in held:
            print(f"Held back {b['account_id']}: {', '.join(b['found'])}")
        sys.exit("The leak check held back documents: nothing written. Fix the redaction or the allow list.")
    return "".join(json.dumps({f: d[f] for f in FIELDS}, ensure_ascii=False) + "\n" for d in safe)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    text = build()
    if "--check" in sys.argv:
        same = OUT.exists() and OUT.read_text(encoding="utf-8") == text
        print(f"{OUT.relative_to(REPO)}: {'up to date' if same else 'OUT OF DATE: run python search_export.py'}")
        sys.exit(0 if same else 1)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(f"{text.count(chr(10))} redacted documents -> {OUT.relative_to(REPO)}")
