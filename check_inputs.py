"""
Check a book of business before the engine runs on it: are the inputs there,
in the shape DATA_CONTRACT.md describes, and what will (and won't) work?

First runs on a new company's data fail on the data, not on the logic: a
missing column, dates the wrong way round, accounts that appear in the usage
data but not in the CRM. This script reads every input, says what it found in
plain English, and ends with what the engine can do with it.

    python check_inputs.py              the engine's own folder (data/)
    python check_inputs.py D:\\export    any folder laid out the same way

Problems:  ERROR  a required input is missing or unusable: the run stops here
           WARN   it runs, but some rows or features are affected
           ok     fine

Plain Python, no DuckDB: it has to work on data DuckDB would refuse.
Exit code 1 when there is an ERROR (run_all.ps1 stops), else 0.
"""

import csv
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

from settings import INPUTS, SETTINGS

sys.stdout.reconfigure(encoding="utf-8")

# the folder in [data] inputs (settings.local.toml for your own exports), or one given here
FOLDER = Path(sys.argv[1]) if len(sys.argv) > 1 else INPUTS
FILES = SETTINGS["data"]                  # file names in that folder
COLUMNS = SETTINGS.get("columns", {})     # your column names -> the engine's
TIERS = SETTINGS["tiers"]
CONTRACT = SETTINGS["utilisation"]["contract_column"]
USAGE_EVENT = SETTINGS["utilisation"]["usage_event"]
REGIONS = set(SETTINGS["regions"]["utc_offset"])
CHURN_DAYS = 90                           # churn and expansion need a full 90 days live
OUTCOMES = {"renewed", "expanded", "downsized", "churned"}

errors = warnings = 0
works = []                                # (feature, on?, why) for the summary


def say(level, text):
    global errors, warnings
    errors += level == "ERROR"
    warnings += level == "WARN"
    print(f"  {level:<5}  {text}")


def examples(ids, n=5):
    """'acc_001, acc_007 and 3 more': enough to go and look, not a wall of IDs."""
    ids = sorted(ids)
    return ", ".join(ids[:n]) + (f" and {len(ids) - n} more" if len(ids) > n else "")


def count(n, word):
    """'1 account', '3 accounts'."""
    plural = word + ("es" if word.endswith(("s", "ch")) else "s")
    return f"{n:,} {word if n == 1 else plural}"


def to_date(text):
    try:
        return date.fromisoformat((text or "").strip()[:10])
    except ValueError:
        return None


def read_csv(name, required, label, mapping=None):
    """Read a CSV with a header. None (and an ERROR or a note) if it can't be used.
    mapping: your column names -> the engine's ([columns.*] in settings)."""
    path = FOLDER / name
    if not path.exists():
        return None
    with path.open(encoding="utf-8-sig", newline="") as f:      # -sig: Excel's byte-order mark
        rows = [{(mapping or {}).get(k, k): v for k, v in r.items()} for r in csv.DictReader(f)]
    columns = set(rows[0]) if rows else set()
    missing = [c for c in required if c not in columns]
    if missing:
        say("ERROR", f"{label}: missing column{'s' if len(missing) > 1 else ''} {', '.join(missing)} ({name})")
        return None
    say("ok", f"{label}: {count(len(rows), 'row')} ({name})")
    return rows


print(f"\nChecking the inputs in {FOLDER}\n")

# --- 1. CRM accounts (required) ---------------------------------------------------
print("1. CRM accounts")
crm_cols = ["account_id", "account_name", "tier", "region", "contract_start", "renewal_date",
            CONTRACT, "csm_owner", "cs_pool"]
crm = read_csv(FILES["crm_file"], crm_cols, "CRM", COLUMNS.get("crm"))
if crm is None and not (FOLDER / FILES["crm_file"]).exists():
    say("ERROR", f"CRM: {FILES['crm_file']} not found. Nothing can run without it")
