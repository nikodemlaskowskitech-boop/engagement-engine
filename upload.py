"""
Copies the engine's inputs to the data bucket on AWS (Phase 6).

    python upload.py              upload what's new or changed
    python upload.py --dry-run    say what would be uploaded, upload nothing
    python upload.py --all        send every input again, changed or not (e.g. so files
                                  uploaded before the bucket had its own key get it too)

Which files: the inputs named in settings.toml [data] (events, CRM, CS team,
CS history, holdout, renewal outcomes), wherever [data] says they live. Where
they go: the bucket in [aws] bucket, under [aws] inputs_prefix, keeping their
folder layout (events/2026-10-15.jsonl -> inputs/events/2026-10-15.jsonl).
Plus the action log (data/action_log.csv: the tasks already sent) under
[aws] state_prefix, so the engine in AWS knows them too.

Only new or changed files go up: each file's MD5 fingerprint is compared with
the bucket's copy. So a rerun sends nothing, and the bucket's versioning keeps a
history of real changes, not of reruns. Where the fingerprint comes from: with
S3's own encryption, the ETag S3 lists is the file's MD5. With our KMS key it
isn't (S3 then makes the ETag from the encrypted bytes), so each upload also
stores its MD5 as a label on the file (metadata "md5"), and a file whose ETag
doesn't match is looked up once to read that label.

When something went up, the last file written is a small marker
(inputs/_upload_complete.json, name in [aws] upload_marker). It tells AWS the
batch is complete: the check Lambda wakes on it, once per upload, never
halfway through. A rerun with nothing new writes no marker and wakes nothing.

Demo data only: your own exports (settings.local.toml [data] inputs pointing
elsewhere) stay on your machine, as Phase 4d promises. upload.py refuses them.

Sign in first: aws login --profile cairnpath (the profile in [aws]).
"""

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone

from settings import DEMO, INPUTS, SETTINGS

AWS = SETTINGS["aws"]
DATA = SETTINGS["data"]
PREFIX = AWS["inputs_prefix"].strip("/") + "/"
MARKER = PREFIX + AWS["upload_marker"]
STATE = AWS["state_prefix"].strip("/") + "/"
STATE_FILES = ["action_log.csv"]   # state the engine carries between runs (tasks sent, from actions.py)
SHOW = 10                    # file names printed per run; the rest is a count


def local_inputs():
    """Every input file that exists: {path in the bucket: file on disk}."""
    files = {}
    for key in ("crm_file", "team_file", "holdout_file", "outcomes_file"):
        path = INPUTS / DATA[key]
        if path.is_file():
            files[path] = True
    for key in ("events_glob", "engagement_glob"):
        for path in sorted(INPUTS.glob(DATA[key])):
            files[path] = True
    # as_posix(): S3 keys use "/", even on Windows (where paths use "\")
    out = {PREFIX + p.relative_to(INPUTS).as_posix(): p for p in files}
    for name in STATE_FILES:
        if (INPUTS / name).is_file():
            out[STATE + name] = INPUTS / name
    return out


def md5(path):
    """The file's MD5, as S3 shows it in the ETag of a single-part upload."""
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def remote_etags(s3, bucket):
    """What the bucket holds under the inputs and state prefixes: {key: ETag}."""
    etags = {}
    for prefix in (PREFIX, STATE):
        pages = s3.get_paginator("list_objects_v2").paginate(
            Bucket=bucket, Prefix=prefix, ExpectedBucketOwner=str(AWS["account_id"]))
        for page in pages:
            for obj in page.get("Contents", []):
                etags[obj["Key"]] = obj["ETag"].strip('"')
    return etags


def plan(local, remote, stored_md5=lambda key: None, everything=False):
    """Split the local files into new, changed and unchanged. stored_md5(key): the MD5
    label the bucket's copy carries (asked only when its ETag isn't the MD5)."""
    new, changed, same = [], [], []
    for key, path in local.items():
        if key not in remote:
            new.append(key)
        elif everything:
            changed.append(key)
        else:
            mine = md5(path)
            if remote[key] == mine or stored_md5(key) == mine:
                same.append(key)
            else:
                changed.append(key)
    return new, changed, same


def run(s3, dry_run=False, demo=None, everything=False):
    if not (DEMO if demo is None else demo):
        print(f"Not uploading: {INPUTS} is your own data (settings.local.toml), and it stays on "
              "this machine. upload.py sends the demo data only.")
        return 1
    bucket = AWS["bucket"]
    local = local_inputs()
    if not local:
        print(f"No inputs found in {INPUTS}. Run generate_events.py first?")
        return 1
    owner = str(AWS["account_id"])

    def stored_md5(key):
        return s3.head_object(Bucket=bucket, Key=key, ExpectedBucketOwner=owner).get("Metadata", {}).get("md5")

    new, changed, same = plan(local, remote_etags(s3, bucket), stored_md5, everything)
    print(f"Bucket {bucket}, folder {PREFIX}: {len(new)} new, {len(changed)} changed, "
          f"{len(same)} unchanged ({len(local)} input files)")
    todo = new + changed
    for i, key in enumerate(todo):
        if i < SHOW:
            print(f"  {'would upload' if dry_run else 'upload'}  {key}"
                  f"{'  (changed)' if key in changed else ''}")
        elif i == SHOW:
            print(f"  ... and {len(todo) - SHOW} more")
        if not dry_run:
            # the MD5 goes along as a label, so the next run can tell the file hasn't changed.
            # ExpectedBucketOwner: refuse if the bucket ever belonged to another account.
            # Encryption: the bucket's default (our KMS key), nothing to set here.
            with local[key].open("rb") as body:
                s3.put_object(Bucket=bucket, Key=key, Body=body, Metadata={"md5": md5(local[key])},
                              ExpectedBucketOwner=owner)
    if dry_run:
        print("Dry run: nothing uploaded." + (" Would then write the upload-complete marker." if todo else ""))
    elif todo:
        # last, so the check in AWS only ever sees a complete batch
        marker = {"uploaded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                  "new": len(new), "changed": len(changed), "files": len(local)}
        s3.put_object(Bucket=bucket, Key=MARKER, Body=json.dumps(marker).encode("utf-8"),
                      ExpectedBucketOwner=str(AWS["account_id"]))
        print(f"Done. Marker written ({MARKER}): the input check runs in AWS now; "
              f"its report lands in {AWS['reports_prefix']}check_inputs/latest.txt in a minute.")
    return 0


def client():
    """An S3 client signed in with the [aws] profile (aws login)."""
    import boto3
    session = boto3.Session(profile_name=AWS.get("profile") or None, region_name=AWS["region"])
    return session.client("s3")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Copy the engine's inputs to the S3 data bucket.")
    ap.add_argument("--dry-run", action="store_true", help="say what would be uploaded, upload nothing")
    ap.add_argument("--all", action="store_true", help="send every input again, changed or not")
    args = ap.parse_args()
    sys.exit(run(client(), args.dry_run, everything=args.all))
