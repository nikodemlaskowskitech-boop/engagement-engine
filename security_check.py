"""
Live security checks: asks AWS whether each deployed role can do what it
shouldn't. Read-only: nothing is created, changed or run.

    python security_check.py

infra_test.py checks the *template* before a deploy. This checks what is
*actually deployed*, with the IAM policy simulator: for each case it asks AWS
"would this role be allowed to do this, on this resource?", using the role's
real policies and the bucket's real policy. Every "should be denied" case must
come back denied, and a few "should be allowed" cases must come back allowed
(a check that denies everything would prove nothing).

The simulator knows nothing about the request beyond what it's told. So each
question also says which region it's made in (aws:RequestedRegion) and whether
it uses HTTPS (aws:SecureTransport), like a real request would. Without the
region, the organization's region guardrail (an SCP that denies everything
outside the allowed regions) matches every question and denies it all.

Sign in first: aws login --profile cairnpath. Exit 1 on any FAIL.
"""

import json
import sys

from settings import SETTINGS

AWS = SETTINGS["aws"]
STACK = "CairnpathData"
DENY, ALLOW = "denied", "allowed"
# CloudFormation logical ID prefix -> short name used in the cases
WANTED = {"CheckInputsServiceRole": "check", "RunEngineServiceRole": "engine",
          "ScheduleRole": "schedule", "CrawlerRole": "crawler",
          "CheckInputs": "check_fn", "RunEngine": "engine_fn"}


def find(cfn):
    """The deployed roles and functions, by short name: {name: physical id}."""
    found = {}
    for r in cfn.describe_stack_resources(StackName=STACK)["StackResources"]:
        kind = r["ResourceType"]
        if kind not in ("AWS::IAM::Role", "AWS::Lambda::Function"):
            continue
        for prefix, name in WANTED.items():
            # longest prefix wins: "CheckInputsServiceRole..." is the role, "CheckInputs..." the function
            is_role_name = name in ("check", "engine", "schedule", "crawler")
            if r["LogicalResourceId"].startswith(prefix) and name not in found and (
                    (kind == "AWS::IAM::Role") == is_role_name):
                found[name] = r["PhysicalResourceId"]
                break
    missing = set(WANTED.values()) - set(found)
    if missing:
        sys.exit(f"Not found in stack {STACK}: {', '.join(sorted(missing))}. Deployed?")
    return found


def cases(account, region):
    """(role, action, resource, expected, why, plain_http)"""
    b = f"arn:aws:s3:::{AWS['bucket']}"
    i, r, o, st = AWS["inputs_prefix"], AWS["reports_prefix"] + "check_inputs/", AWS["outputs_prefix"], AWS["state_prefix"]
    key = f"arn:aws:ssm:{region}:{account}:parameter{AWS['api_key_parameter']}"
    other = f"arn:aws:ssm:{region}:{account}:parameter/cairnpath/some-other-secret"
    fn = lambda name: f"arn:aws:lambda:{region}:{account}:function:{{{name}}}"   # filled in later
    return [
        # the engine
        ("engine", "s3:PutObject", f"{b}/{i}events/forged.jsonl", DENY, "can't change its own inputs", False),
        ("engine", "s3:PutObject", f"{b}/{r}latest.json", DENY, "can't fake the input check's verdict", False),
        ("engine", "s3:DeleteObject", f"{b}/{o}run.json", DENY, "can't delete anything", False),
        ("engine", "ssm:GetParameter", other, DENY, "can read the API key, but no other secret", False),
        ("engine", "s3:GetObject", f"{b}/{i}crm_accounts.csv", DENY, "plain HTTP refused, even for its own inputs", True),
        ("engine", "s3:PutObject", f"{b}/{o}run.json", ALLOW, "control: it can write its results", False),
        ("engine", "ssm:GetParameter", key, ALLOW, "control: it can read the API key", False),
        # the input check
        ("check", "s3:PutObject", f"{b}/{i}_upload_complete.json", DENY, "can't write where it's triggered (no loop)", False),
        ("check", "s3:PutObject", f"{b}/{o}run.json", DENY, "can't write the engine's results", False),
        ("check", "ssm:GetParameter", key, DENY, "can't read the API key", False),
        ("check", "s3:PutBucketPolicy", b, DENY, "can't change the bucket's rules", False),
        ("check", "s3:PutObject", f"{b}/{r}latest.json", ALLOW, "control: it can write its report", False),
        # the schedule
        ("schedule", "lambda:InvokeFunction", fn("check_fn"), DENY, "can start the engine, nothing else", False),
        ("schedule", "s3:GetObject", f"{b}/{st}action_log.csv", DENY, "no access to the data at all", False),
        ("schedule", "lambda:InvokeFunction", fn("engine_fn"), ALLOW, "control: it can start the engine", False),
        # the Glue crawler
        ("crawler", "s3:PutObject", f"{b}/{o}tables/x/x.parquet", DENY, "read-only", False),
        ("crawler", "s3:GetObject", f"{b}/{i}crm_accounts.csv", DENY, "reads the result tables, not the raw inputs", False),
        ("crawler", "s3:GetObject", f"{b}/{o}tables/crm/crm.parquet", ALLOW, "control: it can read the tables", False),
    ]


