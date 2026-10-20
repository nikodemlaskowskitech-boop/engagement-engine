"""
Cairnpath flags: who needs attention, why, and who should act.

Flag 1, activation. Looks at accounts whose contract started in the last
45 days and gives each one a status:

  Activated    SDK installed and 2+ active users within 14 days of contract start
  Activating   under 14 days, not all signals yet (no action, check again tomorrow)
  Stalled      14+ days, SDK in but fewer than 2 users
  Silent       10+ days since contract, no events at all

Plus two checks on our own data, which go to CS ops rather than the customer:

  Handoff gap      events from an account that has no CRM record
  No CSM assigned  an Enterprise account in scope with no csm_owner

Every row carries its reasons, who it is routed to and the action, and the
results are saved to data/flags_activation.csv for the action log.

Run:  python flags.py
"""

import sys

from cairnpath_db import connect

sys.stdout.reconfigure(encoding="utf-8")

SCOPE_DAYS = 45        # only contracts from the last 45 days
WINDOW_DAYS = 14       # activation must happen within 14 days of contract start
MIN_USERS = 2          # "activated" needs at least this many active users
SILENT_AFTER_DAYS = 10 # no events at all after this many days = silent

con = connect()

con.sql(f"""
    CREATE VIEW activation AS
    WITH in_scope AS (
        -- CRM accounts that signed in the last {SCOPE_DAYS} days
        SELECT c.*, a.as_of_date, a.as_of_date - c.contract_start AS days_since_contract
        FROM crm c, as_of a
        WHERE a.as_of_date - c.contract_start <= {SCOPE_DAYS}
    ),
    signals AS (
        -- what each in-scope account did in its first {WINDOW_DAYS} days
        SELECT s.account_id,
               min(e.event_date) FILTER (WHERE e.event_name = 'sdk_installed')     AS sdk_date,
               count(DISTINCT e.user_id)
                   FILTER (WHERE e.event_date < s.contract_start + {WINDOW_DAYS})  AS users_first_14d,
               count(DISTINCT e.user_id)                                           AS users_so_far,
               count(e.event_id)                                                   AS events_so_far
        FROM in_scope s
        LEFT JOIN events e ON e.account_id = s.account_id
        GROUP BY s.account_id
    ),
    statuses AS (
        SELECT s.account_id, s.account_name, s.tier, s.region, s.csm_owner, s.cs_pool,
               s.contract_start, s.days_since_contract,
               g.sdk_date, g.users_first_14d, g.users_so_far,
               CASE
                   WHEN g.events_so_far = 0 AND s.days_since_contract >= {SILENT_AFTER_DAYS}
                       THEN 'Silent'
                   WHEN g.sdk_date < s.contract_start + {WINDOW_DAYS}
                        AND g.users_first_14d >= {MIN_USERS}
                       THEN 'Activated'
                   WHEN s.days_since_contract < {WINDOW_DAYS}
                       THEN 'Activating'
                   ELSE 'Stalled'
               END AS status
        FROM in_scope s
        JOIN signals g USING (account_id)
    ),
    customer_flags AS (
        SELECT account_id, account_name, tier, region, status,
               -- the reasons, in words a CSM would use
               CASE status
                   WHEN 'Silent' THEN
                       format('{{}} days since contract, no events at all', days_since_contract)
                   WHEN 'Activated' THEN
                       format('SDK installed on day {{}}, {{}} users active in the first {WINDOW_DAYS} days',
                              sdk_date - contract_start, users_first_14d)
                   WHEN 'Activating' THEN
                       format('{{}} days since contract, {{}} users so far', days_since_contract, users_so_far)
                   ELSE
                       format('{{}} days since contract, only {{}} active user(s) in the first {WINDOW_DAYS} days',
                              days_since_contract, users_first_14d)
               END AS reasons,
               -- who acts: nobody yet, the automation, the regional pool or the named CSM
               CASE
                   WHEN status = 'Activating'   THEN 'none'
                   WHEN tier = 'Self-service'   THEN 'automated'
                   WHEN tier = 'Scale'          THEN cs_pool
                   ELSE coalesce(csm_owner, 'cs_ops')
               END AS routed_to,
               CASE
                   WHEN status = 'Activating' THEN 'No action yet; check again tomorrow'
                   WHEN status = 'Activated' AND tier = 'Self-service' THEN 'Send welcome email and self-onboarding pack'
                   WHEN status = 'Activated' AND tier = 'Scale'        THEN 'Send welcome email and onboarding workshop invite'
                   WHEN status = 'Activated'                           THEN 'CSM books a kickoff call'
                   WHEN status = 'Stalled'   AND tier = 'Self-service' THEN 'Send "invite your team" email'
                   WHEN status = 'Stalled'                             THEN 'Reach out: only one person is using it'
                   WHEN status = 'Silent'    AND tier = 'Self-service' THEN 'Send "need help getting started?" email; visible to CS ops'
                   WHEN status = 'Silent'    AND tier = 'Scale'        THEN 'Reach out: signed but never started'
                   ELSE 'Urgent: Enterprise customer signed but never started'
               END AS action
        FROM statuses
    ),
    handoff_gaps AS (
        -- events from accounts the CRM doesn't know about
        SELECT e.account_id, NULL AS account_name, NULL AS tier, NULL AS region,
               'Handoff gap' AS status,
               format('Active since {{}}, {{}} users seen, no CRM record',
                      min(e.event_date)::VARCHAR, count(DISTINCT e.user_id)) AS reasons,
               'cs_ops' AS routed_to,
               'Create or fix the CRM record (tier, contract, owner); no customer email until then' AS action
        FROM events e
        WHERE e.account_id NOT IN (SELECT account_id FROM crm)
        GROUP BY e.account_id
    ),
    missing_csm AS (
        -- new Enterprise accounts nobody has been assigned to
        SELECT account_id, account_name, tier, region,
               'No CSM assigned' AS status,
               format('Enterprise, contract started {{}} ({{}} days ago), csm_owner is empty',
                      contract_start::VARCHAR, days_since_contract) AS reasons,
               'cs_ops' AS routed_to,
               'Assign a CSM' AS action
        FROM statuses
        WHERE tier = 'Enterprise' AND csm_owner IS NULL
    )
    SELECT * FROM customer_flags
    UNION ALL SELECT * FROM handoff_gaps
    UNION ALL SELECT * FROM missing_csm
""")

