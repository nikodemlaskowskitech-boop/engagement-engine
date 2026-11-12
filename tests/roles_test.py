"""Acceptance tests for the per-person cockpit pages (ROLES.md).

Written as a black box from ROLES.md, settings.toml ([roles], [tiers]) and the
inputs (data/cs_team.csv, data/crm_accounts.csv) only, without reading the
implementation (export_views.py, views_template.html, cockpit_sync.py source).

What is tested:
  1. Pages exist: one per manager, CSM and pool member, leader.html, and
     index.html identical to the landing page; no page for a pool.
  2. page-meta on each page: person, role, tabs (= settings, in order),
     outcomes, accounts (= exactly the person's scope), edit (= exactly the
     role's actions, each on the accounts it is allowed on; nothing for the
     leader).
  3. Raw HTML: no acc_ id and no (distinctive) account name from outside the
     person's scope, anywhere in the file, hidden or not. Below manager level:
     no Leadership tab, no Self-service account.
  4. The page switcher lists the other pages.
  5. Cross-region cover accounts sit on their owner's team's pages.
  6. cockpit_sync.build refuses decisions the role may not make, and still
     lets the allowed ones through.
  Plus views-data: on a team-total page no renewal rows and small groups
  (n under [roles] min_group) with no sums; on by-owner pages renewal rows only
  for the page's own accounts. Accounts with no owner (Self-service, or no CSM
  yet) only on the managers' pages of their region, and never editable.

Not tested here: "Once published: a decision on an account outside the page's
scope, read from the shared database" (ROLES.md, must never contain). That
data arrives at run time from the published page's database, not in the
static file, so a raw-HTML test can't see it; it needs a browser test against
a stand-in database.

Run: python tests/roles_test.py   (after python export_views.py)
Exit 1 on any FAIL.
"""
import csv
import json
import re
import sys
import tomllib
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGES = ROOT / "data" / "pages"
FAILS = []


def check(name, ok, detail=""):
    if ok:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  ->  {detail}")
        FAILS.append(name)
    return ok


def section(title):
    print(f"\n{title}")


def short(items, n=8):
    items = sorted(items)
    more = f" (+{len(items) - n} more)" if len(items) > n else ""
    return ", ".join(items[:n]) + more


# ---------------------------------------------------------------- inputs

SETTINGS = tomllib.loads((ROOT / "settings.toml").read_text(encoding="utf-8"))
ROLES = SETTINGS["roles"]
TIER_MAP = SETTINGS.get("tiers", {})
ENGINE_TIERS = set(TIER_MAP.values())


def engine_tier(crm_tier):
    # [tiers] maps the CRM's names onto the engine's; a CRM value that is
    # already an engine name (the demo data) stays as it is.
    if crm_tier in ENGINE_TIERS:
        return crm_tier
    return TIER_MAP.get(crm_tier, crm_tier)


with open(ROOT / "data" / "cs_team.csv", newline="", encoding="utf-8") as f:
    TEAM_ROWS = list(csv.DictReader(f))
with open(ROOT / "data" / "crm_accounts.csv", newline="", encoding="utf-8") as f:
    # today's book only: a churned account left at its renewal (ROLES.md, The rule)
    CRM = [r for r in csv.DictReader(f) if (r.get("status") or "active") != "churned"]

PEOPLE = {r["owner_id"]: r for r in TEAM_ROWS}
POOLS = {o for o, r in PEOPLE.items() if r["owner_type"] == "pool"}
CSMS = {o for o, r in PEOPLE.items() if r["owner_type"] == "csm"}
MEMBERS = {o for o, r in PEOPLE.items() if r["owner_type"] == "member"}
MANAGERS = {r["manager"] for r in TEAM_ROWS if r["manager"]}
LEADER = ROLES.get("leader_id", "leader")
LANDING = ROLES.get("landing", "leader")


def role_of(pid):
    if pid == LEADER:
        return "leader"
    if pid in MANAGERS:
        return "manager"
    if pid in CSMS:
        return "csm"
    if pid in MEMBERS:
        return "member"
    return None