def run(cfn, iam, s3, account, region):
    names = find(cfn)
    bucket_policy = s3.get_bucket_policy(Bucket=AWS["bucket"], ExpectedBucketOwner=account)["Policy"]
    results = []
    for role, action, resource, expected, why, plain_http in cases(account, region):
        resource = resource.format(**names)
        kw = {"PolicySourceArn": f"arn:aws:iam::{account}:role/{names[role]}",
              "ActionNames": [action], "ResourceArns": [resource],
              # what a real request carries: its region and whether it uses HTTPS
              "ContextEntries": [{"ContextKeyName": "aws:SecureTransport", "ContextKeyType": "boolean",
                                  "ContextKeyValues": ["false" if plain_http else "true"]},
                                 {"ContextKeyName": "aws:RequestedRegion", "ContextKeyType": "string",
                                  "ContextKeyValues": [region]}]}
        if action.startswith("s3:"):          # the bucket's own policy counts too (e.g. HTTPS only)
            kw.update(ResourcePolicy=bucket_policy, ResourceOwner=f"arn:aws:iam::{account}:root")
        result = iam.simulate_principal_policy(**kw)["EvaluationResults"][0]
        decision = result["EvalDecision"]
        got = ALLOW if decision == "allowed" else DENY
        # which policy decided it (an SCP, the role's own policy, the bucket policy): for the report
        by = sorted({f"{m.get('SourcePolicyType', '?')} {m.get('SourcePolicyId', '')}".strip()
                     for m in result.get("MatchedStatements", [])})
        results.append({"role": role, "action": action, "resource": resource.split(":::")[-1].split(":")[-1],
                        "expected": expected, "got": got, "aws_said": decision, "decided_by": by,
                        "why": why, "ok": got == expected})
    return results


def report(results):
    print(f"\nLive security checks on stack {STACK} (IAM policy simulator, nothing changed)\n")
    for r in results:
        print(f"  {'PASS' if r['ok'] else 'FAIL'}  {r['role']:<8} {r['action']:<22} {r['expected']:<8} "
              f"{r['why']}" + ("" if r["ok"] else f"  ->  AWS said {r['aws_said']} ({', '.join(r['decided_by']) or 'no statement matched'}): {r['resource']}"))
    bad = sum(not r["ok"] for r in results)
    denied = sum(r["expected"] == DENY for r in results)
    print(f"\n{len(results) - bad} of {len(results)} as expected ({denied} things that must be refused, "
          f"{len(results) - denied} controls that must work).")
    print("\nFAILED" if bad else "\nOK")
    return bad


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    import boto3
    session = boto3.Session(profile_name=AWS.get("profile") or None, region_name=AWS["region"])
    results = run(session.client("cloudformation"), session.client("iam"), session.client("s3"),
                  str(AWS["account_id"]), AWS["region"])
    sys.exit(1 if report(results) else 0)
