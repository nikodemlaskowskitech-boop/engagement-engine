"""
The facts layer (ARCHITECTURE.md fix C, 13 Nov 2026): the questions the product
answers, asked one way, for every place that needs them.

The cockpit export (export_views.py) was the only reader of the engine's facts,
and it built them in its own shape. The MCP server and the Ask box (Phase 9)
need the same facts asked differently: one account, the at-risk list for a
segment, a timeline. Without one layer each would rebuild the logic, and two
copies of a rule drift apart (the "called" rule did, until #150).

So the facts live here, as plain functions over a cairnpath_db connection that
return plain Python (dicts and lists, dates as text). export_views.py builds the
cockpit from them; the next readers call the same functions.

    import facts
    con = facts.connect_all()          # connect() plus the report views facts need
    facts.account(con, "acc_024")      # one account: facts, flags with reasons, value at stake
    facts.at_risk(con, manager="mgr_sophie")
    facts.timeline(con, "acc_024")     # customer touches, tasks, plans, forecasts
    facts.tasks(con, owner="pool_uki")

SQL computes every fact; this module only gathers and shapes them. Nothing here
calls a model.
"""
import csv
import sys

import team_report
from cairnpath_db import connect
from settings import SETTINGS, TASK_FLAGS, output_file

FLAGS = TASK_FLAGS            # settings.toml [work]


def rows(con, sql):
    """Run a query and return a list of dicts, dates as text."""
    rel = con.sql(sql)
    return [{k: (str(v) if hasattr(v, "isoformat") else v) for k, v in zip(rel.columns, r)}
            for r in rel.fetchall()]


def connect_all():
    """A connection with the views the facts need (time_to_action for "called")."""
    con = connect()
    team_report.add_views(con)
    return con


# --- the building blocks (export_views.py uses these for the whole book) ---

def as_of(con):
    return str(con.sql("SELECT as_of_date FROM as_of").fetchone()[0])


def util_dates(con):
    """The days a 30-day utilisation exists (the first 29 days of data can't have one)."""
    return [str(d) for (d,) in con.sql("SELECT DISTINCT event_date FROM daily_util_30d ORDER BY 1").fetchall()]


def week_ends(con):
    return [str(d) for (d,) in con.sql(
        "SELECT as_of_date - (7 * (12 - i))::INTEGER FROM as_of, range(13) t(i) ORDER BY i").fetchall()]


def flags_by_account():
    """Open flags per account, straight from flags.py's files, with the value at stake
    (ARR at risk or upside) on the churn and expansion rows."""
    flags = {}
    for flag in FLAGS:
        path = output_file("flags_" + flag)
        if not path.exists():
            sys.exit(f"{path} is missing: run python flags.py, then python journeys.py")
        with path.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                flags.setdefault(r["account_id"], []).append(
                    {"flag": flag, "status": r["status"], "reasons": r["reasons"], "routed_to": r["routed_to"],
                     "action": r.get("action", "")})      # what the flag asks for today (a task's text can lag it)
    value_path = output_file("account_value")
    if not value_path.exists():
        sys.exit(f"{value_path} is missing: run python flags.py")
    with value_path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            for fl in flags.get(r["account_id"], []):
                if fl["flag"] == r["flag"] and fl["status"] == r["status"]:
                    fl["value"], fl["value_basis"] = round(float(r["amount"])), r["basis"]
    return flags


ADDRESSED_FLAGS = ("expansion", "growth")     # growth in play: is anyone on it? (25 Nov 2026)
DAYS = SETTINGS["growth"]["addressed_days"]    # how long a meeting or a plan counts


