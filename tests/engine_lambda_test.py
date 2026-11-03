"""
Engine Lambda test: does the handler that runs the engine in AWS gate, run and
upload correctly, without AWS?

    python tests/engine_lambda_test.py

Builds the Lambda's folder as the deploy does (infra/bundle.py, without
DuckDB), then runs its handler against a stand-in S3 holding the demo inputs.
  - every Python file the steps import is in the bundle (nothing missing in AWS)
  - the gate: an ERROR from the input check means SKIPPED, nothing computed
  - a run: inputs and the action log in, only new files out, all under outputs/,
    run.json written last; a failing step gives FAILED and raises
If DuckDB is installed here, the real engine steps run and their results must
match the laptop's own data/ files; otherwise stand-in steps check the plumbing.
Needs data/ (run .\\run_all.ps1 first). Exit 1 on any FAIL.
"""

import ast
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "infra")]

from bundle import ENGINE_FILES, bundle_engine     # noqa: E402
from settings import SETTINGS                      # noqa: E402

AWS, D = SETTINGS["aws"], SETTINGS["data"]
OWNER = str(AWS["account_id"])
INPUTS, STATE, OUTPUTS = AWS["inputs_prefix"], AWS["state_prefix"], AWS["outputs_prefix"]
CHECK = AWS["reports_prefix"] + "check_inputs/latest.json"
MARKER = AWS["upload_marker"]
REAL = importlib.util.find_spec("duckdb") is not None

failed = 0


def check(ok, name, detail=""):
    global failed
    failed += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ->  {detail}" if not ok and detail else ""))


class FakeS3:
    """A bucket in memory: {key: bytes}. Records the order of writes."""

    def __init__(self, objects):
        self.objects, self.writes = dict(objects), []

    def _owner(self, owner):
        if owner != OWNER:
            raise PermissionError("wrong or missing ExpectedBucketOwner")

    def get_object(self, Bucket, Key, ExpectedBucketOwner=None):
        self._owner(ExpectedBucketOwner)
        import io
        return {"Body": io.BytesIO(self.objects[Key])}

    def get_paginator(self, name):
        s3 = self

        class Pages:
            def paginate(self, Bucket, Prefix, ExpectedBucketOwner=None):
                s3._owner(ExpectedBucketOwner)
                keys = sorted(k for k in s3.objects if k.startswith(Prefix))
                for i in range(0, max(len(keys), 1), 100):
                    yield {"Contents": [{"Key": k} for k in keys[i:i + 100]]}
        return Pages()

    def download_file(self, Bucket, Key, Filename, ExtraArgs=None):
        self._owner((ExtraArgs or {}).get("ExpectedBucketOwner"))
        Path(Filename).write_bytes(self.objects[Key])

    def put_object(self, Bucket, Key, Body, ContentType=None, ExpectedBucketOwner=None):
        self._owner(ExpectedBucketOwner)
        self.objects[Key] = Body
        self.writes.append(Key)


def bucket_with_demo(status):
    """The demo inputs as upload.py lays them out, the action log, and the check's verdict."""
    objs, data = {}, REPO / "data"
    for key in ("crm_file", "team_file", "holdout_file", "outcomes_file"):
        if (data / D[key]).exists():
            objs[INPUTS + D[key]] = (data / D[key]).read_bytes()
    for g in (D["events_glob"], D["engagement_glob"]):
        for p in data.glob(g):
            objs[INPUTS + p.relative_to(data).as_posix()] = p.read_bytes()
    objs[INPUTS + MARKER] = b"{}"
    if (data / "action_log.csv").exists():
        objs[STATE + "action_log.csv"] = (data / "action_log.csv").read_bytes()
    objs[CHECK] = json.dumps({"status": status, "errors": 1 if status == "ERROR" else 0,
                              "warnings": 2, "checked_at": "2026-11-03T00:00:00Z"}).encode()
    return objs


tmp = Path(tempfile.mkdtemp())

print("\nEngine Lambda: the bundle")
bundle = bundle_engine(tmp / "bundle", deps=False)
check(sorted(p.name for p in bundle.iterdir()) == sorted(ENGINE_FILES + ["handler.py"]),
      "holds the handler and the engine's files, nothing else (never settings.local.toml)")
check(set(AWS["engine_steps"]) <= set(ENGINE_FILES), "every step in [aws] engine_steps is bundled")


