"""
Gaps in an account's own data (flags.py data_gaps, 24 Nov 2026): the engine must never guess.

Six made-up accounts over a year, in a temporary folder, each one a way bad data
broke the engine or would have fooled it, found when the generator planted it:
  acc_1  a normal Scale account: no gap, and nothing changes for it.
  acc_2  contracted_events left empty: "No contract volume", no usage flag, and no
         null utilisation for the page to crash on (export_views did).
  acc_3  renewal_date typed 26/06/2027: one such cell made the whole column text, and
         every renewal sum after it failed. Now that row alone is empty, "Renewal date
         unreadable", and the other renewals still read.
  acc_4  cs_pool left empty, using a fraction of its contract (Shelfware): its tasks
         went to nobody (routed_to empty). Now to CS ops, and "No pool".
  acc_5  the volume feed stopped 10 days ago while the product kept going: "Volume
         feed stopped" for data engineering, and no usage flag from the quiet days.
  acc_6  contract empty and its users fell from 10 to 2: "Risk hidden" to its pool,
         since the fall can be seen without the contract.
Plus check_inputs.py: warnings, never a stop, for all of them.

Needs DuckDB, no AWS, no demo data. Run: python tests/data_gaps_test.py
"""
import contextlib
import csv
import io
import json
import os
import subprocess
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
DAYS = [END - timedelta(days=364 - i) for i in range(365)]


def events(acc, volume, users, volume_until=END):
    """A year of one account: daily volume (until volume_until) and users(day) people charting."""
    rows = [{"event_id": f"{acc}_sdk", "event_name": "sdk_installed", "event_type": "product",
             "timestamp": f"{DAYS[0]}T09:00:00Z", "account_id": acc, "user_id": f"{acc}_u0",
             "properties": {}, "context": {}}]
    for d in DAYS:
        if d <= volume_until:
            rows.append({"event_id": f"{acc}_v{d}", "event_name": "daily_event_volume", "event_type": "system",
                         "timestamp": f"{d}T12:00:00Z", "account_id": acc, "user_id": None,
                         "properties": {"event_count": volume}, "context": {}})
        rows += [{"event_id": f"{acc}_c{d}_{u}", "event_name": "chart_created", "event_type": "product",
                  "timestamp": f"{d}T10:00:00Z", "account_id": acc, "user_id": f"{acc}_u{u}",
                  "properties": {}, "context": {}} for u in range(users(d))]
    return rows


def read(path):
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


