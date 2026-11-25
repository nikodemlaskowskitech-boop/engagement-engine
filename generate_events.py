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
import zlib
import uuid
from datetime import date, datetime, time, timedelta
from pathlib import Path

from settings import reach_days     # the planted effect uses the cockpit's own rule (17 Nov 2026)

# --- Settings -------------------------------------------------------------

SLOW_HANDOFF_SHARE = 0.3       # new Enterprise logos whose CSM came weeks late...
SLOW_HANDOFF_DAYS = (18, 40)   # ...this many days after the contract started (else 1-6)
# The three hand-written stories (stories/) are about these accounts, so their
# personality is fixed whatever the weights draw: Yarrow Studio declining,
# Nettle Foods growing, Driftwood Labs shelfware.
STORY_ACCOUNTS = {"acc_024": "declining", "acc_012": "power_user", "acc_156": "shelfware"}
STORY_RENEWALS = {"acc_024": date(2027, 1, 31), "acc_012": date(2026, 12, 15), "acc_156": date(2026, 11, 13)}
# The four hero accounts (24 Nov 2026): the stories a demo walks through, each a two-year arc
# that tests/heroes_test.py holds in place. The three handover stories above, plus Alder Media,
# the signal nobody saw. Each one's renewal in today's year is set here, not drawn (the draw
# still happens, so no other account moves), and none has a random dip in year one: its
# year-one is the calm before its story.
#   Nettle Foods    signed Dec 2024, ramped, expanded at its first renewal (Dec 2025); a June
#                   wobble caught and recovered; now heading past its contract (expansion Act)
#   Yarrow Studio   a calm year, expanded in Jan 2026; falling since summer, caught early
#                   (churn Act and a call the same week), renewal Jan 2027
#   Driftwood Labs  bought 106M events in 2023, never used a quarter of them, renewed flat
#                   twice: Shelfware every week of two years, renewal 13 Nov 2026 (right-size)
#   Alder Media     signed Jan 2025, expanded Jan 2026; a June ticket asks for a full export
#                   "for a vendor comparison", an October email about a competitor's contract,
#                   and no meeting in 30 days: the missed signal (its documents: the corpus)
HEROES = {"acc_012": ("expanded", 1.5), "acc_024": ("expanded", 1.6),
          "acc_156": ("renewed", 1.0), "acc_042": ("expanded", 1.8)}   # today's year's renewal: outcome, ARR after / before
SEED = 42                      # same seed = same data every run
END_DATE = date(2026, 10, 18)  # last day of data ("today" for the flags = the day after)
DAYS = 365                     # a year of history, so trends and "as of" views have something to show
ARC_DAYS = 90                  # each account's current story (declining, power user...) plays out
                               # over the last 90 days; before that it sits at its normal level
START_DATE = END_DATE - timedelta(days=DAYS - 1)
# Year one (24 Nov 2026, Nikodem: two years of history): the year before START_DATE, written
# after everything else with its own random streams, so today's year stays exactly as it was
# (every tested number holds). Usage, onboardings, a few dips that recover, QBRs and
# check-ins; its renewals stay in the finance bridge (arr_bridge_history.csv) for now.
YEAR_ONE_DAYS = 365
HISTORY_START = START_DATE - timedelta(days=YEAR_ONE_DAYS)
YEAR_ONE_DIPS = dict(share=0.10, start=(30, 250), bottom=0.5, fall=30, recover=40)
YEAR_ONE_MEETINGS = {"Enterprise": ("qbr", 91), "Scale": ("check_in", 120)}   # topic, every this many days
OUT_DIR = Path("data")

REGIONS = ["UK&I", "DACH", "France & Benelux", "Nordics"]

# How many accounts per tier, and how they spread across regions.
# Enterprise is fixed so every CSM carries 8 accounts.
TIER_COUNTS = {"Enterprise": 48, "Scale": 150, "Self-service": 300}
ENTERPRISE_PER_REGION = {"UK&I": 16, "DACH": 16, "France & Benelux": 8, "Nordics": 8}
REGION_WEIGHTS = {"UK&I": 0.35, "DACH": 0.30, "France & Benelux": 0.20, "Nordics": 0.15}

# Contracted events per year, by tier: the middle of the range. Each account
# gets its own size, from half to double this (own random generator, so the
# rest of the data doesn't change).
CONTRACT_SIZE = {
    "Enterprise": 300_000_000,
    "Scale": 60_000_000,
    "Self-service": 12_000_000,
}
# What the company charges, in EUR per 1,000 contracted events (volume
# discount: bigger tiers pay less per event). ARR = contract x price; it goes
# into the CRM, like a real company's ARR or annual order value field.
PRICE_PER_1K = {"Enterprise": 0.45, "Scale": 0.55, "Self-service": 0.65}

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

# --- The year before the current story --------------------------------------------
# New logos: of the existing accounts whose current term started during the year,
# this share signed then (a new customer); the rest renewed that day.
FIRST_YEAR_SHARE = 0.12     # new logos: with the new customers of today's story, about 25% ARR growth a year
# Episodes: accounts that hit a dip during the year. The owner gets a task when it
# shows and calls (or doesn't) in their own style. The planted effect, like the
# onboarding email's: reached within its tier's reach_days (settings [journeys]), the account recovers more
# often. A recovered account carries on as one of today's healthy accounts; one
# that doesn't churns at its renewal and leaves the book (status "churned" in the
# CRM, no events after). Fake data: it shows the method, not that calls work.
# Tuned (12 Nov 2026) so the year reads like a healthy B2B book: about 89% GRR.
EPISODES = 110
EPISODE_LAST_START = 195   # the last day a dip may start: recovered by the time today's story begins
RECOVERY_ODDS = {"called_in_time": 0.80, "not_in_time": 0.55}
# In-year renewals of accounts that stayed: most renew flat, some grow, some shrink
# (an account that had a dip shrinks more often); the bigger the tier, the more
# room to grow (more teams, add-ons). Fake numbers, own generator.
RENEWAL_CHANGE_ODDS = {                   # renewed, expanded, downsized
    "Enterprise": (0.36, 0.58, 0.06), "Scale": (0.53, 0.40, 0.07), "Self-service": (0.80, 0.14, 0.06),
    "had_a_dip": (0.65, 0.05, 0.30)}
EXPAND_BY = (1.25, 1.90)     # the new ARR over the old one
DOWNSIZE_TO = (0.70, 0.90)
# The year before the data, as finance reports it: quarterly ARR bridge, top line only
PRIOR_YEAR = {"grr": 0.87, "nrr": 0.99, "new_logo_share": 0.20}
EPISODE_TIER_WEIGHTS = {"Enterprise": 0.12, "Scale": 0.33, "Self-service": 0.55}
DIP_BOTTOM = 0.30          # usage and activity fall to this share of normal...
DIP_DAYS = 30              # ...over this many days
RECOVER_DAYS = 40          # and climb back over this many, once recovery starts

# A planted trend, for the leader's Signals band (21 Nov 2026, Nikodem: "this is a demo, we are
# telling a story and we want these points to appear"). The brief's own example: new customers
# ramp worse than the book. Among new logos signed 60 to 200 days before the end of the data
# (live long enough for the Shelfware rule), about half of the healthy ones become shelfware:
# they go live, ramp a little, and never roll out. Chosen by a hash of the account ID, not a
# random draw. It is written to the answer key's "planted" column, check_flags.py tests that
# the engine finds it, and the page says it was planted. Set share to 0 to turn it off.
PLANTED_TREND = dict(name="new-cohort shelfware", signed_days_ago=(60, 200), share=0.5)
# The data lab (22 Nov 2026, Operational and the Signals band read flat): every decline started
# the same day, 90 days before the end, so the flags piled up at the year's end and nothing ever
# got better. Two fixes, both chosen by a hash of the account ID (no random draw moves):
# - each declining, slipping or power-user account's story lasts its own length, STORY_DAYS
#   (the handover stories' accounts keep 90), so signals fire across the autumn;
# - RECOVERY: a share of healthy Enterprise and Scale accounts sat far below their contract
#   all year and recover over the last 90 days (an adoption push that worked): Shelfware or
#   Watch three months ago, no flag today. The answer key keeps them "healthy" (their flags
#   today) and marks them "recovered" in the planted column.
STORY_DAYS = (45, 150)
# Planted bad data (24 Nov 2026, Nikodem: "mimic wrong data entries or lack of it"). Real CRMs
# have holes, and a feed can stop while the product keeps working. A few accounts get one gap
# each, chosen by a hash of the account ID (no random draw moves): the CRM field is written
# empty or in a format the engine can't read, or the daily volume feed stops while product
# events keep arriving. The flags that need the missing data show "data missing" instead
# (flags_data.csv, flags.py), and the answer key's data_issue column says what was planted,
# for check_flags.py. One per row: (issue, tier, personality, region or None). The first and
# the fifth hide a live risk (users falling; an open Shelfware flag), so they reach the owner.
DATA_ISSUES = [("no_contract", "Scale", "declining", "DACH"),
               ("no_contract", "Self-service", "healthy", None),
               ("feed_stopped", "Scale", "healthy", None),
               ("feed_stopped", "Enterprise", "healthy", None),
               ("bad_renewal", "Scale", "shelfware", "DACH"),
               ("bad_renewal", "Enterprise", "healthy", None),
               ("no_pool", "Scale", "healthy", None)]
