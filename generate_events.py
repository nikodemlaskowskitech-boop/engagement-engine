"""
Cairnpath fake data generator.

Writes three things into ./data:
  - crm_accounts.csv          the fake CRM table (one row per account)
  - cs_team.csv               who covers what: named CSMs and regional pools
  - events/YYYY-MM-DD.jsonl   one file per day, one JSON event per line
                              (like a daily dump landing in S3)

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
#   weight        = share of accounts with this personality

PERSONALITIES = {
    "healthy": dict(
        weight=0.57, lifecycle="existing",
        activity=(0.6, 0.6), util=(0.65, 0.70), user_growth=1.1, errors=(0.02, 0.02),
        features=["chart_created", "dashboard_shared", "cohort_created", "data_export"],
    ),
    "declining": dict(
        weight=0.13, lifecycle="existing",
        activity=(0.6, 0.05), util=(0.55, 0.22), user_growth=1.0, errors=(0.02, 0.35),
        features=["chart_created", "dashboard_shared"],
    ),
    "power_user": dict(
        weight=0.10, lifecycle="existing",
        activity=(0.75, 0.9), util=(0.80, 0.97), user_growth=1.5, errors=(0.01, 0.01),
        features=["chart_created", "dashboard_shared", "cohort_created",
                  "experiment_launched", "data_export"],
    ),
    "new_customer": dict(      # signs recently, installs the SDK, the team gets going
        weight=0.08, lifecycle="new",
        activity=(0.5, 0.5), util=(0.30, 0.30), user_growth=1.4, errors=(0.03, 0.03),
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
    # An Enterprise deal is never missing from the CRM, so no handoff gaps there
    names = [n for n in PERSONALITIES if not (tier == "Enterprise" and n == "handoff_gap")]
    return random.choices(names, weights=[PERSONALITIES[n]["weight"] for n in names])[0]


def contract_start_for(personality):
    """When the contract started, depending on the account's lifecycle."""
    p = PERSONALITIES[personality]
    if p["lifecycle"] == "existing":
        # Signed before the data window, renewal still ahead (offset < 275 days)
        return START_DATE - timedelta(days=random.randint(10, 270))
    if personality == "never_onboarded":
        return END_DATE - timedelta(days=random.randint(12, 40))
    if personality == "stalled":
        return END_DATE - timedelta(days=random.randint(16, 40))
    return END_DATE - timedelta(days=random.randint(3, 40))      # new_customer


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
                add_ons="experiments" if (tier != "Self-service" and random.random() < 0.4) else "",
                csm="",
                pool="",
                next_qbr="",
            )
            if tier == "Enterprise":
                # Round-robin the region's CSMs so each carries the same book
                region_csms = csms_by_region[region]
                acc["csm"] = region_csms[i % len(region_csms)]
                acc["next_qbr"] = END_DATE + timedelta(days=random.randint(5, 90))
            elif tier == "Scale":
                acc["pool"] = pools_by_region[region][0]
            accounts.append(acc)

    # A few new Enterprise accounts were never handed to a CSM (a handoff miss)
    new_enterprise = [a for a in accounts if a["tier"] == "Enterprise"
                      and PERSONALITIES[a["personality"]]["lifecycle"] == "new" and a["in_crm"]]
    for acc in new_enterprise[:2]:
        acc["csm"] = ""
    return accounts


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
               "contracted_events", "add_ons", "csm_owner", "cs_pool", "next_qbr_date"]
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
                "next_qbr_date": acc["next_qbr"].isoformat() if acc["next_qbr"] else "",
            })
    return path


def events_for_day(acc, day_index, day, pool):
    """All events one account produces on one day."""
    p = PERSONALITIES[acc["personality"]]
    if p.get("silent"):
        return []                                   # never onboarded: silence

    onboard_day = acc["contract_start"] + timedelta(days=2)   # SDK goes in 2 days after signing
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
    while len(pool) < target:
        new_user = f"{acc['id']}_usr_{len(pool) + 1:02d}"
        out.append(event("user_invited", "product", day, acc["id"], random.choice(pool),
                         {"invited_user": new_user}))
        pool.append(new_user)

    # Product activity: each user is active with today's probability
    activity = lerp(p["activity"], day_index)
    for user in pool:
        if random.random() < activity:
            for _ in range(random.randint(1, 4)):
                out.append(event(random.choice(p["features"]), "product", day, acc["id"], user))

    # System: how many events the customer sent us today (this is what they pay for)
    daily_contract = CONTRACT_SIZE[acc["tier"]] / 365
    volume = int(daily_contract * lerp(p["util"], day_index) * random.uniform(0.85, 1.15))
    out.append(event("daily_event_volume", "system", day, acc["id"],
                     properties={"event_count": volume}))

    # System: ingestion errors
    if random.random() < lerp(p["errors"], day_index):
        for _ in range(random.randint(1, 5)):
            out.append(event("ingestion_error", "system", day, acc["id"],
                             properties={"error_code": random.choice(
                                 ["SCHEMA_MISMATCH", "AUTH_EXPIRED", "PAYLOAD_TOO_LARGE"])}))
    return out


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
    total = write_events(accounts)
    print(f"Accounts:   {len(accounts)} ({sum(a['in_crm'] for a in accounts)} in the CRM)")
    print(f"CS team:    {team_path}")
    print(f"CRM table:  {crm_path}")
    print(f"Events:     {total:,} across {DAYS} daily files, {START_DATE} to {END_DATE}")