def manager_of(pid):
    return pid if pid in MANAGERS else PEOPLE.get(pid, {}).get("manager", "")


ALL_IDS = {a["account_id"] for a in CRM}
NAME = {a["account_id"]: a["account_name"] for a in CRM}
REGION = {a["account_id"]: a["region"] for a in CRM}
TIER = {a["account_id"]: engine_tier(a["tier"]) for a in CRM}
OWNERS = {
    a["account_id"]: {a[c] for c in ("csm_owner", "cs_pool", "pool_owner") if a.get(c)}
    for a in CRM
}
SELF_SERVICE = {i for i, t in TIER.items() if t == "Self-service"}
# Accounts with no owner at all, of any tier: Self-service, or an Enterprise
# account with no CSM yet (acc_005, acc_008 in the demo). ROLES.md treats them
# alike: managers of the customer's region see them, CSMs and members don't,
# nobody edits them.
UNOWNED = {i for i, own in OWNERS.items() if not own}
MIN_GROUP = ROLES.get("min_group", 5)

# A team = everyone under one manager (pools included: they carry the manager).
TEAM_OF_MANAGER = {m: {o for o, r in PEOPLE.items() if r["manager"] == m} for m in MANAGERS}
# A team's regions = the regions its people sit in (used for Self-service).
TEAM_REGIONS = {m: {PEOPLE[o]["region"] for o in TEAM_OF_MANAGER[m]} for m in MANAGERS}
# A team's accounts = those owned by anyone in it (not the customer's region).
TEAM_ACCOUNTS = {
    m: {i for i, own in OWNERS.items() if own & TEAM_OF_MANAGER[m]} for m in MANAGERS
}


def own_accounts(pid):
    # CSM: csm_owner; pool member: pool_owner. Pools are not people.
    return {i for i, own in OWNERS.items() if pid in own}


def expected_scope(pid):
    role = role_of(pid)
    cfg = ROLES[role]
    if cfg.get("scope") == "book":
        ids = set(ALL_IDS)
        if not cfg.get("self_service", False):
            ids -= UNOWNED
        return ids
    m = manager_of(pid)
    ids = set(TEAM_ACCOUNTS.get(m, set()))
    if cfg.get("self_service", False):
        regions = TEAM_REGIONS.get(m, set())
        ids |= {i for i in UNOWNED if REGION[i] in regions}
    return ids


def allowed_edit(pid, how):
    if how == "own":
        return own_accounts(pid)
    if how == "team":
        # "Team's accounts": owned by someone in the team. Accounts with no
        # owner (Self-service, Enterprise with no CSM yet) are on a manager's
        # page but nobody may edit them ("until it has an owner").
        return set(TEAM_ACCOUNTS.get(manager_of(pid), set()))
    if how == "book":
        return set(ALL_IDS)
    return set()


EXPECTED_PAGES = {f"{p}.html": p for p in sorted(MANAGERS | CSMS | MEMBERS)}
EXPECTED_PAGES["leader.html"] = LEADER

# Actions whose edit list must be every allowed account: a forecast and a
# next step can be set on any account in scope. For the others (tasks,
# hand-backs, handover briefs) the page may list only the accounts where such
# a thing exists, so the list must be a subset of the allowed accounts.
EXACT_ACTIONS = {"forecast", "next_step"}

# ------------------------------------------------- distinctive account names
# An account name counts as "on the page" only when it is reasonably
# distinctive, so ordinary words don't trip the test:
#   - at least two words and at least 8 characters (all demo names are
#     "Word Word", e.g. "Sable Apps"; a one-word name such as "Orbit" could be
#     ordinary text and is skipped),
#   - matched case-sensitively as whole words (not inside a longer word),
#   - not equal to, or contained in, the name of an account that IS in scope
#     (then its appearance can be legitimate),
#   - not equal to a person's, pool's or region's name.
OTHER_LABELS = {r["owner_name"] for r in TEAM_ROWS} | {r["region"] for r in TEAM_ROWS}


