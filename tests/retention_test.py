"""
GRR and NRR by the standard definitions (retention.py), on five made-up accounts
whose answer is known by hand (12 Nov 2026):

  acc_1  €100k all year                       -> 100 in, 100 out
  acc_2  renewed in May from €100k to €150k   -> 100 in, 150 out (GRR counts 100)
  acc_3  renewed in June from €100k to €60k   -> 100 in,  60 out
  acc_4  churned in March (€100k)             -> 100 in,   0 out
  acc_5  a new logo signed in July (€80k)     -> in neither number
  GRR = (100 + 100 + 60 + 0) / 400 = 65%;  NRR = (100 + 150 + 60 + 0) / 400 = 77.5%

Plus the year before from a two-line ARR bridge: GRR = 1 - (churn + downsell) /
starting ARR, NRR adds expansion. And the forecast's and the call effect's views
exist and are empty-safe.

Needs DuckDB, no AWS, no demo data. Run: python tests/retention_test.py
"""
import contextlib
import csv
import io
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
START = END - timedelta(days=364)

with tempfile.TemporaryDirectory() as tmp:
    data = Path(tmp) / "data"
    (data / "events").mkdir(parents=True)
    # one volume event a day per account while it's a customer (the period runs on the events' dates)
    life = {"acc_1": (START, END), "acc_2": (START, END), "acc_3": (START, END),
            "acc_4": (START, date(2026, 2, 28)), "acc_5": (date(2026, 7, 1), END)}
    for i in range(365):
        d = START + timedelta(days=i)
        rows = [{"event_id": f"{a}_{d}", "event_name": "daily_event_volume", "event_type": "system",
                 "timestamp": f"{d}T12:00:00Z", "account_id": a, "user_id": None,
                 "properties": {"event_count": 1000}, "context": {}}
                for a, (f, t) in life.items() if f <= d <= t]
        (data / "events" / f"{d}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    cols = ["account_id", "account_name", "tier", "region", "contract_start", "renewal_date",
            "contracted_events", "add_ons", "csm_owner", "cs_pool", "pool_owner", "last_qbr_date",
            "next_qbr_date", "arr", "status", "customer_since", "csm_since"]
    crm = [
        ["acc_1", "Flat Co", "Scale", "UK&I", "2026-01-10", "2027-01-10", 365000, "", "", "pool_uki", "", "2026-08-01", "2026-11-01", 100000, "active", "2023-01-10", "2023-01-10"],
        ["acc_2", "Grew Co", "Scale", "UK&I", "2026-05-01", "2027-05-01", 365000, "", "", "pool_uki", "", "2026-08-01", "2026-11-01", 150000, "active", "2022-05-01", "2022-05-01"],
        ["acc_3", "Shrank Co", "Scale", "UK&I", "2026-06-01", "2027-06-01", 365000, "", "", "pool_uki", "", "2026-08-01", "2026-11-01", 60000, "active", "2024-06-01", "2024-06-01"],
        ["acc_4", "Gone Co", "Scale", "UK&I", "2025-03-01", "2026-03-01", 365000, "", "", "pool_uki", "", "2026-08-01", "2026-11-01", 100000, "churned", "2024-03-01", "2024-03-01"],
        ["acc_5", "New Co", "Scale", "UK&I", "2026-07-01", "2027-07-01", 365000, "", "", "pool_uki", "", "2026-08-01", "2026-11-01", 80000, "active", "2026-07-01", "2026-07-01"],
    ]
    with (data / "crm_accounts.csv").open("w", newline="") as f:
        csv.writer(f).writerows([cols] + crm)
    with (data / "cs_team.csv").open("w", newline="") as f:
        csv.writer(f).writerows([["owner_id", "owner_name", "owner_type", "region", "manager", "pool"],
                                 ["pool_uki", "UK&I pool", "pool", "UK&I", "mgr_x", ""]])
    with (data / "renewal_history.csv").open("w", newline="") as f:
        csv.writer(f).writerows([["account_id", "renewal_date", "outcome", "renewal_arr", "arr_before"],
                                 ["acc_1", "2026-01-10", "renewed", 100000, 100000],
                                 ["acc_4", "2026-03-01", "churned", 0, 100000],
                                 ["acc_2", "2026-05-01", "expanded", 150000, 100000],
                                 ["acc_3", "2026-06-01", "downsized", 60000, 100000]])
    with (data / "arr_bridge_history.csv").open("w", newline="") as f:
        csv.writer(f).writerows([["period_start", "period_end", "starting_arr", "new_logo_arr", "expansion_arr",
                                  "contraction_arr", "churned_arr", "ending_arr"],
                                 ["2024-10-19", "2025-04-18", 1000, 100, 20, 10, 40, 1070],
                                 ["2025-04-19", "2025-10-18", 1070, 50, 30, 10, 40, 1100]])

    os.chdir(tmp)
    settings.INPUTS, settings.OUTPUTS = data, data
    settings.SETTINGS["data"].update({"events_glob": "events/*.jsonl", "crm_file": "crm_accounts.csv",
                                      "team_file": "cs_team.csv", "engagement_glob": "engagement/*.jsonl",
                                      "renewal_history_file": "renewal_history.csv",
                                      "arr_bridge_file": "arr_bridge_history.csv",
                                      "outcomes_file": "renewal_outcomes.csv"})
    import backtest                               # noqa: E402
    import cairnpath_db                           # noqa: E402
    import flags                                  # noqa: E402
    import retention                              # noqa: E402
    import team_report                            # noqa: E402
    with contextlib.redirect_stdout(io.StringIO()):
        flags.main()                              # the flag files team_report reads
    for f in ("journey", "activation", "qbr"):
        p = data / f"flags_{f}.csv"
        if not p.exists():
            p.write_text("account_id,account_name,tier,region,status,reasons,routed_to,action\n")
    con = cairnpath_db.connect()
    flags.add_views(con)
    team_report.add_views(con)
    backtest.add_views(con)
    retention.add_views(con)

    one = lambda sql: dict(zip(con.sql(sql).columns, con.sql(sql).fetchone()))
    print("\nThis year, the standard cohort")
    y = one("SELECT * FROM retention_year")
    check(y["accounts"] == 4, "the cohort is the 4 customers of day one (the new logo is out)", str(y["accounts"]))
    check(y["start_arr"] == 400000, "starting ARR = €400k (each at its ARR on day one)", str(y["start_arr"]))
    check(abs(y["grr"] - 0.65) < 1e-9, "GRR = 65% (expansion capped at day one's ARR)", str(y["grr"]))
    check(abs(y["nrr"] - 0.775) < 1e-9, "NRR = 77.5% (expansion counted)", str(y["nrr"]))
    check((y["churned_arr"], y["contraction_arr"], y["expansion_arr"]) == (100000, 40000, 50000),
          "churn €100k, downsell €40k, expansion €50k", str((y["churned_arr"], y["contraction_arr"], y["expansion_arr"])))
    moves = dict(con.sql("SELECT account_id, movement FROM retention_accounts").fetchall())
    check(moves == {"acc_1": "flat", "acc_2": "expansion", "acc_3": "contraction", "acc_4": "churned"},
          "each account's part is right", str(moves))

    print("\nThe year before, from finance's ARR bridge")
    p = one("SELECT * FROM bridge_rates_prior")
    check(abs(p["grr"] - (1 - 100 / 1000)) < 1e-9, "GRR = 1 - (churn 80 + downsell 20) / 1000 = 90%", str(p["grr"]))
    check(abs(p["nrr"] - (1 + (50 - 100) / 1000)) < 1e-9, "NRR = 1 + (expansion 50 - 100) / 1000 = 95%", str(p["nrr"]))
    t = one("SELECT * FROM bridge_rates_this")
    check(abs(t["start_arr"] - 400000) < 1e-6 and abs(t["end_arr"] - 390000) < 1e-6,
          "this year's own bridge runs from €400k to €390k (€310k kept and grown, €80k new logo)",
          str((t["start_arr"], t["end_arr"])))

    print("\nThe views the page needs exist without renewal outcomes or an action log")
    for v in ("retention_forecast", "call_effect", "call_value"):
        try:
            con.sql(f"SELECT * FROM {v}").fetchall()
            ok = True
        except Exception as e:                  # noqa: BLE001
            ok, err = False, str(e)[:200]
        check(ok, f"{v} runs", "" if ok else err)

print(f"\n{'All passed' if not failed else f'{failed} failed'}")
sys.exit(1 if failed else 0)
