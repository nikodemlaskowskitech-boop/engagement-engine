"""
First look at the Cairnpath data with DuckDB.

DuckDB reads the JSON and CSV files directly: no database to install,
no loading step. Each query below checks one thing the flags will need.

Run:  python explore.py
"""

import sys

import duckdb

# Always write UTF-8, so DuckDB's table borders also work when the
# output is saved to a file on Windows (which defaults to cp1252).
sys.stdout.reconfigure(encoding="utf-8")

con = duckdb.connect()

# Treat the files as tables.
# We spell out the schema instead of letting DuckDB guess it: this is the
# schema we agreed, and a file that breaks it fails loudly instead of quietly.
con.sql("""
    CREATE VIEW events AS
    SELECT *, "timestamp"::DATE AS event_date
    FROM read_json('data/events/*.jsonl', format = 'newline_delimited', columns = {
        event_id:   'VARCHAR',
        event_name: 'VARCHAR',
        event_type: 'VARCHAR',
        timestamp:  'TIMESTAMP',
        account_id: 'VARCHAR',
        user_id:    'VARCHAR',
        properties: 'STRUCT(event_count BIGINT, invited_user VARCHAR, error_code VARCHAR)',
        context:    'STRUCT(sdk_version VARCHAR, source VARCHAR)'
    })
""")
con.sql("CREATE VIEW crm AS SELECT * FROM read_csv_auto('data/crm_accounts.csv')")
con.sql("CREATE VIEW cs_team AS SELECT * FROM read_csv_auto('data/cs_team.csv')")

# Utilisation per account, kept as a view so later queries (and the flags)
# can reuse it instead of copying the logic.
con.sql("""
    CREATE VIEW utilisation AS
    WITH as_of AS (
        -- the data's own 'today': the last day we have data for
        SELECT max(event_date) AS as_of_date FROM events
    ),
    first_seen AS (
        -- go-live date = the first day we saw anything from the account
        SELECT account_id, min(event_date) AS go_live_date
        FROM events
        GROUP BY account_id
    ),
    last_30 AS (
        -- total volume the account sent in the last 30 days
        SELECT account_id, sum(properties.event_count) AS events_30d
        FROM events, as_of
        WHERE event_name = 'daily_event_volume'
          AND event_date > as_of_date - 30
        GROUP BY account_id
    )
    SELECT c.account_id,
           c.tier,
           c.region,
           f.go_live_date,
           -- days live in the window: 30 at most, fewer for new accounts.
           -- Quiet days after go-live still count, so a customer who
           -- goes silent shows a drop instead of hiding it.
           -- Accounts that never went live get no value (least() would
           -- otherwise ignore the empty go-live date and return 30).
           CASE WHEN f.go_live_date IS NOT NULL
                THEN least(30, a.as_of_date - f.go_live_date + 1)
           END AS days_measured,
           round(l.events_30d * 365
                 / least(30, a.as_of_date - f.go_live_date + 1)
                 / c.contracted_events * 100) AS utilisation_pct
    FROM crm c
    CROSS JOIN as_of a
    LEFT JOIN first_seen f ON f.account_id = c.account_id
    LEFT JOIN last_30 l    ON l.account_id = c.account_id
""")

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