def distinctive(name):
    return len(name.split()) >= 2 and len(name) >= 8 and name not in OTHER_LABELS


def names_found(html, ids, in_scope_names):
    hits = []
    for i in ids:
        name = NAME.get(i, "")
        if not name or not distinctive(name):
            continue
        if any(name in n for n in in_scope_names):
            continue
        if re.search(r"(?<![\w])" + re.escape(name) + r"(?![\w])", html):
            hits.append(f"{name} ({i})")
    return hits


# ---------------------------------------------------------------- HTML parse


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta_blocks = []
        self._in_meta = False
        self._buf = []
        self.switch_count = 0
        self._in_switch = False
        self.switch_options = []
        self.views_blocks = []
        self._in_views = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "script" and a.get("id") == "page-meta":
            self._in_meta = True
            self._buf = []
            self.meta_type = a.get("type")
        if tag == "script" and a.get("id") == "views-data":
            self._in_views = True
            self._buf = []
            self.views_type = a.get("type")
        if tag == "select" and a.get("id") == "page-switch":
            self.switch_count += 1
            self._in_switch = True
        if tag == "option" and self._in_switch:
            self.switch_options.append(a.get("value") or a.get("data-href") or "")

    def handle_endtag(self, tag):
        if tag == "script" and self._in_meta:
            self._in_meta = False
            self.meta_blocks.append(("".join(self._buf), self.meta_type))
        elif tag == "script" and self._in_views:
            self._in_views = False
            self.views_blocks.append(("".join(self._buf), self.views_type))
        if tag == "select" and self._in_switch:
            self._in_switch = False

    def handle_data(self, data):
        if self._in_meta or self._in_views:
            self._buf.append(data)


