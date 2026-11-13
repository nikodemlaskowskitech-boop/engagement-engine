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
                ("SlackListenerServiceRoleF1A2B3C4", "AWS::IAM::Role", "CairnpathData-SlackListenerServiceRole-FFF"),
                ("SlackListener5D6E7F80", "AWS::Lambda::Function", "CairnpathData-SlackListener-FFF"),
                ("SearchServiceRole0A1B2C3D", "AWS::IAM::Role", "CairnpathData-SearchServiceRole-GGG"),
                ("Search4E5F6A7B", "AWS::Lambda::Function", "CairnpathData-Search-GGG"),
                ("SearchLogsC8D9E0F1", "AWS::Logs::LogGroup", "logs-search"),
                ("BucketNotificationsHandler050aRole", "AWS::IAM::Role", "CairnpathData-Handler-EEE"),
                ("DataKey1F2C3D4E", "AWS::KMS::Key", "1234abcd-12ab-34cd-56ef-1234567890ab"),
                ("Data666C94C7", "AWS::S3::Bucket", "cairnpathdata-data666c94c7-mp5lz9ky9ir4"),
                ("Site1A2B3C4D", "AWS::S3::Bucket", "cairnpathdata-site1a2b3c4d-xyz"),
                ("SiteCdn9F8E7D6C", "AWS::CloudFront::Distribution", "E2ABCDEF")]
        return {"StackResources": [{"LogicalResourceId": a, "ResourceType": b, "PhysicalResourceId": c} for a, b, c in rows]}


SITE = "cairnpathdata-site1a2b3c4d-xyz"


class FakeS3:
    def get_bucket_policy(self, Bucket, ExpectedBucketOwner=None):
        assert ExpectedBucketOwner == ACCOUNT
        sid = "DenyWritesToAnyoneElse" if Bucket == SITE else "DenyDataToAnyoneElse"
        return {"Policy": json.dumps({"Statement": [{"Effect": "Deny", "Condition": {"Bool": {"aws:SecureTransport": "false"}}},
                                                    {"Sid": sid, "Effect": "Deny"}]})}


class FakeKMS:
    def __init__(self):
        self.asked = []

    def get_key_policy(self, KeyId, PolicyName):
        self.asked.append(KeyId)
        # like the real key policy: Resource "*" (which the real simulator refuses)
        return {"Policy": json.dumps({"Statement": [
            {"Sid": "Root", "Effect": "Allow", "Action": "kms:*", "Resource": "*",
             "Principal": {"AWS": f"arn:aws:iam::{ACCOUNT}:root"}},
            {"Sid": "DenyUseOutsideS3", "Effect": "Deny", "Resource": "*"}]})}


ADMIN_ARN = f"arn:aws:iam::{ACCOUNT}:role/aws-reserved/AccountFullAccessRole"
EXEMPT = ("CheckInputs", "RunEngine", "SlackListener", "CrawlerRole", "AccountFullAccessRole")
DATA = ("s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket")


