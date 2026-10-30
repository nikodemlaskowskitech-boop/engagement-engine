"""
Cairnpath fake data generator.

Writes six things into ./data:
  - crm_accounts.csv          the fake CRM table (one row per account)
  - cs_team.csv               who covers what: named CSMs and regional pools
  - events/YYYY-MM-DD.jsonl   one file per day, one JSON event per line
                              (like a daily dump landing in S3)
  - answer_key.csv            each account's hidden personality, for testing
                              only (check_flags.py); the flags never read it
  - engagement/YYYY-MM-DD.jsonl  what CS did and how customers responded
                              (emails, workshops, meetings), same schema
  - onboarding_holdout.csv    the control group: new accounts that get no
                              onboarding emails (actions.py checks it)

The book of business is ~500 European accounts across three tiers and four
sub-regions. Each account gets a "personality" that shapes its behaviour
over time, so the flags we build later have something real to find.

Run:  python generate_events.py
"""

import csv
import json
import random
import uuid
from datetime import date, datetime, time, timedelta
from pathlib import Path

# --- Settings -------------------------------------------------------------

SEED = 42                      # same seed = same data every run
END_DATE = date(2026, 10, 18)  # last day of data ("today" for the flags = the day after)
DAYS = 90                      # 90, not 30: the churn rules look at 2+ months of trend
START_DATE = END_DATE - timedelta(days=DAYS - 1)
OUT_DIR = Path("data")

REGIONS = ["UK&I", "DACH", "France & Benelux", "Nordics"]

# How many accounts per tier, and how they spread across regions.
# Enterprise is fixed so every CSM carries 8 accounts.
TIER_COUNTS = {"Enterprise": 48, "Scale": 150, "Self-service": 300}
ENTERPRISE_PER_REGION = {"UK&I": 16, "DACH": 16, "France & Benelux": 8, "Nordics": 8}
REGION_WEIGHTS = {"UK&I": 0.35, "DACH": 0.30, "France & Benelux": 0.20, "Nordics": 0.15}

# Contracted events per year, by tier
CONTRACT_SIZE = {
    "Enterprise": 300_000_000,
    "Scale": 60_000_000,
    "Self-service": 12_000_000,
}

# Typical starting user pool, by tier (min, max)
USERS_BY_TIER = {"Enterprise": (10, 25), "Scale": (4, 10), "Self-service": (2, 5)}

# How much of their contract accounts really use, by tier. Each account gets a
# random factor in this range, applied to its personality's utilisation.
# Enterprise is often over-provisioned at signing; Self-service is more spread
# out. The low ends keep a healthy or ramped-up account above 40%: low and flat
# is the shelfware personality's job, not an accident of the factor.
UTIL_FACTOR_BY_TIER = {"Enterprise": (0.70, 1.05), "Scale": (0.80, 1.15), "Self-service": (0.65, 1.25)}

# The working week: how likely users are to show up, Monday to Sunday,
# relative to the personality's activity. B2B analysts work weekdays (a bit
# less on Fridays) and barely at weekends; the weekday boost keeps the weekly
# average close to before. Event volume has no weekly rhythm on purpose: it's
# the customer's own end users sending events, and they don't stop at weekends.
WEEKDAY_ACTIVITY = (1.2, 1.2, 1.2, 1.2, 1.1, 0.3, 0.25)

# --- CS engagement settings ------------------------------------------------------

# About 1 in 5 new Self-service and Scale accounts get no "getting started" email
# (a control group), so "welcomed vs. not" can be compared honestly later.
CONTROL_SHARE = 0.2
# The planted effect: welcomed accounts install the SDK 2 days after signing,
# the control group after 5. It's fake data, so the comparison shows the
# method works; it doesn't prove that emails work.
SDK_DELAY_DAYS = {"welcomed": 2, "control": 5}

# Local time vs. UTC across the window (all summer time: clocks change on 25 Oct)
UTC_OFFSET_HOURS = {"UK&I": 1, "DACH": 2, "France & Benelux": 2, "Nordics": 2}

# Each owner's style: days to act on a problem (min, max), days between
# check-ins, the chance they act on a declining or growing account at all,
# and how often they follow up afterwards (None: one call, then nothing).
# Different styles are what make "time to action by CSM" worth a chart.
OWNER_STYLE = {
    "csm_anna":   dict(act_days=(1, 3),  check_in_days=21, acts=0.95, follow_up_days=14),
    "csm_tom":    dict(act_days=(6, 12), check_in_days=45, acts=0.50, follow_up_days=None),
    "csm_lukas":  dict(act_days=(2, 5),  check_in_days=28, acts=0.85, follow_up_days=21),
    "csm_mia":    dict(act_days=(1, 2),  check_in_days=21, acts=0.95, follow_up_days=14),
    "csm_claire": dict(act_days=(3, 7),  check_in_days=30, acts=0.80, follow_up_days=21),
    "csm_erik":   dict(act_days=(5, 14), check_in_days=40, acts=0.55, follow_up_days=None),
    "pool":       dict(act_days=(3, 10), check_in_days=None, acts=0.60, follow_up_days=28),  # every Scale pool
}

