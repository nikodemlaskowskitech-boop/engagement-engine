"""
research.py (20 Nov 2026): the account page's research, from the redacted documents only.
Same answer every time; every line shown comes from the redacted file (nothing else can
reach a page); counts by kind; at most [research] max_themes per account; one piece of
evidence supports one theme; and, against the generator's answer key (tests only, never the
engine), a floor on precision and recall so a change can't quietly make it noisy.

    python tests/research_test.py
"""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import research                     # noqa: E402
from settings import SETTINGS       # noqa: E402

failed = 0


def check(ok, name, detail=""):
    global failed
    failed += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ->  {detail}" if not ok and detail else ""))


export = json.loads((ROOT / "data" / "views_data.json").read_text(encoding="utf-8"))
docs = research.rows()
check(len(docs) > 0 and (ROOT / SETTINGS["search"]["site_documents_file"]).exists(), f"reads the redacted file the public search carries ({len(docs)} documents)")
r1, r2 = research.build(export["as_of"]), research.build(export["as_of"])
check(r1 == r2 and r1, "the same answer every time")
text = {(d["account_id"], d["id"]): d for d in docs}
def taken_from(line, doc):           # long lines are cut at 200 characters with "..." (search._best_line)
    return line == doc["title"] or line in doc["text"] or (line.endswith("...") and line[:-3] in doc["text"])
lines_ok = all(taken_from(t["line"], text[(a, t["id"])]) for a, r in r1.items() for t in r["themes"])
check(lines_ok, "every evidence line is taken word for word from that redacted document (nothing else reaches a page)")
check(all(len(r["themes"]) <= SETTINGS["research"]["max_themes"] for r in r1.values()), f"at most {SETTINGS['research']['max_themes']} themes per account")
check(all(len({t["id"] for t in r["themes"]}) == len(r["themes"]) for r in r1.values()), "one piece of evidence supports one theme only")
check(all(not t["line"].lower().startswith(("hi ", "hello", "dear")) for r in r1.values() for t in r["themes"]), "a greeting is never the evidence")
a0 = next(iter(r1)); mine = [d for d in docs if d["account_id"] == a0 and d["date"] >= "0"]
check(r1[a0]["tickets"] + r1[a0]["emails"] + r1[a0]["calls"] <= len(mine), f"{a0}: the counts are its documents in the window")
key_file = ROOT / "data" / SETTINGS["search"]["corpus_folder"] / SETTINGS["search"]["key_file"]
if key_file.exists():
    key = {q["id"]: set(q["accounts"]) for q in json.loads(key_file.read_text(encoding="utf-8"))["questions"]}
    shown = [(a, t["theme"]) for a, r in r1.items() for t in r["themes"]]
    right = sum(a in key[th] for a, th in shown)
    truth = sum(len(v & set(r1)) for v in key.values())
    check(shown and right / len(shown) >= 0.7, f"precision {right}/{len(shown)} ({right / max(1, len(shown)):.0%}) on the demo corpus, floor 70% (optimistic: chosen on this corpus)")
    check(right / max(1, truth) >= 0.6, f"recall {right}/{truth} ({right / max(1, truth):.0%}), floor 60%")
print("\nPromises our side made (22 Nov 2026)")
mail_ours = {"id": "E9", "kind": "E", "text": "From: CS 1 <[email]>\nTo: Customer A <[email]>\n\nThanks for today. I'll send the SSO guide by Friday. Talk soon."}
mail_theirs = {"id": "E8", "kind": "E", "text": "From: Customer A <[email]>\nTo: CS 1 <[email]>\n\nI'll send you our numbers next week."}
call = {"id": "C9", "kind": "C", "text": "[00:00:10] Customer A: We'll check internally.\n[00:00:20] CS 2: Good point. I'll set up a session with your admins."}
check(research.promises(mail_ours) == ["I'll send the SSO guide by Friday."], "an email we sent: the promise sentence, nothing else", research.promises(mail_ours))
check(research.promises(mail_theirs) == [], "the customer's email: their words, not our promise")
check(research.promises(call) == ["I'll set up a session with your admins."], "a call: our speaker's promise sentence only", research.promises(call))
check(research.promises({"id": "S1", "kind": "S", "text": "We'll fix it"}) == [], "a ticket: not a source of our promises")

print("\nFAILED" if failed else "\nAll passed")
sys.exit(1 if failed else 0)