class FakeIAM:
    """A tiny simulator that answers like a correct deployment, unless told to leak one case.
    perimeter=False: the bucket policy has no perimeter (as before 4 Nov 2026)."""
    def __init__(self, leak=None, perimeter=True, key_perimeter=True, site_perimeter=True):
        self.leak, self.asked, self.perimeter, self.key_perimeter = leak, [], perimeter, key_perimeter
        self.site_perimeter = site_perimeter

    def get_role(self, RoleName):
        return {"Role": {"Arn": ADMIN_ARN}}

    def simulate_principal_policy(self, **kw):
        self.asked.append(kw)
        for st in json.loads(kw.get("ResourcePolicy") or '{"Statement": []}').get("Statement", []):
            if st.get("Resource") in ("*", ["*"]):        # what AWS answered on 5 Nov 2026
                raise ValueError("InvalidInput: ResourceNames are not in a valid ARN format: *")
        role, action, res = kw["PolicySourceArn"], kw["ActionNames"][0], kw["ResourceArns"][0]
        ctx = {c["ContextKeyName"]: c["ContextKeyValues"][0] for c in kw["ContextEntries"]}
        http = ctx["aws:SecureTransport"] == "false"
        # like the organization's region guardrail: StringNotEquals on a missing key matches, so it denies
        if ctx.get("aws:RequestedRegion") not in ("eu-north-1", "us-east-1", "us-west-2"):
            return {"EvaluationResults": [{"EvalDecision": "explicitDeny", "MatchedStatements": [
                {"SourcePolicyType": "Organizations SCP", "SourcePolicyId": "RegionFloor"}]}]}
        if (self.perimeter and "DenyDataToAnyoneElse" in kw.get("ResourcePolicy", "") and action.startswith(DATA)
                and not any(x in ctx.get("aws:PrincipalArn", "") for x in EXEMPT)):
            return {"EvaluationResults": [{"EvalDecision": "explicitDeny", "MatchedStatements": [
                {"SourcePolicyType": "Resource Policy", "SourcePolicyId": "DenyDataToAnyoneElse"}]}]}
        if (self.site_perimeter and "DenyWritesToAnyoneElse" in kw.get("ResourcePolicy", "")
                and action in ("s3:PutObject", "s3:DeleteObject")
                and not any(x in ctx.get("aws:PrincipalArn", "") for x in ("RunEngine", "AccountFullAccessRole"))):
            return {"EvaluationResults": [{"EvalDecision": "explicitDeny", "MatchedStatements": [
                {"SourcePolicyType": "Resource Policy", "SourcePolicyId": "DenyWritesToAnyoneElse"}]}]}
        if (self.key_perimeter and action.startswith("kms:") and "DenyUseOutsideS3" in kw.get("ResourcePolicy", "")
                and "kms:ViaService" not in ctx and "AccountFullAccessRole" not in role):
            return {"EvaluationResults": [{"EvalDecision": "explicitDeny", "MatchedStatements": [
                {"SourcePolicyType": "Resource Policy", "SourcePolicyId": "DenyUseOutsideS3"}]}]}
        # like the real simulator: an Allow to the account root in a resource policy reads as
        # "every role in this account may" (in KMS it only hands the decision to IAM)
        if action.startswith("kms:") and f":iam::{ACCOUNT}:root" in kw.get("ResourcePolicy", ""):
            return {"EvaluationResults": [{"EvalDecision": "allowed", "MatchedStatements": [
                {"SourcePolicyType": "Resource Policy", "SourcePolicyId": "ResourcePolicy"}]}]}
        if "AccountFullAccessRole" in role:                  # the admin may do anything else
            return {"EvaluationResults": [{"EvalDecision": "allowed"}]}
        if any('"s3:*"' in p for p in kw.get("PolicyInputList", [])) and action.startswith("s3:") and not http:
            return {"EvaluationResults": [{"EvalDecision": "allowed"}]}
        allowed = (("RunEngine" in role and ((action == "s3:PutObject" and "/outputs/" in res)
                                             or (action == "s3:PutObject" and f":::{SITE}/" in res and not http)
                                             or (action == "ssm:GetParameter" and "anthropic-api-key" in res)
                                             or (action == "s3:GetObject" and "/inputs/" in res and not http)
                                             or action == "kms:Decrypt"))
                   or ("CheckInputs" in role and action == "s3:PutObject" and "/reports/check_inputs/" in res)
                   or ("ScheduleRole" in role and action == "lambda:InvokeFunction" and "RunEngine" in res)
                   or ("CrawlerRole" in role and action == "s3:GetObject" and "/outputs/tables/" in res)
                   or ("SlackListener" in role and ((action == "s3:PutObject" and "/state/clicks/" in res)
                                                    or (action == "ssm:GetParameter" and "/cairnpath/slack-" in res)
                                                    or action == "kms:GenerateDataKey")))
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
results = sc.run(FakeCfn(), iam, FakeS3(), ACCOUNT, REGION, FakeKMS())
check(all(r["ok"] for r in results), "a correct deployment passes every case", [r for r in results if not r["ok"]])
check(sum(r["expected"] == sc.ALLOW for r in results) >= 4, "it includes controls that must be allowed (not only denials)")
s3_calls = [k for k in iam.asked if k["ActionNames"][0].startswith("s3:")]
check(all("ResourcePolicy" in k and k["ResourceOwner"] == f"arn:aws:iam::{ACCOUNT}:root" for k in s3_calls),
      "S3 cases include the bucket's own policy (so HTTPS-only is tested)")
check(any(k["ContextEntries"][0]["ContextKeyValues"] == ["false"] for k in iam.asked), "one case asks over plain HTTP")
check(all({"ContextKeyName": "aws:RequestedRegion", "ContextKeyType": "string", "ContextKeyValues": [REGION]}
          in k["ContextEntries"] for k in iam.asked),
      "every question says its region (else the region guardrail denies everything, as on 4 Nov 2026)")
