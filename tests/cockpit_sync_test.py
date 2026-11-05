"""
cockpit_sync.py test: is every decision from the cockpit checked again on our
side, and does a bad one get skipped instead of stopping the run?

    python tests/cockpit_sync_test.py

No DuckDB needed: the checks run on a small made-up CRM and CS team. The
cockpit's forms check what people type, but anyone with edit access can write
to the page's database directly, so nothing the page sends is trusted.
Exit 1 on any FAIL.
"""

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cockpit_sync as cs   # noqa: E402

failed = 0


def check(ok, name, detail=""):
    global failed
    failed += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ->  {detail}" if not ok and detail else ""))


ACCOUNTS = {"acc_024": {"arr": 234400.0, "owners": {"csm_mia"}},                 # Enterprise: Mia
            "acc_100": {"arr": 50000.0, "owners": {"pool_dach", "mem_priya"}},   # Scale: DACH pool, Priya
            "acc_300": {"arr": 5000.0, "owners": set()}}                         # Self-service: nobody
TEAM = {"csm_mia": "mgr_sophie", "csm_tom": "mgr_james", "pool_dach": "mgr_sophie",
        "mem_priya": "mgr_sophie", "mgr_sophie": "", "mgr_james": ""}
AS_OF, RUN = "2026-10-18", "2026-10-19"
AT = "2026-10-30T13:49:35.179Z"

# the three real decisions from the laptop's cockpit (as saved by the page)
REAL = [
    {"kind": "forecast", "account_id": "acc_024", "amount": 100000, "as_of": AS_OF, "by": "csm_mia", "call": "downsize",
     "data_at_risk": 234400, "decided_at": AT, "reason": "Client quote a lower utilization than forecasted", "role": "owner"},
    {"kind": "next_step", "account_id": "acc_024", "as_of": AS_OF, "by": "csm_mia", "decided_at": "2026-10-30T14:20:44.461Z",
     "due": "2026-10-31", "note": "Covering the latest utilisation numbers", "role": "owner", "type": "call"},
    {"kind": "assign", "account_id": "acc_100", "flag": "churn", "status": "Act", "from_owner": "pool_dach",
     "to_owner": "mem_priya", "manager": "mgr_sophie", "as_of": AS_OF, "decided_at": AT},
]


def run(decisions):
    return cs.build(decisions, AS_OF, RUN, ACCOUNTS, TEAM)


print("\ncockpit_sync.py: real decisions still go through")
events, skipped, stale = run(REAL)
check(len(events) == 3 and not skipped, "the page's own decisions all pass", skipped)
check({e["event_name"] for e in events} == {"forecast_set", "next_step_set", "task_assigned"},
      "and become the same events as before")

print("\nNever trust the page: each of these is skipped, with the reason")
bad = {
    "an account that doesn't exist": {**REAL[0], "account_id": "acc_999"},
    "a forecast that isn't renew, downsize or churn": {**REAL[0], "call": "maybe"},
    "an amount written as text": {**REAL[0], "amount": "lots"},
    "a downsize bigger than the ARR": {**REAL[0], "amount": 999999999},
    "a negative downsize": {**REAL[0], "amount": -5},
    "an amount on a 'renew'": {**REAL[0], "call": "renew", "amount": 1},
    "a forecast with no reason": {**REAL[0], "reason": "   "},
    "a forecast as an owner on someone else's account": {**REAL[0], "by": "csm_tom"},
    "a forecast as the wrong manager": {**REAL[0], "by": "mgr_james", "role": "manager"},
    "a role the page never sends": {**REAL[0], "role": "admin"},
    "a forecast on a Self-service account (no owner)": {**REAL[0], "account_id": "acc_300", "amount": 100},
    "a next step type the page doesn't have": {**REAL[1], "type": "dinner"},
    "a due date that isn't a date": {**REAL[1], "due": "next Tuesday"},
    "an impossible date": {**REAL[1], "due": "2026-02-30"},
    "a reassignment to someone not in the team": {**REAL[2], "to_owner": "evil_bob"},
    "a reassignment by someone who isn't a manager": {**REAL[2], "manager": "csm_tom"},
    "a missing decided_at": {k: v for k, v in REAL[1].items() if k != "decided_at"},
    "an unknown kind": {**REAL[0], "kind": "delete_everything"},
    "not even a decision": "DROP TABLE accounts;",
}
for name, d in bad.items():
    try:
        events, skipped, _ = run([d])
        check(not events and len(skipped) == 1, f"skipped: {name}", (events, skipped))
    except Exception as e:                       # the old script crashed here, stopping run_all
        check(False, f"skipped: {name}", f"crashed: {e!r}")

events, skipped, _ = run([REAL[0], bad["an amount written as text"], REAL[1]])
check(len(events) == 2 and len(skipped) == 1, "one bad decision doesn't take the good ones with it")
check(run({"not": "a list"})[1] == [("the file", "isn't a list of decisions")], "a file that isn't a list: said, no crash")

print("\nFree text is cleaned, never trusted")
long_note = {**REAL[1], "note": "x" * 5000 + "\x00\x07<script>alert(1)</script>"}
note = run([long_note])[0][0]["properties"]["note"]
check(len(note) == cs.MAX_TEXT and "\x00" not in note and "\x07" not in note,
      f"notes cut to {cs.MAX_TEXT} characters ([cockpit] max_text), control characters removed")
nul = run([{**REAL[0], "reason": "Budget cut\x00 confirmed"}])[0][0]["properties"]["note"]
check(nul == "Budget cut confirmed", "a control character inside the text is dropped", nul)
html = run([{**REAL[1], "note": "<img src=x onerror=alert(1)>"}])[0][0]["properties"]["note"]
check(html == "<img src=x onerror=alert(1)>",
      "markup is kept as plain text (the page escapes it when it shows it: stored as typed, shown safely)")

print("\nThe rest behaves as before")
events, _, stale = run([{**REAL[2], "as_of": "2026-09-01"}])
check(not events and stale == 1, "a reassignment on older data is skipped as stale, not as invalid")
check(run([{"kind": "handover_confirm", "account_id": "acc_012"}]) == ([], [], 0),
      "a handover confirmation (the page's own) is neither applied nor flagged")
twice = json.dumps(run(REAL)[0]) == json.dumps(run(REAL)[0])
check(twice, "the same decisions always give the same events (a re-run doubles nothing)")

print("\nFAILED" if failed else "\nOK")
sys.exit(1 if failed else 0)
