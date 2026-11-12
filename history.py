"""Flag and task history: what the engine said, week by week, across the data.

The flags and the task queue only ever describe one day: the data's as-of date.
The trends on the cockpit (Leadership: tasks not picked up, new Enterprise
accounts with no CSM) need the same answers for every week of the year. This
script replays them: for each week it cuts the inputs back to that day and runs
the very same SQL (flags.add_views and the action_status view of
cairnpath_db.py), so a trend can never disagree with today's numbers.

What a replay can and can't know, as of an earlier day W:
  - events, CS history and the action log: everything dated up to W (exact)
  - the book: accounts that had signed by W (CRM customer_since) and hadn't left
    by then (a churned account's renewal_date is the day it left). A term that renewed after W is put
    back one year. Contract size and ARR are today's (the CRM keeps no history).
  - the CSM: none before csm_since (when the current CSM took the account)
  - QBRs: the last one held by W, and the next one held after W (as if booked)
  - the CRM fields nobody dates (tier, region, a pool member) are today's.
In real use each run would store its own snapshot instead (a CRM change can't be
replayed); for the demo, a replay of the generated year is the backfill.

Writes data/history_flags.csv (week, account_id, flag, status, routed_to) and
data/history_tasks.csv (week, the tasks still Open or Claimed that week: account,
flag, status, routed_to, action_status, age_days). export_views.py puts both in
the cockpit's data.

Run after flags.py (the action log and today's CRM); it reads the same inputs.
"""
import time

from cairnpath_db import connect
from settings import SETTINGS
import flags

STEP_DAYS = SETTINGS["history"]["step_days"]          # one snapshot every this many days
MIN_DAYS = SETTINGS["history"]["min_days"]            # a replay needs a full churn window behind it
FLAGS = ("activation", "churn", "expansion", "qbr")    # journeys are escalations of these: not replayed


def main():
    started = time.time()
    con = connect()
    flags.add_views(con)

    # Read each input once into a table; the views the engine uses then become
    # "this table, up to day W", swapped every week. Every view built on them
    # (utilisation, monthly_usage, the flags, action_status) follows by name.
    con.sql("CREATE TABLE events_all AS SELECT * FROM events")
    con.sql("CREATE TABLE engagement_all AS SELECT * FROM engagement")
    con.sql("CREATE TABLE crm_now AS SELECT * FROM crm_all")
    has_log = con.sql("SELECT count(*) FROM information_schema.tables WHERE table_name = 'action_log'").fetchone()[0]
    if has_log:
        con.sql("CREATE TABLE action_log_all AS SELECT * FROM action_log")
    # QBRs held, from the CS history: the CRM only knows the last and the next one
    con.sql("""CREATE TABLE qbrs_held AS
               SELECT DISTINCT account_id, event_date FROM engagement_all
               WHERE event_name = 'meeting_held' AND properties.topic = 'qbr'""")

    first, last = con.sql("SELECT min(event_date), max(event_date) FROM events_all").fetchone()
    weeks = []
    day = last
    while (day - first).days + 1 >= MIN_DAYS:      # a replay needs a full churn window behind it
        weeks.append(day)
        day = day.fromordinal(day.toordinal() - STEP_DAYS)
    weeks.reverse()
    print(f"History: {len(weeks)} snapshots, every {STEP_DAYS} days, {weeks[0]} to {weeks[-1]}"
          if weeks else f"History: not enough data ({(last - first).days + 1} days, needs {MIN_DAYS})")

    con.sql("""CREATE TABLE hist_flags (week DATE, account_id VARCHAR, flag VARCHAR,
                                        status VARCHAR, routed_to VARCHAR)""")
    con.sql("""CREATE TABLE hist_tasks (week DATE, account_id VARCHAR, flag VARCHAR, status VARCHAR,
                                        routed_to VARCHAR, action_status VARCHAR, age_days INTEGER)""")
    for w in weeks:
        replay(con, w, has_log)

    # A replay of the last day must give today's flags (flags.py's files): if not,
    # the replay is wrong, and so would every week before it be.
    if weeks:
        con.sql("CREATE TABLE today_flags AS " + " UNION ALL ".join(
            f"SELECT account_id, '{f}' AS flag, status FROM read_csv('data/flags_{f}.csv', all_varchar = true)"
            for f in FLAGS))
        diff = con.sql(f"""
            SELECT count(*) FROM (
                (SELECT account_id, flag, status FROM hist_flags WHERE week = DATE '{weeks[-1]}'
                 EXCEPT ALL SELECT account_id, flag, status FROM today_flags)
                UNION ALL
                (SELECT account_id, flag, status FROM today_flags
                 EXCEPT ALL SELECT account_id, flag, status FROM hist_flags WHERE week = DATE '{weeks[-1]}'))
        """).fetchone()[0]
        if diff:
            raise SystemExit(f"History: the replay of {weeks[-1]} differs from today's flags in {diff} rows")

    con.sql("COPY (SELECT * FROM hist_flags ORDER BY week, flag, account_id) TO 'data/history_flags.csv' (HEADER)")
    con.sql("COPY (SELECT * FROM hist_tasks ORDER BY week, account_id, flag) TO 'data/history_tasks.csv' (HEADER)")

    print("\nBy week (every fourth): flags at Act, tasks not picked up, oldest, Enterprise with no CSM")
    con.sql("""
        WITH w AS (SELECT DISTINCT week FROM hist_flags),
        n AS (SELECT week, row_number() OVER (ORDER BY week DESC) AS k FROM w)
        SELECT n.week,
               (SELECT count(*) FROM hist_flags f WHERE f.week = n.week AND f.status = 'Act') AS act,
               (SELECT count(*) FROM hist_tasks t WHERE t.week = n.week AND t.action_status = 'Open') AS not_picked_up,
               (SELECT max(age_days) FROM hist_tasks t WHERE t.week = n.week AND t.action_status = 'Open') AS oldest_days,
               (SELECT count(*) FROM hist_flags f WHERE f.week = n.week AND f.status = 'No CSM assigned') AS no_csm
        FROM n WHERE k % 4 = 1 ORDER BY n.week
    """).show()
    print(f"history.py: {len(weeks)} snapshots in {time.time() - started:.0f}s")


