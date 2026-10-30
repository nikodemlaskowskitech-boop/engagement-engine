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
  daily_util_30d per account and day: utilisation over the 30 days up to
                 that day, rounded like the flags (when did a line get crossed?)
  last_touch     per account: the last CS touch (what and when), the last
                 meeting, and whether the getting-started email was opened
  qbr_schedule   Enterprise accounts with their QBR timing: coming up in the
                 next 30 days, or overdue (live 4+ months, last QBR 120+ days
                 ago or never, nothing booked in the next 30 days)
  qbr_slips      flags handed to a QBR whose date passed with no QBR held
                 (from the action log; empty before the first actions.py run)
  action_status  every task sent to a person: Open, Claimed, Done or Resolved,
                 and its age (from the action log, engagement and the flags)

Keeping this in one place means the schema and the utilisation rule are
defined once, not copied into every script.
"""

import os

import duckdb

from settings import SETTINGS


USAGE_EVENT = SETTINGS["utilisation"]["usage_event"]   # what counts as usage (settings.toml)


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
            -- status and manager: a manager's decision in the cockpit (cockpit_sync.py);
            -- amount: a renewal forecast's value (forecast_set); due: a next step's
            -- date (next_step_set); both from the cockpit
            properties: 'STRUCT(owner_id VARCHAR, topic VARCHAR, outcome VARCHAR, note VARCHAR, slack_user VARCHAR, status VARCHAR, manager VARCHAR, amount DOUBLE, due VARCHAR)',
            context:    'STRUCT(source VARCHAR)'
        })
    """)
    # The CRM, with your tier names mapped to the engine's three coverage models
    # (settings.toml [tiers]) and your commitment column read as
    # contracted_events ([utilisation]). Your own tier name stays in crm_tier.
    # Everything downstream speaks the engine's names, so a new team edits
    # settings.toml, not the SQL.
    t, u, money = SETTINGS["tiers"], SETTINGS["utilisation"], SETTINGS["money"]
    # ARR from the CRM; without that column, an estimate from the list price
    # ([money] in settings.toml), so the value views still work
    crm_cols = {r[0] for r in con.sql("DESCRIBE SELECT * FROM read_csv_auto('data/crm_accounts.csv')").fetchall()}
    price = "CASE tier " + " ".join(f"WHEN '{t[k]}' THEN {money['price_per_1k'][n]}" for k, n in
                                     (("tech_touch", "Self-service"), ("pooled", "Scale"), ("named_csm", "Enterprise"))) + " END"
    arr = (f"{money['arr_column']}::DOUBLE" if money["arr_column"] in crm_cols
           else f"round({u['contract_column']} / 1000.0 * {price}, -2)")
    arr_exclude = f", {money['arr_column']}" if money["arr_column"] in crm_cols else ""
    con.sql(f"""
        CREATE VIEW crm AS
        WITH raw AS (SELECT * FROM read_csv_auto('data/crm_accounts.csv'))
        SELECT * EXCLUDE (tier, {u['contract_column']}{arr_exclude}),
               {arr}                                 AS arr,
               {'false' if money['arr_column'] in crm_cols else 'true'} AS arr_estimated,
               CASE tier WHEN '{t['tech_touch']}' THEN 'Self-service'
                         WHEN '{t['pooled']}'     THEN 'Scale'
                         WHEN '{t['named_csm']}'  THEN 'Enterprise'
                         ELSE tier END              AS tier,
               tier                                 AS crm_tier,
               {u['contract_column']}::BIGINT       AS contracted_events
        FROM raw
    """)
    con.sql("CREATE VIEW cs_team AS SELECT * FROM read_csv_auto('data/cs_team.csv')")

    # Utilisation per account, kept as a view so later queries (and the flags)
    # can reuse it instead of copying the logic.
    con.sql(f"""
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
            WHERE event_name = '{USAGE_EVENT}'
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
    con.sql(f"""
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
                   sum(e.properties.event_count) FILTER (WHERE e.event_name = '{USAGE_EVENT}'
                       AND e.event_date <= l.as_of_date - 60) AS vol_m1,
                   sum(e.properties.event_count) FILTER (WHERE e.event_name = '{USAGE_EVENT}'
                       AND e.event_date > l.as_of_date - 60 AND e.event_date <= l.as_of_date - 30) AS vol_m2,
                   sum(e.properties.event_count) FILTER (WHERE e.event_name = '{USAGE_EVENT}'
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

    # QBR timing comes from settings.toml
    up, overdue = SETTINGS["qbr"]["coming_up_days"], SETTINGS["qbr"]["overdue_after_days"]
    con.sql(f"""
        CREATE VIEW qbr_schedule AS
        SELECT c.account_id, c.account_name, c.tier, c.region, c.csm_owner,
               c.contract_start, c.last_qbr_date, c.next_qbr_date,
               a.as_of_date - c.last_qbr_date  AS days_since_last_qbr,
               c.next_qbr_date - a.as_of_date  AS days_to_next_qbr,
               c.next_qbr_date > a.as_of_date AND c.next_qbr_date <= a.as_of_date + {up} AS qbr_coming_up,
               a.as_of_date - c.contract_start >= {overdue}
                   AND (c.last_qbr_date IS NULL OR a.as_of_date - c.last_qbr_date > {overdue})
                   AND NOT coalesce(c.next_qbr_date > a.as_of_date
                                    AND c.next_qbr_date <= a.as_of_date + {up}, false) AS qbr_overdue
        FROM crm c
        CROSS JOIN as_of a
        WHERE c.tier = 'Enterprise'
    """)

    # 30-day utilisation on every day, measured the way the flags measure it
    # (rounded, full 30 days only), so reports and journeys can find the day
    # an account first crossed a flag's line.
    con.sql(f"""
        CREATE VIEW daily_util_30d AS
        WITH daily AS (
            SELECT account_id, event_date, properties.event_count AS vol
            FROM events
            WHERE event_name = '{USAGE_EVENT}'
        ),
        rolling AS (
            SELECT account_id, event_date,
                   sum(vol) OVER w   AS vol_30d,
                   count(*) OVER w   AS days_in_window
            FROM daily
            WINDOW w AS (PARTITION BY account_id ORDER BY event_date
                         ROWS BETWEEN 29 PRECEDING AND CURRENT ROW)
        )
        SELECT r.account_id, r.event_date,
               round(r.vol_30d * 365.0 / 30 / c.contracted_events * 100) AS util
        FROM rolling r
        JOIN crm c USING (account_id)
        WHERE r.days_in_window = 30          -- a full 30 days, or it isn't comparable
    """)

    # The last touch per account, for journeys ("no meeting in 30 days") and
    # for context on every task ("last touch: QBR on 12 Sep"). Customer-facing
    # touches only: a task claimed, reassigned or a forecast is internal work.
    con.sql("""
        CREATE VIEW last_touch AS
        SELECT account_id,
               max(event_date)                                  AS last_touch_date,
               arg_max(replace(properties.topic, '_', ' ') || ' ('
                       || replace(event_name, '_', ' ') || ')', "timestamp") AS last_touch,
               max(event_date) FILTER (WHERE event_name = 'meeting_held') AS last_meeting_date,
               min(event_date) FILTER (WHERE event_name = 'email_sent'
                                         AND properties.topic = 'getting_started') AS getting_started_sent,
               count(*) FILTER (WHERE event_name = 'email_opened'
                                  AND properties.topic = 'getting_started') > 0  AS getting_started_opened
        FROM engagement
        WHERE event_name IN ('email_sent', 'email_opened', 'workshop_attended', 'meeting_held')
        GROUP BY account_id
    """)

    # QBR slips: flags handed to a QBR ("covered by the QBR on <date>" in the
    # action log) whose date has passed with no QBR held since. A QBR that keeps
    # moving must not hide a flag for ever: actions.py raises these as normal
    # tasks (never folded into a QBR again) and journeys.py escalates them.
    if os.path.exists("data/action_log.csv"):
        con.sql("""
            CREATE VIEW qbr_slips AS
            WITH covered AS (
                SELECT account_id, flag, status, logged_on::DATE AS logged_on,
                       regexp_extract(outcome, '(\\d{4}-\\d{2}-\\d{2})$', 1)::DATE AS promised_qbr
                FROM read_csv('data/action_log.csv', all_varchar = true)
                WHERE channel = 'qbr'
            )
            SELECT c.*
            FROM covered c, as_of a
            WHERE c.promised_qbr <= a.as_of_date      -- its day has come and gone
              AND NOT EXISTS (SELECT 1 FROM engagement e
                              WHERE e.account_id = c.account_id AND e.event_name = 'meeting_held'
                                AND e.properties.topic = 'qbr' AND e.event_date >= c.logged_on)
        """)
    else:   # no actions yet, so nothing can have slipped
        con.sql("""CREATE VIEW qbr_slips AS
                   SELECT NULL::VARCHAR AS account_id, NULL::VARCHAR AS flag, NULL::VARCHAR AS status,
                          NULL::DATE AS logged_on, NULL::DATE AS promised_qbr WHERE false""")

    # Action status: the action log says a task was SENT; this says what happened
    # since, worked out from the data (nobody has to tick anything):
    #   Done      the owner met the account about it after the task was sent:
    #             churn -> churn call or QBR, expansion -> growth call or QBR,
    #             QBR -> the QBR, activation and escalations -> any meeting.
    #             A check-in doesn't close a churn task: the problem has to be raised.
    #   Resolved  the flag went away on its own (usage recovered, CRM fixed).
    #             CS ops tasks can only end this way: the CRM record is fixed.
    #   Claimed   someone took it in Slack ("I'll take it"), nothing since
    #   (a manager can give a task to someone else in the cockpit: routed_to is then
    #    the new owner, assigned_from the old one, assigned_by the manager)
    #   (a manager's "Follow-up not picked up" task is Done once they approve the hand-back)
    #   Open      sent, nothing since
    flag_names = ("activation", "churn", "expansion", "qbr", "journey")
    if os.path.exists("data/action_log.csv") and all(
            os.path.exists(f"data/flags_{f}.csv") for f in flag_names):
        flag_files = " UNION ALL ".join(
            f"SELECT account_id, '{f}' AS flag, status FROM read_csv('data/flags_{f}.csv', all_varchar = true)"
            for f in flag_names)
        con.sql(f"""
            CREATE VIEW action_status AS
            WITH tasks AS (
                -- tasks for people (cards and Slack digests); the latest send of each
                SELECT account_id, flag, status,
                       arg_max(routed_to, line)      AS routed_to,
                       arg_min(routed_to, line)      AS first_owner,   -- before any reassignment
                       arg_max(action, line)         AS action,
                       max(logged_on)::DATE          AS sent_on
                -- line: the log's own order, so two sends on one day still have a last one
                FROM (SELECT *, row_number() OVER () AS line
                      FROM read_csv('data/action_log.csv', all_varchar = true))
                WHERE channel IN ('card', 'queue')
                GROUP BY account_id, flag, status
            ),
            open_flags AS ({flag_files}),
            done AS (
                SELECT t.account_id, t.flag, t.status, min(e.event_date) AS done_on
                FROM tasks t
                JOIN engagement e ON e.account_id = t.account_id AND e.event_date >= t.sent_on
                WHERE t.routed_to <> 'cs_ops'
                  AND CASE WHEN t.status = 'Follow-up not picked up'
                               THEN e.event_name = 'handback_approved'
                           WHEN e.event_name <> 'meeting_held' THEN false
                           ELSE CASE t.flag
                          WHEN 'churn'     THEN e.properties.topic IN ('churn_call', 'qbr')
                          WHEN 'expansion' THEN e.properties.topic IN ('growth_call', 'qbr')
                          WHEN 'qbr'       THEN e.properties.topic = 'qbr'
                          ELSE true                         -- activation, escalations
                      END END
                GROUP BY ALL
            ),
            claimed AS (
                SELECT t.account_id, t.flag, t.status, min(e.event_date) AS claimed_on,
                       arg_min(e.properties.slack_user, e."timestamp") AS claimed_by
                FROM tasks t
                JOIN engagement e ON e.account_id = t.account_id AND e.event_date >= t.sent_on
                WHERE e.event_name = 'task_claimed' AND e.properties.topic = t.flag
                GROUP BY ALL
            ),
            assigned AS (
                -- a manager gave the task to someone else in the cockpit: the latest decision wins
                SELECT t.account_id, t.flag, t.status,
                       arg_max(e.properties.owner_id, e."timestamp") AS assigned_to,
                       arg_max(e.properties.manager, e."timestamp")  AS assigned_by
                FROM tasks t
                JOIN engagement e ON e.account_id = t.account_id AND e.event_date >= t.sent_on
                WHERE e.event_name = 'task_assigned' AND e.properties.topic = t.flag
                  AND e.properties.status = t.status
                GROUP BY ALL
            )
            SELECT t.account_id, t.flag, t.status,
                   coalesce(g.assigned_to, t.routed_to) AS routed_to,   -- who has it now
                   t.action, t.sent_on,
                   CASE WHEN d.done_on IS NOT NULL      THEN 'Done'
                        WHEN o.account_id IS NULL       THEN 'Resolved'
                        WHEN c.claimed_on IS NOT NULL   THEN 'Claimed'
                        ELSE 'Open' END                 AS action_status,
                   d.done_on, c.claimed_by,
                   a.as_of_date + 1 - t.sent_on         AS age_days,  -- days since it was sent
                   g.assigned_by,                                     -- the manager, if reassigned
                   CASE WHEN g.assigned_to IS NOT NULL AND g.assigned_to <> t.first_owner
                        THEN t.first_owner END          AS assigned_from
            FROM tasks t
            CROSS JOIN as_of a
            LEFT JOIN done d     USING (account_id, flag, status)
            LEFT JOIN claimed c  USING (account_id, flag, status)
            LEFT JOIN assigned g USING (account_id, flag, status)
            -- a follow-up ("Shelfware (follow-up)") is resolved when its original flag is gone
            LEFT JOIN (SELECT DISTINCT * FROM open_flags) o
                   ON o.account_id = t.account_id AND o.flag = t.flag
                  AND o.status = regexp_replace(t.status, ' \\(follow-up.*\\)$', '')
        """)
    else:   # actions.py hasn't run yet: no tasks
        con.sql("""CREATE VIEW action_status AS
                   SELECT NULL::VARCHAR AS account_id, NULL::VARCHAR AS flag, NULL::VARCHAR AS status,
                          NULL::VARCHAR AS routed_to, NULL::VARCHAR AS action, NULL::DATE AS sent_on, NULL::VARCHAR AS action_status,
                          NULL::DATE AS done_on, NULL::VARCHAR AS claimed_by, NULL::INTEGER AS age_days,
                          NULL::VARCHAR AS assigned_by, NULL::VARCHAR AS assigned_from
                   WHERE false""")
    return con
