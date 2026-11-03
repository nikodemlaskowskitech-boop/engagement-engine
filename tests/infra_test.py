"""
Infrastructure test: does the CDK code describe a safe bucket, before anything
reaches AWS? Builds the CloudFormation template in memory (no AWS login, no
deploy, no cost) and checks it.

    python tests/infra_test.py

Needs aws-cdk-lib and constructs (requirements.txt) and Node.js (CDK runs on
it). Exit 1 on any FAIL. Run it on every PR that touches infra/.
"""

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "infra")]

import aws_cdk as cdk                                   # noqa: E402
from aws_cdk.assertions import Match, Template          # noqa: E402

from data_stack import DataStack                        # noqa: E402
from settings import SETTINGS                           # noqa: E402

failed = 0


def check(ok, name, detail=""):
    global failed
    failed += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ->  {detail}" if not ok and detail else ""))


def has(template, kind, props):
    """True when the template has a resource of this kind with these properties."""
    try:
        template.has_resource_properties(kind, props)
        return True
    except Exception as e:           # the assertion error explains what differed
        return str(e)[:300]


app = cdk.App()
stack = DataStack(app, "Test", env=cdk.Environment(account=str(SETTINGS["aws"]["account_id"]),
                                                   region=SETTINGS["aws"]["region"]))
t = Template.from_stack(stack)
buckets = t.find_resources("AWS::S3::Bucket")
bucket = next(iter(buckets.values()), {})

print("\nData bucket: safe by default")
check(len(buckets) == 1, "exactly one bucket", f"{len(buckets)} found")
r = has(t, "AWS::S3::Bucket", {"BucketEncryption": {"ServerSideEncryptionConfiguration": [
    {"ServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]}})
check(r is True, "encrypted at rest", r)
r = has(t, "AWS::S3::Bucket", {"PublicAccessBlockConfiguration": {
    "BlockPublicAcls": True, "BlockPublicPolicy": True, "IgnorePublicAcls": True, "RestrictPublicBuckets": True}})
check(r is True, "all four public-access blocks on", r)
r = has(t, "AWS::S3::Bucket", {"VersioningConfiguration": {"Status": "Enabled"}})
check(r is True, "versioned (an overwrite keeps the old copy)", r)
r = has(t, "AWS::S3::Bucket", {"OwnershipControls": {"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]}})
check(r is True, "ACLs off (access only by policy)", r)
r = has(t, "AWS::S3::BucketPolicy", {"PolicyDocument": {"Statement": Match.array_with([Match.object_like({
    "Effect": "Deny", "Condition": {"Bool": {"aws:SecureTransport": "false"}}})])}})
check(r is True, "plain HTTP refused (HTTPS only)", r)
keep = SETTINGS["aws"]["keep_old_versions_days"]
r = has(t, "AWS::S3::Bucket", {"LifecycleConfiguration": {"Rules": Match.array_with([Match.object_like({
    "NoncurrentVersionExpiration": {"NoncurrentDays": keep}, "Status": "Enabled"})])}})
check(r is True, f"old versions expire after {keep} days (settings.toml [aws])", r)
check(bucket.get("DeletionPolicy") == "Retain" and bucket.get("UpdateReplacePolicy") == "Retain",
      "kept if the stack is deleted (Retain)", f"{bucket.get('DeletionPolicy')}/{bucket.get('UpdateReplacePolicy')}")
check("BucketName" not in bucket.get("Properties", {}), "name left to AWS (no hard-coded bucket name)")
check(stack.region == SETTINGS["aws"]["region"] and stack.account == str(SETTINGS["aws"]["account_id"]),
      f"account and region from settings.toml ({stack.account}, {stack.region})")
check("BucketName" in t.find_outputs("*"), "prints the bucket name after deploy")

print("\nFAILED" if failed else "\nOK")
sys.exit(1 if failed else 0)