# How customers respond, by personality
OPEN_RATE = {"new_customer": 0.80, "stalled": 0.45, "never_onboarded": 0.10}
ONBOARDING_WORKSHOP_RATE = {"new_customer": 0.65, "stalled": 0.15, "never_onboarded": 0.03}
ADVANCED_WORKSHOP_RATE = {"healthy": 0.08, "power_user": 0.20, "declining": 0.10, "shelfware": 0.03,
                          "slipping": 0.06}
TRAILING_LAG_DAYS = 15     # a 30-day average crosses a line about 15 days after daily usage does

# Personality weights that differ by tier (otherwise each personality's own weight applies).
# An Enterprise deal is never missing from the CRM, and a silent Enterprise
# customer is rare (it gets escalated fast).
TIER_WEIGHT_OVERRIDES = {"Enterprise": {"handoff_gap": 0.0, "never_onboarded": 0.01}}

# Enterprise customers covered from another region's team (region, the CSM who covers it):
# a smaller book in London picks up Nordic and Benelux accounts
CROSS_REGION_COVER = [("Nordics", "csm_anna"), ("Nordics", "csm_tom"), ("France & Benelux", "csm_lukas")]

# --- CS team ----------------------------------------------------------------
# Enterprise accounts have a named CSM; Scale accounts go to a regional pool;
# Self-service has no human coverage.

CS_TEAM = [
    # owner_id,        name,              type,  region,              manager
    ("csm_anna",       "Anna Whitfield",  "csm",  "UK&I",             "mgr_james"),
    ("csm_tom",        "Tom Brennan",     "csm",  "UK&I",             "mgr_james"),
    ("csm_lukas",      "Lukas Brandt",    "csm",  "DACH",             "mgr_sophie"),
    ("csm_mia",        "Mia Hofer",       "csm",  "DACH",             "mgr_sophie"),
    ("csm_claire",     "Claire Dubois",   "csm",  "France & Benelux", "mgr_sophie"),
    ("csm_erik",       "Erik Lindqvist",  "csm",  "Nordics",          "mgr_james"),
    ("pool_uki",       "UK&I Scale team", "pool", "UK&I",             "mgr_james"),
    ("pool_dach",      "DACH Scale team", "pool", "DACH",             "mgr_sophie"),
    ("pool_fb",        "F&B Scale team",  "pool", "France & Benelux", "mgr_sophie"),
    ("pool_nordics",   "Nordics Scale team", "pool", "Nordics",       "mgr_james"),
]

# --- Personalities --------------------------------------------------------
# Every value with _start/_end moves in a straight line across the 90 days.
#   activity      = chance that each user is active on a given day
#   util          = share of the contracted volume the customer is sending
#   user_growth   = user pool at the end vs. the start (1.5 = +50%)
#   errors        = chance of ingestion errors on a given day
#   features      = which product events their users produce
#   lifecycle     = "existing" (live before the data starts) or "new" (signed recently)
#   ramp_days     = new accounts only: util goes from its start to its end value over
#                   this many days after go-live (instead of across the 90 days)
#   weight        = share of accounts with this personality

PERSONALITIES = {
    "healthy": dict(
        weight=0.41, lifecycle="existing",
        activity=(0.6, 0.6), util=(0.65, 0.70), user_growth=1.1, errors=(0.02, 0.02),
        features=["chart_created", "dashboard_shared", "cohort_created", "data_export"],
    ),
    "declining": dict(
        weight=0.13, lifecycle="existing",
        activity=(0.6, 0.05), util=(0.55, 0.22), user_growth=1.0, errors=(0.02, 0.35),
        features=["chart_created", "dashboard_shared"],
    ),
    "slipping": dict(          # volume sliding under the line, but the same people still use it:
        weight=0.05, lifecycle="existing",       # one churn signal only, so churn Watch
        activity=(0.6, 0.6), util=(0.58, 0.31), user_growth=1.0, errors=(0.02, 0.02),
        features=["chart_created", "dashboard_shared", "cohort_created"],
    ),
    "power_user": dict(
        weight=0.10, lifecycle="existing",
        activity=(0.75, 0.9), util=(0.80, 0.97), user_growth=1.5, errors=(0.01, 0.01),
        features=["chart_created", "dashboard_shared", "cohort_created",
                  "experiment_launched", "data_export"],
    ),
    "new_customer": dict(      # signs during the window, installs the SDK, the team gets going
        weight=0.14, lifecycle="new", ramp_days=30,   # volume ramps up over the first month
        activity=(0.5, 0.5), util=(0.20, 0.70), user_growth=1.4, errors=(0.03, 0.03),
        features=["chart_created", "dashboard_shared"],
    ),
    "shelfware": dict(         # live for months, people use it, but the rollout never came:
        weight=0.05, lifecycle="existing",       # flat volume far below what they bought
        activity=(0.45, 0.45), util=(0.22, 0.24), user_growth=1.0, errors=(0.02, 0.02),
        features=["chart_created", "dashboard_shared"],
    ),
    "stalled": dict(           # signs recently, installs the SDK, but only one person uses it
        weight=0.04, lifecycle="new", single_user=True,
        activity=(0.4, 0.3), util=(0.08, 0.05), user_growth=1.0, errors=(0.05, 0.05),
        features=["chart_created"],
    ),
    "never_onboarded": dict(   # contract signed, no events at all
        weight=0.04, lifecycle="new", silent=True,
    ),
    "handoff_gap": dict(       # active in the product, but missing from the CRM
        weight=0.04, lifecycle="existing", in_crm=False,
        activity=(0.5, 0.55), util=(0.40, 0.45), user_growth=1.1, errors=(0.02, 0.02),
        features=["chart_created", "dashboard_shared", "cohort_created"],
    ),
}

