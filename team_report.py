"""
CS team report: when an account needs action, how fast does its owner act?

For every account at churn or expansion Act with a CSM or a pool:

  signal date   the day its 30-day utilisation first crossed the flag's line
                (under 40% for churn, 80%+ for expansion). We never stored
                when flags fired, so the start is rebuilt from the usage.
  call date     the owner's first problem call (churn, growth or activation
                call). Routine check-ins and QBRs don't count: they were in
                the diary anyway, so they aren't a reaction.
  days to call  call date minus signal date. A call up to 14 days before the
                signal counts as 0 ("called early"): good owners spot trouble
                before a 30-day average does.

Per owner, and rolled up per manager: Act accounts, how many got a call, the
median days to call, how many were called early, how many were escalated
(no meeting in 30 days, from journeys.py), and all meetings in the 90 days.

Not measured yet: outcome (did the account recover after the call?). That
needs the generator to make calls change usage, and time to pass.

Counts sit next to every percentage: most CSMs have one or two Act accounts,
and one account is an anecdote, not a pattern.

Run flags.py and journeys.py first, then:  python team_report.py
"""

import sys

from cairnpath_db import connect

sys.stdout.reconfigure(encoding="utf-8")

EARLY_DAYS = 14          # a call this many days before the signal still counts
PROBLEM_CALLS = "('churn_call', 'growth_call', 'activation_call')"

con = connect()

con.sql(f"""
    CREATE VIEW time_to_action AS
    WITH daily AS (
        SELECT account_id, event_date, properties.event_count AS vol
        FROM events
        WHERE event_name = 'daily_event_volume'
    ),
    trailing AS (
        -- 30-day utilisation on every day, the way the flags measure it
        SELECT account_id, event_date,
               sum(vol) OVER w   AS vol_30d,
               count(*) OVER w   AS days_in_window
        FROM daily
        WINDOW w AS (PARTITION BY account_id ORDER BY event_date
                     ROWS BETWEEN 29 PRECEDING AND CURRENT ROW)
    ),
    util_30d AS (
        SELECT t.account_id, t.event_date,
               t.vol_30d * 365.0 / 30 / c.contracted_events * 100 AS util
        FROM trailing t
        JOIN crm c USING (account_id)
        WHERE t.days_in_window = 30          -- a full 30 days, or it isn't comparable
    ),
    act AS (
        SELECT account_id, routed_to, 'churn' AS flag
        FROM read_csv_auto('data/flags_churn.csv') WHERE status = 'Act'
        UNION ALL
        SELECT account_id, routed_to, 'expansion' AS flag
        FROM read_csv_auto('data/flags_expansion.csv') WHERE status = 'Act'
    ),
    signals AS (
        SELECT a.account_id, a.routed_to, a.flag,
               min(u.event_date) FILTER (WHERE (a.flag = 'churn' AND u.util < 40)
                                            OR (a.flag = 'expansion' AND u.util >= 80)) AS signal_date
        FROM act a
        JOIN util_30d u USING (account_id)
        GROUP BY a.account_id, a.routed_to, a.flag
    ),
    calls AS (
        SELECT account_id, event_date AS call_date
        FROM engagement
        WHERE event_name = 'meeting_held' AND properties.topic IN {PROBLEM_CALLS}
    )
    SELECT s.account_id, s.routed_to, s.flag, s.signal_date,
           min(c.call_date) AS call_date,
           greatest(min(c.call_date) - s.signal_date, 0) AS days_to_call,
           min(c.call_date) < s.signal_date AS called_early,
           s.account_id IN (SELECT account_id FROM read_csv_auto('data/flags_journey.csv')
                            WHERE status = 'No meeting in 30 days') AS escalated
    FROM signals s
    LEFT JOIN calls c ON c.account_id = s.account_id
                     AND c.call_date >= s.signal_date - {EARLY_DAYS}
    GROUP BY s.account_id, s.routed_to, s.flag, s.signal_date
""")

con.sql("""
    CREATE VIEW meetings_90d AS
    SELECT properties.owner_id AS owner_id, count(*) AS meetings_90d
    FROM engagement, as_of
    WHERE event_name = 'meeting_held' AND event_date <= as_of_date
    GROUP BY 1
""")

print("\nCS team report: when an account needs action, how fast does its owner act?")

print("\n1. By owner (CSMs, then Scale pools), fastest first")
con.sql("""
    SELECT o.owner_name                                      AS owner,
           o.owner_type                                      AS type,
           count(*)                                          AS act_accounts,
           format('{} ({}%)', count(t.call_date),
                  round(100.0 * count(t.call_date) / count(*))::INT) AS called,
           median(t.days_to_call)                            AS median_days_to_call,
           count(*) FILTER (WHERE t.called_early)            AS called_early,
           count(*) FILTER (WHERE t.escalated)               AS escalated,
           any_value(m.meetings_90d)                         AS meetings_90d
    FROM time_to_action t
    JOIN cs_team o ON o.owner_id = t.routed_to
    LEFT JOIN meetings_90d m ON m.owner_id = t.routed_to
    GROUP BY o.owner_name, o.owner_type
    ORDER BY o.owner_type, median_days_to_call NULLS LAST, owner
""").show()

print("\n2. By manager (their CSMs and pools together)")
con.sql("""
    SELECT o.manager,
           count(*)                                          AS act_accounts,
           format('{} ({}%)', count(t.call_date),
                  round(100.0 * count(t.call_date) / count(*))::INT) AS called,
           median(t.days_to_call)                            AS median_days_to_call,
           count(*) FILTER (WHERE t.escalated)               AS escalated
    FROM time_to_action t
    JOIN cs_team o ON o.owner_id = t.routed_to
    GROUP BY o.manager
    ORDER BY o.manager
""").show()

print("\n3. Every Act account, slowest first (no call at the top)")
con.sql("""
    SELECT o.owner_name AS owner, t.account_id, c.account_name, t.flag,
           t.signal_date, t.call_date, t.days_to_call, t.escalated
    FROM time_to_action t
    JOIN cs_team o ON o.owner_id = t.routed_to
    JOIN crm c USING (account_id)
    ORDER BY t.call_date IS NOT NULL, t.days_to_call DESC, owner
""").show(max_rows=100)

print("\nNot measured yet: outcome (did the account recover after the call?).")
