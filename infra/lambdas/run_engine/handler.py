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
the check last saw, with the same engine code, there's nothing new and it stops
(SKIPPED, "up to date"); if an event-driven run was missed or failed, or a
deploy changed the engine's code, the schedule catches it up.

Handover briefs only on request (5 Nov 2026): no automatic run calls the model.
Every run does "handover.py --dry-run" instead, which compares each brief with
today's context for free and marks it out of date when it changed. Only an
event with {"briefs": true} swaps in the real handover.py, and only the admin
can send one (the schedule's input is fixed, S3 events carry Records, nobody
else may invoke this function): request_briefs.py does it. Either way the step
is optional (if it fails, the run still counts as OK, with a warning). The API
key comes from Parameter Store (SecureString, read at run time, only on a
briefs request, passed to that step only; never in the Lambda's settings,
never printed). The briefs' cache (outputs/handover/) is
downloaded first and only changed files go back, so an unchanged account
costs no model call and no new S3 version. The redaction keys (*_key.json)
never leave the Lambda. export_views.py runs again after the briefs, so the
cockpit shows them.

Slack clicks (from the Slack listener Lambda, one file each under
state/clicks/) are merged into data/engagement/slack_clicks.jsonl before the
steps run, so every step sees them as CS history, like the laptop's clicks.
Clicks don't wake the engine (one run per click would be wasteful); the daily
schedule runs it when clicks came in since the last run.

The public demo site (6 Nov 2026): the step "roles.py --site" builds the role pages in
sandbox mode into data/site/; after a good run they're copied to the site bucket
(SITE_BUCKET, its own bucket behind CloudFront, never the data bucket), not to
outputs/. Only pages that changed go up; nothing there is ever deleted.

Only writes under outputs/ (and the site bucket), so it can never trigger the check or itself.
A failing step is a real failure: run.json says FAILED, then the function raises.
"""

import hashlib
import json
import re
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
# Budgets (28 Nov 2026: the run failed on memory with no warning, 1,492 of 2,048 MB the day
# before). Each step reports its own peak memory; past this share of the function's memory or
# its time limit, the run says so in run.json and in its log (a "BUDGET WARNING" line, which an
# alarm watches), while it still works.
BUDGET_SHARE = float(os.environ.get("BUDGET_SHARE", "0.7"))
MEMORY_MB = int(os.environ.get("AWS_LAMBDA_FUNCTION_MEMORY_SIZE", "0") or 0)   # set by Lambda itself
# a step runs in its own Python, then reports the most memory it held (Linux: kilobytes)
STEP = ("import resource, runpy, sys\n"
        "sys.argv = sys.argv[1:]\n"
        "try:\n    runpy.run_path(sys.argv[0], run_name='__main__')\n"
        "finally:\n    print('\\n@@peak_mb=%d' % (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024), file=sys.stderr)\n")
PEAK = re.compile(r"\n@@peak_mb=(\d+)\n?")
BRIEFS_STEP = os.environ.get("BRIEFS_STEP", "handover.py")   # the only step that calls the model: on request only
DRY_RUN = BRIEFS_STEP + " --dry-run"
ACCOUNT_ID = re.compile(r"acc_[A-Za-z0-9_-]{1,40}")               # what a forced brief may name
MAX_FORCE = 5                         # what every other run does instead (free)
KEY_PARAMETER = os.environ.get("API_KEY_PARAMETER", "")    # Parameter Store name of the LLM API key
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "")          # who writes the briefs in AWS (settings [aws])
LEDGER = os.environ.get("LEDGER_FILE", "ai_spend.csv")      # the AI spend ledger, kept between runs (outputs/)
CACHE = "handover/"                                        # the briefs and their fingerprints, kept between runs
NEVER_UPLOAD = ("_key.json",)                              # redaction keys stay inside the Lambda
STORE = os.environ.get("STORE_FOLDER", "store/")            # ingest.py's events store: rebuilt each run, never uploaded
STATE_FILES = ["action_log.csv"]                         # state kept between runs (tasks already sent)
CLICKS = os.environ.get("CLICKS_PREFIX", "state/clicks/")   # the Slack listener's clicks, one file each
CLICKS_FILE = "engagement/slack_clicks.jsonl"              # ...merged here for the engine (never uploaded)
SITE_BUCKET = os.environ.get("SITE_BUCKET", "")            # the public demo site's own bucket (empty = no site)
SITE = os.environ.get("SITE_FOLDER", "site/")              # where "roles.py --site" writes the pages, under data/
SITE_CACHE = int(os.environ.get("SITE_CACHE_SECONDS", "300"))   # how long CloudFront and browsers keep a page


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


def code_version():
    """A fingerprint of the engine's own code (the bundled files, not the libraries): a deploy
    that changes a script or the settings changes it, so the schedule reruns on the new code."""
    digest = hashlib.md5()
    for name in sorted(CODE) + ["handler.py"]:
        path = HERE / name
        for f in sorted(path.rglob("*")) if path.is_dir() else [path]:
            if f.is_file():
                digest.update(f.relative_to(HERE).as_posix().encode() + f.read_bytes())
    return digest.hexdigest()[:12]


def up_to_date(s3, check):
    """True when the last run went fine, used the same input check and engine code as now,
    and no Slack click came in after it started."""
    try:
        last = json.loads(s3.get_object(Bucket=BUCKET, Key=OUTPUTS + "run.json",
                                         ExpectedBucketOwner=OWNER)["Body"].read())
    except Exception:                           # no run yet (or unreadable): not up to date
        return False
    clicked = newest_click(s3)
    return (last.get("status") == "OK" and last.get("check", {}).get("checked_at") == check.get("checked_at")
            and last.get("code") == code_version()
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


def carry(s3, name, data):
    """Bring one file from outputs/ back into data/ (it grows run after run, like the AI spend
    ledger). Returns its MD5, or None when there's none yet."""
    for page in s3.get_paginator("list_objects_v2").paginate(
            Bucket=BUCKET, Prefix=OUTPUTS + name, ExpectedBucketOwner=OWNER):
        if any(obj["Key"] == OUTPUTS + name for obj in page.get("Contents", [])):
            s3.download_file(BUCKET, OUTPUTS + name, str(data / name), ExtraArgs={"ExpectedBucketOwner": OWNER})
            return md5(data / name)
    return None


