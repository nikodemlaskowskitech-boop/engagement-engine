"""
Check the flags against the answer key: does each flag find the accounts it
should, and only those?

generate_events.py gives every account a hidden personality (healthy,
declining, power_user, ...) and writes it to data/answer_key.csv. Real data
has no answer key, so the flags never read it. This script does, and checks
each flag two ways:

  found        of the accounts that should be flagged, how many were
  false alarm  flagged accounts that shouldn't have been

Then it lists every miss and false alarm by name, with the numbers, so a
person can judge whether the rule or the data is wrong.

The QBR flag isn't checked here: it runs on the calendar, not on behaviour.

Run flags.py first, then:  python check_flags.py
"""

import csv
from datetime import date
import sys
from collections import Counter
from pathlib import Path

from cairnpath_db import connect
from settings import DEMO, SETTINGS

sys.stdout.reconfigure(encoding="utf-8")

DATA = Path(__file__).resolve().parent / "data"


def read(name):
    path = DATA / name
    if not path.exists():
        sys.exit(f"{path} is missing: run python generate_events.py, then python flags.py")
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


# The answer key only exists for the generated demo data. On a company's own
# data there is no truth to check against here: the backtest against its
# closed renewals (backtest.py) is the real test.
if not DEMO:
    print("Running on your own data ([data] inputs): no answer key, so this check is skipped. "
          "backtest.py tests the flags against your closed renewals.")
    sys.exit(0)

key = {r["account_id"]: r for r in read("answer_key.csv")}
flags = {name: read(f"flags_{name}.csv") for name in ("activation", "churn", "expansion")}

# Utilisation now (last 30 days), to explain expansion misses
con = connect()
util_now = dict(con.sql("SELECT account_id, util_m3 FROM monthly_usage").fetchall())

# The activation flag only looks at contracts from the last 45 days (SCOPE_DAYS
# in flags.py). A customer who signed in July is past onboarding by October,
# so it can't be expected to show up there.
ACTIVATION_SCOPE_DAYS = SETTINGS["activation"]["scope_days"]
in_activation_scope = {a for (a,) in con.sql(f"""
    SELECT c.account_id FROM crm c, as_of a
    WHERE a.as_of_date - c.contract_start <= {ACTIVATION_SCOPE_DAYS}""").fetchall()}


def personality(account_id):
    return key[account_id]["personality"]


def share(part, whole):
    """A percentage that doesn't round 1 in 343 down to a misleading 0%."""
    pct = 100 * part / whole if whole else 0
    return "under 1%" if 0 < pct < 1 else f"{pct:.0f}%"


# --- 1. What each personality triggers ------------------------------------------

print("\n1. What each personality triggers (accounts, then flag statuses)\n")
for p, n in Counter(r["personality"] for r in key.values()).most_common():
    found = []
    for name, rows in flags.items():
        statuses = Counter(r["status"] for r in rows if personality(r["account_id"]) == p)
        if statuses:
            found.append(f"{name}: " + ", ".join(f"{s} {c}" for s, c in statuses.most_common()))
    print(f"  {p:<16} {n:>4}   " + ("; ".join(found) or "no flags"))

# --- 2. Expectations -------------------------------------------------------------
# (what we expect, flag, statuses that count, personality that should get them,
#  minimum share found, statuses no other personality should get)

EXPECTATIONS = [
    ("Stalled accounts show as Stalled",         "activation", {"Stalled"},     "stalled",         100),
    ("Never-onboarded accounts show as Silent",  "activation", {"Silent"},      "never_onboarded", 100),
    ("Handoff gaps are caught",                  "activation", {"Handoff gap"}, "handoff_gap",     100),
    ("New customers (last 45 days) activate",    "activation", {"Activated", "Activating"}, "new_customer", 90),
    ("Declining accounts raise a churn flag",    "churn",      {"Act", "Watch"}, "declining",      90),
    # Usage sliding but the same people using it: one signal only, so Watch, not Act
    ("Slipping accounts show as churn Watch",     "churn",      {"Watch"},        "slipping",       70),
    # A shelfware account with a random bad week can land in Watch instead, so not 100%
    ("Shelfware accounts show as Shelfware",     "churn",      {"Shelfware"},    "shelfware",      80),
    # Power users are engaged, but some bought far more than they use: with
    # headroom left there's no expansion case yet, so not all of them qualify.
    ("Power users raise an expansion flag",      "expansion",  {"Act", "Watch"}, "power_user",     60),
]

# False alarms: rows that should never come from these personalities
FALSE_ALARMS = [
    ("Healthy or growing accounts in churn",     "churn",      {"Act", "Watch"}, {"healthy", "power_user"}),
    # New customers ramp up over their first month: they must never look like shelfware
    ("Ramping or healthy accounts as Shelfware", "churn",      {"Shelfware"},    {"healthy", "power_user", "new_customer"}),
    # A healthy account near its cap is a fair Watch (heading over contract);
    # Act needs growing users too, so it should come from power users only.
    ("Healthy accounts at expansion Act",        "expansion",  {"Act"},          {"healthy"}),
]

