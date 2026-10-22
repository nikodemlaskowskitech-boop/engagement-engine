"""
Export everything the four views need to one file: data/views_data.json,
and build the views page from it: data/views.html (views_template.html with
the data inside, so it opens in any browser, offline).

The views page only draws; every number is computed here, in SQL, with the
same views the reports use (cairnpath_db.py, team_report.py and
onboarding_report.py), so the page and the reports can never disagree.

What goes in:
  accounts        every CRM account: tier, region, owner, contract dates,
                  30-day utilisation for each day it can be measured, weekly
                  active users for each of the 13 weeks, and its open flags
                  (activation, churn, expansion, QBR, journeys)
  touches         every CS touch on the timeline (emails, opens, workshops,
                  meetings), with owner and topic
  onboarding      welcomed vs. control, one row per new account
  time_to_action  one row per churn or expansion Act account with an owner
  team            the CS team (owners, types, managers)

Real user IDs never leave this script: weekly active users are counts.

Run generate_events.py, flags.py and journeys.py first, then:
    python export_views.py
"""

import csv
import json
import sys
from pathlib import Path

import onboarding_report
import team_report
from cairnpath_db import connect

sys.stdout.reconfigure(encoding="utf-8")

DATA = Path(__file__).resolve().parent / "data"
FLAGS = ("activation", "churn", "expansion", "qbr", "journey")


def rows(con, sql):
    """Run a query and return a list of dicts, dates as text."""
    rel = con.sql(sql)
    return [{k: (str(v) if hasattr(v, "isoformat") else v) for k, v in zip(rel.columns, r)}
            for r in rel.fetchall()]


con = connect()
team_report.add_views(con)          # time_to_action, meetings_90d
onboarding_report.add_views(con)    # onboarding

as_of = str(con.sql("SELECT as_of_date FROM as_of").fetchone()[0])

# The days a 30-day utilisation exists (the first 29 days of data can't have one)
util_dates = [str(d) for (d,) in con.sql(
    "SELECT DISTINCT event_date FROM daily_util_30d ORDER BY 1").fetchall()]
util = {}
for account_id, day, value in con.sql(
        "SELECT account_id, event_date, util FROM daily_util_30d").fetchall():
    util.setdefault(account_id, {})[str(day)] = int(value)

# Weekly active users: 13 weeks, the last one ending on the as-of date
wau = {}
for account_id, weeks_ago, users in con.sql("""
        SELECT e.account_id, (a.as_of_date - e.event_date) // 7 AS weeks_ago,
               count(DISTINCT e.user_id) AS users
        FROM events e, as_of a
        WHERE e.user_id IS NOT NULL AND (a.as_of_date - e.event_date) // 7 < 13
        GROUP BY ALL""").fetchall():
    wau.setdefault(account_id, [0] * 13)[12 - weeks_ago] = users      # oldest week first
week_ends = [str(d) for (d,) in con.sql(
    "SELECT as_of_date - (7 * (12 - i))::INTEGER FROM as_of, range(13) t(i) ORDER BY i").fetchall()]

# Open flags per account, straight from the flag files
flags = {}
for flag in FLAGS:
    path = DATA / f"flags_{flag}.csv"
    if not path.exists():
        sys.exit(f"{path} is missing: run python flags.py, then python journeys.py")
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            flags.setdefault(r["account_id"], []).append(
                {"flag": flag, "status": r["status"], "reasons": r["reasons"],
                 "routed_to": r["routed_to"]})

accounts = rows(con, """
    SELECT c.account_id, c.account_name, c.tier, c.region, c.contract_start, c.renewal_date,
           c.contracted_events, c.add_ons, coalesce(c.csm_owner, c.cs_pool) AS owner_id,
           t.owner_name AS owner, t.manager
    FROM crm c
    LEFT JOIN cs_team t ON t.owner_id = coalesce(c.csm_owner, c.cs_pool)
    ORDER BY c.account_id""")
for a in accounts:
    a["util"] = [util.get(a["account_id"], {}).get(d) for d in util_dates]
    a["wau"] = wau.get(a["account_id"], [0] * 13)
    a["flags"] = flags.get(a["account_id"], [])

touches = rows(con, """
    SELECT account_id, event_date AS date, event_name AS event,
           properties.topic AS topic, properties.owner_id AS owner_id,
           context.source AS source
    FROM engagement
    ORDER BY "timestamp" """)

export = {
    "as_of": as_of,
    "util_dates": util_dates,
    "week_ends": week_ends,
    "accounts": accounts,
    "touches": touches,
    "onboarding": rows(con, "SELECT * FROM onboarding ORDER BY account_id"),
    "time_to_action": rows(con, """
        SELECT t.*, o.owner_name AS owner, o.owner_type, o.manager
        FROM time_to_action t JOIN cs_team o ON o.owner_id = t.routed_to
        ORDER BY t.account_id"""),
    "team": rows(con, "SELECT * FROM cs_team ORDER BY owner_type, owner_id"),
}

out = DATA / "views_data.json"
data_json = json.dumps(export, separators=(",", ":"))
out.write_text(data_json, encoding="utf-8")

# The page: the template with the data dropped into its <script> tag ("</" is
# escaped so no account name can ever close the tag early)
template = (Path(__file__).resolve().parent / "views_template.html").read_text(encoding="utf-8")
page = DATA / "views.html"
page.write_text("<!doctype html>\n<meta charset=\"utf-8\">\n"
                "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
                + template.replace("__VIEWS_DATA__", data_json.replace("</", "<\\/")),
                encoding="utf-8")
print(f"\nViews data as of {as_of}: {len(accounts)} accounts, {len(touches)} touches, "
      f"{len(export['onboarding'])} onboarding rows, {len(export['time_to_action'])} Act accounts")
print(f"Saved {out.relative_to(DATA.parent)} ({out.stat().st_size // 1024} KB)")
print(f"Saved {page.relative_to(DATA.parent)}: open it in a browser")
