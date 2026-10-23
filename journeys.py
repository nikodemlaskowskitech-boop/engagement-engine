"""
Journeys: rules that react to the last touch, not just to usage.

The flags say "this account needs attention". Journeys look at the CS
timeline (data/engagement/) and ask what has already happened, and whether
it worked. The rules:

  Email never opened      a Self-service account is stalled or silent, and its
                          getting-started email (5+ days ago) was never opened.
                          Another email to the same address won't help: the
                          contact is probably wrong or dead. CS ops checks it.

  No meeting in 30 days   an account at churn or expansion Act (Scale or
                          Enterprise) with no meeting in the last 30 days,
                          or none at all, and Act for 14+ days (its 30-day
                          utilisation crossed the line at least 14 days ago:
                          an account that turned Act yesterday is nobody's
                          failure yet). Escalated to the owner's manager,
                          who gets one Slack message listing their team's
                          accounts, not a task per account. Not when a
                          QBR is booked in the next 30 days: that meeting
                          is the plan (QBR first, see actions.py). Only
                          once: after a QBR has slipped, a newly booked one
                          no longer holds the escalation back.

  Follow-up not picked up  a Scale follow-up named to the person who took
                          the first task, still not claimed or done after 5
                          days. Their manager gets an approval: hand it back
                          to the pool (button in Slack). Then anyone in the
                          pool can take it (actions.py).

  Reassigned, not picked up  a manager gave a task to someone in the cockpit
                          and it's still not claimed or done 5 days later:
                          back to that manager to check or reassign.

Rows have the same columns as the flags and are saved to
data/flags_journey.csv, so actions.py picks them up and does each one once.

Run flags.py first (this reads its results), then:  python journeys.py
"""

import sys

from cairnpath_db import connect
from settings import SETTINGS

sys.stdout.reconfigure(encoding="utf-8")

UNOPENED_AFTER_DAYS = SETTINGS["journeys"]["unopened_after_days"]   # all in settings.toml
NO_MEETING_DAYS = SETTINGS["journeys"]["no_meeting_days"]
ACT_FOR_DAYS = SETTINGS["journeys"]["act_for_days"]
HANDBACK_DAYS = SETTINGS["follow_ups"]["handback_after_days"]

con = connect()

