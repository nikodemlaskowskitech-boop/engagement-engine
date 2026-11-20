"""
The account page's research (20 Nov 2026): what the documents say about each account, from
the redacted documents only (the file the public search carries, search.site_documents_file),
so the same code runs on the laptop and in AWS and nothing unredacted reaches a page.

Per account: how many tickets (open ones apart), emails and call notes in the last
[research] days, and up to [research] max_themes possible themes. A theme counts when the
public search would return the account for that theme's question ([research.themes]); each
comes with the line that matched and its source (S1, E2, C1), so a person judges it.
Keyword search, not a verdict: the page says so.

    from research import build
    build(as_of) -> {account_id: {"tickets", "open_tickets", "emails", "calls", "themes": [...]}}
"""

import json
import re
from datetime import date, timedelta

import duckdb

import search
from settings import REPO, SETTINGS

CONF = SETTINGS["research"]
TICKET = re.compile(r"^Ticket \S+ \((P\d), ([\w-]+)\)")      # "Ticket ME-5002 (P2, solved): ..."
CLOSED = {"solved", "closed"}


def rows():
    """The redacted documents, as the public search has them."""
    path = REPO / SETTINGS["search"]["site_documents_file"]
    if not path.exists():
        path = REPO / "site_documents.jsonl"                  # in the engine Lambda (infra/bundle.py)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def evidence(hit):
    """The line to show: the one that matched, unless it's only a greeting ("Hi CS 1,"),
    then the document's title."""
    line = (hit.get("line") or "").strip()
    if len(line) < 25 or re.match(r"^(hi|hello|dear|thanks|best)\b", line, re.I):
        return hit["title"]
    return line


def build(as_of, docs=None):
    docs = rows() if docs is None else docs
    if not docs:
        return {}
    since = (date.fromisoformat(str(as_of)[:10]) - timedelta(days=int(CONF["days"]))).isoformat()
    out = {}
    for d in docs:                                            # the counts, in the window
        if d["date"] < since:
            continue
        r = out.setdefault(d["account_id"], {"tickets": 0, "open_tickets": 0, "emails": 0, "calls": 0, "themes": []})
        if d["kind"] == "S":
            r["tickets"] += 1
            m = TICKET.match(d["title"])
            r["open_tickets"] += bool(m and m.group(2).lower() not in CLOSED)
        elif d["kind"] == "E":
            r["emails"] += 1
        elif d["kind"] == "C":
            r["calls"] += 1
    con = duckdb.connect()
    search.add_views(con, rows=docs)
    found = {}                                                # (account, document) -> its strongest theme
    for theme, (label, question) in CONF["themes"].items():   # in settings order: no ties
        top = None
        for hit in search.accounts(con, question):            # what the public search would return
            acc = hit["account_id"]
            best = search.search(con, question, k=1, account_id=acc)
            if not best or best[0]["date"] < since or acc not in out:
                continue
            b = best[0]
            top = top or b["score"]                           # the best account's score for this question
            if b["score"] < float(CONF["min_relative_score"]) * top:
                continue                                      # a weak match: not shown
            t = {"theme": theme, "label": label, "id": b["id"], "kind": b["kind"], "date": b["date"],
                 "title": b["title"], "line": evidence(b), "score": b["score"]}
            k = (acc, b["id"])                                # one piece of evidence, one theme
            if k not in found or t["score"] > found[k]["score"]:
                found[k] = t
    for (acc, _), t in sorted(found.items()):
        out[acc]["themes"].append(t)
    for r in out.values():                                    # the strongest few, then newest first
        r["themes"] = sorted(sorted(r["themes"], key=lambda t: (-t["score"], t["theme"]))[:int(CONF["max_themes"])],
                             key=lambda t: (t["date"], t["theme"]), reverse=True)
        for t in r["themes"]:
            t.pop("score")
    return dict(sorted(out.items()))


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    res = build(SETTINGS.get("as_of") or date.today().isoformat())
    for acc, r in list(res.items())[:5]:
        print(acc, r["tickets"], r["open_tickets"], r["emails"], r["calls"], [t["label"] for t in r["themes"]])
