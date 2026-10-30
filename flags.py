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

Flag 2, churn risk. Looks at accounts live for the full 90 days of data and
checks three signals:

  Utilisation falling   under 40% now, and lower each month for two months
  Breadth dropping      weekly active users 30%+ below their 90-day average, and
                        at least one whole user fewer (on a 2-person account a
                        normal quiet week is already "-48%")
  Errors rising         5+ ingestion errors in the last 30 days, more than before

One usage signal = Watch, two = Act. Rising errors only count next to a usage
signal (on their own they're noise), and they push Watch up to Act: falling
usage plus rising errors often means data stopped arriving, so the action asks
for a technical check first.

Plus Shelfware: live 60+ days, and the 30-day utilisation has never reached
40%. Nothing is falling, so the signals above stay quiet, but the customer
uses a fraction of what they bought and the renewal will come in smaller.
Usually the rollout never happened (other teams, data sources or use cases
still to come). Scale and Enterprise get an adoption review and a ramp plan;
Self-service has no CSM, so it gets the one-to-many version: an automated
use-case inspiration email (CS adoption content, not a plan-limit message).
Accounts already at Watch or Act keep that status.

Flag 3, expansion. Looks at accounts live for the full 90 days that are
heading over their contract:

  Volume signal    at 90%+ of contracted volume already, or at 80%+ and climbing
                   3+ points a month fast enough to pass 90% before renewal
  Breadth signal   active users up 25%+ in the last two months

Volume plus breadth = Act (real growth: the CSM investigates and builds the
growth case; Sales only comes in to sign). Volume alone = Watch: rising volume
with flat users is often over-tracking, so the action is a tracking check, not
an upsell.

This is a CSM cockpit, not an org-wide tool. Customer messaging triggered by
usage or plan limits belongs to Product, so churn and expansion never send
those: Self-service rows go to a Self-service report ("report") for analysis,
and Scale/Enterprise rows ask the pool or CSM to investigate. The exception is
Self-service Shelfware, which gets adoption content (use cases), never a
plan-limit or upgrade message.

Every row carries its reasons, who it is routed to and the action. Results are
saved to data/flags_activation.csv, data/flags_churn.csv,
data/flags_expansion.csv and data/flags_qbr.csv for the action log.

Flag 4, QBR (Enterprise only):

  QBR coming up   next QBR in the next 30 days: prepare it (python qbr.py builds
                  the facts pack)
  QBR overdue     live 4+ months, last QBR 120+ days ago (or never), and
                  nothing booked in the next 30 days: book one

Run:  python flags.py
"""

import sys

from cairnpath_db import connect
from settings import SETTINGS

sys.stdout.reconfigure(encoding="utf-8")

# All thresholds live in settings.toml
SCOPE_DAYS = SETTINGS["activation"]["scope_days"]               # only recent contracts
WINDOW_DAYS = SETTINGS["activation"]["window_days"]             # activation window after contract start
MIN_USERS = SETTINGS["activation"]["min_users"]                 # "activated" needs this many active users
SILENT_AFTER_DAYS = SETTINGS["activation"]["silent_after_days"] # no events after this many days = silent

CHURN_UTIL_BELOW = SETTINGS["churn"]["util_below"]
CHURN_BREADTH_DROP = SETTINGS["churn"]["breadth_drop"]
CHURN_BREADTH_MIN_USERS = SETTINGS["churn"]["breadth_min_users"]
CHURN_MIN_ERRORS = SETTINGS["churn"]["min_errors"]
RAMP_DAYS = SETTINGS["churn"]["ramp_days"]
# renewal this close: act now (used by Shelfware and expansion)
RENEWAL_SOON_MONTHS = SETTINGS["expansion"]["renewal_soon_months"]

EXP_AT_CAP = SETTINGS["expansion"]["at_cap"]
EXP_HIGH = SETTINGS["expansion"]["high"]
EXP_MIN_CLIMB = SETTINGS["expansion"]["min_climb"]
EXP_BREADTH_GROWTH = SETTINGS["expansion"]["breadth_growth"]

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
                       format('{{}} days since contract, only {{}} active {{}} in the first {WINDOW_DAYS} days',
                              days_since_contract, users_first_14d,
                              CASE WHEN users_first_14d = 1 THEN 'user' ELSE 'users' END)
               END AS reasons,
               -- who acts: nobody yet, the automation, the regional pool or the named CSM
               CASE
                   WHEN status = 'Activating'   THEN 'none'
                   WHEN tier = 'Self-service'   THEN 'automated'
                   WHEN tier = 'Scale'          THEN cs_pool
                   -- no CSM yet: CS ops gets one task, on the "No CSM assigned"
                   -- row below; this row waits for the new CSM to pick it up
                   WHEN csm_owner IS NULL       THEN 'none'
                   ELSE csm_owner
               END AS routed_to,
               CASE
                   WHEN status = 'Activating' THEN 'No action yet; check again tomorrow'
                   WHEN tier = 'Enterprise' AND csm_owner IS NULL
                       THEN format('Waiting for a CSM (see No CSM assigned); they then pick up this {{}} account',
                                   lower(status))
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
               -- if the first event is on the first day of data, the account was
               -- already active before our data starts: say so, don't show a fake date
               format('{{}}, {{}} users seen, no CRM record',
                      CASE WHEN min(e.event_date) = (SELECT min(event_date) FROM events)
                           THEN 'Active since before the data starts'
                           ELSE 'Active since ' || min(e.event_date)::VARCHAR END,
                      count(DISTINCT e.user_id)) AS reasons,
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
    SELECT routed_to, status, account_id, account_name, reasons, action
    FROM activation
    WHERE routed_to NOT IN ('none', 'automated')
    ORDER BY routed_to, status, account_id
""").show(max_rows=100, max_width=320)

