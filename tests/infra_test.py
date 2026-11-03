"""
Infrastructure test: does the CDK code describe a safe bucket, before anything
reaches AWS? Builds the CloudFormation template in memory (no AWS login, no
deploy, no cost) and checks it.

    python tests/infra_test.py

Needs aws-cdk-lib and constructs (requirements.txt) and Node.js (CDK runs on
it). Exit 1 on any FAIL. Run it on every PR that touches infra/.
"""

import json
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

print("\nCheck Lambda: wakes on the marker, least privilege")
aws = SETTINGS["aws"]
fns = {k: v for k, v in t.find_resources("AWS::Lambda::Function").items()
       if v["Properties"].get("Handler") == "handler.handler"}
fn_id, fn = next(iter(fns.items()), (None, {}))
props = fn.get("Properties", {})
check(len(fns) == 1, "one check function", f"{len(fns)} found")
check(props.get("Runtime") == "python3.13" and props.get("Architectures") == ["arm64"],
      "Python 3.13 on Graviton (arm64)", f"{props.get('Runtime')} {props.get('Architectures')}")
check(props.get("MemorySize") == aws["check_memory_mb"] and props.get("Timeout") == aws["check_timeout_seconds"]
      and props.get("EphemeralStorage", {}).get("Size") == aws["check_disk_mb"],
      "memory, timeout and disk from settings.toml [aws]")
env = props.get("Environment", {}).get("Variables", {})
check(env.get("INPUTS_PREFIX") == aws["inputs_prefix"] and env.get("MARKER_NAME") == aws["upload_marker"]
      and env.get("REPORTS_PREFIX") == aws["reports_prefix"] + "check_inputs/"
      and env.get("ACCOUNT_ID") == str(aws["account_id"]) and "Ref" in str(env.get("BUCKET")),
      "told the bucket, prefixes, marker and account (no hard-coded bucket name)", env)
r = has(t, "AWS::Logs::LogGroup", {"RetentionInDays": 30})
check(r is True, "logs kept 30 days, then deleted (no cost pile-up)", r)
notes = t.find_resources("Custom::S3BucketNotifications")
cfg = next(iter(notes.values()), {}).get("Properties", {}).get("NotificationConfiguration", {})
lam = cfg.get("LambdaFunctionConfigurations", [])
rules = [r["Filter"]["Key"]["FilterRules"] for r in lam] if lam else []
check(len(lam) == 1 and lam[0]["Events"] == ["s3:ObjectCreated:*"]
      and rules[0] == [{"Name": "prefix", "Value": aws["inputs_prefix"] + aws["upload_marker"]}],
      "wakes only on the upload-complete marker (one run per upload, never on its own reports)", lam)
policies = [v for v in t.find_resources("AWS::IAM::Policy").values()
            if any(r.get("Ref") == props.get("Role", {}).get("Fn::GetAtt", [None])[0]
                   for r in v["Properties"].get("Roles", []))]
stmts = [s for p in policies for s in p["Properties"]["PolicyDocument"]["Statement"]]
actions = [a for s in stmts for a in ([s["Action"]] if isinstance(s["Action"], str) else s["Action"])]
text = json.dumps(stmts)
check(bool(stmts) and "s3:*" not in actions and not any(a.startswith("s3:Delete") for a in actions),
      "its role has no s3:* and no delete", actions)
check("/" + aws["inputs_prefix"] + "*" in text and "/" + aws["reports_prefix"] + "check_inputs/*" in text,
      "reads only inputs/*, writes only reports/check_inputs/*")
puts = [s for s in stmts if any(a.startswith("s3:PutObject") for a in
        ([s["Action"]] if isinstance(s["Action"], str) else s["Action"]))]
check(bool(puts) and all('"/' + aws["inputs_prefix"] + '*"' not in json.dumps(s["Resource"]) for s in puts),
      "can't write to inputs/ (so it can never trigger itself)")
check("CheckFunction" in t.find_outputs("*"), "prints the function name after deploy")

print("\nFAILED" if failed else "\nOK")
sys.exit(1 if failed else 0)