print("\n2. Checks (counts next to percentages: small groups mislead)\n")
problems = []

for label, flag, statuses, p, min_pct in EXPECTATIONS:
    should = [a for a, r in key.items() if r["personality"] == p]
    if flag == "activation" and p != "handoff_gap":      # handoff gaps have no contract date
        should = [a for a in should if a in in_activation_scope]
    hit = {r["account_id"] for r in flags[flag] if r["status"] in statuses}
    found = [a for a in should if a in hit]
    # Activation statuses belong to one personality each, so anyone else there is
    # a false alarm. (Churn and expansion false alarms are checked further down.)
    others = sorted(a for a in hit if personality(a) != p and flag == "activation")
    ok = len(found) >= min_pct / 100 * len(should) and not others
    target = "100%" if min_pct == 100 else f"{min_pct}%+"
    print(f"  {'PASS ' if ok else 'CHECK'}  {label}: {len(found)} of {len(should)} "
          f"({share(len(found), len(should))}, target {target})"
          + (f"; also {len(others)} from other personalities" if others else ""))
    for a in should:
        if a not in hit:
            note = f"utilisation now {util_now[a]:.0f}%" if flag == "expansion" and util_now.get(a) is not None else ""
            problems.append((label, "missed", a, note))
    for a in others:
        problems.append((label, f"false alarm ({personality(a)})", a, ""))

for label, flag, statuses, wrong in FALSE_ALARMS:
    should_not = [a for a, r in key.items() if r["personality"] in wrong]
    rows = [r for r in flags[flag] if r["status"] in statuses and personality(r["account_id"]) in wrong]
    print(f"  {'PASS ' if not rows else 'CHECK'}  {label}: {len(rows)} of {len(should_not)} "
          f"({share(len(rows), len(should_not))}, target 0)")
    for r in rows:
        problems.append((label, f"false alarm ({personality(r['account_id'])})",
                         r["account_id"], f"{r['status']}: {r['reasons']}"))

# The planted trend (generate_events.PLANTED_TREND, 21 Nov 2026): the engine must see that new
# logos of the recent cohort are shelfware far more often than the rest of the book. Rates come
# from the flags, never from the answer key; the key only says which accounts were planted.
from generate_events import PLANTED_TREND, RECOVERY, END_DATE
planted = [a for a, r in key.items() if r.get("planted") == PLANTED_TREND["name"]]
if planted:
    crm = {r["account_id"]: r for r in read("crm_accounts.csv") if r.get("status") != "churned"}
    lo, hi = PLANTED_TREND["signed_days_ago"]
    def days_ago(a):
        return (END_DATE - date.fromisoformat(crm[a]["customer_since"])).days
    shelf = {r["account_id"] for r in flags["churn"] if r["status"] == "Shelfware"}
    cohort = [a for a in crm if lo <= days_ago(a) <= hi]
    rest = [a for a in crm if days_ago(a) > hi]
    c_hit, r_hit = sum(a in shelf for a in cohort), sum(a in shelf for a in rest)
    c_rate, r_rate = c_hit / max(1, len(cohort)), r_hit / max(1, len(rest))
    found = sum(a in shelf for a in planted)
    ok = c_rate >= 3 * r_rate and found >= 0.8 * len(planted)
    print(f"  {'PASS ' if ok else 'CHECK'}  Planted trend ({PLANTED_TREND['name']}): signed {lo}-{hi} days ago "
          f"{c_hit} of {len(cohort)} Shelfware ({share(c_hit, len(cohort))}) against {r_hit} of {len(rest)} "
          f"({share(r_hit, len(rest))}) for the book signed earlier, target 3x; "
          f"{found} of {len(planted)} planted accounts flagged")
    for a in planted:
        if a not in shelf:
            problems.append(("Planted trend", "missed", a, ""))

# Recovered accounts (generate_events.RECOVERY, the data lab, 22 Nov 2026): far below their
# contract all year, back to normal over the last 90 days. Today they carry no churn flag; that
# they had one three months ago shows on Leadership's "What moved" (role_pages_test checks it).
recovered = [a for a, r in key.items() if r.get("planted") == RECOVERY["name"]]
if recovered:
    churned_now = {r["account_id"] for r in flags["churn"]}
    still = [a for a in recovered if a in churned_now]
    ok = len(recovered) >= 3 and not still
    print(f"  {'PASS ' if ok else 'CHECK'}  Recovered accounts: {len(recovered) - len(still)} of {len(recovered)} carry no churn flag today")
    for a in still:
        problems.append(("Recovered", "still flagged", a, ""))

# --- 3. Every miss and false alarm, by name -------------------------------------

print("\n3. Misses and false alarms, to judge one by one\n")
if not problems:
    print("  None")
for label, kind, a, note in problems:
    print(f"  {a}  {key[a]['tier']:<13} {kind:<24} {label}" + (f"  [{note}]" if note else ""))
print()