def read_page(fname):
    path = PAGES / fname
    if not path.exists():
        return None, None, f"{path.relative_to(ROOT)} missing"
    try:
        html = path.read_text(encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        return None, None, f"unreadable: {e}"
    p = PageParser()
    try:
        p.feed(html)
    except Exception as e:  # noqa: BLE001
        return html, None, f"HTML parse error: {e}"
    return html, p, ""


ACC_RE = re.compile(r"acc_\d+")


# ===================================================================== tests

def test_pages_exist():
    section("1. Pages exist")
    if not check("data/pages/ folder exists", PAGES.is_dir(),
                 "run python export_views.py first (folder missing)"):
        for fname in EXPECTED_PAGES:
            check(f"page {fname}", False, "folder missing")
        check("index.html", False, "folder missing")
        return
    for fname in EXPECTED_PAGES:
        check(f"page {fname}", (PAGES / fname).is_file(), "missing")
    pool_pages = [p for p in POOLS if (PAGES / f"{p}.html").exists()]
    check("no page for a pool", not pool_pages, f"pool pages: {short(pool_pages)}")
    found = {p.name for p in PAGES.glob("*.html")}
    extra = found - set(EXPECTED_PAGES) - {"index.html"}
    check("no unexpected pages", not extra, short(extra))
    idx, land = PAGES / "index.html", PAGES / f"{LANDING}.html"
    if check("index.html exists", idx.is_file(), "missing") and land.is_file():
        check(f"index.html is a copy of the landing page ({LANDING}.html)",
              idx.read_bytes() == land.read_bytes(), "contents differ")
    elif idx.is_file():
        check("index.html is a copy of the landing page", False, f"{LANDING}.html missing")


def test_page(fname, pid):
    role = role_of(pid)
    cfg = ROLES[role]
    label = f"{fname} [{role}]"
    section(f"2-5. {label}")
    html, parser, err = read_page(fname)
    if html is None or parser is None:
        check(f"{label}: readable", False, err)
        return

    # ---- page-meta
    meta = None
    if check(f"{label}: exactly one page-meta", len(parser.meta_blocks) == 1,
             f"found {len(parser.meta_blocks)}"):
        raw, typ = parser.meta_blocks[0]
        check(f"{label}: page-meta type application/json", typ == "application/json", f"type={typ!r}")
        try:
            meta = json.loads(raw)
        except Exception as e:  # noqa: BLE001
            check(f"{label}: page-meta is valid JSON", False, str(e)[:120])
    if not isinstance(meta, dict):
        if meta is not None:
            check(f"{label}: page-meta is an object", False, type(meta).__name__)
        meta = None

    scope = expected_scope(pid)
    if meta is not None:
        check(f"{label}: person", meta.get("person") == pid, f"{meta.get('person')!r}")
        check(f"{label}: role", meta.get("role") == role, f"{meta.get('role')!r}")
        check(f"{label}: tabs in settings order (opens on {cfg['tabs'][0]})",
              meta.get("tabs") == cfg["tabs"], f"{meta.get('tabs')} vs {cfg['tabs']}")
        check(f"{label}: outcomes", meta.get("outcomes") == cfg["outcomes"],
              f"{meta.get('outcomes')!r} vs {cfg['outcomes']!r}")
        accs = meta.get("accounts")
        if check(f"{label}: accounts is a list", isinstance(accs, list), type(accs).__name__):
            accs = set(accs)
            missing, extra = scope - accs, accs - scope
            if role == "leader":
                # The whole book; ids outside the CRM (e.g. a handoff gap,
                # events with no CRM row) are allowed on the leader's page.
                check(f"{label}: accounts = whole book", not missing,
                      f"missing {len(missing)}: {short(missing)}")
            else:
                check(f"{label}: accounts = exactly the scope ({len(scope)})",
                      not missing and not extra,
                      f"missing {len(missing)}: {short(missing)}; extra {len(extra)}: {short(extra)}")
        edit = meta.get("edit")
        role_edit = cfg.get("edit", {})
        if check(f"{label}: edit is an object", isinstance(edit, dict), type(edit).__name__):
            if role == "leader":
                nonempty = {k: v for k, v in edit.items() if v}
                check(f"{label}: leader can edit nothing", not nonempty, f"{list(nonempty)}")
            else:
                check(f"{label}: edit actions = role's ({short(role_edit)})",
                      set(edit) == set(role_edit), f"got {sorted(edit)}")
                for action, how in role_edit.items():
                    got = edit.get(action)
                    if not isinstance(got, list):
                        check(f"{label}: edit.{action} list", False, f"{type(got).__name__}")
                        continue
                    got, allowed = set(got), allowed_edit(pid, how)
                    if action in EXACT_ACTIONS:
                        check(f"{label}: edit.{action} = {how} accounts ({len(allowed)})",
                              got == allowed,
                              f"not allowed: {short(got - allowed)}; missing: {short(allowed - got)}")
                    else:
                        check(f"{label}: edit.{action} only on {how} accounts",
                              got <= allowed, f"not allowed: {short(got - allowed)}")

    # ---- raw HTML: nothing outside the scope
    if role == "leader":
        out_ids = set()
    else:
        in_html = set(ACC_RE.findall(html))
        out_ids = in_html - scope
    known_out = {i for i in out_ids if i in ALL_IDS}
    unknown_out = out_ids - ALL_IDS
    check(f"{label}: no account id from outside the scope in the raw HTML", not known_out,
          f"{len(known_out)} found: {short(known_out)}")
    # Ids not in the CRM (no owner, so no team) belong on no team's page.
    check(f"{label}: no account id missing from the CRM", not unknown_out,
          f"{len(unknown_out)} found: {short(unknown_out)}")
    if role != "leader":
        in_scope_names = {NAME[i] for i in scope if i in NAME}
        hits = names_found(html, ALL_IDS - scope, in_scope_names)
        check(f"{label}: no account name from outside the scope", not hits,
              f"{len(hits)} found: {short(hits)}")

    # ---- below manager level
    if role in ("csm", "member"):
        if meta is not None:
            check(f"{label}: no Leadership tab", "lead" not in (meta.get("tabs") or []),
                  f"tabs={meta.get('tabs')}")
            check(f"{label}: outcomes are the team total", meta.get("outcomes") == "team_total",
                  f"{meta.get('outcomes')!r}")
        ss = set(ACC_RE.findall(html)) & SELF_SERVICE
        check(f"{label}: no Self-service account", not ss, f"{len(ss)}: {short(ss)}")
        nocsm = set(ACC_RE.findall(html)) & (UNOWNED - SELF_SERVICE)
        check(f"{label}: no account without an owner (e.g. no CSM yet)", not nocsm,
              f"{len(nocsm)}: {short(nocsm)}")

    # ---- views-data: renewals and team totals
    test_views_data(label, parser, meta, cfg, scope)

    # ---- page switcher
    if check(f"{label}: one page switcher", parser.switch_count == 1,
             f"found {parser.switch_count}"):
        linked = {Path(v.split("#")[0].split("?")[0]).name for v in parser.switch_options if v}
        others = set(EXPECTED_PAGES) - {fname}
        missing = others - linked
        check(f"{label}: switcher lists every other page", not missing,
              f"missing {short(missing)}")
        bogus = linked - set(EXPECTED_PAGES) - {"index.html"}
        check(f"{label}: switcher links only real pages", not bogus, short(bogus))


SMALL_SEEN = [0]  # how many small groups were checked (so a pass isn't empty)
SUM_KEYS = {"value": ("arr", "at_risk", "lost"), "calls": ("churned", "kept")}


def groups_of(block):
    # outcomes_total.value / .calls: a list of groups, or an object of named
    # groups (ROLES.md doesn't say which); a single group object is one group.
    if isinstance(block, list):
        return [g for g in block if isinstance(g, dict)]
    if isinstance(block, dict):
        if "n" in block:
            return [block]
        return [g for g in block.values() if isinstance(g, dict)]
    return None


def test_views_data(label, parser, meta, cfg, scope):
    if not check(f"{label}: exactly one views-data", len(parser.views_blocks) == 1,
                 f"found {len(parser.views_blocks)}"):
        return
    raw, typ = parser.views_blocks[0]
    check(f"{label}: views-data type application/json", typ == "application/json", f"type={typ!r}")
    try:
        data = json.loads(raw)
    except Exception as e:  # noqa: BLE001
        check(f"{label}: views-data is valid JSON", False, str(e)[:120])
        return
    if not check(f"{label}: views-data is an object", isinstance(data, dict), type(data).__name__):
        return
    renewals = data.get("renewals")
    if cfg["outcomes"] == "team_total":
        check(f"{label}: renewals list is empty (no row per account)",
              renewals == [], f"{type(renewals).__name__} with "
              f"{len(renewals) if isinstance(renewals, (list, dict)) else '?'} entries")
        tot = data.get("outcomes_total")
        if not check(f"{label}: outcomes_total present", isinstance(tot, dict),
                     f"{type(tot).__name__}"):
            return
        for part, keys in SUM_KEYS.items():
            groups = groups_of(tot.get(part))
            if not check(f"{label}: outcomes_total.{part} present", groups is not None,
                         f"{type(tot.get(part)).__name__}"):
                continue
            bad = []
            for g in groups:
                n = g.get("n")
                if not isinstance(n, (int, float)):
                    bad.append(f"group without a number n: {json.dumps(g)[:60]}")
                    continue
                if n < MIN_GROUP:
                    if g.get("small") is not True:
                        bad.append(f"n={n} without small: true")
                    sums = [k for k in keys if g.get(k) is not None]
                    if sums:
                        bad.append(f"n={n} still has {', '.join(sums)}")
            SMALL_SEEN[0] += sum(1 for g in groups
                                 if isinstance(g.get("n"), (int, float)) and g["n"] < MIN_GROUP)
            check(f"{label}: outcomes_total.{part}: groups under {MIN_GROUP} show no sums",
                  not bad, "; ".join(bad[:4]))
    else:
        # by_owner pages keep renewal rows, but only for their own scope.
        if check(f"{label}: renewals is a list", isinstance(renewals, list),
                 f"{type(renewals).__name__}"):
            outside = set()
            for row in renewals:
                outside |= set(ACC_RE.findall(json.dumps(row))) - scope
            if cfg.get("scope") == "book":
                outside -= outside - ALL_IDS  # the leader may hold non-CRM ids
            check(f"{label}: renewal rows only for accounts in scope", not outside,
                  f"{len(outside)}: {short(outside)}")


def test_cover():
    section("5. Cross-region cover")
    covered = [a for a in CRM if a["csm_owner"] in PEOPLE
               and PEOPLE[a["csm_owner"]]["region"] != a["region"]]
    check("demo data has cover accounts", bool(covered), "none found")
    region_manager = {}
    for m, regions in TEAM_REGIONS.items():
        for r in regions:
            region_manager.setdefault(r, set()).add(m)
    for a in covered:
        i, owner = a["account_id"], a["csm_owner"]
        m = manager_of(owner)
        for page in (f"{owner}.html", f"{m}.html"):
            html, _, err = read_page(page)
            check(f"{i} ({a['region']}, covered by {owner}) on {page}",
                  html is not None and i in set(ACC_RE.findall(html)), err or "not on the page")
        # A manager of the customer's region who isn't the owner's manager
        # must not have it. (In the demo data the cover never crosses teams,
        # so this is only exercised by the cockpit_sync check below.)
        for other in region_manager.get(a["region"], set()) - {m}:
            html, _, err = read_page(f"{other}.html")
            check(f"{i} not on {other}.html (customer region's team only)",
                  html is not None and i not in set(ACC_RE.findall(html)), err or "on the page")


# ------------------------------------------------------------ cockpit_sync

AS_OF = "2026-10-30"
RUN_DATE = "2026-10-31"
AT = "2026-10-30T13:49:35.179Z"

# A small made-up book: two teams. acc_901 is Mia's, acc_902 her teammate
# Lukas's, acc_903 a DACH pool account owned by member Jonas, acc_904 the same
# pool owned by Lena, acc_905 a Nordics account covered by Tom (other team),
# acc_906 a UK&I account of Anna's.
T_ACCOUNTS = {
    "acc_901": {"arr": 200000.0, "owners": {"csm_mia"}},
    "acc_902": {"arr": 150000.0, "owners": {"csm_lukas"}},
    "acc_903": {"arr": 40000.0, "owners": {"pool_dach", "pm_jonas"}},
    "acc_904": {"arr": 40000.0, "owners": {"pool_dach", "pm_lena"}},
    "acc_905": {"arr": 90000.0, "owners": {"csm_tom"}},
    "acc_906": {"arr": 120000.0, "owners": {"csm_anna"}},
}
T_TEAM = {
    "csm_mia": "mgr_sophie", "csm_lukas": "mgr_sophie", "pool_dach": "mgr_sophie",
    "pm_jonas": "mgr_sophie", "pm_lena": "mgr_sophie",
    "csm_tom": "mgr_james", "csm_anna": "mgr_james", "pool_uki": "mgr_james",
    "pm_priya": "mgr_james",
    "mgr_sophie": "", "mgr_james": "",
}


def forecast(acc, by, role, amount=10000):
    return {"kind": "forecast", "account_id": acc, "amount": amount, "as_of": AS_OF,
            "by": by, "call": "downsize", "data_at_risk": 50000, "decided_at": AT,
            "reason": "test reason", "role": role}


def next_step(acc, by, role):
    return {"kind": "next_step", "account_id": acc, "as_of": AS_OF, "by": by,
            "decided_at": AT, "due": "2026-11-10", "note": "test note", "role": role,
            "type": "call"}


def assign(acc, frm, to, manager):
    return {"kind": "assign", "account_id": acc, "flag": "churn", "status": "Act",
            "from_owner": frm, "to_owner": to, "manager": manager, "as_of": AS_OF,
            "decided_at": AT}


def test_cockpit_sync():
    section("6. cockpit_sync refuses what the role may not do")
    sys.path.insert(0, str(ROOT))
    try:
        import cockpit_sync  # noqa: PLC0415
        build = cockpit_sync.build
    except Exception as e:  # noqa: BLE001
        check("import cockpit_sync.build", False, f"{type(e).__name__}: {e}")
        return

    def run(decision):
        try:
            out = build([decision], AS_OF, RUN_DATE, T_ACCOUNTS, T_TEAM)
            events, skipped = out[0], out[1]
            return list(events or []), list(skipped or []), None
        except Exception as e:  # noqa: BLE001
            return None, None, f"crashed: {type(e).__name__}: {e}"

    def refused(name, d):
        ev, sk, err = run(d)
        check(name, err is None and not ev and len(sk) == 1,
              err or f"events={len(ev)}, skipped={len(sk)}")

    def allowed(name, d):
        ev, sk, err = run(d)
        check(name, err is None and ev and not sk,
              err or f"events={len(ev)}, skipped={sk}")

    print("  refused:")
    refused("CSM forecasts a teammate's account", forecast("acc_902", "csm_mia", "owner"))
    refused("CSM sets a next step on a teammate's account", next_step("acc_902", "csm_mia", "owner"))
    refused("CSM claims the manager role on a teammate's account",
            forecast("acc_902", "csm_mia", "manager"))
    refused("member forecasts a pool colleague's account", forecast("acc_904", "pm_jonas", "owner"))
    refused("member reassigns a task", assign("acc_903", "pool_dach", "pm_lena", "pm_jonas"))
    refused("CSM reassigns a task", assign("acc_901", "csm_mia", "csm_lukas", "csm_mia"))
    for r in ("leader", "manager", "owner"):
        refused(f"leader sets a forecast (role {r})", forecast("acc_901", LEADER, r))
        refused(f"leader sets a next step (role {r})", next_step("acc_901", LEADER, r))
    refused("leader reassigns a task", assign("acc_903", "pool_dach", "pm_lena", LEADER))
    refused("manager forecasts another team's account", forecast("acc_906", "mgr_sophie", "manager"))
    refused("manager sets a next step on another team's account",
            next_step("acc_906", "mgr_sophie", "manager"))
    refused("manager reassigns another team's task",
            assign("acc_905", "csm_tom", "csm_anna", "mgr_sophie"))
    refused("customer-region manager acts on a cover account owned by another team",
            forecast("acc_905", "mgr_sophie", "manager"))

    print("  allowed:")
    allowed("CSM forecasts own account", forecast("acc_901", "csm_mia", "owner"))
    allowed("CSM sets a next step on own account", next_step("acc_901", "csm_mia", "owner"))
    allowed("member forecasts own account", forecast("acc_903", "pm_jonas", "owner"))
    allowed("member sets a next step on own account", next_step("acc_903", "pm_jonas", "owner"))
    allowed("manager forecasts a team account", forecast("acc_902", "mgr_sophie", "manager"))
    allowed("manager sets a next step on a team account", next_step("acc_903", "mgr_sophie", "manager"))
    allowed("manager reassigns a team task", assign("acc_903", "pool_dach", "pm_lena", "mgr_sophie"))
    allowed("owner's manager acts on a cover account", forecast("acc_905", "mgr_james", "manager"))


def main():
    print("Roles: per-person pages (ROLES.md)")
    test_pages_exist()
    if PAGES.is_dir():
        for fname, pid in EXPECTED_PAGES.items():
            test_page(fname, pid)
        test_cover()
    else:
        section("2-5. Page contents")
        check("page contents", False, "data/pages/ missing: nothing to check")
    test_cockpit_sync()
    print(f"\n  note: {SMALL_SEEN[0]} groups under min_group checked on team-total pages")
    print()
    if FAILS:
        print(f"FAILED ({len(FAILS)} checks)")
        sys.exit(1)
    print("OK")


if __name__ == "__main__":
    main()
