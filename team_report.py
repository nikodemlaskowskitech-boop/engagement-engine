"""
CS team report: when an account needs action, how fast does its owner act?

For every account at churn or expansion Act with a CSM or a pool:

  signal date   the day its 30-day utilisation first crossed the flag's line
                (under 40% for churn, 80%+ for expansion). We never stored
                when flags fired, so the start is rebuilt from the usage.
  call date     the owner's first problem call (churn, growth or activation
                call). Routine check-ins don't count: they were in the diary
                anyway, so they aren't a reaction. A QBR counts when the
                signal came in the 30 days before it: QBR first means the
                flag was handed to that QBR (actions.py), so the QBR is the
                action. "via" says which it was (call or qbr).
  days to call  call date minus signal date. A call up to 14 days before the
                signal counts as 0 ("called early"): good owners spot trouble
                before a 30-day average does.

Per owner, and rolled up per manager: Act accounts, how many got a call, the
median days to call, how many were called early, how many were escalated
(no meeting in 30 days, from journeys.py), and all meetings in the 90 days.

Action status (sections 4 and 5): what happened to each task actions.py sent:
Open, Claimed in Slack, Done (the right kind of meeting since) or Resolved
(the flag went away). Managers see what nobody picked up: CS ops items and
pool tasks no one has claimed, oldest first.

Not measured yet: outcome (did the account recover after the call?). That
needs the generator to make calls change usage, and time to pass.

Counts sit next to every percentage: most CSMs have one or two Act accounts,
and one account is an anecdote, not a pattern.

Run flags.py and journeys.py first, then:  python team_report.py
"""

import os
import sys

from cairnpath_db import connect
from settings import SETTINGS

sys.stdout.reconfigure(encoding="utf-8")

EARLY_DAYS = SETTINGS["team_report"]["early_days"]   # a call this early still counts (settings.toml)
QBR_WINDOW = SETTINGS["qbr"]["coming_up_days"]       # a signal this close before a QBR is the QBR's job
PROBLEM_CALLS = "('churn_call', 'growth_call', 'activation_call')"

def add_views(con):
    """Create the time_to_action and meetings_90d views (also used by export_views.py)."""
    con.sql(f"""
        CREATE VIEW time_to_action AS
        WITH act AS (
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
            JOIN daily_util_30d u USING (account_id)    -- 30-day utilisation per day (cairnpath_db.py)
            GROUP BY a.account_id, a.routed_to, a.flag
        ),
        calls AS (
            SELECT account_id, event_date AS call_date, 'call' AS via
            FROM engagement
            WHERE event_name = 'meeting_held' AND properties.topic IN {PROBLEM_CALLS}
            UNION ALL
            SELECT account_id, event_date, 'qbr'
            FROM engagement
            WHERE event_name = 'meeting_held' AND properties.topic = 'qbr'
        )
        SELECT s.account_id, s.routed_to, s.flag, s.signal_date,
               min(c.call_date) AS call_date,
               arg_min(c.via, c.call_date) AS via,
               -- no call means no number: greatest() would quietly turn an empty
               -- value into 0 and make uncalled accounts look fast
               CASE WHEN min(c.call_date) IS NOT NULL
                    THEN greatest(min(c.call_date) - s.signal_date, 0) END AS days_to_call,
               min(c.call_date) < s.signal_date AS called_early,
               s.account_id IN (SELECT account_id FROM read_csv_auto('data/flags_journey.csv')
                                WHERE status = 'No meeting in 30 days') AS escalated
        FROM signals s
        LEFT JOIN calls c ON c.account_id = s.account_id
                         AND ((c.via = 'call' AND c.call_date >= s.signal_date - {EARLY_DAYS})
                              -- a QBR covers a signal from the 30 days before it
                              OR (c.via = 'qbr' AND c.call_date BETWEEN s.signal_date
                                                                    AND s.signal_date + {QBR_WINDOW}))
        GROUP BY s.account_id, s.routed_to, s.flag, s.signal_date
    """)

    # Action status: the action log says a task was SENT; this says what happened
    # since, worked out from the data (nobody has to tick anything):
    #   Done      the owner met the account about it after the task was sent:
    #             churn -> churn call or QBR, expansion -> growth call or QBR,
    #             QBR -> the QBR, activation and escalations -> any meeting.
    #             A check-in doesn't close a churn task: the problem has to be raised.
    #   Resolved  the flag went away on its own (usage recovered, CRM fixed).
    #             CS ops tasks can only end this way: the CRM record is fixed.
    #   Claimed   someone took it in Slack ("I'll take it"), nothing since
    #   Open      sent, nothing since
    if os.path.exists("data/action_log.csv"):
        flag_files = " UNION ALL ".join(
            f"SELECT account_id, '{f}' AS flag, status FROM read_csv('data/flags_{f}.csv', all_varchar = true)"
            for f in ("activation", "churn", "expansion", "qbr", "journey"))
        con.sql(f"""
            CREATE VIEW action_status AS
            WITH tasks AS (
                -- tasks for people (cards and Slack digests); the latest send of each
                SELECT account_id, flag, status,
                       arg_max(routed_to, logged_on) AS routed_to,
                       max(logged_on)::DATE          AS sent_on
                FROM read_csv('data/action_log.csv', all_varchar = true)
                WHERE channel IN ('card', 'queue')
                GROUP BY account_id, flag, status
            ),
            open_flags AS ({flag_files}),
            done AS (
                SELECT t.account_id, t.flag, t.status, min(e.event_date) AS done_on
                FROM tasks t
                JOIN engagement e ON e.account_id = t.account_id AND e.event_date >= t.sent_on
                WHERE e.event_name = 'meeting_held' AND t.routed_to <> 'cs_ops'
                  AND CASE t.flag
                          WHEN 'churn'     THEN e.properties.topic IN ('churn_call', 'qbr')
                          WHEN 'expansion' THEN e.properties.topic IN ('growth_call', 'qbr')
                          WHEN 'qbr'       THEN e.properties.topic = 'qbr'
                          ELSE true                         -- activation, escalations
                      END
                GROUP BY ALL
            ),
            claimed AS (
                SELECT t.account_id, t.flag, t.status, min(e.event_date) AS claimed_on,
                       arg_min(e.properties.slack_user, e."timestamp") AS claimed_by
                FROM tasks t
                JOIN engagement e ON e.account_id = t.account_id AND e.event_date >= t.sent_on
                WHERE e.event_name = 'task_claimed' AND e.properties.topic = t.flag
                GROUP BY ALL
            )
            SELECT t.account_id, t.flag, t.status, t.routed_to, t.sent_on,
                   CASE WHEN d.done_on IS NOT NULL      THEN 'Done'
                        WHEN o.account_id IS NULL       THEN 'Resolved'
                        WHEN c.claimed_on IS NOT NULL   THEN 'Claimed'
                        ELSE 'Open' END                 AS action_status,
                   d.done_on, c.claimed_by,
                   a.as_of_date + 1 - t.sent_on         AS age_days   -- days since it was sent
            FROM tasks t
            CROSS JOIN as_of a
            LEFT JOIN done d    USING (account_id, flag, status)
            LEFT JOIN claimed c USING (account_id, flag, status)
            LEFT JOIN (SELECT DISTINCT * FROM open_flags) o USING (account_id, flag, status)
        """)
    else:   # actions.py hasn't run yet: no tasks
        con.sql("""CREATE VIEW action_status AS
                   SELECT NULL::VARCHAR AS account_id, NULL::VARCHAR AS flag, NULL::VARCHAR AS status,
                          NULL::VARCHAR AS routed_to, NULL::DATE AS sent_on, NULL::VARCHAR AS action_status,
                          NULL::DATE AS done_on, NULL::VARCHAR AS claimed_by, NULL::INTEGER AS age_days
                   WHERE false""")

    con.sql("""
        CREATE VIEW meetings_90d AS
        SELECT properties.owner_id AS owner_id, count(*) AS meetings_90d
        FROM engagement, as_of
        WHERE event_name = 'meeting_held' AND event_date <= as_of_date
        GROUP BY 1
    """)