# --- Fictional company names --------------------------------------------------

NAME_FIRST = ["Northgate", "Bluefin", "Orbit", "Lumen", "Tidewater", "Kestrel", "Pinecone",
              "Quarry", "Maple", "Saffron", "Driftwood", "Copperline", "Harbour", "Ironwood",
              "Silverleaf", "Foxglove", "Granite", "Juniper", "Larkspur", "Meridian",
              "Nettle", "Oakridge", "Puffin", "Riverstone", "Sable", "Thistle", "Umber",
              "Vantage", "Willow", "Yarrow", "Alder", "Brightwater"]
NAME_SECOND = ["Retail", "Media", "Logistics", "Health", "Apps", "Fintech", "Studio", "Games",
               "Learning", "Foods", "Travel", "Labs", "Energy", "Insurance", "Mobility",
               "Commerce", "Analytics", "Software", "Fashion", "Banking"]


# --- Helpers -----------------------------------------------------------------

def lerp(pair, day_index):
    """Straight line from start to end value across the data window."""
    start, end = pair
    return start + (end - start) * day_index / (DAYS - 1)


def new_id():
    """Reproducible UUID (uuid4() would change on every run)."""
    return str(uuid.UUID(int=random.getrandbits(128), version=4))


def timestamp(day):
    """Random moment during the working day, in UTC."""
    seconds = random.randint(7 * 3600, 20 * 3600)
    moment = datetime.combine(day, time()) + timedelta(seconds=seconds)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def event(name, kind, day, account_id, user_id=None, properties=None):
    """One event in the agreed schema."""
    return {
        "event_id": new_id(),
        "event_name": name,
        "event_type": kind,                 # "product" or "system"
        "timestamp": timestamp(day),
        "account_id": account_id,
        "user_id": user_id,                 # empty for system events
        "properties": properties or {},
        "context": {"sdk_version": "2.1.0", "source": "web"},
    }


def pick_personality(tier):
    names = list(PERSONALITIES)
    overrides = TIER_WEIGHT_OVERRIDES.get(tier, {})
    weights = [overrides.get(n, PERSONALITIES[n]["weight"]) for n in names]
    return random.choices(names, weights=weights)[0]


def contract_start_for(personality):
    """When the contract started, depending on the account's lifecycle."""
    p = PERSONALITIES[personality]
    if p["lifecycle"] == "existing":
        # Signed before the data window, renewal still ahead (offset < 275 days)
        return START_DATE - timedelta(days=random.randint(10, 270))
    # Silent and stalled accounts are recent: older ones would have been
    # rescued or lost by now, so they stay inside the activation window.
    if personality == "never_onboarded":
        return END_DATE - timedelta(days=random.randint(12, 40))
    if personality == "stalled":
        return END_DATE - timedelta(days=random.randint(16, 40))
    # Healthy new customers sign all through the window, like a real sales
    # pipeline, so onboarding shows up every week (not just in the last six).
    return END_DATE - timedelta(days=random.randint(3, DAYS - 2))      # new_customer


# --- Build the book of business ----------------------------------------------

def build_accounts():
    names = [f"{a} {b}" for a in NAME_FIRST for b in NAME_SECOND]
    random.shuffle(names)

    # Regions: Enterprise is fixed per region, the others follow the weights
    regions_by_tier = {"Enterprise": [r for r, n in ENTERPRISE_PER_REGION.items() for _ in range(n)]}
    for tier in ("Scale", "Self-service"):
        regions_by_tier[tier] = random.choices(
            REGIONS, weights=[REGION_WEIGHTS[r] for r in REGIONS], k=TIER_COUNTS[tier])

    csms_by_region = {}
    pools_by_region = {}
    for owner_id, _, kind, region, _ in CS_TEAM:
        (csms_by_region if kind == "csm" else pools_by_region).setdefault(region, []).append(owner_id)

    accounts = []
    n = 0
    for tier, regions in regions_by_tier.items():
        for i, region in enumerate(regions):
            n += 1
            personality = pick_personality(tier)
            p = PERSONALITIES[personality]
            lo, hi = USERS_BY_TIER[tier]
            users_start = 1 if p.get("single_user") else random.randint(lo, hi)
            acc = dict(
                id=f"acc_{n:03d}",
                name=names[n - 1],
                tier=tier,
                region=region,
                personality=personality,
                contract_start=contract_start_for(personality),
                in_crm=p.get("in_crm", True),
                users_start=users_start,
                users_end=max(users_start, round(users_start * p.get("user_growth", 1.0))),
                util_factor=random.uniform(*UTIL_FACTOR_BY_TIER[tier]),
                add_ons="experiments" if (tier != "Self-service" and random.random() < 0.4) else "",
                csm="",
                pool="",
                next_qbr="",
                last_qbr="",
            )
            if tier == "Enterprise":
                # Round-robin the region's CSMs so each carries the same book
                region_csms = csms_by_region[region]
                acc["csm"] = region_csms[i % len(region_csms)]
                acc["next_qbr"] = END_DATE + timedelta(days=random.randint(5, 90))
            elif tier == "Scale":
                acc["pool"] = pools_by_region[region][0]
            accounts.append(acc)

    # A few customers are covered from another region's team: the CSM speaks the
    # language, or the account followed its CSM. Picked by rule, not at random,
    # so nothing else in the data moves. (Team = where the CSM sits; region =
    # where the customer is. The cockpit filters on both.)
    for region, csm in CROSS_REGION_COVER:
        home = {c[0]: c[3] for c in CS_TEAM}             # owner -> the region they sit in
        candidates = [a for a in accounts if a["tier"] == "Enterprise" and a["region"] == region
                      and a["csm"] and home[a["csm"]] == region]  # not one already moved
        if candidates:
            candidates[-1]["csm"] = csm

    # A few new Enterprise accounts were never handed to a CSM (a handoff miss)
    new_enterprise = [a for a in accounts if a["tier"] == "Enterprise"
                      and PERSONALITIES[a["personality"]]["lifecycle"] == "new" and a["in_crm"]]
    for acc in new_enterprise[:2]:
        acc["csm"] = ""
    set_qbr_dates(accounts)
    set_champions(accounts)
    set_welcome_group(accounts)
    return accounts


