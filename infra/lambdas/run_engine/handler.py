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

Also runs once a day on a schedule (EventBridge Scheduler, event {"trigger":
"schedule"}), as a safety net: if the last run is OK and was made on the data
the check last saw, there's nothing new and it stops (SKIPPED, "up to date");
if an event-driven run was missed or failed, the schedule catches it up.

Handover briefs (handover.py, an optional step: if it fails, the run still
counts as OK, with a warning): the API key comes from Parameter Store
(SecureString, read at run time and passed to that step only; never in the
Lambda's settings, never printed). The briefs' cache (outputs/handover/) is
downloaded first and only changed files go back, so an unchanged account
costs no model call and no new S3 version. The redaction keys (*_key.json)
never leave the Lambda. export_views.py runs again after the briefs, so the
cockpit shows them.

Slack clicks (from the Slack listener Lambda, one file each under
state/clicks/) are merged into data/engagement/slack_clicks.jsonl before the
steps run, so every step sees them as CS history, like the laptop's clicks.
Clicks don't wake the engine (one run per click would be wasteful); the daily
schedule runs it when clicks came in since the last run.

Only writes under outputs/, so it can never trigger the check or itself.
A failing step is a real failure: run.json says FAILED, then the function raises.
"""

import hashlib
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
CODE = json.loads(os.environ.get("ENGINE_CODE", "[]"))   # files and folders copied into the work folder
OPTIONAL = json.loads(os.environ.get("OPTIONAL_STEPS", "[]"))   # a failure here is a warning, not a failed run
KEY_PARAMETER = os.environ.get("API_KEY_PARAMETER", "")    # Parameter Store name of the LLM API key
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "")          # who writes the briefs in AWS (settings [aws])
CACHE = "handover/"                                        # the briefs and their fingerprints, kept between runs
NEVER_UPLOAD = ("_key.json",)                              # redaction keys stay inside the Lambda
STATE_FILES = ["action_log.csv"]                         # state kept between runs (tasks already sent)
CLICKS = os.environ.get("CLICKS_PREFIX", "state/clicks/")   # the Slack listener's clicks, one file each
CLICKS_FILE = "engagement/slack_clicks.jsonl"              # ...merged here for the engine (never uploaded)


def _s3():
    import boto3
    return boto3.client("s3")


def _ssm():
    import boto3
    return boto3.client("ssm")


def api_key(ssm):
    """The LLM API key from Parameter Store, decrypted, or None if it isn't there."""
    if not KEY_PARAMETER:
        return None
    try:
        return ssm.get_parameter(Name=KEY_PARAMETER, WithDecryption=True)["Parameter"]["Value"]
    except Exception:
        return None


def md5(path):
    return hashlib.md5(path.read_bytes()).hexdigest()


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


def newest_click(s3):
    """When the newest Slack click arrived ("2026-11-04T10:00:00Z"), or None."""
    newest = None
    for page in s3.get_paginator("list_objects_v2").paginate(
            Bucket=BUCKET, Prefix=CLICKS, ExpectedBucketOwner=OWNER):
        for obj in page.get("Contents", []):
            when = obj["LastModified"].astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            newest = max(newest or when, when)
    return newest


def up_to_date(s3, check):
    """True when the last run went fine, used the same input check as now, and no
    Slack click came in after it started."""
    try:
        last = json.loads(s3.get_object(Bucket=BUCKET, Key=OUTPUTS + "run.json",
                                         ExpectedBucketOwner=OWNER)["Body"].read())
    except Exception:                           # no run yet (or unreadable): not up to date
        return False
    clicked = newest_click(s3)
    return (last.get("status") == "OK" and last.get("check", {}).get("checked_at") == check.get("checked_at")
            and not (clicked and clicked >= last.get("started_at", "")))


def merge_clicks(data):
    """The clicks downloaded from state/clicks/ -> one engagement file, oldest first, none
    dated after the run date (pull_clicks.combine: the laptop does exactly the same).
    Same name as the file the laptop may upload, so a click is never counted twice:
    the bucket's clicks are the full set."""
    from pull_clicks import combine
    folder = data / CLICKS[len(STATE):]
    events = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.rglob("*.json"))]
    shutil.rmtree(folder, ignore_errors=True)
    if not events:
        return 0
    target = data / CLICKS_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(combine(events, data / "events"), encoding="utf-8")
    return len(events)


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


