"""
Onboarding report: does the getting-started email help? Welcomed vs. control.

New Self-service and Scale accounts are split in two:

  welcomed   got the getting-started email the day after signing
  control    the onboarding holdout (data/onboarding_holdout.csv): no email

For each group, all counted from signing day:

  SDK installed        how many installed it at all
  days to SDK          median days from signing to SDK install
  days to first chart  median days from signing to the first chart
  activated in 14d     SDK in and 2+ active users within 14 days, counted
                       only for accounts signed 14+ days ago (younger ones
                       haven't had their chance yet)

Speed and outcome are shown side by side on purpose: an email can get
people started sooner without changing who ends up activated.

Read it with care:
  - The control group is small (about 1 in 5 new accounts), so a few
    accounts move its percentages a lot. Counts sit next to every one.
  - This is generated data with a planted effect (control accounts install
    the SDK 3 days later). It shows the method works; it doesn't prove real
    emails work.

Run generate_events.py first (no flags needed), then:  python onboarding_report.py
"""

import sys

from cairnpath_db import connect
from settings import SETTINGS, input_path

sys.stdout.reconfigure(encoding="utf-8")

ACTIVATION_DAYS = SETTINGS["activation"]["window_days"]   # same window as the activation flag
MIN_USERS = SETTINGS["activation"]["min_users"]

def add_views(con):
    """Create the onboarding view (also used by export_views.py)."""
    # the holdout list ([data] holdout_file) is optional: without it, nobody is a control
    h = input_path("holdout_file")
    HOLDOUT_SQL = (f"SELECT account_id FROM read_csv_auto('{str(h).replace(chr(92), '/')}')" if h.exists()
                   else "SELECT NULL::VARCHAR AS account_id WHERE false")
    con.sql(f"""
        CREATE VIEW onboarding AS
        WITH welcomed AS (
            SELECT DISTINCT account_id
            FROM engagement
            WHERE event_name = 'email_sent' AND properties.topic = 'getting_started'
        ),
        holdout AS (
            {HOLDOUT_SQL}
        ),
        cohort AS (
            SELECT c.account_id, c.tier, c.contract_start,
                   CASE WHEN c.account_id IN (SELECT account_id FROM holdout)
                        THEN 'control (no email)' ELSE 'welcomed' END AS welcome_group
            FROM crm c
            WHERE c.account_id IN (SELECT account_id FROM welcomed)
               OR c.account_id IN (SELECT account_id FROM holdout)
        )
        SELECT k.account_id, k.tier, k.welcome_group, k.contract_start,
               min(e.event_date) FILTER (WHERE e.event_name = 'sdk_installed')  AS sdk_date,
               min(e.event_date) FILTER (WHERE e.event_name = 'chart_created')  AS first_chart_date,
               count(DISTINCT e.user_id)
                   FILTER (WHERE e.event_date < k.contract_start + {ACTIVATION_DAYS}) AS users_first_14d,
               a.as_of_date - k.contract_start >= {ACTIVATION_DAYS}           AS old_enough
        FROM cohort k
        LEFT JOIN events e ON e.account_id = k.account_id
        CROSS JOIN as_of a
        GROUP BY k.account_id, k.tier, k.welcome_group, k.contract_start, a.as_of_date
    """)


if __name__ == "__main__":
    con = connect()
    add_views(con)

    SUMMARY = f"""
        count(*)                                                        AS accounts,
        format('{{}} ({{}}%)', count(sdk_date),
               round(100.0 * count(sdk_date) / count(*))::INT)          AS sdk_installed,
        median(sdk_date - contract_start)                               AS median_days_to_sdk,
        median(first_chart_date - contract_start)                       AS median_days_to_chart,
        format('{{}} of {{}} ({{}}%)',
               count(*) FILTER (WHERE old_enough AND sdk_date < contract_start + {ACTIVATION_DAYS}
                                    AND users_first_14d >= {MIN_USERS}),
               count(*) FILTER (WHERE old_enough),
               round(100.0 * count(*) FILTER (WHERE old_enough AND sdk_date < contract_start + {ACTIVATION_DAYS}
                                                  AND users_first_14d >= {MIN_USERS})
                     / nullif(count(*) FILTER (WHERE old_enough), 0))::INT) AS activated_in_14d
    """

    print("\nOnboarding report: does the getting-started email help? Welcomed vs. control")

    print("\n1. Both tiers together")
    con.sql(f"""
        SELECT welcome_group, {SUMMARY}
        FROM onboarding
        GROUP BY welcome_group
        ORDER BY welcome_group DESC
    """).show(max_width=250)

    print("\n2. By tier (smaller groups: read the counts, not just the percentages)")
    con.sql(f"""
        SELECT tier, welcome_group, {SUMMARY}
        FROM onboarding
        GROUP BY tier, welcome_group
        ORDER BY tier, welcome_group DESC
    """).show(max_width=250)

    print("\nHow to read it: days to SDK and to first chart measure speed; activated in 14 days")
    print("measures outcome. A gap in speed with no gap in outcome means the email gets people")
    print("started sooner but doesn't change who activates.")
    print("Generated data with a planted effect (control installs the SDK 3 days later): this")
    print("shows the method works; it doesn't prove that real emails work.")
