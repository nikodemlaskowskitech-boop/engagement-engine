"""
Shared DuckDB setup for the Cairnpath scripts.

Every script calls connect() to get a connection with the same views:
  events       the daily JSON event files, with the agreed schema declared
  engagement   the CS history: emails, workshops and meetings, same schema
               (event_type 'engagement'; properties.owner_id and .topic)
  crm          the CRM table
  cs_team      named CSMs and regional pools
  as_of        the data's own 'today': the last day we have data for
  utilisation  last-30-day utilisation per CRM account (see the comments)
  monthly_usage  utilisation and active users in each of the three 30-day
                 months, for accounts live for the full 90 days
  qbr_schedule   Enterprise accounts with their QBR timing: coming up in the
                 next 30 days, or overdue (live 4+ months, last QBR 120+ days
                 ago or never, nothing booked in the next 30 days)

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
    # What CS did and how customers responded. Same schema as the product
    # events, but its own properties: who owns the touch and what it was about.
    con.sql("""
        CREATE VIEW engagement AS
        SELECT *, "timestamp"::DATE AS event_date
        FROM read_json('data/engagement/*.jsonl', format = 'newline_delimited', columns = {
            event_id:   'VARCHAR',
            event_name: 'VARCHAR',
            event_type: 'VARCHAR',
            timestamp:  'TIMESTAMP',
            account_id: 'VARCHAR',
            user_id:    'VARCHAR',
            properties: 'STRUCT(owner_id VARCHAR, topic VARCHAR)',
            context:    'STRUCT(source VARCHAR)'
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

    # Month by month, for accounts live for the whole 90-day window.
    # m1 = 90 to 61 days ago, m2 = 60 to 31 days ago, m3 = the last 30 days.
    # Used by the churn and expansion flags, so both read the same numbers.
    con.sql("""
        CREATE VIEW monthly_usage AS
        WITH live AS (
            SELECT c.account_id, c.contracted_events, a.as_of_date
            FROM crm c
            CROSS JOIN as_of a
            JOIN (SELECT account_id, min(event_date) AS go_live_date
                  FROM events GROUP BY account_id) f ON f.account_id = c.account_id
            WHERE f.go_live_date <= a.as_of_date - 89
        ),
        sums AS (
            SELECT l.account_id, l.contracted_events,
                   sum(e.properties.event_count) FILTER (WHERE e.event_name = 'daily_event_volume'
                       AND e.event_date <= l.as_of_date - 60) AS vol_m1,
                   sum(e.properties.event_count) FILTER (WHERE e.event_name = 'daily_event_volume'
                       AND e.event_date > l.as_of_date - 60 AND e.event_date <= l.as_of_date - 30) AS vol_m2,
                   sum(e.properties.event_count) FILTER (WHERE e.event_name = 'daily_event_volume'
                       AND e.event_date > l.as_of_date - 30) AS vol_m3,
                   count(DISTINCT e.user_id) FILTER (WHERE e.event_date <= l.as_of_date - 60) AS users_m1,
                   count(DISTINCT e.user_id) FILTER (WHERE e.event_date > l.as_of_date - 30) AS users_m3
            FROM live l
            JOIN events e ON e.account_id = l.account_id
            GROUP BY l.account_id, l.contracted_events
        )
        SELECT account_id,
               round(vol_m1 * 365 / 30 / contracted_events * 100) AS util_m1,
               round(vol_m2 * 365 / 30 / contracted_events * 100) AS util_m2,
               round(vol_m3 * 365 / 30 / contracted_events * 100) AS util_m3,
               users_m1,
               users_m3
        FROM sums
    """)

    con.sql("""
        CREATE VIEW qbr_schedule AS
        SELECT c.account_id, c.account_name, c.tier, c.region, c.csm_owner,
               c.contract_start, c.last_qbr_date, c.next_qbr_date,
               a.as_of_date - c.last_qbr_date  AS days_since_last_qbr,
               c.next_qbr_date - a.as_of_date  AS days_to_next_qbr,
               c.next_qbr_date > a.as_of_date AND c.next_qbr_date <= a.as_of_date + 30 AS qbr_coming_up,
               a.as_of_date - c.contract_start >= 120
                   AND (c.last_qbr_date IS NULL OR a.as_of_date - c.last_qbr_date > 120)
                   AND NOT coalesce(c.next_qbr_date > a.as_of_date
                                    AND c.next_qbr_date <= a.as_of_date + 30, false) AS qbr_overdue
        FROM crm c
        CROSS JOIN as_of a
        WHERE c.tier = 'Enterprise'
    """)
    return con
