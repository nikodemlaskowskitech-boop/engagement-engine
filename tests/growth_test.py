"""
Whitespace (flags.py view growth, 25 Nov 2026): growth with a hook, never "they don't use it,
sell it". Made-up accounts over 120 days, in a temporary folder:
  acc_1  Enterprise, cohort-heavy, no add-on, QBR in 53 days -> Add-on hook, rides on the QBR
                                                               (no separate task)
  acc_8  the same, nothing booked                           -> Add-on hook, a task to its CSM
  acc_9  Scale, cohort-heavy, marked "not a fit" last month  -> Not a fit, paused 6 months
  acc_2  Enterprise, the add-on bought, never used          -> Unused add-on (and its wide
                                                               dashboards "also seen": one line)
  acc_3  Scale, dashboards to 30 people, 3 log in          -> People hook, to its pool
  acc_4  Scale, cohort-heavy, using a tenth of its contract -> Held: basics first (not a task)
  acc_5  Enterprise, cohort-heavy, no contract volume       -> Held: data missing
  acc_6  Self-service, cohort-heavy                         -> to the report, no pitch
  acc_7  Enterprise, an ordinary mix                        -> nothing (no hook, no pitch)
And the store: a schema change makes a store built before it stale (a new property once
left an old store looking fresh, and every step failed on the missing key).

Needs DuckDB, no AWS, no demo data. Run: python tests/growth_test.py
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
DAYS = [END - timedelta(days=119 - i) for i in range(120)]
# per account: (volume a day, people, the day's actions: [(event, viewers or None)])
PLAN = {
    "acc_1": (1000, 6, [("cohort_created", None)] * 3 + [("chart_created", None)]),
    "acc_2": (1000, 6, [("dashboard_shared", 30), ("chart_created", None)]),
    "acc_3": (1000, 3, [("dashboard_shared", 30), ("chart_created", None)]),
    "acc_4": (100, 6, [("cohort_created", None)] * 3 + [("chart_created", None)]),
    "acc_5": (1000, 6, [("cohort_created", None)] * 3 + [("chart_created", None)]),
    "acc_6": (1000, 6, [("cohort_created", None)] * 3 + [("chart_created", None)]),
    "acc_8": (1000, 6, [("cohort_created", None)] * 3 + [("chart_created", None)]),
    "acc_9": (1000, 6, [("cohort_created", None)] * 3 + [("chart_created", None)]),
    "acc_7": (1000, 6, [("chart_created", None), ("dashboard_shared", 3), ("cohort_created", None), ("data_export", None)]),
}

with tempfile.TemporaryDirectory() as tmp:
    data = Path(tmp) / "data"
    (data / "events").mkdir(parents=True)
    for d in DAYS:
        rows = []
        for acc, (vol, people, acts) in PLAN.items():
            rows.append({"event_id": f"{acc}v{d}", "event_name": "daily_event_volume", "event_type": "system",
                         "timestamp": f"{d}T12:00:00Z", "account_id": acc, "user_id": None,
                         "properties": {"event_count": vol}, "context": {}})
            for u in range(people):
                for n, (name, viewers) in enumerate(acts):
                    rows.append({"event_id": f"{acc}{d}{u}{n}", "event_name": name, "event_type": "product",
                                 "timestamp": f"{d}T10:00:00Z", "account_id": acc, "user_id": f"{acc}_u{u}",
                                 "properties": {"viewers": viewers} if viewers else {}, "context": {}})
        (data / "events" / f"{d}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    with (data / "crm_accounts.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["account_id", "account_name", "tier", "region", "contract_start", "renewal_date",
                    "contracted_events", "add_ons", "csm_owner", "cs_pool", "last_qbr_date", "next_qbr_date",
                    "arr", "status", "customer_since"])
        for acc, tier, addon, contract in (("acc_1", "Enterprise", "", 600000), ("acc_2", "Enterprise", "experiments", 600000),
                                           ("acc_3", "Scale", "", 600000), ("acc_4", "Scale", "", 600000),
                                           ("acc_5", "Enterprise", "", ""), ("acc_6", "Self-service", "", 600000),
                                           ("acc_7", "Enterprise", "", 600000), ("acc_8", "Enterprise", "", 600000),
                                           ("acc_9", "Scale", "", 600000)):
            w.writerow([acc, acc.upper(), tier, "UK&I", "2026-03-01", "2027-03-01", contract, addon,
                        "csm_x" if tier == "Enterprise" else "", "pool_x" if tier == "Scale" else "",
                        "2026-09-01", "2027-02-10" if acc == "acc_8" else "2026-12-10", 100000, "active", "2024-03-01"])
    with (data / "cs_team.csv").open("w", newline="") as f:
        csv.writer(f).writerows([["owner_id", "owner_name", "owner_type", "region", "manager", "pool"],
                                 ["csm_x", "X", "csm", "UK&I", "mgr_x", ""], ["pool_x", "UK&I pool", "pool", "UK&I", "mgr_x", ""]])
    (data / "engagement").mkdir()
    (data / "engagement" / "decisions.jsonl").write_text(json.dumps({
        "event_id": "d1", "event_name": "recommendation_declined", "event_type": "engagement",
        "timestamp": "2026-09-20T10:00:00Z", "account_id": "acc_9", "user_id": None,
        "properties": {"owner_id": "pool_x", "topic": "growth", "status": "Add-on hook", "outcome": "not_now"},
        "context": {"source": "cockpit"}}) + "\n")
    (data / "flags_journey.csv").write_text("account_id,account_name,tier,region,status,reasons,routed_to,action\n")

    os.chdir(tmp)
    settings.INPUTS, settings.OUTPUTS = data, data
    settings.SETTINGS["data"].update({"events_glob": "events/*.jsonl", "crm_file": "crm_accounts.csv",
                                      "team_file": "cs_team.csv", "engagement_glob": "engagement/*.jsonl"})
    import cairnpath_db                           # noqa: E402
    import flags                                  # noqa: E402
    import ingest                                 # noqa: E402
    with contextlib.redirect_stdout(io.StringIO()):
        ingest.main()
        flags.main()

    got = {r["account_id"]: r for r in csv.DictReader((data / "flags_growth.csv").open(encoding="utf-8"))}
    print("\nEach hook, or the reason it's held back")
    for acc, status, owner in (("acc_1", "Add-on hook", "none"), ("acc_8", "Add-on hook", "csm_x"),
                               ("acc_9", "Not a fit", "none"), ("acc_2", "Unused add-on", "none"),
                               ("acc_3", "People hook", "pool_x"), ("acc_4", "Held: basics first", "none"),
                               ("acc_5", "Held: data missing", "none")):
        r = got.get(acc, {})
        check(r.get("status") == status and r.get("routed_to") == owner, f"{acc}: {status}, to {owner}",
              (r.get("status"), r.get("routed_to")))
    check(got.get("acc_6", {}).get("routed_to") == "report", "acc_6 (Self-service): to the report, no pitch",
          got.get("acc_6"))
    check("acc_7" not in got, "acc_7 (an ordinary mix): nothing, no hook no pitch")
    check("Also seen: People hook" in got.get("acc_2", {}).get("reasons", ""),
          "acc_2: one line per account (the unused add-on first), its people hook as \"also seen\"",
          got.get("acc_2", {}).get("reasons"))
    check(got.get("acc_1", {}).get("action", "").startswith("Raise it at the QBR (in 53 days), no separate task. Open with:"),
          "acc_1: rides on the QBR in 53 days, with the question to open with", got.get("acc_1", {}).get("action"))
    check(got.get("acc_8", {}).get("action", "").startswith("Nothing booked in 60 days: book a conversation"),
          "acc_8: nothing booked, so a task: book a conversation", got.get("acc_8", {}).get("action"))
    check("Not a fit: Not now (timing or budget) (20 Sep); back after 19 Mar 2027" in got.get("acc_9", {}).get("reasons", ""),
          "acc_9: the reason, when, and when it comes back", got.get("acc_9", {}).get("reasons"))
    check("Dashboards shared with up to 30 people; 3 log in" in got.get("acc_3", {}).get("reasons", ""),
          "acc_3: the evidence in the customer's terms", got.get("acc_3", {}).get("reasons"))

    print("\nThe store follows the schema")
    check(cairnpath_db.store_is_fresh(), "fresh after ingest")
    real = cairnpath_db.events_from_json
    cairnpath_db.events_from_json = lambda: real().replace("viewers INTEGER", "viewers INTEGER, extra VARCHAR")
    check(not cairnpath_db.store_is_fresh(), "a schema change makes it stale (rebuilt, never read with a missing key)")
    cairnpath_db.events_from_json = real

print(f"\n{'All passed' if not failed else f'{failed} failed'}")
sys.exit(1 if failed else 0)