con.sql("COPY (SELECT * FROM activation ORDER BY status, account_id) TO 'data/flags_activation.csv' (HEADER)")
print("\nSaved every row to data/flags_activation.csv")


# --- Flag 2: churn risk ---------------------------------------------------------

con.sql(f"""
    CREATE VIEW churn_trend AS
    WITH base AS (
        -- CRM accounts live for the whole 90-day window (we need two months of trend);
        -- monthly_usage already holds exactly those accounts
        SELECT c.*, a.as_of_date
        FROM crm c
        JOIN monthly_usage mu ON mu.account_id = c.account_id
        CROSS JOIN as_of a
    ),
    weekly AS (
        -- active users this week vs. the average week (13 weeks in 90 days)
        SELECT b.account_id,
               count(DISTINCT e.user_id) FILTER (WHERE e.event_date > b.as_of_date - 7) AS wau_now,
               count(DISTINCT e.user_id || '|' || CAST((b.as_of_date - e.event_date) // 7 AS VARCHAR)) / 13.0 AS wau_avg
        FROM base b
        JOIN events e ON e.account_id = b.account_id AND e.user_id IS NOT NULL
        GROUP BY b.account_id
    ),
    errors AS (
        SELECT b.account_id,
               count(e.event_id) FILTER (WHERE e.event_date > b.as_of_date - 30) AS err_now,
               count(e.event_id) FILTER (WHERE e.event_date >  b.as_of_date - 60
                                           AND e.event_date <= b.as_of_date - 30) AS err_before
        FROM base b
        LEFT JOIN events e ON e.account_id = b.account_id AND e.event_name = 'ingestion_error'
        GROUP BY b.account_id
    ),
    signals AS (
        SELECT b.account_id, b.account_name, b.tier, b.region, b.csm_owner, b.cs_pool,
               m.util_m1, m.util_m2, m.util_m3,
               coalesce(w.wau_now, 0) AS wau_now,
               coalesce(w.wau_avg, 0) AS wau_avg,
               r.err_now, r.err_before
        FROM base b
        JOIN monthly_usage m USING (account_id)
        LEFT JOIN weekly w USING (account_id)
        JOIN errors r USING (account_id)
    ),
    scored AS (
        SELECT *,
               util_m3 < {CHURN_UTIL_BELOW} AND util_m3 < util_m2 AND util_m2 < util_m1 AS util_falling,
               wau_avg > 0 AND wau_now <= (1 - {CHURN_BREADTH_DROP}) * wau_avg
                   AND wau_avg - wau_now >= {CHURN_BREADTH_MIN_USERS}                  AS breadth_dropping,
               err_now >= {CHURN_MIN_ERRORS} AND err_now > err_before                  AS errors_rising
        FROM signals
    ),
    totals AS (
        -- errors only raise the score: on their own they're noise (healthy
        -- accounts get a bad week now and then), so they need a usage signal
        SELECT *,
               util_falling::INT + breadth_dropping::INT                              AS usage_signals,
               util_falling::INT + breadth_dropping::INT
                   + CASE WHEN errors_rising AND (util_falling OR breadth_dropping) THEN 1 ELSE 0 END AS score
        FROM scored
    )
    SELECT account_id, account_name, tier, region,
           CASE WHEN score >= 2
                THEN 'Act' ELSE 'Watch' END AS status,
           -- the reasons: the numbers behind each signal that fired
           concat_ws('; ',
               CASE WHEN util_falling THEN
                   format('Utilisation {{}}%, then {{}}%, then {{}}% (falling two months running)',
                          util_m1::INT, util_m2::INT, util_m3::INT) END,
               CASE WHEN breadth_dropping THEN
                   format('{{}} weekly active {{}} vs. {{}} on average ({{}}%)',
                          wau_now, CASE WHEN wau_now = 1 THEN 'user' ELSE 'users' END,
                          round(wau_avg, 1), round((wau_now / wau_avg - 1) * 100)::INT) END,
               CASE WHEN errors_rising THEN
                   -- show both months as they are: "up from" made flat trends sound like spikes
                   format('{{}} ingestion errors in the last 30 days ({{}} the month before)',
                          err_now, err_before) END
           ) AS reasons,
           -- This is a CSM cockpit: usage-triggered customer messaging belongs to
           -- Product. Self-service has no CSM, so its rows go to the Self-service
           -- report for analysis rather than to an automated email.
           CASE
               WHEN tier = 'Self-service' THEN 'report'
               WHEN tier = 'Scale'        THEN cs_pool
               ELSE coalesce(csm_owner, 'cs_ops')
           END AS routed_to,
           CASE
               WHEN tier = 'Self-service' THEN
                   'Analyse in the Self-service report (look for patterns across accounts)'
               WHEN score < 2 THEN 'Watch: raise it at the next check-in'
               WHEN errors_rising THEN
                   'Technical check first (ingestion errors are rising), then talk to the customer about usage'
               WHEN tier = 'Scale'        THEN 'Reach out this week and offer a usage review'
               ELSE 'CSM calls this week: usage and breadth are both dropping'
           END AS action
    FROM totals
    WHERE usage_signals >= 1
""")

