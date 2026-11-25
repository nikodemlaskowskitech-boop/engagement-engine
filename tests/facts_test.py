"""
The facts layer (facts.py, ARCHITECTURE.md fix C, 13 Nov 2026): one way to ask the
engine's facts, so the cockpit and the next readers (the MCP server, the Ask box)
can't disagree.

Against the demo data and the last export (run .\\run_all.ps1 first):
  - one account's facts are exactly the cockpit's for that account;
  - at_risk(segment) holds only that segment, biggest ARR at risk first, and adds up
    to the cockpit's churn values for the same accounts;
  - a pool member's tasks include the pool-queue tasks on their own accounts (the
    cockpit's rule, #150), and nobody else's;
  - the timeline is newest first and holds every touch the cockpit shows;
  - nothing but an ID reaches the SQL.

Needs DuckDB and data/. Run: python tests/facts_test.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import facts                                       # noqa: E402

failed = 0


def check(ok, what, detail=""):
    global failed
    print(f"  {'PASS' if ok else 'FAIL'}  {what}" + (f"  ->  {str(detail)[:300]}" if detail and not ok else ""))
    failed += not ok


D = json.loads((ROOT / "data" / "views_data.json").read_text(encoding="utf-8"))
con = facts.connect_all()
book = {a["account_id"]: a for a in D["accounts"]}

print("\nOne account: the cockpit's facts")
churn_act = [a for a in D["accounts"] if any(f["flag"] == "churn" and f["status"] == "Act" for f in a["flags"])]
for acc in [churn_act[0]["account_id"], D["accounts"][0]["account_id"]]:
    a = facts.account(con, acc)
    # the page keeps the last [cockpit] page_days of the daily series (24 Nov 2026): the facts
    # layer has them all, and its tail is the page's
    a = {**a, "util": a["util"][len(a["util"]) - len(book[acc]["util"]):]}
    same = {k: a[k] for k in book[acc]} == book[acc]
    check(same, f"{acc}: every field the cockpit has, the same value", [k for k in book[acc] if a.get(k) != book[acc][k]])
check(facts.account(con, "acc_nope") is None, "an account not in the book: None, not an error")
a = facts.account(con, churn_act[0]["account_id"])
tta = [r for r in D["time_to_action"] if r["account_id"] == a["account_id"]]
check(a["called"] == {r["flag"]: r["call_date"] for r in tta}, "'called' is the team report's one rule", a["called"])

print("\nThe at-risk list for a segment")
manager = next(t["manager"] for t in D["team"] if t.get("manager"))
risk = facts.at_risk(con, manager=manager)
check(risk and all(r["manager"] == manager for r in risk), f"manager = {manager}: only that team's accounts", len(risk))
check([r["at_risk"] for r in risk] == sorted((r["at_risk"] for r in risk), reverse=True), "biggest ARR at risk first")
want = sum(f.get("value", 0) for a in D["accounts"] if a["manager"] == manager for f in a["flags"]
           if f["flag"] == "churn" and f["status"] in ("Act", "Watch", "Shelfware"))
check(sum(r["at_risk"] for r in risk) == want, "adds up to the cockpit's churn values for the same accounts",
      (sum(r["at_risk"] for r in risk), want))
act = facts.at_risk(con, statuses=("Act",))
check(len(act) == len({a["account_id"] for a in churn_act}), "churn Act across the book: the cockpit's count", len(act))

print("\nTasks: a pool member's work")
members = {t["owner_id"] for t in D["team"] if t["owner_type"] == "member"}
pools = {t["owner_id"] for t in D["team"] if t["owner_type"] == "pool"}
m = next((m for m in sorted(members) if any(t["routed_to"] in pools and book.get(t["account_id"], {}).get("owner_id") == m
                                             for t in D["tasks"])), None)
if m:
    mine = facts.tasks(con, owner=m)
    expect = [t for t in D["tasks"] if t["routed_to"] == m
              or (t["routed_to"] in pools and book.get(t["account_id"], {}).get("owner_id") == m)]
    check(mine == expect, f"{m}: tasks routed to them plus pool-queue tasks on their own accounts ({len(expect)})",
          (len(mine), len(expect)))
else:
    print("  SKIP  no pool member with a pool-queue task on their accounts")
pool = sorted(pools)[0]
check(facts.tasks(con, owner=pool) == [t for t in D["tasks"] if t["routed_to"] == pool],
      "a pool's tasks: exactly the ones in its queue")
check(facts.tasks(con) == D["tasks"], "the whole queue: the cockpit's, in the same order")

print("\nThe timeline")
acc = churn_act[0]["account_id"]
tl = facts.timeline(con, acc)
check([x["date"] for x in tl] == sorted((x["date"] for x in tl), reverse=True), "newest first")
cut = D["util_dates"][0] if D["util_dates"] else ""
check(len([x for x in tl if x["kind"] == "touch" and str(x["date"]) >= cut])
      >= len([t for t in D["touches"] if t["account_id"] == acc]) > 0
      and len([x for x in tl if x["kind"] == "touch"]) >= len([t for t in D["touches"] if t["account_id"] == acc]),
      "every customer touch the cockpit shows (the page's window), and the timeline keeps the older ones")

print("\nGrowth addressed (fix M, 25 Nov 2026): one rule, in the engine")
from datetime import date as _d, timedelta as _td
done = facts.addressed(con)
_asof = _d.fromisoformat(facts.as_of(con))
_fresh = _asof - _td(days=facts.DAYS)
lines = [(a["account_id"], f["flag"], f) for a in facts.accounts(con) for f in a["flags"] if f["flag"] in facts.ADDRESSED_FLAGS]
check(lines and all("addressed" in f and f["addressed"] == done[(i, fl)] for i, fl, f in lines),
      f"every expansion and growth line carries its addressed state ({len(lines)} lines)")
on = [v for v in done.values() if v["k"] in ("raised", "planned")]
check(all(v["date"] >= v["since"] for v in on) and all(_d.fromisoformat(v["date"]) >= _fresh for v in done.values() if v["k"] == "raised"),
      f"raised = a meeting since the signal began and in the last {facts.DAYS} days ({len(on)} on it)")
lapsed = [v for v in done.values() if v["k"] == "lapsed"]
check(all(_d.fromisoformat(v["date"]) < _fresh for v in lapsed),
      f"lapsed = raised once, but more than {facts.DAYS} days ago ({len(lapsed)} lapsed)", lapsed[:2])
check(all(v["since"] <= str(_asof) for v in done.values()), "since is never after the as-of date")

print("\nOnly IDs reach the SQL")
for bad in ("acc_1'; DROP TABLE crm; --", "acc_1) OR (1=1"):
    try:
        facts.account(con, bad)
        check(False, f"refused: {bad!r}")
    except ValueError:
        check(True, f"refused: {bad!r}")

print(f"\n{'All passed' if not failed else f'{failed} failed'}")
sys.exit(1 if failed else 0)
