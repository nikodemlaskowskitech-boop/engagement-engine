"""
Handover brief test: privacy first, then quality.

    python handover.py --dry-run      (or without --dry-run, to get the briefs)
    python tests/handover_test.py

1. Privacy (always): what would be sent to the model (data/handover/*_sent.txt)
   contains no real name (full or first), no email address, no phone number
   and no company name. Runs without a model or an API key.
2. Quality (once a brief exists, data/handover/<account>.md): against
   stories/answer_key.json, how many planted facts it found, whether it
   claimed anything it must not, and the brief's own checks (citations to
   sources that don't exist, numbers found in no source).

Plain Python. Exit 1 if any privacy check fails, a brief is missing after a
run (cut short or empty), finds under half the planted facts, or claims
something it must not.
"""

import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "data" / "handover"
KEY = json.loads((REPO / "stories" / "answer_key.json").read_text(encoding="utf-8"))

failed = 0
print("\n1. Privacy: what the model would see")
for account in [k for k in KEY if not k.startswith("_")]:
    sent_path, key_path = OUT / f"{account}_sent.txt", OUT / f"{account}_key.json"
    if not sent_path.exists():
        print(f"  SKIP  {account}: run python handover.py --dry-run first"); continue
    sent, key = sent_path.read_text(encoding="utf-8"), json.loads(key_path.read_text(encoding="utf-8"))
    leaks = []
    for label, real in key.items():
        words = [real] + ([real.split()[0]] if label != "the customer" else [])
        leaks += [w for w in words if re.search(rf"\b{re.escape(w)}\b", sent)]
    leaks += re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", sent)
    leaks += re.findall(r"\+\d[\d ()-]{7,}\d", sent)
    ok = not leaks
    failed += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {account}: {len(key) - 1} people and the company replaced"
          + (f"; LEAKED: {', '.join(sorted(set(leaks)))}" if leaks else "; no name, email or phone left"))

print("\n2. Quality: the brief against the answer key")
for account, story in KEY.items():
    if account.startswith("_"):
        continue
    brief_path, check_path = OUT / f"{account}.md", OUT / f"{account}_check.json"
    checks = json.loads(check_path.read_text(encoding="utf-8")) if check_path.exists() else {}
    if "incomplete" in checks:                       # the model ran but no brief was saved
        failed += 1
        print(f"  FAIL  {account}: no brief, {checks['incomplete']}"); continue
    if not brief_path.exists():
        print(f"  SKIP  {account}: no brief yet (python handover.py {account})"); continue
    if checks.get("stop_reason") != "end_turn":      # only a finished answer counts (older runs didn't record it)
        failed += 1
        print(f"  FAIL  {account}: not known to be a finished answer (stopped: {checks.get('stop_reason', 'not recorded')}); re-run handover.py"); continue
    body = brief_path.read_text(encoding="utf-8").split("\n---\n")[0].lower()   # the brief, not its source list
    if len(body.strip()) < 200:                      # empty or nearly: "fully cited" means nothing then
        failed += 1
        print(f"  FAIL  {account}: the brief is empty or almost ({len(body.strip())} characters)"); continue
    found = [f for f in story["must_find"] if all(any(w in body for w in group) for group in f["all"])]
    missed = [f["id"] for f in story["must_find"] if f not in found]
    wrong = [m["id"] for m in story["must_not"] if any(w in body for w in m["any"])]
    too_few = len(found) < len(story["must_find"]) / 2      # a brief that misses most of the story failed its job
    failed += bool(wrong) or too_few
    print(f"  {'PASS' if not (wrong or too_few) else 'FAIL'}  {account}: found {len(found)} of {len(story['must_find'])} planted facts"
          + (f" (missed: {', '.join(missed)})" if missed else "")
          + f"; claimed {len(wrong)} thing(s) it must not" + (f" ({', '.join(wrong)})" if wrong else "")
          + f"; {len(checks.get('unknown_citations', []))} unknown citations, {len(checks.get('unverified_numbers', []))} unverified numbers")

print("\nFAILED" if failed else "\nOK")
sys.exit(1 if failed else 0)