# Shelfware: low and flat, never ramped up. The trend signals above need a fall,
# so an account that was always low never trips them.
con.sql(f"""
    CREATE VIEW shelfware AS
    WITH live AS (
        SELECT c.*, a.as_of_date, f.go_live_date
        FROM crm c
        CROSS JOIN as_of a
        JOIN (SELECT account_id, min(event_date) AS go_live_date
              FROM events GROUP BY account_id) f ON f.account_id = c.account_id
        WHERE f.go_live_date <= a.as_of_date - {RAMP_DAYS}   -- had time to ramp up
    ),
    util AS (
        -- every 30-day utilisation we can compute for the account
        SELECT account_id,
               arg_max(util, event_date)       AS util_now,
               max(util)                       AS util_max,
               min(event_date) - 29            AS seen_since   -- the first 30-day window's start
        FROM daily_util_30d
        GROUP BY account_id
    )
    SELECT l.account_id, l.account_name, l.tier, l.region,
           'Shelfware' AS status,
           format('Never ramped up: utilisation {{}}% now and never above {{}}% since {{}}; '
                  || '{{}}M events a year bought; renewal {{}}',
                  u.util_now::INT, u.util_max::INT, strftime(u.seen_since, '%d %b'),
                  (l.contracted_events // 1000000)::INT, strftime(l.renewal_date::DATE, '%d %b %Y')) AS reasons,
           CASE WHEN l.tier = 'Self-service' THEN 'automated'     -- one-to-many: an email
                WHEN l.tier = 'Scale'        THEN l.cs_pool
                ELSE coalesce(l.csm_owner, 'cs_ops') END AS routed_to,
           CASE WHEN l.tier = 'Self-service' THEN
                    'Send the use-case inspiration email: how other teams use Cairnpath, '
                    || 'with links to the guides for each use case'
                -- renewal close: no time for a slow ramp plan, so the call is about the renewal itself
                WHEN (l.renewal_date::DATE - l.as_of_date) / 30.0 <= {RENEWAL_SOON_MONTHS} THEN
                    format('Renewal in {{}}: call this week. Agree what blocks the rollout and a '
                           || 'realistic volume for the renewal, so it is not a surprise cut',
                           -- under two weeks in days ("9 days"), else in weeks ("8 weeks")
                           CASE WHEN l.renewal_date::DATE - l.as_of_date < 14
                                THEN (l.renewal_date::DATE - l.as_of_date)::VARCHAR || ' days'
                                ELSE ((l.renewal_date::DATE - l.as_of_date) // 7)::VARCHAR || ' weeks' END)
                WHEN l.tier = 'Scale' THEN
                    'Offer an adoption review: which teams and data sources are still to come? '
                    || 'Invite them to the next advanced workshop'
                ELSE 'CSM books an adoption review: what is blocking the rollout (teams, data '
                     || 'sources, use cases)? Agree a ramp plan well before the renewal'
           END AS action
    FROM live l
    JOIN util u USING (account_id)
    WHERE u.util_max < {CHURN_UTIL_BELOW}
""")

