"""
Utilisation reads each day against the contract in force that day (cairnpath_db's
contract_terms, 24 Nov 2026).

The CRM holds only today's term. With two years of data, an account that expanded in
January looked, in every week before it, as if it used a fraction of a contract it hadn't
bought yet: history.py showed Yarrow Studio as Shelfware, then churn Watch, for all of 2025.

One made-up account: 300,000 events a year until its renewal 50 days ago, then 600,000
(ARR 25,000 -> 50,000), sending half its contract every day on both sides. Utilisation
must read about 50% before and after, never a quarter before, and no "fall" at the renewal.

Needs DuckDB, no AWS, no demo data. Run: python tests/contract_terms_test.py
"""
import csv
import json
import os
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


END = date(2026, 10, 18)
RENEWAL = END - timedelta(days=50)
DAYS = [END - timedelta(days=199 - i) for i in range(200)]

with tempfile.TemporaryDirectory() as tmp:
    data = Path(tmp) / "data"
    (data / "events").mkdir(parents=True)
    for d in DAYS:
        per_year = 300000 if d < RENEWAL else 600000
        rows = [{"event_id": f"v{d}", "event_name": "daily_event_volume", "event_type": "system",
                 "timestamp": f"{d}T12:00:00Z", "account_id": "acc_1", "user_id": None,
                 "properties": {"event_count": per_year // 2 // 365}, "context": {}},
                {"event_id": f"c{d}", "event_name": "chart_created", "event_type": "product",
                 "timestamp": f"{d}T10:00:00Z", "account_id": "acc_1", "user_id": "u1",
                 "properties": {}, "context": {}}]
        (data / "events" / f"{d}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    with (data / "crm_accounts.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["account_id", "account_name", "tier", "region", "contract_start", "renewal_date",
                    "contracted_events", "add_ons", "csm_owner", "cs_pool", "last_qbr_date", "next_qbr_date",
                    "arr", "status", "customer_since"])
        w.writerow(["acc_1", "Grew Co", "Scale", "UK&I", RENEWAL, RENEWAL + timedelta(days=365), 600000, "", "",
                    "pool_x", "2026-09-01", "2026-12-01", 50000, "active", DAYS[0]])
    with (data / "renewal_history.csv").open("w", newline="") as f:
        csv.writer(f).writerows([["account_id", "renewal_date", "outcome", "renewal_arr", "arr_before"],
                                 ["acc_1", RENEWAL, "expanded", 50000, 25000]])
    with (data / "cs_team.csv").open("w", newline="") as f:
        csv.writer(f).writerows([["owner_id", "owner_name", "owner_type", "region", "manager", "pool"],
                                 ["pool_x", "UK&I pool", "pool", "UK&I", "mgr_x", ""]])

    os.chdir(tmp)
    settings.INPUTS, settings.OUTPUTS = data, data
    settings.SETTINGS["data"].update({"events_glob": "events/*.jsonl", "crm_file": "crm_accounts.csv",
                                      "team_file": "cs_team.csv", "engagement_glob": "engagement/*.jsonl",
                                      "renewal_history_file": "renewal_history.csv"})
    from cairnpath_db import connect              # noqa: E402
    con = connect()

    print("\nThe terms")
    terms = con.sql("SELECT valid_to, contracted_events FROM contract_terms ORDER BY valid_from").fetchall()
    check([v for _, v in terms] == [300000, 600000], "two terms: 300,000 before the renewal, 600,000 after", terms)

    print("\nEvery day against the contract in force")
    util = dict(con.sql("SELECT event_date, util FROM daily_util_30d").fetchall())
    before = [u for d, u in util.items() if d < RENEWAL]
    after = [u for d, u in util.items() if d >= RENEWAL + timedelta(days=30)]
    check(before and all(45 <= u <= 55 for u in before), "about 50% before the renewal (not 25%)",
          (min(before or [0]), max(before or [0])))
    check(after and all(45 <= u <= 55 for u in after), "about 50% after it", (min(after or [0]), max(after or [0])))

    print("\nThe churn months: no fall at the renewal")
    m = con.sql("SELECT util_m1, util_m2, util_m3 FROM monthly_usage").fetchone()
    check(m is not None and all(45 <= u <= 55 for u in m), "months 1 to 3 all about 50% (month 1 was before it)", m)
    pct = con.sql("SELECT utilisation_pct FROM utilisation").fetchone()[0]
    check(45 <= pct <= 55, "today's utilisation about 50%", pct)

print(f"\n{'All passed' if not failed else f'{failed} failed'}")
sys.exit(1 if failed else 0)
