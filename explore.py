"""
First look at the Cairnpath data with DuckDB.

DuckDB reads the JSON and CSV files directly: no database to install,
no loading step. Each query below checks one thing the flags will need.

Run:  python explore.py
"""

import sys

from cairnpath_db import connect

# Always write UTF-8, so DuckDB's table borders also work when the
# output is saved to a file on Windows (which defaults to cp1252).
sys.stdout.reconfigure(encoding="utf-8")

con = connect()

print("\n1. What does an event look like to DuckDB?")
con.sql("DESCRIBE events").show()

print("\n2. Book of business: accounts in the CRM by tier and region")
con.sql("""
    SELECT tier,
           count(*) FILTER (WHERE region = 'UK&I')             AS "UK&I",
           count(*) FILTER (WHERE region = 'DACH')             AS "DACH",
           count(*) FILTER (WHERE region = 'France & Benelux') AS "France & Benelux",
           count(*) FILTER (WHERE region = 'Nordics')          AS "Nordics",
           count(*)                                            AS total
    FROM crm
    GROUP BY tier
    ORDER BY total
""").show()

print("\n3. Utilisation by tier (accounts that are live)")
print("   (new accounts are only measured over the days they've been live)")
con.sql("""
    SELECT tier,
           count(*)                                         AS live_accounts,
           round(median(utilisation_pct))                   AS median_pct,
           count(*) FILTER (WHERE utilisation_pct < 40)     AS under_40_pct,
           count(*) FILTER (WHERE utilisation_pct >= 90)    AS over_90_pct
    FROM utilisation
    WHERE utilisation_pct IS NOT NULL
    GROUP BY tier
    ORDER BY tier
""").show()

print("\n4. Handoff gap: activity, but no CRM row")
con.sql("""
    SELECT count(DISTINCT account_id) AS accounts_missing_from_crm
    FROM events
    WHERE account_id NOT IN (SELECT account_id FROM crm)
""").show()

print("\n5. Silent accounts: contract started 10+ days ago, no events at all")
con.sql("""
    SELECT account_id, tier, region, contract_start
    FROM crm
    WHERE account_id NOT IN (SELECT DISTINCT account_id FROM events)
      AND contract_start <= (SELECT max(event_date) FROM events) - 10
    ORDER BY contract_start
""").show()

print("\n6. CSM books: Enterprise accounts per CSM (and any left unassigned)")
con.sql("""
    SELECT coalesce(c.csm_owner, '(unassigned)') AS csm,
           t.owner_name,
           c.region,
           t.manager,
           count(*) AS accounts
    FROM crm c
    LEFT JOIN cs_team t ON t.owner_id = c.csm_owner
    WHERE c.tier = 'Enterprise'
    GROUP BY ALL
    ORDER BY c.region, csm
""").show()

print("\n7. CS engagement week by week (emails, opens, workshop attendees, meetings)")
con.sql("""
    SELECT date_trunc('week', event_date)::DATE AS week_starting,
           count(*) FILTER (WHERE event_name = 'email_sent')        AS emails_sent,
           count(*) FILTER (WHERE event_name = 'email_opened')      AS emails_opened,
           count(*) FILTER (WHERE event_name = 'workshop_attended') AS workshop_attendees,
           count(*) FILTER (WHERE event_name = 'meeting_held')      AS meetings
    FROM engagement
    GROUP BY ALL
    ORDER BY week_starting
""").show()

print("\n8. Meetings by owner and topic (CSMs and Scale pools)")
con.sql("""
    SELECT coalesce(t.owner_name, e.properties.owner_id) AS owner,
           count(*) FILTER (WHERE e.properties.topic = 'qbr')        AS qbr,
           count(*) FILTER (WHERE e.properties.topic = 'check_in')   AS check_in,
           count(*) FILTER (WHERE e.properties.topic = 'kickoff')    AS kickoff,
           count(*) FILTER (WHERE e.properties.topic IN ('churn_call', 'activation_call', 'growth_call', 'follow_up')) AS calls,
           count(*) AS meetings
    FROM engagement e
    LEFT JOIN cs_team t ON t.owner_id = e.properties.owner_id
    WHERE e.event_name = 'meeting_held'
    GROUP BY ALL
    ORDER BY meetings DESC
""").show()