def addressed(con):
    """Growth in play, addressed or not (25 Nov 2026, ARCHITECTURE.md fix M: the rule lived only
    in the cockpit's script, so the MCP servers and the Ask box couldn't answer it).
    {(account_id, flag): {"since", "k", "date", "due"}} for today's expansion and growth lines.
    since = the first week of the run the flag is in now (history.py's weekly replay), or today
    when the replay hasn't seen it yet. Addressed = a meeting held since then ("raised"), else a
    next step planned since then ("planned"), within [growth] addressed_days (a quarter); older
    than that it is "lapsed" (raised once, not since); else a growth line marked not a fit
    ("not_a_fit"); k is None while nobody was ever on it. The cockpit reads this; its own copy only adds decisions
    saved in the page after the export, and the weekly trend."""
    hist = output_file("history_flags")
    hist_tasks = output_file("history_tasks")
    flagged = "SELECT NULL::DATE AS week, NULL::VARCHAR AS account_id, NULL::VARCHAR AS flag WHERE false"
    weeks = "SELECT NULL::DATE AS week WHERE false"
    if hist.exists() and hist_tasks.exists():
        h = str(hist).replace("\\", "/")
        t = str(hist_tasks).replace("\\", "/")
        # the same weeks and rows as the cockpit's history (export_views.history: no Activated/Activating)
        flagged = (f"SELECT week::DATE AS week, account_id, flag FROM read_csv('{h}', header = true, all_varchar = true) "
                   "WHERE status NOT IN ('Activated', 'Activating')")
        weeks = f"SELECT week FROM ({flagged}) UNION SELECT week::DATE FROM read_csv('{t}', header = true, all_varchar = true)"
    found = {}
    for flag in ADDRESSED_FLAGS:
        path = output_file("flags_" + flag)
        if not path.exists():
            continue
        p = str(path).replace("\\", "/")
        for account_id, status, since, k, date, due in con.sql(f"""
            WITH wk AS (SELECT week, row_number() OVER (ORDER BY week) AS i FROM ({weeks}) GROUP BY week),
            fl AS (SELECT DISTINCT f.account_id, w.i, w.week FROM ({flagged}) f JOIN wk w USING (week)
                   WHERE f.flag = '{flag}'),
            runs AS (SELECT account_id, i, week, i - row_number() OVER (PARTITION BY account_id ORDER BY i) AS grp FROM fl),
            now_run AS (SELECT account_id, min(week) AS since FROM runs r
                        WHERE grp = (SELECT grp FROM runs x WHERE x.account_id = r.account_id
                                     AND x.i = (SELECT max(i) FROM wk))
                        GROUP BY account_id),
            today AS (SELECT o.account_id, o.status, coalesce(n.since, a.as_of_date) AS since
                      FROM read_csv('{p}', header = true, all_varchar = true) o
                      CROSS JOIN as_of a LEFT JOIN now_run n USING (account_id)),
            met AS (SELECT t.account_id, max(e.event_date) AS date FROM today t
                    JOIN engagement e ON e.account_id = t.account_id AND e.event_name = 'meeting_held'
                         AND e.event_date >= t.since
                    GROUP BY t.account_id),
            planned AS (SELECT t.account_id, arg_max(e."timestamp"::DATE, e."timestamp") AS date,
                               arg_max(e.properties.due, e."timestamp") AS due FROM today t
                        JOIN engagement e ON e.account_id = t.account_id AND e.event_name = 'next_step_set'
                             AND e."timestamp"::DATE >= t.since
                        GROUP BY t.account_id)
            -- a plan counts until its due date, a meeting from its day: each for addressed_days
            SELECT t.account_id, t.status, t.since,
                   CASE WHEN m.date >= a.as_of_date - {DAYS} THEN 'raised'
                        WHEN greatest(p.date, try_cast(p.due AS DATE)) >= a.as_of_date - {DAYS} THEN 'planned'
                        WHEN t.status = 'Not a fit' THEN 'not_a_fit'
                        WHEN m.date IS NOT NULL OR p.date IS NOT NULL THEN 'lapsed' END AS k,
                   coalesce(CASE WHEN m.date >= a.as_of_date - {DAYS} THEN m.date END,
                            CASE WHEN greatest(p.date, try_cast(p.due AS DATE)) >= a.as_of_date - {DAYS} THEN p.date END,
                            greatest(m.date, p.date)) AS date,
                   CASE WHEN NOT coalesce(m.date >= a.as_of_date - {DAYS}, false) THEN p.due END AS due
            FROM today t CROSS JOIN as_of a LEFT JOIN met m USING (account_id) LEFT JOIN planned p USING (account_id)
            ORDER BY t.account_id""").fetchall():
            found[(account_id, flag)] = {"since": str(since), "k": k, "date": str(date) if date else None,
                                         "due": str(due) if due else None}
    return found