# One churn list: the trend signals first; Shelfware only where they found nothing
con.sql("""
    CREATE VIEW churn AS
    SELECT * FROM churn_trend
    UNION ALL
    SELECT * FROM shelfware WHERE account_id NOT IN (SELECT account_id FROM churn_trend)
""")

print("\n\nChurn risk (Watch/Act: live for the full 90 days; Shelfware: live 60+ days, never ramped)")

print("\n3. Accounts per status and tier")
con.sql("""
    SELECT status,
           count(*) FILTER (WHERE tier = 'Enterprise')   AS "Enterprise",
           count(*) FILTER (WHERE tier = 'Scale')        AS "Scale",
           count(*) FILTER (WHERE tier = 'Self-service') AS "Self-service",
           count(*)                                      AS total
    FROM churn
    GROUP BY status
    ORDER BY status
""").show()

print("\n4. Churn work queue: everything that needs a human, by owner")
con.sql("""
    SELECT routed_to, status, account_id, account_name, reasons, action
    FROM churn
    WHERE routed_to NOT IN ('none', 'automated', 'report')
    ORDER BY routed_to, status, account_id
""").show(max_rows=100, max_width=360)

con.sql("COPY (SELECT * FROM churn ORDER BY status, account_id) TO 'data/flags_churn.csv' (HEADER)")
print("\nSaved every row to data/flags_churn.csv")


# --- Flag 3: expansion ----------------------------------------------------------