FEED_STOP_DAYS = 12        # feed_stopped: no daily_event_volume in the last this many days
# accounts the tests, the demo script and the stories name: never given a gap
DATA_ISSUE_SPARED = {"acc_001", "acc_002", "acc_003", "acc_005", "acc_008", "acc_012", "acc_024", "acc_037",
                     "acc_042", "acc_087", "acc_100", "acc_123", "acc_156", "acc_300"}
# Growth hooks (25 Nov 2026, whitespace: Nikodem's "no hook, no pitch"). Features follow what
# was bought: experiments only with the experiments add-on, and add-on holders use it. A few
# accounts get a hook a CSM could open a growth conversation with, picked by a hash like the
# data gaps (no random draw moves), each with what the engine should do with it:
#   addon         cohort-heavy in the last 90 days, no experiments add-on: the add-on's hook
#   people        dashboards shared to many people, few of them log in: an adoption hook
#   unused_addon  the experiments add-on bought, never used: negative whitespace (retention)
# (issue, tier, personality, expected). Expected "show", or held back: "held_churn" (an account
# at churn risk isn't pitched to) or "held_core" (Scale: the basics first, then add-ons).
GROWTH_HOOKS = [("addon", "Enterprise", "healthy", "show"), ("addon", "Enterprise", "healthy", "show"),
                ("addon", "Enterprise", "power_user", "show"), ("addon", "Scale", "healthy", "show"),
                ("addon", "Scale", "healthy", "show"), ("addon", "Scale", "healthy", "show"),
                ("addon", "Scale", "power_user", "show"), ("addon", "Scale", "declining", "held_churn"),
                ("addon", "Scale", "shelfware", "held_core"),
                ("people", "Enterprise", "healthy", "show"), ("people", "Enterprise", "healthy", "show"),
                ("people", "Scale", "healthy", "show"), ("people", "Scale", "healthy", "show"),
                ("people", "Scale", "healthy", "show"),
                ("unused_addon", "Enterprise", "healthy", "show"), ("unused_addon", "Enterprise", "healthy", "show"),
                ("unused_addon", "Scale", "healthy", "show"), ("unused_addon", "Scale", "healthy", "show"),
                ("unused_addon", "Scale", "healthy", "show")]
HOOK_DAYS = 90                 # hooks are recent behaviour: the last this many days
ADDON = "experiments"
RECOVERY = dict(name="recovered", share=0.07, util=(0.30, 0.66), activity=(0.35, 0.6))

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
    # owner_id,        name,              type,    region,             manager,      pool
    ("csm_anna",       "Anna Whitfield",  "csm",    "UK&I",             "mgr_james",  ""),
    ("csm_tom",        "Tom Brennan",     "csm",    "UK&I",             "mgr_james",  ""),
    ("csm_lukas",      "Lukas Brandt",    "csm",    "DACH",             "mgr_sophie", ""),
    ("csm_mia",        "Mia Hofer",       "csm",    "DACH",             "mgr_sophie", ""),
    ("csm_claire",     "Claire Dubois",   "csm",    "France & Benelux", "mgr_sophie", ""),
    ("csm_erik",       "Erik Lindqvist",  "csm",    "Nordics",          "mgr_james",  ""),
    # the regional queues: new Scale tasks land here, anyone in the pool takes them
    ("pool_uki",       "UK&I Scale team", "pool",   "UK&I",             "mgr_james",  ""),
    ("pool_dach",      "DACH Scale team", "pool",   "DACH",             "mgr_sophie", ""),
    ("pool_fb",        "F&B Scale team",  "pool",   "France & Benelux", "mgr_sophie", ""),
    ("pool_nordics",   "Nordics Scale team", "pool", "Nordics",         "mgr_james",  ""),
    # the people in each pool, about 1 per 15-20 Scale accounts; each owns some
    # Scale accounts by name (forecasts, next steps, their own My book)
    ("pm_priya",       "Priya Shah",      "member", "UK&I",             "mgr_james",  "pool_uki"),
    ("pm_owen",        "Owen Clarke",     "member", "UK&I",             "mgr_james",  "pool_uki"),
    ("pm_hannah",      "Hannah Doyle",    "member", "UK&I",             "mgr_james",  "pool_uki"),
    ("pm_jonas",       "Jonas Weber",     "member", "DACH",             "mgr_sophie", "pool_dach"),
    ("pm_lena",        "Lena Fischer",    "member", "DACH",             "mgr_sophie", "pool_dach"),
    ("pm_camille",     "Camille Martin",  "member", "France & Benelux", "mgr_sophie", "pool_fb"),
    ("pm_bram",        "Bram Janssen",    "member", "France & Benelux", "mgr_sophie", "pool_fb"),
    ("pm_sofie",       "Sofie Nilsen",    "member", "Nordics",          "mgr_james",  "pool_nordics"),
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

