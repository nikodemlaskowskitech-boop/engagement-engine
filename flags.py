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

Data missing (24 Nov 2026): gaps in one account's own data. A missing or
unreadable contract volume, renewal date or pool, or a volume feed that stopped
while product events keep coming. The flags that need the value don't guess:
the account shows the gap instead (data/flags_data.csv), to the data's owner
([data_quality]), and to the account's owner too when the gap hides a risk we
can still see (users falling, errors rising, an open churn or expansion row).

Run:  python flags.py
"""

import sys

from cairnpath_db import connect
from settings import SETTINGS, out_sql

sys.stdout.reconfigure(encoding="utf-8")

# All thresholds live in settings.toml
SCOPE_DAYS = SETTINGS["activation"]["scope_days"]               # only recent contracts
WINDOW_DAYS = SETTINGS["activation"]["window_days"]             # activation window after contract start
MIN_USERS = SETTINGS["activation"]["min_users"]                 # "activated" needs this many active users
SILENT_AFTER_DAYS = SETTINGS["activation"]["silent_after_days"] # no events after this many days = silent
RENEWAL_SEEN_DAYS = SETTINGS["activation"]["renewal_seen_days"] # events before the term = renewing, not onboarding

CHURN_UTIL_BELOW = SETTINGS["churn"]["util_below"]
CHURN_BREADTH_DROP = SETTINGS["churn"]["breadth_drop"]
CHURN_BREADTH_MIN_USERS = SETTINGS["churn"]["breadth_min_users"]
CHURN_MIN_ERRORS = SETTINGS["churn"]["min_errors"]
RAMP_DAYS = SETTINGS["churn"]["ramp_days"]
# renewal this close: act now (used by Shelfware and expansion)
RENEWAL_SOON_MONTHS = SETTINGS["expansion"]["renewal_soon_months"]

DQ = SETTINGS["data_quality"]
GR = SETTINGS["growth"]
FEED_GAP_DAYS = DQ["feed_gap_days"]
CONTRACT = SETTINGS["utilisation"]["contract_column"]
USAGE_EVENT = SETTINGS["utilisation"]["usage_event"]

EXP_AT_CAP = SETTINGS["expansion"]["at_cap"]
EXP_HIGH = SETTINGS["expansion"]["high"]
EXP_MIN_CLIMB = SETTINGS["expansion"]["min_climb"]
EXP_BREADTH_GROWTH = SETTINGS["expansion"]["breadth_growth"]

# --- Value at stake ---------------------------------------------------------------
# What each churn or expansion row is worth, so the work can be sorted by
# value. It guides the work; it's never a CSM's credit.
#   ARR at risk   Act: the whole ARR (usage and users falling: they may leave).
#                 Watch, Shelfware: the unused part, ARR x (100% - utilisation):
#                 customers cut their commitment to what they use at renewal.
#   Opportunity   forecast utilisation at renewal above 100% x ARR, a straight
#                 line from the last two months (capped); Watch counts at a share.
MONEY = SETTINGS["money"]


def at_risk_sql():
    """[money.at_risk] in settings -> the SQL for each churn status's amount and its basis."""
    amount, basis = [], []
    for status, rule in MONEY["at_risk"].items():
        if rule == "unused":
            amount.append(f"WHEN '{status}' THEN b.arr * greatest(0, 100 - least(100, coalesce(b.util, 0))) / 100")
            basis.append(f"WHEN '{status}' THEN format('Unused part: {{}}% of the contract in use', coalesce(b.util, 0)::INT)")
        else:
            share = float(rule)
            amount.append(f"WHEN '{status}' THEN b.arr * {share}")
            # the status's meaning, not one route to it: Act can also be one usage signal plus rising
            # ingestion errors, with users flat (16 Nov 2026 review: "users are falling" on flat users)
            basis.append(f"WHEN '{status}' THEN '{'Whole ARR' if share == 1 else f'{share:.0%} of ARR'}: at Act, the customer may leave'"
                         if status == "Act" else f"WHEN '{status}' THEN '{share:.0%} of ARR'")
    return ("CASE ch.status " + " ".join(amount) + " ELSE 0 END",
            "CASE ch.status " + " ".join(basis) + " END")


