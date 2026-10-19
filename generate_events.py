"""
Cairnpath fake data generator.

Writes two things into ./data:
  - crm_accounts.csv       the fake CRM table (one row per account)
  - events/YYYY-MM-DD.jsonl  one file per day, one JSON event per line
                             (like a daily dump landing in S3)

Each account has a "personality" that shapes its behaviour over time,
so the flags we build later have something real to find.

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

# Contracted events per year, by tier
CONTRACT_SIZE = {
    "Self-service": 12_000_000,
    "Scale": 60_000_000,
    "Enterprise": 300_000_000,
}

# --- Personalities --------------------------------------------------------
# Every value with _start/_end moves in a straight line across the 90 days.
#   activity  = chance that each user is active on a given day
#   util      = share of the contracted volume the customer is sending
#   users     = size of the user pool (growth creates user_invited events)
#   errors    = chance of ingestion errors on a given day
#   features  = which product events their users produce

PERSONALITIES = {
    "healthy": dict(
        activity=(0.6, 0.6), util=(0.65, 0.70), users=(8, 9), errors=(0.02, 0.02),
        features=["chart_created", "dashboard_shared", "cohort_created", "data_export"],
    ),
    "declining": dict(
        activity=(0.6, 0.05), util=(0.55, 0.22), users=(10, 10), errors=(0.02, 0.35),
        features=["chart_created", "dashboard_shared"],
    ),
    "power_user": dict(
        activity=(0.75, 0.9), util=(0.80, 0.97), users=(10, 15), errors=(0.01, 0.01),
        features=["chart_created", "dashboard_shared", "cohort_created",
                  "experiment_launched", "data_export"],
    ),
    "new_customer": dict(
        activity=(0.5, 0.5), util=(0.30, 0.30), users=(3, 5), errors=(0.03, 0.03),
        features=["chart_created", "dashboard_shared"],
    ),
    "never_onboarded": None,   # contract signed, no events at all
    "handoff_gap": dict(       # active in the product, but missing from the CRM
        activity=(0.5, 0.55), util=(0.40, 0.45), users=(4, 5), errors=(0.02, 0.02),
        features=["chart_created", "dashboard_shared", "cohort_created"],
    ),
}

# --- Accounts ---------------------------------------------------------------
# contract_offset = days between contract start and START_DATE
#   (positive = signed before the data window, negative = signed during it)
# in_crm=False makes the handoff gap: activity with no CRM row.

ACCOUNTS = [
    dict(id="acc_001", name="Northgate Retail", tier="Enterprise", personality="healthy",
         contract_offset=200, add_ons="experiments", csm="csm_anna", qbr_in_days=20),
    dict(id="acc_002", name="Bluefin Media", tier="Enterprise", personality="declining",
         contract_offset=220, add_ons="experiments", csm="csm_tom", qbr_in_days=25),
    dict(id="acc_003", name="Orbit Logistics", tier="Enterprise", personality="power_user",
         contract_offset=150, add_ons="experiments", csm="csm_anna", qbr_in_days=60),
    dict(id="acc_004", name="Lumen Health", tier="Scale", personality="healthy",
         contract_offset=120, add_ons=""),
    dict(id="acc_005", name="Tidewater Apps", tier="Scale", personality="declining",
         contract_offset=200, add_ons=""),
    dict(id="acc_006", name="Kestrel Fintech", tier="Scale", personality="power_user",
         contract_offset=240, add_ons="experiments"),
    dict(id="acc_007", name="Pinecone Studio", tier="Scale", personality="never_onboarded",
         contract_offset=-70, add_ons=""),
    dict(id="acc_008", name="Quarry Games", tier="Scale", personality="new_customer",
         contract_offset=-75, add_ons=""),
    dict(id="acc_009", name="Maple Learning", tier="Self-service", personality="healthy",
         contract_offset=90, add_ons=""),
    dict(id="acc_010", name="Saffron Foods", tier="Self-service", personality="new_customer",
         contract_offset=-80, add_ons=""),
    dict(id="acc_011", name="Driftwood Travel", tier="Self-service", personality="never_onboarded",
         contract_offset=-60, add_ons=""),
    dict(id="acc_012", name="Copperline Labs", tier="Self-service", personality="handoff_gap",
         contract_offset=100, add_ons="", in_crm=False),
]


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


def contract_start(acc):
    return START_DATE - timedelta(days=acc["contract_offset"])


# --- CRM table ---------------------------------------------------------------

def write_crm():
    path = OUT_DIR / "crm_accounts.csv"
    columns = ["account_id", "account_name", "tier", "contract_start", "renewal_date",
               "contracted_events", "add_ons", "csm_owner", "next_qbr_date"]
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for acc in ACCOUNTS:
            if not acc.get("in_crm", True):
                continue
            start = contract_start(acc)
            qbr = acc.get("qbr_in_days")
            writer.writerow({
                "account_id": acc["id"],
                "account_name": acc["name"],
                "tier": acc["tier"],
                "contract_start": start.isoformat(),
                "renewal_date": (start + timedelta(days=365)).isoformat(),
                "contracted_events": CONTRACT_SIZE[acc["tier"]],
                "add_ons": acc["add_ons"],
                "csm_owner": acc.get("csm", ""),
                "next_qbr_date": (END_DATE + timedelta(days=qbr)).isoformat() if qbr else "",
            })
    return path


# --- Events ------------------------------------------------------------------

def events_for_day(acc, day_index, day, pool):
    """All events one account produces on one day."""
    p = PERSONALITIES[acc["personality"]]
    if p is None:
        return []                                   # never onboarded: silence

    start = contract_start(acc)
    onboard_day = start + timedelta(days=2)         # new customers install 2 days after signing
    if day < onboard_day:
        return []

    out = []
    if day == onboard_day:
        out.append(event("sdk_installed", "product", day, acc["id"], pool[0]))

    # Grow the user pool; each new user is invited by an existing one
    target = round(lerp(p["users"], day_index))
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
                name = random.choice(p["features"])
                out.append(event(name, "product", day, acc["id"], user))

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


def write_events():
    events_dir = OUT_DIR / "events"
    events_dir.mkdir(parents=True, exist_ok=True)
    pools = {acc["id"]: [f"{acc['id']}_usr_01"] for acc in ACCOUNTS}
    total = 0
    for day_index in range(DAYS):
        day = START_DATE + timedelta(days=day_index)
        rows = []
        for acc in ACCOUNTS:
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
    crm_path = write_crm()
    total = write_events()
    print(f"CRM table:  {crm_path}")
    print(f"Events:     {total:,} across {DAYS} daily files, {START_DATE} to {END_DATE}")
