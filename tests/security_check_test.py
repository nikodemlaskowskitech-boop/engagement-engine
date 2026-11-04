"""
security_check.py test: does it find the deployed roles, ask the right
questions and fail when AWS says something unexpected? No AWS: stand-ins for
CloudFormation, IAM's policy simulator and S3.

    python tests/security_check_test.py

Exit 1 on any FAIL.
"""

import contextlib
import io
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import security_check as sc    # noqa: E402

ACCOUNT, REGION = "809071263914", "eu-north-1"


class FakeCfn:
    """The stack as CloudFormation lists it, look-alike IDs included (log groups, CDK's helper)."""
    def describe_stack_resources(self, StackName):
        rows = [("CheckInputsServiceRole4A4C9588", "AWS::IAM::Role", "CairnpathData-CheckInputsServiceRole-AAA"),
                ("CheckInputsCEF6170D", "AWS::Lambda::Function", "CairnpathData-CheckInputs-AAA"),
                ("CheckInputsLogs6A4976E7", "AWS::Logs::LogGroup", "logs-check"),
                ("RunEngineServiceRoleB75B8867", "AWS::IAM::Role", "CairnpathData-RunEngineServiceRole-BBB"),
                ("RunEngineE691D882", "AWS::Lambda::Function", "CairnpathData-RunEngine-BBB"),
                ("ScheduleRole756A1A25", "AWS::IAM::Role", "CairnpathData-ScheduleRole-CCC"),
                ("CrawlerRoleA9495AEE", "AWS::IAM::Role", "CairnpathData-CrawlerRole-DDD"),
                ("BucketNotificationsHandler050aRole", "AWS::IAM::Role", "CairnpathData-Handler-EEE")]
        return {"StackResources": [{"LogicalResourceId": a, "ResourceType": b, "PhysicalResourceId": c} for a, b, c in rows]}


class FakeS3:
    def get_bucket_policy(self, Bucket, ExpectedBucketOwner=None):
        assert ExpectedBucketOwner == ACCOUNT
        return {"Policy": json.dumps({"Statement": [{"Effect": "Deny", "Condition": {"Bool": {"aws:SecureTransport": "false"}}}]})}


class FakeIAM:
    """A tiny simulator that answers like a correct deployment, unless told to leak one case."""
    def __init__(self, leak=None):
        self.leak, self.asked = leak, []

    def simulate_principal_policy(self, **kw):
        self.asked.append(kw)
        role, action, res = kw["PolicySourceArn"], kw["ActionNames"][0], kw["ResourceArns"][0]
        http = kw["ContextEntries"][0]["ContextKeyValues"][0] == "false"
        allowed = (("RunEngine" in role and ((action == "s3:PutObject" and "/outputs/" in res)
                                             or (action == "ssm:GetParameter" and "anthropic-api-key" in res)
                                             or (action == "s3:GetObject" and "/inputs/" in res and not http)))
                   or ("CheckInputs" in role and action == "s3:PutObject" and "/reports/check_inputs/" in res)
                   or ("ScheduleRole" in role and action == "lambda:InvokeFunction" and "RunEngine" in res)
                   or ("CrawlerRole" in role and action == "s3:GetObject" and "/outputs/tables/" in res))
        if self.leak and self.leak(role, action, res):
            allowed = True
        return {"EvaluationResults": [{"EvalDecision": "allowed" if allowed else ("explicitDeny" if http else "implicitDeny")}]}


failed = 0


def check(ok, name, detail=""):
    global failed
    failed += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ->  {detail}" if not ok and detail else ""))


def quiet(fn, *a):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a)


print("\nsecurity_check.py")
names = sc.find(FakeCfn())
check(names["check"].endswith("CheckInputsServiceRole-AAA") and names["check_fn"].endswith("CheckInputs-AAA")
      and names["engine"].endswith("RunEngineServiceRole-BBB") and names["engine_fn"].endswith("RunEngine-BBB"),
      "tells roles from functions with the same prefix (CheckInputsServiceRole vs CheckInputs)", names)
iam = FakeIAM()
results = sc.run(FakeCfn(), iam, FakeS3(), ACCOUNT, REGION)
check(all(r["ok"] for r in results), "a correct deployment passes every case", [r for r in results if not r["ok"]])
check(sum(r["expected"] == sc.ALLOW for r in results) >= 4, "it includes controls that must be allowed (not only denials)")
s3_calls = [k for k in iam.asked if k["ActionNames"][0].startswith("s3:")]
check(all("ResourcePolicy" in k and k["ResourceOwner"] == f"arn:aws:iam::{ACCOUNT}:root" for k in s3_calls),
      "S3 cases include the bucket's own policy (so HTTPS-only is tested)")
check(any(k["ContextEntries"][0]["ContextKeyValues"] == ["false"] for k in iam.asked), "one case asks over plain HTTP")
check(all("{" not in k["ResourceArns"][0] for k in iam.asked), "function names filled in from the stack")
leaky = FakeIAM(leak=lambda role, action, res: "RunEngine" in role and action == "s3:PutObject" and "/inputs/" in res)
results = sc.run(FakeCfn(), leaky, FakeS3(), ACCOUNT, REGION)
bad = [r for r in results if not r["ok"]]
check(len(bad) == 1 and "change its own inputs" in bad[0]["why"], "a role that can write its inputs is caught", bad)
check(quiet(sc.report, results) == 1, "...and the report counts it as a failure (exit 1)")
too_tight = FakeIAM(leak=None)
too_tight.simulate_principal_policy = lambda **kw: {"EvaluationResults": [{"EvalDecision": "implicitDeny"}]}
results = sc.run(FakeCfn(), too_tight, FakeS3(), ACCOUNT, REGION)
check(not all(r["ok"] for r in results), "a check that denies everything fails too (the controls catch it)")

print("\nFAILED" if failed else "\nOK")
sys.exit(1 if failed else 0)
