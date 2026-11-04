"""
Brings the Slack clicks from AWS to the laptop, so the laptop's pipeline sees
them too (who took which task, which calls were logged).

    python pull_clicks.py

Since the Slack listener runs in AWS, each click is saved in the data bucket,
one file each under [aws] slack_clicks_prefix (state/clicks/). This gathers
them into data/engagement/slack_clicks.jsonl, oldest first. That's the same
file name the engine in AWS uses, so when upload.py sends it up, nothing is
counted twice. The file is only rewritten when a click is new.

First step of run_all.ps1. Not signed in to AWS (or no network)? It says so
and the run carries on without the newest clicks: the laptop never stops
working because AWS can't be reached. Read-only on AWS.
"""

import json
import sys
from datetime import date, timedelta
from pathlib import Path

from settings import INPUTS, SETTINGS

AWS = SETTINGS["aws"]
CLICKS = AWS["slack_clicks_prefix"]
TARGET = Path(__file__).resolve().parent / "data" / "engagement" / "slack_clicks.jsonl"
EVENTS = INPUTS / Path(SETTINGS["data"]["events_glob"]).parent       # the product events, one file per day


def run_date(events_folder):
    """The pipeline's run date: the day after the last day of product events
    (one file per day, events/<date>.jsonl). None when there are none."""
    days = [p.stem for p in Path(events_folder).glob("*.jsonl")]
    days = [d for d in days if len(d) == 10 and d[4] == d[7] == "-"]
    return date.fromisoformat(max(days)) + timedelta(days=1) if days else None


def combine(events, events_folder):
    """The clicks as one engagement file's text, oldest first. A click is never
    dated after the run date it answers: a card posted before buttons carried
    the run date gets dated by the listener's clock, so it's brought back to
    the run date here (the engine's 'today' is the data's, never the clock's)."""
    last = run_date(events_folder)
    for e in events:
        if last and e["timestamp"][:10] > last.isoformat():
            e["timestamp"] = last.isoformat() + e["timestamp"][10:]
    events = sorted(events, key=lambda e: (e["timestamp"], e["event_id"]))
    return "".join(json.dumps(e) + "\n" for e in events)


def clicks(s3):
    """Every click in the bucket."""
    owner, events = str(AWS["account_id"]), []
    for page in s3.get_paginator("list_objects_v2").paginate(
            Bucket=AWS["bucket"], Prefix=CLICKS, ExpectedBucketOwner=owner):
        for obj in page.get("Contents", []):
            body = s3.get_object(Bucket=AWS["bucket"], Key=obj["Key"], ExpectedBucketOwner=owner)["Body"].read()
            events.append(json.loads(body))
    return events


def pull(s3, target=TARGET, events_folder=None):
    """Write the clicks file if anything changed. Returns (clicks, written)."""
    events = clicks(s3)
    text = combine(events, events_folder or EVENTS)
    old = target.read_text(encoding="utf-8") if target.exists() else ""
    if not events or text == old:
        return len(events), False
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return len(events), True


def main(s3=None):
    try:
        if s3 is None:
            import boto3
            s3 = boto3.Session(profile_name=AWS.get("profile") or None, region_name=AWS["region"]).client("s3")
        count, written = pull(s3)
    except Exception as error:                  # not signed in, session expired, offline...
        print(f"Slack clicks not pulled ({type(error).__name__}): sign in with "
              f"aws login --profile {AWS.get('profile')} to include them. Carrying on.")
        return 0
    if not count:
        print("No Slack clicks in AWS yet.")
    else:
        print(f"{count} Slack click(s) in AWS; " + ("data/engagement/slack_clicks.jsonl updated."
                                                    if written else "nothing new."))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