def publish_site(s3, folder):
    """Copy the sandbox pages to the site bucket: HTML only, flat (no folders), and only the
    pages whose content changed (compared by MD5, kept as the object's own label). Returns the
    names sent. Never deletes: the engine has no delete right there."""
    if not SITE_BUCKET or not folder.is_dir():
        return []
    have = {}
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=SITE_BUCKET, ExpectedBucketOwner=OWNER):
        for obj in page.get("Contents", []):
            have[obj["Key"]] = obj.get("ETag", "").strip('"')
    sent = []
    for p in sorted(folder.glob("*.html")):          # flat: a page name is never a path
        digest = md5(p)
        if have.get(p.name) == digest:                # SSE-S3, single part: the ETag is the MD5
            continue
        s3.put_object(Bucket=SITE_BUCKET, Key=p.name, Body=p.read_bytes(),
                      ContentType="text/html; charset=utf-8",
                      CacheControl=f"public, max-age={SITE_CACHE}", ExpectedBucketOwner=OWNER)
        sent.append(p.name)
    return sent


def run_steps(work, secrets=None, steps=None):
    """Run each engine step in order, like run_all.ps1 (a step may carry arguments, e.g. --dry-run).
    Returns (ok, log, failed step, step times, warnings, step peaks in MB). secrets go to the
    briefs step only."""
    steps = STEPS if steps is None else steps
    env = {**os.environ, "PYTHONPATH": str(HERE), "PYTHONIOENCODING": "utf-8", "HOME": "/tmp"}
    if LLM_PROVIDER:
        env["CAIRNPATH_LLM_PROVIDER"] = LLM_PROVIDER
    log, times, warnings, peaks = [f"Engine run {now()}\n"], {}, [], {}
    for step in steps:
        label = step if step not in times else f"{step} (again)"
        if step == BRIEFS_STEP and secrets is None:
            times[label] = 0.0
            warnings.append(f"{step} skipped: no API key in Parameter Store ({KEY_PARAMETER})")
            log.append(f"\n===== python {step}: skipped, no API key in Parameter Store =====\n")
            continue
        t0 = time.time()
        step_env = {**env, **(secrets or {})} if step == BRIEFS_STEP else env
        done = subprocess.run([sys.executable, "-c", STEP, *step.split()], cwd=work, env=step_env,
                              capture_output=True, text=True, timeout=900)
        times[label] = round(time.time() - t0, 1)
        peak = PEAK.search(done.stderr or "")
        if peak:
            peaks[label] = int(peak.group(1))
        stderr = PEAK.sub("", done.stderr or "")
        log.append(f"\n===== python {step} ({times[label]} s" + (f", {peaks[label]} MB" if label in peaks else "")
                   + f") =====\n{done.stdout}{stderr}")
        if done.returncode != 0:
            if step in OPTIONAL:                 # e.g. the model API is down: the cockpit still updates
                warnings.append(f"{step} failed (see last_run.txt); the rest of the run went on")
                log.append(f"\nWARNING: {step} failed; optional, carrying on\n")
                continue
            log.append(f"\nFAILED: {step}\n")
            return False, "".join(log), step, times, warnings, peaks
    log.append(f"\nDONE: {len(steps)} steps" + (f", {len(warnings)} warning(s)" if warnings else "") + "\n")
    return True, "".join(log), None, times, warnings, peaks


def budget_warnings(peaks, seconds, limit_seconds, memory_mb=None, share=None):
    """Past share of the memory or the time limit: one line each, while the run still works."""
    memory_mb = MEMORY_MB if memory_mb is None else memory_mb
    share = BUDGET_SHARE if share is None else share
    out = []
    if memory_mb and peaks:
        step, mb = max(peaks.items(), key=lambda kv: kv[1])
        if mb >= share * memory_mb:
            out.append(f"memory: {step} peaked at {mb:,} MB of {memory_mb:,} ({100 * mb // memory_mb}%): "
                       f"over {int(share * 100)}% of the function's memory")
    if limit_seconds and seconds >= share * limit_seconds:
        out.append(f"time: the run took {seconds:.0f} s of {limit_seconds:.0f} ({100 * seconds // limit_seconds:.0f}%): "
                   f"over {int(share * 100)}% of the function's time limit")
    return out


