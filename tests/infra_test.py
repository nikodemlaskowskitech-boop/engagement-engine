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
import os                                               # noqa: E402
os.environ.setdefault("CAIRNPATH_SKIP_DEPS", "1")       # build the template without fetching DuckDB

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

aws = SETTINGS["aws"]
r = has(t, "AWS::Logs::LogGroup", {"RetentionInDays": 30})
check(r is True and len(t.find_resources("AWS::Logs::LogGroup")) == 2,
      "both Lambdas' logs kept 30 days, then deleted (no cost pile-up)", r)
notes = t.find_resources("Custom::S3BucketNotifications")
lam = next(iter(notes.values()), {}).get("Properties", {}).get("NotificationConfiguration", {}) \
    .get("LambdaFunctionConfigurations", [])
check(len(lam) == 2, "the bucket wakes exactly two functions", lam)
ours = {k: v for k, v in t.find_resources("AWS::Lambda::Function").items()
        if v["Properties"].get("Handler") == "handler.handler"}


def actions_of(stmt):
    return [stmt["Action"]] if isinstance(stmt["Action"], str) else stmt["Action"]


def lambda_checks(title, env_key, prefix, trigger, may_read, may_write, never_write, output, retries):
    """One of our Lambdas: runtime, sizes from settings, its single trigger, least privilege."""
    print(f"\n{title}")
    found = {k: v for k, v in ours.items() if env_key in v["Properties"]["Environment"]["Variables"]}
    fn_id, fn = next(iter(found.items()), (None, {}))
    props = fn.get("Properties", {})
    check(len(found) == 1, "one function", f"{len(found)} found")
    check(props.get("Runtime") == "python3.13" and props.get("Architectures") == ["arm64"],
          "Python 3.13 on Graviton (arm64)", f"{props.get('Runtime')} {props.get('Architectures')}")
    check(props.get("MemorySize") == aws[prefix + "_memory_mb"] and props.get("Timeout") == aws[prefix + "_timeout_seconds"]
          and props.get("EphemeralStorage", {}).get("Size") == aws[prefix + "_disk_mb"],
          "memory, timeout and disk from settings.toml [aws]")
    env = props.get("Environment", {}).get("Variables", {})
    check(env.get("ACCOUNT_ID") == str(aws["account_id"]) and "Ref" in str(env.get("BUCKET")),
          "told the bucket (not hard-coded) and the account")
    mine = [c for c in lam if c["LambdaFunctionArn"].get("Fn::GetAtt", [None])[0] == fn_id]
    check(len(mine) == 1 and mine[0]["Events"] == ["s3:ObjectCreated:*"]
          and mine[0]["Filter"]["Key"]["FilterRules"] == [{"Name": "prefix", "Value": trigger}],
          f"wakes only on {trigger}", mine)
    cfg = [v for v in t.find_resources("AWS::Lambda::EventInvokeConfig").values()
           if v["Properties"]["FunctionName"] == {"Ref": fn_id}]
    check(cfg and cfg[0]["Properties"].get("MaximumRetryAttempts") == retries, f"retries after a crash: {retries}")
    role = props.get("Role", {}).get("Fn::GetAtt", [None])[0]
    stmts = [st for v in t.find_resources("AWS::IAM::Policy").values()
             if any(r.get("Ref") == role for r in v["Properties"].get("Roles", []))
             for st in v["Properties"]["PolicyDocument"]["Statement"]]
    acts = [a for st in stmts for a in actions_of(st)]
    check(bool(stmts) and "s3:*" not in acts and not any(a.startswith("s3:Delete") for a in acts),
          "its role has no s3:* and no delete", acts)
    text = json.dumps(stmts)
    check(all('/' + r + '"' in text for r in may_read), f"may read {', '.join(may_read)}")
    puts = [st for st in stmts if any(a.startswith("s3:PutObject") for a in actions_of(st))]
    put_text = json.dumps([st["Resource"] for st in puts])
    check(bool(puts) and all('/' + w + '"' in put_text for w in may_write)
          and not any('"/' + w in put_text for w in never_write),
          f"writes only {', '.join(may_write)}; never {', '.join(never_write)} (no loops)", put_text)
    check(output in t.find_outputs("*"), "prints the function name after deploy")


reports = aws["reports_prefix"] + "check_inputs/"
lambda_checks("Check Lambda: wakes on the upload marker, least privilege", "REPORTS_PREFIX",
              "check", aws["inputs_prefix"] + aws["upload_marker"],
              may_read=[aws["inputs_prefix"] + "*"], may_write=[reports + "*"],
              never_write=[aws["inputs_prefix"]], output="CheckFunction", retries=1)
lambda_checks("Engine Lambda: wakes on the check's verdict, least privilege", "ENGINE_STEPS",
              "engine", reports + "latest.json",
              may_read=[aws["inputs_prefix"] + "*", aws["state_prefix"] + "*", reports + "latest.json",
                        aws["outputs_prefix"] + "run.json"],
              may_write=[aws["outputs_prefix"] + "*"],
              never_write=[aws["inputs_prefix"], aws["reports_prefix"], aws["state_prefix"]],
              output="EngineFunction", retries=0)

print("\nAthena: tables over outputs/tables/, results kept 7 days, a scan cap per query")
dbs = t.find_resources("AWS::Glue::Database")
check([v["Properties"]["DatabaseInput"]["Name"] for v in dbs.values()] == [aws["glue_database"]],
      f"one Glue database, {aws['glue_database']} (name from settings)")
