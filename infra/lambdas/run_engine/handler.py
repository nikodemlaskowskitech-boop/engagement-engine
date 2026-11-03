"""
Lambda: runs the engine in AWS (Phase 6): flags, journeys, reports and the
cockpit export, the same scripts as run_all.ps1 on the laptop.

Runs when the input check writes its summary (reports/check_inputs/latest.json),
so the order is always: upload -> check -> engine. Gate: if the check said
ERROR, the engine doesn't run on that data; it writes outputs/run.json with
status SKIPPED and stops.

How: copies the engine's code into /tmp/work (the scripts expect a repo folder
with data/ next to them, and /var/task is read-only), downloads inputs/ into
/tmp/work/data/ (same layout as the laptop) plus the action log from state/,
runs each step in [aws] engine_steps in order, then uploads every file the
engine wrote to outputs/: the flags, account values, views_data.json, the
cockpit page views.html and the Parquet tables for Athena (outputs/tables/),
plus last_run.txt (everything the steps printed) and
run.json (the summary, written last).

Only writes under outputs/, so it can never trigger the check or itself.
A failing step is a real failure: run.json says FAILED, then the function raises.
"""

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent          # the bundled engine code + duckdb
BUCKET = os.environ.get("BUCKET", "")
OWNER = os.environ.get("ACCOUNT_ID", "")
INPUTS = os.environ.get("INPUTS_PREFIX", "inputs/")
STATE = os.environ.get("STATE_PREFIX", "state/")
OUTPUTS = os.environ.get("OUTPUTS_PREFIX", "outputs/")
CHECK = os.environ.get("CHECK_SUMMARY", "reports/check_inputs/latest.json")
MARKER = os.environ.get("MARKER_NAME", "_upload_complete.json")
STEPS = json.loads(os.environ.get("ENGINE_STEPS", "[]"))
CODE = json.loads(os.environ.get("ENGINE_CODE", "[]"))   # files copied into the work folder
STATE_FILES = ["action_log.csv"]                         # state kept between runs (tasks already sent)


def _s3():
    import boto3
    return boto3.client("s3")


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def put(s3, key, body, kind="text/plain; charset=utf-8"):
    """Write text or bytes (Parquet files are binary) to the bucket."""
    s3.put_object(Bucket=BUCKET, Key=key, Body=body.encode("utf-8") if isinstance(body, str) else body,
                  ContentType=kind, ExpectedBucketOwner=OWNER)


def check_status(s3):
    """What the input check said about the data now in the bucket."""
    body = s3.get_object(Bucket=BUCKET, Key=CHECK, ExpectedBucketOwner=OWNER)["Body"].read()
    return json.loads(body)


def download(s3, prefix, folder, skip=()):
    """Copy every object under prefix into folder, same layout. Returns the relative paths."""
    got = []
    for page in s3.get_paginator("list_objects_v2").paginate(
            Bucket=BUCKET, Prefix=prefix, ExpectedBucketOwner=OWNER):
        for obj in page.get("Contents", []):
            rel = obj["Key"][len(prefix):]
            if not rel or rel.endswith("/") or rel in skip:
                continue
            target = folder / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(BUCKET, obj["Key"], str(target), ExtraArgs={"ExpectedBucketOwner": OWNER})
            got.append(rel)
    return got


def run_steps(work):
    """Run each engine step in order, like run_all.ps1. Returns (ok, log, failed step, step times)."""
    env = {**os.environ, "PYTHONPATH": str(HERE), "PYTHONIOENCODING": "utf-8", "HOME": "/tmp"}
    log, times = [f"Engine run {now()}\n"], {}
    for step in STEPS:
        t0 = time.time()
        done = subprocess.run([sys.executable, step], cwd=work, env=env,
                              capture_output=True, text=True, timeout=900)
        times[step] = round(time.time() - t0, 1)
        log.append(f"\n===== python {step} ({times[step]} s) =====\n{done.stdout}{done.stderr}")
        if done.returncode != 0:
            log.append(f"\nFAILED: {step}\n")
            return False, "".join(log), step, times
    log.append(f"\nDONE: {len(STEPS)} of {len(STEPS)} steps\n")
    return True, "".join(log), None, times


def handler(event, context, s3=None, work=None):
    s3 = s3 or _s3()
    started = time.time()
    trigger = next((r["s3"]["object"]["key"] for r in event.get("Records", [])), "manual")
    summary = {"started_at": now(), "triggered_by": trigger}

    check = check_status(s3)
    summary["check"] = {k: check.get(k) for k in ("status", "errors", "warnings", "checked_at")}
    if check.get("status") == "ERROR":          # the gate: never compute on data that failed its check
        summary.update(status="SKIPPED", reason="the input check found errors (reports/check_inputs/latest.txt)")
        put(s3, OUTPUTS + "run.json", json.dumps(summary, indent=2), "application/json")
        print(json.dumps(summary))
        return summary

    work = Path(work or "/tmp/work")
    shutil.rmtree(work, ignore_errors=True)     # a warm Lambda keeps /tmp: start clean every time
    data = work / "data"
    data.mkdir(parents=True)
    try:
        for name in CODE:                       # the engine expects its code and data/ side by side
            shutil.copy(HERE / name, work / name)
        inputs = download(s3, INPUTS, data, skip={MARKER})
        state = download(s3, STATE, data)
        state = [s for s in state if s in STATE_FILES]
        ok, log, failed, times = run_steps(work)
        # everything the steps wrote, at any depth (tables/<name>/ for Athena), minus what came in
        written = sorted(p.relative_to(data).as_posix() for p in data.rglob("*")
                         if p.is_file() and p.relative_to(data).as_posix() not in set(inputs) | set(state))
        for name in written:
            kind = {"json": "application/json", "html": "text/html; charset=utf-8",
                    "csv": "text/csv; charset=utf-8", "parquet": "application/vnd.apache.parquet"
                    }.get(name.rsplit(".", 1)[-1], "text/plain; charset=utf-8")
            put(s3, OUTPUTS + name, (data / name).read_bytes(), kind)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    summary.update(status="OK" if ok else "FAILED", failed_step=failed, step_seconds=times,
                   inputs=len(inputs), outputs=written, seconds=round(time.time() - started, 1))
    put(s3, OUTPUTS + "last_run.txt", log)
    put(s3, OUTPUTS + "run.json", json.dumps(summary, indent=2), "application/json")   # last
    print(json.dumps(summary))
    if not ok:
        raise RuntimeError(f"engine step {failed} failed; see {OUTPUTS}last_run.txt")
    return summary