check(all("{" not in k["ResourceArns"][0] for k in iam.asked), "function names filled in from the stack")
leaky = FakeIAM(leak=lambda role, action, res: "RunEngine" in role and action == "s3:PutObject" and "/inputs/" in res)
results = sc.run(FakeCfn(), leaky, FakeS3(), ACCOUNT, REGION, FakeKMS())
bad = [r for r in results if not r["ok"]]
check(len(bad) == 1 and "change its own inputs" in bad[0]["why"], "a role that can write its inputs is caught", bad)
check(names.get("slack", "").endswith("SlackListenerServiceRole-FFF"), "finds the Slack listener's role too", names)
check(names.get("search", "").endswith("SearchServiceRole-GGG"), "finds the public search's role (not its function or log)", names)
# its own policy granting a data read is still refused by the bucket's perimeter (it's not exempt):
leaky = FakeIAM(leak=lambda role, action, res: "SearchServiceRole" in role and action == "s3:GetObject")
check(all(r["ok"] for r in sc.run(FakeCfn(), leaky, FakeS3(), ACCOUNT, REGION, FakeKMS())),
      "a public search given a data read by mistake is still refused by the bucket (the second lock)")
# ...but a secret has no such second lock, so the check must catch that one itself
leaky = FakeIAM(leak=lambda role, action, res: "SearchServiceRole" in role and action == "ssm:GetParameter")
bad = [r for r in sc.run(FakeCfn(), leaky, FakeS3(), ACCOUNT, REGION, FakeKMS()) if not r["ok"]]
check(len(bad) == 1 and "public search" in bad[0]["why"], "a public search that can read the API key is caught", bad)
leaky = FakeIAM(leak=lambda role, action, res: "SlackListener" in role and action == "s3:GetObject")
bad = [r for r in sc.run(FakeCfn(), leaky, FakeS3(), ACCOUNT, REGION, FakeKMS()) if not r["ok"]]
check(len(bad) == 1 and bad[0]["role"] == "slack" and "read any data" in bad[0]["why"],
      "a Slack listener that can read data is caught (it faces the internet)", bad)
# audit 6 Nov 2026 (F5): what AWS's managed AWSGlueServiceRole allowed on any aws-glue-* path
leaky = FakeIAM(leak=lambda role, action, res: "CrawlerRole" in role and action in ("s3:PutObject", "s3:DeleteObject")
                and "/aws-glue-" in res)
bad = [r for r in sc.run(FakeCfn(), leaky, FakeS3(), ACCOUNT, REGION, FakeKMS()) if not r["ok"]]
check(len(bad) == 2 and all(r["role"] == "crawler" for r in bad),
      "a crawler that can write or delete an aws-glue-* path is caught (the managed Glue policy)", bad)
check(quiet(sc.report, results) == 1, "...and the report counts it as a failure (exit 1)")
orig = sc.run
def no_region_run():
    """What happened on 4 Nov 2026: questions without a region. The controls must catch it."""
    asked = FakeIAM()
    real = asked.simulate_principal_policy
    def strip(**kw):
        kw["ContextEntries"] = [c for c in kw["ContextEntries"] if c["ContextKeyName"] != "aws:RequestedRegion"]
        return real(**kw)
    asked.simulate_principal_policy = strip
    return sc.run(FakeCfn(), asked, FakeS3(), ACCOUNT, REGION, FakeKMS())
results = no_region_run()
bad = [r for r in results if not r["ok"]]
check(len(bad) == sum(r["expected"] == sc.ALLOW for r in results) and all(r["expected"] == sc.ALLOW for r in bad)
      and all("Organizations SCP RegionFloor" in r["decided_by"] for r in bad),
      "questions without a region: every control fails and the report names the SCP that denied them", bad)
print("\nThe data perimeter cases")
iam = FakeIAM()
results = sc.run(FakeCfn(), iam, FakeS3(), ACCOUNT, REGION, FakeKMS())
check(all(k["ContextEntries"][2] == {"ContextKeyName": "aws:PrincipalArn", "ContextKeyType": "string",
                                     "ContextKeyValues": [k["PolicySourceArn"]]} for k in iam.asked),
      "every question says who's asking (aws:PrincipalArn), so the perimeter can tell roles apart")
check(any(k.get("PolicyInputList") for k in iam.asked) and any(k["PolicySourceArn"] == ADMIN_ARN for k in iam.asked),
      "it simulates an identity with s3:* on everything, and the admin role by its real ARN (path included)")
check(all(r["ok"] for r in results) and sum(r["role"] == "admin" and r["action"].startswith("s3:")
                                           and SITE not in r["resource"] for r in results) == 2,
      "with the perimeter: the s3:* identity is refused, the admin still reads data and can fix the policy")
bad = [r for r in sc.run(FakeCfn(), FakeIAM(perimeter=False), FakeS3(), ACCOUNT, REGION, FakeKMS()) if not r["ok"]]
check(len(bad) == 2 and all("perimeter" in r["why"] for r in bad),
      "without the perimeter, the two perimeter cases fail (they'd catch it being removed)", bad)
