"""
First look at the Cairnpath data with DuckDB.

DuckDB reads the JSON and CSV files directly: no database to install,
no loading step. Each query below checks one thing the flags will need.

Run:  python explore.py
"""

import duckdb

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

print("\n1. What does an event look like to DuckDB?")
con.sql("DESCRIBE events").show()

print("\n2. Events per account, product vs system")
con.sql("""
    SELECT account_id,
           count(*) FILTER (WHERE event_type = 'product') AS product_events,
           count(*) FILTER (WHERE event_type = 'system')  AS system_events,
           count(DISTINCT user_id)                        AS users_seen
    FROM events
    GROUP BY account_id
    ORDER BY account_id
""").show()

print("\n3. Utilisation: last 30 days' volume, annualised, vs. the contract")
print("   (new accounts are only measured over the days they've been live)")
con.sql("""
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
           f.go_live_date,
           -- days live in the window: 30 at most, fewer for new accounts.
           -- Quiet days after go-live still count, so a customer who
           -- goes silent shows a drop instead of hiding it.
           least(30, a.as_of_date - f.go_live_date + 1) AS days_measured,
           round(l.events_30d * 365
                 / least(30, a.as_of_date - f.go_live_date + 1)
                 / c.contracted_events * 100) AS utilisation_pct
    FROM crm c
    CROSS JOIN as_of a
    LEFT JOIN first_seen f ON f.account_id = c.account_id
    LEFT JOIN last_30 l    ON l.account_id = c.account_id
    ORDER BY utilisation_pct DESC NULLS LAST
""").show()

print("\n4. Handoff gap: activity, but no CRM row")
con.sql("""
    SELECT DISTINCT account_id FROM events
    WHERE account_id NOT IN (SELECT account_id FROM crm)
""").show()

print("\n5. Silent accounts: contract started 10+ days ago, no events at all")
con.sql("""
    SELECT account_id, tier, contract_start
    FROM crm
    WHERE account_id NOT IN (SELECT DISTINCT account_id FROM events)
      AND contract_start <= (SELECT max(event_date) FROM events) - 10
""").show()