con.sql(f"""
    CREATE VIEW expansion AS
    WITH signals AS (
        SELECT c.account_id, c.account_name, c.tier, c.region, c.csm_owner, c.cs_pool,
               m.util_m1, m.util_m3, m.users_m1, m.users_m3,
               (m.util_m3 - m.util_m1) / 2.0             AS climb_per_month,
               (c.renewal_date - a.as_of_date) / 30.0     AS months_to_renewal
        FROM crm c
        JOIN monthly_usage m USING (account_id)
        CROSS JOIN as_of a
    ),
    scored AS (
        SELECT *,
               -- a straight-line forecast only counts when the account is already
               -- high and clearly climbing: small noisy drifts projected over months
               -- would otherwise "reach the cap" on paper
               util_m3 >= {EXP_AT_CAP}
                   OR (util_m3 >= {EXP_HIGH} AND climb_per_month >= {EXP_MIN_CLIMB}
                       AND util_m3 + climb_per_month * months_to_renewal >= {EXP_AT_CAP}) AS volume_signal,
               users_m1 > 0 AND users_m3 >= (1 + {EXP_BREADTH_GROWTH}) * users_m1     AS breadth_signal,
               ceil(({EXP_AT_CAP} - util_m3) / nullif(climb_per_month, 0))             AS months_to_cap
        FROM signals
    )
    SELECT account_id, account_name, tier, region,
           CASE WHEN breadth_signal THEN 'Act' ELSE 'Watch' END AS status,
           concat_ws('; ',
               CASE
                   WHEN util_m3 >= 100 THEN
                       format('Already over the contract: {{}}% of contracted volume', util_m3::INT)
                   WHEN util_m3 >= {EXP_AT_CAP} THEN
                       format('Already at {{}}% of contracted volume', util_m3::INT)
                   ELSE
                       format('Utilisation {{}}% and climbing {{}} points a month: about {{}} months to {EXP_AT_CAP}%, renewal in {{}}',
                              util_m3::INT, round(climb_per_month)::INT, months_to_cap::INT,
                              round(months_to_renewal)::INT)
               END,
               CASE WHEN breadth_signal THEN
                   format('{{}} active users this month vs. {{}} two months ago (+{{}}%)',
                          users_m3, users_m1, round((users_m3 / users_m1 - 1) * 100)::INT) END
           ) AS reasons,
           -- expansion stays with CS: the CSM (or pool) investigates and builds the
           -- case, Sales signs. Plan-limit messaging to customers belongs to Product,
           -- so Self-service rows go to the Self-service report for analysis.
           CASE
               WHEN tier = 'Self-service' THEN 'report'
               WHEN tier = 'Scale'        THEN cs_pool
               ELSE coalesce(csm_owner, 'cs_ops')
           END AS routed_to,
           CASE
               WHEN tier = 'Self-service' THEN
                   'Analyse in the Self-service report (look for patterns across accounts)'
               WHEN NOT breadth_signal THEN
                   'Investigate the tracking first: volume is rising but users are not (possible over-tracking)'
               WHEN util_m3 >= 100 THEN
                   'Already over the contract: investigate what drives the growth, then raise the overage and build the upgrade case'
               WHEN months_to_renewal <= {RENEWAL_SOON_MONTHS} THEN
                   'Renewal within 3 months: confirm the growth is real (teams, use cases), then bring the case into the renewal'
               ELSE
                   'Investigate what drives the growth (teams, use cases), then build the upgrade case before they hit the cap'
           END AS action
    FROM scored
    WHERE volume_signal
""")

print("\n\nExpansion (accounts live for the full 90 days)")

print("\n5. Accounts per status and tier")
con.sql("""
    SELECT status,
           count(*) FILTER (WHERE tier = 'Enterprise')   AS "Enterprise",
           count(*) FILTER (WHERE tier = 'Scale')        AS "Scale",
           count(*) FILTER (WHERE tier = 'Self-service') AS "Self-service",
           count(*)                                      AS total
    FROM expansion
    GROUP BY status
    ORDER BY status
""").show()

print("\n6. Expansion work queue: everything that needs a human, by owner")
con.sql("""
    SELECT routed_to, status, account_id, account_name, reasons, action
    FROM expansion
    WHERE routed_to NOT IN ('none', 'automated', 'report')
    ORDER BY routed_to, status, account_id
""").show(max_rows=100, max_width=360)

con.sql("COPY (SELECT * FROM expansion ORDER BY status, account_id) TO 'data/flags_expansion.csv' (HEADER)")
print("\nSaved every row to data/flags_expansion.csv")