crawlers = t.find_resources("AWS::Glue::Crawler")
cr = next(iter(crawlers.values()), {}).get("Properties", {})
paths = [json.dumps(tg["Path"]) for tg in cr.get("Targets", {}).get("S3Targets", [])]
check(len(crawlers) == 1 and len(paths) == len(aws["athena_tables"])
      and all(f'/{aws["outputs_prefix"]}tables/{n}/' in p for n, p in zip(aws["athena_tables"], paths)),
      "the crawler reads one folder per table in [aws] athena_tables, under outputs/tables/", paths)
check(json.loads(cr.get("Configuration", "{}")).get("Grouping", {}).get("TableLevelConfiguration") == 4,
      "tables made at the folder level (the 5 same-shaped flag tables stay 5 tables, not merged)")
crole = cr.get("Role", {}).get("Fn::GetAtt", [None])[0]
cstmts = [st for v in t.find_resources("AWS::IAM::Policy").values()
          if any(r.get("Ref") == crole for r in v["Properties"].get("Roles", []))
          for st in v["Properties"]["PolicyDocument"]["Statement"]]
cacts = [a for st in cstmts for a in actions_of(st)]
check(bool(cstmts) and not any(a.startswith(("s3:Put", "s3:Delete")) or a == "s3:*" for a in cacts)
      and '/' + aws["outputs_prefix"] + 'tables/*"' in json.dumps(cstmts),
      "the crawler may read outputs/tables/* only, never write to the bucket", cacts)
wgs = t.find_resources("AWS::Athena::WorkGroup")
wg = next(iter(wgs.values()), {}).get("Properties", {})
wcfg = wg.get("WorkGroupConfiguration", {})
check(wg.get("Name") == aws["athena_workgroup"] and wcfg.get("EnforceWorkGroupConfiguration") is True,
      "one Athena workgroup, its settings enforced (a query can't send results elsewhere)")
check(wcfg.get("BytesScannedCutoffPerQuery") == aws["athena_scan_limit_mb"] * 1024 * 1024,
      f"a query scanning over {aws['athena_scan_limit_mb']} MB is cancelled (cost guard)")
check(f'/{aws["athena_results_prefix"]}' in json.dumps(wcfg.get("ResultConfiguration", {}).get("OutputLocation"))
      and wcfg["ResultConfiguration"].get("EncryptionConfiguration", {}).get("EncryptionOption") == "SSE_S3",
      "query results go to athena-results/ in our bucket, encrypted")
r = has(t, "AWS::S3::Bucket", {"LifecycleConfiguration": {"Rules": Match.array_with([Match.object_like({
    "Prefix": aws["athena_results_prefix"], "ExpirationInDays": aws["athena_results_keep_days"]})])}})
check(r is True, f"query results deleted after {aws['athena_results_keep_days']} days", r)
check(not any(c["Filter"]["Key"]["FilterRules"][0]["Value"].startswith((aws["outputs_prefix"], aws["athena_results_prefix"]))
              for c in lam), "nothing wakes on outputs/ or athena-results/ (no loops)")

print("\nSchedule: a daily safety net for the engine")
scheds = t.find_resources("AWS::Scheduler::Schedule")
sc = next(iter(scheds.values()), {}).get("Properties", {})
engine_id = next(k for k, v in ours.items() if "ENGINE_STEPS" in v["Properties"]["Environment"]["Variables"])
check(len(scheds) == 1 and sc.get("ScheduleExpression") == aws["engine_schedule"]
      and sc.get("ScheduleExpressionTimezone") == aws["engine_schedule_timezone"],
      f"one schedule, {aws['engine_schedule']} in {aws['engine_schedule_timezone']} (from settings)")
tg = sc.get("Target", {})
check(tg.get("Arn") == {"Fn::GetAtt": [engine_id, "Arn"]} and json.loads(tg.get("Input", "{}")) == {"trigger": "schedule"}
      and tg.get("RetryPolicy", {}).get("MaximumRetryAttempts") == 0,
      "wakes the engine Lambda with {\"trigger\": \"schedule\"}, no retries")
srole = tg.get("RoleArn", {}).get("Fn::GetAtt", [None])[0]
trust = t.find_resources("AWS::IAM::Role").get(srole, {}).get("Properties", {}).get("AssumeRolePolicyDocument", {})
check("scheduler.amazonaws.com" in json.dumps(trust) and "aws:SourceAccount" in json.dumps(trust),
      "its role is for EventBridge Scheduler, only on schedules in our own account")
sstmts = [st for v in t.find_resources("AWS::IAM::Policy").values()
          if any(r.get("Ref") == srole for r in v["Properties"].get("Roles", []))
          for st in v["Properties"]["PolicyDocument"]["Statement"]]
check(bool(sstmts) and {a for st in sstmts for a in actions_of(st)} == {"lambda:InvokeFunction"}
      and all(engine_id in json.dumps(st["Resource"]) for st in sstmts),
      "and it may only start the engine, nothing else", sstmts)

engine_env = next(v for v in ours.values() if "ENGINE_STEPS" in v["Properties"]["Environment"]["Variables"])
check(json.loads(engine_env["Properties"]["Environment"]["Variables"]["ENGINE_STEPS"]) == aws["engine_steps"],
      "runs the steps in [aws] engine_steps, in order")

print("\nFAILED" if failed else "\nOK")
sys.exit(1 if failed else 0)
