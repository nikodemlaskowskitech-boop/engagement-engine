"""
The weekly history (history.py) must say what the engine would have said that week.

Three made-up accounts over a year, in a temporary folder, each one a way a
replay goes wrong (12 Nov 2026):
  acc_1  renewed in May. Before the fix the activation flag took every renewal
         for a new contract, and 14 days later called the customer "Stalled".
  acc_2  left the book on 1 March. Its old events must not look like a handoff
         gap in the weeks after it left, nor before.
  acc_3  a new Enterprise logo signed on 1 June, its CSM came on 20 June: absent
         before it signed, "No CSM assigned" in between, and the CS ops task for
         it Open (with its age) until the CSM came, then gone (Resolved).
history.py itself checks that the last week equals today's flags.

Needs DuckDB, no AWS, no demo data. Run: python tests/history_test.py
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
DAYS = [END - timedelta(days=364 - i) for i in range(365)]


def account_events(acc, first, last, users, volume):
    rows = []
    for d in DAYS:
        if not (first <= d <= last):
            continue
        rows.append({"event_id": f"{acc}_v{d}", "event_name": "daily_event_volume", "event_type": "system",
                     "timestamp": f"{d}T12:00:00Z", "account_id": acc, "user_id": None,
                     "properties": {"event_count": volume}, "context": {}})
        if d == first:
            rows.append({"event_id": f"{acc}_sdk", "event_name": "sdk_installed", "event_type": "product",
                         "timestamp": f"{d}T09:00:00Z", "account_id": acc, "user_id": f"{acc}_u0",
                         "properties": {}, "context": {}})
        rows += [{"event_id": f"{acc}_c{d}_{u}", "event_name": "chart_created", "event_type": "product",
                  "timestamp": f"{d}T10:00:00Z", "account_id": acc, "user_id": f"{acc}_u{u}",
                  "properties": {}, "context": {}} for u in range(users)]
    return rows


with tempfile.TemporaryDirectory() as tmp:
    data = Path(tmp) / "data"
    (data / "events").mkdir(parents=True)
    by_day = {}
    for r in (account_events("acc_1", DAYS[0], END, 10, 1000)
              + account_events("acc_2", DAYS[0], date(2026, 2, 28), 10, 1000)
              + account_events("acc_3", date(2026, 6, 1), END, 3, 1000)):
        by_day.setdefault(r["timestamp"][:10], []).append(r)
    for d, rows in by_day.items():
        (data / "events" / f"{d}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    cols = ["account_id", "account_name", "tier", "region", "contract_start", "renewal_date",
            "contracted_events", "add_ons", "csm_owner", "cs_pool", "pool_owner", "last_qbr_date",
            "next_qbr_date", "arr", "status", "customer_since", "csm_since"]
    with (data / "crm_accounts.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        w.writerow(["acc_1", "Renewed Co", "Enterprise", "UK&I", "2026-05-01", "2027-05-01", 600000, "",
                    "csm_x", "", "", "2026-08-01", "2026-11-01", 100000, "active", "2024-05-01", "2024-05-01"])
        w.writerow(["acc_2", "Gone Co", "Enterprise", "UK&I", "2025-03-01", "2026-03-01", 600000, "",
                    "csm_x", "", "", "2025-12-01", "", 100000, "churned", "2024-03-01", "2024-03-01"])
        w.writerow(["acc_3", "New Co", "Enterprise", "UK&I", "2026-06-01", "2027-06-01", 600000, "",
                    "csm_x", "", "", "", "", 100000, "active", "2026-06-01", "2026-06-20"])
    with (data / "cs_team.csv").open("w", newline="") as f:
        csv.writer(f).writerows([["owner_id", "owner_name", "owner_type", "region", "manager", "pool"],
                                 ["csm_x", "X", "csm", "UK&I", "mgr_x", ""]])
    with (data / "action_log.csv").open("w", newline="") as f:
        csv.writer(f).writerows([["logged_on", "account_id", "flag", "status", "routed_to", "channel", "action", "outcome"],
                                 ["2026-06-02", "acc_3", "activation", "No CSM assigned", "cs_ops", "queue",
                                  "Assign a CSM", "posted to Slack"]])
    (data / "flags_journey.csv").write_text("account_id,account_name,tier,region,status,reasons,routed_to,action\n")

    # the engine reads and writes data/ next to where it runs: run it in the temporary folder
    os.chdir(tmp)
    settings.INPUTS, settings.OUTPUTS = data, data
    settings.SETTINGS["data"].update({"events_glob": "events/*.jsonl", "crm_file": "crm_accounts.csv",
                                      "team_file": "cs_team.csv", "engagement_glob": "engagement/*.jsonl"})
    import flags                                  # noqa: E402
    import history                                # noqa: E402
    with contextlib.redirect_stdout(io.StringIO()):
        flags.main()
    try:
        with contextlib.redirect_stdout(io.StringIO()) as out:
            history.main()
        ran = True
    except SystemExit as e:                       # the replay of the last day differs from today
        ran, out = False, str(e)
    print("\nThe replay runs, and its last week equals today's flags")
    check(ran, "history.py finished and its own check passed", str(out))

    hf = list(csv.DictReader((data / "history_flags.csv").open())) if ran else []
    ht = list(csv.DictReader((data / "history_tasks.csv").open())) if ran else []
    rows = lambda acc, week=None: [r for r in hf if r["account_id"] == acc and (week is None or r["week"] == week)]
    weeks = sorted({r["week"] for r in hf})
    june = [w for w in weeks if "2026-06-01" <= w < "2026-06-20"]
    after = [w for w in weeks if w >= "2026-06-20"]

    print("\nA renewal is not a new customer")
    act1 = [r["status"] for r in rows("acc_1") if r["flag"] == "activation"]
    check(not act1, "acc_1 (renewed 1 May) never gets an activation status", str(sorted(set(act1))))

    print("\nAn account that left is history, not a handoff gap")
    gap2 = [r["week"] for r in rows("acc_2") if r["status"] == "Handoff gap"]
    check(not gap2, "acc_2 (left 1 March) is never a handoff gap", str(gap2[:3]))

    print("\nA new Enterprise logo before its CSM came")
    before = [r for r in hf if r["account_id"] == "acc_3" and r["week"] < "2026-06-01"]
    check(not before, "acc_3 is in no week before it signed", str(before[:2]))
    check(bool(june) and all(any(r["status"] == "No CSM assigned" for r in rows("acc_3", w)) for w in june),
          f"'No CSM assigned' in every week before its CSM came ({', '.join(june)})")
    check(not any(r["status"] == "No CSM assigned" for w in after for r in rows("acc_3", w)),
          "and in no week after it")
    task = {r["week"]: r for r in ht if r["account_id"] == "acc_3"}
    ages = [(w, task[w]["action_status"], int(task[w]["age_days"])) for w in june if w in task]
    want = [(w, "Open", (date.fromisoformat(w) - date(2026, 6, 2)).days + 1) for w in june]
    check(ages == want, "its CS ops task is Open, with its age, in those weeks", f"{ages} != {want}")
    check(not any(w in task for w in after), "and gone once the CSM came (Resolved)")

print(f"\n{'All passed' if not failed else f'{failed} failed'}")
sys.exit(1 if failed else 0)
