"""
Check Lambda test: does the handler that runs in AWS check the inputs and write
its report, without AWS?

    python tests/check_lambda_test.py

Builds the Lambda's folder exactly as the deploy does (infra/bundle.py), then runs
its handler against a stand-in S3 that serves the demo inputs from data/. Then
again with a broken CRM file (a required column missing): the report must say
ERROR, and the function must still succeed (a data problem is a result, not a
crash). Needs data/ (run .\\run_all.ps1 first). Exit 1 on any FAIL.
"""

import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "infra")]

from bundle import CHECK_FILES, bundle_check_inputs    # noqa: E402
from settings import SETTINGS                          # noqa: E402

AWS = SETTINGS["aws"]
INPUTS, MARKER = AWS["inputs_prefix"], AWS["upload_marker"]
REPORTS = AWS["reports_prefix"] + "check_inputs/"
OWNER = str(AWS["account_id"])


class FakeS3:
    """Serves a local folder as the bucket's inputs/; keeps what the handler writes."""

    def __init__(self, folder):
        self.folder, self.written = folder, {}

    def _owner(self, owner):
        if owner != OWNER:
            raise PermissionError("wrong or missing ExpectedBucketOwner")

    def get_paginator(self, name):
        s3 = self

        class Pages:
            def paginate(self, Bucket, Prefix, ExpectedBucketOwner=None):
                s3._owner(ExpectedBucketOwner)
                keys = [INPUTS + p.relative_to(s3.folder).as_posix()
                        for p in sorted(s3.folder.rglob("*")) if p.is_file()]
                keys.append(INPUTS + MARKER)           # the marker is in the bucket too
                for i in range(0, len(keys), 50):      # pages, like S3 (which pages at 1,000)
                    yield {"Contents": [{"Key": k} for k in keys[i:i + 50]]}
        return Pages()

    def download_file(self, Bucket, Key, Filename, ExtraArgs=None):
        self._owner((ExtraArgs or {}).get("ExpectedBucketOwner"))
        assert Key != INPUTS + MARKER, "the marker must not be downloaded as an input"
        shutil.copy(self.folder / Key[len(INPUTS):], Filename)

    def put_object(self, Bucket, Key, Body, ContentType=None, ExpectedBucketOwner=None):
        self._owner(ExpectedBucketOwner)
        self.written[Key] = Body.decode("utf-8")


failed = 0


def check(ok, name, detail=""):
    global failed
    failed += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ->  {detail}" if not ok and detail else ""))


tmp = Path(tempfile.mkdtemp())
bundle = bundle_check_inputs(tmp / "bundle")
check(sorted(p.name for p in bundle.iterdir()) == sorted(CHECK_FILES + ["handler.py"]),
      "the Lambda's folder holds the handler and check_inputs.py with its settings, nothing else")

spec = importlib.util.spec_from_file_location("handler", bundle / "handler.py")
handler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handler)
handler.BUCKET, handler.OWNER = AWS["bucket"], OWNER
import os                                                # noqa: E402
os.environ["CHECK_FOLDER"] = str(tmp / "work")

# the demo inputs, as upload.py sends them (data/ minus the engine's outputs)
data = tmp / "inputs"
d = SETTINGS["data"]
for key in ("crm_file", "team_file", "holdout_file", "outcomes_file"):
    if (REPO / "data" / d[key]).exists():
        (data).mkdir(exist_ok=True)
        shutil.copy(REPO / "data" / d[key], data)
for glob in (d["events_glob"], d["engagement_glob"]):
    for p in (REPO / "data").glob(glob):
        (data / p.relative_to(REPO / "data")).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(p, data / p.relative_to(REPO / "data"))
n_inputs = sum(1 for p in data.rglob("*") if p.is_file())

print("\nCheck Lambda: the demo inputs")
event = {"Records": [{"s3": {"object": {"key": INPUTS + MARKER}}}]}
s3 = FakeS3(data)
summary = handler.handler(event, None, s3=s3)
check(summary["status"] in ("OK", "WARN") and summary["errors"] == 0,
      f"the demo data passes ({summary['status']}, {summary['warnings']} warnings)", summary)
check(summary["files_checked"] == n_inputs, f"every input checked ({n_inputs}), the marker skipped",
      summary["files_checked"])
check(summary["triggered_by"] == INPUTS + MARKER, "the summary names what woke it")
check(set(s3.written) >= {REPORTS + "latest.txt", REPORTS + "latest.json"}
      and len(s3.written) == 4, "writes latest.txt/json and a dated copy of each", sorted(s3.written))
check(all(k.startswith(REPORTS) for k in s3.written), f"writes only under {REPORTS} (never inputs/: no self-trigger)")
check("What the engine can do with this data" in s3.written[REPORTS + "latest.txt"],
      "the report is check_inputs.py's own report")
check(json.loads(s3.written[REPORTS + "latest.json"]) == summary, "latest.json is the summary")
check(not (tmp / "work").exists(), "its scratch copy of the inputs is cleaned up")

print("\nCheck Lambda: a broken CRM file")
crm = data / d["crm_file"]
rows = crm.read_text(encoding="utf-8").splitlines()
cols = rows[0].split(",")
drop = cols.index("renewal_date")
crm.write_text("\n".join(",".join(c for i, c in enumerate(r.split(",")) if i != drop) for r in rows) + "\n",
               encoding="utf-8")
s3 = FakeS3(data)
summary = handler.handler(event, None, s3=s3)
check(summary["status"] == "ERROR" and summary["errors"] >= 1,
      f"a missing required column gives status ERROR ({summary['errors']} error(s))", summary)
check("renewal_date" in s3.written[REPORTS + "latest.txt"], "the report names the missing column")

shutil.rmtree(tmp)
print("\nFAILED" if failed else "\nOK")
sys.exit(1 if failed else 0)
