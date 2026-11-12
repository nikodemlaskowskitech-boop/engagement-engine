"""
One cockpit page per person, holding only what their role may see (ROLES.md).

    python roles.py          rebuild data/pages/ from data/views_data.json (export_views.py calls it too)

The matrix is settings.toml [roles]. The team is the boundary: everyone under
the same manager (cs_team.csv). Each page gets a copy of the export cut down to
the person's accounts, so what a role may not see was never put in the file:
data minimisation, not a filter. Tabs the role doesn't have are taken out of
the page too. A small JSON block (page-meta) tells the page who it's for and
what that person may change; cockpit_sync.py checks every saved decision again
on our side, so the page is never trusted.

Plain Python, no DuckDB: it works on the export, after export_views.py.
"""

import json
import re
import statistics
import sys
from pathlib import Path

from settings import SETTINGS, OUTPUTS

ROLES = SETTINGS["roles"]
PAGES = OUTPUTS / "pages"
TEMPLATE = Path(__file__).resolve().parent / "views_template.html"

# what each part of the export is keyed by: rows with an account_id outside the scope go
ACCOUNT_ROWS = ["touches", "forecasts", "next_steps", "onboarding", "time_to_action",
                "churn_signals", "renewals", "tasks"]


def people(team):
    """Everyone who gets a page: managers (from the manager column), CSMs and pool
    members. Pools are queues, not people. Returns {id: {"role", "name", "manager"}}."""
    out = {}
    for m in sorted({t["manager"] for t in team if t.get("manager")}):
        out[m] = {"role": "manager", "name": m.replace("mgr_", "").capitalize(), "manager": m}
    for t in team:
        if t["owner_type"] in ("csm", "member"):
            out[t["owner_id"]] = {"role": t["owner_type"], "name": t["owner_name"], "manager": t["manager"]}
    return out


def owners(a):
    """An account's owners: its named owner and its pool (the export's owner_id is the
    CSM, else the pool member, else the pool)."""
    return {o for o in (a.get("owner_id"), a.get("pool")) if o}


def scope(person_id, person, accounts, team):
    """The account ids on this person's page, and the ones that are their own."""
    cfg = ROLES[person["role"]]
    if cfg["scope"] == "book":
        return {a["account_id"] for a in accounts}, set()
    mgr = person["manager"]
    regions = {t["region"] for t in team if t["manager"] == mgr}             # where the team's people sit
    ids, own = set(), set()
    for a in accounts:
        if a.get("manager") == mgr:                                          # owned by someone in the team
            ids.add(a["account_id"])
            if a.get("owner_id") == person_id:
                own.add(a["account_id"])
        elif cfg.get("self_service") and not owners(a) and a["region"] in regions:   # no owner: Self-service, or no CSM yet
            ids.add(a["account_id"])
    return ids, own


def edit_rights(person, ids, own, accounts):
    """For each action the role may take: the accounts it may take it on. "own" = the
    person's accounts; "team" = the team's owned accounts (an unowned one has nobody to act)."""
    team_owned = {a["account_id"] for a in accounts if a["account_id"] in ids and owners(a)}
    return {action: sorted(own if where == "own" else team_owned)
            for action, where in ROLES[person["role"]].get("edit", {}).items()}


def outcomes_total(renewals, too_late):
    """'Did it work?' as team totals only (no row per account), the same sums the page draws."""
    lost = [r for r in renewals if r.get("lost_revenue")]
    flagged = [r for r in renewals if r.get("flagged")]
    tp = [r for r in renewals if r.get("flagged") and r.get("lost_revenue")]
    warn = [r["warning_days"] for r in tp if r.get("warning_days") is not None]
    edges = [0, 30, 60, 90, 120, 150, 180]
    bins = [{"from": e, "to": edges[i + 1] if i + 1 < len(edges) else None,
             "n": sum(1 for w in warn if w >= e and (i + 1 == len(edges) or w < edges[i + 1]))}
            for i, e in enumerate(edges)]
    act = [r for r in renewals if r.get("flag_status") == "Act" and r.get("tier") != "Self-service"]
    calls = [{"group": g, "n": len(xs), "churned": sum(r.get("outcome") == "churned" for r in xs),
              "kept": sum(not r.get("lost_revenue") for r in xs)}
             for g, xs in (("Churn call held", [r for r in act if r.get("had_churn_call")]),
                           ("No churn call", [r for r in act if not r.get("had_churn_call")]))]
    valued = [r for r in renewals if r.get("renewal_arr") is not None]
    total = lambda xs, k: sum(r.get(k) or 0 for r in xs)
    value = []
    if valued:
        for g in ("Act", "Watch", "Shelfware", "No churn flag"):
            xs = [r for r in valued if (r.get("flag_status") or "No churn flag") == g]
            if xs:
                value.append({"group": g, "n": len(xs), "arr": total(xs, "arr"),
                              "at_risk": total(xs, "data_at_risk"), "lost": total(xs, "lost_arr")})
        value.append({"group": "All renewals", "n": len(valued), "arr": total(valued, "arr"),
                      "at_risk": total(valued, "data_at_risk"), "lost": total(valued, "lost_arr")})
    # small groups give away someone's own result (two accounts, both one person's): no sums
    k = ROLES.get("min_group", 5)
    # and once one group is hidden, the total goes too: total minus the others would give it back
    if any(g["n"] < k for g in value):
        for g in value:
            if g["n"] < k or g["group"] == "All renewals":
                g.update(arr=None, at_risk=None, lost=None, small=True)
    if any(c["n"] < k for c in calls):           # two rows: one hidden would follow from the other
        for c in calls:
            c.update(churned=None, kept=None, small=True)
    return {"min_group": k, "renewals": len(renewals), "lost": len(lost), "flagged": len(flagged), "tp": len(tp),
            "fp": len(flagged) - len(tp), "fn": len(lost) - len(tp),
            "tn": len(renewals) - len(flagged) - (len(lost) - len(tp)),
            "median_warning": round(statistics.median(warn)) if warn else None,
            "too_late": sum(w < too_late for w in warn), "warned": len(warn),
            "warning_bins": bins, "calls": calls, "value": value}


