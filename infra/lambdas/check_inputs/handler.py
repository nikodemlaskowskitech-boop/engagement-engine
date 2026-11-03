"""
Lambda: checks the inputs in the data bucket after each upload (Phase 6).

Runs when upload.py writes its "upload complete" marker (inputs/_upload_complete.json),
so a batch of 158 files means one check, not 158. It copies everything under
inputs/ into the Lambda's scratch disk (/tmp), runs the same check_inputs.py
as the laptop does, and writes the report back under reports/check_inputs/:
latest.txt and latest.json (the summary), plus a dated copy of each.

Reports go under reports/, outside inputs/, so writing them never triggers
this function again (a Lambda that triggers itself loops, and bills, forever).

A data problem (ERROR in the report) is a result, not a crash: the function
still succeeds and the summary says status "ERROR". Only a real failure
(S3 unreachable, the check itself crashing) raises.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent          # check_inputs.py, settings.py and settings.toml sit here
BUCKET = os.environ.get("BUCKET", "")
INPUTS = os.environ.get("INPUTS_PREFIX", "inputs/")
REPORTS = os.environ.get("REPORTS_PREFIX", "reports/check_inputs/")
MARKER = os.environ.get("MARKER_NAME", "_upload_complete.json")
OWNER = os.environ.get("ACCOUNT_ID", "")


def _s3():
    import boto3                    # in the Lambda runtime already, nothing to package
    return boto3.client("s3")


def download_inputs(s3, folder):
    """Copy every input under the prefix into folder, keeping the layout. Returns the count."""
    n = 0
    for page in s3.get_paginator("list_objects_v2").paginate(
            Bucket=BUCKET, Prefix=INPUTS, ExpectedBucketOwner=OWNER):
        for obj in page.get("Contents", []):
            rel = obj["Key"][len(INPUTS):]
            if not rel or rel.endswith("/") or rel == MARKER:
                continue
            target = folder / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(BUCKET, obj["Key"], str(target),
                             ExtraArgs={"ExpectedBucketOwner": OWNER})
            n += 1
    return n


def run_check(folder):
    """Run check_inputs.py on folder, exactly as on the laptop. Returns (exit code, report text)."""
    done = subprocess.run([sys.executable, str(HERE / "check_inputs.py"), str(folder)],
                          cwd=HERE, capture_output=True, text=True, timeout=600,
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    if done.returncode not in (0, 1):        # 0 = fine, 1 = data errors; anything else = it crashed
        raise RuntimeError(f"check_inputs.py crashed (exit {done.returncode}): {done.stderr[-2000:]}")
    return done.returncode, done.stdout


def summarise(code, report, files, seconds, trigger):
    """The one-glance summary: status, error and warning counts, from the report's last line."""
    m = re.search(r"(\d+) errors?, (\d+) warnings?", report)
    errors, warnings = (int(m.group(1)), int(m.group(2))) if m else (None, None)
    return {
        "status": "ERROR" if code == 1 else ("WARN" if warnings else "OK"),
        "errors": errors, "warnings": warnings, "files_checked": files,
        "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "seconds": round(seconds, 1), "triggered_by": trigger,
    }


def handler(event, context, s3=None):
    s3 = s3 or _s3()
    started = time.time()
    trigger = next((r["s3"]["object"]["key"] for r in event.get("Records", [])), "manual")
    folder = Path("/tmp/inputs") if os.environ.get("AWS_LAMBDA_FUNCTION_NAME") else Path(
        os.environ.get("CHECK_FOLDER", "/tmp/check_inputs_local"))
    shutil.rmtree(folder, ignore_errors=True)   # a warm Lambda keeps /tmp: never mix two uploads
    folder.mkdir(parents=True)
    try:
        files = download_inputs(s3, folder)
        code, report = run_check(folder)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    summary = summarise(code, report, files, time.time() - started, trigger)
    stamp = summary["checked_at"].replace(":", "")
    body = {"txt": report, "json": json.dumps(summary, indent=2)}
    for name in ("latest", stamp):
        for ext, text in body.items():
            s3.put_object(Bucket=BUCKET, Key=f"{REPORTS}{name}.{ext}", Body=text.encode("utf-8"),
                          ContentType="application/json" if ext == "json" else "text/plain; charset=utf-8",
                          ExpectedBucketOwner=OWNER)
    print(json.dumps(summary))                  # also lands in CloudWatch Logs
    return summary
