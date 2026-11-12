"""
Retention in the industry's words: GRR and NRR, the ARR bridge, a forecast,
and what a call is worth. The top line the CS work rolls up to.

Standard definitions (12 Nov 2026, Nikodem: "speak industry language"):
  Cohort   the customers on the first day of the period, with their ARR that day
  GRR      what the cohort pays at the end, each account capped at what it paid
           at the start, over what it paid at the start: churn and downsell
           come off, expansion doesn't count. Never above 100%.
  NRR      the same with expansion counted (can go above 100%).
  New logos are in neither: they weren't customers on day one.

Where each number comes from:
  This year   account by account: the CRM's ARR today, customer_since, status
              (churned = left at its renewal_date) and the closed renewals
              (renewal_history: ARR before and after each renewal). ARR on any
              day = the arr_before of the account's next renewal, or today's.
  Year before the top line finance reports: a quarterly ARR bridge
              (arr_bridge_history), no accounts. Its GRR and NRR are worked out
              from the bridge (losses over the year's starting ARR), so logos
              signed during that year count in its losses: slightly harsher.
              This year's bridge-based figure is shown too, for a fair comparison.
  Forecast    the renewals in the next [retention] forecast_days: the ARR the
              data puts at risk (flags.py's rules), the owners' forecasts where
              they set one, and the upside. In GRR points of today's book.
              The demo knows what happened next (renewal_outcomes), so it shows
              forecast against actual: how predictable the number is.
  Calls       accounts that got a churn task during the year, called within
              [journeys] act_for_days or not, and what they renewed at. Fake data
              with a planted effect: it shows the method, never "we saved X".

Run after backtest.py (export_views.py also calls add_views):  python retention.py
"""
import sys

from cairnpath_db import connect
from settings import SETTINGS

sys.stdout.reconfigure(encoding="utf-8")

FORECAST_DAYS = SETTINGS["retention"]["forecast_days"]
FAST = SETTINGS["journeys"]["act_for_days"]