def peer_medians(export, owners_in):
    """The medians a page compares people with, by kind of owner (csm, member, pool), over the
    owners in owners_in, computed here on the whole export: a page cut to one team can't work
    out a book-wide median itself (6 Nov 2026). Numbers only, no names. Same rules as the page."""
    from datetime import date, timedelta
    as_of = date.fromisoformat(export["as_of"])
    since = (as_of - timedelta(days=29)).isoformat()                  # the page's "last 30 days"
    accounts, tasks = export["accounts"], export.get("tasks", [])
    met = {t["account_id"] for t in export.get("touches", []) if t["event"] == "meeting_held" and t["date"] >= since}
    team = {t["owner_id"]: t for t in export["team"]}

    def book(o):
        return [a for a in accounts if (a.get("pool") if team[o]["owner_type"] == "pool" else a.get("owner_id")) == o]

    def work(o):                                  # tasks routed to them, plus a member's own accounts' pool tasks
        t, own = team[o], {a["account_id"] for a in accounts if a.get("owner_id") == o}
        return [x for x in tasks if x["routed_to"] == o
                or (t["owner_type"] == "member" and x["routed_to"] == t.get("pool") and x["account_id"] in own)]

    def med(xs):
        xs = [x for x in xs if x is not None]
        return round(statistics.median(xs), 2) if xs else None

    out = {}
    for kind in ("csm", "member", "pool"):
        people = [o for o in owners_in if o in team and team[o]["owner_type"] == kind]
        if not people:
            continue
        stats = []
        for o in people:
            b = book(o)
            calls = [r["days_to_call"] for r in export.get("time_to_action", []) if r["routed_to"] == o and r.get("call_date")]
            stats.append({"accs": len(b), "open": sum(x["action_status"] == "Open" for x in work(o)),
                          "meetings": sum(1 for t in export.get("touches", []) if t["event"] == "meeting_held"
                                          and t.get("owner_id") == o and t["date"] >= since),
                          "met": sum(a["account_id"] in met for a in b) / len(b) if b else None,
                          "calls": statistics.median(calls) if calls else None})
        out[kind] = {k: med([s[k] for s in stats]) for k in ("accs", "open", "meetings", "met", "calls")}
        out[kind]["met_all"] = med([s["met"] or 0 for s in stats])      # owners with no accounts as 0%
    return out


