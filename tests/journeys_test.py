"""
journeys.py's "No meeting in 30 days": the day an Act signal started (24 Nov 2026).

With two years of data, "churn Act since ..." took the first day the account ever crossed the
line: Alder Media read "since 2025-02-16", a dip a year earlier. The rule now: the first day over
the line after the account last came back for at least [journeys] signal_return_days in a row,
so an old dip doesn't count and a few days' wobble at the line doesn't restart the clock.

Runs on the pipeline's own outputs (data/), like check_flags.py. Run: python tests/journeys_test.py
"""
import csv
import re
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import cairnpath_db                                 # noqa: E402
from settings import SETTINGS, output_file          # noqa: E402

failed = 0


def check(ok, what, detail=""):
    global failed
    print(f"  {'PASS' if ok else 'FAIL'}  {what}" + (f"  ->  {str(detail)[:300]}" if detail and not ok else ""))
    failed += not ok


RETURN = int(SETTINGS["journeys"]["signal_return_days"])
LINES = {"churn": lambda u: u < SETTINGS["churn"]["util_below"], "expansion": lambda u: u >= SETTINGS["expansion"]["high"]}
con = cairnpath_db.connect()
first_day = con.sql("SELECT min(event_date) FROM events").fetchone()[0]
rows = [r for r in csv.DictReader(output_file("flags_journey").open(encoding="utf-8"))
        if r["status"] == "No meeting in 30 days"]
print(f"\nNo meeting in 30 days: {len(rows)} accounts, data from {first_day}")
checked = 0
for r in rows:
    m = re.match(r"(churn|expansion) Act since (\d{4}-\d{2}-\d{2})", r["reasons"])
    if not m:
        continue                                    # "since unknown": Act from users or errors, not utilisation
    flag, since = m.group(1), date.fromisoformat(m.group(2))
    series = con.sql(f"""SELECT event_date, util FROM daily_util_30d WHERE account_id = '{r["account_id"]}'
                         ORDER BY event_date""").fetchall()
    over = {d: LINES[flag](u) for d, u in series}
    after = [d for d in over if d >= since]
    # 1. it's over the line on that day
    ok1 = over.get(since) is True
    # 2. since then it never came back for RETURN days in a row
    run = longest = 0
    for d in after:
        run = run + 1 if not over[d] else 0
        longest = max(longest, run)
    # 3. just before it, it was back for at least RETURN days in a row (or the data starts there)
    before = [d for d in over if d < since]
    back = 0
    for d in reversed(before):
        if over[d]:
            break
        back += 1
    ok3 = not before or back >= RETURN
    check(ok1 and longest < RETURN and ok3,
          f"{r['account_id']} {flag} Act since {since}: over the line then, no return of {RETURN}+ days since, "
          f"and {'the data starts there' if not before else f'{back} days back before it'}",
          (ok1, longest, back))
    checked += 1
check(checked > 0, f"at least one dated signal checked ({checked})")
alder = next((r for r in rows if r["account_id"] == "acc_042"), None)
if alder:
    m = re.search(r"since (\d{4}-\d{2}-\d{2})", alder["reasons"])
    check(m and date.fromisoformat(m.group(1)) >= date.fromisoformat(str(con.sql("SELECT max(event_date) FROM events").fetchone()[0])) - timedelta(days=90),
          "Alder Media (acc_042): this autumn's signal, not last year's dip", alder["reasons"])

print(f"\n{'All passed' if not failed else f'{failed} failed'}")
sys.exit(1 if failed else 0)