CHAMPION_SHARE_OF_ACCOUNTS = 0.4   # roughly 4 in 10 accounts have one power user


def set_champions(accounts):
    """Give some accounts a champion: one user (the first) who is active almost
    every day and does several times more than anyone else. Real accounts are
    rarely evenly spread; single-champion risk depends on this.

    Uses its own random generator so it doesn't change any other account fact.
    """
    rng = random.Random(SEED + 2)
    for acc in accounts:
        acc["has_champion"] = rng.random() < CHAMPION_SHARE_OF_ACCOUNTS


def set_welcome_group(accounts):
    """Decide which new Self-service and Scale accounts get the "getting started"
    email and which are the control group. Own random generator, like the others."""
    rng = random.Random(SEED + 3)
    for acc in accounts:
        new = PERSONALITIES[acc["personality"]]["lifecycle"] == "new"
        acc["welcomed"] = (new and acc["in_crm"] and acc["tier"] != "Enterprise"
                           and rng.random() >= CONTROL_SHARE)
        acc["control"] = new and acc["in_crm"] and acc["tier"] != "Enterprise" and not acc["welcomed"]


def nearest_weekday(day):
    """Meetings don't happen at weekends: Saturday moves to Friday, Sunday to Monday."""
    return day + timedelta(days={5: -1, 6: 1}.get(day.weekday(), 0))


def set_qbr_dates(accounts):
    """Quarterly QBRs for Enterprise accounts, with some overdue and not rebooked.

    Uses its own random generator so adding QBR dates doesn't change any of
    the other generated data.
    """
    rng = random.Random(SEED + 1)
    for acc in accounts:
        if acc["tier"] != "Enterprise":
            continue
        start = acc["contract_start"]
        if (END_DATE - start).days < 100:
            # new account: no QBR yet, the first one roughly a quarter after signing
            acc["last_qbr"] = ""
            acc["next_qbr"] = start + timedelta(days=rng.randint(80, 100))
            continue
        last = END_DATE - timedelta(days=rng.randint(15, 160))
        acc["last_qbr"] = max(last, start + timedelta(days=60))
        planned = acc["last_qbr"] + timedelta(days=90 + rng.randint(-7, 7))
        if planned > END_DATE:
            acc["next_qbr"] = planned
        elif rng.random() < 0.5:
            acc["next_qbr"] = ""                    # overdue and nobody rebooked it
        else:
            acc["next_qbr"] = END_DATE + timedelta(days=rng.randint(5, 30))  # slipped, rebooked
    for acc in accounts:                        # QBRs are held on weekdays
        for field in ("last_qbr", "next_qbr"):
            if acc[field]:
                acc[field] = nearest_weekday(acc[field])


# --- Output files -------------------------------------------------------------

def write_team():
    path = OUT_DIR / "cs_team.csv"
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["owner_id", "owner_name", "owner_type", "region", "manager"])
        writer.writerows(CS_TEAM)
    return path


def write_crm(accounts):
    path = OUT_DIR / "crm_accounts.csv"
    columns = ["account_id", "account_name", "tier", "region", "contract_start", "renewal_date",
               "contracted_events", "add_ons", "csm_owner", "cs_pool", "last_qbr_date",
               "next_qbr_date"]
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for acc in accounts:
            if not acc["in_crm"]:
                continue
            start = acc["contract_start"]
            writer.writerow({
                "account_id": acc["id"],
                "account_name": acc["name"],
                "tier": acc["tier"],
                "region": acc["region"],
                "contract_start": start.isoformat(),
                "renewal_date": (start + timedelta(days=365)).isoformat(),
                "contracted_events": CONTRACT_SIZE[acc["tier"]],
                "add_ons": acc["add_ons"],
                "csm_owner": acc["csm"],
                "cs_pool": acc["pool"],
                "last_qbr_date": acc["last_qbr"].isoformat() if acc["last_qbr"] else "",
                "next_qbr_date": acc["next_qbr"].isoformat() if acc["next_qbr"] else "",
            })
    return path