def local_imports(name, seen):
    """Every repo module a script imports, followed recursively."""
    tree = ast.parse((REPO / name).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        mods = ([a.name for a in node.names] if isinstance(node, ast.Import) else
                [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
        for m in mods:
            f = m.split(".")[0] + ".py"
            if (REPO / f).exists() and f not in seen:
                seen.add(f)
                local_imports(f, seen)
    return seen


needed = set()
for step in AWS["engine_steps"]:
    local_imports(step, needed)
check(needed <= set(ENGINE_FILES), "every repo module the steps import is bundled", sorted(needed - set(ENGINE_FILES)))

spec = importlib.util.spec_from_file_location("engine_handler", bundle / "handler.py")
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)
h.BUCKET, h.OWNER, h.CODE = AWS["bucket"], OWNER, ENGINE_FILES
event = {"Records": [{"s3": {"object": {"key": CHECK}}}]}
work = tmp / "work"

print("\nEngine Lambda: the gate")
s3 = FakeS3(bucket_with_demo("ERROR"))
summary = h.handler(event, None, s3=s3, work=work)
check(summary["status"] == "SKIPPED", "the check said ERROR: the engine does not run (SKIPPED)", summary)
check(s3.writes == [OUTPUTS + "run.json"], "...and writes only outputs/run.json, saying why", s3.writes)
check(not work.exists(), "...without downloading anything")

print(f"\nEngine Lambda: a run ({'the real engine, DuckDB found' if REAL else 'stand-in steps: no DuckDB here'})")
if REAL:
    h.STEPS = AWS["engine_steps"]
else:
    # stand-ins that touch the same places: read an input and the action log, write outputs
    stand_in = ("from pathlib import Path\n"
                "assert Path('data/crm_accounts.csv').exists() and Path('data/action_log.csv').exists()\n"
                "assert Path('data/events').is_dir() and not Path('data/" + MARKER + "').exists()\n"
                "Path('data/flags_churn.csv').write_text('account_id,status\\n', encoding='utf-8')\n"
                "Path('data/views_data.json').write_text('{}', encoding='utf-8')\n"
                "Path('data/tables/flags_churn').mkdir(parents=True)\n"
                "Path('data/tables/flags_churn/flags_churn.parquet').write_bytes(b'PAR1\\x00\\xff')\n"
                "print('stand-in step ran')\n")
    (bundle / "stand_in.py").write_text(stand_in, encoding="utf-8")
    h.CODE, h.STEPS = ENGINE_FILES + ["stand_in.py"], ["stand_in.py"]
s3 = FakeS3(bucket_with_demo("WARN"))
summary = h.handler(event, None, s3=s3, work=work)
check(summary["status"] == "OK", "the check said WARN: the engine runs (OK)", summary.get("failed_step"))
check(all(k.startswith(OUTPUTS) for k in s3.writes), "writes only under outputs/ (never inputs/ or reports/: no loops)",
      [k for k in s3.writes if not k.startswith(OUTPUTS)])
check(s3.writes[-1] == OUTPUTS + "run.json", "run.json is written last (whoever reads it sees a finished run)")
out_names = summary["outputs"]
check("views_data.json" in out_names and "flags_churn.csv" in out_names, "uploads what the engine wrote", out_names)
if not REAL:
    check(s3.objects.get(OUTPUTS + "tables/flags_churn/flags_churn.parquet") == b"PAR1\x00\xff",
          "uploads tables in their own folders (outputs/tables/<name>/), binary files intact")
else:
    check(all(OUTPUTS + f"tables/{t}/{t}.parquet" in s3.objects for t in AWS["athena_tables"]),
          "uploads a Parquet table per [aws] athena_tables entry, each in its own folder")
check(not ({"crm_accounts.csv", "action_log.csv", "cs_team.csv"} & set(out_names)),
      "never re-uploads an input or the action log as an output")
check("DONE" in s3.objects[OUTPUTS + "last_run.txt"].decode(), "last_run.txt has every step's output, like run_all")
check(summary["triggered_by"] == CHECK and summary["check"]["status"] == "WARN", "run.json says what woke it and the check's verdict")
check(not work.exists(), "its work folder is cleaned up")
if REAL:
    # byte for byte, order included: a sort with ties can come out in a different
    # order on another machine (account_value.csv did, until account_id broke the ties)
    for name in ("flags_activation.csv", "flags_churn.csv", "flags_expansion.csv", "flags_qbr.csv",
                 "account_value.csv", "flags_journey.csv"):
        same = s3.objects[OUTPUTS + name] == (REPO / "data" / name).read_bytes()
        check(same, f"{name} matches the laptop's, byte for byte")

print("\nEngine Lambda: a step that fails")
(bundle / "broken.py").write_text("raise SystemExit('this step broke')\n", encoding="utf-8")
h.CODE, h.STEPS = ENGINE_FILES + ["broken.py"], ["broken.py"]
s3 = FakeS3(bucket_with_demo("OK"))
try:
    h.handler(event, None, s3=s3, work=work)
    check(False, "a failing step raises (the Lambda shows the error)")
except RuntimeError:
    check(True, "a failing step raises (the Lambda shows the error)")
run = json.loads(s3.objects[OUTPUTS + "run.json"])
check(run["status"] == "FAILED" and run["failed_step"] == "broken.py", "run.json says FAILED and names the step", run)
check("this step broke" in s3.objects[OUTPUTS + "last_run.txt"].decode(), "last_run.txt holds the error")

shutil.rmtree(tmp)
print("\nFAILED" if failed else "\nOK")
sys.exit(1 if failed else 0)
