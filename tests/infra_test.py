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
groups = t.find_resources("AWS::Logs::LogGroup")
check(len(groups) == 4 and all(g["Properties"].get("RetentionInDays") == 30 for g in groups.values()),
      "every log (three Lambdas, the Slack API's access log) kept 30 days, then deleted (no cost pile-up)",
      {k: g["Properties"].get("RetentionInDays") for k, g in groups.items()})
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
                        aws["outputs_prefix"] + "run.json", aws["outputs_prefix"] + "handover/*"],
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

print("\nEngine Lambda: the API key for the briefs")
eng = next(v for v in ours.values() if "ENGINE_STEPS" in v["Properties"]["Environment"]["Variables"])
evars = eng["Properties"]["Environment"]["Variables"]
check(evars.get("API_KEY_PARAMETER") == aws["api_key_parameter"] and evars.get("LLM_PROVIDER") == aws["briefs_llm_provider"]
      and json.loads(evars.get("OPTIONAL_STEPS", "[]")) == aws["engine_optional_steps"],
      "told the key's name (not the key), the provider and the optional steps, from settings")
check(not any("sk-ant" in str(v) or "ANTHROPIC_API_KEY" in str(k) for k, v in evars.items()),
      "no API key in its settings (it reads it at run time)")
erole = eng["Properties"]["Role"]["Fn::GetAtt"][0]
ssm_stmts = [st for v in t.find_resources("AWS::IAM::Policy").values()
             if any(r.get("Ref") == erole for r in v["Properties"].get("Roles", []))
             for st in v["Properties"]["PolicyDocument"]["Statement"]
             if any(a.startswith("ssm:") for a in actions_of(st))]
check(len(ssm_stmts) == 1 and actions_of(ssm_stmts[0]) == ["ssm:GetParameter"]
      and f"parameter{aws['api_key_parameter']}" in json.dumps(ssm_stmts[0]["Resource"]),
      "may read that one parameter, nothing else in Parameter Store, and never write it", ssm_stmts)

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

print("\nSlack listener: one public address, signature checked, least privilege")
slk = {k: v for k, v in ours.items() if "SIGNING_SECRET_PARAMETER" in v["Properties"]["Environment"]["Variables"]}
slk_id, slk_fn = next(iter(slk.items()), (None, {"Properties": {"Environment": {"Variables": {}}}}))
sp = slk_fn["Properties"]
svars = sp["Environment"]["Variables"]
check(len(slk) == 1 and sp.get("Runtime") == "python3.13" and sp.get("Architectures") == ["arm64"]
      and sp.get("MemorySize") == aws["slack_memory_mb"] and sp.get("Timeout") == aws["slack_timeout_seconds"],
      "one function, Python 3.13 on arm64, memory and timeout from settings")
check(svars.get("SIGNING_SECRET_PARAMETER") == aws["slack_signing_secret_parameter"]
      and svars.get("BOT_TOKEN_PARAMETER") == aws["slack_bot_token_parameter"]
      and svars.get("CLICKS_PREFIX") == aws["slack_clicks_prefix"]
      and svars.get("MAX_AGE_SECONDS") == str(aws["slack_max_age_seconds"]),
      "told the secrets' names (not the secrets), where clicks go and the replay window, from settings")
check(not any(str(v).startswith(("xoxb-", "xapp-")) or "SECRET" in k and "PARAMETER" not in k for k, v in svars.items()),
      "no token or secret in its settings (read at run time)")
check(not any(c["LambdaFunctionArn"].get("Fn::GetAtt", [None])[0] == slk_id for c in lam),
      "never woken by the bucket: only by API Gateway")
lrole = sp.get("Role", {}).get("Fn::GetAtt", [None])[0]
lstmts = [st for v in t.find_resources("AWS::IAM::Policy").values()
          if any(r.get("Ref") == lrole for r in v["Properties"].get("Roles", []))
          for st in v["Properties"]["PolicyDocument"]["Statement"]]