def events_for_day(acc, day_index, day, pool):
    """All events one account produces on one day."""
    p = PERSONALITIES[acc["personality"]]
    if p.get("silent"):
        return []                                   # never onboarded: silence

    # The SDK goes in 2 days after signing, or 5 for the control group (no email)
    onboard_day = acc["contract_start"] + timedelta(
        days=SDK_DELAY_DAYS["control" if acc["control"] else "welcomed"])
    if day < onboard_day:
        return []

    out = []
    if day == onboard_day:
        out.append(event("sdk_installed", "product", day, acc["id"], pool[0]))

    # New accounts start with one user and grow to the full team over two weeks;
    # existing accounts grow (or not) across the 90 days
    if p["lifecycle"] == "new":
        days_live = (day - onboard_day).days
        target = min(acc["users_end"], 1 + round((acc["users_start"] - 1) * days_live / 10))
    else:
        target = round(acc["users_start"] + (acc["users_end"] - acc["users_start"]) * day_index / (DAYS - 1))
    invited_today = set()
    while len(pool) < target:
        new_user = f"{acc['id']}_usr_{len(pool) + 1:02d}"
        invited_today.add(new_user)
        out.append(event("user_invited", "product", day, acc["id"], random.choice(pool),
                         {"invited_user": new_user}))
        pool.append(new_user)

    # Product activity: each user is active with today's probability
    activity = lerp(p["activity"], day_index)
    for user in pool:
        champion = acc["has_champion"] and user == pool[0]
        # the champion turns up almost daily (if the account is active at all)
        # and does several times more per day than a normal user
        chance = min(0.95, activity * 1.8) if champion else activity
        chance = min(1.0, chance * WEEKDAY_ACTIVITY[day.weekday()])   # quiet weekends
        # someone who accepts an invite looks around the same day (otherwise a slow
        # coin flip could leave a healthy new customer looking Stalled at day 14)
        if random.random() < chance or user in invited_today:
            for _ in range(random.randint(5, 12) if champion else random.randint(1, 4)):
                out.append(event(random.choice(p["features"]), "product", day, acc["id"], user))

    # System: how many events the customer sent us today (this is what they pay for)
    daily_contract = CONTRACT_SIZE[acc["tier"]] / 365
    if p.get("ramp_days"):
        # new customers ramp up: more data sources and teams come on over the first month
        ramp = min(1.0, (day - onboard_day).days / p["ramp_days"])
        planned = p["util"][0] + (p["util"][1] - p["util"][0]) * ramp
    else:
        planned = lerp(p["util"], day_index)
    utilisation = min(1.2, planned * acc["util_factor"])   # cap at 120% (overage)
    volume = int(daily_contract * utilisation * random.uniform(0.85, 1.15))
    out.append(event("daily_event_volume", "system", day, acc["id"],
                     properties={"event_count": volume}))

    # System: ingestion errors
    if random.random() < lerp(p["errors"], day_index):
        for _ in range(random.randint(1, 5)):
            out.append(event("ingestion_error", "system", day, acc["id"],
                             properties={"error_code": random.choice(
                                 ["SCHEMA_MISMATCH", "AUTH_EXPIRED", "PAYLOAD_TOO_LARGE"])}))
    return out


# --- CS engagement history --------------------------------------------------------
# What CS did over the 90 days and how customers responded, in the same schema
# as the product events (event_type "engagement"). Accounts missing from the
# CRM get nothing: CS doesn't know they exist, which is the handoff gap.

def cs_event(rng, name, when, acc, owner, topic, user_id=None):
    return {
        "event_id": str(uuid.UUID(int=rng.getrandbits(128), version=4)),
        "event_name": name,
        "event_type": "engagement",
        "timestamp": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "account_id": acc["id"],
        "user_id": user_id,                     # the customer user, when they respond
        "properties": {"owner_id": owner, "topic": topic},
        "context": {"source": "cs_history"},
    }


def local_time(rng, day, region, hour):
    """A moment around `hour` local time on `day`, converted to UTC."""
    moment = datetime.combine(day, time(hour)) + timedelta(minutes=rng.randint(-20, 40))
    return moment - timedelta(hours=UTC_OFFSET_HOURS[region])


def workshop_days(region, first_allowed):
    """Fortnightly Tuesday workshops per region; F&B and the Nordics run the
    weeks in between, so the CS team isn't running them all at once."""
    day = START_DATE + timedelta(days=(1 - START_DATE.weekday()) % 7)   # first Tuesday
    if region in ("France & Benelux", "Nordics"):
        day += timedelta(days=7)
    while day <= END_DATE:
        if day >= first_allowed:
            yield day
        day += timedelta(days=14)


def visible_day(acc, below=None, above=None):
    """The day an account's problem (or growth) shows in its numbers: when its
    30-day utilisation would cross the line the flags use. Owners react to
    this, so "time to action" in the reports measures something real."""
    p = PERSONALITIES[acc["personality"]]
    for i in range(DAYS):
        util = min(1.2, lerp(p["util"], i) * acc["util_factor"])
        if (below is not None and util < below) or (above is not None and util >= above):
            return START_DATE + timedelta(days=i + TRAILING_LAG_DAYS)
    return None                                   # never crosses inside the window


