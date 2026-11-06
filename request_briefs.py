"""
Ask the engine in AWS to write the handover briefs: the only way the model is
called in AWS (5 Nov 2026).

    python request_briefs.py          ask, then wait for the run and say what happened
    python request_briefs.py --no-wait  ask and return at once

Every other run (an upload, the daily schedule) only checks, for free, whether
each brief is out of date; the cockpit shows which ones are. This starts one
engine run with {"briefs": true}: handover.py rewrites the briefs whose context
changed (the cache skips the rest), within the monthly budget in settings.toml
[llm.monthly_budget_usd.aws], and every call goes in the AI spend ledger.

Only the admin can do this: starting the engine function is allowed to the admin
role (aws login), the daily schedule (whose input is fixed) and S3 events, and
nobody else. Sign in first: aws login --profile cairnpath.
"""

import json
import sys
import time
from datetime import datetime, timezone

from settings import SETTINGS

AWS = SETTINGS["aws"]
STACK = "CairnpathData"
WAIT_SECONDS, POLL_SECONDS = 600, 15


def engine_function(cfn):
    """The engine function's name, as the stack printed it (CairnpathData.EngineFunction)."""
    outputs = cfn.describe_stacks(StackName=STACK)["Stacks"][0].get("Outputs", [])
    name = next((o["OutputValue"] for o in outputs if o["OutputKey"] == "EngineFunction"), None)
    if not name:
        sys.exit(f"No EngineFunction output in stack {STACK}: deployed?")
    return name


def last_run(s3):
    try:
        body = s3.get_object(Bucket=AWS["bucket"], Key=AWS["outputs_prefix"] + "run.json",
                             ExpectedBucketOwner=str(AWS["account_id"]))["Body"].read()
        return json.loads(body)
    except Exception:                       # no run yet, or not readable yet
        return {}


def request(cfn, lam, s3, wait=True, sleep=time.sleep, clock=time.time):
    asked_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    name = engine_function(cfn)
    # "Event": start it and return; the run takes a minute or two
    lam.invoke(FunctionName=name, InvocationType="Event",
               Payload=json.dumps({"trigger": "briefs", "briefs": True}).encode("utf-8"))
    print(f"Asked {name} to write the handover briefs ({asked_at}).")
    if not wait:
        print(f"See outputs/run.json in a couple of minutes (\"briefs\": true).")
        return 0
    start = clock()
    while clock() - start < WAIT_SECONDS:
        sleep(POLL_SECONDS)
        run = last_run(s3)
        if run.get("briefs") and run.get("started_at", "") >= asked_at and run.get("status"):
            print(f"Run {run['status']} in {run.get('seconds', '?')} s"
                  + (f" (failed step: {run['failed_step']})" if run.get("failed_step") else ""))
            for w in run.get("warnings", []):
                print(f"  warning: {w}")
            briefs = [o for o in run.get("outputs", []) if o.startswith("handover/") and o.endswith(".md")]
            print(f"  briefs rewritten: {', '.join(b[9:-3] for b in briefs) if briefs else 'none (nothing had changed)'}")
            if AWS.get("outputs_prefix") and any(o.endswith(SETTINGS['llm']['ledger_file']) for o in run.get("outputs", [])):
                print(f"  the AI spend ledger has new rows (outputs/{SETTINGS['llm']['ledger_file']})")
            return 0 if run["status"] == "OK" else 1
    print(f"No finished briefs run after {WAIT_SECONDS // 60} minutes: look at outputs/run.json later.")
    return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    import boto3
    session = boto3.Session(profile_name=AWS.get("profile") or None, region_name=AWS["region"])
    sys.exit(request(session.client("cloudformation"), session.client("lambda"), session.client("s3"),
                     wait="--no-wait" not in sys.argv))
