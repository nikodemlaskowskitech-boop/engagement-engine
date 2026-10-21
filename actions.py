"""
The action log: turn flags into actions, and do each action once only.

flags.py says what should happen ("send welcome email", "CSM to
investigate"), but it raises the same flags every time it runs. Without a
record of what was already done, a daily run would email the same customer
every day. This script keeps that record in data/action_log.csv.

For every flag row that needs doing, it checks the log first:
  - already logged  -> skip it (that's what makes it "once only")
  - not logged yet  -> do it and add a row to the log

"Once" means once per account, flag and status. If a churn flag moves from
Watch to Act, that's a new situation and gets a new action; the same Watch
again tomorrow does not.

Not actions, so not logged:
  - routed_to 'none'    (Activating: nothing to do yet)
  - routed_to 'report'  (Self-service rows: the report reads the flag files
                         directly, for analysis across accounts)

How an action is "done" today (no real email or Slack yet, that's the next step):
  automated              -> email, recorded as "sent (simulated)"
  a CSM, pool or CS ops  -> a task in their queue, recorded as "queued"

Facts the pipeline creates ("welcome sent") live here, never in the CRM.

Run flags.py first, then:  python actions.py
Run it twice: the second run should log nothing new.
"""

import csv
import sys
from collections import Counter
from pathlib import Path

from cairnpath_db import connect

sys.stdout.reconfigure(encoding="utf-8")

DATA = Path(__file__).resolve().parent / "data"
LOG = DATA / "action_log.csv"
FLAGS = ("activation", "churn", "expansion", "qbr")
NOT_ACTIONS = {"none", "report"}
COLUMNS = ["logged_on", "account_id", "flag", "status", "routed_to", "channel", "action", "outcome"]

# The run date is the data's "today" (the day after the last day of events),
# never the computer's clock: a re-run on old data must log the same dates.
run_date = connect().sql("SELECT as_of_date + 1 FROM as_of").fetchone()[0]


def key(row):
    """What makes an action 'the same one': account, flag and status."""
    return (row["account_id"], row["flag"], row["status"])


# What was already done, from earlier runs
done = set()
if LOG.exists():
    with LOG.open(encoding="utf-8") as f:
        done = {key(r) for r in csv.DictReader(f)}

# What the flags ask for today
new_rows, skipped = [], 0
for flag in FLAGS:
    path = DATA / f"flags_{flag}.csv"
    if not path.exists():
        sys.exit(f"{path} is missing: run python flags.py first")
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["routed_to"] in NOT_ACTIONS:
                continue
            row = {"logged_on": run_date, "account_id": r["account_id"], "flag": flag,
                   "status": r["status"], "routed_to": r["routed_to"], "action": r["action"]}
            if key(row) in done:
                skipped += 1
                continue
            if r["routed_to"] == "automated":
                row.update(channel="email", outcome="sent (simulated)")
            else:
                row.update(channel="queue", outcome="queued")
            new_rows.append(row)
            done.add(key(row))   # so a duplicate row in the same run is only done once

# Append the new actions (the log only ever grows: it's the history)
first_run = not LOG.exists()
with LOG.open("a", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=COLUMNS)
    if first_run:
        writer.writeheader()
    writer.writerows(new_rows)

print(f"\nAction log, run date {run_date}\n")
print(f"  New actions logged: {len(new_rows)}")
print(f"  Already done, skipped: {skipped}")

if new_rows:
    print("\n  New actions by channel and owner:")
    for (channel, owner), n in sorted(Counter((r["channel"], r["routed_to"]) for r in new_rows).items()):
        print(f"    {channel:<6} {owner:<14} {n:>3}")

print(f"\nSaved to {LOG.relative_to(DATA.parent)}")