def accounts(con, where=""):
    """Every account in today's book (or those matching a SQL condition on c./t.): its
    CRM facts, owner and manager, 30-day utilisation per day, weekly active users for
    13 weeks and its open flags."""
    dates = util_dates(con)
    util = {}
    for account_id, day, value in con.sql("SELECT account_id, event_date, util FROM daily_util_30d").fetchall():
        util.setdefault(account_id, {})[str(day)] = int(value)
    wau = {}
    for account_id, weeks_ago, users in con.sql("""
            SELECT e.account_id, (a.as_of_date - e.event_date) // 7 AS weeks_ago,
                   count(DISTINCT e.user_id) AS users
            FROM events e, as_of a
            WHERE e.user_id IS NOT NULL AND (a.as_of_date - e.event_date) // 7 < 13
            GROUP BY ALL""").fetchall():
        wau.setdefault(account_id, [0] * 13)[12 - weeks_ago] = users      # oldest week first
    flags = flags_by_account()
    done = addressed(con)
    for account_id, fls in flags.items():
        for fl in fls:
            if (account_id, fl["flag"]) in done:
                fl["addressed"] = done[(account_id, fl["flag"])]
    out = rows(con, f"""
        SELECT c.account_id, c.account_name, c.tier, c.region, c.contract_start, c.renewal_date,
               c.contracted_events, c.add_ons, c.last_qbr_date, c.next_qbr_date,
               coalesce(c.csm_owner, c.pool_owner, c.cs_pool) AS owner_id,   -- the named owner first
               c.cs_pool AS pool,
               c.arr, c.arr_estimated,
               coalesce(c.customer_since, c.contract_start) AS customer_since,   -- first signed: the cohort (22 Nov 2026)
               t.owner_name AS owner, t.manager
        FROM crm c
        LEFT JOIN cs_team t ON t.owner_id = coalesce(c.csm_owner, c.pool_owner, c.cs_pool)
        {'WHERE ' + where if where else ''}
        ORDER BY c.account_id""")
    for a in out:
        a["util"] = [util.get(a["account_id"], {}).get(d) for d in dates]
        a["wau"] = wau.get(a["account_id"], [0] * 13)
        a["flags"] = flags.get(a["account_id"], [])
    return out


def touches(con, account_id=None):
    """Customer touches (emails, meetings, workshops, clicks), oldest first. With what the
    CSM logged with a meeting in Slack, its outcome and note (18 Nov 2026: Last in My work
    opens it); the public site drops the free-text note (roles.py --site)."""
    only = f"AND account_id = '{_id(account_id)}'" if account_id else ""
    return rows(con, f"""
        SELECT account_id, event_date AS date, event_name AS event,
               properties.topic AS topic, properties.owner_id AS owner_id,
               context.source AS source, properties.outcome AS outcome, properties.note AS note
        FROM engagement
        WHERE event_name NOT IN ('forecast_set', 'next_step_set', 'recommendation_declined', 'outcome_checked',
                                 'commitment_made', 'commitment_closed')   -- internal: exported apart
        {only}
        -- ties on the same minute broken by account and event: the same data always
        -- exports in the same order, so two runs can be compared file to file
        ORDER BY "timestamp", account_id, event_name, event_id""")


def forecasts(con, account_id=None):
    """Renewal forecasts set in the cockpit (cockpit_sync.py), oldest first."""
    only = f"AND account_id = '{_id(account_id)}'" if account_id else ""
    return rows(con, f"""
        SELECT account_id, "timestamp" AS decided_at, properties.owner_id AS by,
               properties.outcome AS role, properties.status AS call,
               properties.amount AS amount, properties.note AS reason
        FROM engagement WHERE event_name = 'forecast_set' {only}
        ORDER BY "timestamp", event_id""")