def engagement_for_account(rng, acc):
    p = PERSONALITIES[acc["personality"]]
    region, start, tier = acc["region"], acc["contract_start"], acc["tier"]
    owner = acc["csm"] or acc["pool"]
    style = OWNER_STYLE["pool" if acc["pool"] else acc["csm"]] if owner else None
    contact = f"{acc['id']}_usr_01"
    out = []

    def follow_ups(after):
        """Owners who follow up keep meeting every few weeks; others don't."""
        if not style["follow_up_days"]:
            return
        day = after + timedelta(days=style["follow_up_days"] + rng.randint(-3, 3))
        while day <= END_DATE:
            meeting(day, "follow_up")
            day += timedelta(days=style["follow_up_days"] + rng.randint(-3, 3))

    def meeting(day, topic):
        day = nearest_weekday(day)
        out.append(cs_event(rng, "meeting_held", local_time(rng, day, region, rng.choice([10, 11, 14, 15])),
                            acc, owner, topic, contact))

    # Getting started email, the day after signing (control group: none)
    if acc["welcomed"]:
        sent = local_time(rng, start + timedelta(days=1 if start.weekday() < 4 else 7 - start.weekday()),
                          region, 9)                       # next working day
        out.append(cs_event(rng, "email_sent", sent, acc, "automated", "getting_started"))
        if rng.random() < OPEN_RATE[acc["personality"]]:
            opened = sent + timedelta(hours=min(120, rng.expovariate(1 / 18)))
            local = opened + timedelta(hours=UTC_OFFSET_HOURS[region])
            if local.weekday() >= 5 or not 7 <= local.hour <= 21:
                # nobody reads work email at 2 a.m. or at weekends: next working morning
                day = local.date() + timedelta(days=1 if local.hour > 21 else 0)
                while day.weekday() >= 5:
                    day += timedelta(days=1)
                opened = local_time(rng, day, region, 8)
            out.append(cs_event(rng, "email_opened", opened, acc, "automated", "getting_started", contact))

    # Scale: fortnightly workshops. New accounts are invited (in the email) to
    # their first onboarding workshop; live accounts sometimes join an advanced one.
    if tier == "Scale" and p["lifecycle"] == "new" and acc["welcomed"]:
        first = next(workshop_days(region, start + timedelta(days=4)), None)
        if first and rng.random() < ONBOARDING_WORKSHOP_RATE[acc["personality"]]:
            for n in range(1, min(3, acc["users_end"]) + 1):
                out.append(cs_event(rng, "workshop_attended", local_time(rng, first, region, 14),
                                    acc, owner, "onboarding_workshop", f"{acc['id']}_usr_{n:02d}"))
    if tier == "Scale" and acc["personality"] in ADVANCED_WORKSHOP_RATE:
        for day in workshop_days(region, START_DATE):
            rate = ADVANCED_WORKSHOP_RATE[acc["personality"]]
            if acc["personality"] == "declining":
                rate *= 1 - (day - START_DATE).days / DAYS      # interest fades as usage falls
            if rng.random() < rate:
                for n in range(1, rng.randint(1, 2) + 1):
                    out.append(cs_event(rng, "workshop_attended", local_time(rng, day, region, 14),
                                        acc, owner, "advanced_workshop", f"{acc['id']}_usr_{n:02d}"))

    if not owner:            # Self-service: no human coverage, so no meetings
        return in_window(out)

    # Enterprise: QBRs on the CRM dates, a kickoff for new accounts, regular check-ins
    if tier == "Enterprise":
        if acc["last_qbr"] and START_DATE <= acc["last_qbr"] <= END_DATE:
            meeting(acc["last_qbr"], "qbr")
        if p["lifecycle"] == "new":
            meeting(start + timedelta(days=rng.randint(*style["act_days"])), "kickoff")
        elif acc["personality"] in ("healthy", "power_user", "declining", "shelfware", "slipping"):
            day = START_DATE + timedelta(days=rng.randint(0, style["check_in_days"]))
            while day <= END_DATE:
                near_qbr = acc["last_qbr"] and abs((day - acc["last_qbr"]).days) < 10
                if not near_qbr:
                    meeting(day, "check_in")
                day += timedelta(days=style["check_in_days"] + rng.randint(-5, 5))

    # Declining accounts: a churn call once it shows, if the owner gets to it
    seen = visible_day(acc, below=0.40) if acc["personality"] == "declining" else None
    if seen and rng.random() < style["acts"]:
        first = seen + timedelta(days=rng.randint(*style["act_days"]))
        meeting(first, "churn_call")
        follow_ups(first)
    # Growing accounts: a growth call once the growth shows, if the owner gets to it
    seen = visible_day(acc, above=0.80) if acc["personality"] == "power_user" else None
    if seen and rng.random() < style["acts"]:
        first = seen + timedelta(days=rng.randint(*style["act_days"]))
        meeting(first, "growth_call")
        follow_ups(first)
    # Stalled Scale accounts: the pool calls once the 14-day window has passed
    if tier == "Scale" and acc["personality"] == "stalled" and rng.random() < style["acts"]:
        meeting(start + timedelta(days=14 + rng.randint(*style["act_days"])), "activation_call")

    return in_window(out)


def in_window(rows):
    """Keep only what falls inside the data window (a churn call planned for
    next week hasn't happened yet)."""
    return [e for e in rows if START_DATE.isoformat() <= e["timestamp"][:10] <= END_DATE.isoformat()]