def add_views(con):
    """arr_segments, retention_year, arr_bridge_this, arr_bridge_prior, retention_forecast,
    call_effect. Needs connect() (crm_all, renewal_history, arr_bridge) and, for the
    forecast, backtest.add_views and the flags' account_value."""
    # ARR on any day, per account: one row per stretch of time at one ARR.
    # Before a renewal the account paid that renewal's arr_before; after the last
    # renewal, today's ARR (or nothing, once it left).
    con.sql("""
        CREATE VIEW arr_segments AS
        WITH r AS (
            SELECT account_id, renewal_date, outcome, arr_before, renewal_arr,
                   lag(renewal_date) OVER (PARTITION BY account_id ORDER BY renewal_date) AS prev
            FROM renewal_history
        ),
        before AS (                       -- the stretch that ended at each renewal
            SELECT r.account_id, coalesce(r.prev, c.customer_since, c.contract_start) AS valid_from,
                   r.renewal_date AS valid_to, r.arr_before AS arr
            FROM r JOIN crm_all c USING (account_id)
        ),
        latest AS (                       -- since the last renewal (or since signing): today's ARR
            SELECT c.account_id,
                   coalesce((SELECT max(renewal_date) FROM renewal_history h WHERE h.account_id = c.account_id),
                            c.customer_since, c.contract_start) AS valid_from,
                   NULL::DATE AS valid_to, c.arr
            FROM crm_all c
            WHERE coalesce(c.status, 'active') <> 'churned'
        )
        SELECT * FROM before UNION ALL SELECT * FROM latest
    """)
    con.sql("""
        CREATE MACRO arr_on(t) AS TABLE
        SELECT account_id, arr FROM arr_segments
        WHERE valid_from <= t AND (valid_to IS NULL OR t < valid_to)
    """)
    # One period, cohort by cohort: the standard GRR and NRR
    con.sql("""
        CREATE MACRO retention_between(t0, t1) AS TABLE
        WITH cohort AS (
            SELECT s.account_id, s.arr AS start_arr, coalesce(e.arr, 0) AS end_arr
            FROM arr_on(t0) s LEFT JOIN arr_on(t1) e USING (account_id)
        )
        SELECT t0 AS period_start, t1 AS period_end, count(*) AS accounts,
               sum(start_arr) AS start_arr, sum(end_arr) AS end_arr,
               sum(start_arr) FILTER (WHERE end_arr = 0)                       AS churned_arr,
               sum(start_arr - end_arr) FILTER (WHERE end_arr > 0 AND end_arr < start_arr) AS contraction_arr,
               sum(end_arr - start_arr) FILTER (WHERE end_arr > start_arr)      AS expansion_arr,
               count(*) FILTER (WHERE end_arr = 0)                             AS churned_n,
               count(*) FILTER (WHERE end_arr > 0 AND end_arr < start_arr)     AS contraction_n,
               count(*) FILTER (WHERE end_arr > start_arr)                      AS expansion_n,
               sum(least(start_arr, end_arr)) / sum(start_arr)                  AS grr,
               sum(end_arr) / sum(start_arr)                                    AS nrr
        FROM cohort
    """)
    # This year: from the first day of data to the as-of date
    con.sql("""
        CREATE VIEW retention_year AS
        SELECT r.* FROM (SELECT min(event_date) AS d0, max(event_date) AS d1 FROM events) p,
                        retention_between(p.d0, p.d1) r
    """)
    # Each account's part in this year's numbers (for the click-through lists)
    con.sql("""
        CREATE VIEW retention_accounts AS
        WITH p AS (SELECT min(event_date) AS d0, max(event_date) AS d1 FROM events)
        SELECT s.account_id, s.arr AS start_arr, coalesce(e.arr, 0) AS end_arr,
               CASE WHEN coalesce(e.arr, 0) = 0 THEN 'churned'
                    WHEN e.arr < s.arr THEN 'contraction'
                    WHEN e.arr > s.arr THEN 'expansion' ELSE 'flat' END AS movement
        FROM p, arr_on(p.d0) s LEFT JOIN arr_on(p.d1) e USING (account_id)
    """)
    # This year's quarterly ARR bridge, the same shape as finance's for the year before
    con.sql("""
        CREATE VIEW arr_bridge_this AS
        WITH p AS (SELECT min(event_date) AS d0, max(event_date) AS d1 FROM events),
        q AS (
            SELECT i AS quarter, p.d0 + CAST(round(i * 365 / 4.0) AS INTEGER) AS qs,
                   least(p.d1, p.d0 + CAST(round((i + 1) * 365 / 4.0) AS INTEGER) - 1) AS qe
            FROM p, range(4) t(i)
        )
        SELECT q.quarter, q.qs AS period_start, q.qe AS period_end,
               r.start_arr AS starting_arr,
               coalesce((SELECT sum(n.arr) FROM arr_on(q.qe) n
                         WHERE n.account_id NOT IN (SELECT account_id FROM arr_on(q.qs))), 0) AS new_logo_arr,
               coalesce(r.expansion_arr, 0) AS expansion_arr,
               coalesce(r.contraction_arr, 0) AS contraction_arr,
               coalesce(r.churned_arr, 0) AS churned_arr,
               (SELECT sum(arr) FROM arr_on(q.qe)) AS ending_arr
        FROM q, retention_between(q.qs, q.qe) r
        ORDER BY q.quarter
    """)
    con.sql("""
        CREATE VIEW arr_bridge_prior AS
        SELECT row_number() OVER (ORDER BY period_start) - 1 AS quarter, * FROM arr_bridge ORDER BY period_start
    """)
    # GRR and NRR from a bridge (the only way for a year known by its top line)
    for name, src in (("bridge_rates_prior", "arr_bridge_prior"), ("bridge_rates_this", "arr_bridge_this")):
        con.sql(f"""
            CREATE VIEW {name} AS
            SELECT min(period_start) AS period_start, max(period_end) AS period_end,
                   arg_min(starting_arr, period_start) AS start_arr, arg_max(ending_arr, period_start) AS end_arr,
                   sum(new_logo_arr) AS new_logo_arr, sum(expansion_arr) AS expansion_arr,
                   sum(contraction_arr) AS contraction_arr, sum(churned_arr) AS churned_arr,
                   1 - (sum(churned_arr) + sum(contraction_arr)) / arg_min(starting_arr, period_start) AS grr,
                   1 + (sum(expansion_arr) - sum(churned_arr) - sum(contraction_arr))
                       / arg_min(starting_arr, period_start) AS nrr
            FROM {src}
        """)
    # Forecast: the renewals in the next forecast_days, in GRR points of today's book.
    # actual_*: what happened (the demo's renewal outcomes) for forecast against actual.
    con.sql(f"""
        CREATE VIEW retention_forecast_accounts AS
        WITH up AS (
            SELECT c.account_id, c.arr, c.renewal_date
            FROM crm c, as_of a
            WHERE c.renewal_date > a.as_of_date AND c.renewal_date <= a.as_of_date + {FORECAST_DAYS}
        ),
        risk AS (SELECT account_id, amount::DOUBLE AS at_risk FROM account_value WHERE flag = 'churn'),
        gain AS (SELECT account_id, amount::DOUBLE AS upside FROM account_value WHERE flag = 'expansion'),
        owner AS (
            SELECT account_id, arg_max(properties.status, "timestamp") AS call,
                   arg_max(properties.amount, "timestamp") AS amount
            FROM engagement WHERE event_name = 'forecast_set' GROUP BY account_id
        ),
        rows AS (
            SELECT u.account_id, u.arr, u.renewal_date,
                   coalesce(r.at_risk, 0) AS data_loss,
                   CASE o.call WHEN 'churn' THEN u.arr WHEN 'downsize' THEN greatest(0, u.arr - o.amount)
                               WHEN 'renew' THEN 0 ELSE coalesce(r.at_risk, 0) END AS owner_loss,
                   o.call IS NOT NULL AS has_owner_call,
                   coalesce(g.upside, 0) AS upside,
                   b.lost_arr AS actual_loss,
                   CASE WHEN b.renewal_arr > u.arr THEN b.renewal_arr - u.arr ELSE 0 END AS actual_gain,
                   b.account_id IS NOT NULL AS has_outcome
            FROM up u
            LEFT JOIN risk r USING (account_id)
            LEFT JOIN gain g USING (account_id)
            LEFT JOIN owner o USING (account_id)
            LEFT JOIN backtest b USING (account_id)
        )
        SELECT * FROM rows
    """)
    con.sql(f"""
        CREATE VIEW retention_forecast AS
        WITH book AS (SELECT sum(arr) AS book_arr FROM crm)
        SELECT {FORECAST_DAYS} AS days, count(*) AS renewals, sum(arr) AS up_for_renewal,
               max(book_arr) AS book_arr,
               sum(data_loss) AS data_loss, sum(owner_loss) AS owner_loss,
               count(*) FILTER (WHERE has_owner_call) AS owner_calls,
               sum(upside) AS upside,
               sum(actual_loss) FILTER (WHERE has_outcome) AS actual_loss,
               sum(actual_gain) FILTER (WHERE has_outcome) AS actual_gain,
               count(*) FILTER (WHERE has_outcome) AS with_outcome
        FROM retention_forecast_accounts, book
    """)
    # What a call is worth, like for like: accounts with a churn Act task during the
    # year, called within FAST days or not, and their next renewal (if it came).
    # No action log yet: an empty view with the same columns.
    has_log = con.sql("SELECT count(*) FROM information_schema.tables WHERE table_name = 'action_log'").fetchone()[0]
    if not has_log:
        con.sql("""CREATE VIEW call_effect_accounts AS SELECT NULL::VARCHAR AS account_id, NULL::VARCHAR AS grp,
                   NULL::DATE AS sent, NULL::DATE AS call_day, NULL::VARCHAR AS outcome,
                   NULL::DOUBLE AS arr_before, NULL::DOUBLE AS renewal_arr WHERE false""")
    else:
        con.sql(f"""
            CREATE VIEW call_effect_accounts AS
            WITH tasks AS (
                SELECT account_id, min(logged_on::DATE) AS sent
                FROM action_log WHERE flag = 'churn' AND status = 'Act' GROUP BY account_id
            ),
            calls AS (
                SELECT t.account_id, min(e.event_date) AS call_day
                FROM tasks t JOIN engagement e ON e.account_id = t.account_id AND e.event_date >= t.sent
                WHERE e.event_name = 'meeting_held' AND e.properties.topic IN ('churn_call', 'qbr')
                GROUP BY t.account_id
            ),
            renewed AS (
                SELECT t.account_id, arg_min(h.outcome, h.renewal_date) AS outcome,
                       arg_min(h.arr_before, h.renewal_date) AS arr_before,
                       arg_min(h.renewal_arr, h.renewal_date) AS renewal_arr
                FROM tasks t JOIN renewal_history h ON h.account_id = t.account_id AND h.renewal_date > t.sent
                GROUP BY t.account_id
            )
            SELECT t.account_id,
                   CASE WHEN c.call_day - t.sent <= {FAST} THEN 'in_time' ELSE 'late_or_never' END AS grp,
                   t.sent, c.call_day, r.outcome, r.arr_before, r.renewal_arr
            FROM tasks t JOIN renewed r USING (account_id) LEFT JOIN calls c USING (account_id)
        """)
    con.sql("""
        CREATE VIEW call_effect AS
        SELECT grp, count(*) AS accounts, sum(arr_before) AS arr_before, sum(renewal_arr) AS renewal_arr,
               count(*) FILTER (WHERE outcome = 'churned') AS churned,
               sum(renewal_arr) / sum(arr_before) AS kept_share
        FROM call_effect_accounts GROUP BY grp
    """)

    # The business case for calling in time, on today's book: the churn accounts at Act
    # that a person owns and nobody has called yet, their ARR, and what they would keep
    # at the in-time rate instead of the late rate. Needs team_report.add_views
    # (time_to_action). A method, not a promise: the rates come from a handful of accounts.
    con.sql("""
        CREATE VIEW call_waiting AS
        SELECT t.account_id, c.arr FROM time_to_action t JOIN crm c USING (account_id)
        WHERE t.flag = 'churn' AND t.call_date IS NULL
          AND t.routed_to NOT IN ('report', 'automated', 'none')
    """)
    con.sql("""
        CREATE VIEW call_value AS
        WITH rates AS (
            SELECT max(kept_share) FILTER (WHERE grp = 'in_time')       AS kept_in_time,
                   max(kept_share) FILTER (WHERE grp = 'late_or_never') AS kept_late
            FROM call_effect
        ),
        waiting AS (SELECT * FROM call_waiting)
        SELECT count(w.account_id) AS accounts, coalesce(sum(w.arr), 0) AS arr,
               max(r.kept_in_time) AS kept_in_time, max(r.kept_late) AS kept_late,
               coalesce(sum(w.arr), 0) * greatest(0, max(r.kept_in_time) - max(r.kept_late)) AS value
        FROM rates r LEFT JOIN waiting w ON true
    """)