accounts = {}
if crm:
    ids = Counter(r["account_id"] for r in crm)
    if dup := [a for a, n in ids.items() if n > 1]:
        say("ERROR", f"CRM: {count(len(dup), 'account ID')} used more than once: {examples(dup)}")
    accounts = {r["account_id"]: r for r in crm}
    known = {TIERS["tech_touch"], TIERS["pooled"], TIERS["named_csm"]}
    tiers = Counter(r["tier"] for r in crm)
    if odd := {t: n for t, n in tiers.items() if t not in known}:
        say("WARN", "CRM: tier names not in settings.toml [tiers], so they get no routing: "
                    + ", ".join(f"{t!r} ({n})" for t, n in odd.items()))
    print("         tiers: " + ", ".join(f"{t} {n}" for t, n in tiers.most_common()))
    if odd := {r["region"] for r in crm} - REGIONS:
        say("WARN", f"CRM: regions without a time zone in settings.toml [regions] "
                    f"(emails go out on UTC): {', '.join(sorted(odd))}")
    bad_dates = [r["account_id"] for r in crm
                 if not (to_date(r["contract_start"]) and to_date(r["renewal_date"]))]
    if bad_dates:
        say("WARN", f"CRM: {count(len(bad_dates), 'account')} with a missing or unreadable contract or "
                    f"renewal date (use YYYY-MM-DD): {examples(bad_dates)}")
    backwards = [r["account_id"] for r in crm if to_date(r["contract_start"]) and to_date(r["renewal_date"])
                 and to_date(r["renewal_date"]) <= to_date(r["contract_start"])]
    if backwards:
        say("WARN", f"CRM: {count(len(backwards), 'account')} with a renewal on or before the contract start: {examples(backwards)}")
    def usable(value):
        try:
            return float(value) > 0          # "300,000,000" or "3e8 events" don't load
        except (TypeError, ValueError):
            return False
    no_contract = [r["account_id"] for r in crm if not usable(r[CONTRACT])]
    if no_contract:
        say("WARN", f"CRM: {count(len(no_contract), 'account')} without a usable {CONTRACT}, so no utilisation, "
                    f"churn or expansion for them: {examples(no_contract)}")
    arr_col = SETTINGS["money"]["arr_column"]
    if arr_col not in crm[0]:
        say("WARN", f"CRM: no {arr_col} column, so ARR is estimated from {CONTRACT} x the list price "
                    f"in settings.toml [money]")
    elif bad := [r["account_id"] for r in crm if not usable(r[arr_col])]:
        say("WARN", f"CRM: {count(len(bad), 'account')} without a usable {arr_col}: {examples(bad)}")
    no_csm = [r["account_id"] for r in crm if r["tier"] == TIERS["named_csm"] and not r["csm_owner"].strip()]
    if no_csm:
        say("WARN", f"CRM: {count(len(no_csm), TIERS['named_csm'] + ' account')} with no csm_owner "
                    f"(they show as No CSM assigned, for CS ops): {examples(no_csm)}")
    no_pool = [r["account_id"] for r in crm if r["tier"] == TIERS["pooled"] and not r["cs_pool"].strip()]
    if no_pool:
        say("WARN", f"CRM: {count(len(no_pool), TIERS['pooled'] + ' account')} with no cs_pool, so nobody gets "
                    f"their tasks: {examples(no_pool)}")

# --- 2. CS team (required when a tier has people) ---------------------------------
print("\n2. CS team")
team = read_csv(FILES["team_file"], ["owner_id", "owner_name", "owner_type", "region", "manager"], "CS team", COLUMNS.get("team"))
owners = {}
if team:
    owners = {r["owner_id"]: r for r in team}
    if odd := {r["owner_type"] for r in team} - {"csm", "pool", "member"}:
        say("WARN", f"CS team: owner_type must be csm, pool or member, found: {', '.join(sorted(odd))}")
    pools = {r["owner_id"] for r in team if r["owner_type"] == "pool"}
    if lost := [r["owner_id"] for r in team if r["owner_type"] == "member" and r.get("pool") not in pools]:
        say("WARN", f"CS team: pool members with no known pool (pool column): {examples(lost)}")
    if no_mgr := [r["owner_id"] for r in team if not r["manager"].strip()]:
        say("WARN", f"CS team: {count(len(no_mgr), 'owner')} without a manager, so no escalations for them: {examples(no_mgr)}")
    if accounts:
        named = {r["csm_owner"] for r in crm if r["csm_owner"].strip()} | {r["cs_pool"] for r in crm if r["cs_pool"].strip()}
        if unknown := named - set(owners):
            say("WARN", f"CS team: owners named in the CRM but not in the team table "
                        f"(tasks without a name or a manager): {examples(unknown)}")
elif not (FOLDER / FILES["team_file"]).exists():
    say("WARN", f"CS team: {FILES['team_file']} not found. Tasks can't name an owner or a manager")

# --- 3. Product events (required) -------------------------------------------------
print("\n3. Product events")
event_files = sorted(FOLDER.glob(FILES["events_glob"]))
first = last = None
by_name, broken, usage_days, users_seen, event_accounts = Counter(), 0, Counter(), set(), set()
if not event_files:
    say("ERROR", "Events: no .jsonl files in events/. Nothing can run without usage data")
