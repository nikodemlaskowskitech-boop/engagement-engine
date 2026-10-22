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

Where actions go (a Slack sandbox, set up in .env):
  a CSM, pool or CS ops  -> one message per owner in #cs-queue, listing
                            their new tasks (SLACK_WEBHOOK_QUEUE)
  automated              -> customer emails are still simulated: there are
                            no real customers. They're previewed in
                            #email-preview instead (SLACK_WEBHOOK_EMAIL)
Without the webhooks in .env, nothing is posted and actions are only logged.

An action is logged only after its Slack post worked. If a post fails, those
actions stay out of the log and are retried on the next run.

Facts the pipeline creates ("welcome sent") live here, never in the CRM.
Each customer email also becomes an email_sent event in the engagement stream
(data/engagement/<run date>.jsonl), so the CS history and new actions sit on
one timeline that journeys and views can read.

Run flags.py first, then:  python actions.py
Run it twice: the second run should post and log nothing new.
"""

import csv
import json
import os
import sys
import urllib.error
import uuid
import urllib.request
from collections import defaultdict
from datetime import datetime, time, timedelta
from pathlib import Path

from cairnpath_db import connect

sys.stdout.reconfigure(encoding="utf-8")

REPO = Path(__file__).resolve().parent
DATA = REPO / "data"
LOG = DATA / "action_log.csv"
FLAGS = ("activation", "churn", "expansion", "qbr")
NOT_ACTIONS = {"none", "report"}
COLUMNS = ["logged_on", "account_id", "flag", "status", "routed_to", "channel", "action", "outcome"]

# Which customer email each automated action is, as a topic in the engagement stream
EMAIL_TOPICS = {("activation", "Activated"): "welcome_pack",
                ("activation", "Stalled"):   "invite_your_team",
                ("activation", "Silent"):    "need_help_starting"}
EMAIL_HOUR = 9                                    # emails go out at 9:00 local time
UTC_OFFSET_HOURS = {"UK&I": 1, "DACH": 2, "France & Benelux": 2, "Nordics": 2}


def load_webhooks():
    """Read the Slack webhook addresses from .env (git-ignored), without
    printing them: anyone with the address can post into the workspace."""
    env = REPO / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8-sig").splitlines():
            name, _, value = line.partition("=")
            if name.strip() in ("SLACK_WEBHOOK_QUEUE", "SLACK_WEBHOOK_EMAIL") and value.strip():
                os.environ[name.strip()] = value.strip().strip('"').strip("'")
    return os.environ.get("SLACK_WEBHOOK_QUEUE"), os.environ.get("SLACK_WEBHOOK_EMAIL")


def post_to_slack(webhook, text):
    """Send one message to a Slack channel. True if Slack accepted it."""
    request = urllib.request.Request(
        webhook, data=json.dumps({"text": text}).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status == 200
    except (urllib.error.HTTPError, urllib.error.URLError) as error:
        # the error never includes the address, so it's safe to print
        print(f"  Slack post failed ({error}): not logged, will retry next run")
        return False


def write_email_events(tasks):
    """Record each customer email as an email_sent event, same schema as the
    CS history the generator wrote."""
    path = DATA / "engagement" / f"{run_date}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for t in tasks:
            sent = (datetime.combine(run_date, time(EMAIL_HOUR))
                    - timedelta(hours=UTC_OFFSET_HOURS.get(t["region"], 1)))
            f.write(json.dumps({
                "event_id": str(uuid.uuid4()),
                "event_name": "email_sent",
                "event_type": "engagement",
                "timestamp": sent.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "account_id": t["account_id"],
                "user_id": None,
                "properties": {"owner_id": "automated",
                               "topic": EMAIL_TOPICS.get((t["flag"], t["status"]), "other")},
                "context": {"source": "actions.py"},
            }) + "\n")


def key(row):
    """What makes an action 'the same one': account, flag and status."""
    return (row["account_id"], row["flag"], row["status"])


def task_line(t):
    """One task, as it reads in Slack."""
    name = t["account_name"] or "(not in the CRM)"
    return (f"• *{name}* ({t['account_id']}), {t['flag']} {t['status']}: {t['reasons']}\n"
            f"    → {t['action']}")


# The run date is the data's "today" (the day after the last day of events),
# never the computer's clock: a re-run on old data must log the same dates.
run_date = connect().sql("SELECT as_of_date + 1 FROM as_of").fetchone()[0]
queue_hook, email_hook = load_webhooks()

with (DATA / "cs_team.csv").open(encoding="utf-8") as f:
    owner_names = {r["owner_id"]: r["owner_name"] for r in csv.DictReader(f)}
owner_names["cs_ops"] = "CS ops"
owner_names["email"] = "Customer emails"

# What was already done, from earlier runs
done = set()
if LOG.exists():
    with LOG.open(encoding="utf-8") as f:
        done = {key(r) for r in csv.DictReader(f)}

# What the flags ask for today, grouped by where it goes
new = defaultdict(list)        # owner (or "email") -> new tasks
skipped = 0
for flag in FLAGS:
    path = DATA / f"flags_{flag}.csv"
    if not path.exists():
        sys.exit(f"{path} is missing: run python flags.py first")
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["routed_to"] in NOT_ACTIONS:
                continue
            task = dict(r, flag=flag)
            if key(task) in done:
                skipped += 1
                continue
            new["email" if r["routed_to"] == "automated" else r["routed_to"]].append(task)
            done.add(key(task))   # so a duplicate row in the same run is only done once

# Do the actions: one Slack message per group, then log the group if it worked
first_run = not LOG.exists()
logged = 0
with LOG.open("a", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=COLUMNS)
    if first_run:
        writer.writeheader()
    for group, tasks in sorted(new.items()):
        if group == "email":
            channel, hook = "email", email_hook
            text = (f"*{len(tasks)} customer emails* that would go out today "
                    f"(simulated, run date {run_date}):\n" + "\n".join(task_line(t) for t in tasks))
        else:
            channel, hook = "queue", queue_hook
            text = (f"*{owner_names.get(group, group)}*: {len(tasks)} new "
                    f"{'task' if len(tasks) == 1 else 'tasks'} (run date {run_date})\n"
                    + "\n".join(task_line(t) for t in tasks))
        if hook:
            if not post_to_slack(hook, text):
                continue
            outcome = "previewed in Slack (simulated)" if channel == "email" else "posted to Slack"
        else:
            outcome = "sent (simulated)" if channel == "email" else "queued"
        if channel == "email":
            write_email_events(tasks)       # the email, on the engagement timeline
        writer.writerows({"logged_on": run_date, "account_id": t["account_id"], "flag": t["flag"],
                          "status": t["status"], "routed_to": t["routed_to"], "channel": channel,
                          "action": t["action"], "outcome": outcome} for t in tasks)
        f.flush()         # write each group straight away: a crash later can't cause a re-post
        logged += len(tasks)
        print(f"  {channel:<6} {owner_names.get(group, group):<20} {len(tasks):>3}  {outcome}")

print(f"\nAction log, run date {run_date}")
print(f"  New actions logged: {logged}")
print(f"  Already done, skipped: {skipped}")
if not (queue_hook and email_hook):
    print("  Slack: no webhook in .env for "
          + " and ".join(n for n, h in (("#cs-queue", queue_hook), ("#email-preview", email_hook)) if not h)
          + ", so those actions are only logged")
print(f"\nSaved to {LOG.relative_to(REPO)}")