def next_steps(con, account_id=None):
    """Next steps planned in the cockpit ("action planned"), oldest first."""
    only = f"AND e.account_id = '{_id(account_id)}'" if account_id else ""
    return rows(con, f"""
        SELECT e.account_id, e."timestamp" AS decided_at, e.properties.owner_id AS by,
               e.properties.outcome AS role, e.properties.status AS type,
               e.properties.due AS due, e.properties.note AS note, a.as_of_date AS as_of,
               e.properties.intended AS intended
        FROM engagement e, as_of a WHERE e.event_name = 'next_step_set' {only}
        ORDER BY e."timestamp", e.event_id""")


def declines(con, account_id=None):
    """Recommendations judged not needed (22 Nov 2026), oldest first: which flag's to-do, the
    reason ([work] decline_reasons), a snooze date for "later", who."""
    only = f"AND account_id = '{_id(account_id)}'" if account_id else ""
    return rows(con, f"""
        SELECT account_id, "timestamp" AS decided_at, properties.owner_id AS by,
               properties.topic AS flag, properties.status AS status,
               properties.outcome AS reason, properties.due AS due, properties.note AS note
        FROM engagement WHERE event_name = 'recommendation_declined' {only}
        ORDER BY "timestamp", event_id""")


def commitments(con, account_id=None):
    """Promises made to customers (22 Nov 2026), oldest first, each with how it ended if it has:
    from the Slack call form (source meeting) or tracked in the cockpit (from a document, or
    added). key = the commitment's event id, which a close names."""
    only = f"AND m.account_id = '{_id(account_id)}'" if account_id else ""
    return rows(con, f"""
        WITH closed AS (
            SELECT properties.topic AS key, properties.status AS result, "timestamp" AS closed_at,
                   properties.owner_id AS closed_by,
                   row_number() OVER (PARTITION BY properties.topic ORDER BY "timestamp" DESC, event_id DESC) AS n
            FROM engagement WHERE event_name = 'commitment_closed')
        SELECT m.event_id AS key, m.account_id, m."timestamp" AS made_at, m.properties.owner_id AS by,
               m.properties.note AS what, m.properties.due AS due,
               coalesce(m.properties.outcome, 'meeting') AS source, m.properties.status AS doc,
               m.context.source AS via, c.result, c.closed_at, c.closed_by
        FROM engagement m LEFT JOIN closed c ON c.key = m.event_id AND c.n = 1
        WHERE m.event_name = 'commitment_made' {only}
        ORDER BY m."timestamp", m.event_id""")


def outcome_checks(con, account_id=None):
    """Did a next step achieve its aim (22 Nov 2026), oldest first: the aim, yes | partly | no, who."""
    only = f"AND account_id = '{_id(account_id)}'" if account_id else ""
    return rows(con, f"""
        SELECT account_id, "timestamp" AS decided_at, properties.owner_id AS by,
               properties.outcome AS role, properties.intended AS intended,
               properties.status AS result, properties.note AS note
        FROM engagement WHERE event_name = 'outcome_checked' {only}
        ORDER BY "timestamp", event_id""")