s3_stmts = [st for st in lstmts if any(a.startswith("s3:") for a in actions_of(st))]
s3_acts = {a for st in s3_stmts for a in actions_of(st)}
check(bool(s3_stmts) and all(a.startswith("s3:PutObject") or a == "s3:Abort*" for a in s3_acts)
      and all(f'/{aws["slack_clicks_prefix"]}*"' in json.dumps(st["Resource"]) for st in s3_stmts),
      f"may only write under {aws['slack_clicks_prefix']}: no reads, no lists, no deletes", sorted(s3_acts))
ssm_l = [st for st in lstmts if any(a.startswith("ssm:") for a in actions_of(st))]
res = json.dumps([st["Resource"] for st in ssm_l])
check(len(ssm_l) == 1 and actions_of(ssm_l[0]) == ["ssm:GetParameter"]
      and f"parameter{aws['slack_signing_secret_parameter']}" in res and f"parameter{aws['slack_bot_token_parameter']}" in res
      and aws["api_key_parameter"] not in res and "*" not in res,
      "may read its two secrets only (not the API key), and never write them", ssm_l)
check(not any(a not in ("ssm:GetParameter",) and not a.startswith(("s3:", "logs:", "xray:")) for st in lstmts for a in actions_of(st)),
      "nothing else in its role", [a for st in lstmts for a in actions_of(st)])
apis = t.find_resources("AWS::ApiGatewayV2::Api")
routes = [v["Properties"]["RouteKey"] for v in t.find_resources("AWS::ApiGatewayV2::Route").values()]
check(len(apis) == 1 and routes == ["POST /slack/actions"], "one HTTP API, one route: POST /slack/actions", routes)
stages = [v["Properties"] for v in t.find_resources("AWS::ApiGatewayV2::Stage").values()]
st0 = stages[0] if stages else {}
thr = st0.get("DefaultRouteSettings", {})
check(len(stages) == 1 and st0.get("StageName") == "$default" and st0.get("AutoDeploy") is True
      and thr.get("ThrottlingRateLimit") == aws["slack_rate_per_second"]
      and thr.get("ThrottlingBurstLimit") == aws["slack_burst"],
      f"rate limit {aws['slack_rate_per_second']}/s, burst {aws['slack_burst']} (a flood stops at the gateway)", thr)
fmt = st0.get("AccessLogSettings", {}).get("Format", "")
check("DestinationArn" in st0.get("AccessLogSettings", {}) and "$context.status" in fmt and "body" not in fmt.lower(),
      "every request in an access log (who, when, our answer), never the body")
perms = [v["Properties"] for v in t.find_resources("AWS::Lambda::Permission").values()
         if v["Properties"].get("FunctionName", {}).get("Fn::GetAtt", [None])[0] == slk_id]
api_id = next(iter(apis), None)
check(len(perms) == 1 and perms[0]["Principal"] == "apigateway.amazonaws.com"
      and api_id in json.dumps(perms[0].get("SourceArn")) and "/slack/actions" in json.dumps(perms[0].get("SourceArn")),
      "only this API's route may call the function", perms)
check({"SlackRequestUrl", "SlackListenerFunction"} <= set(t.find_outputs("*")),
      "prints the address for the Slack app after deploy")
check(aws["slack_clicks_prefix"].startswith(aws["state_prefix"])
      and engine_env["Properties"]["Environment"]["Variables"].get("CLICKS_PREFIX") == aws["slack_clicks_prefix"]
      and not any(c["Filter"]["Key"]["FilterRules"][0]["Value"].startswith(aws["state_prefix"]) for c in lam),
      "clicks land in state/ (the engine reads them), and nothing wakes on state/ (no run per click)")

print("\nData perimeter: the bucket refuses everyone but our roles and the admin")
bp = next(iter(t.find_resources("AWS::S3::BucketPolicy").values()), {}).get("Properties", {})
stmts = {st.get("Sid"): st for st in bp.get("PolicyDocument", {}).get("Statement", [])}
outside, others = stmts.get("DenyOutsideThisAccount", {}), stmts.get("DenyDataToAnyoneElse", {})
check(outside.get("Effect") == "Deny" and outside.get("Principal") == {"AWS": "*"} and outside.get("Action") == "s3:*"
      and outside.get("Condition", {}).get("StringNotEquals", {}).get("aws:PrincipalAccount") == str(aws["account_id"]),
      "refuses any identity outside this account, for everything", outside)
