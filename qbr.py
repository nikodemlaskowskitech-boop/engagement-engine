"""
QBR facts packs: everything a CSM needs on one page before a QBR.

For every Enterprise account with a QBR in the next 30 days, this collects
the facts, all computed in SQL:

  account     tier, region, CSM, contract dates, contracted volume, add-ons
  qbr         next and last QBR dates
  usage       utilisation month by month, and a simple forecast at renewal
  key users   the top three users and their share of activity; a champion
              only when the top user does 2x+ what the next one does, and
              whether they're slowing down
  adoption    weekly active users vs. the 90-day average (monthly active users
              alone hides declines), features used, underused add-ons
  health      ingestion errors and every open flag from flags.py

Users are pseudonymised ("User A") before anything leaves this script, as the
LLM step will read the packs. The real user IDs go to a separate key file so
names can be put back into the final brief, outside the model.

Run flags.py first (this reads its results), then:  python qbr.py

Writes data/qbr_facts.json and data/qbr_user_key.csv.
"""

import csv
import json
import sys
from pathlib import Path

from cairnpath_db import connect

sys.stdout.reconfigure(encoding="utf-8")

con = connect()

CHAMPION_RATIO = 2        # the top user counts as the champion when they do at least
                          # twice as much as the next most active user


def rows(sql):
    """Run a query and return a list of dicts (column name -> value)."""
    rel = con.sql(sql)
    return [dict(zip(rel.columns, r)) for r in rel.fetchall()]


# The accounts with a QBR coming up, with their account and usage facts
accounts = rows("""
    SELECT q.account_id, q.account_name, q.region,
           c.csm_owner, t.owner_name AS csm_name,
           c.contract_start, c.renewal_date, c.contracted_events, c.add_ons,
           q.next_qbr_date, q.days_to_next_qbr, q.last_qbr_date, q.days_since_last_qbr,
           m.util_m1, m.util_m2, m.util_m3, m.users_m1, m.users_m3,
           round((c.renewal_date - a.as_of_date) / 30.0, 1) AS months_to_renewal,
           -- straight-line forecast at renewal, only when climbing (and never below today)
           round(m.util_m3 + greatest((m.util_m3 - m.util_m1) / 2.0, 0)
                 * (c.renewal_date - a.as_of_date) / 30.0)   AS forecast_util_at_renewal
    FROM qbr_schedule q
    JOIN crm c USING (account_id)
    LEFT JOIN cs_team t ON t.owner_id = c.csm_owner
    LEFT JOIN monthly_usage m USING (account_id)
    CROSS JOIN as_of a
    WHERE q.qbr_coming_up
    ORDER BY q.next_qbr_date
""")
ids = ", ".join(f"'{a['account_id']}'" for a in accounts) or "''"

# Features used in the last 90 days
features = rows(f"""
    SELECT account_id, event_name, count(*) AS times_used
    FROM events
    WHERE event_type = 'product' AND account_id IN ({ids})
    GROUP BY account_id, event_name
    ORDER BY account_id, times_used DESC
""")

# The top three users by activity, their share, and the champion's trend
top_users = rows(f"""
    WITH per_user AS (
        SELECT e.account_id, e.user_id,
               count(*) AS events_90d,
               count(*) FILTER (WHERE e.event_date > a.as_of_date - 30) AS events_last_30d,
               count(*) FILTER (WHERE e.event_date > a.as_of_date - 60
                                  AND e.event_date <= a.as_of_date - 30) AS events_prev_30d
        FROM events e
        CROSS JOIN as_of a
        WHERE e.event_type = 'product' AND e.user_id IS NOT NULL AND e.account_id IN ({ids})
        GROUP BY e.account_id, e.user_id
    ),
    ranked AS (
        SELECT *,
               row_number() OVER (PARTITION BY account_id ORDER BY events_90d DESC, user_id) AS rank,
               round(100.0 * events_90d / sum(events_90d) OVER (PARTITION BY account_id)) AS share_pct
        FROM per_user
    )
    SELECT * FROM ranked WHERE rank <= 3 ORDER BY account_id, rank
""")

# Weekly active users now vs. the average week (the same measure churn uses):
# monthly active users counts anyone who showed up once and hides declines
weekly = rows(f"""
    SELECT e.account_id,
           count(DISTINCT e.user_id) FILTER (WHERE e.event_date > a.as_of_date - 7) AS wau_now,
           round(count(DISTINCT e.user_id || '|' || CAST((a.as_of_date - e.event_date) // 7 AS VARCHAR))
                 / 13.0, 1) AS wau_avg_90d
    FROM events e
    CROSS JOIN as_of a
    WHERE e.user_id IS NOT NULL AND e.account_id IN ({ids})
    GROUP BY e.account_id
""")

errors = rows(f"""
    SELECT e.account_id, count(*) AS ingestion_errors_30d
    FROM events e
    CROSS JOIN as_of a
    WHERE e.event_name = 'ingestion_error' AND e.event_date > a.as_of_date - 30
      AND e.account_id IN ({ids})
    GROUP BY e.account_id
""")