RISK_AMOUNT, RISK_BASIS = at_risk_sql()


def add_views(con):
    """Every flag as a view (activation, churn, shelfware, expansion, account_value, qbr).
    main() saves today's rows; history.py replays them as of past weeks on the same
    connection, so the history and today use exactly the same rules."""
    con.sql(f"""
        CREATE VIEW activation AS
        WITH in_scope AS (
            -- CRM accounts that signed in the last {SCOPE_DAYS} days: their first contract,
            -- not a renewal (a customer since earlier, or one already sending events a
            -- month before this term began, onboarded long ago; found by the year of data)
            SELECT c.*, a.as_of_date, a.as_of_date - c.contract_start AS days_since_contract
            FROM crm c, as_of a
            WHERE a.as_of_date - c.contract_start <= {SCOPE_DAYS}
              AND coalesce(c.customer_since, c.contract_start) >= c.contract_start
              AND NOT EXISTS (SELECT 1 FROM events e WHERE e.account_id = c.account_id
                              AND e.event_date < c.contract_start - {RENEWAL_SEEN_DAYS})
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
                       WHEN tier = 'Scale'          THEN coalesce(cs_pool, 'cs_ops')
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
            WHERE e.account_id NOT IN (SELECT account_id FROM crm_all)   -- a churned account isn't a gap
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

    # The volume feed stopped while the product didn't: daily_event_volume missing for
    # FEED_GAP_DAYS+ days, product events still arriving. Utilisation would read as a fall
    # (quiet days count as zero), so the usage rules skip these accounts and data_gaps says why.
    con.sql(f"""
        CREATE VIEW feed_gaps AS
        SELECT e.account_id, a.as_of_date,
               max(e.event_date) FILTER (WHERE e.event_name = '{USAGE_EVENT}') AS last_volume,
               max(e.event_date) FILTER (WHERE e.event_type = 'product')       AS last_product
        FROM events e, as_of a
        GROUP BY e.account_id, a.as_of_date
        HAVING last_volume <= a.as_of_date - {FEED_GAP_DAYS}
           AND last_product > a.as_of_date - {FEED_GAP_DAYS}
    """)

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
            -- active users this week vs. the average week (view weekly_users, cairnpath_db.py)
            SELECT w.* FROM weekly_users w JOIN base b USING (account_id)
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
                   util_m3 < {CHURN_UTIL_BELOW} AND util_m3 < util_m2 AND util_m2 < util_m1
                       AND account_id NOT IN (SELECT account_id FROM feed_gaps)               AS util_falling,
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
                   WHEN tier = 'Scale'        THEN coalesce(cs_pool, 'cs_ops')
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
                      (l.contracted_events // 1000000)::INT,
                      coalesce(strftime(l.renewal_date::DATE, '%d %b %Y'), 'date unknown')) AS reasons,
               CASE WHEN l.tier = 'Self-service' THEN 'automated'     -- one-to-many: an email
                    WHEN l.tier = 'Scale'        THEN coalesce(l.cs_pool, 'cs_ops')
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
          AND l.account_id NOT IN (SELECT account_id FROM feed_gaps)   -- the feed stopped: not their usage
    """)

    con.sql("""
        CREATE VIEW churn AS
        SELECT * FROM churn_trend
        UNION ALL
        SELECT * FROM shelfware WHERE account_id NOT IN (SELECT account_id FROM churn_trend)
    """)

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
                           -- "1 month", "2 months": the unit said, singular when it's one (20 Nov 2026)
                           format('Utilisation {{}}% and climbing {{}} points a month: about {{}} to {EXP_AT_CAP}%, renewal in {{}}',
                                  util_m3::INT, round(climb_per_month)::INT,
                                  months_to_cap::INT || CASE WHEN months_to_cap::INT = 1 THEN ' month' ELSE ' months' END,
                                  round(months_to_renewal)::INT || CASE WHEN round(months_to_renewal)::INT = 1 THEN ' month' ELSE ' months' END)
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
                   WHEN tier = 'Scale'        THEN coalesce(cs_pool, 'cs_ops')
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
          AND account_id NOT IN (SELECT account_id FROM feed_gaps)     -- the feed stopped: volume unknown
    """)

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
                   round({RISK_AMOUNT}, -2) AS amount,     -- rules: [money.at_risk] in settings.toml
                   {RISK_BASIS} AS basis
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

    # Gaps in an account's own data: the flags that need the missing value can't be
    # computed, so the account shows "data missing" here instead of a wrong flag (or none).
    # One row per gap to the data's owner ([data_quality]: CRM fields -> CS ops, the volume
    # feed -> data engineering), plus one row to the account's owner when the gap hides a
    # risk we can still see: users falling, ingestion errors rising, or an open churn or
    # expansion row (the missing value could make it worse, or move the renewal).
    con.sql(f"""
        CREATE VIEW data_gaps AS
        WITH gaps AS (
            SELECT account_id, 'No contract volume' AS status, 'crm' AS source,
                   '{CONTRACT} is empty or unreadable in the CRM: no utilisation, so falling usage, '
                   || 'Shelfware and expansion volume are not checked' AS reasons,
                   'Fill in {CONTRACT} in the CRM (the order form has it)' AS action
            FROM crm WHERE coalesce(contracted_events, 0) <= 0
            UNION ALL
            SELECT account_id, 'Renewal date unreadable', 'crm',
                   'renewal_date is empty or not a date (YYYY-MM-DD) in the CRM: no renewal timing, '
                   || 'so no "renewal soon" actions and no renewal forecast',
                   'Fix renewal_date in the CRM (YYYY-MM-DD)'
            FROM crm WHERE renewal_date IS NULL
            UNION ALL
            SELECT account_id, 'No pool', 'crm',
                   'Scale account with an empty cs_pool: its tasks go to CS ops instead of a pool',
                   'Set cs_pool in the CRM'
            FROM crm WHERE tier = 'Scale' AND cs_pool IS NULL
            UNION ALL
            SELECT g.account_id, 'Volume feed stopped', 'events',
                   format('No {USAGE_EVENT} since {{}}, product events until {{}}: utilisation would read as a '
                          || 'fall, so falling usage, Shelfware and expansion volume are not checked',
                          strftime(g.last_volume, '%d %b'), strftime(g.last_product, '%d %b')),
                   format('Find why the volume feed stopped on {{}} (their pipeline or ours); the usage '
                          || 'flags wait for it', strftime(g.last_volume, '%d %b'))
            FROM feed_gaps g JOIN crm c USING (account_id)
        ),
        errors AS (
            SELECT e.account_id,
                   count(*) FILTER (WHERE e.event_date > a.as_of_date - 30)                     AS err_now,
                   count(*) FILTER (WHERE e.event_date <= a.as_of_date - 30
                                      AND e.event_date >  a.as_of_date - 60)                    AS err_before
            FROM events e, as_of a
            WHERE e.event_name = 'ingestion_error' AND e.account_id IN (SELECT account_id FROM gaps)
            GROUP BY e.account_id
        ),
        open_rows AS (
            SELECT account_id, string_agg(f || ' ' || status, ', ' ORDER BY f) AS open_flags
            FROM (SELECT account_id, 'churn' AS f, status FROM churn
                  UNION ALL SELECT account_id, 'expansion', status FROM expansion)
            GROUP BY account_id
        ),
        seen AS (
            -- what can still be seen without the missing value, in words
            SELECT g.account_id,
                   concat_ws('; ',
                       CASE WHEN w.wau_avg > 0 AND w.wau_now <= (1 - {CHURN_BREADTH_DROP}) * w.wau_avg
                                 AND w.wau_avg - w.wau_now >= {CHURN_BREADTH_MIN_USERS}
                            THEN format('{{}} weekly active users vs. {{}} on average ({{}}%)', w.wau_now,
                                        round(w.wau_avg, 1), round((w.wau_now / w.wau_avg - 1) * 100)::INT) END,
                       -- errors alone are noise in the churn rule; here they're the one sign left,
                       -- so a stricter bar: many, and at least double the month before
                       CASE WHEN r.err_now >= {DQ["risk_min_errors"]} AND r.err_now >= 2 * r.err_before
                            THEN format('{{}} ingestion errors in 30 days ({{}} the month before)',
                                        r.err_now, r.err_before) END,
                       CASE WHEN o.open_flags IS NOT NULL THEN o.open_flags || ' already open' END) AS risk
            FROM (SELECT DISTINCT account_id FROM gaps) g
            LEFT JOIN weekly_users w USING (account_id)
            LEFT JOIN errors r USING (account_id)
            LEFT JOIN open_rows o USING (account_id)
        )
        SELECT c.account_id, c.account_name, c.tier, c.region, g.status,
               g.reasons || CASE WHEN s.risk <> '' THEN '. Still visible: ' || s.risk ELSE '' END AS reasons,
               CASE g.source WHEN 'crm' THEN '{DQ["crm_owner"]}' ELSE '{DQ["events_owner"]}' END AS routed_to,
               g.action
        FROM gaps g JOIN crm c USING (account_id) JOIN seen s USING (account_id)
        UNION ALL
        SELECT c.account_id, c.account_name, c.tier, c.region, 'Risk hidden' AS status,
               format('{{}}, and {{}}', string_agg(g.status, ' and ' ORDER BY g.status), s.risk) AS reasons,
               CASE WHEN c.tier = 'Scale' THEN coalesce(c.cs_pool, 'cs_ops')
                    ELSE coalesce(c.csm_owner, 'cs_ops') END AS routed_to,
               'Talk to the customer now, don''t wait for the data fix: the missing value could hide a '
               || 'bigger drop or an early renewal' AS action
        FROM gaps g JOIN crm c USING (account_id) JOIN seen s USING (account_id)
        WHERE s.risk <> '' AND c.tier <> 'Self-service'      -- Self-service: no owner to warn
          -- no pool or CSM: the owner would be CS ops, who already has the gap
          AND coalesce(CASE WHEN c.tier = 'Scale' THEN c.cs_pool ELSE c.csm_owner END, 'cs_ops') <> 'cs_ops'
        GROUP BY c.account_id, c.account_name, c.tier, c.region, c.cs_pool, c.csm_owner, s.risk
    """)

    # Whitespace (25 Nov 2026): growth with a hook, never "they don't use it, sell it". Each hook
    # is a behaviour of the last hook_days and the question to open with. One line per account
    # (an unused add-on first: it's a risk at renewal, then the add-on, then people). Held back,
    # shown but not a task (routed 'none'): at churn Watch or Act, with data missing, and on Scale
    # an add-on hook before the basics work (utilisation under basics_min_util, or Shelfware).
    # Self-service rows go to the report: no pitch to people with no CSM.
    con.sql(f"""
        CREATE VIEW growth_hooks AS
        WITH recent AS (
            SELECT e.account_id,
                   count(*) FILTER (WHERE e.event_type = 'product')                       AS actions,
                   count(*) FILTER (WHERE e.event_name = 'cohort_created')                AS cohorts,
                   count(DISTINCT e.user_id) FILTER (WHERE e.event_name = 'cohort_created') AS builders,
                   count(*) FILTER (WHERE e.event_name = 'experiment_launched')           AS experiments,
                   max(try_cast(e.properties.viewers AS INTEGER))
                       FILTER (WHERE e.event_name = 'dashboard_shared')                   AS max_viewers
            FROM events e, as_of a
            WHERE e.event_date > a.as_of_date - {GR["hook_days"]}
            GROUP BY e.account_id
        ),
        base AS (
            SELECT c.account_id, c.account_name, c.tier, c.region, c.arr, c.csm_owner, c.cs_pool,
                   coalesce(c.add_ons, '') LIKE '%{GR["addon"]}%' AS has_addon,
                   r.actions, r.cohorts, r.builders, r.experiments, r.max_viewers,
                   coalesce(w.wau_now, 0) AS wau_now, u.utilisation_pct AS util,
                   u.go_live_date <= a.as_of_date - {GR["hook_days"]} AS settled,
                   q.days_to_next_qbr, c.renewal_date - a.as_of_date AS days_to_renewal
            FROM crm c
            CROSS JOIN as_of a
            JOIN recent r USING (account_id)
            LEFT JOIN weekly_users w USING (account_id)
            LEFT JOIN utilisation u USING (account_id)
            LEFT JOIN qbr_schedule q USING (account_id)
        ),
        hooks AS (
            SELECT *, 1 AS rank, 'Unused add-on' AS hook,
                   format('{GR["addon"]} add-on bought, no experiment in {GR["hook_days"]} days, '
                          || 'while {{}} people use the product', wau_now) AS evidence,
                   'What did you plan to test when you bought experiments, and what got in the way?' AS opener
            FROM base WHERE has_addon AND experiments = 0 AND settled
            UNION ALL
            SELECT *, 2, 'Add-on hook',
                   format('{{}} cohorts in {GR["hook_days"]} days by {{}} people ({{}}% of all actions), '
                          || 'no {GR["addon"]} add-on', cohorts, builders, round(100.0 * cohorts / actions)::INT),
                   'Your team builds a lot of cohorts: what decisions are they feeding, and how do you test the changes?'
            FROM base WHERE NOT has_addon AND actions > 0 AND cohorts >= {GR["addon_min_cohorts"]}
                        AND builders >= {GR["addon_min_builders"]}
                        AND 1.0 * cohorts / actions >= {GR["addon_cohort_share"]}
            UNION ALL
            SELECT *, 3, 'People hook',
                   format('Dashboards shared with up to {{}} people; {{}} log in in a week', max_viewers, wau_now),
                   'Who reads these dashboards, and what would they do if they could explore the data themselves?'
            FROM base WHERE max_viewers >= {GR["people_min_viewers"]} AND max_viewers >= 2 * wau_now
        ),
        ranked AS (                               -- one line per account: the first by rank
            SELECT *, row_number() OVER (PARTITION BY account_id ORDER BY rank) AS n FROM hooks
        ),
        also AS (                                 -- the account's other hooks, named on that line
            SELECT account_id, string_agg(hook, ', ' ORDER BY rank) AS also FROM ranked WHERE n > 1 GROUP BY account_id
        ),
        one AS (SELECT r.*, a.also FROM ranked r LEFT JOIN also a USING (account_id) WHERE r.n = 1),
        held AS (
            SELECT o.*,
                   CASE WHEN ch.status IN ('Watch', 'Act') THEN 'Held: churn risk'
                        WHEN dg.account_id IS NOT NULL THEN 'Held: data missing'
                        WHEN o.tier = 'Scale' AND o.hook = 'Add-on hook'
                             AND (ch.status = 'Shelfware' OR coalesce(o.util, 0) < {GR["basics_min_util"]})
                             THEN 'Held: basics first'
                   END AS held,
                   ch.status AS churn_status
            FROM one o
            LEFT JOIN churn ch USING (account_id)
            LEFT JOIN (SELECT DISTINCT account_id FROM data_gaps) dg USING (account_id)
        )
        SELECT account_id, account_name, tier, region,
               coalesce(held, hook) AS status,
               evidence
               || CASE WHEN hook <> 'People hook'
                       THEN format('; {GR["addon"]} add-on about €{{}}k a year at list', round(arr * {GR["addon_share_of_arr"]} / 1000)::INT)
                       ELSE '' END
               || CASE WHEN also IS NOT NULL THEN '. Also seen: ' || also ELSE '' END
               || CASE held WHEN 'Held: churn risk' THEN format('. Held back: churn {{}} first', churn_status)
                            WHEN 'Held: data missing' THEN '. Held back: the data first'
                            WHEN 'Held: basics first' THEN '. Held back: the basics first (utilisation or Shelfware)'
                            ELSE '' END AS reasons,
               CASE WHEN held IS NOT NULL THEN 'none'
                    WHEN tier = 'Self-service' THEN 'report'
                    WHEN tier = 'Scale' THEN coalesce(cs_pool, 'cs_ops')
                    ELSE coalesce(csm_owner, 'cs_ops') END AS routed_to,
               CASE WHEN held IS NOT NULL THEN 'No growth conversation now: ' || lower(replace(held, 'Held: ', ''))
                    ELSE format('Open with: "{{}}" Raise it {{}}. Investigate, don''t pitch; if it isn''t a fit, '
                                || 'say why (it pauses for {GR["pause_days"]} days)', opener,
                                CASE WHEN tier = 'Enterprise' AND days_to_next_qbr BETWEEN 0 AND {GR["qbr_window_days"]}
                                     THEN format('at the QBR ({{}} days)', days_to_next_qbr)
                                     WHEN days_to_renewal BETWEEN 0 AND {GR["renewal_window_days"]}
                                     THEN format('with the renewal ({{}} days)', days_to_renewal)
                                     ELSE 'at the next check-in' END) END AS action
        FROM held
    """)


def main():
    con = connect()
    add_views(con)


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

    con.sql(f"COPY (SELECT * FROM activation ORDER BY status, account_id) TO '{out_sql('flags_activation')}' (HEADER)")
    print("\nSaved every row to data/flags_activation.csv")


    # --- Flag 2: churn risk ---------------------------------------------------------


    # Shelfware: low and flat, never ramped up. The trend signals above need a fall,
    # so an account that was always low never trips them.

    # One churn list: the trend signals first; Shelfware only where they found nothing

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

    con.sql(f"COPY (SELECT * FROM churn ORDER BY status, account_id) TO '{out_sql('flags_churn')}' (HEADER)")
    print("\nSaved every row to data/flags_churn.csv")


    # --- Flag 3: expansion ----------------------------------------------------------


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

    con.sql(f"COPY (SELECT * FROM expansion ORDER BY status, account_id) TO '{out_sql('flags_expansion')}' (HEADER)")
    print("\nSaved every row to data/flags_expansion.csv")


    print("\n\nValue at stake (EUR), by flag and status")
    con.sql("""
        SELECT flag, status, count(*) AS accounts, sum(amount)::BIGINT AS amount, sum(arr)::BIGINT AS arr
        FROM account_value GROUP BY flag, status ORDER BY flag, status
    """).show()
    con.sql(f"COPY (SELECT * FROM account_value ORDER BY flag, amount DESC, account_id) TO '{out_sql('account_value')}' (HEADER)")
    print("Saved every row to data/account_value.csv")


    # --- Flag 4: QBR ---------------------------------------------------------------


    print("\n\nQBR (Enterprise accounts)")

    print("\n7. QBRs coming up and overdue, by CSM")
    con.sql("""
        SELECT routed_to, status, account_id, account_name, reasons, action
        FROM qbr
        ORDER BY routed_to, status, account_id
    """).show(max_rows=100, max_width=300)

    con.sql(f"COPY (SELECT * FROM qbr ORDER BY status, account_id) TO '{out_sql('flags_qbr')}' (HEADER)")
    print("\nSaved every row to data/flags_qbr.csv")


    # --- Data gaps -------------------------------------------------------------------


    print("\n\nData missing: gaps in an account's own data (the flags that need it wait)")
    con.sql("""
        SELECT d.routed_to, d.status, d.account_id, d.account_name, c.arr::BIGINT AS arr, d.reasons
        FROM data_gaps d JOIN crm c USING (account_id)
        ORDER BY d.status = 'Risk hidden' DESC, d.routed_to, d.status, d.account_id
    """).show(max_rows=100, max_width=320)
    con.sql(f"COPY (SELECT * FROM data_gaps ORDER BY status, account_id) TO '{out_sql('flags_data')}' (HEADER)")
    print("Saved every row to data/flags_data.csv")


    # --- Growth: whitespace with a hook ------------------------------------------------


    print("\n\nGrowth hooks: whitespace with a hook (no hook, no pitch); held back = shown, not a task")
    con.sql("""
        SELECT routed_to, status, account_id, account_name, tier, reasons
        FROM growth_hooks ORDER BY status LIKE 'Held%', routed_to, status, account_id
    """).show(max_rows=100, max_width=320)
    con.sql(f"COPY (SELECT * FROM growth_hooks ORDER BY status, account_id) TO '{out_sql('flags_growth')}' (HEADER)")
    print("Saved every row to data/flags_growth.csv")


if __name__ == "__main__":
    main()