print(f"\nActivation flag, as of {con.sql('SELECT as_of_date FROM as_of').fetchone()[0]}")

print("\n1. Accounts per status and tier (handoff gaps have no tier: they're not in the CRM)")
con.sql("""
    SELECT status,
           count(*) FILTER (WHERE tier = 'Enterprise')   AS "Enterprise",
           count(*) FILTER (WHERE tier = 'Scale')        AS "Scale",
           count(*) FILTER (WHERE tier = 'Self-service') AS "Self-service",
           count(*) FILTER (WHERE tier IS NULL)          AS "No tier",
           count(*)                                      AS total
    FROM activation
    GROUP BY status
    ORDER BY CASE status WHEN 'Silent' THEN 1 WHEN 'Stalled' THEN 2 WHEN 'No CSM assigned' THEN 3
                         WHEN 'Handoff gap' THEN 4 WHEN 'Activating' THEN 5 ELSE 6 END
""").show()

print("\n2. Work queue: everything that needs a human, by owner")
con.sql("""
    SELECT routed_to, status, account_id, account_name, reasons
    FROM activation
    WHERE routed_to NOT IN ('none', 'automated')
    ORDER BY routed_to, status, account_id
""").show(max_rows=100, max_width=200)

con.sql("COPY (SELECT * FROM activation ORDER BY status, account_id) TO 'data/flags_activation.csv' (HEADER)")
print("\nSaved every row to data/flags_activation.csv")
