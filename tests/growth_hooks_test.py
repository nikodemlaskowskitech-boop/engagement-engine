"""
The growth hooks the generator plants (generate_events.GROWTH_HOOKS, 25 Nov 2026) are in
the events, and nothing else looks like them.

Whitespace rests on "no hook, no pitch": a growth line needs an observed behaviour. So the
demo data must hold real hooks on the planted accounts and none elsewhere, or the engine's
whitespace (next step) would be tested against noise:
  - features follow what was bought: experiments only with the experiments add-on, and
    nearly every add-on holder uses it;
  - addon: cohort-heavy in the last 90 days, no add-on; no other CRM account comes close;
  - people: dashboards shared to 8+ people; nobody else's go to more than 4;
  - unused_addon: the add-on bought, no experiment in 90 days.

Reads what generate_events.py wrote (data/). Run: python tests/growth_hooks_test.py
"""
import collections
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
failed = 0


def check(ok, what, detail=""):
    global failed
    print(f"  {'PASS' if ok else 'FAIL'}  {what}" + (f"  ->  {str(detail)[:300]}" if detail and not ok else ""))
    failed += not ok


def read(name):
    with (DATA / name).open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


key = {r["account_id"]: r for r in read("answer_key.csv")}
crm = {r["account_id"]: r for r in read("crm_accounts.csv")}
files = sorted((DATA / "events").glob("*.jsonl"))
recent = {f.stem for f in files[-90:]}
count, builders, viewers, exp_all = (collections.defaultdict(collections.Counter), collections.defaultdict(set),
                                     collections.Counter(), collections.Counter())
for f in files:
    for line in f.read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        if e["event_type"] != "product":
            continue
        a = e["account_id"]
        if e["event_name"] == "experiment_launched":
            exp_all[a] += 1
        if f.stem in recent:
            count[a][e["event_name"]] += 1
            if e["event_name"] == "cohort_created":
                builders[a].add(e["user_id"])
            if e["event_name"] == "dashboard_shared":
                viewers[a] = max(viewers[a], e["properties"].get("viewers", 0))

has = lambda a: "experiments" in (crm.get(a, {}).get("add_ons") or "")          # noqa: E731
share = lambda a: count[a]["cohort_created"] / max(1, sum(count[a].values()))  # noqa: E731
hook = {a: r.get("growth_hook", "") for a, r in key.items()}
planted = collections.defaultdict(list)
for a, h in hook.items():
    if h:
        planted[h].append(a)

print("\nFeatures follow what was bought")
check(not [a for a in exp_all if not has(a)], "no experiment without the experiments add-on (two years)",
      [a for a in exp_all if not has(a)][:5])
holders = [a for a in crm if has(a) and crm[a]["status"] != "churned" and hook.get(a) != "unused_addon"
           and sum(count[a].values()) >= 100]
using = [a for a in holders if count[a]["experiment_launched"]]
check(len(using) >= 0.9 * len(holders), f"add-on holders use it: {len(using)} of {len(holders)} active ones")

print("\nThe planted hooks are there, and only there")
check({h: len(v) for h, v in planted.items()} == {"addon": 9, "people": 5, "unused_addon": 5},
      "19 hooks planted: 9 add-on (2 held back), 5 people, 5 unused add-on", {h: len(v) for h, v in planted.items()})
check(all(share(a) >= 0.5 and count[a]["cohort_created"] >= 100 and not has(a) for a in planted["addon"]),
      "add-on hooks: half or more of their actions are cohorts (90 days), no add-on",
      [(a, round(share(a), 2)) for a in planted["addon"]])
others = [a for a in crm if not hook.get(a) and share(a) >= 0.45 and count[a]["cohort_created"] >= 20]
check(not others, "no other CRM account is that cohort-heavy (45%+)", others)
check(all(viewers[a] >= 8 for a in planted["people"]), "people hooks: dashboards shared to 8+ people",
      [(a, viewers[a]) for a in planted["people"]])
check(not [a for a in viewers if not hook.get(a) and viewers[a] > 4], "nobody else shares to more than 4")
check(all(has(a) and count[a]["experiment_launched"] == 0 for a in planted["unused_addon"]),
      "unused add-ons: bought, no experiment in 90 days")
check({key[a]["hook_expected"] for a in planted["addon"]} == {"show", "held_churn", "held_core"},
      "add-on hooks include one at churn risk and one Scale account without the basics (held back)")

print(f"\n{'All passed' if not failed else f'{failed} failed'}")
sys.exit(1 if failed else 0)
