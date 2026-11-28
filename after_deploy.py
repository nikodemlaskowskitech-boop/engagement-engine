"""
After a deploy: run the engine in AWS once, now, and say how it went (28 Nov 2026).

The 26 Nov deploy's first real run was the 06:17 schedule two days later, and it failed
(out of memory) with nobody watching. This starts one engine run with {"trigger": "deploy"}
(a deploy run always runs: only the schedule skips when nothing is new), waits for its
run.json, and prints the result, the time and the peak memory against the function's limits,
and any warning. The model is never called (no briefs). The run.json is saved to
data/aws_outputs/run.json, where Claude reads it.

    python after_deploy.py        (deploy.ps1 runs it last)

Exit 1 if the run failed or never finished; 0 otherwise, warnings printed. Sign in first:
aws login --profile cairnpath.
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from request_briefs import AWS, engine_function, last_run

WAIT_SECONDS, POLL_SECONDS = 1200, 20                  # the engine may take up to 15 minutes
SAVED = Path(__file__).resolve().parent / "data" / "aws_outputs" / "run.json"


def report(run):
    """The run in a few lines: result, time and memory against the limits, warnings."""
    b = run.get("budgets", {})
    lines = [f"Engine run {run.get('status')} in {run.get('seconds', '?')} s"
             + (f" of {b['seconds']:.0f}" if b.get("seconds") else "")
             + (f" (failed step: {run['failed_step']}, see outputs/last_run.txt)" if run.get("failed_step") else "")]
    peaks = run.get("step_peak_mb") or {}
    if peaks:
        step, mb = max(peaks.items(), key=lambda kv: kv[1])
        lines.append(f"  peak memory: {mb:,} MB ({step})" + (f" of {b['memory_mb']:,}" if b.get("memory_mb") else ""))
    slow = sorted((run.get("step_seconds") or {}).items(), key=lambda kv: -kv[1])[:3]
    if slow:
        lines.append("  slowest steps: " + ", ".join(f"{s} {t:.0f} s" for s, t in slow))
    lines += [f"  WARNING: {w}" for w in run.get("warnings", [])]
    return "\n".join(lines)


def after_deploy(cfn, lam, s3, sleep=time.sleep, clock=time.time, save=SAVED):
    asked_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    name = engine_function(cfn)
    lam.invoke(FunctionName=name, InvocationType="Event", Payload=json.dumps({"trigger": "deploy"}).encode("utf-8"))
    print(f"Started {name} on the new code ({asked_at}); waiting for its run.json ...")
    start = clock()
    while clock() - start < WAIT_SECONDS:
        sleep(POLL_SECONDS)
        run = last_run(s3)
        if run.get("triggered_by") == "deploy" and run.get("started_at", "") >= asked_at and run.get("status"):
            if save:
                save.parent.mkdir(parents=True, exist_ok=True)
                save.write_text(json.dumps(run, indent=2), encoding="utf-8")
            print(report(run))
            return 0 if run["status"] == "OK" else 1
    print(f"No finished run after {WAIT_SECONDS // 60} minutes: look at outputs/run.json later.")
    return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    import boto3
    session = boto3.Session(profile_name=AWS.get("profile") or None, region_name=AWS["region"])
    sys.exit(after_deploy(session.client("cloudformation"), session.client("lambda"), session.client("s3")))
