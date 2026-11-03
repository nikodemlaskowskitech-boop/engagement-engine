"""
Copies the engine's inputs to the data bucket on AWS (Phase 6).

    python upload.py              upload what's new or changed
    python upload.py --dry-run    say what would be uploaded, upload nothing

Which files: the inputs named in settings.toml [data] (events, CRM, CS team,
CS history, holdout, renewal outcomes), wherever [data] says they live. Where
they go: the bucket in [aws] bucket, under [aws] inputs_prefix, keeping their
folder layout (events/2026-10-15.jsonl -> inputs/events/2026-10-15.jsonl).

Only new or changed files go up: each file's MD5 fingerprint is compared with
the one S3 keeps for it (its ETag). So a rerun sends nothing, and the bucket's
versioning keeps a history of real changes, not of reruns.

Sign in first: aws login --profile cairnpath (the profile in [aws]).
"""

import argparse
import hashlib
import sys

from settings import INPUTS, SETTINGS

AWS = SETTINGS["aws"]
DATA = SETTINGS["data"]
PREFIX = AWS["inputs_prefix"].strip("/") + "/"


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
    return {PREFIX + p.relative_to(INPUTS).as_posix(): p for p in files}


def md5(path):
    """The file's MD5, as S3 shows it in the ETag of a single-part upload."""
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def remote_etags(s3, bucket):
    """What the bucket holds under the prefix: {key: MD5}."""
    etags = {}
    pages = s3.get_paginator("list_objects_v2").paginate(
        Bucket=bucket, Prefix=PREFIX, ExpectedBucketOwner=str(AWS["account_id"]))
    for page in pages:
        for obj in page.get("Contents", []):
            etags[obj["Key"]] = obj["ETag"].strip('"')
    return etags


def plan(local, remote):
    """Split the local files into new, changed and unchanged."""
    new, changed, same = [], [], []
    for key, path in local.items():
        if key not in remote:
            new.append(key)
        elif remote[key] != md5(path):
            changed.append(key)
        else:
            same.append(key)
    return new, changed, same


def run(s3, dry_run=False):
    bucket = AWS["bucket"]
    local = local_inputs()
    if not local:
        print(f"No inputs found in {INPUTS}. Run generate_events.py first?")
        return 1
    new, changed, same = plan(local, remote_etags(s3, bucket))
    print(f"Bucket {bucket}, folder {PREFIX}: {len(new)} new, {len(changed)} changed, "
          f"{len(same)} unchanged ({len(local)} input files)")
    for key in new + changed:
        print(f"  {'would upload' if dry_run else 'upload'}  {key}"
              f"{'  (changed)' if key in changed else ''}")
        if not dry_run:
            # put_object: one request per file, so the ETag stays the plain MD5.
            # ExpectedBucketOwner: refuse if the bucket ever belonged to another account.
            with local[key].open("rb") as body:
                s3.put_object(Bucket=bucket, Key=key, Body=body,
                              ExpectedBucketOwner=str(AWS["account_id"]))
    if dry_run:
        print("Dry run: nothing uploaded.")
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
    args = ap.parse_args()
    sys.exit(run(client(), args.dry_run))