def write_engagement(accounts):
    """One file per day with any CS activity in data/engagement/.
    Returns the number of rows and the accounts that had a churn call."""
    rng = random.Random(SEED + 4)         # own generator: no product number changes
    by_day = {START_DATE + timedelta(days=i): [] for i in range(DAYS)}
    called = set()
    for acc in accounts:
        if not acc["in_crm"]:
            continue
        for e in engagement_for_account(rng, acc):
            by_day[date.fromisoformat(e["timestamp"][:10])].append(e)
            if e["event_name"] == "meeting_held" and e["properties"]["topic"] == "churn_call":
                called.add(acc["id"])
    out_dir = OUT_DIR / "engagement"
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.jsonl"):
        old.unlink()
    total = 0
    for day, rows in by_day.items():
        rows.sort(key=lambda e: e["timestamp"])
        if not rows:
            continue                       # quiet days (weekends) get no file
        with (out_dir / f"{day.isoformat()}.jsonl").open("w") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")
        total += len(rows)
    return total, called


# --- Task history: as if the engine had been running for a few weeks ----------
# A fresh run sends every task on the same day, so every task is 0 days old,
# Open, and the cockpit looks frozen. Real teams have tasks of every age: some
# done, some taken in Slack and in progress, some open for weeks, some resolved
# because the flag went away. This writes that history into the action log
# (data/action_log.csv), dated the day each account's problem showed, plus the
# Slack "I'll take it" clicks for some of them. The pipeline then works it out
# like real data: a churn call after the task makes it Done (action_status),
# a claim makes it Claimed, a flag that's gone makes it Resolved. Tasks with
# the same account, flag and status aren't sent again (actions.py).
# Its own random generator, so nothing else in the data changes.

HISTORY_CLAIM_RATE = 0.4        # share of open pool tasks someone took in Slack
HISTORY_QBR_SLIPS = 2           # Enterprise accounts whose promised QBR came and went

TASK_HISTORY = {                # personality -> flag, status, when it showed, the action
    "declining": ("churn", "Act", dict(below=0.40), "Call this week: usage and breadth are dropping"),
    "slipping":  ("churn", "Watch", dict(below=0.40), "Check in: usage is sliding, users are steady"),
    "power_user": ("expansion", "Act", dict(above=0.80), "Build the growth case: usage and users are up"),
}


def write_task_history(accounts):
    rng = random.Random(SEED + 9)
    rows, claims = [], []
    log = lambda day, acc, flag, status, owner, channel, action, outcome: rows.append(
        [day.isoformat(), acc["id"], flag, status, owner, channel, action, outcome])
    slips = 0
    for acc in accounts:
        owner = acc["csm"] or acc["pool"]
        if not (acc["in_crm"] and owner):
            continue                       # Self-service: emails, not tasks for people
        kind = acc["personality"]
        if kind in TASK_HISTORY:
            flag, status, line, action = TASK_HISTORY[kind]
            seen = visible_day(acc, **line)
            if not seen or seen > END_DATE - timedelta(days=2):
                continue                   # too recent: the next run sends it
            sent = nearest_weekday(seen + timedelta(days=1))
            # a couple of Enterprise accounts were handed to a QBR that never happened
            if (kind == "declining" and acc["tier"] == "Enterprise" and slips < HISTORY_QBR_SLIPS
                    and (not acc["last_qbr"] or acc["last_qbr"] < sent)):
                promised = END_DATE - timedelta(days=rng.randint(5, 15))
                log(sent, acc, flag, status, owner, "qbr", action, f"covered by the QBR on {promised.isoformat()}")
                slips += 1
                continue
            log(sent, acc, flag, status, owner, "queue", action, "posted to Slack (history)")
            # some pool tasks were taken in Slack a day or three later
            if acc["pool"] and rng.random() < HISTORY_CLAIM_RATE:
                when = local_time(rng, nearest_weekday(sent + timedelta(days=rng.randint(1, 3))), acc["region"], 10)
                if when.date() <= END_DATE:
                    claims.append({"event_id": str(uuid.UUID(int=rng.getrandbits(128), version=4)),
                                   "event_name": "task_claimed", "event_type": "engagement",
                                   "timestamp": when.strftime("%Y-%m-%dT%H:%M:%SZ"), "account_id": acc["id"],
                                   "user_id": None, "properties": {"owner_id": owner, "topic": flag},
                                   "context": {"source": "slack"}})
        elif kind == "shelfware":
            # flagged weeks ago, so a Scale follow-up falls due on the next run
            sent = nearest_weekday(END_DATE - timedelta(days=rng.randint(31, 45)))
            log(sent, acc, "churn", "Shelfware", owner, "queue",
                "Adoption review: agree a ramp plan before renewal", "posted to Slack (history)")
        elif kind == "stalled" and acc["pool"]:
            sent = nearest_weekday(acc["contract_start"] + timedelta(days=15))
            if sent <= END_DATE - timedelta(days=2):
                log(sent, acc, "activation", "Stalled", owner, "queue",
                    "Help them get a second person started", "posted to Slack (history)")
    rows.sort()
    path = OUT_DIR / "action_log.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["logged_on", "account_id", "flag", "status", "routed_to", "channel", "action", "outcome"])
        writer.writerows(rows)
    claims.sort(key=lambda e: e["timestamp"])
    with (OUT_DIR / "engagement" / "task_history.jsonl").open("w") as f:
        for e in claims:
            f.write(json.dumps(e) + "\n")
    return len(rows), len(claims), slips