def handler(event, context, s3=None, work=None, ssm=None):
    s3 = s3 or _s3()
    started = time.time()
    # the time this run may take: what Lambda gives it (gone from the start), else the settings' limit
    limit_seconds = (context.get_remaining_time_in_millis() / 1000 if context is not None and hasattr(context, "get_remaining_time_in_millis")
                     else float(os.environ.get("TIMEOUT_SECONDS", "900")))
    trigger = next((r["s3"]["object"]["key"] for r in event.get("Records", [])),
                   event.get("trigger", "manual"))
    # briefs only when explicitly asked for, never on an S3 event (those carry Records)
    briefs = bool(event.get("briefs")) and not event.get("Records")
    # a briefs request may name accounts to rewrite even if nothing changed (16 Nov 2026: a brief's
    # checks improved, its sources didn't). Account IDs only, at most a few: anything else is refused.
    force = event.get("force") or []
    if force and (not briefs or not isinstance(force, list) or len(force) > MAX_FORCE
                  or not all(isinstance(a, str) and ACCOUNT_ID.fullmatch(a) for a in force)):
        summary = {"started_at": now(), "triggered_by": event.get("trigger", "manual"), "briefs": briefs,
                   "status": "REFUSED", "reason": "force: a list of up to %d account IDs, with briefs" % MAX_FORCE}
        print(json.dumps(summary))
        return summary
    steps = [BRIEFS_STEP if briefs and s == DRY_RUN else s for s in STEPS]
    summary = {"started_at": now(), "triggered_by": trigger, "briefs": briefs, "code": code_version()}

    check = check_status(s3)
    summary["check"] = {k: check.get(k) for k in ("status", "errors", "warnings", "checked_at")}
    if check.get("status") == "ERROR":          # the gate: never compute on data that failed its check
        summary.update(status="SKIPPED", reason="the input check found errors (reports/check_inputs/latest.txt)")
        put(s3, OUTPUTS + "run.json", json.dumps(summary, indent=2), "application/json")
        print(json.dumps(summary))
        return summary
    if trigger == "schedule" and not briefs and up_to_date(s3, check):
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
        ledger = carry(s3, LEDGER, data)         # this month's AI spend so far: the budget counts it
        key = api_key(ssm or _ssm()) if BRIEFS_STEP in steps else None    # read only on a briefs request
        secrets = {"ANTHROPIC_API_KEY": key, **({"CAIRNPATH_FORCE_BRIEFS": ",".join(force)} if force else {})} if key else None
        ok, log, failed, times, warnings, peaks = run_steps(work, secrets, steps)
        # everything the steps wrote, at any depth (tables/<name>/ for Athena), minus what came in,
        # minus redaction keys, minus cache files that didn't change (no new S3 version for nothing)
        written = []
        for p in sorted(data.rglob("*")):
            rel = p.relative_to(data).as_posix()
            if (not p.is_file() or rel in set(inputs) | set(state) or rel.endswith(NEVER_UPLOAD)
                    or rel.startswith(SITE)                                   # goes to the site bucket instead
                    or rel.startswith(STORE)                                  # the events again, as Parquet: work data
                    or (rel.startswith(CACHE) and cache.get(rel[len(CACHE):]) == md5(p))
                    or (rel == LEDGER and ledger == md5(p))):         # no new model call: no new version
                continue
            written.append(rel)
        for name in written:
            kind = {"json": "application/json", "html": "text/html; charset=utf-8",
                    "csv": "text/csv; charset=utf-8", "parquet": "application/vnd.apache.parquet"
                    }.get(name.rsplit(".", 1)[-1], "text/plain; charset=utf-8")
            put(s3, OUTPUTS + name, (data / name).read_bytes(), kind)
        site = publish_site(s3, data / SITE.strip("/")) if ok else []        # never half a failed run's pages
    finally:
        shutil.rmtree(work, ignore_errors=True)
    seconds = round(time.time() - started, 1)
    over = budget_warnings(peaks, seconds, limit_seconds)
    for line in over:
        print(f"BUDGET WARNING {line}")              # the alarm's metric filter looks for this
    summary.update(status="OK" if ok else "FAILED", failed_step=failed, warnings=warnings + over, step_seconds=times,
                   step_peak_mb=peaks, budgets={"memory_mb": MEMORY_MB, "seconds": limit_seconds, "warn_at": BUDGET_SHARE},
                   inputs=len(inputs), slack_clicks=clicks, outputs=written, site_pages=site,
                   seconds=seconds)
    put(s3, OUTPUTS + "last_run.txt", log)
    put(s3, OUTPUTS + "run.json", json.dumps(summary, indent=2), "application/json")   # last
    print(json.dumps(summary))
    if not ok:
        raise RuntimeError(f"engine step {failed} failed; see {OUTPUTS}last_run.txt")
    return summary