check(outside.get("Condition", {}).get("BoolIfExists", {}).get("aws:PrincipalIsAWSService") == "false"
      and others.get("Condition", {}).get("BoolIfExists", {}).get("aws:PrincipalIsAWSService") == "false",
      "AWS services acting for the bucket (lifecycle rules) aren't caught by it")
acts = actions_of(others) if others else []
check(others.get("Effect") == "Deny" and {"s3:GetObject*", "s3:PutObject*", "s3:DeleteObject*", "s3:ListBucket*"} <= set(acts),
      "refuses reading, writing, deleting and listing the data to anyone not exempt", acts)
check(not any(a.startswith(("s3:PutBucket", "s3:DeleteBucket", "s3:GetBucket")) or a == "s3:*" for a in acts),
      "leaves the bucket's own settings to admins, so a mistake can always be fixed (no lockout)", acts)
exempt = others.get("Condition", {}).get("ArnNotLike", {}).get("aws:PrincipalArn", [])
exempt_text = json.dumps(exempt)
role_ids = {}
for k, v in ours.items():
    var = v["Properties"]["Environment"]["Variables"]
    kind = "check" if "REPORTS_PREFIX" in var else "engine" if "ENGINE_STEPS" in var else "slack"
    role_ids[kind] = v["Properties"]["Role"]["Fn::GetAtt"][0]
role_ids["crawler"] = crole
check(len(exempt) == 4 + len(aws["admin_role_names"]) + len(aws["break_glass_users"])
      and all(rid in exempt_text for rid in role_ids.values()),
      "exempt: the check, engine, Slack listener and crawler roles", sorted(role_ids))
check(all(f"role/*{n}" in exempt_text for n in aws["admin_role_names"])
      and all(f"user/{n}" in exempt_text for n in aws["break_glass_users"])
      and "ScheduleRole" not in exempt_text,
      "...plus the admin from settings and the break-glass user; nobody else (not even the schedule)", exempt)

print("\nGuardDuty: on trial, only the plans this stack needs")
dets = t.find_resources("AWS::GuardDuty::Detector")
if aws.get("guardduty"):
    feats = {f["Name"]: f["Status"] for f in next(iter(dets.values()), {}).get("Properties", {}).get("Features", [])}
    check(len(dets) == 1, "one detector ([aws] guardduty = true)", len(dets))
    check(feats.get("S3_DATA_EVENTS") == "ENABLED" and feats.get("LAMBDA_NETWORK_LOGS") == "ENABLED",
          "watches the bucket's data events and the Lambdas' network activity", feats)
    check(all(feats.get(n) == "DISABLED" for n in ("EKS_AUDIT_LOGS", "EBS_MALWARE_PROTECTION",
                                                   "RDS_LOGIN_EVENTS", "RUNTIME_MONITORING")),
          "plans for services we don't run are off, so they can't add cost", feats)
else:
    check(not dets, "[aws] guardduty = false: no detector (switched off, nothing charged)")

print("\nIAM Access Analyzer: anything reachable from outside the account")
analyzers = [v["Properties"] for v in t.find_resources("AWS::AccessAnalyzer::Analyzer").values()]
check(len(analyzers) == 1 and analyzers[0].get("Type") == "ACCOUNT"
      and analyzers[0].get("AnalyzerName") == aws["access_analyzer_name"]
      and "AnalyzerConfiguration" not in analyzers[0],
      "one free external-access analyzer for this account, named from settings (not the paid unused-access kind)",
      analyzers)
check("AccessAnalyzerArn" in t.find_outputs("*"), "prints its ARN after deploy")

print("\nFAILED" if failed else "\nOK")
sys.exit(1 if failed else 0)
