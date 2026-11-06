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


def build(export, template=None, out=PAGES):
    """Write one page per person, leader.html and index.html (the landing page)."""
    template = template or TEMPLATE.read_text(encoding="utf-8")
    everyone = {ROLES["leader_id"]: {"role": "leader", "name": ROLES["leader_name"], "manager": None},
                **people(export["team"])}
    files = {pid: ("leader.html" if p["role"] == "leader" else f"{pid}.html") for pid, p in everyone.items()}
    kind = {"leader": "Leader", "manager": "Manager", "csm": "CSM", "member": "Pool member"}
    out.mkdir(parents=True, exist_ok=True)
    written = {}
    for pid, person in everyone.items():
        page, meta = slice_export(export, pid, person)
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
    sys.stdout.reconfigure(encoding="utf-8")
    src = OUTPUTS / "views_data.json"
    if not src.exists():
        sys.exit(f"{src} is missing: run python export_views.py first")
    for pid, (name, n) in build(json.loads(src.read_text(encoding="utf-8"))).items():
        print(f"  {name:<18} {n:>4} accounts")
    print("Saved data/pages/ (index.html = the landing page)")