# --- Renewal outcomes: the future, for backtesting ---------------------------
# What happened at each renewal in the OUTCOME_HORIZON_DAYS after the data
# ends, as if we were looking back from later. Real teams get this from the
# CRM (closed-won/lost renewals). It lets backtest.py ask: did the flags warn
# us about the accounts that churned or shrank, and how early?
# Mostly driven by personality, with surprises: a healthy account can still
# churn (acquired, budget cut), and no flag can see that coming. A churn call
# improves a declining account's odds: that is the "outcome after a call".

OUTCOME_HORIZON_DAYS = 180
OUTCOME_ODDS = {                      # renewed, expanded, downsized, churned
    "healthy":      (0.90, 0.03, 0.04, 0.03),
    "power_user":   (0.38, 0.60, 0.00, 0.02),
    "shelfware":    (0.25, 0.00, 0.55, 0.20),
    "declining":    (0.15, 0.00, 0.30, 0.55),
    "slipping":     (0.55, 0.00, 0.35, 0.10),     # usually a smaller renewal, rarely a loss
    "declining_called": (0.40, 0.00, 0.30, 0.30),   # a churn call happened
}
OUTCOMES = ("renewed", "expanded", "downsized", "churned")


def write_renewal_outcomes(accounts, called):
    rng = random.Random(SEED + 7)         # own generator: nothing else changes
    path = OUT_DIR / "renewal_outcomes.csv"
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["account_id", "renewal_date", "outcome"])
        for acc in accounts:
            renewal = acc["contract_start"] + timedelta(days=365)
            if not acc["in_crm"] or not (END_DATE < renewal <= END_DATE + timedelta(days=OUTCOME_HORIZON_DAYS)):
                continue
            kind = acc["personality"]
            if kind == "declining" and acc["id"] in called:
                kind = "declining_called"
            odds = OUTCOME_ODDS.get(kind, OUTCOME_ODDS["healthy"])
            writer.writerow([acc["id"], renewal.isoformat(), rng.choices(OUTCOMES, weights=odds)[0]])
    return path


def write_holdout(accounts):
    """The onboarding programme's own list of held-out accounts. Unlike the
    answer key, this is a real-world fact: whoever sends onboarding emails
    must check it, or the control group stops being one."""
    path = OUT_DIR / "onboarding_holdout.csv"
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["account_id", "held_out_since"])
        for acc in accounts:
            if acc["control"]:
                writer.writerow([acc["id"], acc["contract_start"].isoformat()])
    return path


def write_answer_key(accounts):
    """The truth behind the data: which personality each account was given.

    Real data has no answer key, so the flags must never read this file. It
    exists only so check_flags.py can test whether each flag finds the
    accounts it should.
    """
    path = OUT_DIR / "answer_key.csv"
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["account_id", "tier", "personality", "has_champion", "welcome_group"])
        for acc in accounts:
            group = "welcomed" if acc["welcomed"] else "control" if acc["control"] else ""
            writer.writerow([acc["id"], acc["tier"], acc["personality"], acc["has_champion"], group])
    return path


def write_events(accounts):
    events_dir = OUT_DIR / "events"
    events_dir.mkdir(parents=True, exist_ok=True)
    for old in events_dir.glob("*.jsonl"):          # start clean: no files left from older runs
        old.unlink()
    pools = {acc["id"]: [f"{acc['id']}_usr_01"] for acc in accounts}
    total = 0
    for day_index in range(DAYS):
        day = START_DATE + timedelta(days=day_index)
        rows = []
        for acc in accounts:
            rows.extend(events_for_day(acc, day_index, day, pools[acc["id"]]))
        rows.sort(key=lambda e: e["timestamp"])
        with (events_dir / f"{day.isoformat()}.jsonl").open("w") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")
        total += len(rows)
    return total


if __name__ == "__main__":
    random.seed(SEED)
    OUT_DIR.mkdir(exist_ok=True)
    accounts = build_accounts()
    team_path = write_team()
    crm_path = write_crm(accounts)
    key_path = write_answer_key(accounts)
    holdout_path = write_holdout(accounts)
    total = write_events(accounts)
    cs_total, called = write_engagement(accounts)
    outcomes_path = write_renewal_outcomes(accounts, called)
    # New data is a new world: the old action log is replaced by this data's own
    # history (tasks of every age), and the old email events went with data/engagement/
    tasks, claimed, slipped = write_task_history(accounts)
    print(f"Accounts:   {len(accounts)} ({sum(a['in_crm'] for a in accounts)} in the CRM)")
    print(f"CS team:    {team_path}")
    print(f"CRM table:  {crm_path}")
    print(f"Answer key: {key_path} (for check_flags.py only)")
    print(f"Holdout:    {holdout_path} (no onboarding emails for these accounts)")
    print(f"Events:     {total:,} across {DAYS} daily files, {START_DATE} to {END_DATE}")
    print(f"Engagement: {cs_total:,} CS touches and responses in data/engagement/")
    print(f"Task history: {tasks} earlier tasks in data/action_log.csv, {claimed} taken in Slack, "
          f"{slipped} QBRs that slipped")
    print(f"Outcomes:   {outcomes_path} (renewals in the {OUTCOME_HORIZON_DAYS} days after the data, for backtest.py)")
