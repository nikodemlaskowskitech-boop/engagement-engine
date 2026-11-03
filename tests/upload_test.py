"""
Upload test: does upload.py send only what's new or changed, to the right place?

    python tests/upload_test.py

No AWS and no cost: a stand-in S3 keeps the uploads in memory. Works on small
made-up inputs in a temporary folder. Exit 1 on any FAIL.
"""

import hashlib
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import upload    # noqa: E402


class FakeS3:
    """Enough of S3 for upload.py: list (with pages), put, and the owner check."""

    def __init__(self, owner):
        self.objects, self.puts, self.owner = {}, [], owner

    def _check(self, kw):
        if kw.get("ExpectedBucketOwner") != self.owner:
            raise PermissionError("wrong or missing ExpectedBucketOwner")

    def get_paginator(self, name):
        s3 = self

        class Pages:
            def paginate(self, Bucket, Prefix, **kw):
                s3._check(kw)
                keys = sorted(k for k in s3.objects if k.startswith(Prefix))
                for i in range(0, max(len(keys), 1), 2):           # 2 per page: paging must work
                    yield {"Contents": [{"Key": k, "ETag": f'"{s3.objects[k]}"'} for k in keys[i:i + 2]]}
        return Pages()

    def put_object(self, Bucket, Key, Body, **kw):
        self._check(kw)
        data = Body if isinstance(Body, bytes) else Body.read()      # S3 takes bytes or a file
        self.objects[Key] = hashlib.md5(data).hexdigest()
        self.puts.append(Key)


failed = 0


def check(ok, name, detail=""):
    global failed
    failed += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ->  {detail}" if not ok and detail else ""))


def step(s3, dry_run=False):
    """Run upload.py; return the input files it sent (the marker is checked on its own)."""
    before = len(s3.puts)
    upload.run(s3, dry_run, demo=True)
    return [k for k in s3.puts[before:] if k != upload.MARKER]


def markers(s3):
    return s3.puts.count(upload.MARKER)


tmp = Path(tempfile.mkdtemp())
(tmp / "events").mkdir()
(tmp / "engagement").mkdir()
for day in ("2026-10-01", "2026-10-02", "2026-10-03"):
    (tmp / "events" / f"{day}.jsonl").write_text(f'{{"day": "{day}"}}\n', encoding="utf-8")
(tmp / "engagement" / "2026-10-01.jsonl").write_text("{}\n", encoding="utf-8")
(tmp / "crm_accounts.csv").write_text("account_id\nacc_001\n", encoding="utf-8")
(tmp / "cs_team.csv").write_text("owner_id\ncsm_1\n", encoding="utf-8")
(tmp / "answer_key.csv").write_text("not an input\n", encoding="utf-8")     # must never go up
(tmp / "action_log.csv").write_text("tasks sent\n", encoding="utf-8")       # state: goes to state/
(tmp / "flags_churn.csv").write_text("an output\n", encoding="utf-8")      # an engine output: stays local
upload.INPUTS = tmp
owner = str(upload.AWS["account_id"])
s3 = FakeS3(owner)

print("\nUpload: only new or changed inputs, to the right place")
sent = step(s3)
check(len(sent) == 7, "first run uploads every input (4 day files + CRM + team) and the action log", sent)
check(all(k.startswith(upload.PREFIX) for k in sent if k != upload.STATE + "action_log.csv"),
      f"inputs under {upload.PREFIX}")
check(upload.STATE + "action_log.csv" in sent, f"the action log under {upload.STATE} (the engine's memory of tasks sent)")
check("inputs/events/2026-10-02.jsonl" in sent, "keeps the folder layout, with / not \\", sent)
check(not any("answer_key" in k or "flags_churn" in k for k in sent),
      "the answer key and the engine's outputs are not uploaded")
check(markers(s3) == 1 and s3.puts[-1] == upload.MARKER,
      "the upload-complete marker is written once, and last (the check sees a whole batch)")
check(step(s3) == [] and markers(s3) == 1, "second run, nothing changed: nothing uploaded, no marker")

(tmp / "events" / "2026-10-03.jsonl").write_text('{"day": "edited"}\n', encoding="utf-8")
(tmp / "events" / "2026-10-04.jsonl").write_text('{"day": "new"}\n', encoding="utf-8")
check(step(s3, dry_run=True) == [] and markers(s3) == 1, "--dry-run uploads nothing, writes no marker")
sent = step(s3)
check(sorted(sent) == ["inputs/events/2026-10-03.jsonl", "inputs/events/2026-10-04.jsonl"],
      "a changed file and a new file: just those two", sent)
check(markers(s3) == 2, "...then a new marker, so the check runs again")
check(upload.MARKER not in upload.local_inputs(), "the marker is never treated as an input")

before = len(s3.puts)
code = upload.run(s3, demo=False)
check(code == 1 and len(s3.puts) == before, "your own data (not the demo) is refused: nothing leaves the laptop")

try:
    upload.run(FakeS3("111111111111"), demo=True)
    check(False, "refuses a bucket owned by another account")
except PermissionError:
    check(True, "refuses a bucket owned by another account (ExpectedBucketOwner)")

shutil.rmtree(tmp)
print("\nFAILED" if failed else "\nOK")
sys.exit(1 if failed else 0)