def main():
    import backtest
    import flags
    import team_report
    con = connect()
    flags.add_views(con)           # account_value, for the forecast
    team_report.add_views(con)     # time_to_action, for the value of calling in time
    backtest.add_views(con)
    add_views(con)
    pct = lambda x: f"{100 * x:.1f}%" if x is not None else "-"
    m = lambda x: f"€{(x or 0) / 1e6:.2f}M"
    y = con.sql("SELECT * FROM retention_year").fetchone()
    cols = con.sql("SELECT * FROM retention_year").columns
    y = dict(zip(cols, y))
    print(f"This year, {y['period_start']} to {y['period_end']} (the {y['accounts']} customers of day one)")
    print(f"  GRR {pct(y['grr'])}   NRR {pct(y['nrr'])}   on {m(y['start_arr'])} starting ARR")
    print(f"  churn {m(y['churned_arr'])} ({y['churned_n']}), downsell {m(y['contraction_arr'])} "
          f"({y['contraction_n']}), expansion {m(y['expansion_arr'])} ({y['expansion_n']})")
    for label, view in (("Year before (finance's ARR bridge)", "bridge_rates_prior"),
                        ("This year, the same bridge method", "bridge_rates_this")):
        r = con.sql(f"SELECT grr, nrr, start_arr, end_arr FROM {view}").fetchone()
        print(f"{label}: GRR {pct(r[0])}, NRR {pct(r[1])}, ARR {m(r[2])} -> {m(r[3])}")
    print("\nThis year's ARR bridge, quarter by quarter")
    con.sql("SELECT * EXCLUDE (quarter) FROM arr_bridge_this").show()
    f = dict(zip(con.sql("SELECT * FROM retention_forecast").columns, con.sql("SELECT * FROM retention_forecast").fetchone()))
    pts = lambda x: f"{100 * (x or 0) / f['book_arr']:.1f} pts"
    print(f"\nForecast, renewals in the next {f['days']} days: {f['renewals']} ({m(f['up_for_renewal'])} of {m(f['book_arr'])})")
    print(f"  loss, the data's rules {m(f['data_loss'])} ({pts(f['data_loss'])} of GRR); with the owners' "
          f"{f['owner_calls']} forecasts {m(f['owner_loss'])}; upside {m(f['upside'])}")
    print(f"  what happened (demo outcomes, {f['with_outcome']} renewals): loss {m(f['actual_loss'])} "
          f"({pts(f['actual_loss'])}), growth {m(f['actual_gain'])}")
    print("\nWhat a call is worth: accounts with a churn task this year, at their next renewal")
    con.sql("SELECT grp, accounts, churned, round(kept_share * 100, 1) AS kept_pct, arr_before, renewal_arr FROM call_effect ORDER BY grp").show()
    v = con.sql("SELECT accounts, arr, value FROM call_value").fetchone()
    print(f"Today: {v[0]} accounts at Act not called yet ({m(v[1])}). Called in time, at this year's rates, about "
          f"{m(v[2])} more would stay ({pts(v[2])} of GRR). Planted effect, small groups: the method, not a promise.")


if __name__ == "__main__":
    main()
