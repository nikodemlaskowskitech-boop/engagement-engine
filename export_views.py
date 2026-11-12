"""
Export everything the four views need to one file: data/views_data.json,
and build the views page from it: data/views.html (views_template.html with
the data inside, so it opens in any browser, offline).

The views page only draws; every number is computed here, in SQL, with the
same views the reports use (cairnpath_db.py, team_report.py and
onboarding_report.py), so the page and the reports can never disagree.

What goes in:
  accounts        every CRM account: tier, region, owner, ARR, contract and QBR dates,
                  30-day utilisation for each day it can be measured, weekly
                  active users for each of the 13 weeks, and its open flags
                  (activation, churn, expansion, QBR, journeys), churn and
                  expansion with their value (ARR at risk, opportunity)
  touches         every CS touch on the timeline (emails, opens, workshops,
                  meetings), with owner and topic
  onboarding      welcomed vs. control, one row per new account
  time_to_action  one row per churn or expansion Act account with an owner
  team            the CS team (owners, types, managers)
  churn_signals   every churn flag row, with its first call (the overview's sieve)
  renewals        every renewal after the data ends, with its outcome and
                  whether it was flagged (backtest.py's view; empty without outcomes)
  tasks           every task sent to a person (action_status): who has it,
                  its manager, Open/Claimed/Done/Resolved and its age, and
                  whether a manager reassigned it (empty before actions.py runs)
  handovers       finished AI handover briefs (handover.py), per account: the
                  brief with real names back in, its sources, what is unverified,
                  what the model saw (redacted) and when and why it was written.
                  The redaction key (<acc>_key.json) is never exported.
  settings        the few thresholds the page labels with

Real user IDs never leave this script: weekly active users are counts.

Run generate_events.py, flags.py and journeys.py first, then:
    python export_views.py
"""

import csv
import json
import sys
from pathlib import Path

import backtest
import onboarding_report
import retention
import roles
import team_report
from settings import SETTINGS
from cairnpath_db import connect

sys.stdout.reconfigure(encoding="utf-8")

DATA = Path(__file__).resolve().parent / "data"
FLAGS = ("activation", "churn", "expansion", "qbr", "journey")


