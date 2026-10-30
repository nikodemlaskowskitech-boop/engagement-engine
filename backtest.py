"""
Backtest: did the churn flags warn us about the accounts that churned or
shrank at renewal, and how early?

The flags run on the data as it stands (data/flags_churn.csv). The outcomes
are what happened at each renewal in the months after the data ends
(data/renewal_outcomes.csv): renewed, expanded, downsized or churned. In real
life they come from the CRM's closed renewals; here the generator writes them,
with surprises a flag can't see (a healthy customer acquired or out of budget).

  Recall         of the accounts that churned or downsized, how many were
                 flagged beforehand (Watch, Act or Shelfware)
  Precision      of the flagged accounts that renewed in the period, how many
                 really churned or downsized (the rest were false alarms)
  Warning time   days from the first signal (30-day utilisation under the
                 churn line) to the renewal. Under 30 days is too late to act.
  Calls          for flagged accounts, churn with and without a churn call:
                 the first look at "outcome after a call"

Warning time is a lower bound for accounts already under the line when the
data starts: the true signal may be older.

  Sieve        every churn signal, stage by stage: sent to automation or a
                 person, called, called fast, kept at renewal
  Value        the ARR the data put at risk (flags.py, [money.at_risk]) and
                 the owners' forecasts, next to the ARR really lost at
                 renewal (needs renewal_arr in the outcomes): is the rule
                 too cautious, too relaxed, or about right?

Run flags.py and journeys.py first, then:  python backtest.py
"""

import os
import sys

import team_report
from cairnpath_db import connect
from settings import SETTINGS, input_path

sys.stdout.reconfigure(encoding="utf-8")

LINE = SETTINGS["churn"]["util_below"]                 # the churn line (settings.toml)
TOO_LATE = SETTINGS["backtest"]["too_late_days"]       # less warning than this can't be acted on
OUTCOMES = str(input_path("outcomes_file")).replace("\\", "/")   # closed renewals ([data] in settings)
FAST = SETTINGS["journeys"]["act_for_days"]            # "called within" for the funnel (14 days)

def add_views(con):
    """Create the backtest view (also used by export_views.py). Empty, with the
    same columns, when there are no renewal outcomes yet."""
    if not input_path("outcomes_file").exists():
        con.sql("""CREATE VIEW backtest AS SELECT
                   NULL::VARCHAR AS account_id, NULL::VARCHAR AS account_name, NULL::VARCHAR AS tier,
                   NULL::DATE AS renewal_date, NULL::VARCHAR AS outcome, NULL::BOOLEAN AS lost_revenue,
                   NULL::VARCHAR AS flag_status, NULL::BOOLEAN AS flagged, NULL::DATE AS signal_date,
                   NULL::BIGINT AS warning_days, NULL::BOOLEAN AS had_churn_call, NULL::DOUBLE AS util_now,
                   NULL::DOUBLE AS arr, NULL::DOUBLE AS renewal_arr, NULL::DOUBLE AS lost_arr,
                   NULL::DOUBLE AS data_at_risk, NULL::VARCHAR AS forecast_call, NULL::DOUBLE AS forecast_lost
                   WHERE false""")
        return
    # renewal_arr (what the account renewed at) is optional: without it, the
    # value comparison stays empty and the counts still work
    cols = {r[0] for r in con.sql(f"DESCRIBE SELECT * FROM read_csv('{OUTCOMES}', all_varchar = true)").fetchall()}
    renewal_arr = "renewal_arr::DOUBLE" if "renewal_arr" in cols else "NULL::DOUBLE"
    con.sql(f"""
    CREATE VIEW backtest AS
    WITH outcomes AS (
        SELECT account_id, renewal_date::DATE AS renewal_date, outcome, {renewal_arr} AS renewal_arr
        FROM read_csv('{OUTCOMES}', all_varchar = true)
    ),
    at_risk AS (
        -- the data's number (flags.py): ARR at risk per churn row
        SELECT account_id, amount::DOUBLE AS data_at_risk
        FROM read_csv('data/account_value.csv', all_varchar = true) WHERE flag = 'churn'
    ),
    forecast AS (
        -- the owner's (or manager's) latest renewal forecast from the cockpit
        SELECT account_id, arg_max(properties.status, "timestamp") AS call,
               arg_max(properties.amount, "timestamp") AS amount
        FROM engagement WHERE event_name = 'forecast_set' GROUP BY account_id
    ),
    flagged AS (
        SELECT account_id, status
        FROM read_csv('data/flags_churn.csv', all_varchar = true)
    ),
    signal AS (
        -- the first day the account's 30-day utilisation was under the churn line
        SELECT account_id, min(event_date) FILTER (WHERE util < {LINE}) AS signal_date
        FROM daily_util_30d
        GROUP BY account_id
    ),
    called AS (
        SELECT DISTINCT account_id FROM engagement
        WHERE event_name = 'meeting_held' AND properties.topic = 'churn_call'
    )
    SELECT o.account_id, c.account_name, c.tier, o.renewal_date, o.outcome,
           o.outcome IN ('churned', 'downsized')           AS lost_revenue,
           f.status                                         AS flag_status,
           f.status IS NOT NULL                             AS flagged,
           s.signal_date,
           o.renewal_date - s.signal_date                   AS warning_days,
           o.account_id IN (SELECT account_id FROM called)  AS had_churn_call,
           u.utilisation_pct                                AS util_now,
           c.arr,
           o.renewal_arr,
           greatest(0, c.arr - o.renewal_arr)               AS lost_arr,
           coalesce(r.data_at_risk, 0)                      AS data_at_risk,
           fc.call                                          AS forecast_call,
           CASE fc.call WHEN 'churn' THEN c.arr
                        WHEN 'downsize' THEN greatest(0, c.arr - fc.amount)
                        WHEN 'renew' THEN 0 END             AS forecast_lost
    FROM outcomes o
    JOIN crm c USING (account_id)
    LEFT JOIN at_risk r USING (account_id)
    LEFT JOIN forecast fc USING (account_id)
    LEFT JOIN flagged f USING (account_id)
    LEFT JOIN signal s USING (account_id)
    LEFT JOIN utilisation u USING (account_id)
""")