if __name__ == "__main__":
    con = connect()
    add_views(con)

    print("\nCS team report: when an account needs action, how fast does its owner act?")

    print("\n1. By owner (CSMs, then Scale pools), fastest first")
    con.sql("""
        SELECT o.owner_name                                      AS owner,
               o.owner_type,
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
    """).show(max_width=250)

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
               t.signal_date, t.call_date, t.via, t.days_to_call, t.escalated
        FROM time_to_action t
        JOIN cs_team o ON o.owner_id = t.routed_to
        JOIN crm c USING (account_id)
        ORDER BY t.call_date IS NOT NULL, t.days_to_call DESC, owner
    """).show(max_rows=100)

    first_day = con.sql("SELECT min(event_date) + 29 FROM events").fetchone()[0]
    print(f"\nA signal on {first_day} (the first day a 30-day average exists) means the account")
    print("was already over the line before measuring could start; the true wait may be longer.")
    print("\n4. Action status: sent is not done (from the action log; run actions.py first)")
    con.sql("""
        SELECT coalesce(o.owner_name, s.routed_to)                       AS owner,
               count(*)                                                  AS tasks,
               count(*) FILTER (WHERE action_status = 'Open')            AS open,
               count(*) FILTER (WHERE action_status = 'Claimed')         AS claimed,
               format('{} ({}%)', count(*) FILTER (WHERE action_status = 'Done'),
                      round(100.0 * count(*) FILTER (WHERE action_status = 'Done') / count(*))::INT) AS done,
               count(*) FILTER (WHERE action_status = 'Resolved')        AS resolved,
               max(age_days) FILTER (WHERE action_status IN ('Open', 'Claimed')) AS oldest_open_days
        FROM action_status s
        LEFT JOIN cs_team o ON o.owner_id = s.routed_to
        GROUP BY ALL
        ORDER BY oldest_open_days DESC NULLS LAST, owner
    """).show(max_rows=50)

    print("\n5. For managers: what nobody has picked up, oldest first")
    print("   CS ops items (handoff gaps, no CSM) and pool tasks no one has claimed")
    con.sql("""
        SELECT coalesce(o.manager, 'all managers') AS manager, s.routed_to, s.account_id,
               coalesce(c.account_name, '(not in the CRM)') AS account_name,
               s.flag, s.status, s.sent_on, s.age_days
        FROM action_status s
        LEFT JOIN cs_team o ON o.owner_id = s.routed_to
        LEFT JOIN crm c USING (account_id)
        WHERE s.action_status = 'Open' AND (s.routed_to = 'cs_ops' OR o.owner_type = 'pool')
        ORDER BY s.age_days DESC, manager, s.routed_to, s.account_id
    """).show(max_rows=100)

    print("\nNot measured yet: outcome (did the account recover after the call?).")