# --- Value at stake ---------------------------------------------------------------
# What each churn or expansion row is worth, so the work can be sorted by
# value. It guides the work; it's never a CSM's credit.
#   ARR at risk   Act: the whole ARR (usage and users falling: they may leave).
#                 Watch, Shelfware: the unused part, ARR x (100% - utilisation):
#                 customers cut their commitment to what they use at renewal.
#   Opportunity   forecast utilisation at renewal above 100% x ARR, a straight
#                 line from the last two months (capped); Watch counts at a share.
MONEY = SETTINGS["money"]
con.sql(f"""
    CREATE VIEW account_value AS
    WITH base AS (
        SELECT c.account_id, c.arr, c.arr_estimated, u.utilisation_pct AS util,
               m.util_m1, m.util_m3, (c.renewal_date - a.as_of_date) / 30.0 AS months_to_renewal
        FROM crm c
        CROSS JOIN as_of a
        LEFT JOIN utilisation u USING (account_id)
        LEFT JOIN monthly_usage m USING (account_id)
    ),
    risk AS (
        SELECT b.account_id, 'churn' AS flag, ch.status, b.arr, b.arr_estimated,
               round(CASE WHEN ch.status = 'Act' THEN b.arr
                          ELSE b.arr * greatest(0, 100 - least(100, coalesce(b.util, 0))) / 100 END, -2) AS amount,
               CASE WHEN ch.status = 'Act' THEN 'Whole ARR: usage and users are falling'
                    ELSE format('Unused part: {{}}% of the contract in use', coalesce(b.util, 0)::INT) END AS basis
        FROM churn ch JOIN base b USING (account_id)
    ),
    forecast AS (
        SELECT b.*, ex.status,
               least({MONEY['max_forecast_pct']},
                     b.util_m3 + greatest(0, (b.util_m3 - b.util_m1) / 2.0) * greatest(0, b.months_to_renewal)) AS util_at_renewal
        FROM expansion ex JOIN base b USING (account_id)
    ),
    upside AS (
        SELECT account_id, 'expansion' AS flag, status, arr, arr_estimated,
               round(arr * greatest(0, util_at_renewal - 100) / 100
                     * CASE WHEN status = 'Watch' THEN {MONEY['watch_opportunity_share']} ELSE 1 END, -2) AS amount,
               format('Forecast {{}}% of the contract at renewal{{}}', round(util_at_renewal)::INT,
                      CASE WHEN status = 'Watch' THEN ', counted at {round(MONEY['watch_opportunity_share'] * 100)}% (Watch)' ELSE '' END) AS basis
        FROM forecast
    )
    SELECT * FROM risk UNION ALL SELECT * FROM upside
""")
print("\n\nValue at stake (EUR), by flag and status")
con.sql("""
    SELECT flag, status, count(*) AS accounts, sum(amount)::BIGINT AS amount, sum(arr)::BIGINT AS arr
    FROM account_value GROUP BY flag, status ORDER BY flag, status
""").show()
con.sql("COPY (SELECT * FROM account_value ORDER BY flag, amount DESC) TO 'data/account_value.csv' (HEADER)")
print("Saved every row to data/account_value.csv")


# --- Flag 4: QBR ---------------------------------------------------------------

con.sql("""
    CREATE VIEW qbr AS
    SELECT account_id, account_name, tier, region,
           CASE WHEN qbr_coming_up THEN 'QBR coming up' ELSE 'QBR overdue' END AS status,
           CASE
               WHEN qbr_coming_up THEN
                   format('QBR on {} (in {} days); last QBR {}', next_qbr_date::VARCHAR, days_to_next_qbr,
                          coalesce(days_since_last_qbr::VARCHAR || ' days ago', 'none yet'))
               WHEN last_qbr_date IS NULL THEN
                   format('No QBR yet, {} days after contract start; nothing booked',
                          (SELECT as_of_date FROM as_of) - contract_start)
               ELSE
                   format('Last QBR {} days ago ({}); {}', days_since_last_qbr, last_qbr_date::VARCHAR,
                          CASE WHEN next_qbr_date IS NULL THEN 'nothing booked'
                               ELSE 'next one booked for ' || next_qbr_date::VARCHAR END)
           END AS reasons,
           coalesce(csm_owner, 'cs_ops') AS routed_to,
           CASE
               WHEN csm_owner IS NULL THEN 'Assign a CSM first; they then own this QBR'
               WHEN qbr_coming_up THEN 'Prepare the QBR: run python qbr.py for the facts pack'
               ELSE 'Book a QBR'
           END AS action
    FROM qbr_schedule
    WHERE qbr_coming_up OR qbr_overdue
""")

print("\n\nQBR (Enterprise accounts)")

print("\n7. QBRs coming up and overdue, by CSM")
con.sql("""
    SELECT routed_to, status, account_id, account_name, reasons, action
    FROM qbr
    ORDER BY routed_to, status, account_id
""").show(max_rows=100, max_width=300)

con.sql("COPY (SELECT * FROM qbr ORDER BY status, account_id) TO 'data/flags_qbr.csv' (HEADER)")
print("\nSaved every row to data/flags_qbr.csv")
