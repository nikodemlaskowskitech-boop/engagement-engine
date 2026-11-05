"""
DataStack: one private S3 bucket for the pipeline's data (inputs, outputs,
briefs) once it runs in AWS.

Safe by default, each line on purpose:
  - encrypted at rest with our own KMS key: reading the data needs the bucket's
    permission and the key's, and every use of the key is logged
  - no public access, ever (all four "block public access" switches on)
  - HTTPS only (a bucket policy refuses plain HTTP)
  - versioned: an overwrite or delete keeps the old copy, so a bad run can be
    rolled back; old copies expire after [aws] keep_old_versions_days
  - kept if the stack is deleted (RETAIN): data outlives the code that made it

And the first piece of code that runs in AWS: a Lambda that checks the inputs
after each upload (lambdas/check_inputs/handler.py). It wakes up only when
upload.py writes its "upload complete" marker, may only read inputs/ and write
reports/check_inputs/, and runs the same check_inputs.py as the laptop.

Then the engine Lambda, a daily schedule, Athena, and the Slack listener: one
public HTTPS address (API Gateway) for Slack's button clicks, and a small
Lambda that checks each request's Slack signature before doing anything.
"""

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Size, Stack
from aws_cdk import aws_accessanalyzer as accessanalyzer
from aws_cdk import aws_apigatewayv2 as apigwv2
from aws_cdk import aws_apigatewayv2_integrations as integrations
from aws_cdk import aws_athena as athena
from aws_cdk import aws_ce as ce
from aws_cdk import aws_glue as glue
from aws_cdk import aws_guardduty as guardduty
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_notifications as s3n
from aws_cdk import aws_scheduler as scheduler
from constructs import Construct

import json

from bundle import ENGINE_FILES, bundle_check_inputs, bundle_engine, bundle_slack
from settings import SETTINGS


class DataStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        keep_days = SETTINGS["aws"]["keep_old_versions_days"]

        # Our own key for the bucket (instead of S3's built-in one). Its default policy lets IAM
        # in this account decide who may use it, so the admin can always manage it (no lockout);
        # each role gets the key permissions it needs from the bucket grants below, nothing more.
        # Rotated yearly by AWS (old data stays readable), kept if the stack is deleted, and a
        # deletion would wait [aws] kms_deletion_window_days: data without its key is gone.
        self.key = kms.Key(
            self, "DataKey", alias=SETTINGS["aws"]["kms_key_alias"],
            description="Cairnpath data bucket: encrypts everything in it",
            enable_key_rotation=True,
            pending_window=Duration.days(SETTINGS["aws"]["kms_deletion_window_days"]),
            removal_policy=RemovalPolicy.RETAIN)

        self.bucket = s3.Bucket(
            self, "Data",
            encryption=s3.BucketEncryption.KMS, encryption_key=self.key,
            bucket_key_enabled=True,       # one data key per bucket, not a KMS call per file: cost near zero
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            versioned=True,
            object_ownership=s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,   # no ACLs: access is only by policy
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[s3.LifecycleRule(
                noncurrent_version_expiration=Duration.days(keep_days),  # old versions don't pile up cost
                abort_incomplete_multipart_upload_after=Duration.days(7),
            ), s3.LifecycleRule(                                         # Athena's query results: scratch
                prefix=SETTINGS["aws"]["athena_results_prefix"],
                expiration=Duration.days(SETTINGS["aws"]["athena_results_keep_days"]),
            )],
        )
        # the name AWS generates, printed after `cdk deploy` (S3 names are global, so we let AWS pick)
        CfnOutput(self, "BucketName", value=self.bucket.bucket_name)

        # --- the check Lambda ---
        aws = SETTINGS["aws"]
        inputs, reports = aws["inputs_prefix"], aws["reports_prefix"] + "check_inputs/"
        fn = lambda_.Function(
            self, "CheckInputs",
            description="Checks the inputs in the data bucket after each upload (check_inputs.py)",
            runtime=lambda_.Runtime.PYTHON_3_13,
            architecture=lambda_.Architecture.ARM_64,         # Graviton: about 20% cheaper per second
            handler="handler.handler",
            code=lambda_.Code.from_asset(str(bundle_check_inputs())),
            memory_size=aws["check_memory_mb"],
            timeout=Duration.seconds(aws["check_timeout_seconds"]),
            ephemeral_storage_size=Size.mebibytes(aws["check_disk_mb"]),   # /tmp holds a copy of the inputs
            environment={"BUCKET": self.bucket.bucket_name, "INPUTS_PREFIX": inputs,
                         "REPORTS_PREFIX": reports, "MARKER_NAME": aws["upload_marker"],
                         "ACCOUNT_ID": str(aws["account_id"])},
            retry_attempts=1,                                  # one retry on a crash, not two
            log_group=logs.LogGroup(self, "CheckInputsLogs",
                                    retention=logs.RetentionDays.ONE_MONTH,   # logs don't pile up cost
                                    removal_policy=RemovalPolicy.DESTROY),
        )
        # least privilege: read the inputs, write its own reports, nothing else in the bucket
        self.bucket.grant_read(fn, inputs + "*")
        self.bucket.grant_put(fn, reports + "*")
        # wake up on the marker only: one check per upload, never on its own reports
        self.bucket.add_event_notification(
            s3.EventType.OBJECT_CREATED, s3n.LambdaDestination(fn),
            s3.NotificationKeyFilter(prefix=inputs + aws["upload_marker"]))
        CfnOutput(self, "CheckFunction", value=fn.function_name)

        # --- the engine Lambda: flags, journeys, reports, cockpit export ---
        outputs, state = aws["outputs_prefix"], aws["state_prefix"]
        check_summary = reports + "latest.json"
        engine = lambda_.Function(
            self, "RunEngine",
            description="Runs the engine (flags, journeys, reports, cockpit export) after a clean input check",
            runtime=lambda_.Runtime.PYTHON_3_13,
            architecture=lambda_.Architecture.ARM_64,
            handler="handler.handler",
            code=lambda_.Code.from_asset(str(bundle_engine())),
            memory_size=aws["engine_memory_mb"],
            timeout=Duration.seconds(aws["engine_timeout_seconds"]),
            ephemeral_storage_size=Size.mebibytes(aws["engine_disk_mb"]),
            environment={"BUCKET": self.bucket.bucket_name, "ACCOUNT_ID": str(aws["account_id"]),
                         "INPUTS_PREFIX": inputs, "STATE_PREFIX": state, "OUTPUTS_PREFIX": outputs,
                         "CHECK_SUMMARY": check_summary, "MARKER_NAME": aws["upload_marker"],
                         "CLICKS_PREFIX": aws["slack_clicks_prefix"],     # Slack clicks (inside state/)
                         "ENGINE_STEPS": json.dumps(aws["engine_steps"]),
                         "ENGINE_CODE": json.dumps(ENGINE_FILES),
                         "OPTIONAL_STEPS": json.dumps(aws["engine_optional_steps"]),
                         # the key's NAME, not the key: the value is read at run time (never shown here)
                         "API_KEY_PARAMETER": aws["api_key_parameter"],
                         "LLM_PROVIDER": aws["briefs_llm_provider"],
                         "LEDGER_FILE": SETTINGS["llm"]["ledger_file"]},
            retry_attempts=0,                                  # same data, same result: a retry wouldn't help
            log_group=logs.LogGroup(self, "RunEngineLogs",
                                    retention=logs.RetentionDays.ONE_MONTH,
                                    removal_policy=RemovalPolicy.DESTROY),
        )
        # reads the inputs, the action log and the check's verdict; writes only outputs/
        self.bucket.grant_read(engine, inputs + "*")
        self.bucket.grant_read(engine, state + "*")
        self.bucket.grant_read(engine, check_summary)
        self.bucket.grant_put(engine, outputs + "*")
        self.bucket.grant_read(engine, outputs + "handover/*")          # the briefs' cache from earlier runs
        self.bucket.grant_read(engine, outputs + SETTINGS["llm"]["ledger_file"])   # the AI spend ledger so far
        # the LLM API key: read this one parameter, decrypted (the AWS-managed aws/ssm key allows
        # that for roles in this account that may read the parameter), nothing else in Parameter Store
        engine.add_to_role_policy(iam.PolicyStatement(
            actions=["ssm:GetParameter"],
            resources=[f"arn:aws:ssm:{self.region}:{self.account}:parameter{aws['api_key_parameter']}"]))
        # runs right after each input check (its summary is written last)
        self.bucket.add_event_notification(
            s3.EventType.OBJECT_CREATED, s3n.LambdaDestination(engine),
            s3.NotificationKeyFilter(prefix=check_summary))
        CfnOutput(self, "EngineFunction", value=engine.function_name)

        # --- the daily safety net: EventBridge Scheduler wakes the engine once a day ---
        self.bucket.grant_read(engine, outputs + "run.json")        # to see whether it's up to date
        schedule_role = iam.Role(
            self, "ScheduleRole",
            assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com", conditions={
                "StringEquals": {"aws:SourceAccount": self.account}}))   # only our own schedules
        engine.grant_invoke(schedule_role)                               # may start the engine, nothing else
        scheduler.CfnSchedule(
            self, "EngineSchedule",
            description="Daily safety net: runs the engine if the last run was missed or failed",
            schedule_expression=aws["engine_schedule"],
            schedule_expression_timezone=aws["engine_schedule_timezone"],
            flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(mode="OFF"),
            target=scheduler.CfnSchedule.TargetProperty(
                arn=engine.function_arn, role_arn=schedule_role.role_arn,
                input=json.dumps({"trigger": "schedule"}),
                retry_policy=scheduler.CfnSchedule.RetryPolicyProperty(maximum_retry_attempts=0)))

        # --- Athena: ask your own questions of the engine's results ---
        # Glue Data Catalog = the list of tables ("this folder is a table with these columns").
        # The crawler reads the Parquet files under outputs/tables/ and writes that list for us:
        # one table per folder, columns and types taken from the files. Run it once after the
        # first engine run, and again only when a table's columns change (new data needs no crawl).
        database = glue.CfnDatabase(self, "GlueDatabase", catalog_id=self.account,
                                    database_input=glue.CfnDatabase.DatabaseInputProperty(
                                        name=aws["glue_database"],
                                        description="Cairnpath engine results (outputs/tables/), for Athena"))
        crawler_role = iam.Role(
            self, "CrawlerRole", assumed_by=iam.ServicePrincipal("glue.amazonaws.com"),
            managed_policies=[iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSGlueServiceRole")])
        self.bucket.grant_read(crawler_role, outputs + "tables/*")             # reads the tables, nothing else
        crawler = glue.CfnCrawler(
            self, "TablesCrawler", role=crawler_role.role_arn,
            database_name=aws["glue_database"],
            description="Turns each folder under outputs/tables/ into an Athena table",
            # one target per table folder, and tables made at the folder's level (bucket = 1,
            # outputs/ = 2, tables/ = 3, <name>/ = 4): without this, the five flag tables (same
            # columns) would be merged into one table with a "partition" column instead
            targets=glue.CfnCrawler.TargetsProperty(s3_targets=[glue.CfnCrawler.S3TargetProperty(
                path=f"s3://{self.bucket.bucket_name}/{outputs}tables/{name}/")
                for name in aws["athena_tables"]]),
            configuration=json.dumps({"Version": 1.0, "Grouping": {"TableLevelConfiguration": 4}}),
            schema_change_policy=glue.CfnCrawler.SchemaChangePolicyProperty(
                update_behavior="UPDATE_IN_DATABASE", delete_behavior="DELETE_FROM_DATABASE"))
        crawler.add_resource_dependency(database)    # the database exists before the crawler
        # Athena workgroup: where query results go, and a cap on how much one query may scan
        athena.CfnWorkGroup(
            self, "Workgroup", name=aws["athena_workgroup"],
            description="Cairnpath: queries over the engine's results",
            recursive_delete_option=True,
            work_group_configuration=athena.CfnWorkGroup.WorkGroupConfigurationProperty(
                enforce_work_group_configuration=True,
                bytes_scanned_cutoff_per_query=aws["athena_scan_limit_mb"] * 1024 * 1024,
                publish_cloud_watch_metrics_enabled=True,
                result_configuration=athena.CfnWorkGroup.ResultConfigurationProperty(
                    output_location=f"s3://{self.bucket.bucket_name}/{aws['athena_results_prefix']}",
                    encryption_configuration=athena.CfnWorkGroup.EncryptionConfigurationProperty(
                        encryption_option="SSE_S3"))))
        CfnOutput(self, "Crawler", value=crawler.ref)

        # --- the Slack listener: clicks on task cards, without the laptop ---
        # API Gateway gives Slack one public address. Anyone on the internet can reach it, so
        # the Lambda checks Slack's signature on every request before doing anything (see its
        # handler). The gateway adds a rate limit, so a flood is cut off before it reaches
        # (and costs) the Lambda, and an access log of every request.
        clicks = aws["slack_clicks_prefix"]
        params = [aws["slack_signing_secret_parameter"], aws["slack_bot_token_parameter"]]
        listener = lambda_.Function(
            self, "SlackListener",
            description="Handles Slack button clicks: checks the signature, saves the click, updates the card",
            runtime=lambda_.Runtime.PYTHON_3_13,
            architecture=lambda_.Architecture.ARM_64,
            handler="handler.handler",
            code=lambda_.Code.from_asset(str(bundle_slack())),
            memory_size=aws["slack_memory_mb"],
            timeout=Duration.seconds(aws["slack_timeout_seconds"]),
            environment={"BUCKET": self.bucket.bucket_name, "ACCOUNT_ID": str(aws["account_id"]),
                         "CLICKS_PREFIX": clicks, "MAX_AGE_SECONDS": str(aws["slack_max_age_seconds"]),
                         # the secrets' NAMES, not the secrets: read at run time, never shown here
                         "SIGNING_SECRET_PARAMETER": params[0], "BOT_TOKEN_PARAMETER": params[1]},
            log_group=logs.LogGroup(self, "SlackListenerLogs",
                                    retention=logs.RetentionDays.ONE_MONTH,
                                    removal_policy=RemovalPolicy.DESTROY),
        )
        # writes clicks, reads nothing. Written out by hand rather than grant_put: that would also
        # give kms:Decrypt (needed for big multipart uploads), and a click is one small file. So
        # the public-facing role can encrypt a click with the key, never decrypt anything.
        listener.add_to_role_policy(iam.PolicyStatement(
            actions=["s3:PutObject"], resources=[self.bucket.arn_for_objects(clicks + "*")]))
        listener.add_to_role_policy(iam.PolicyStatement(
            actions=["kms:GenerateDataKey"], resources=[self.key.key_arn]))
        listener.add_to_role_policy(iam.PolicyStatement(
            actions=["ssm:GetParameter"],
            resources=[f"arn:aws:ssm:{self.region}:{self.account}:parameter{p}" for p in params]))
        api = apigwv2.HttpApi(self, "SlackApi", description="Slack's button clicks (signature checked in the Lambda)",
                              create_default_stage=False)
        api.add_routes(path="/slack/actions", methods=[apigwv2.HttpMethod.POST],
                       integration=integrations.HttpLambdaIntegration("SlackClicks", listener))
        access_logs = logs.LogGroup(self, "SlackApiAccessLogs", retention=logs.RetentionDays.ONE_MONTH,
                                    removal_policy=RemovalPolicy.DESTROY)
        stage = api.add_stage(
            "Stage", stage_name="$default", auto_deploy=True,      # (id "Default" would clash with the API itself)
            throttle=apigwv2.ThrottleSettings(rate_limit=aws["slack_rate_per_second"], burst_limit=aws["slack_burst"]))
        # the access log: who called, when, what we answered (never the body). Set on the
        # CloudFormation stage directly: CDK's Python wrapper for it doesn't take plain settings
        stage.node.default_child.access_log_settings = apigwv2.CfnStage.AccessLogSettingsProperty(
            destination_arn=access_logs.log_group_arn,
            format=json.dumps({"requestId": "$context.requestId", "ip": "$context.identity.sourceIp",
                               "time": "$context.requestTime", "route": "$context.routeKey",
                               "status": "$context.status", "latencyMs": "$context.responseLatency"}))
        CfnOutput(self, "SlackListenerFunction", value=listener.function_name)
        CfnOutput(self, "SlackRequestUrl", value=f"{api.api_endpoint}/slack/actions")   # for the Slack app

        # --- the data perimeter: a second, independent check on the bucket itself ---
        # Each role's own policy already limits what it can do. These two statements make the
        # bucket refuse everyone else, whatever their own permissions say: an identity outside
        # this account, or any identity in it that isn't one of our four workload roles or the
        # admin (you, via aws login, plus the break-glass user). So a stolen key or a careless
        # "s3:*" policy elsewhere in the account still can't read or change the data.
        # Bucket-level settings (this policy itself, notifications) are left out on purpose: an
        # admin can always fix a mistake here, so this can never lock us out for good.
        acct = self.account
        workload_roles = [fn.role.role_arn, engine.role.role_arn, listener.role.role_arn, crawler_role.role_arn]
        admins = ([f"arn:aws:iam::{acct}:role/*{name}" for name in aws["admin_role_names"]]      # any IAM path
                  + [f"arn:aws:iam::{acct}:user/{name}" for name in aws["break_glass_users"]])
        not_a_service = {"BoolIfExists": {"aws:PrincipalIsAWSService": "false"}}
        self.bucket.add_to_resource_policy(iam.PolicyStatement(
            sid="DenyOutsideThisAccount", effect=iam.Effect.DENY, principals=[iam.AnyPrincipal()],
            actions=["s3:*"], resources=[self.bucket.bucket_arn, self.bucket.arn_for_objects("*")],
            conditions={"StringNotEquals": {"aws:PrincipalAccount": acct}, **not_a_service}))
        self.bucket.add_to_resource_policy(iam.PolicyStatement(
            sid="DenyDataToAnyoneElse", effect=iam.Effect.DENY, principals=[iam.AnyPrincipal()],
            actions=["s3:GetObject*", "s3:PutObject*", "s3:DeleteObject*", "s3:RestoreObject",
                     "s3:ListBucket*", "s3:ReplicateObject"],
            resources=[self.bucket.bucket_arn, self.bucket.arn_for_objects("*")],
            conditions={"ArnNotLike": {"aws:PrincipalArn": workload_roles + admins}, **not_a_service}))

        # --- GuardDuty, on trial ([aws] guardduty): watches the account's own logs for signs
        # of a break-in (credentials used from odd places, crypto-mining, data pulled out of the
        # bucket in bulk). Account-wide, so it's one switch: false removes it, findings included.
        # Only what this stack has: S3 data events and Lambda network activity. Plans for
        # services we don't run (EKS, EC2 malware scans, RDS, runtime agents) are off, so they
        # can't add cost.
        if aws.get("guardduty"):
            on = lambda name: guardduty.CfnDetector.CFNFeatureConfigurationProperty(name=name, status="ENABLED")
            off = lambda name: guardduty.CfnDetector.CFNFeatureConfigurationProperty(name=name, status="DISABLED")
            detector = guardduty.CfnDetector(
                self, "GuardDuty", enable=True, finding_publishing_frequency="FIFTEEN_MINUTES",
                features=[on("S3_DATA_EVENTS"), on("LAMBDA_NETWORK_LOGS"),
                          off("EKS_AUDIT_LOGS"), off("EBS_MALWARE_PROTECTION"),
                          off("RDS_LOGIN_EVENTS"), off("RUNTIME_MONITORING")])
            CfnOutput(self, "GuardDutyDetector", value=detector.ref)

        # --- IAM Access Analyzer: anything reachable from outside this account ---
        # Reads every resource policy in the account (buckets, roles' trust policies, Lambda
        # permissions, secrets, keys) and lists what another account or the public could reach.
        # The external-access analyzer is free; the separate "unused access" one is paid and left out.
        analyzer = accessanalyzer.CfnAnalyzer(self, "AccessAnalyzer", type="ACCOUNT",
                                             analyzer_name=aws["access_analyzer_name"])
        CfnOutput(self, "AccessAnalyzerArn", value=analyzer.attr_arn)

        # --- the key's own perimeter: only through S3 ---
        # The roles' key permissions are meant for S3 to use on their behalf when they read or
        # write a file. This makes the key refuse anyone who calls KMS directly (e.g. with a
        # stolen role credential, to decrypt a copied file outside the bucket's rules), except
        # the admins. Key management (its policy, rotation) isn't denied: no lockout.
        self.key.add_to_resource_policy(iam.PolicyStatement(
            sid="DenyUseOutsideS3", effect=iam.Effect.DENY, principals=[iam.AnyPrincipal()],
            actions=["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey*", "kms:ReEncrypt*"],
            resources=["*"],
            conditions={"StringNotEquals": {"kms:ViaService": f"s3.{self.region}.amazonaws.com"},
                        "ArnNotLike": {"aws:PrincipalArn": admins}, **not_a_service}))
        CfnOutput(self, "DataKeyArn", value=self.key.key_arn)

        # --- Cost Anomaly Detection (free): an email when a day's spend jumps ---
        # AWS learns this account's usual daily cost per service and flags a day that breaks
        # the pattern; the email goes out when the unexpected part reaches the threshold.
        # A "custom" monitor scoped to this account, not AWS's own services monitor: an
        # account may have only one of those, and AWS creates a default one for new accounts.
        # The address comes from settings.local.toml (the repo is public); none = not built.
        if aws.get("cost_alert_email"):
            monitor = ce.CfnAnomalyMonitor(
                self, "CostMonitor", monitor_name="cairnpath-account-spend", monitor_type="CUSTOM",
                monitor_specification=json.dumps({"Dimensions": {
                    "Key": "LINKED_ACCOUNT", "Values": [str(aws["account_id"])]}}))
            ce.CfnAnomalySubscription(
                self, "CostAlert", subscription_name="cairnpath-cost-alert", frequency="DAILY",
                monitor_arn_list=[monitor.attr_monitor_arn],
                subscribers=[ce.CfnAnomalySubscription.SubscriberProperty(
                    type="EMAIL", address=aws["cost_alert_email"])],
                threshold_expression=json.dumps({"Dimensions": {
                    "Key": "ANOMALY_TOTAL_IMPACT_ABSOLUTE", "MatchOptions": ["GREATER_THAN_OR_EQUAL"],
                    "Values": [str(aws["cost_alert_threshold_usd"])]}}))