def handovers():
    """Finished handover briefs from data/handover/ (empty until handover.py runs).
    Only a finished answer is shown: a cut-short or unchecked brief stays out."""
    folder, out = DATA / "handover", {}
    stories = Path(__file__).resolve().parent / SETTINGS["handover"]["stories_folder"] / "directory.csv"
    if not stories.exists():
        return out
    with stories.open(encoding="utf-8") as f:
        for entry in csv.DictReader(f):
            acc = entry["account_id"]
            md, check, meta, sent = (folder / f"{acc}{s}" for s in (".md", "_check.json", "_meta.json", "_sent.txt"))
            if not (md.exists() and check.exists() and sent.exists()):
                continue
            checks = json.loads(check.read_text(encoding="utf-8"))
            if checks.get("stop_reason") != "end_turn":
                continue
            m = json.loads(meta.read_text(encoding="utf-8")) if meta.exists() else {}
            stale_file = folder / f"{acc}_stale.json"   # handover.py --dry-run: the context moved on since
            stale = json.loads(stale_file.read_text(encoding="utf-8")) if stale_file.exists() else None
            text = md.read_text(encoding="utf-8")
            body, _, tail = text.partition("\n---\n")
            sources = [{"id": i, "title": t} for i, t in
                       ((l.split("**")[1], l.split("**", 2)[2].strip()) for l in tail.splitlines() if l.startswith("- **"))]
            unverified = []
            if checks.get("unknown_citations"):
                unverified.append("Citations to sources that don't exist: " + ", ".join(checks["unknown_citations"]))
            if checks.get("unverified_numbers"):
                unverified.append("Numbers found in no source: " + ", ".join(checks["unverified_numbers"]))
            unverified += [f"No source: {u}" for u in checks.get("uncited_sentences", [])]
            # a version stamp the confirm points at (older briefs: the file's time)
            from datetime import datetime, timezone
            version = m.get("written_at") or datetime.fromtimestamp(md.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
            out[acc] = {"from": entry["handover_from"], "to": entry["handover_to"], "brief": body.strip(),
                        "sources": sources, "unverified": unverified, "sent": sent.read_text(encoding="utf-8"),
                        "version": version, "written_as_of": m.get("written_as_of"), "why": m.get("why", []),
                        "stale": stale["changes"] if stale else []}
    return out


def rows(con, sql):
    """Run a query and return a list of dicts, dates as text."""
    rel = con.sql(sql)
    return [{k: (str(v) if hasattr(v, "isoformat") else v) for k, v in zip(rel.columns, r)}
            for r in rel.fetchall()]


class OffBook:
    """Accounts no longer in today's book (they left, or never had a CRM row) are on
    no page, so they travel under a key of their own (ob_1, ...) with only what the
    segment bar needs: tier, region, owner and manager, never their id or name.
    The trends and the retention numbers share one set of keys (history.off_book)."""

    def __init__(self, con, on_book):
        self.on_book, self.key, self.rows = on_book, {}, []
        self.churned = {r[0]: r for r in con.sql("""
            SELECT c.account_id, c.tier, c.region, coalesce(c.csm_owner, c.pool_owner, c.cs_pool) AS owner_id,
                   c.cs_pool AS pool, t.manager
            FROM crm_all c LEFT JOIN cs_team t ON t.owner_id = coalesce(c.csm_owner, c.pool_owner, c.cs_pool)
            WHERE coalesce(c.status, 'active') = 'churned'""").fetchall()}

    def ref(self, a):
        if a in self.on_book:
            return a
        if a not in self.key:
            self.key[a] = f"ob_{len(self.key) + 1}"
            r = self.churned.get(a)
            self.rows.append({"account_id": self.key[a], "why": "left" if r else "not in the CRM",
                              "tier": r[1] if r else None, "region": r[2] if r else None,
                              "owner_id": r[3] if r else None, "pool": r[4] if r else None,
                              "manager": r[5] if r else None})
        return self.key[a]


def history(off):
    """The weekly history (history.py) for the trends: the flags that ask for action
    and the queue (tasks Open or Claimed), one row per week and account. Empty
    without history.py's files."""
    if not (DATA / "history_flags.csv").exists() or not (DATA / "history_tasks.csv").exists():
        return {"weeks": [], "off_book": off.rows, "flags": [], "tasks": []}
    with (DATA / "history_flags.csv").open(encoding="utf-8") as f:
        hf = [r for r in csv.DictReader(f) if r["status"] not in ("Activated", "Activating")]
    with (DATA / "history_tasks.csv").open(encoding="utf-8") as f:
        ht = list(csv.DictReader(f))
    weeks = sorted({r["week"] for r in hf} | {r["week"] for r in ht})
    wi = {w: i for i, w in enumerate(weeks)}
    for a in sorted({r["account_id"] for r in hf + ht}):
        off.ref(a)                                   # keys in id order, so they don't move between runs
    return {"weeks": weeks, "off_book": off.rows,
            # [week, account, flag, status]
            "flags": [[wi[r["week"]], off.ref(r["account_id"]), r["flag"], r["status"]] for r in hf],
            # [week, account, flag, status, routed_to, action_status, age_days]
            "tasks": [[wi[r["week"]], off.ref(r["account_id"]), r["flag"], r["status"], r["routed_to"],
                       r["action_status"], int(r["age_days"])] for r in ht]}


def retention_export(con, off):
    """GRR and NRR (retention.py's views) for the business case page: this year
    against the year before, the forecast and what a call is worth, with the
    accounts behind each number (in-book ids, or off-book keys)."""
    def one(sql):
        rel = con.sql(sql)
        row = rel.fetchone()
        return {k: (str(v) if hasattr(v, "isoformat") else v) for k, v in zip(rel.columns, row)} if row else {}
    return {
        "year": one("SELECT * FROM retention_year"),
        "prior": one("SELECT * FROM bridge_rates_prior") if con.sql("SELECT count(*) FROM arr_bridge").fetchone()[0] else None,
        "this_bridge": one("SELECT grr, nrr FROM bridge_rates_this"),
        "forecast": one("SELECT * FROM retention_forecast"),
        "calls": rows(con, "SELECT * FROM call_effect ORDER BY grp"),
        "call_value": one("SELECT * FROM call_value"),
        "licence_only": True,     # all ARR here is licence (volume and add-ons); no services in the book
        # the year's cohort, so the page can work out GRR and NRR for any segment:
        # [account, movement (churned/contraction/expansion/flat), ARR at the start, ARR now]
        "cohort": [[off.ref(a), m, s0, s1] for a, m, s0, s1 in con.sql(
            "SELECT account_id, movement, start_arr, end_arr FROM retention_accounts ORDER BY account_id").fetchall()],
        # [account, ARR, renewal date, the data's loss, the owner's, upside, actual loss, actual gain]
        "renewals_ahead": [[a, arr, str(d), dl, ol, up, al, ag] for a, arr, d, dl, ol, up, al, ag in con.sql(
            "SELECT account_id, arr, renewal_date, data_loss, owner_loss, upside, actual_loss, actual_gain "
            "FROM retention_forecast_accounts ORDER BY renewal_date, account_id").fetchall()],
        # [account, group, outcome, ARR before, ARR after]
        "call_accounts": [[off.ref(a), g, o, b, r] for a, g, o, b, r in con.sql(
            "SELECT account_id, grp, outcome, arr_before, renewal_arr FROM call_effect_accounts ORDER BY account_id").fetchall()],
        "call_waiting": [a for (a,) in con.sql("SELECT account_id FROM call_waiting ORDER BY account_id").fetchall()],
    }


con = connect()
team_report.add_views(con)          # time_to_action, meetings_90d
onboarding_report.add_views(con)    # onboarding
backtest.add_views(con)             # backtest (empty without renewal outcomes)
retention.add_views(con)            # GRR, NRR, the forecast, what a call is worth

as_of = str(con.sql("SELECT as_of_date FROM as_of").fetchone()[0])

# The days a 30-day utilisation exists (the first 29 days of data can't have one)
util_dates = [str(d) for (d,) in con.sql(
    "SELECT DISTINCT event_date FROM daily_util_30d ORDER BY 1").fetchall()]
util = {}
for account_id, day, value in con.sql(
        "SELECT account_id, event_date, util FROM daily_util_30d").fetchall():
    util.setdefault(account_id, {})[str(day)] = int(value)

# Weekly active users: 13 weeks, the last one ending on the as-of date
wau = {}
for account_id, weeks_ago, users in con.sql("""
        SELECT e.account_id, (a.as_of_date - e.event_date) // 7 AS weeks_ago,
               count(DISTINCT e.user_id) AS users
        FROM events e, as_of a
        WHERE e.user_id IS NOT NULL AND (a.as_of_date - e.event_date) // 7 < 13
        GROUP BY ALL""").fetchall():
    wau.setdefault(account_id, [0] * 13)[12 - weeks_ago] = users      # oldest week first
week_ends = [str(d) for (d,) in con.sql(
    "SELECT as_of_date - (7 * (12 - i))::INTEGER FROM as_of, range(13) t(i) ORDER BY i").fetchall()]

# Open flags per account, straight from the flag files
flags = {}
for flag in FLAGS:
    path = DATA / f"flags_{flag}.csv"
    if not path.exists():
        sys.exit(f"{path} is missing: run python flags.py, then python journeys.py")
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            flags.setdefault(r["account_id"], []).append(
                {"flag": flag, "status": r["status"], "reasons": r["reasons"],
                 "routed_to": r["routed_to"]})

# What each churn or expansion row is worth (flags.py: ARR at risk, opportunity)
value_path = DATA / "account_value.csv"
if not value_path.exists():
    sys.exit(f"{value_path} is missing: run python flags.py")
with value_path.open(encoding="utf-8") as f:
    for r in csv.DictReader(f):
        for fl in flags.get(r["account_id"], []):
            if fl["flag"] == r["flag"] and fl["status"] == r["status"]:
                fl["value"], fl["value_basis"] = round(float(r["amount"])), r["basis"]

accounts = rows(con, """
    SELECT c.account_id, c.account_name, c.tier, c.region, c.contract_start, c.renewal_date,
           c.contracted_events, c.add_ons, c.last_qbr_date, c.next_qbr_date,
           coalesce(c.csm_owner, c.pool_owner, c.cs_pool) AS owner_id,   -- the named owner first
           c.cs_pool AS pool,
           c.arr, c.arr_estimated,
           t.owner_name AS owner, t.manager
    FROM crm c
    LEFT JOIN cs_team t ON t.owner_id = coalesce(c.csm_owner, c.pool_owner, c.cs_pool)
    ORDER BY c.account_id""")
for a in accounts:
    a["util"] = [util.get(a["account_id"], {}).get(d) for d in util_dates]
    a["wau"] = wau.get(a["account_id"], [0] * 13)
    a["flags"] = flags.get(a["account_id"], [])

touches = rows(con, """
    SELECT account_id, event_date AS date, event_name AS event,
           properties.topic AS topic, properties.owner_id AS owner_id,
           context.source AS source
    FROM engagement
    WHERE event_name NOT IN ('forecast_set', 'next_step_set')   -- internal: exported below
    -- ties on the same minute broken by account and event: the same data always
    -- exports in the same order, so two runs can be compared file to file
    ORDER BY "timestamp", account_id, event_name, event_id""")

off = OffBook(con, {a["account_id"] for a in accounts})
export = {
    "as_of": as_of,
    "util_dates": util_dates,
    "week_ends": week_ends,
    "accounts": accounts,
    "touches": touches,
    # renewal forecasts set in the cockpit (cockpit_sync.py), oldest first: the
    # offline page shows them; the published page reads its own database
    "forecasts": rows(con, """
        SELECT account_id, "timestamp" AS decided_at, properties.owner_id AS by,
               properties.outcome AS role, properties.status AS call,
               properties.amount AS amount, properties.note AS reason
        FROM engagement WHERE event_name = 'forecast_set'
        ORDER BY "timestamp", event_id"""),
    # next steps planned in the cockpit ("action planned"), oldest first
    "next_steps": rows(con, """
        SELECT e.account_id, e."timestamp" AS decided_at, e.properties.owner_id AS by,
               e.properties.outcome AS role, e.properties.status AS type,
               e.properties.due AS due, e.properties.note AS note, a.as_of_date AS as_of
        FROM engagement e, as_of a WHERE e.event_name = 'next_step_set'
        ORDER BY e."timestamp", e.event_id"""),
    "onboarding": rows(con, "SELECT * FROM onboarding ORDER BY account_id"),
    "time_to_action": rows(con, """
        SELECT t.*, o.owner_name AS owner, o.owner_type, o.manager
        FROM time_to_action t JOIN cs_team o ON o.owner_id = t.routed_to
        ORDER BY t.account_id"""),
    "team": rows(con, "SELECT * FROM cs_team ORDER BY owner_type, owner_id"),
    # the overview's sieve: every churn signal with who it went to and whether
    # (and how fast) a person acted on it
    "churn_signals": rows(con, """
        SELECT f.account_id, c.tier, f.status, f.routed_to, f.reasons,
               t.signal_date, t.call_date, t.days_to_call, t.via
        FROM read_csv('data/flags_churn.csv', all_varchar = true) f
        JOIN crm c USING (account_id)
        LEFT JOIN time_to_action t ON t.account_id = f.account_id AND t.flag = 'churn'
        ORDER BY f.account_id"""),
    # "did it work?": every renewal after the data ends, flagged or not
    "renewals": rows(con, "SELECT * EXCLUDE (account_name) FROM backtest ORDER BY renewal_date, account_id"),
    # the manager's queue: every task sent to a person and what became of it.
    # Its manager is the owner's manager; a manager's own task (an escalation,
    # a hand-back to approve) is theirs; CS ops items go to the account's manager
    "tasks": rows(con, """
        SELECT s.account_id, s.flag, s.status, s.routed_to, s.action, s.sent_on,
               s.action_status, s.age_days, s.assigned_by, s.assigned_from,
               coalesce(o.manager, CASE WHEN s.routed_to LIKE 'mgr_%' THEN s.routed_to END,
                        ao.manager) AS manager
        FROM action_status s
        LEFT JOIN cs_team o  ON o.owner_id = s.routed_to
        LEFT JOIN crm c      USING (account_id)
        LEFT JOIN cs_team ao ON ao.owner_id = coalesce(c.csm_owner, c.pool_owner, c.cs_pool)
        ORDER BY s.age_days DESC, s.account_id, s.flag, s.status"""),
    "handovers": handovers(),
    "history": history(off),
    "retention": retention_export(con, off),
    "settings": {"fast_days": SETTINGS["journeys"]["act_for_days"],
                 "stale_task_days": SETTINGS["cockpit"]["stale_task_days"],
                 "below_median_points": SETTINGS["cockpit"]["below_median_points"],
                 "call_target_days": SETTINGS["journeys"]["act_for_days"],
                 "too_late_days": SETTINGS["backtest"]["too_late_days"],
                 "churn_line": SETTINGS["churn"]["util_below"],
                 "follow_up_days": SETTINGS["follow_ups"]["after_days"],
                 "currency": SETTINGS["money"]["currency"],
                 "cs_cost_per_head": SETTINGS["money"].get("cs_cost_per_head", 0),
                 "forecast_gap_share": SETTINGS["money"]["forecast_gap_share"],
                 "open_no_flags_up_to": SETTINGS["cockpit"].get("open_no_flags_up_to", 15)},
}

out = DATA / "views_data.json"
data_json = json.dumps(export, separators=(",", ":"))
out.write_text(data_json, encoding="utf-8")

# The page: the template with the data dropped into its <script> tag ("</" is
# escaped so no account name can ever close the tag early)
template = (Path(__file__).resolve().parent / "views_template.html").read_text(encoding="utf-8")
page = DATA / "views.html"
page.write_text("<!doctype html>\n<meta charset=\"utf-8\">\n"
                "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
                + template.replace("__VIEWS_DATA__", data_json.replace("</", "<\\/"))
                          .replace("__PAGE_META__", "null").replace("__PAGE_OPTIONS__", ""),
                encoding="utf-8")
# One page per person (ROLES.md): each holds only what their role may see
pages = roles.build(export, template)
print(f"\nViews data as of {as_of}: {len(accounts)} accounts, {len(touches)} touches, "
      f"{len(export['onboarding'])} onboarding rows, {len(export['time_to_action'])} Act accounts, "
      f"{len(export['tasks'])} tasks")
print(f"Saved {out.relative_to(DATA.parent)} ({out.stat().st_size // 1024} KB)")
print(f"Saved {page.relative_to(DATA.parent)}: open it in a browser")
print(f"Saved data/pages/: {len(pages)} pages, one per person; index.html = the leader's (ROLES.md)")
