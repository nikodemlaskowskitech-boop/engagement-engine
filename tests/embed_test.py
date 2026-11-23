"""
Search by meaning, the plumbing (22 Nov 2026): embed.py and search.py's meaning() and
ranked(), with the stand-in embedder (no AWS, no cost). The stand-in's vectors measure
nothing about meaning: tests/search_test.py measures the real model on the laptop.

  - one vector per redacted document, the right size, length 1, and no text in the file;
  - a second run embeds nothing (reused), a changed document is embedded again, a removed
    one is dropped, and another model means everything is embedded again;
  - only the committed redacted file is ever sent to the embedder (PRIVACY.md §13);
  - every call checks the budget first: a spent budget means no call at all;
  - meaning() and hybrid return real documents, the same answer twice, and hybrid keeps
    the keywords' best document near the top; keywords mode is exactly search().

    python tests/embed_test.py
"""
import json
import math
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import embed                                        # noqa: E402
import llm                                          # noqa: E402
import search                                       # noqa: E402
import search_export                                # noqa: E402
from cairnpath_db import connect                    # noqa: E402

failed = 0


def check(ok, what, detail=""):
    global failed
    print(f"  {'PASS' if ok else 'FAIL'}  {what}" + (f"  ->  {detail}" if detail and not ok else ""))
    failed += not ok


tmp = Path(tempfile.mkdtemp()) / "vectors.jsonl"
docs = embed.documents()
json_docs = [json.loads(x) for x in search_export.OUT.read_text(encoding="utf-8").splitlines() if x.strip()]
dims = int(embed.CONF["dimensions"])

print("\nThe vectors file")
sent = []
real_standin = embed.StandIn.__call__
embed.StandIn.__call__ = lambda self, text: (sent.append(text), real_standin(self, text))[1]
done, reused = embed.build("stand-in", tmp)
rows = list(embed.load(tmp).values())
check(done == len(docs) and reused == 0 and len(rows) == len(docs), f"one vector per redacted document ({len(docs)})", (done, reused, len(rows)))
check(all(len(r["vector"]) == dims and abs(math.sqrt(sum(x * x for x in r["vector"])) - 1) < 1e-3 for r in rows),
      f"each vector has {dims} numbers and length 1")
check(all(set(r) == {"account_id", "id", "model", "dims", "hash", "vector"} for r in rows),
      "the file holds IDs, model, size, hash and numbers: no title, no text")
allowed = {embed.text_of(d) for d in json_docs}
check(sent and all(t in allowed for t in sent), "only the committed redacted file's text reached the embedder")

print("\nOnly what changed is embedded again")
sent.clear()
check(embed.build("stand-in", tmp) == (0, len(docs)) and not sent, "a second run embeds nothing")
changed = [dict(d) for d in docs]
changed[0]["text"] += " One more line."
check(embed.build("stand-in", tmp, changed) == (1, len(docs) - 1), "a changed document is embedded again, alone")
check(embed.build("stand-in", tmp, changed[1:]) == (0, len(docs) - 1) and len(embed.load(tmp)) == len(docs) - 1,
      "a removed document is dropped from the file")
lines = [json.loads(x) for x in tmp.read_text(encoding="utf-8").splitlines()]
lines[0]["model"] = "another-model"
tmp.write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
check(embed.build("stand-in", tmp, changed[1:])[0] == 1, "a vector from another model is made again")
embed.StandIn.__call__ = real_standin

print("\nThe budget comes first")
saved = dict(llm.BUDGETS)
llm.BUDGETS["embed"] = 0.0
try:
    embed.embed_query("anything", "stand-in")
    check(False, "a spent budget refuses the call")
except llm.OverBudget:
    check(True, "a spent budget refuses the call")
llm.BUDGETS.pop("embed")
try:
    embed.embed_query("anything", "stand-in")
    check(False, "no budget for embed: refused")
except llm.OverBudget:
    check(True, "no budget for embed: refused")
llm.BUDGETS.clear(); llm.BUDGETS.update(saved)