def tasks(con, account_id=None, owner=None):
    """Every task sent to a person and what became of it, oldest first. Its manager is
    the owner's manager; a manager's own task is theirs; CS ops items go to the
    account's manager. owner = tasks routed to them, plus (for a pool member) the
    pool-queue tasks on their own accounts, the same rule as the cockpit."""
    cond = []
    if account_id:
        cond.append(f"s.account_id = '{_id(account_id)}'")
    if owner:
        o = _id(owner)
        cond.append(f"""(s.routed_to = '{o}' OR (o.owner_type = 'pool'
                         AND coalesce(c.csm_owner, c.pool_owner, c.cs_pool) = '{o}'
                         AND (SELECT owner_type FROM cs_team WHERE owner_id = '{o}') = 'member'))""")
    return rows(con, f"""
        SELECT s.account_id, s.flag, s.status, s.routed_to, s.action, s.sent_on,
               s.action_status, s.age_days, s.assigned_by, s.assigned_from,
               coalesce(o.manager, CASE WHEN s.routed_to LIKE 'mgr_%' THEN s.routed_to END,
                        ao.manager) AS manager
        FROM action_status s
        LEFT JOIN cs_team o  ON o.owner_id = s.routed_to
        LEFT JOIN crm c      USING (account_id)
        LEFT JOIN cs_team ao ON ao.owner_id = coalesce(c.csm_owner, c.pool_owner, c.cs_pool)
        {'WHERE ' + ' AND '.join(cond) if cond else ''}
        ORDER BY s.age_days DESC, s.account_id, s.flag, s.status""")


def time_to_action(con, account_id=None):
    """The one "called" rule (team_report.py): signal date, first call or QBR after it."""
    only = f"WHERE t.account_id = '{_id(account_id)}'" if account_id else ""
    return rows(con, f"""
        SELECT t.*, o.owner_name AS owner, o.owner_type, o.manager
        FROM time_to_action t JOIN cs_team o ON o.owner_id = t.routed_to
        {only}
        ORDER BY t.account_id""")


# --- the questions (the MCP server and the Ask box ask these) ---

def account(con, account_id):
    """One account: the same facts the cockpit shows, plus whether it was called.
    None if it isn't in today's book."""
    found = accounts(con, f"c.account_id = '{_id(account_id)}'")
    if not found:
        return None
    a = found[0]
    a["called"] = {r["flag"]: r["call_date"] for r in time_to_action(con, account_id)}
    a["as_of"] = as_of(con)
    return a


def timeline(con, account_id):
    """Everything that happened on one account, newest first: customer touches, tasks
    sent, plans and forecasts, each with its date and kind."""
    items = [{"date": t["date"], "kind": "touch", **t} for t in touches(con, account_id)]
    items += [{"date": t["sent_on"], "kind": "task", **t} for t in tasks(con, account_id)]
    items += [{"date": n["decided_at"][:10], "kind": "next_step", **n} for n in next_steps(con, account_id)]
    items += [{"date": f["decided_at"][:10], "kind": "forecast", **f} for f in forecasts(con, account_id)]
    return sorted(items, key=lambda x: str(x["date"]), reverse=True)


def at_risk(con, owner=None, manager=None, region=None, tier=None, statuses=("Act", "Watch", "Shelfware")):
    """The churn list for a segment, biggest ARR at risk first, with its reasons and
    whether it was called. owner = the named owner; manager = their team; region =
    where the customer is; tier = the coverage model."""
    cond = []
    for col, val in (("coalesce(c.csm_owner, c.pool_owner, c.cs_pool)", owner), ("t.manager", manager),
                     ("c.region", region), ("c.tier", tier)):
        if val:
            cond.append(f"{col} = '{_id(val)}'")
    called = {r["account_id"]: r["call_date"] for r in time_to_action(con) if r["flag"] == "churn"}
    out = []
    for a in accounts(con, " AND ".join(cond)):
        for f in a["flags"]:
            if f["flag"] == "churn" and f["status"] in statuses:
                out.append({"account_id": a["account_id"], "account_name": a["account_name"], "tier": a["tier"],
                            "region": a["region"], "owner": a["owner"], "manager": a["manager"],
                            "arr": a["arr"], "renewal_date": a["renewal_date"], "status": f["status"],
                            "reasons": f["reasons"], "at_risk": f.get("value", 0),
                            "called_on": called.get(a["account_id"])})
    return sorted(out, key=lambda r: (-(r["at_risk"] or 0), r["account_id"]))


def _id(value):
    """IDs and names go into SQL text: allow only what an ID or a region can contain."""
    v = str(value)
    if not all(ch.isalnum() or ch in "_-&. " for ch in v):
        raise ValueError(f"not an ID: {v!r}")
    return v
