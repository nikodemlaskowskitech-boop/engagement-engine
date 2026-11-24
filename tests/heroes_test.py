"""
The four hero accounts' two-year arcs (generate_events.HEROES, 24 Nov 2026) still hold.

A demo walks through these four; a change anywhere in the generator, the flags or the
history replay could quietly rewrite their story. This reads what a full run wrote
(run the pipeline first: .\\run_all.ps1, or generate_events.py, generate_documents.py and
every [pipeline] step) and checks each arc, chapter by chapter:
  Nettle Foods    signed Dec 2024, a calm first year, expanded at the Dec 2025 renewal, a
                  June 2026 wobble called within two weeks and gone, expansion Act today
  Yarrow Studio   a calm year and a half, expanded Jan 2026, churn Act since September with
                  a call the same week: caught early
  Driftwood Labs  Shelfware in every week of two years, renewed flat, renewal within a month
  Alder Media     signed Jan 2025, expanded Jan 2026; churn Act and no meeting since: the
                  missed signal, with its June export ticket and October competitor email

Run: python tests/heroes_test.py
"""
import csv
import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
failed = 0


def check(ok, what, detail=""):
    global failed
    print(f"  {'PASS' if ok else 'FAIL'}  {what}" + (f"  ->  {detail}" if detail and not ok else ""))
    failed += not ok


def read(name):
    with (DATA / name).open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


crm = {r["account_id"]: r for r in read("crm_accounts.csv")}
renewals = {(r["account_id"], r["renewal_date"]): r["outcome"] for r in read("renewal_history.csv")}
weeks = read("history_flags.csv")
today = {name: {r["account_id"]: r["status"] for r in read(f"flags_{name}.csv")}
         for name in ("churn", "expansion", "journey")}
meetings = []
for f in sorted((DATA / "engagement").glob("*.jsonl")):
    for line in f.read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        if e["event_name"] == "meeting_held":
            meetings.append((e["account_id"], e["timestamp"][:10], e["properties"].get("topic")))
as_of = max(r["week"] for r in weeks)


def flagged(acc, flag, statuses, start="", end="9999"):
    """The weeks (history.py) the account carried one of these statuses, in [start, end)."""
    return sorted(r["week"] for r in weeks if r["account_id"] == acc and r["flag"] == flag
                  and r["status"] in statuses and start <= r["week"] < end)


def met(acc, start, days, topics=None):
    """Meetings on the account in the days from start (topics: only these)."""
    end = str(date.fromisoformat(start) + timedelta(days=days))
    return [m for m in meetings if m[0] == acc and start <= m[1] <= end and (topics is None or m[2] in topics)]


CHURN = ("Watch", "Act", "Shelfware")

print("\nNettle Foods (acc_012): growth, with a wobble caught early")
a = "acc_012"
check(crm[a]["customer_since"] == "2024-12-15", "signed 15 Dec 2024", crm[a]["customer_since"])
check(flagged(a, "activation", ("Activated",), end="2025-03-01"), "activated in its first weeks (history, early 2025)")
check(not flagged(a, "churn", CHURN, end="2026-05-01"), "no churn flag from signing to May 2026 (a calm first year)",
      flagged(a, "churn", CHURN, end="2026-05-01")[:5])
check(renewals.get((a, "2025-12-15")) == "expanded", "expanded at its first renewal (Dec 2025)", renewals.get((a, "2025-12-15")))
wobble = flagged(a, "churn", ("Act",), "2026-05-01", "2026-08-01")
check(bool(wobble), "a June 2026 wobble reached churn Act", wobble)
check(bool(wobble) and met(a, wobble[0], 14, ("churn_call",)), "the CSM called within two weeks of it",
      wobble and met(a, wobble[0], 30))
check(a not in today["churn"], "no churn flag today: it recovered", today["churn"].get(a))
check(today["expansion"].get(a) == "Act", "expansion Act today: heading past its contract", today["expansion"].get(a))

print("\nYarrow Studio (acc_024): churn, caught early")
a = "acc_024"
calm = flagged(a, "churn", CHURN, end="2026-07-01")
check(len(calm) <= 2, "no lasting churn flag until summer 2026 (a calm year and a half; 2 noisy weeks at most)", calm)
check(renewals.get((a, "2026-01-31")) == "expanded", "expanded in Jan 2026", renewals.get((a, "2026-01-31")))
act = flagged(a, "churn", ("Act",), "2026-07-01")
check(bool(act) and act[0] >= "2026-08-15", "churn Act since late summer 2026", act[:1])
check(bool(act) and met(a, str(date.fromisoformat(act[0]) - timedelta(days=7)), 14, ("churn_call",)),
      "a churn call the same week: caught early", act and met(a, act[0], 30))
check(today["churn"].get(a) == "Act" and crm[a]["renewal_date"] == "2027-01-31", "Act today, renewal 31 Jan 2027",
      (today["churn"].get(a), crm[a]["renewal_date"]))

print("\nDriftwood Labs (acc_156): shelfware, renewed twice without anyone asking")
a = "acc_156"
all_weeks = sorted({r["week"] for r in weeks})
shelf = flagged(a, "churn", ("Shelfware",))
check(len(shelf) == len(all_weeks), f"Shelfware in every week of history ({len(shelf)} of {len(all_weeks)})")
check(renewals.get((a, "2025-11-13")) == "renewed", "renewed flat in Nov 2025", renewals.get((a, "2025-11-13")))
left = (date.fromisoformat(crm[a]["renewal_date"]) - date.fromisoformat(as_of)).days
check(0 < left <= 31, f"renews within a month of today ({left} days): time to right-size", crm[a]["renewal_date"])

print("\nAlder Media (acc_042): the signal nobody saw")
a = "acc_042"
check(crm[a]["customer_since"] == "2025-01-16", "signed 16 Jan 2025", crm[a]["customer_since"])
check(flagged(a, "activation", ("Activated",), end="2025-04-01"), "activated in its first weeks")
check(renewals.get((a, "2026-01-16")) == "expanded", "expanded at its first renewal (Jan 2026)", renewals.get((a, "2026-01-16")))
check(not flagged(a, "churn", ("Act",), end="2026-07-01"), "no churn Act before summer 2026",
      flagged(a, "churn", ("Act",), end="2026-07-01"))
act = flagged(a, "churn", ("Act",), "2026-07-01")
check(today["churn"].get(a) == "Act" and bool(act), "churn Act today", today["churn"].get(a))
check(bool(act) and not met(a, act[0], 400), "no meeting since the flag went up", act and met(a, act[0], 400))
check(today["journey"].get(a) == "No meeting in 30 days", "so the manager sees No meeting in 30 days",
      today["journey"].get(a))
corpus = DATA / "corpus"
tickets = [r for r in csv.DictReader((corpus / "support_cases.csv").open(encoding="utf-8"))
           if r["organization"].startswith("Alder Media")]
check(any(r["created_at"].startswith("2026-06") and "export" in r["subject"].lower() for r in tickets),
      "a June 2026 ticket asks for a full export (the RFP Support saw and CS didn't)",
      [(r["created_at"], r["subject"]) for r in tickets])
emails = sorted(p.name for p in (corpus / "emails").glob("*alder-media*"))
check(any(n.startswith("2026-10") and "competitor" in n for n in emails), "an October 2026 email about a competitor",
      emails)

print(f"\n{'All passed' if not failed else f'{failed} failed'}")
sys.exit(1 if failed else 0)