def replay(con, w, has_log):
    """Cut every input back to day w, then record the flags and the live queue."""
    d = f"DATE '{w}'"
    con.sql(f"CREATE OR REPLACE VIEW events AS SELECT * FROM events_all WHERE event_date <= {d}")
    con.sql(f"CREATE OR REPLACE VIEW engagement AS SELECT * FROM engagement_all WHERE event_date <= {d}")
    con.sql(f"""
        CREATE OR REPLACE VIEW crm_all AS
        WITH q AS (
            SELECT account_id,
                   max(event_date) FILTER (WHERE event_date <= {d}) AS last_qbr,
                   min(event_date) FILTER (WHERE event_date > {d})  AS next_qbr
            FROM qbrs_held GROUP BY account_id
        )
        SELECT c.* REPLACE (
                   -- a term that renewed after w: on w it was still in the year before
                   CASE WHEN c.contract_start > {d} THEN c.contract_start - 365 ELSE c.contract_start END AS contract_start,
                   CASE WHEN c.contract_start > {d} THEN c.contract_start ELSE c.renewal_date END AS renewal_date,
                   -- left by w: churned (crm_all keeps it, so its old events aren't a handoff gap)
                   CASE WHEN c.status = 'churned' AND c.renewal_date <= {d} THEN 'churned' ELSE 'active' END AS status,
                   CASE WHEN c.csm_since > {d} THEN NULL ELSE c.csm_owner END AS csm_owner,
                   CASE WHEN q.account_id IS NULL THEN c.last_qbr_date ELSE q.last_qbr END AS last_qbr_date,
                   CASE WHEN q.next_qbr IS NOT NULL THEN q.next_qbr
                        WHEN c.next_qbr_date > {d} THEN c.next_qbr_date END AS next_qbr_date)
        FROM crm_now c
        LEFT JOIN q USING (account_id)
        WHERE coalesce(c.customer_since, c.contract_start) <= {d}     -- had signed by w
    """)
    con.sql("CREATE OR REPLACE TABLE wk_flags AS " + " UNION ALL ".join(
        f"SELECT account_id, '{f}' AS flag, status, routed_to FROM {f}" for f in FLAGS))
    con.sql(f"INSERT INTO hist_flags SELECT {d}, * FROM wk_flags")
    if not has_log:
        return
    con.sql(f"CREATE OR REPLACE VIEW action_log AS SELECT * FROM action_log_all WHERE logged_on::DATE <= {d}")
    con.sql("CREATE OR REPLACE VIEW open_flags AS SELECT account_id, flag, status FROM wk_flags")
    con.sql(f"""INSERT INTO hist_tasks
                SELECT {d}, account_id, flag, status, routed_to, action_status, age_days
                FROM action_status WHERE action_status IN ('Open', 'Claimed')""")


if __name__ == "__main__":
    main()
