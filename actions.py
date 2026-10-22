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

Onboarding holdout: accounts in data/onboarding_holdout.csv are the control
group and get no onboarding emails. Their email actions are logged as "held
out" (so they aren't retried) and nothing is sent; their queue tasks carry a
note so the pool or CSM doesn't send the welcome email by hand either. People
still help them if they ask: a holdout withholds the programme, not support.

QBR first: an Enterprise account with a QBR in the next 30 days gets no
separate churn or expansion task. The QBR is where the CSM deals with it: the
QBR task lists the open flags ("also in the brief: ..."), qbr.py puts them in
the facts pack, and the flag is logged as "covered by the QBR on <date>". The
flag files keep the rows, so the problem is still tracked, and team_report.py
counts the QBR as the action. If the QBR date passes with no QBR held, the
flag is raised again as a normal task (the QBR slipped; the problem didn't),
and it is never handed to a QBR again: a customer who keeps moving the QBR
can't park a problem for months.

Facts the pipeline creates ("welcome sent") live here, never in the CRM.
Each customer email also becomes an email_sent event in the engagement stream
(data/engagement/<run date>.jsonl), so the CS history and new actions sit on
one timeline that journeys and views can read.

Run flags.py and journeys.py first, then:  python actions.py
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
from time import sleep

from cairnpath_db import connect
from settings import SETTINGS
from slack_loop import card_blocks, card_channel, interactive, load_env

sys.stdout.reconfigure(encoding="utf-8")

REPO = Path(__file__).resolve().parent
DATA = REPO / "data"
LOG = DATA / "action_log.csv"
FLAGS = ("activation", "churn", "expansion", "qbr", "journey")   # journey: from journeys.py
NOT_ACTIONS = {"none", "report"}
COLUMNS = ["logged_on", "account_id", "flag", "status", "routed_to", "channel", "action", "outcome"]

# Which customer email each automated action is, as a topic in the engagement stream
EMAIL_TOPICS = {("activation", "Activated"): "welcome_pack",
                ("activation", "Stalled"):   "invite_your_team",
                ("activation", "Silent"):    "need_help_starting",
                ("churn", "Shelfware"):      "use_case_inspiration"}
EMAIL_HOUR = SETTINGS["actions"]["email_hour"]   # emails go out at this hour, local time
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
    """One task, as it reads in Slack, with the account's last CS touch for context."""
    name = t["account_name"] or "(not in the CRM)"
    touch = last_touch.get(t["account_id"])
    context = f"\n    _last touch: {touch[0]}, {touch[1]}_" if touch else "\n    _no CS touch on record_"
    return (f"• *{name}* ({t['account_id']}), {t['flag']} {t['status']}: {t['reasons']}\n"
            f"    → {t['action']}" + context)


# The run date is the data's "today" (the day after the last day of events),
# never the computer's clock: a re-run on old data must log the same dates.
con = connect()
run_date = con.sql("SELECT as_of_date + 1 FROM as_of").fetchone()[0]
# last touch up to the end of the data (not today's own emails), for context in Slack
last_touch = {a: (what, when) for a, what, when in con.sql("""
    SELECT account_id,
           arg_max(replace(properties.topic, '_', ' ') || ' (' || replace(event_name, '_', ' ') || ')',
                   "timestamp"),
           max(event_date)
    FROM engagement, as_of
    WHERE event_date <= as_of_date
    GROUP BY account_id""").fetchall()}
queue_hook, email_hook = load_webhooks()
# the bot token posts task cards with buttons; without it, everything stays a digest
bot_token, demo_user = load_env("SLACK_BOT_TOKEN", "SLACK_DEMO_USER")

with (DATA / "cs_team.csv").open(encoding="utf-8") as f:
    owner_names = {r["owner_id"]: r["owner_name"] for r in csv.DictReader(f)}
owner_names["cs_ops"] = "CS ops"
owner_names["email"] = "Customer emails"
# managers aren't owners in cs_team, only a column: mgr_james -> "James (manager)"
with (DATA / "cs_team.csv").open(encoding="utf-8") as f:
    for r in csv.DictReader(f):
        owner_names.setdefault(r["manager"], f"{r['manager'].removeprefix('mgr_').title()} (manager)")

with (DATA / "onboarding_holdout.csv").open(encoding="utf-8") as f:
    holdout = {r["account_id"] for r in csv.DictReader(f)}
HOLDOUT_NOTE = " (onboarding holdout: no welcome email or onboarding pack; help if they ask)"

# QBR first: Enterprise accounts with a QBR coming up, and its date
qbr_soon = dict(con.sql("SELECT account_id, next_qbr_date FROM qbr_schedule WHERE qbr_coming_up").fetchall())
QBR_COVERS = ("churn", "expansion")       # flags the QBR takes over (settings could hold this later)
in_the_brief = defaultdict(list)          # account -> flags folded into its QBR task

# What was already done, from earlier runs
done = set()
# Flags handed to a QBR that then didn't happen (view qbr_slips, cairnpath_db.py):
# key -> the QBR date that was promised. Each flag gets one QBR, not a new one
# every time the date moves: these come back as normal tasks.
slipped = {(a, fl, st): promised for a, fl, st, promised
           in con.sql("SELECT account_id, flag, status, max(promised_qbr) FROM qbr_slips GROUP BY ALL").fetchall()}

if LOG.exists():
    with LOG.open(encoding="utf-8") as f:
        last_channel = {key(r): r["channel"] for r in csv.DictReader(f)}   # the log is in time order
    done = set(last_channel)
    # a slipped flag whose last log entry is still "covered by the QBR" goes out again,
    # once: after that its last entry is the new task, so it counts as done
    slipped = {k: v for k, v in slipped.items() if last_channel.get(k) == "qbr"}
    done -= set(slipped)

# What the flags ask for today, grouped by where it goes
new = defaultdict(list)        # owner (or "email") -> new tasks
skipped = 0
for flag in FLAGS:
    path = DATA / f"flags_{flag}.csv"
    if not path.exists():
        sys.exit(f"{path} is missing: run python flags.py, then python journeys.py")
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["routed_to"] in NOT_ACTIONS:
                continue
            task = dict(r, flag=flag)
            if key(task) in done:
                skipped += 1
                continue
            if key(task) in slipped:                    # its QBR came and went: don't wait again
                task["action"] = (f"QBR due {slipped[key(task)]} didn't happen: act now, "
                                  f"don't wait for the next one. {task['action']}")
            elif flag in QBR_COVERS and r["account_id"] in qbr_soon:
                new["qbr_covered"].append(task)         # logged, no separate task
                in_the_brief[r["account_id"]].append(f"{flag} {r['status']}")
                done.add(key(task))
                continue
            if r["account_id"] in holdout:
                if r["routed_to"] == "automated":
                    new["held_out"].append(task)        # logged, never sent
                    done.add(key(task))
                    continue
                task["action"] += HOLDOUT_NOTE          # a person shouldn't send it by hand either
            if r["routed_to"] == "automated":
                new["email"].append(task)
            elif interactive(r["routed_to"]) and bot_token:
                new["card"].append(task)                # a card with buttons (settings.toml [slack])
            else:
                new[r["routed_to"]].append(task)        # the owner's digest message
            done.add(key(task))   # so a duplicate row in the same run is only done once

# The QBR task names what it now covers, so the CSM sees it in one place
for tasks in new.values():
    for t in tasks:
        if t["flag"] == "qbr" and in_the_brief.get(t["account_id"]):
            t["action"] += ". Also in the brief: " + ", ".join(in_the_brief[t["account_id"]])

# Do the actions: one Slack message per group, then log the group if it worked
first_run = not LOG.exists()
logged = 0
with LOG.open("a", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=COLUMNS)
    if first_run:
        writer.writeheader()
    for group, tasks in sorted(new.items()):
        if group == "card":
            # one card per task, each logged only once Slack has accepted it
            from slack_sdk import WebClient
            from slack_sdk.errors import SlackApiError
            client = WebClient(token=bot_token)
            posted = 0
            for t in tasks:
                owner = owner_names.get(t["routed_to"], t["routed_to"])
                try:
                    client.chat_postMessage(channel=card_channel(t["routed_to"], demo_user or SETTINGS["slack"]["team_channel"]),
                                            blocks=card_blocks(t, owner, last_touch.get(t["account_id"])),
                                            text=f"{t['account_name']}: {t['flag']} {t['status']} for {owner}")
                except SlackApiError as error:
                    needed = error.response.get("needed")      # Slack names the missing permission
                    print(f"  Slack card failed for {t['account_id']} ({error.response['error']}"
                          + (f", the bot needs the scope {needed}" if needed else "")
                          + "): not logged, will retry next run")
                    continue
                writer.writerow({"logged_on": run_date, "account_id": t["account_id"], "flag": t["flag"],
                                 "status": t["status"], "routed_to": t["routed_to"], "channel": "card",
                                 "action": t["action"], "outcome": "card posted"})
                f.flush()
                posted += 1
                sleep(1.1)                              # Slack allows about one message a second
            logged += posted
            print(f"  {'card':<6} {'Interactive owners':<20} {posted:>3}  task cards with buttons")
            continue
        if group == "qbr_covered":
            writer.writerows({"logged_on": run_date, "account_id": t["account_id"], "flag": t["flag"],
                              "status": t["status"], "routed_to": t["routed_to"], "channel": "qbr",
                              "action": t["action"],
                              "outcome": f"covered by the QBR on {qbr_soon[t['account_id']]}"} for t in tasks)
            f.flush()
            logged += len(tasks)
            print(f"  {'qbr':<6} {'Covered by a QBR':<20} {len(tasks):>3}  folded into the QBR task and brief")
            continue
        if group == "held_out":
            writer.writerows({"logged_on": run_date, "account_id": t["account_id"], "flag": t["flag"],
                              "status": t["status"], "routed_to": t["routed_to"], "channel": "none",
                              "action": t["action"], "outcome": "held out (control group)"} for t in tasks)
            f.flush()
            logged += len(tasks)
            print(f"  {'none':<6} {'Onboarding holdout':<20} {len(tasks):>3}  held out (control group)")
            continue
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
