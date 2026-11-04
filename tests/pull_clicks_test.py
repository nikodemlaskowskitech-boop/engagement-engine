"""
pull_clicks.py test: the Slack clicks from AWS reach the laptop, once, and a
missing AWS session never stops the run. No AWS: a stand-in S3.

    python tests/pull_clicks_test.py

Exit 1 on any FAIL.
"""

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pull_clicks as pc     # noqa: E402

OWNER = str(pc.AWS["account_id"])
failed = 0


def check(ok, name, detail=""):
    global failed
    failed += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ->  {detail}" if not ok and detail else ""))


class FakeS3:
    def __init__(self, objects):
        self.objects, self.writes = objects, 0

    def _owner(self, owner):
        if owner != OWNER:
            raise PermissionError("wrong or missing ExpectedBucketOwner")

    def get_paginator(self, name):
        s3 = self

        class Pages:
            def paginate(self, Bucket, Prefix, ExpectedBucketOwner=None):
                s3._owner(ExpectedBucketOwner)
                yield {"Contents": [{"Key": k} for k in sorted(s3.objects) if k.startswith(Prefix)]}
        return Pages()

    def get_object(self, Bucket, Key, ExpectedBucketOwner=None):
        self._owner(ExpectedBucketOwner)
        return {"Body": io.BytesIO(self.objects[Key])}

    def put_object(self, **kw):
        self.writes += 1


def event(n, ts):
    return json.dumps({"event_id": f"ev-{n}", "event_name": "task_claimed", "timestamp": ts,
                       "account_id": "acc_037", "context": {"source": "slack"}}).encode()


print("\npull_clicks.py")
target = Path(tempfile.mkdtemp()) / "engagement" / "slack_clicks.jsonl"
s3 = FakeS3({pc.CLICKS + "2026-10-19/ev-1.json": event(1, "2026-10-19T14:00:00Z"),
             pc.CLICKS + "2026-10-19/ev-2.json": event(2, "2026-10-19T09:00:00Z"),
             "state/action_log.csv": b"not a click"})
events_dir = target.parent.parent / "events"
events_dir.mkdir(parents=True)
for d in ("2026-10-17", "2026-10-18"):
    (events_dir / f"{d}.jsonl").write_text("{}\n", encoding="utf-8")
count, written = pc.pull(s3, target, events_dir)
lines = target.read_text(encoding="utf-8").splitlines() if target.exists() else []
check(count == 2 and written and [json.loads(l)["event_id"] for l in lines] == ["ev-2", "ev-1"],
      "the clicks land in data/engagement/slack_clicks.jsonl, oldest first (nothing else from state/)", lines)
check(target.name == "slack_clicks.jsonl", "same file name the engine in AWS uses (an upload never doubles a click)")
before = target.stat().st_mtime_ns
count, written = pc.pull(s3, target, events_dir)
check(count == 2 and not written and target.stat().st_mtime_ns == before,
      "nothing new: the file is left alone (no needless upload)")
s3.objects[pc.CLICKS + "2026-11-04/ev-3.json"] = event(3, "2026-11-04T08:00:00Z")
count, written = pc.pull(s3, target, events_dir)
last = next(e for e in map(json.loads, target.read_text(encoding="utf-8").splitlines()) if e["event_id"] == "ev-3")
check(count == 3 and written, "a new click: the file is rewritten with all three")
check(last["event_id"] == "ev-3" and last["timestamp"] == "2026-10-19T08:00:00Z",
      "a click dated by the clock (a card from before run dates) is brought back to the run date", last)
check(s3.writes == 0, "read-only on AWS")


class NoSession:
    def get_paginator(self, name):
        raise RuntimeError("Unable to locate credentials")


out = io.StringIO()
with contextlib.redirect_stdout(out):
    code = pc.main(NoSession())
check(code == 0 and "not pulled" in out.getvalue() and "aws login" in out.getvalue(),
      "not signed in: says how to fix it and carries on (exit 0, run_all goes on)", out.getvalue())

print("\nFAILED" if failed else "\nOK")
sys.exit(1 if failed else 0)