def open_flags(account_id):
    """Every row flags.py raised for this account, read from its result files."""
    found = []
    for flag in ("activation", "churn", "expansion"):
        path = Path(f"data/flags_{flag}.csv")
        if not path.exists():
            sys.exit(f"{path} is missing: run python flags.py first")
        with path.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r["account_id"] == account_id:
                    found.append({"flag": flag, "status": r["status"], "reasons": r["reasons"]})
    return found


# --- Assemble one pack per account, pseudonymising users on the way ---------------

packs, user_key = [], []
for a in accounts:
    acc_id = a["account_id"]
    users = [u for u in top_users if u["account_id"] == acc_id]
    key_users = []
    for u, alias in zip(users, ["User A", "User B", "User C"]):
        user_key.append({"account_id": acc_id, "alias": alias, "user_id": u["user_id"]})
        key_users.append({"alias": alias, "share_of_activity_pct": u["share_pct"],
                          "events_last_30d": u["events_last_30d"],
                          "events_prev_30d": u["events_prev_30d"]})
    # A "champion" only exists when one person clearly carries the account. If
    # activity is spread evenly, saying "the champion is slowing down" would
    # mislead. Relative, not a fixed share: in a 20-person team even a strong
    # champion has only 15-25% of the activity.
    champion = None
    if key_users and (len(key_users) == 1 or
                      key_users[0]["share_of_activity_pct"] >= CHAMPION_RATIO * key_users[1]["share_of_activity_pct"]):
        champion = key_users[0]
    if champion and champion["events_prev_30d"]:
        change = round(100 * (champion["events_last_30d"] / champion["events_prev_30d"] - 1))
        champion_trend = (f"{champion['alias']} carries {champion['share_of_activity_pct']:.0f}% of activity; "
                          f"their activity is {change:+d}% vs. the previous 30 days")
    elif champion:
        champion_trend = f"{champion['alias']} carries {champion['share_of_activity_pct']:.0f}% of activity"
    else:
        champion_trend = "Activity is spread evenly: no single champion"

    w = next((x for x in weekly if x["account_id"] == acc_id), {"wau_now": 0, "wau_avg_90d": 0})
    wau_change = round(100 * (w["wau_now"] / w["wau_avg_90d"] - 1)) if w["wau_avg_90d"] else None

    used = {f["event_name"]: f["times_used"] for f in features if f["account_id"] == acc_id}
    underused = []
    if "experiments" in (a["add_ons"] or "") and not used.get("experiment_launched"):
        underused.append("Experiments add-on: no experiments launched in 90 days")

    packs.append({
        "account": {
            "account_id": acc_id, "name": a["account_name"], "region": a["region"],
            "csm": a["csm_name"] or "unassigned",
            "contract_start": a["contract_start"], "renewal_date": a["renewal_date"],
            "months_to_renewal": a["months_to_renewal"],
            "contracted_events_per_year": a["contracted_events"],
            "add_ons": a["add_ons"] or "none",
        },
        "qbr": {
            "next_qbr_date": a["next_qbr_date"], "days_to_next_qbr": a["days_to_next_qbr"],
            "last_qbr_date": a["last_qbr_date"] or "none yet",
            "days_since_last_qbr": a["days_since_last_qbr"],
        },
        "usage": {
            "utilisation_pct_by_month": [a["util_m1"], a["util_m2"], a["util_m3"]],
            # the forecast only projects growth; flat or falling usage is not forecast down
            "forecast_utilisation_pct_at_renewal": (
                a["forecast_util_at_renewal"]
                if a["util_m1"] is not None and a["util_m3"] > a["util_m1"]
                else "no growth trend to forecast"),
        },
        "adoption": {
            "weekly_active_users_now": w["wau_now"],
            "weekly_active_users_avg_90d": w["wau_avg_90d"],
            "weekly_active_users_change_pct": wau_change,
            "monthly_active_users_two_months_ago": a["users_m1"],
            "monthly_active_users_this_month": a["users_m3"],
            "features_used_90d": used,
            "underused_add_ons": underused,
        },
        "key_users": key_users,
        "champion_trend": champion_trend,
        "health": {
            "ingestion_errors_30d": next((e["ingestion_errors_30d"] for e in errors
                                          if e["account_id"] == acc_id), 0),
            "open_flags": open_flags(acc_id),
        },
    })

Path("data/qbr_facts.json").write_text(json.dumps(packs, indent=2, default=str), encoding="utf-8")
with Path("data/qbr_user_key.csv").open("w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=["account_id", "alias", "user_id"])
    writer.writeheader()
    writer.writerows(user_key)

print(f"\nQBR facts packs: {len(packs)} Enterprise accounts with a QBR in the next 30 days\n")
for p in packs:
    flags = ", ".join(f"{f['flag']} {f['status']}" for f in p["health"]["open_flags"]) or "none"
    print(f"  {p['qbr']['next_qbr_date']}  {p['account']['account_id']}  {p['account']['name']:<24}"
          f"  CSM: {p['account']['csm']:<16}  open flags: {flags}")
if packs:
    print("\nExample pack (the first one):\n")
    print(json.dumps(packs[0], indent=2, default=str))
print("\nSaved data/qbr_facts.json and data/qbr_user_key.csv (the key never goes to the LLM)")