print("\nThe KMS key cases")
iam, kms = FakeIAM(), FakeKMS()
results = sc.run(FakeCfn(), iam, FakeS3(), ACCOUNT, REGION, kms)
key_qs = [k for k in iam.asked if k["ActionNames"][0].startswith("kms:")]
check(names.get("key") == "1234abcd-12ab-34cd-56ef-1234567890ab" and kms.asked == [names["key"]],
      "finds the data key in the stack and reads its real policy", names.get("key"))
check(key_qs and all("DenyUseOutsideS3" in k.get("ResourcePolicy", "")
                     and k["ResourceArns"][0] == f"arn:aws:kms:{REGION}:{ACCOUNT}:key/{names['key']}" for k in key_qs),
      "key questions are asked against the key, with the key's own policy")
check(all('"Resource": "*"' not in k["ResourcePolicy"] for k in key_qs),
      "the key policy's Resource \"*\" is spelled out as the key's ARN (the simulator refuses \"*\")")
check(all(":root" not in k["ResourcePolicy"] for k in key_qs),
      "the root statement (in KMS it only lets IAM decide) is left out, as KMS reads it")
via = [k for k in key_qs if any(c["ContextKeyName"] == "kms:ViaService" for c in k["ContextEntries"])]
check(via and len(via) < len(key_qs) and all({"ContextKeyName": "kms:ViaService", "ContextKeyType": "string",
                                               "ContextKeyValues": [f"s3.{REGION}.amazonaws.com"]} in k["ContextEntries"] for k in via),
      "some go through S3 (kms:ViaService), some call KMS directly")
check(all(r["ok"] for r in results) and sum(r["action"].startswith("kms:") for r in results) >= 6,
      "a correct key passes: roles use it only through S3, the Slack listener never decrypts, the admin keeps control")
bad = [r for r in sc.run(FakeCfn(), FakeIAM(key_perimeter=False), FakeS3(), ACCOUNT, REGION, FakeKMS()) if not r["ok"]]
check(len(bad) == 1 and "directly" in bad[0]["why"], "a key without its 'only through S3' rule is caught", bad)
bad = [r for r in sc.run(FakeCfn(), FakeIAM(leak=lambda role, action, res: "SlackListener" in role and action == "kms:Decrypt"),
                         FakeS3(), ACCOUNT, REGION, FakeKMS()) if not r["ok"]]
check(len(bad) == 1 and bad[0]["role"] == "slack", "a Slack listener that can decrypt is caught", bad)

print("\nThe public site's bucket")
iam = FakeIAM()
results = sc.run(FakeCfn(), iam, FakeS3(), ACCOUNT, REGION, FakeKMS())
site_qs = [k for k in iam.asked if k["ResourceArns"][0].startswith(f"arn:aws:s3:::{SITE}/")]
check(sc.find(FakeCfn()).get("site") == SITE and len(site_qs) >= 6,
      "finds the site bucket in the stack and asks about it", sc.find(FakeCfn()).get("site"))
check(all("DenyWritesToAnyoneElse" in k["ResourcePolicy"] for k in site_qs)
      and not any("DenyWritesToAnyoneElse" in k.get("ResourcePolicy", "") for k in iam.asked if k not in site_qs),
      "site questions use the site bucket's own policy, data questions the data bucket's")
check(all(r["ok"] for r in results), "a correct site passes: only the engine writes, it can't read or delete, admin keeps control")
bad = [r for r in sc.run(FakeCfn(), FakeIAM(site_perimeter=False), FakeS3(), ACCOUNT, REGION, FakeKMS()) if not r["ok"]]
check(len(bad) == 1 and "site perimeter" in bad[0]["why"], "a site bucket without its write perimeter is caught", bad)
bad = [r for r in sc.run(FakeCfn(), FakeIAM(leak=lambda role, action, res: "RunEngine" in role and action == "s3:DeleteObject"
                                            and SITE in res), FakeS3(), ACCOUNT, REGION, FakeKMS()) if not r["ok"]]
check(len(bad) == 1 and "delete" in bad[0]["why"], "an engine that can delete a site page is caught", bad)
sc.AWS["site"] = False
try:
    no_site = sc.run(FakeCfn(), FakeIAM(), FakeS3(), ACCOUNT, REGION, FakeKMS())
    check(not any(SITE in r["resource"] for r in no_site) and all(r["ok"] for r in no_site),
          "[aws] site = false: no site questions")
finally:
    sc.AWS["site"] = True

too_tight = FakeIAM(leak=None)
too_tight.simulate_principal_policy = lambda **kw: {"EvaluationResults": [{"EvalDecision": "implicitDeny"}]}
results = sc.run(FakeCfn(), too_tight, FakeS3(), ACCOUNT, REGION, FakeKMS())
check(not all(r["ok"] for r in results), "a check that denies everything fails too (the controls catch it)")

print("\nFAILED" if failed else "\nOK")
sys.exit(1 if failed else 0)