con.sql(f"""
    CREATE VIEW journey AS
    WITH activation AS (SELECT * FROM read_csv_auto('data/flags_activation.csv')),
    acts AS (
        SELECT *, 'churn' AS flag FROM read_csv_auto('data/flags_churn.csv') WHERE status = 'Act'
        UNION ALL
        SELECT *, 'expansion' AS flag FROM read_csv_auto('data/flags_expansion.csv') WHERE status = 'Act'
    ),
    act_signal AS (
        -- the day each Act account first crossed its flag's line
        SELECT f.account_id, f.flag,
               min(u.event_date) FILTER (WHERE (f.flag = 'churn' AND u.util < 40)
                                            OR (f.flag = 'expansion' AND u.util >= 80)) AS signal_date
        FROM acts f
        JOIN daily_util_30d u USING (account_id)
        GROUP BY f.account_id, f.flag
    ),
    email_never_opened AS (
        SELECT f.account_id, f.account_name, f.tier, f.region,
               'Email never opened' AS status,
               format('{{}}; getting-started email sent {{}} ({{}} days ago), never opened',
                      f.status, t.getting_started_sent::VARCHAR,
                      a.as_of_date - t.getting_started_sent) AS reasons,
               'cs_ops' AS routed_to,
               'Check the contact in the CRM (wrong person or dead address?), '
                   || 'then send the getting-started email to the right person' AS action
        FROM activation f
        JOIN last_touch t USING (account_id)
        CROSS JOIN as_of a
        WHERE f.tier = 'Self-service'
          AND f.status IN ('Stalled', 'Silent')
          AND a.as_of_date - t.getting_started_sent >= {UNOPENED_AFTER_DAYS}
          AND NOT t.getting_started_opened
    ),
    no_meeting AS (
        SELECT f.account_id, f.account_name, f.tier, f.region,
               'No meeting in 30 days' AS status,
               format('{{}} Act since {{}}, owner {{}}; {{}}',
                      f.flag, coalesce(s.signal_date::VARCHAR, 'unknown'), coalesce(o.owner_name, f.routed_to),
                      CASE WHEN t.last_meeting_date IS NULL THEN 'no meeting in the 90 days of history'
                           ELSE format('last meeting {{}} ({{}} days ago)', t.last_meeting_date::VARCHAR,
                                       a.as_of_date - t.last_meeting_date) END) AS reasons,
               o.manager AS routed_to,
               format('Escalation: agree a plan with {{}} this week', coalesce(o.owner_name, f.routed_to))
                   AS action
        FROM acts f
        JOIN cs_team o ON o.owner_id = f.routed_to       -- only accounts with a CSM or a pool
        LEFT JOIN last_touch t ON t.account_id = f.account_id
        LEFT JOIN act_signal s ON s.account_id = f.account_id AND s.flag = f.flag
        CROSS JOIN as_of a
        WHERE (t.last_meeting_date IS NULL OR a.as_of_date - t.last_meeting_date > {NO_MEETING_DAYS})
          -- no signal date (Act from users and errors, not utilisation): keep it
          AND (s.signal_date IS NULL OR a.as_of_date - s.signal_date >= {ACT_FOR_DAYS})
          -- QBR first: a QBR in the next 30 days is the meeting, no escalation;
          -- unless an earlier QBR for this account already slipped (qbr_slips)
          AND f.account_id NOT IN (SELECT account_id FROM qbr_schedule WHERE qbr_coming_up
                                   AND account_id NOT IN (SELECT account_id FROM qbr_slips))
    )
    ,
    handback AS (
        -- a Scale follow-up goes to the person who took the first task; if they
        -- haven't picked it up in 5 days (holiday, left, swamped), their manager
        -- approves handing it back to the pool. A person decides, not a timer.
        SELECT s.account_id, c.account_name, c.tier, c.region,
               'Follow-up not picked up' AS status,
               format('{{}} {{}} sent {{}} ({{}} days ago) to the person who took the first task; '
                      || 'not claimed or done since', s.flag, s.status, s.sent_on::VARCHAR, s.age_days) AS reasons,
               o.manager AS routed_to,
               format('Approve handing it back to {{}}, so anyone there can take it', o.owner_name) AS action
        FROM action_status s                                -- cairnpath_db.py
        JOIN cs_team o ON o.owner_id = s.routed_to AND o.owner_type = 'pool'
        LEFT JOIN crm c USING (account_id)
        WHERE s.status LIKE '%(follow-up)' AND s.action LIKE '%For <@%'   -- named to a person
          AND s.action_status = 'Open' AND s.age_days >= {HANDBACK_DAYS}
    )
    ,
    reassigned_waiting AS (
        -- a manager gave a task to someone who hasn't picked it up in 5 days:
        -- back to that manager, to check or give it to someone else
        SELECT s.account_id, c.account_name, c.tier, c.region,
               'Reassigned, not picked up' AS status,
               format('{{}} {{}} given to {{}} {{}} days ago; not claimed or done since',
                      s.flag, s.status, o.owner_name, s.age_days) AS reasons,
               s.assigned_by AS routed_to,
               format('Check with {{}}, or give it to someone else in the cockpit', o.owner_name) AS action
        FROM action_status s                                -- cairnpath_db.py
        JOIN cs_team o ON o.owner_id = s.routed_to
        LEFT JOIN crm c USING (account_id)
        WHERE s.assigned_by IS NOT NULL
          AND s.action_status = 'Open' AND s.age_days >= {HANDBACK_DAYS}
    )
    SELECT * FROM email_never_opened
    UNION ALL
    SELECT * FROM no_meeting
    UNION ALL
    SELECT * FROM handback
    UNION ALL
    SELECT * FROM reassigned_waiting
""")

print("\nJourneys: what happened since the flag, and did it work?\n")
print("1. Rows per journey and who gets them")
con.sql("""
    SELECT status, routed_to, count(*) AS accounts
    FROM journey
    GROUP BY ALL
    ORDER BY status, routed_to
""").show()

print("\n2. Every row")
con.sql("""
    SELECT routed_to, status, account_id, account_name, reasons
    FROM journey
    ORDER BY routed_to, status, account_id
""").show(max_rows=100, max_width=250)

# Work the rows out first, then write: the journey view reads the old
# flags_journey.csv (through action_status), and Windows won't replace a file
# that is still open for reading
con.sql("CREATE TABLE journey_rows AS SELECT * FROM journey ORDER BY status, account_id")
con.sql("COPY journey_rows TO 'data/flags_journey.csv' (HEADER)")
print("\nSaved every row to data/flags_journey.csv (actions.py picks it up)")