with tempfile.TemporaryDirectory() as tmp:
    data = Path(tmp) / "data"
    (data / "events").mkdir(parents=True)
    ten = lambda d: 10
    by_day = {}
    for r in (events("acc_1", 1000, ten) + events("acc_2", 1000, ten) + events("acc_3", 1000, ten)
              + events("acc_4", 100, ten) + events("acc_5", 1000, ten, END - timedelta(days=10))
              + events("acc_6", 1000, lambda d: 10 if d < END - timedelta(days=6) else 2)):
        by_day.setdefault(r["timestamp"][:10], []).append(r)
    for d, rows in by_day.items():
        (data / "events" / f"{d}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    cols = ["account_id", "account_name", "tier", "region", "contract_start", "renewal_date",
            "contracted_events", "add_ons", "csm_owner", "cs_pool", "pool_owner", "last_qbr_date",
            "next_qbr_date", "arr", "status", "customer_since", "csm_since"]
    crm = [  # 600,000 a year = 1,644 a day: 1,000 a day is about 60%, 100 a day about 6%
        ["acc_1", "Fine Co", "Scale", "UK&I", "2026-03-01", "2027-03-01", 600000],
        ["acc_2", "No Contract Co", "Scale", "UK&I", "2026-03-01", "2027-03-01", ""],
        ["acc_3", "Date Co", "Scale", "UK&I", "2026-06-26", "26/06/2027", 600000],
        ["acc_4", "No Pool Co", "Scale", "UK&I", "2026-03-01", "2027-03-01", 600000],
        ["acc_5", "Feed Co", "Enterprise", "UK&I", "2026-03-01", "2027-03-01", 600000],
        ["acc_6", "Falling Co", "Scale", "UK&I", "2026-03-01", "2027-03-01", ""],
    ]
    with (data / "crm_accounts.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for acc, name, tier, region, start, renewal, contract in crm:
            w.writerow([acc, name, tier, region, start, renewal, contract, "",
                        "csm_x" if tier == "Enterprise" else "", "" if acc == "acc_4" or tier == "Enterprise" else "pool_x",
                        "", "2026-09-01" if tier == "Enterprise" else "", "2026-12-01" if tier == "Enterprise" else "",
                        50000, "active", "2023-03-01", "2023-03-01" if tier == "Enterprise" else ""])
    with (data / "cs_team.csv").open("w", newline="") as f:
        csv.writer(f).writerows([["owner_id", "owner_name", "owner_type", "region", "manager", "pool"],
                                 ["csm_x", "X", "csm", "UK&I", "mgr_x", ""],
                                 ["pool_x", "UK&I pool", "pool", "UK&I", "mgr_x", ""]])

    print("\ncheck_inputs.py: warnings, never a stop")
    run = subprocess.run([sys.executable, "check_inputs.py", str(data)], cwd=ROOT, capture_output=True,
                         text=True, encoding="utf-8")
    check(run.returncode == 0 and "0 errors" in run.stdout, "it runs (exit 0, no ERROR)", run.stdout[-400:])
    for what, needle in (("the unreadable renewal", "acc_3"), ("the empty contract", "acc_2, acc_6"),
                         ("the empty pool", "no cs_pool"), ("the stopped feed", "feed stopped")):
        check(needle in run.stdout, f"it warns about {what}", needle)

    # the engine reads and writes data/ next to where it runs: run it in the temporary folder
    os.chdir(tmp)
    settings.INPUTS, settings.OUTPUTS = data, data
    settings.SETTINGS["data"].update({"events_glob": "events/*.jsonl", "crm_file": "crm_accounts.csv",
                                      "team_file": "cs_team.csv", "engagement_glob": "engagement/*.jsonl"})
    (data / "flags_journey.csv").write_text("account_id,account_name,tier,region,status,reasons,routed_to,action\n")
    import flags                                  # noqa: E402
    from cairnpath_db import connect              # noqa: E402
    with contextlib.redirect_stdout(io.StringIO()):
        flags.main()

    print("\nOne bad cell spoils only its own row")
    con = connect()
    renewals = dict(con.sql("SELECT account_id, renewal_date FROM crm").fetchall())
    check(renewals["acc_3"] is None and renewals["acc_1"] == date(2027, 3, 1),
          "acc_3's renewal is empty, acc_1's still a date", str(renewals))
    utils = con.sql("SELECT count(*) FROM daily_util_30d WHERE util IS NULL").fetchone()[0]
    check(utils == 0, "no null utilisation for the page to read (no contract = no rows)", str(utils))

    gaps = {(r["account_id"], r["status"]): r for r in read(data / "flags_data.csv")}
    churn = {r["account_id"]: r for r in read(data / "flags_churn.csv")}
    expansion = {r["account_id"]: r for r in read(data / "flags_expansion.csv")}
    print("\nEach gap shows, to the right owner")
    want = [("acc_2", "No contract volume", "cs_ops"), ("acc_3", "Renewal date unreadable", "cs_ops"),
            ("acc_4", "No pool", "cs_ops"), ("acc_5", "Volume feed stopped", "data_eng"),
            ("acc_6", "No contract volume", "cs_ops"), ("acc_6", "Risk hidden", "pool_x")]
    for acc, status, owner in want:
        r = gaps.get((acc, status))
        check(r is not None and r["routed_to"] == owner, f"{acc}: {status}, to {owner}",
              r and r["routed_to"])
    check(not any(a == "acc_1" for a, _ in gaps), "acc_1 (nothing missing) has no gap")
    check(len(gaps) == len(want), f"nothing else ({len(want)} rows)", str(sorted(gaps)))
    check("users" in gaps[("acc_6", "Risk hidden")]["reasons"], "acc_6's hidden risk says what can still be seen",
          gaps[("acc_6", "Risk hidden")]["reasons"])

    print("\nNo guessing: the flags that need the missing value stay quiet")
    check("acc_2" not in churn, "acc_2 (no contract) gets no usage flag")
    check(("acc_4", "Risk hidden") not in gaps, "acc_4 (no pool, Shelfware open): no second row to CS ops, who has the gap")
    check("acc_5" not in churn and "acc_5" not in expansion,
          "acc_5 (feed stopped) gets no churn or expansion flag from the quiet days", str(churn.get("acc_5")))
    check(churn.get("acc_4", {}).get("status") == "Shelfware" and churn["acc_4"]["routed_to"] == "cs_ops",
          "acc_4 (no pool) is still Shelfware, and its task goes to CS ops, not to nobody", str(churn.get("acc_4")))

    print("\nThe page's facts read it without a crash")
    import facts                                  # noqa: E402
    try:
        accounts = {a["account_id"]: a for a in facts.accounts(con)}
        ok = "acc_2" in accounts and any(f["flag"] == "data" for f in accounts["acc_2"]["flags"])
        check(ok, "facts.accounts() runs, and acc_2 carries its data flag")
    except Exception as e:                        # noqa: BLE001
        check(False, "facts.accounts() runs", repr(e))

print(f"\n{'All passed' if not failed else f'{failed} failed'}")
sys.exit(1 if failed else 0)