else:
    for path in event_files:
        with path.open(encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    e = json.loads(line)
                    day = to_date(e["timestamp"])
                    account = e["account_id"]
                    name = e["event_name"]
                except (ValueError, KeyError, TypeError):
                    broken += 1
                    continue
                if day is None:
                    broken += 1
                    continue
                first, last = min(first or day, day), max(last or day, day)
                by_name[name] += 1
                event_accounts.add(account)
                if name == USAGE_EVENT:
                    usage_days[account] += 1
                if e.get("user_id"):
                    users_seen.add(account)
    say("ok", f"Events: {sum(by_name.values()):,} in {len(event_files)} files, {first} to {last} "
              f"({(last - first).days + 1} days); the engine's 'today' is {last}")
    if broken:
        say("WARN", f"Events: {count(broken, 'line')} unreadable (bad JSON, or no timestamp, account_id or event_name): skipped")
    print("         " + ", ".join(f"{n} {c:,}" for n, c in by_name.most_common()))
    span = (last - first).days + 1
    if span < CHURN_DAYS:
        say("WARN", f"Events: only {span} days of data. Churn and expansion need {CHURN_DAYS} days live, "
                    f"so they flag nobody yet")
    if not by_name[USAGE_EVENT]:
        say("ERROR", f"Events: no {USAGE_EVENT} events (settings.toml [utilisation]): no utilisation, "
                     f"so no churn, Shelfware or expansion")
    if accounts:
        if gap := event_accounts - set(accounts):
            say("WARN", f"Events: {count(len(gap), 'account')} sending events but aren't in the CRM "
                        f"(they show as Handoff gap, for CS ops): {examples(gap)}")
        quiet = {a for a in accounts if a not in event_accounts}
        if quiet:
            say("ok", f"Events: {len(quiet)} CRM accounts send no events at all (new, or Silent)")

# --- 4. Engagement history (optional) ---------------------------------------------
print("\n4. Engagement history (optional)")
eng_files = sorted(FOLDER.glob(FILES["engagement_glob"]))
eng, no_topic, eng_broken = Counter(), 0, 0
for path in eng_files:
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                e = json.loads(line)
                eng[e["event_name"]] += 1
                no_topic += not (e.get("properties") or {}).get("topic")
            except (ValueError, KeyError, TypeError):
                eng_broken += 1
if not eng_files:
    say("WARN", "Engagement: none found. Flags still work; journeys, time to action and last touch don't")
else:
    say("ok", f"Engagement: {sum(eng.values()):,} touches: " + ", ".join(f"{n} {c}" for n, c in eng.most_common()))
    if not eng["meeting_held"]:
        say("WARN", "Engagement: no meeting_held, so time to action and Done can't be measured")
    if no_topic:
        say("WARN", f"Engagement: {count(no_topic, 'touch')} without a topic: they can't tell a churn call from a check-in")
    if eng_broken:
        say("WARN", f"Engagement: {count(eng_broken, 'line')} unreadable: skipped")

# --- 5. Onboarding holdout and 6. renewal outcomes (optional) ---------------------
print("\n5. Onboarding holdout (optional)")
holdout = read_csv(FILES["holdout_file"], ["account_id"], "Holdout")
if holdout is None and not (FOLDER / FILES["holdout_file"]).exists():
    say("WARN", "Holdout: none. Everyone gets onboarding emails; no welcomed vs. control report")

print("\n6. Renewal outcomes (optional)")
outcomes = read_csv(FILES["outcomes_file"], ["account_id", "renewal_date", "outcome"], "Outcomes")
if outcomes:
    if odd := Counter(r["outcome"] for r in outcomes if r["outcome"] not in OUTCOMES):
        say("WARN", f"Outcomes: unknown values (use {', '.join(sorted(OUTCOMES))}): "
                    + ", ".join(f"{o!r} ({n})" for o, n in odd.items()))
elif not (FOLDER / FILES["outcomes_file"]).exists():
    say("WARN", "Outcomes: none. No backtest (recall, precision, warning time)")

# --- What works with this data ----------------------------------------------------
usage_ok = bool(by_name[USAGE_EVENT])
long_enough = bool(first) and (last - first).days + 1 >= CHURN_DAYS
named_tier = any(r["tier"] == TIERS["named_csm"] for r in crm or [])
works = [
    ("Activation (go-live, stalled, silent)", bool(by_name) and bool(accounts),
     "needs events and the CRM"),
    ("Churn, Shelfware and expansion", usage_ok and long_enough and bool(accounts),
     f"needs {USAGE_EVENT} and {CHURN_DAYS} days of events"),
    ("Breadth signals (active users)", bool(users_seen), "needs events with a user_id"),
    ("QBR coming up / overdue", named_tier and any((r.get("next_qbr_date") or r.get("last_qbr_date")) for r in crm or []),
     f"needs {TIERS['named_csm']} accounts with QBR dates"),
    ("Tasks with owners and managers", bool(owners), "needs the CS team table"),
    ("Journeys, time to action, last touch", bool(eng["meeting_held"] or eng["email_sent"]),
     "needs engagement history"),
    ("Welcomed vs. control report", bool(holdout), "needs the onboarding holdout"),
    ("Backtest (did the flags see it coming?)", bool(outcomes), "needs renewal outcomes"),
]
print("\nWhat the engine can do with this data")
for feature, on, why in works:
    print(f"  {'yes' if on else 'no ':<4} {feature}" + ("" if on else f"  ({why})"))

print(f"\n{errors} error{'s' if errors != 1 else ''}, {warnings} warning{'s' if warnings != 1 else ''}."
      + (" Fix the errors before running the engine." if errors else " Good to run."))
sys.exit(1 if errors else 0)
