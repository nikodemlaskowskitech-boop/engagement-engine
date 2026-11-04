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
          "SlackListenerServiceRole": "slack",
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
            is_role_name = name in ("check", "engine", "schedule", "crawler", "slack")
            if r["LogicalResourceId"].startswith(prefix) and name not in found and (
                    (kind == "AWS::IAM::Role") == is_role_name):
                found[name] = r["PhysicalResourceId"]
                break
    missing = set(WANTED.values()) - set(found)
    if missing:
        sys.exit(f"Not found in stack {STACK}: {', '.join(sorted(missing))}. Deployed?")
    return found


# an extra identity policy for the perimeter cases: "this identity may do anything in S3"
ALLOW_ALL_S3 = json.dumps({"Version": "2012-10-17", "Statement": [
    {"Effect": "Allow", "Action": "s3:*", "Resource": "*"}]})


def cases(account, region):
    """(role, action, resource, expected, why, plain_http[, extra identity policy])"""
    b = f"arn:aws:s3:::{AWS['bucket']}"
    i, r, o, st = AWS["inputs_prefix"], AWS["reports_prefix"] + "check_inputs/", AWS["outputs_prefix"], AWS["state_prefix"]
    key = f"arn:aws:ssm:{region}:{account}:parameter{AWS['api_key_parameter']}"
    other = f"arn:aws:ssm:{region}:{account}:parameter/cairnpath/some-other-secret"
    signing = f"arn:aws:ssm:{region}:{account}:parameter{AWS['slack_signing_secret_parameter']}"
    bot = f"arn:aws:ssm:{region}:{account}:parameter{AWS['slack_bot_token_parameter']}"
    clicks = AWS["slack_clicks_prefix"]
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
        # the Slack listener: public-facing, so the narrowest of all
        ("slack", "s3:GetObject", f"{b}/{i}crm_accounts.csv", DENY, "can't read any data", False),
        ("slack", "s3:PutObject", f"{b}/{i}events/forged.jsonl", DENY, "can't write the inputs", False),
        ("slack", "s3:PutObject", f"{b}/{st}action_log.csv", DENY, "can't touch the action log, only add clicks", False),
        ("slack", "s3:PutObject", f"{b}/{o}run.json", DENY, "can't write the engine's results", False),
        ("slack", "ssm:GetParameter", key, DENY, "can't read the API key", False),
        ("slack", "s3:PutObject", f"{b}/{clicks}2026-10-19/test.json", ALLOW, "control: it can save a click", False),
        ("slack", "ssm:GetParameter", signing, ALLOW, "control: it can read Slack's signing secret", False),
        ("engine", "ssm:GetParameter", bot, DENY, "the engine can't read Slack's bot token", False),
        # the data perimeter (bucket policy): even an identity in this account with s3:* on
        # everything (simulated: the schedule role plus an extra allow-all policy) is refused
        ("schedule", "s3:GetObject", f"{b}/{i}crm_accounts.csv", DENY,
         "perimeter: another identity with s3:* still can't read the data", False, ALLOW_ALL_S3),
        ("schedule", "s3:PutObject", f"{b}/{o}run.json", DENY,
         "perimeter: ...nor write it", False, ALLOW_ALL_S3),
        ("admin", "s3:GetObject", f"{b}/{i}crm_accounts.csv", ALLOW, "control: the admin can still read the data", False),
        ("admin", "s3:PutBucketPolicy", b, ALLOW, "control: the admin can still fix the bucket's rules (no lockout)", False),
    ]


def run(cfn, iam, s3, account, region):
    names = find(cfn)
    arns = {k: f"arn:aws:iam::{account}:role/{v}" for k, v in names.items()}
    # the admin role (what aws login gives you): its real ARN, path included
    arns["admin"] = iam.get_role(RoleName=AWS["admin_role_names"][0])["Role"]["Arn"]
    bucket_policy = s3.get_bucket_policy(Bucket=AWS["bucket"], ExpectedBucketOwner=account)["Policy"]
    results = []
    for case in cases(account, region):
        role, action, resource, expected, why, plain_http = case[:6]
        resource = resource.format(**names)
        kw = {"PolicySourceArn": arns[role],
              "ActionNames": [action], "ResourceArns": [resource],
              # what a real request carries: its region, whether it uses HTTPS, and who's asking
              # (the bucket's perimeter decides on aws:PrincipalArn: without it, it denies everyone)
              "ContextEntries": [{"ContextKeyName": "aws:SecureTransport", "ContextKeyType": "boolean",
                                  "ContextKeyValues": ["false" if plain_http else "true"]},
                                 {"ContextKeyName": "aws:RequestedRegion", "ContextKeyType": "string",
                                  "ContextKeyValues": [region]},
                                 {"ContextKeyName": "aws:PrincipalArn", "ContextKeyType": "string",
                                  "ContextKeyValues": [arns[role]]},
                                 {"ContextKeyName": "aws:PrincipalAccount", "ContextKeyType": "string",
                                  "ContextKeyValues": [account]},
                                 {"ContextKeyName": "aws:PrincipalIsAWSService", "ContextKeyType": "boolean",
                                  "ContextKeyValues": ["false"]}]}
        if len(case) > 6:                     # an extra identity policy (the perimeter cases)
            kw["PolicyInputList"] = [case[6]]
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