def run_steps(work, secrets=None):
    """Run each engine step in order, like run_all.ps1.
    Returns (ok, log, failed step, step times, warnings). secrets go to the optional steps only."""
    env = {**os.environ, "PYTHONPATH": str(HERE), "PYTHONIOENCODING": "utf-8", "HOME": "/tmp"}
    if LLM_PROVIDER:
        env["CAIRNPATH_LLM_PROVIDER"] = LLM_PROVIDER
    log, times, warnings = [f"Engine run {now()}\n"], {}, []
    for step in STEPS:
        label = step if step not in times else f"{step} (again)"
        if step in OPTIONAL and secrets is None:
            times[label] = 0.0
            warnings.append(f"{step} skipped: no API key in Parameter Store ({KEY_PARAMETER})")
            log.append(f"\n===== python {step}: skipped, no API key in Parameter Store =====\n")
            continue
        t0 = time.time()
        step_env = {**env, **(secrets or {})} if step in OPTIONAL else env
        done = subprocess.run([sys.executable, step], cwd=work, env=step_env,
                              capture_output=True, text=True, timeout=900)
        times[label] = round(time.time() - t0, 1)
        log.append(f"\n===== python {step} ({times[label]} s) =====\n{done.stdout}{done.stderr}")
        if done.returncode != 0:
            if step in OPTIONAL:                 # e.g. the model API is down: the cockpit still updates
                warnings.append(f"{step} failed (see last_run.txt); the rest of the run went on")
                log.append(f"\nWARNING: {step} failed; optional, carrying on\n")
                continue
            log.append(f"\nFAILED: {step}\n")
            return False, "".join(log), step, times, warnings
    log.append(f"\nDONE: {len(STEPS)} steps" + (f", {len(warnings)} warning(s)" if warnings else "") + "\n")
    return True, "".join(log), None, times, warnings


def handler(event, context, s3=None, work=None, ssm=None):
    s3 = s3 or _s3()
    started = time.time()
    trigger = next((r["s3"]["object"]["key"] for r in event.get("Records", [])),
                   event.get("trigger", "manual"))
    summary = {"started_at": now(), "triggered_by": trigger}

    check = check_status(s3)
    summary["check"] = {k: check.get(k) for k in ("status", "errors", "warnings", "checked_at")}
    if check.get("status") == "ERROR":          # the gate: never compute on data that failed its check
        summary.update(status="SKIPPED", reason="the input check found errors (reports/check_inputs/latest.txt)")
        put(s3, OUTPUTS + "run.json", json.dumps(summary, indent=2), "application/json")
        print(json.dumps(summary))
        return summary
    if trigger == "schedule" and up_to_date(s3, check):
        # nothing new since the last good run: say so, but leave that run's run.json as it is
        summary.update(status="SKIPPED", reason="up to date: the last run was OK, on the data the check last saw")
        print(json.dumps(summary))
        return summary

    work = Path(work or "/tmp/work")
    shutil.rmtree(work, ignore_errors=True)     # a warm Lambda keeps /tmp: start clean every time
    data = work / "data"
    data.mkdir(parents=True)
    try:
        for name in CODE:                       # the engine expects its code and data/ side by side
            if (HERE / name).is_dir():
                shutil.copytree(HERE / name, work / name)
            else:
                shutil.copy(HERE / name, work / name)
        inputs = download(s3, INPUTS, data, skip={MARKER})
        state = download(s3, STATE, data)
        clicks = merge_clicks(data)              # Slack clicks -> engagement/slack_clicks.jsonl
        state = [s for s in state if s in STATE_FILES] + ([CLICKS_FILE] if clicks else [])
        cache = {rel: md5(data / CACHE / rel) for rel in download(s3, OUTPUTS + CACHE, data / CACHE)}
        key = api_key(ssm or _ssm()) if any(s in OPTIONAL for s in STEPS) else None
        ok, log, failed, times, warnings = run_steps(work, {"ANTHROPIC_API_KEY": key} if key else None)
        # everything the steps wrote, at any depth (tables/<name>/ for Athena), minus what came in,
        # minus redaction keys, minus cache files that didn't change (no new S3 version for nothing)
        written = []
        for p in sorted(data.rglob("*")):
            rel = p.relative_to(data).as_posix()
            if (not p.is_file() or rel in set(inputs) | set(state) or rel.endswith(NEVER_UPLOAD)
                    or (rel.startswith(CACHE) and cache.get(rel[len(CACHE):]) == md5(p))):
                continue
            written.append(rel)
        for name in written:
            kind = {"json": "application/json", "html": "text/html; charset=utf-8",
                    "csv": "text/csv; charset=utf-8", "parquet": "application/vnd.apache.parquet"
                    }.get(name.rsplit(".", 1)[-1], "text/plain; charset=utf-8")
            put(s3, OUTPUTS + name, (data / name).read_bytes(), kind)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    summary.update(status="OK" if ok else "FAILED", failed_step=failed, warnings=warnings, step_seconds=times,
                   inputs=len(inputs), slack_clicks=clicks, outputs=written, seconds=round(time.time() - started, 1))
    put(s3, OUTPUTS + "last_run.txt", log)
    put(s3, OUTPUTS + "run.json", json.dumps(summary, indent=2), "application/json")   # last
    print(json.dumps(summary))
    if not ok:
        raise RuntimeError(f"engine step {failed} failed; see {OUTPUTS}last_run.txt")
    return summary
