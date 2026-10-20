"""
Shared DuckDB setup for the Cairnpath scripts.

Every script calls connect() to get a connection with the same views:
  events       the daily JSON event files, with the agreed schema declared
  crm          the CRM table
  cs_team      named CSMs and regional pools
  as_of        the data's own 'today': the last day we have data for
  utilisation  last-30-day utilisation per CRM account (see the comments)

Keeping this in one place means the schema and the utilisation rule are
defined once, not copied into every script.
"""

import duckdb


def connect():
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
    con.sql("CREATE VIEW as_of AS SELECT max(event_date) AS as_of_date FROM events")
    return con