def slice_export(export, person_id, person):
    """The export as this person's page may hold it, and the page's meta."""
    accounts, team = export["accounts"], export["team"]
    ids, own = scope(person_id, person, accounts, team)
    cfg = ROLES[person["role"]]
    page = {k: v for k, v in export.items()}
    page["accounts"] = [a for a in accounts if a["account_id"] in ids]
    for key in ACCOUNT_ROWS:
        page[key] = [r for r in export.get(key, []) if r.get("account_id") in ids]
    page["handovers"] = {k: v for k, v in export.get("handovers", {}).items() if k in ids}
    # the weekly history: today's accounts in scope, plus the accounts no longer in the
    # book (left, or no CRM row) that the same rule puts in scope; those carry a key
    # of their own (ob_1, ...), never their id or name
    hist = export.get("history") or {}
    off = hist.get("off_book", [])
    off_ids, _ = scope(person_id, person, off, team) if off else (set(), set())
    keep = ids | off_ids
    page["history"] = {"weeks": hist.get("weeks", []),
                       "off_book": [o for o in off if o["account_id"] in off_ids],
                       "flags": [r for r in hist.get("flags", []) if r[1] in keep],
                       "tasks": [r for r in hist.get("tasks", []) if r[1] in keep]}
    # "against the median": the whole book's people of the same kind, or only the team's ([roles] peer_median)
    everyone = [t["owner_id"] for t in export["team"]]
    peers = everyone if cfg["scope"] == "book" or ROLES.get("peer_median", "book") == "book" \
        else [t["owner_id"] for t in export["team"] if t["manager"] == person["manager"]]
    page["peer_medians"] = {"scope": "book" if peers == everyone else "team", **peer_medians(export, peers)}
    if cfg["outcomes"] == "team_total":                   # totals only: no row per account
        page["outcomes_total"] = outcomes_total(page["renewals"], export["settings"]["too_late_days"])
        page["renewals"] = []
    meta = {"person": person_id, "role": person["role"], "name": person["name"], "tabs": cfg["tabs"],
            "accounts": sorted(ids), "edit": edit_rights(person, ids, own, accounts),
            "outcomes": cfg["outcomes"]}
    return page, meta


def strip_tabs(html, keep):
    """Take the tabs this role doesn't have out of the page: the button and its section."""
    for view in re.findall(r'data-view="([a-z]+)"', html):
        if view in keep:
            continue
        html = re.sub(rf'\s*<button role="tab" id="tab-{view}"[^>]*>.*?</button>', "", html, count=1)
        html = re.sub(rf'\s*<section class="view" id="view-{view}".*?</section>', "", html, count=1, flags=re.S)
    return html


def safe_json(obj):
    """JSON for a <script> tag: "</" escaped so no text can close the tag early."""
    return json.dumps(obj, separators=(",", ":")).replace("</", "<\\/")


def build(export, template=None, out=PAGES, sandbox=False):
    """Write one page per person, leader.html and index.html (the landing page).
    sandbox=True: the public demo site's pages (forms save in the visitor's browser only)."""
    template = template or TEMPLATE.read_text(encoding="utf-8")
    everyone = {ROLES["leader_id"]: {"role": "leader", "name": ROLES["leader_name"], "manager": None},
                **people(export["team"])}
    files = {pid: ("leader.html" if p["role"] == "leader" else f"{pid}.html") for pid, p in everyone.items()}
    kind = {"leader": "Leader", "manager": "Manager", "csm": "CSM", "member": "Pool member"}
    out.mkdir(parents=True, exist_ok=True)
    written = {}
    for pid, person in everyone.items():
        page, meta = slice_export(export, pid, person)
        meta["sandbox"] = bool(sandbox)
        options = "".join(f'<option value="{files[o]}"{" selected" if o == pid else ""}>'
                          f'{everyone[o]["name"]} ({kind[everyone[o]["role"]]})</option>' for o in everyone)
        html = strip_tabs(template, set(meta["tabs"]))
        html = (html.replace("__PAGE_OPTIONS__", options)
                    .replace("__PAGE_META__", safe_json(meta))
                    .replace("__VIEWS_DATA__", safe_json(page)))
        text = ("<!doctype html>\n<meta charset=\"utf-8\">\n"
                "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n" + html)
        (out / files[pid]).write_text(text, encoding="utf-8")
        written[pid] = (files[pid], len(meta["accounts"]))
    landing = files[ROLES["leader_id"]] if ROLES["landing"] == "leader" else files[ROLES["landing"]]
    (out / "index.html").write_text((out / landing).read_text(encoding="utf-8"), encoding="utf-8")
    return written


if __name__ == "__main__":
    # python roles.py         -> data/pages/ (the cockpit, as export_views.py writes it)
    # python roles.py --site  -> data/<[aws] site_folder>/: the public demo site's pages, in sandbox
    #                            mode (the engine Lambda copies them to the site bucket)
    sys.stdout.reconfigure(encoding="utf-8")
    site = "--site" in sys.argv[1:]
    src = OUTPUTS / "views_data.json"
    if not src.exists():
        sys.exit(f"{src} is missing: run python export_views.py first")
    out = OUTPUTS / SETTINGS["aws"]["site_folder"].strip("/") if site else PAGES
    for pid, (name, n) in build(json.loads(src.read_text(encoding="utf-8")), out=out, sandbox=site).items():
        print(f"  {name:<18} {n:>4} accounts")
    print(f"Saved {out.relative_to(OUTPUTS.parent).as_posix()}/ (index.html = the landing page)"
          + (" in sandbox mode, for the public site" if site else ""))
