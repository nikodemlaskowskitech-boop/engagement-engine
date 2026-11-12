"""
A year of data must read like 90 days did (12 Nov 2026).

Two windows in the engine had no start, so they quietly assumed the data was
exactly 90 days long: the weekly-users average (every week of data divided by
13) and "month 1" of the monthly usage (everything older than 60 days). On a
year of data, every account looked like it had lost most of its users, and the
"climbing" test for expansion broke. This builds a tiny, perfectly flat account
over 365 days in a temporary folder and checks that both read flat.

No AWS, no demo data needed. Run: python tests/long_history_test.py
"""
import csv
import json
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import settings                                   # noqa: E402

failed = 0


def check(ok, what, detail=""):
    global failed
    print(f"  {'PASS' if ok else 'FAIL'}  {what}" + (f"  ->  {detail}" if detail and not ok else ""))
    failed += not ok


with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    (tmp / "events").mkdir()
    end, users = date(2026, 10, 18), [f"u{i}" for i in range(10)]
    # one account, flat for a year: the same 10 users every day, the same volume every day
    for i in range(365):
        day = end - timedelta(days=364 - i)
        rows = [{"event_id": f"v{i}", "event_name": "daily_event_volume", "event_type": "system",
                 "timestamp": f"{day}T12:00:00Z", "account_id": "acc_1", "user_id": None,
                 "properties": {"event_count": 1000}, "context": {}}]
        rows += [{"event_id": f"c{i}_{u}", "event_name": "chart_created", "event_type": "product",
                  "timestamp": f"{day}T10:00:00Z", "account_id": "acc_1", "user_id": u,
                  "properties": {}, "context": {}} for u in users]
        (tmp / "events" / f"{day}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    with (tmp / "crm_accounts.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["account_id", "account_name", "tier", "region", "contract_start", "renewal_date",
                    "contracted_events", "add_ons", "csm_owner", "cs_pool", "pool_owner", "last_qbr_date",
                    "next_qbr_date", "arr", "status"])
        w.writerow(["acc_1", "Flat Co", "Enterprise", "UK&I", "2026-01-01", "2027-01-01", 365000, "",
                    "csm_x", "", "", "2026-08-01", "2026-11-01", 100000, "active"])
        w.writerow(["acc_2", "Gone Co", "Enterprise", "UK&I", "2025-03-01", "2026-03-01", 365000, "",
                    "csm_x", "", "", "2025-12-01", "2026-03-01", 100000, "churned"])
    with (tmp / "cs_team.csv").open("w", newline="") as f:
        csv.writer(f).writerows([["owner_id", "owner_name", "owner_type", "region", "manager", "pool"],
                                 ["csm_x", "X", "csm", "UK&I", "mgr_x", ""]])
    # point the engine at the temporary folder (inputs and outputs)
    settings.INPUTS, settings.OUTPUTS = tmp, tmp
    settings.SETTINGS["data"].update({"events_glob": "events/*.jsonl", "crm_file": "crm_accounts.csv",
                                      "team_file": "cs_team.csv", "engagement_glob": "engagement/*.jsonl"})
    import cairnpath_db                           # noqa: E402
    con = cairnpath_db.connect()

    print("\nA flat account over a year reads flat")
    m = con.sql("SELECT util_m1, util_m2, util_m3, users_m1, users_m3 FROM monthly_usage WHERE account_id = 'acc_1'").fetchone()
    check(m is not None and m[0] == m[1] == m[2], "utilisation: month 1 = month 2 = month 3 (month 1 has a start)", str(m))
    check(m is not None and m[3] == m[4] == 10, "active users: 10 in month 1 and in month 3", str(m))
    w = con.sql("SELECT wau_now, wau_avg FROM weekly_users WHERE account_id = 'acc_1'").fetchone()
    check(w is not None and w[0] == 10 and abs(w[1] - 10) < 0.01, "weekly users: 10 this week, 10 on average (not 28)", str(w))

    print("\nA churned account is history, not today's book")
    book = [r[0] for r in con.sql("SELECT account_id FROM crm ORDER BY 1").fetchall()]
    every = [r[0] for r in con.sql("SELECT account_id FROM crm_all ORDER BY 1").fetchall()]
    check(book == ["acc_1"], "crm (today's book) leaves out the churned account", str(book))
    check(every == ["acc_1", "acc_2"], "crm_all keeps it, so its old events aren't a handoff gap", str(every))

print(f"\n{'All passed' if not failed else f'{failed} failed'}")
sys.exit(1 if failed else 0)