print("\nSearch by meaning and hybrid (stand-in vectors: the plumbing, not the quality)")
embed.build("stand-in", tmp)
search.VECTORS_FILE = tmp
con = connect()
search.add_views(con)
q = "Which customers need more capacity?"
m = search.meaning(con, q, 10, "stand-in")
ids = {(a, i) for a, i in con.sql("SELECT account_id, id FROM documents").fetchall()}
check(len(m) == 10 and all((h["account_id"], h["id"]) in ids for h in m), "meaning() returns 10 real documents")
check(m == search.meaning(con, q, 10, "stand-in"), "the same question, the same answer")
kw = search.search(con, q, 10)
hy = search.ranked(con, q, 10, "hybrid", provider="stand-in")
check(len(hy) == 10 and len({(h["account_id"], h["id"]) for h in hy}) == 10, "hybrid: 10 different documents")
check((kw[0]["account_id"], kw[0]["id"]) in {(h["account_id"], h["id"]) for h in hy[:3]}, "hybrid keeps the keywords' best document in its top 3")
check(search.ranked(con, q, 10, "keywords") == kw, "keywords mode is exactly search(): nothing moved for it")
acc = search.accounts(con, q, 10, mode="hybrid", provider="stand-in")
check(len(acc) == len({a["account_id"] for a in acc}) and len(acc) <= 10, "accounts(): each account once, at most 10")
# the floor (23 Nov 2026): meaning always has a nearest document, so nonsense found five accounts
odd = "zxqv plorbt wibble"                         # no word any document shares: stand-in similarity 0
check(len(search.meaning(con, odd, 5, "stand-in", floor=-1)) == 5, "without a floor, nonsense still 'finds' 5 documents")
bar = search.meaning(con, odd, 1, "stand-in", floor=-1)[0]["score"] + 0.001   # just above nonsense's best
check(search.meaning(con, odd, 5, "stand-in", floor=bar) == [], "with a floor above nonsense's best, nonsense finds nothing by meaning")
# (whether right answers stay above it is measured with the real model: search_test's calibration)
check(search.ranked(con, odd, 5, "hybrid", provider="stand-in") == [] or float(search.EMBED.get("min_similarity", 0)) <= 0,
      "...and hybrid answers nothing (the page says no match), once [embed] min_similarity is set")
floored = search.meaning(con, q, 10, "stand-in", floor=0.2)
check(all(h["score"] >= 0.2 for h in floored) and len(floored) <= 10, "every document kept is at or above the floor")
lines = [h["line"] for h in search.meaning(con, q, 30, "stand-in", floor=-1)]
check(lines and not any(search._NOISE.match(x) for x in lines),
      "a match by meaning shows a line that says something: no header, greeting, sign-off or quote",
      [x for x in lines if search._NOISE.match(x)][:3])
check(search._best_line("Title: x\nHi CS 1,\n\nOur finance team is unhappy.\nCS 2", []) == "Our finance team is unhappy.",
      "no word in common: the first real sentence, not 'Hi CS 1,'")
one = search.meaning(con, q, 5, "stand-in", account_id=kw[0]["account_id"])
check(one and all(h["account_id"] == kw[0]["account_id"] for h in one), "meaning() for one account: only that account's documents")
check(all(h["account_id"] == kw[0]["account_id"] for h in search.ranked(con, q, 5, "hybrid", provider="stand-in", account_id=kw[0]["account_id"])),
      "hybrid for one account: only that account's documents")
search.VECTORS_FILE = Path(tempfile.mkdtemp()) / "none.jsonl"
fresh = connect(); search.add_views(fresh)
import contextlib, io                              # noqa: E401,E402
err = io.StringIO()
with contextlib.redirect_stderr(err):
    fell = search.ranked(fresh, q, 10, "hybrid", provider="stand-in")
check(fell == search.search(fresh, q, 10) and "keywords only" in err.getvalue() and search._fell_back,
      "no vectors: hybrid says so once and answers with the keywords (a search always answers)", err.getvalue())
try:
    search.meaning(fresh, q, 5, "stand-in")
    check(False, "no vectors file: a clear error")
except FileNotFoundError as e:
    check("python embed.py" in str(e), "no vectors file: a clear error that says what to run")
search.VECTORS_FILE = None

print(f"\n{'All passed' if not failed else f'{failed} FAILED'}")
sys.exit(1 if failed else 0)