# Run as a script: the report. Imported (export_views.py): just add_views.
if __name__ == "__main__" and not input_path("outcomes_file").exists():
    # not an error: older data has no outcomes yet (run_all.ps1 -Regenerate makes them)
    print(f"No {OUTCOMES} yet: run python generate_events.py (demo data) or add your closed renewals, then backtest.py")
    sys.exit(0)

if __name__ == "__main__":
    con = connect()
    add_views(con)
    print("\nBacktest: did the churn flags see it coming?")
    first, last = con.sql("SELECT min(renewal_date), max(renewal_date) FROM backtest").fetchone()
    print(f"Renewals from {first} to {last}, after the data ends\n")

    print("1. What happened at renewal, by tier")
    con.sql("""
        SELECT tier, count(*) AS renewals,
               count(*) FILTER (WHERE outcome = 'renewed')   AS renewed,
               count(*) FILTER (WHERE outcome = 'expanded')  AS expanded,
               count(*) FILTER (WHERE outcome = 'downsized') AS downsized,
               count(*) FILTER (WHERE outcome = 'churned')   AS churned
        FROM backtest GROUP BY tier ORDER BY tier
    """).show()

    print("\n2. Recall: of the accounts that churned or downsized, how many were flagged?")
    con.sql("""
        SELECT coalesce(tier, 'All tiers') AS tier,
               count(*) FILTER (WHERE lost_revenue)                           AS churned_or_downsized,
               format('{} ({}%)', count(*) FILTER (WHERE lost_revenue AND flagged),
                      round(100.0 * count(*) FILTER (WHERE lost_revenue AND flagged)
                            / nullif(count(*) FILTER (WHERE lost_revenue), 0))::INT) AS flagged_before
        FROM backtest GROUP BY ROLLUP (tier) ORDER BY grouping(tier), tier
    """).show()

    print("\n3. Precision: of the flagged accounts, how many really churned or downsized?")
    con.sql("""
        SELECT coalesce(flag_status, 'All flags') AS flag_status,
               count(*)                                AS flagged,
               format('{} ({}%)', count(*) FILTER (WHERE lost_revenue),
                      round(100.0 * count(*) FILTER (WHERE lost_revenue) / count(*))::INT) AS churned_or_downsized,
               count(*) FILTER (WHERE NOT lost_revenue) AS false_alarms
        FROM backtest WHERE flagged GROUP BY ROLLUP (flag_status) ORDER BY grouping(flag_status), flag_status
    """).show()

    print(f"\n4. Warning time: days from the first signal to the renewal (caught accounts)")
    con.sql(f"""
        SELECT count(*)                                          AS caught,
               median(warning_days)::INT                         AS median_days,
               min(warning_days)                                 AS shortest,
               max(warning_days)                                 AS longest,
               count(*) FILTER (WHERE warning_days < {TOO_LATE}) AS under_{TOO_LATE}_days
        FROM backtest WHERE lost_revenue AND flagged
    """).show()

    print("\n5. Misses: churned or downsized with no flag, to judge one by one")
    con.sql("""
        SELECT account_id, account_name, tier, outcome, renewal_date,
               util_now AS util_now_pct
        FROM backtest WHERE lost_revenue AND NOT flagged
        ORDER BY tier, renewal_date
    """).show(max_rows=60)

    print("\n6. Calls: churn among Act accounts, with and without a churn call")
    print("   (Act only: comparing all flags would mix declining accounts, which get the")
    print("   calls, with Shelfware ones, which rarely churn, and make calls look harmful)")
    con.sql("""
        SELECT CASE WHEN had_churn_call THEN 'churn call held' ELSE 'no churn call' END AS cs_action,
               count(*) AS act_accounts,
               format('{} ({}%)', count(*) FILTER (WHERE outcome = 'churned'),
                      round(100.0 * count(*) FILTER (WHERE outcome = 'churned') / count(*))::INT) AS churned,
               format('{} ({}%)', count(*) FILTER (WHERE NOT lost_revenue),
                      round(100.0 * count(*) FILTER (WHERE NOT lost_revenue) / count(*))::INT) AS kept_whole
        FROM backtest WHERE flag_status = 'Act' AND tier <> 'Self-service'
        GROUP BY ALL ORDER BY 1
    """).show()

    # --- 7. The engagement sieve, read like a funnel ---------------------------------
    # Every churn signal, stage by stage: who it went to, whether a person acted
    # and how fast, and what happened at renewal.
    team_report.add_views(con)                     # time_to_action: signal date and first call
    print("\n7. The sieve, read like a funnel: churn signals, stage by stage")
    con.sql(f"""
        WITH churn AS (
            SELECT f.account_id, f.status, c.tier,
                   t.call_date, t.days_to_call, b.outcome, b.lost_revenue
            FROM read_csv('data/flags_churn.csv', all_varchar = true) f
            JOIN crm c USING (account_id)
            LEFT JOIN time_to_action t ON t.account_id = f.account_id AND t.flag = 'churn'
            LEFT JOIN backtest b ON b.account_id = f.account_id
        )
        SELECT stage, accounts FROM (VALUES
            (1, 'Churn signals (Watch, Act, Shelfware)', (SELECT count(*) FROM churn)),
            (2, '  sieved to automation (Self-service)',  (SELECT count(*) FROM churn WHERE tier = 'Self-service')),
            (3, '  sieved to a person (Scale, Enterprise)', (SELECT count(*) FROM churn WHERE tier <> 'Self-service')),
            (4, '    at Act: need a call now',            (SELECT count(*) FROM churn WHERE tier <> 'Self-service' AND status = 'Act')),
            (5, '      called',                           (SELECT count(*) FROM churn WHERE tier <> 'Self-service' AND status = 'Act' AND call_date IS NOT NULL)),
            (6, '        called within {FAST} days',         (SELECT count(*) FROM churn WHERE tier <> 'Self-service' AND status = 'Act' AND days_to_call <= {FAST})),
            -- each stage is a subset of the one above it, so it reads top to bottom
            (7, '          renewing in the next 180 days',(SELECT count(*) FROM churn WHERE tier <> 'Self-service' AND status = 'Act' AND days_to_call <= {FAST} AND outcome IS NOT NULL)),
            (8, '            kept whole (renewed or grew)',(SELECT count(*) FROM churn WHERE tier <> 'Self-service' AND status = 'Act' AND days_to_call <= {FAST} AND outcome IS NOT NULL AND NOT lost_revenue))
        ) AS funnel(step, stage, accounts)
        ORDER BY step
    """).show(max_width=200)

    # --- 8. Value: the ARR the data put at risk vs. the ARR really lost ----------------
    if con.sql("SELECT count(renewal_arr) FROM backtest").fetchone()[0]:
        print("\n8. Value: ARR at risk (the data's rule, [money.at_risk]) vs. ARR really lost at renewal")
        con.sql("""
            SELECT coalesce(coalesce(flag_status, 'No churn flag'), 'All') AS status,
                   count(*)                              AS renewals,
                   round(sum(arr))::BIGINT               AS arr,
                   round(sum(data_at_risk))::BIGINT      AS data_at_risk,
                   round(sum(lost_arr))::BIGINT          AS really_lost,
                   CASE WHEN sum(data_at_risk) > 0
                        THEN round(100 * sum(lost_arr) / sum(data_at_risk))::INT END AS lost_pct_of_at_risk
            FROM backtest
            GROUP BY ROLLUP (coalesce(flag_status, 'No churn flag'))
            ORDER BY grouping(coalesce(flag_status, 'No churn flag')), 1
        """).show()
        print("   Under 100%: the rule over-calls the risk (or CS saved some of it); over 100%:")
        print("   the rule is too relaxed. 'No churn flag' losses are what no flag saw.")
        whole, lost = con.sql("SELECT sum(arr) FILTER (WHERE flagged), sum(lost_arr) FILTER (WHERE flagged) FROM backtest").fetchone()
        rule = con.sql("SELECT sum(data_at_risk) FROM backtest WHERE flagged").fetchone()[0]
        print(f"   Flagged accounts lost {lost:,.0f}. This rule said {rule:,.0f} at risk; counting the whole ARR")
        print(f"   of every flagged account would have said {whole:,.0f}.")
        if con.sql("SELECT count(forecast_call) FROM backtest").fetchone()[0]:
            print("\n   Owners' forecasts next to the data, for accounts with a forecast")
            con.sql("""
                SELECT account_id, account_name, forecast_call,
                       round(forecast_lost)::BIGINT AS forecast_lost,
                       round(data_at_risk)::BIGINT AS data_at_risk,
                       round(lost_arr)::BIGINT AS really_lost
                FROM backtest WHERE forecast_call IS NOT NULL ORDER BY account_id
            """).show()
        else:
            print("   No owner forecast on an account that renewed in the period yet.")

    print("Small groups: read the counts, not just the percentages. The outcomes are")
    print("generated, so sections 6 and 7 show the method, not proof that calls work.")