# Weights tuned 12 Nov 2026 so today reads like a real book (about 5% at churn Act,
# not 20%): people trust a demo that looks like their own business.
PERSONALITIES = {
    "healthy": dict(
        weight=0.69, lifecycle="existing",
        activity=(0.6, 0.6), util=(0.65, 0.70), user_growth=1.1, errors=(0.02, 0.02),
        features=["chart_created", "dashboard_shared", "cohort_created", "data_export"],
    ),
    "declining": dict(
        weight=0.05, lifecycle="existing",
        activity=(0.6, 0.05), util=(0.55, 0.22), user_growth=1.0, errors=(0.02, 0.35),
        features=["chart_created", "dashboard_shared"],
    ),
    "slipping": dict(          # volume sliding under the line, but the same people still use it:
        weight=0.03, lifecycle="existing",       # one churn signal only, so churn Watch
        activity=(0.6, 0.6), util=(0.58, 0.31), user_growth=1.0, errors=(0.02, 0.02),
        features=["chart_created", "dashboard_shared", "cohort_created"],
    ),
    "power_user": dict(
        weight=0.05, lifecycle="existing",
        activity=(0.75, 0.9), util=(0.80, 0.97), user_growth=1.5, errors=(0.01, 0.01),
        features=["chart_created", "dashboard_shared", "cohort_created",
                  "experiment_launched", "data_export"],
    ),
    "new_customer": dict(      # signs during the window, installs the SDK, the team gets going
        weight=0.08, lifecycle="new", ramp_days=30,   # volume ramps up over the first month
        activity=(0.5, 0.5), util=(0.20, 0.70), user_growth=1.4, errors=(0.03, 0.03),
        features=["chart_created", "dashboard_shared"],
    ),
    "shelfware": dict(         # live for months, people use it, but the rollout never came:
        weight=0.04, lifecycle="existing",       # flat volume far below what they bought
        activity=(0.45, 0.45), util=(0.22, 0.24), user_growth=1.0, errors=(0.02, 0.02),
        features=["chart_created", "dashboard_shared"],
    ),
    "stalled": dict(           # signs recently, installs the SDK, but only one person uses it
        weight=0.02, lifecycle="new", single_user=True,
        activity=(0.4, 0.3), util=(0.08, 0.05), user_growth=1.0, errors=(0.05, 0.05),
        features=["chart_created"],
    ),
    "never_onboarded": dict(   # contract signed, no events at all
        weight=0.02, lifecycle="new", silent=True,
    ),
    "handoff_gap": dict(       # active in the product, but missing from the CRM
        weight=0.02, lifecycle="existing", in_crm=False,
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

def story_days(acc):
    """How long this account's current story lasts (STORY_DAYS, the data lab): its own
    length for a declining, slipping or power-user account, ARC_DAYS for the rest."""
    if not acc or acc["id"] in STORY_ACCOUNTS or acc["personality"] not in ("declining", "slipping", "power_user"):
        return ARC_DAYS
    lo, hi = STORY_DAYS
    return lo + zlib.crc32(("story" + acc["id"]).encode()) % (hi - lo + 1)


def arc(day_index, acc=None):
    """How far into the current story (0..1): 0 until its last days, then a straight line."""
    n = story_days(acc)
    return min(1.0, max(0, day_index - (DAYS - n)) / (n - 1))


def lerp(pair, day_index, acc=None):
    """The personality's value on a day: its start value until the current story
    begins, then a straight line to its end value on the last day."""
    start, end = pair
    return start + (end - start) * arc(day_index, acc)


def persona(acc):
    """The account's personality, with a recovered account's own story on top (RECOVERY)."""
    p = PERSONALITIES[acc["personality"]]
    return {**p, "util": RECOVERY["util"], "activity": RECOVERY["activity"]} if acc.get("planted") == RECOVERY["name"] else p


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
        # The current term started 99 to 359 days ago, so the renewal is 6 to 266 days ahead
        # (the same spread as before the data grew to a year)
        return END_DATE - timedelta(days=ARC_DAYS - 1) - timedelta(days=random.randint(10, 270))
    # Silent and stalled accounts are recent: older ones would have been
    # rescued or lost by now, so they stay inside the activation window.
    if personality == "never_onboarded":
        return END_DATE - timedelta(days=random.randint(12, 40))
    if personality == "stalled":
        return END_DATE - timedelta(days=random.randint(16, 40))
    # Healthy new customers sign all through the window, like a real sales
    # pipeline, so onboarding shows up every week (not just in the last six).
    return END_DATE - timedelta(days=random.randint(3, ARC_DAYS - 2))  # new_customer


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
    for owner_id, _, kind, region, _, _ in CS_TEAM:
        if kind in ("csm", "pool"):
            (csms_by_region if kind == "csm" else pools_by_region).setdefault(region, []).append(owner_id)

    accounts = []
    n = 0
    for tier, regions in regions_by_tier.items():
        for i, region in enumerate(regions):
            n += 1
            personality = pick_personality(tier)
            personality = STORY_ACCOUNTS.get(f"acc_{n:03d}", personality)   # the handover stories' accounts
            p = PERSONALITIES[personality]
            lo, hi = USERS_BY_TIER[tier]
            users_start = 1 if p.get("single_user") else random.randint(lo, hi)
            acc = dict(
                id=f"acc_{n:03d}",
                name=names[n - 1],
                tier=tier,
                region=region,
                personality=personality,
                contract_start=contract_start_for(personality),     # (a story account's is set just below)
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
            if acc["id"] in STORY_RENEWALS:       # the renewal date the story's emails and calls talk about
                acc["contract_start"] = STORY_RENEWALS[acc["id"]] - timedelta(days=365)
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

    # Each Scale account gets a named owner from its pool, in turn (no random
    # draws, so nothing else in the data moves). Tasks still go to the pool.
    members = {}
    for owner_id, _, kind, _, _, pool in CS_TEAM:
        if kind == "member":
            members.setdefault(pool, []).append(owner_id)
    turn = {}
    for acc in accounts:
        acc["member"] = ""
        if acc["pool"] in members:
            i = turn[acc["pool"]] = turn.get(acc["pool"], -1) + 1
            acc["member"] = members[acc["pool"]][i % len(members[acc["pool"]])]

    # A few new Enterprise accounts were never handed to a CSM (a handoff miss)
    new_enterprise = [a for a in accounts if a["tier"] == "Enterprise"
                      and PERSONALITIES[a["personality"]]["lifecycle"] == "new" and a["in_crm"]]
    for acc in new_enterprise[:2]:
        acc["csm"] = ""
    set_customer_since(accounts)
    plant_cohort_trend(accounts)
    set_qbr_dates(accounts)
    set_champions(accounts)
    set_welcome_group(accounts)
    # Each account's own contract size, rounded like a real order form
    size_rng = random.Random(SEED + 10)
    for acc in accounts:
        acc["contract"] = round(CONTRACT_SIZE[acc["tier"]] * 2 ** size_rng.uniform(-1, 1), -6)
    plan_episodes(accounts, names)
    plant_recoveries(accounts)
    set_csm_since(accounts)
    set_renewal_changes(accounts)
    plant_data_issues(accounts)
    plant_growth_hooks(accounts)
    return accounts


def renewed_in_year(acc):
    """An account that stayed and renewed during the data window (its current term began then)."""
    return (acc["in_crm"] and not acc["lost"] and not acc["first_year"]
            and START_DATE < acc["contract_start"] <= END_DATE and acc["since"] < acc["contract_start"])


def set_renewal_changes(accounts):
    """What each in-year renewal did to the ARR: renewed flat, expanded or downsized.
    Today's ARR is the new term's; arr_before is the old one. Own random generator."""
    rng = random.Random(SEED + 14)
    for acc in accounts:
        acc["renewal_change"] = None
        if not renewed_in_year(acc):
            continue
        odds = RENEWAL_CHANGE_ODDS["had_a_dip" if acc["dip"] else acc["tier"]]
        outcome = rng.choices(("renewed", "expanded", "downsized"), weights=odds)[0]
        arr = arr_of(acc)
        before = (arr if outcome == "renewed" else
                  arr / rng.uniform(*EXPAND_BY) if outcome == "expanded" else arr / rng.uniform(*DOWNSIZE_TO))
        if acc["id"] in HEROES:                       # the story's renewal, after the same draws
            outcome, grew = HEROES[acc["id"]]
            before = arr / grew
        acc["renewal_change"] = (outcome, int(round(before, -2)))


def set_csm_since(accounts):
    """When the account's CSM took it (CRM column csm_since: a real CRM keeps the
    owner field's history). A new Enterprise logo waits a few days for its CSM, and
    now and then a few weeks (a slow handoff): the gap the leader's "no CSM" trend
    shows. Everyone else has had theirs since they became a customer. Own random
    generator, so nothing else moves."""
    rng = random.Random(SEED + 13)
    for acc in accounts:
        acc["csm_since"] = None
        if not acc["csm"]:
            continue
        new = PERSONALITIES[acc["personality"]]["lifecycle"] == "new" or acc.get("first_year")
        if new:
            lag = rng.randint(*SLOW_HANDOFF_DAYS) if rng.random() < SLOW_HANDOFF_SHARE else rng.randint(1, 6)
            # the CRM already names the CSM, so they came by today at the latest
            acc["csm_since"] = min(END_DATE, acc["contract_start"] + timedelta(days=lag))
        else:
            acc["csm_since"] = acc["since"]


def set_customer_since(accounts):
    """When each account first signed. An existing account whose current term
    started during the year is, half the time, a new logo that signed then (it
    onboards and ramps up like a new customer); otherwise it renewed that day and
    has been a customer for one to three years. Own random generator."""
    rng = random.Random(SEED + 11)
    for acc in accounts:
        acc["lost"], acc["ends"], acc["dip"] = False, None, None
        start = acc["contract_start"]
        new = PERSONALITIES[acc["personality"]]["lifecycle"] == "new"
        acc["first_year"] = (not new and start > START_DATE + timedelta(days=7)
                             and rng.random() < FIRST_YEAR_SHARE)
        acc["since"] = start if (new or acc["first_year"]) else start - timedelta(days=365 * rng.randint(1, 3))


def plant_cohort_trend(accounts):
    """PLANTED_TREND: some healthy new logos of the recent cohort become shelfware."""
    lo, hi = PLANTED_TREND["signed_days_ago"]
    for acc in accounts:
        acc["planted"] = ""
        days = (END_DATE - acc["since"]).days
        if (acc["first_year"] and acc["personality"] == "healthy" and acc["id"] not in STORY_ACCOUNTS
                and lo <= days <= hi and zlib.crc32(acc["id"].encode()) % 100 < PLANTED_TREND["share"] * 100):
            acc["personality"], acc["planted"] = "shelfware", PLANTED_TREND["name"]
            acc["users_end"] = acc["users_start"]             # shelfware's users don't grow


def plant_data_issues(accounts):
    """DATA_ISSUES: one gap each for a few settled accounts (not new, not in a dip or a story)."""
    for acc in accounts:
        acc["data_issue"] = ""
    for issue, tier, kind, region in DATA_ISSUES:
        pool = [a for a in accounts if a["in_crm"] and not a.get("lost") and not a.get("dip") and not a["planted"]
                and not a["first_year"] and not a["data_issue"] and a["tier"] == tier and a["personality"] == kind
                and (region is None or a["region"] == region)
                and a["id"] not in STORY_ACCOUNTS and a["id"] not in DATA_ISSUE_SPARED]
        if pool:
            min(pool, key=lambda a: zlib.crc32(("data" + a["id"]).encode()))["data_issue"] = issue


def plant_growth_hooks(accounts):
    """GROWTH_HOOKS: one hook each for a few settled accounts, after the data gaps (never both),
    and the add-on as the hook needs it (set after the draw: no random draw moves)."""
    for acc in accounts:
        acc["growth_hook"] = acc["hook_expected"] = ""
    for hook, tier, kind, expected in GROWTH_HOOKS:
        pool = [a for a in accounts if a["in_crm"] and not a.get("lost") and not a.get("dip") and not a["planted"]
                and not a["first_year"] and not a["data_issue"] and not a["growth_hook"] and a["tier"] == tier
                and a["personality"] == kind and a["id"] not in STORY_ACCOUNTS and a["id"] not in DATA_ISSUE_SPARED]
        if pool:
            acc = min(pool, key=lambda a: zlib.crc32(("hook" + a["id"]).encode()))
            acc["growth_hook"], acc["hook_expected"] = hook, expected
            acc["add_ons"] = ADDON if hook == "unused_addon" else ("" if hook == "addon" else acc["add_ons"])


def coin(e, salt=""):
    """A number from 0 to 99 fixed by the event's own ID: a choice that moves no random draw."""
    return zlib.crc32((salt + e["event_id"]).encode()) % 100


def feature_mix(acc, rows, day):
    """Product events follow what was bought, and a hook shows in the last HOOK_DAYS (applied
    after the draw, so no other account moves). Experiments need the add-on; holders use it on
    about one action in eight, except unused_addon. addon: cohorts take over (a team asking why
    users behave differently). people: dashboards go to many people. Every share says how many."""
    recent = (END_DATE - day).days < HOOK_DAYS
    hook, has = acc.get("growth_hook"), ADDON in (acc.get("add_ons") or "")
    for e in rows:
        if e["event_type"] != "product":
            continue
        name = e["event_name"]
        if name == "experiment_launched" and (not has or hook == "unused_addon"):
            name = "chart_created"
        elif name == "chart_created" and has and hook != "unused_addon" and coin(e, "x") < 12:
            name = "experiment_launched"
        if recent and hook == "addon" and name in ("chart_created", "dashboard_shared") and coin(e, "c") < 60:
            name = "cohort_created"
        if recent and hook == "people" and name == "chart_created" and coin(e, "p") < 35:
            name = "dashboard_shared"
        e["event_name"] = name
        if name == "dashboard_shared":         # how many people the dashboard went to
            e["properties"] = {**e["properties"],
                               # people hook: to well over twice the team that logs in
                               "viewers": 2 * acc["users_end"] + 4 + coin(e, "v") % 10 if recent and hook == "people"
                               else 1 + coin(e, "v") % 4}
    return rows


def crm_cell(acc, column, value):
    """What the CRM holds: the true value, unless a planted gap blanks or garbles it."""
    issue = acc.get("data_issue")
    if issue == "no_contract" and column == "contracted_events":
        return ""                                      # never filled in
    if issue == "bad_renewal" and column == "renewal_date":
        return date.fromisoformat(value).strftime("%d/%m/%Y")   # typed the European way
    if issue == "no_pool" and column == "cs_pool":
        return ""                                      # the pool was never set
    return value


def plant_recoveries(accounts):
    """RECOVERY: some healthy Enterprise and Scale accounts recover over the last 90 days."""
    for acc in accounts:
        if (acc["personality"] == "healthy" and acc["tier"] != "Self-service" and acc["in_crm"]
                and not acc.get("lost") and not acc.get("dip") and not acc["planted"] and not acc["first_year"]
                and acc["id"] not in STORY_ACCOUNTS
                and zlib.crc32(("recover" + acc["id"]).encode()) % 100 < RECOVERY["share"] * 100):
            acc["planted"] = RECOVERY["name"]


def dip_level(dip, i):
    """Share of normal usage on day i of a dip: falls to DIP_BOTTOM over DIP_DAYS,
    then (if it recovers) climbs back over RECOVER_DAYS from wherever it was."""
    if not dip or i < dip["start"]:
        return 1.0
    fall = lambda j: 1 - (1 - DIP_BOTTOM) * min(1.0, (j - dip["start"]) / DIP_DAYS)
    rs = dip.get("recover_from")
    if rs is None or i < rs:
        return fall(i)
    low = fall(rs)
    return low + (1 - low) * min(1.0, (i - rs) / RECOVER_DAYS)


def onboard_day_of(acc):
    """The SDK goes in 2 days after signing, or 5 for the control group (no email)."""
    return acc["since"] + timedelta(days=SDK_DELAY_DAYS["control" if acc["control"] else "welcomed"])


def util_plan(acc, day_index):
    """Share of the contract the account sends on a day, before daily noise: the
    personality's story, a new account's ramp-up and any dip during the year."""
    p = persona(acc)
    day = START_DATE + timedelta(days=day_index)
    ramp_days = p.get("ramp_days") or (30 if acc.get("first_year") else None)
    if ramp_days:
        # new customers ramp up: more data sources and teams come on over the first month
        ramp = min(1.0, max(0, (day - onboard_day_of(acc)).days) / ramp_days)
        lo, hi = (p["util"] if p.get("ramp_days") else (0.2 * p["util"][0], lerp(p["util"], day_index, acc)))
        planned = lo + (hi - lo) * ramp
    else:
        planned = lerp(p["util"], day_index, acc)
    return min(1.2, planned * acc["util_factor"] * dip_level(acc.get("dip"), day_index))   # cap at 120% (overage)


def plan_episodes(accounts, names):
    """The year's dips (see EPISODES). For each, the owner's style decides whether
    and when they call; the call decides the odds of recovery. Recovered: the dip
    goes on one of today's healthy accounts. Not recovered: a lost account, the
    same tier, owner and region, that churns at its renewal and leaves the book.
    Own random generator, so the rest of the data doesn't move."""
    rng = random.Random(SEED + 12)
    hosts = {t: [a for a in accounts if a["tier"] == t and a["personality"] in ("healthy", "power_user")
                 and a["in_crm"] and a["since"] < START_DATE] for t in TIER_COUNTS}
    for t in hosts:
        rng.shuffle(hosts[t])
    next_id, used_names = len(accounts), {a["name"] for a in accounts}
    spare_names = [n for n in names if n not in used_names]
    for _ in range(EPISODES):
        tier = rng.choices(list(EPISODE_TIER_WEIGHTS), weights=list(EPISODE_TIER_WEIGHTS.values()))[0]
        if not hosts[tier]:
            continue
        host = hosts[tier].pop()
        owner = host["csm"] or host["pool"]
        style = OWNER_STYLE["pool" if host["pool"] else host["csm"]] if owner else None
        start = rng.randint(15, EPISODE_LAST_START)   # early enough to recover before today's story begins
        probe = dict(host, dip=dict(start=start))
        seen = visible_day(probe, below=0.40)
        if not seen:
            continue
        called = bool(style) and rng.random() < style["acts"]
        call_day = nearest_weekday(seen + timedelta(days=rng.randint(*style["act_days"]))) if called else None
        in_time = called and (call_day - seen).days <= reach_days(tier)
        recovered = rng.random() < RECOVERY_ODDS["called_in_time" if in_time else "not_in_time"]
        bottom = start + DIP_DAYS
        if recovered:
            rs = ((call_day - START_DATE).days + 7) if called else bottom + 20
            host["dip"] = dict(start=start, recover_from=rs, seen=seen, call_day=call_day, in_time=in_time, outcome="recovered")
            continue
        # not recovered: a lost account like the host, churning at its renewal after the dip
        next_id += 1
        churn = START_DATE + timedelta(days=min(DAYS - 20, bottom + rng.randint(30, 90)))
        lost = dict(host, id=f"acc_{next_id:03d}", name=spare_names.pop() if spare_names else f"Former {next_id}",
                    lost=True, ends=churn, first_year=False, last_qbr="", next_qbr="",
                    contract_start=churn - timedelta(days=365), since=churn - timedelta(days=365 * rng.randint(1, 3)),
                    dip=dict(start=start, recover_from=None, seen=seen, call_day=call_day, in_time=in_time, outcome="lost"))
        accounts.append(lost)


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
        new = PERSONALITIES[acc["personality"]]["lifecycle"] == "new" or acc["first_year"]
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
        writer.writerow(["owner_id", "owner_name", "owner_type", "region", "manager", "pool"])
        writer.writerows(CS_TEAM)
    return path


def write_crm(accounts):
    path = OUT_DIR / "crm_accounts.csv"
    columns = ["account_id", "account_name", "tier", "region", "contract_start", "renewal_date",
               "contracted_events", "add_ons", "csm_owner", "cs_pool", "pool_owner", "last_qbr_date",
               "next_qbr_date", "arr", "status", "customer_since", "csm_since"]
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for acc in accounts:
            if not acc["in_crm"]:
                continue
            start = acc["contract_start"]
            row = {
                "account_id": acc["id"],
                "account_name": acc["name"],
                "tier": acc["tier"],
                "region": acc["region"],
                "contract_start": start.isoformat(),
                "renewal_date": (acc["ends"] or start + timedelta(days=365)).isoformat(),
                "contracted_events": int(acc["contract"]),
                "add_ons": acc["add_ons"],
                "csm_owner": acc["csm"],
                "cs_pool": acc["pool"],
                "pool_owner": acc["member"],
                "last_qbr_date": acc["last_qbr"].isoformat() if acc["last_qbr"] else "",
                "next_qbr_date": acc["next_qbr"].isoformat() if acc["next_qbr"] else "",
                "arr": arr_of(acc),
                "status": "churned" if acc["lost"] else "active",   # churned: left at its renewal
                "customer_since": acc["since"].isoformat(),         # first signed (renewals keep it)
                "csm_since": acc["csm_since"].isoformat() if acc["csm_since"] else "",
            }
            writer.writerow({k: crm_cell(acc, k, v) for k, v in row.items()})   # planted gaps
    return path


def events_for_day(acc, day_index, day, pool):
    """All events one account produces on one day."""
    p = persona(acc)
    if p.get("silent"):
        return []                                   # never onboarded: silence

    if acc["ends"] and day >= acc["ends"]:
        return []                                   # churned at its renewal: gone from the book
    onboard_day = onboard_day_of(acc)               # 2 days after signing, 5 for the control group
    if day < onboard_day:
        return []

    out = []
    if day == onboard_day:
        out.append(event("sdk_installed", "product", day, acc["id"], pool[0]))

    # New accounts start with one user and grow to the full team over two weeks;
    # existing accounts grow (or not) across the 90 days
    days_live = (day - onboard_day).days
    if p["lifecycle"] == "new":
        target = min(acc["users_end"], 1 + round((acc["users_start"] - 1) * days_live / 10))
    else:
        target = round(acc["users_start"] + (acc["users_end"] - acc["users_start"]) * arc(day_index, acc))
        if acc["first_year"]:                       # a new logo's team joins over its first ten days
            target = min(target, 1 + round((acc["users_start"] - 1) * days_live / 10))
    invited_today = set()
    while len(pool) < target:
        new_user = f"{acc['id']}_usr_{len(pool) + 1:02d}"
        invited_today.add(new_user)
        out.append(event("user_invited", "product", day, acc["id"], random.choice(pool),
                         {"invited_user": new_user}))
        pool.append(new_user)

    # Product activity: each user is active with today's probability
    # (a dip thins the team out faster than the volume: people stop logging in first)
    activity = lerp(p["activity"], day_index, acc) * dip_level(acc["dip"], day_index) ** 2
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
    # Before an in-year renewal that changed the ARR, they were on the term before it, and used
    # it at the same rate (24 Nov 2026: usage had jumped or sagged at every such renewal, and the
    # engine now reads each day against the contract in force: cairnpath_db's contract_terms).
    # Scaled after the draw below, so no random draw moves
    contract = acc["contract"]
    if acc.get("renewal_change") and day < acc["contract_start"]:
        contract = contract * acc["renewal_change"][1] / max(1, arr_of(acc))
    daily_contract = contract / 365
    utilisation = util_plan(acc, day_index)
    volume = int(daily_contract * utilisation * random.uniform(0.85, 1.15))
    out.append(event("daily_event_volume", "system", day, acc["id"],
                     properties={"event_count": volume}))

    # System: ingestion errors
    if random.random() < lerp(p["errors"], day_index, acc):
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


# What the CSM logged with a meeting (18 Nov 2026, for Last in My work): an outcome from the
# Slack form's list and, for most, a short note. Taken from the meeting's own ID, never from
# the random draws, so every other number stays exactly as it was. No rule reads them.
AT_RISK = {"declining", "slipping", "shelfware", "stalled", "never_onboarded"}
MEETING_NOTES = {   # (kind, outcome): what they said, then what's next; 3 x 3 = 9 notes each
    ("call", "reached_at_risk"): (
        ["Usage is down since their reorg.", "Sponsor less engaged than last quarter.",
         "They question the value at today's volume."],
        ["Agreed a usage review in two weeks.", "Sending a short adoption plan.",
         "Want a plan before the renewal talks."]),
    ("call", "reached_fine"): (
        ["All good: the dip was a holiday period.", "No concerns raised.", "Healthy, main contact happy."],
        ["Asked about the experiments add-on.", "Follow-up booked next month.",
         "Dashboards now shared with their sales team."]),
    ("qbr", "reached_at_risk"): (
        ["QBR: renewal volume will be questioned.", "QBR: budget pressure from finance.",
         "QBR: champion moving roles."],
        ["Two teams barely use it.", "They want the business outcome in numbers.",
         "Need a new sponsor before the renewal."]),
    ("qbr", "reached_fine"): (
        ["QBR: on track.", "QBR: strong adoption in product.", "QBR: positive."],
        ["Goals for next quarter agreed.", "Analytics team wants more people trained.",
         "Might add a second business unit next year."]),
}


# Promises made in those meetings (23 Nov 2026, commitments step 2): some "what's next" lines
# are a promise our side made, logged on the Slack call form as a commitment with a due date.
# From the meeting's own ID (no random draw); in the last PROMISE_DAYS only, so the cockpit
# shows promises of every kind: kept before the date, dropped, still open, and late.
PROMISES = {                     # the note's "what's next" -> (what we promised, days to deliver)
    "Agreed a usage review in two weeks.": ("Run the usage review with their team", 14),
    "Sending a short adoption plan.": ("Send the adoption plan", 7),
    "Want a plan before the renewal talks.": ("Send a renewal plan before the talks", 14),
    "Asked about the experiments add-on.": ("Send the experiments add-on overview", 7),
    "Analytics team wants more people trained.": ("Book a training for the analytics team", 21),
    "Need a new sponsor before the renewal.": ("Introduce them to an executive sponsor", 21),
}
PROMISE_DAYS = 75


def promise_events(meeting):
    """The commitment a meeting's note promised, and how it ended so far: kept (about half,
    before or near the date), dropped (1 in 10), or still open (late once its date has passed)."""
    note = meeting["properties"].get("note") or ""
    hit = next((v for k, v in PROMISES.items() if note.endswith(k)), None)
    when = datetime.strptime(meeting["timestamp"], "%Y-%m-%dT%H:%M:%SZ")
    if not hit or (END_DATE - when.date()).days > PROMISE_DAYS:
        return []
    what, days = hit
    h = int(meeting["event_id"].replace("-", ""), 16)
    made = when + timedelta(minutes=5)
    key = str(uuid.uuid5(uuid.NAMESPACE_URL, "promise-" + meeting["event_id"]))
    owner = meeting["properties"]["owner_id"]
    due = (when.date() + timedelta(days=days)).isoformat()
    out = [{"event_id": key, "event_name": "commitment_made", "event_type": "engagement",
            "timestamp": made.strftime("%Y-%m-%dT%H:%M:%SZ"), "account_id": meeting["account_id"], "user_id": None,
            "properties": {"owner_id": owner, "note": what, "due": due, "outcome": "meeting"},
            "context": {"source": "cs_history"}}]
    fate, lag = (h >> 8) % 10, days - 3 + (h >> 16) % 8          # kept 3 days early to 4 late
    closed = when + timedelta(days=lag, hours=2)
    if fate < 5 and closed.date() <= END_DATE:
        result = "kept"
    elif fate == 5 and closed.date() <= END_DATE:
        result = "dropped"
    else:
        return out                                                # still open
    out.append({"event_id": str(uuid.uuid5(uuid.NAMESPACE_URL, "closed-" + key)), "event_name": "commitment_closed",
                "event_type": "engagement", "timestamp": closed.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "account_id": meeting["account_id"], "user_id": None,
                "properties": {"owner_id": owner, "topic": key, "status": result}, "context": {"source": "cs_history"}})
    return out


def meeting_log(event, personality, topic):
    """The outcome and note for a generated meeting, from its own ID (no random draw)."""
    h = int(event["event_id"].replace("-", ""), 16)
    if h % 10 >= 8:                                    # 2 in 10: nothing logged in Slack
        return None, None
    risk = personality in AT_RISK
    if (h // 10) % 4 == 0:                             # 1 in 4 goes the other way: real calls surprise
        risk = not risk
    outcome = "reached_at_risk" if risk else "reached_fine"
    kind = "qbr" if topic == "qbr" else "call"
    said, next_ = MEETING_NOTES[(kind, outcome)]
    note = f"{said[(h // 40) % 3]} {next_[(h // 120) % 3]}" if (h // 360) % 4 != 0 else None   # 3 in 4 with a note
    return outcome, note


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


def visible_day(acc, below=None, above=None, story_only=False):
    """The day an account's problem (or growth) shows in its numbers: when its
    30-day utilisation would cross the line the flags use. Owners react to
    this, so "time to action" in the reports measures something real."""
    # story_only: the personality's own story (declining, power user...) starts with the
    # last ARC_DAYS; an account that sat above 80% all year didn't become a growth case in November
    for i in range(DAYS - story_days(acc) if story_only else 0, DAYS):
        util = util_plan(acc, i)
        if i < (onboard_day_of(acc) - START_DATE).days + 30:
            continue                              # a ramp-up isn't a problem: 30 days live first
        if (below is not None and util < below) or (above is not None and util >= above):
            return START_DATE + timedelta(days=i + TRAILING_LAG_DAYS)
    return None                                   # never crosses inside the window


def engagement_for_account(rng, acc):
    p = PERSONALITIES[acc["personality"]]
    region, start, tier = acc["region"], acc["contract_start"], acc["tier"]
    owner = acc["csm"] or acc["member"] or acc["pool"]     # meetings: the named owner
    style = OWNER_STYLE["pool" if acc["pool"] else acc["csm"]] if owner else None
    host = acc["pool"] or owner                            # workshops: the pool runs them
    contact = f"{acc['id']}_usr_01"
    out = []

    def follow_ups(after, until=END_DATE):
        """Owners who follow up keep meeting every few weeks; others don't."""
        if not style["follow_up_days"]:
            return
        day = after + timedelta(days=style["follow_up_days"] + rng.randint(-3, 3))
        while day <= until:
            meeting(day, "follow_up")
            day += timedelta(days=style["follow_up_days"] + rng.randint(-3, 3))

    def meeting(day, topic):
        day = nearest_weekday(day)
        e = cs_event(rng, "meeting_held", local_time(rng, day, region, rng.choice([10, 11, 14, 15])),
                     acc, owner, topic, contact)
        outcome, note = meeting_log(e, acc["personality"], topic)
        if outcome:                                    # only what a CSM would have logged
            e["properties"]["outcome"] = outcome
            if note:
                e["properties"]["note"] = note
        out.append(e)
        out.extend(promise_events(e))                  # a promise made in it, if the note says so

    # Getting started email, the day after signing (control group: none)
    if acc["welcomed"]:
        sent = local_time(rng, start + timedelta(days=1 if start.weekday() < 4 else 7 - start.weekday()),
                          region, 9)                       # next working day
        out.append(cs_event(rng, "email_sent", sent, acc, "automated", "getting_started"))
        if rng.random() < OPEN_RATE.get(acc["personality"], OPEN_RATE["new_customer"]):
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
    is_new = p["lifecycle"] == "new" or acc["first_year"]
    if tier == "Scale" and is_new and acc["welcomed"]:
        first = next(workshop_days(region, start + timedelta(days=4)), None)
        if first and rng.random() < ONBOARDING_WORKSHOP_RATE.get(acc["personality"], ONBOARDING_WORKSHOP_RATE["new_customer"]):
            for n in range(1, min(3, acc["users_end"]) + 1):
                out.append(cs_event(rng, "workshop_attended", local_time(rng, first, region, 14),
                                    acc, host, "onboarding_workshop", f"{acc['id']}_usr_{n:02d}"))
    if tier == "Scale" and acc["personality"] in ADVANCED_WORKSHOP_RATE:
        for day in workshop_days(region, START_DATE):
            rate = ADVANCED_WORKSHOP_RATE[acc["personality"]]
            if acc["personality"] == "declining":
                rate *= 1 - arc((day - START_DATE).days, acc)  # interest fades as usage falls
            if rng.random() < rate:
                for n in range(1, rng.randint(1, 2) + 1):
                    out.append(cs_event(rng, "workshop_attended", local_time(rng, day, region, 14),
                                        acc, host, "advanced_workshop", f"{acc['id']}_usr_{n:02d}"))

    if not owner:            # Self-service: no human coverage, so no meetings
        return in_window(out, acc)

    # Enterprise: QBRs on the CRM dates (and every quarter before), a kickoff for new
    # accounts, regular check-ins
    if tier == "Enterprise":
        first_qbr = max(START_DATE, acc["since"] + timedelta(days=60))
        if acc["last_qbr"]:
            day = acc["last_qbr"]
            while day >= first_qbr:
                meeting(day, "qbr")
                day -= timedelta(days=91 + rng.randint(-7, 7))
        elif acc["lost"]:                          # a lost account's QBRs, while it was still a customer
            day = first_qbr + timedelta(days=rng.randint(0, 80))
            while day < acc["ends"]:
                meeting(day, "qbr")
                day += timedelta(days=91 + rng.randint(-7, 7))
        if is_new:
            kickoff = start + timedelta(days=rng.randint(*style["act_days"]))
            meeting(max(kickoff, acc["csm_since"] + timedelta(days=2)), "kickoff")   # once the CSM has it
        elif acc["personality"] in ("healthy", "power_user", "declining", "shelfware", "slipping"):
            day = START_DATE + timedelta(days=rng.randint(0, style["check_in_days"]))
            while day <= END_DATE:
                near_qbr = acc["last_qbr"] and abs((day - acc["last_qbr"]).days) < 10
                if not near_qbr:
                    meeting(day, "check_in")
                day += timedelta(days=style["check_in_days"] + rng.randint(-5, 5))

    # A dip during the year: the call (if any) was decided with the episode, since it
    # changed the odds of recovery; follow-ups stop once the account recovers or leaves
    dip = acc["dip"]
    if dip and dip.get("call_day"):
        meeting(dip["call_day"], "churn_call")
        stop = acc["ends"] or (START_DATE + timedelta(days=dip["recover_from"] + RECOVER_DAYS) if dip.get("recover_from") else END_DATE)
        follow_ups(dip["call_day"], min(END_DATE, stop))

    # Declining accounts: a churn call once it shows, if the owner gets to it
    seen = visible_day(acc, below=0.40, story_only=True) if acc["personality"] == "declining" else None
    if seen and rng.random() < style["acts"]:
        first = seen + timedelta(days=rng.randint(*style["act_days"]))
        meeting(first, "churn_call")
        follow_ups(first)
    # Growing accounts: a growth call once the growth shows, if the owner gets to it
    seen = visible_day(acc, above=0.80, story_only=True) if acc["personality"] == "power_user" else None
    if seen and rng.random() < style["acts"]:
        first = seen + timedelta(days=rng.randint(*style["act_days"]))
        meeting(first, "growth_call")
        follow_ups(first)
    # Stalled Scale accounts: the pool calls once the 14-day window has passed
    if tier == "Scale" and acc["personality"] == "stalled" and rng.random() < style["acts"]:
        meeting(start + timedelta(days=14 + rng.randint(*style["act_days"])), "activation_call")

    return in_window(out, acc)


def in_window(rows, acc=None):
    """Keep only what falls inside the data window (a churn call planned for
    next week hasn't happened yet), and nothing after a lost account left."""
    last = (acc["ends"] - timedelta(days=1)) if acc and acc["ends"] else END_DATE
    return [e for e in rows if START_DATE.isoformat() <= e["timestamp"][:10] <= last.isoformat()]


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
        dip = acc["dip"]
        if dip:                            # a dip during the year: a churn task when it showed
            sent = nearest_weekday(dip["seen"] + timedelta(days=1))
            log(sent, acc, "churn", "Act", owner, "queue", TASK_HISTORY["declining"][3], "posted to Slack (history)")
            if acc["pool"] and rng.random() < HISTORY_CLAIM_RATE:
                when = local_time(rng, nearest_weekday(sent + timedelta(days=rng.randint(1, 3))), acc["region"], 10)
                claims.append({"event_id": str(uuid.UUID(int=rng.getrandbits(128), version=4)),
                               "event_name": "task_claimed", "event_type": "engagement",
                               "timestamp": when.strftime("%Y-%m-%dT%H:%M:%SZ"), "account_id": acc["id"],
                               "user_id": None, "properties": {"owner_id": owner, "topic": "churn"},
                               "context": {"source": "slack"}})
        if acc["lost"]:
            continue                       # gone from the book: no later tasks
        if kind in TASK_HISTORY:
            flag, status, line, action = TASK_HISTORY[kind]
            seen = visible_day(acc, **line, story_only=True)
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
# Why a renewal was lost, as a CRM's closed-lost reason would say (the data lab, 22 Nov 2026).
# Picked by a hash of the account ID among the reasons its story makes likely, so no random draw
# moves. "Acquired" and a budget cut can hit a healthy account: no usage signal sees those coming.
LOSS_REASONS = {
    "declining": ["moved to a competitor", "champion left", "budget cut"],
    "declining_called": ["moved to a competitor", "champion left", "budget cut"],
    "shelfware": ["never rolled out", "never rolled out", "budget cut"],
    "slipping": ["smaller scope", "smaller scope", "budget cut"],
    "power_user": ["acquired", "budget cut"],
    "healthy": ["acquired", "budget cut", "smaller scope"],
}


def loss_reason(acc, kind):
    """The closed-lost reason for a churned or downsized renewal (LOSS_REASONS)."""
    options = LOSS_REASONS.get(kind, LOSS_REASONS["healthy"])
    return options[zlib.crc32(("reason" + acc["id"]).encode()) % len(options)]


def arr_of(acc):
    """What the account pays a year: contract x the price per 1,000 events."""
    return int(round(acc["contract"] / 1000 * PRICE_PER_1K[acc["tier"]], -2))


def renewal_value(acc, outcome):
    """What the account renews at (no random draws, so the outcomes don't move).
    Churned: nothing. Downsized: cut to about what it uses (at least a quarter).
    Expanded: grows to its usage, at least 10%. Renewed: the same."""
    arr = arr_of(acc)
    p = PERSONALITIES[acc["personality"]]
    use = min(1.2, (p["util"][1] if "util" in p else 0.6) * acc.get("util_factor", 1))
    if outcome == "churned":
        return 0
    if outcome == "downsized":
        return int(round(arr * min(0.9, max(0.25, use)), -2))
    if outcome == "expanded":
        return int(round(arr * max(1.1, use), -2))
    return arr


def write_renewal_outcomes(accounts, called):
    rng = random.Random(SEED + 7)         # own generator: nothing else changes
    path = OUT_DIR / "renewal_outcomes.csv"
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["account_id", "renewal_date", "outcome", "renewal_arr", "reason"])
        for acc in accounts:
            renewal = acc["contract_start"] + timedelta(days=365)
            if not acc["in_crm"] or not (END_DATE < renewal <= END_DATE + timedelta(days=OUTCOME_HORIZON_DAYS)):
                continue
            kind = acc["personality"]
            if kind == "declining" and acc["id"] in called:
                kind = "declining_called"
            odds = OUTCOME_ODDS.get(kind, OUTCOME_ODDS["healthy"])
            outcome = rng.choices(OUTCOMES, weights=odds)[0]
            reason = loss_reason(acc, kind) if outcome in ("churned", "downsized") else ""
            writer.writerow([acc["id"], renewal.isoformat(), outcome, renewal_value(acc, outcome), reason])
    return path


def write_renewal_history(accounts):
    """The renewals inside the data window, as a CRM's closed renewals would show
    them, with the ARR before and after: accounts that renewed (flat, expanded or
    downsized; their current term started that day) and the lost accounts
    (churned at their renewal). For the retention numbers and trends."""
    path = OUT_DIR / "renewal_history.csv"
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["account_id", "renewal_date", "outcome", "renewal_arr", "arr_before"])
        rows = []
        for acc in accounts:
            if not acc["in_crm"]:
                continue
            if acc["lost"]:
                rows.append([acc["id"], acc["ends"].isoformat(), "churned", 0, arr_of(acc)])
            elif acc["renewal_change"]:
                outcome, before = acc["renewal_change"]
                rows.append([acc["id"], acc["contract_start"].isoformat(), outcome, arr_of(acc), before])
        writer.writerows(sorted(rows, key=lambda r: r[1]))
    return path, len(rows)


def arr_at_start(acc):
    """What a customer paid on the first day of the data (None: not a customer yet)."""
    if not acc["in_crm"] or acc["since"] > START_DATE:
        return None
    return acc["renewal_change"][1] if acc.get("renewal_change") else arr_of(acc)


def write_arr_bridge_history(accounts):
    """The year before the data, as finance would report it: a quarterly ARR bridge
    (starting ARR, new logos, expansion, contraction, churn, ending ARR), top line
    only, no accounts. It ends where the data begins: the book's ARR on day one.
    Own random generator."""
    rng = random.Random(SEED + 15)
    end_arr = sum(a for a in map(arr_at_start, accounts) if a)
    py = PRIOR_YEAR
    start_arr = end_arr / (py["nrr"] + py["new_logo_share"])
    totals = {"new_logo_arr": start_arr * py["new_logo_share"],
              "churned_arr": start_arr * (1 - py["grr"]) * 0.75,      # three quarters of the loss is churn...
              "contraction_arr": start_arr * (1 - py["grr"]) * 0.25,  # ...a quarter downsizing
              "expansion_arr": start_arr * (py["nrr"] - py["grr"])}
    shares = {k: [rng.uniform(0.7, 1.3) for _ in range(4)] for k in totals}
    path = OUT_DIR / "arr_bridge_history.csv"
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["period_start", "period_end", "starting_arr", "new_logo_arr", "expansion_arr",
                         "contraction_arr", "churned_arr", "ending_arr"])
        arr = start_arr
        for q in range(4):
            q_start = START_DATE - timedelta(days=365) + timedelta(days=round(q * 365 / 4))
            q_end = START_DATE - timedelta(days=365) + timedelta(days=round((q + 1) * 365 / 4) - 1)
            move = {k: round(totals[k] * shares[k][q] / sum(shares[k]), -2) for k in totals}
            ending = arr + move["new_logo_arr"] + move["expansion_arr"] - move["contraction_arr"] - move["churned_arr"]
            if q == 3:                                    # the bridge lands exactly on day one's ARR
                move["new_logo_arr"] += round(end_arr - ending)
                ending = end_arr
            writer.writerow([q_start.isoformat(), q_end.isoformat(), round(arr), round(move["new_logo_arr"]),
                             round(move["expansion_arr"]), round(move["contraction_arr"]),
                             round(move["churned_arr"]), round(ending)])
            arr = ending
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
        writer.writerow(["account_id", "tier", "personality", "has_champion", "welcome_group", "history", "called_in_time", "planted", "data_issue", "growth_hook", "hook_expected"])
        for acc in accounts:
            group = "welcomed" if acc["welcomed"] else "control" if acc["control"] else ""
            dip = acc["dip"]
            history = ("dip, recovered" if dip and dip["outcome"] == "recovered" else "dip, churned" if dip else
                       "new logo this year" if acc["first_year"] else "")
            writer.writerow([acc["id"], acc["tier"], "churned" if acc["lost"] else acc["personality"],
                             acc["has_champion"], group, history, bool(dip and dip["in_time"]) if dip else "",
                             acc.get("planted", ""), acc.get("data_issue", ""),
                             acc.get("growth_hook", ""), acc.get("hook_expected", "")])
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
            got = events_for_day(acc, day_index, day, pools[acc["id"]])
            if day_index == 0 and in_year_one(acc):
                # its team was already there in year one: drawn as before (nothing else moves),
                # but not written as new invites on day one of today's year
                known = set(year_one_users(acc))
                got = [e for e in got if not (e["event_name"] == "user_invited"
                                              and e["properties"]["invited_user"] in known)]
            got = feature_mix(acc, got, day)            # what was bought, and the growth hooks
            if acc.get("data_issue") == "feed_stopped" and (END_DATE - day).days < FEED_STOP_DAYS:
                # the volume feed stopped (a pipeline problem on their side); the product didn't.
                # Filtered after drawing, so no random draw moves
                got = [e for e in got if e["event_name"] != "daily_event_volume"]
            rows.extend(got)
        rows.sort(key=lambda e: e["timestamp"])
        with (events_dir / f"{day.isoformat()}.jsonl").open("w") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")
        total += len(rows)
    return total


# --- Year one: the year before START_DATE --------------------------------------------
def year_one_users(acc):
    """The users an account had in year one: the team it started today's year with."""
    return [f"{acc['id']}_usr_{i:02d}" for i in range(1, acc["users_start"] + 1)]


def in_year_one(acc):
    """A customer some time in year one, known to the CRM, and not one that never onboarded."""
    return acc["in_crm"] and acc["since"] < START_DATE and not persona(acc).get("silent")


def year_one_events(accounts):
    """Usage in year one: the level each account started today's year at, steady, with daily
    noise; accounts that signed during year one onboard and ramp up like new customers; about
    one in ten has a dip that recovers. Its own random stream."""
    rng = random.Random(SEED + 21)
    plan = {}
    for acc in accounts:
        if not in_year_one(acc):
            continue
        p = persona(acc)
        contract = acc["contract"]
        if acc.get("renewal_change") and acc["contract_start"] >= START_DATE:
            contract = contract * acc["renewal_change"][1] / max(1, arr_of(acc))   # the term before its renewal
        dip = None
        if rng.random() < YEAR_ONE_DIPS["share"]:
            dip = rng.randint(*YEAR_ONE_DIPS["start"])
            if acc["id"] in HEROES:
                dip = None                              # a hero's year one is calm (drawn all the same)
        plan[acc["id"]] = dict(acc=acc, p=p, contract=contract, dip=dip,
                               util=min(1.1, lerp(p["util"], 0, acc) * acc["util_factor"]),
                               activity=lerp(p["activity"], 0, acc), errors=p["errors"][0],
                               users=year_one_users(acc))
    days = {}
    for i in range(YEAR_ONE_DAYS):
        day = HISTORY_START + timedelta(days=i)
        rows = []
        for aid, a in plan.items():
            acc, p = a["acc"], a["p"]
            onboard = onboard_day_of(acc)
            if day < onboard:
                continue
            if day == onboard:
                rows.append(event("sdk_installed", "product", day, aid, a["users"][0]))
            live = (day - onboard).days
            team = a["users"] if onboard < HISTORY_START else a["users"][:min(len(a["users"]), 1 + live * len(a["users"]) // 10)]
            if onboard >= HISTORY_START and 0 < live <= 10 and len(team) > 1 and team[-1] not in a.get("invited", set()):
                a.setdefault("invited", set()).add(team[-1])
                rows.append(event("user_invited", "product", day, aid, team[0], {"invited_user": team[-1]}))
            level = 1.0
            if a["dip"] is not None and i >= a["dip"]:
                d = YEAR_ONE_DIPS
                j = i - a["dip"]
                level = (1 - (1 - d["bottom"]) * min(1, j / d["fall"]) if j <= d["fall"]
                         else d["bottom"] + (1 - d["bottom"]) * min(1, (j - d["fall"]) / d["recover"]))
            act = a["activity"] * level ** 2 * WEEKDAY_ACTIVITY[day.weekday()]
            for n, user in enumerate(team):
                champion = acc["has_champion"] and n == 0
                if rng.random() < (min(0.95, act * 1.8) if champion else min(1.0, act)):
                    for _ in range(rng.randint(5, 12) if champion else rng.randint(1, 4)):
                        rows.append(event(rng.choice(p["features"]), "product", day, aid, user))
            ramp = 1.0 if onboard < HISTORY_START else min(1.0, 0.2 + 0.8 * live / 30)
            volume = int(a["contract"] / 365 * a["util"] * ramp * level * rng.uniform(0.85, 1.15))
            rows.append(event("daily_event_volume", "system", day, aid, properties={"event_count": volume}))
            if rng.random() < a["errors"]:
                for _ in range(rng.randint(1, 3)):
                    rows.append(event("ingestion_error", "system", day, aid, properties={"error_code": rng.choice(
                        ["SCHEMA_MISMATCH", "AUTH_EXPIRED", "PAYLOAD_TOO_LARGE"])}))
        days[day] = rows
    return days, plan


def year_one_owner(acc, day, team):
    """Who owned the account on a day of year one: its owner today, or before the CSM took it
    (csm_since), another CSM of the same region (the CRM's owner history)."""
    owner = acc["csm"] or acc["member"] or acc["pool"]
    if acc["csm"] and acc["csm_since"] and day < acc["csm_since"]:
        others = sorted(t["owner_id"] for t in team if t["owner_type"] == "csm" and t["region"] == acc["region"]
                        and t["owner_id"] != acc["csm"])
        return others[sum(map(ord, acc["id"])) % len(others)] if others else owner
    return owner


def year_one_engagement(accounts, plan):
    """CS history in year one: an Enterprise account's QBR about every quarter, a Scale account's
    check-in about every four months, a kickoff (Enterprise) or onboarding workshop (Scale) after
    signing. No notes or promises: what a CRM keeps from that long ago. Own random stream."""
    rng = random.Random(SEED + 22)
    team = [dict(owner_id=o[0], owner_type=o[2], region=o[3]) for o in CS_TEAM] if isinstance(CS_TEAM[0], tuple) else \
        [dict(owner_id=t["owner_id"], owner_type=t["owner_type"], region=t["region"]) for t in CS_TEAM]
    out = {}
    for aid, a in plan.items():
        acc = a["acc"]
        if acc["tier"] not in YEAR_ONE_MEETINGS:
            continue
        topic, every = YEAR_ONE_MEETINGS[acc["tier"]]
        day = max(HISTORY_START, acc["since"]) + timedelta(days=rng.randint(5, 20))
        if acc["since"] >= HISTORY_START:              # signed in year one: a first meeting to start
            first = "kickoff" if acc["tier"] == "Enterprise" else "onboarding_workshop"
            name = "meeting_held" if first == "kickoff" else "workshop_attended"
            owner = acc["pool"] if name == "workshop_attended" and acc["pool"] else year_one_owner(acc, day, team)
            e = cs_event(rng, name, local_time(rng, nearest_weekday(day), acc["region"], 10), acc, owner, first, a["users"][0])
            out.setdefault(day, []).append(e)
            day += timedelta(days=every)
        while day < START_DATE:
            d = nearest_weekday(day)
            if d < START_DATE:
                e = cs_event(rng, "meeting_held", local_time(rng, d, acc["region"], rng.choice([10, 11, 14, 15])),
                             acc, year_one_owner(acc, d, team), topic, a["users"][0])
                out.setdefault(d, []).append(e)
            day += timedelta(days=every + rng.randint(-10, 10))
    return out


def write_year_one(accounts):
    """Year one's events and CS history, into the same daily files (dates before START_DATE)."""
    days, plan = year_one_events(accounts)
    meetings = year_one_engagement(accounts, plan)
    n = m = 0
    by_id = {a["id"]: a for a in accounts}
    for day, rows in days.items():
        if rows:
            for r in rows:                          # what was bought (no hooks in year one)
                feature_mix(by_id[r["account_id"]], [r], END_DATE - timedelta(days=HOOK_DAYS + 1))
            rows.sort(key=lambda e: e["timestamp"])
            with (OUT_DIR / "events" / f"{day.isoformat()}.jsonl").open("w") as f:
                f.writelines(json.dumps(r) + "\n" for r in rows)
            n += len(rows)
    for day, rows in meetings.items():
        rows.sort(key=lambda e: e["timestamp"])
        path = OUT_DIR / "engagement" / f"{date.fromisoformat(rows[0]['timestamp'][:10]).isoformat()}.jsonl"
        with path.open("a") as f:
            f.writelines(json.dumps(r) + "\n" for r in rows)
        m += len(rows)
    return n, m, len(plan), sum(1 for a in plan.values() if a["dip"] is not None)


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
    history_path, renewals_in_year = write_renewal_history(accounts)
    bridge_path = write_arr_bridge_history(accounts)
    y1_events, y1_touches, y1_accounts, y1_dips = write_year_one(accounts)
    print(f"Accounts:   {len(accounts)} ({sum(a['in_crm'] for a in accounts)} in the CRM)")
    print(f"CS team:    {team_path}")
    print(f"CRM table:  {crm_path}")
    print(f"Answer key: {key_path} (for check_flags.py only)")
    print(f"Holdout:    {holdout_path} (no onboarding emails for these accounts)")
    print(f"Events:     {total:,} across {DAYS} daily files, {START_DATE} to {END_DATE}")
    print(f"Engagement: {cs_total:,} CS touches and responses in data/engagement/")
    print(f"Task history: {tasks} earlier tasks in data/action_log.csv, {claimed} taken in Slack, "
          f"{slipped} QBRs that slipped")
    dips = [a["dip"] for a in accounts if a["dip"]]
    for label, group in (("called in time", [d for d in dips if d["in_time"]]), ("not in time", [d for d in dips if not d["in_time"]])):
        kept = sum(d["outcome"] == "recovered" for d in group)
        print(f"Dips this year, {label}: {len(group)}, recovered {kept} ({round(100 * kept / max(1, len(group)))}%)")
    print(f"New logos this year: {sum(a['first_year'] for a in accounts)}; lost at renewal: {sum(a['lost'] for a in accounts)}")
    print(f"Renewals in the year: {history_path} ({renewals_in_year})")
    cohort = [a for a in accounts if arr_at_start(a)]
    s0 = sum(map(arr_at_start, cohort))
    now = lambda a: 0 if a["lost"] else arr_of(a)
    print(f"This year (accounts that were customers on day one): GRR "
          f"{100 * sum(min(now(a), arr_at_start(a)) for a in cohort) / s0:.1f}%, "
          f"NRR {100 * sum(now(a) for a in cohort) / s0:.1f}% on {s0 / 1e6:.1f}M starting ARR")
    print(f"Year one:   {y1_events:,} events and {y1_touches} CS meetings for {y1_accounts} accounts, "
          f"{HISTORY_START} to {START_DATE - timedelta(days=1)} ({y1_dips} dips that recover)")
    print(f"Year before (top line): {bridge_path}")
    gaps = sorted((a["data_issue"], a["id"]) for a in accounts if a.get("data_issue"))
    print(f"Data gaps:  {len(gaps)} planted (" + ", ".join(f"{a} {i}" for i, a in gaps) + ")")
    hooks = sorted((a["growth_hook"], a["hook_expected"], a["id"]) for a in accounts if a.get("growth_hook"))
    print(f"Growth hooks: {len(hooks)} planted (" + ", ".join(f"{a} {h}{'' if x == 'show' else ' ' + x}" for h, x, a in hooks) + ")")
    print(f"Outcomes:   {outcomes_path} (renewals in the {OUTCOME_HORIZON_DAYS} days after the data, for backtest.py)")
