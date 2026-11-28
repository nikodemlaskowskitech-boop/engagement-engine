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

And the public demo site: CloudFront in front of a second, private bucket that
holds only the sandbox role pages the engine builds from fake data.
"""

from aws_cdk import CfnOutput, Duration, Fn, RemovalPolicy, Size, Stack
from aws_cdk import aws_accessanalyzer as accessanalyzer
from aws_cdk import aws_apigatewayv2 as apigwv2
from aws_cdk import aws_apigatewayv2_authorizers as authorizers
from aws_cdk import aws_apigatewayv2_integrations as integrations
from aws_cdk import aws_athena as athena
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_ce as ce
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_cloudwatch_actions as cw_actions
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subs
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
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

from bundle import ENGINE_FILES, bundle_ask, bundle_check_inputs, bundle_engine, bundle_search, bundle_slack, search_mode
from settings import SETTINGS


# The site's content security policy: the page and its Google fonts, nothing else; no forms
# posting anywhere, never inside another site's frame.
SITE_CSP = ("default-src 'none'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


def site_csp(connect=()):
    """SITE_CSP, plus the addresses a page may call besides its own (26 Nov 2026: the Ask box's
    API and its sign-in page, from this deploy; nothing else)."""
    return SITE_CSP.replace("connect-src 'self'", " ".join(["connect-src 'self'", *connect]))


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
                         "BUDGET_SHARE": str(aws["budget_share"]),       # warn past this share of memory or time
                         "ENGINE_CODE": json.dumps(ENGINE_FILES),
                         "OPTIONAL_STEPS": json.dumps(aws["engine_optional_steps"]),
                         "BRIEFS_STEP": aws["briefs_step"],     # the model, only on an admin's request
                         # the key's NAME, not the key: the value is read at run time (never shown here)
                         "API_KEY_PARAMETER": aws["api_key_parameter"],
                         "LLM_PROVIDER": aws["briefs_llm_provider"],
                         "LEDGER_FILE": SETTINGS["llm"]["ledger_file"],
                         # ingest.py's events store: rebuilt every run, never uploaded
                         "STORE_FOLDER": SETTINGS["data"]["store_folder"] + "/"},
            retry_attempts=0,                                  # same data, same result: a retry wouldn't help
            # one run at a time: a second trigger waits in Lambda's queue (S3 events and the
            # schedule are asynchronous, so a throttled one is tried again, not lost)
            # (0 = no limit: possible only once the account has more than 10 slots in all)
            reserved_concurrent_executions=aws["engine_max_runs"] or None,
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
        # Its own narrow policy, not AWS's AWSGlueServiceRole: that managed policy lets a crawler
        # write and delete any S3 path containing "aws-glue-" in every bucket, ours included
        # (outputs/aws-glue-x/...), and use Glue, EC2 and CloudWatch account-wide (audit 6 Nov 2026).
        crawler_role = iam.Role(
            self, "CrawlerRole", assumed_by=iam.ServicePrincipal("glue.amazonaws.com", conditions={
                "StringEquals": {"aws:SourceAccount": self.account}}))           # only our own crawler
        self.bucket.grant_read(crawler_role, outputs + "tables/*")             # reads the tables, nothing else
        glue_db = f"arn:aws:glue:{self.region}:{self.account}"
        crawler_role.add_to_policy(iam.PolicyStatement(        # the table list: our database only
            actions=["glue:GetDatabase", "glue:GetTable", "glue:GetTables", "glue:CreateTable",
                     "glue:UpdateTable", "glue:DeleteTable", "glue:BatchDeleteTable",
                     "glue:GetPartition", "glue:GetPartitions", "glue:BatchGetPartition",
                     "glue:CreatePartition", "glue:BatchCreatePartition", "glue:UpdatePartition",
                     "glue:BatchUpdatePartition", "glue:DeletePartition", "glue:BatchDeletePartition"],
            resources=[f"{glue_db}:catalog", f"{glue_db}:database/{aws['glue_database']}",
                       f"{glue_db}:table/{aws['glue_database']}/*"]))
        crawler_role.add_to_policy(iam.PolicyStatement(        # its own logs, nothing else
            actions=["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
            resources=[f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws-glue/crawlers*"]))
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
                    # our own key, like everything else in the bucket (SSE_S3 used AWS's key)
                    encryption_configuration=athena.CfnWorkGroup.EncryptionConfigurationProperty(
                        encryption_option="SSE_KMS", kms_key=self.key.key_arn))))
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

        # --- the Ask box (26 Nov 2026): POST /ask, for the CS team only ---
        # Its own door and its own key (ARCHITECTURE.md O): a user pool for the CS team (no
        # sign-up: the admin makes each login), its own API and its own function. The gateway
        # checks the sign-in before any code runs. The function reads the engine's export and
        # its own state/ask/, holds the redacted documents only, and writes counts, never text.
        # Built only with the public site and its own address: the Ask box lives on the site's
        # pages, and its sign-in and its API accept that address only (26 Nov 2026: infra_test's
        # "site = false" build caught an Ask API left behind without a site).
        ask_fn, self.ask_origins = None, []
        if aws.get("ask") and aws.get("site") and aws.get("site_domain"):
            site_address = "https://" + aws["site_domain"]        # the pages the Ask box sits on
            team = cognito.UserPool(
                self, "TeamUsers",
                user_pool_name="cairnpath-team",
                self_sign_up_enabled=False,                       # only the admin makes logins
                sign_in_aliases=cognito.SignInAliases(email=True),
                auto_verify=cognito.AutoVerifiedAttrs(email=True),
                # whose page a login sees (an owner_id from the CS team, or "leader"): set by the
                # admin, read by the function; the cockpit's client can't write it (below)
                custom_attributes={"person": cognito.StringAttribute(min_len=1, max_len=40, mutable=True)},
                password_policy=cognito.PasswordPolicy(min_length=12, require_lowercase=True, require_uppercase=True,
                                                       require_digits=True, require_symbols=False,
                                                       temp_password_validity=Duration.days(3)),
                mfa=cognito.Mfa.OPTIONAL,
                mfa_second_factor=cognito.MfaSecondFactor(sms=False, otp=True),
                account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
                removal_policy=RemovalPolicy.DESTROY)             # demo logins: nothing to keep
            team_client = team.add_client(
                "Cockpit",
                generate_secret=False,                            # a web page can't keep a secret: PKCE instead
                auth_flows=cognito.AuthFlow(user_srp=True),
                o_auth=cognito.OAuthSettings(
                    flows=cognito.OAuthFlows(authorization_code_grant=True),
                    scopes=[cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL],
                    callback_urls=[site_address + "/"], logout_urls=[site_address + "/"]),
                supported_identity_providers=[cognito.UserPoolClientIdentityProvider.COGNITO],
                prevent_user_existence_errors=True,
                read_attributes=cognito.ClientAttributes().with_standard_attributes(email=True)
                                                        .with_custom_attributes("person"),
                write_attributes=cognito.ClientAttributes().with_standard_attributes(email=True),
                id_token_validity=Duration.hours(1), access_token_validity=Duration.hours(1),
                refresh_token_validity=Duration.hours(12))
            login = team.add_domain("TeamLogin", cognito_domain=cognito.CognitoDomainOptions(
                domain_prefix=aws["ask_login_prefix"]))

            ask_fn = lambda_.Function(
                self, "Ask",
                description="The Ask box: one signed-in person's question about an account on their page",
                runtime=lambda_.Runtime.PYTHON_3_13,
                architecture=lambda_.Architecture.ARM_64,
                handler="handler.handler",
                code=lambda_.Code.from_asset(str(bundle_ask())),
                memory_size=aws["ask_memory_mb"],
                timeout=Duration.seconds(aws["ask_timeout_seconds"]),
                ephemeral_storage_size=Size.mebibytes(aws["ask_disk_mb"]),
                # one at a time: the day's counts and its ledger are read and written back whole
                reserved_concurrent_executions=1,
                environment={"BUCKET": self.bucket.bucket_name, "ACCOUNT_ID": str(aws["account_id"]),
                             "EXPORT_KEY": outputs + "views_data.json",
                             "KEY_PARAMETER": aws["api_key_parameter"],     # the key's name, never the key
                             "CAIRNPATH_LLM_PROVIDER": aws["briefs_llm_provider"]},
                log_group=logs.LogGroup(self, "AskLogs", retention=logs.RetentionDays.ONE_MONTH,
                                        removal_policy=RemovalPolicy.DESTROY))
            ask_state = SETTINGS["ask"]["usage_key"].split("{")[0]          # state/ask/
            # Written out by hand, not grant_read: that also lets a role list every file name in the
            # bucket, and this one answers any signed-in login. Read: the export and its own folder.
            ask_fn.add_to_role_policy(iam.PolicyStatement(
                actions=["s3:GetObject"],
                resources=[self.bucket.arn_for_objects(outputs + "views_data.json"),
                           self.bucket.arn_for_objects(ask_state + "*")]))
            ask_fn.add_to_role_policy(iam.PolicyStatement(          # write: its counts and its ledger only
                actions=["s3:PutObject"], resources=[self.bucket.arn_for_objects(ask_state + "*")]))
            # list its own folder only, so a file not written yet reads as "missing", not "denied"
            ask_fn.add_to_role_policy(iam.PolicyStatement(
                actions=["s3:ListBucket"], resources=[self.bucket.bucket_arn],
                conditions={"StringLike": {"s3:prefix": [ask_state + "*"]}}))
            # the key, for S3 to use on its behalf (the key's own perimeter refuses any direct use)
            ask_fn.add_to_role_policy(iam.PolicyStatement(
                actions=["kms:Decrypt", "kms:GenerateDataKey"], resources=[self.key.key_arn]))
            ask_fn.add_to_role_policy(iam.PolicyStatement(
                actions=["ssm:GetParameter"],
                resources=[f"arn:aws:ssm:{self.region}:{self.account}:parameter{aws['api_key_parameter']}"]))

            ask_api = apigwv2.HttpApi(
                self, "AskApi", description="The Ask box (CS team sign-in checked at the gateway)",
                create_default_stage=False,
                # only the demo site's pages may call it from a browser (the sign-in is the real check)
                cors_preflight=apigwv2.CorsPreflightOptions(
                    allow_origins=[site_address], allow_methods=[apigwv2.CorsHttpMethod.POST],
                    allow_headers=["Authorization", "Content-Type"], max_age=Duration.hours(1)))
            ask_api.add_routes(
                path="/ask", methods=[apigwv2.HttpMethod.POST],
                integration=integrations.HttpLambdaIntegration("AskQuestions", ask_fn),
                # the ID token from the team's pool, for the cockpit's client only
                authorizer=authorizers.HttpUserPoolAuthorizer("TeamSignIn", team, user_pool_clients=[team_client]))
            ask_logs = logs.LogGroup(self, "AskApiAccessLogs", retention=logs.RetentionDays.ONE_MONTH,
                                     removal_policy=RemovalPolicy.DESTROY)
            ask_stage = ask_api.add_stage(
                "AskStage", stage_name="$default", auto_deploy=True,
                throttle=apigwv2.ThrottleSettings(rate_limit=aws["ask_rate_per_second"], burst_limit=aws["ask_burst"]))
            # when, which route, our answer: never the body (it holds the question)
            ask_stage.node.default_child.access_log_settings = apigwv2.CfnStage.AccessLogSettingsProperty(
                destination_arn=ask_logs.log_group_arn,
                format=json.dumps({"requestId": "$context.requestId", "time": "$context.requestTime",
                                   "route": "$context.routeKey", "status": "$context.status",
                                   "latencyMs": "$context.responseLatency"}))
            # the engine builds the site's pages: it tells them where to ask and where to sign in
            # (straight from this deploy: nothing copied by hand, nothing stale after a rebuild)
            ask_web = {"ASK_URL": ask_api.api_endpoint + "/ask", "ASK_CLIENT_ID": team_client.user_pool_client_id,
                       "ASK_LOGIN_URL": login.base_url(), "ASK_REDIRECT": site_address + "/"}
            for k, v in ask_web.items():
                engine.add_environment(k, v)
            # and the pages may call those two addresses, nothing else (the site's content policy)
            self.ask_origins = [ask_api.api_endpoint, login.base_url()]
            CfnOutput(self, "AskUrl", value=ask_api.api_endpoint + "/ask")
            CfnOutput(self, "AskUserPoolId", value=team.user_pool_id)
            CfnOutput(self, "AskClientId", value=team_client.user_pool_client_id)
            CfnOutput(self, "AskLoginUrl", value=login.base_url())

        # --- the data perimeter: a second, independent check on the bucket itself ---
        # Each role's own policy already limits what it can do. These two statements make the
        # bucket refuse everyone else, whatever their own permissions say: an identity outside
        # this account, or any identity in it that isn't one of our workload roles or the
        # admin (you, via aws login, plus the break-glass user). So a stolen key or a careless
        # "s3:*" policy elsewhere in the account still can't read or change the data.
        # Bucket-level settings (this policy itself, notifications) are left out on purpose: an
        # admin can always fix a mistake here, so this can never lock us out for good.
        acct = self.account
        workload_roles = [fn.role.role_arn, engine.role.role_arn, listener.role.role_arn, crawler_role.role_arn] \
            + ([ask_fn.role.role_arn] if ask_fn else [])
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

        # --- the public demo site: CloudFront in front of a private bucket of its own ---
        # Anyone with the address can open the role pages, no login. The bucket is NOT public:
        # all four public-access blocks on, and CloudFront is its only reader, through an origin
        # access control (the bucket policy names this one distribution). Never the data bucket:
        # the site bucket holds only the sandbox pages the engine builds from the demo data.
        # S3-managed encryption, not our KMS key: the pages are public on purpose, and CloudFront
        # would otherwise need a grant on the key that guards the real data.
        # The engine may only write pages there (no read of anything else, no delete).
        if aws.get("site"):
            site_bucket = s3.Bucket(
                self, "Site",
                block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
                object_ownership=s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,   # ACLs off
                encryption=s3.BucketEncryption.S3_MANAGED,
                enforce_ssl=True,
                removal_policy=RemovalPolicy.RETAIN)
            # the browser's own safety rules for every page: HTTPS only from now on (HSTS), no
            # framing by other sites, no MIME guessing, and a content policy that lets the page
            # load only itself and its fonts (inline script and style are how the page is built)
            headers = cloudfront.ResponseHeadersPolicy(
                self, "SiteHeaders",
                security_headers_behavior=cloudfront.ResponseSecurityHeadersBehavior(
                    strict_transport_security=cloudfront.ResponseHeadersStrictTransportSecurity(
                        access_control_max_age=Duration.days(365), include_subdomains=True, override=True),
                    content_type_options=cloudfront.ResponseHeadersContentTypeOptions(override=True),
                    frame_options=cloudfront.ResponseHeadersFrameOptions(
                        frame_option=cloudfront.HeadersFrameOption.DENY, override=True),
                    referrer_policy=cloudfront.ResponseHeadersReferrerPolicy(
                        referrer_policy=cloudfront.HeadersReferrerPolicy.NO_REFERRER, override=True),
                    content_security_policy=cloudfront.ResponseHeadersContentSecurityPolicy(
                        content_security_policy=site_csp(self.ask_origins), override=True)))
            # Our own address (optional): [aws] site_domain, with a certificate made by site_cert.py
            # in us-east-1 (the only region CloudFront takes certificates from). The DNS lives at
            # the registrar (Cloudflare), so its two records are added there by hand.
            domain, cert_arn = aws.get("site_domain", ""), aws.get("site_certificate_arn", "")
            if bool(domain) != bool(cert_arn):
                raise ValueError("[aws] site_domain and site_certificate_arn go together: run python site_cert.py")
            if cert_arn and ":us-east-1:" not in cert_arn:
                raise ValueError("[aws] site_certificate_arn must be in us-east-1 (CloudFront's rule)")
            own = {"domain_names": [domain],
                   "certificate": acm.Certificate.from_certificate_arn(self, "SiteCert", cert_arn),
                   "minimum_protocol_version": cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021} if domain else {}
            site = cloudfront.Distribution(
                self, "SiteCdn",
                comment="Cairnpath demo cockpit (sandbox pages, fake data)",
                default_root_object="index.html",
                default_behavior=cloudfront.BehaviorOptions(
                    origin=origins.S3BucketOrigin.with_origin_access_control(site_bucket),
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD,     # read only
                    cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,      # honours the pages' max-age
                    response_headers_policy=headers),
                price_class={"PriceClass_100": cloudfront.PriceClass.PRICE_CLASS_100,
                             "PriceClass_200": cloudfront.PriceClass.PRICE_CLASS_200,
                             "PriceClass_All": cloudfront.PriceClass.PRICE_CLASS_ALL}[aws["site_price_class"]],
                http_version=cloudfront.HttpVersion.HTTP2_AND_3, **own)
            site_bucket.grant_put(engine)                     # writes pages; reads and deletes nothing
            engine.add_to_role_policy(iam.PolicyStatement(    # the engine's "changed?" check lists names only
                actions=["s3:ListBucket"], resources=[site_bucket.bucket_arn]))
            engine.add_environment("SITE_BUCKET", site_bucket.bucket_name)
            engine.add_environment("SITE_FOLDER", aws["site_folder"])
            engine.add_environment("SITE_CACHE_SECONDS", str(aws["site_cache_seconds"]))
            # its own perimeter: only the engine and the admins may write or delete a page there,
            # whatever any other identity's permissions say (a defaced public site is the risk)
            site_bucket.add_to_resource_policy(iam.PolicyStatement(
                sid="DenyWritesToAnyoneElse", effect=iam.Effect.DENY, principals=[iam.AnyPrincipal()],
                actions=["s3:PutObject*", "s3:DeleteObject*", "s3:RestoreObject", "s3:ReplicateObject"],
                resources=[site_bucket.arn_for_objects("*")],
                conditions={"ArnNotLike": {"aws:PrincipalArn": [engine.role.role_arn] + admins}, **not_a_service}))
            CfnOutput(self, "SiteBucket", value=site_bucket.bucket_name)
            CfnOutput(self, "SiteDistributionId", value=site.distribution_id)   # for site_logs.py

            # --- visits (18 Nov 2026): CloudFront's access logs, in a private bucket of their own ---
            # site_logs.py asks CloudFront (from us-east-1, its rule) to write here, with only the
            # fields in [visits]: no IP address, no cookie. visitors.py counts them. Nobody reads
            # them but the admins; AWS's log delivery may only add files, for this account's own
            # delivery sources. Deleted after [aws] site_visit_days.
            if aws.get("site_visits"):
                visits = s3.Bucket(
                    self, "VisitLogs",
                    block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
                    object_ownership=s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
                    encryption=s3.BucketEncryption.S3_MANAGED,      # the log delivery gets no grant on our key
                    enforce_ssl=True,
                    lifecycle_rules=[s3.LifecycleRule(id="DeleteOldVisits",
                                                      expiration=Duration.days(aws["site_visit_days"]))],
                    removal_policy=RemovalPolicy.RETAIN)
                delivery = iam.ServicePrincipal("delivery.logs.amazonaws.com")
                ours = {"StringEquals": {"aws:SourceAccount": acct},
                        "ArnLike": {"aws:SourceArn": f"arn:aws:logs:us-east-1:{acct}:delivery-source:*"}}
                visits.add_to_resource_policy(iam.PolicyStatement(
                    sid="AWSLogDeliveryWrite", principals=[delivery], actions=["s3:PutObject"],
                    resources=[visits.arn_for_objects("*")],
                    conditions={"StringEquals": {**ours["StringEquals"], "s3:x-amz-acl": "bucket-owner-full-control"},
                                "ArnLike": ours["ArnLike"]}))
                visits.add_to_resource_policy(iam.PolicyStatement(
                    sid="AWSLogDeliveryAclCheck", principals=[delivery], actions=["s3:GetBucketAcl"],
                    resources=[visits.bucket_arn], conditions=ours))
                visits.add_to_resource_policy(iam.PolicyStatement(
                    sid="DenyVisitsToAnyoneElse", effect=iam.Effect.DENY, principals=[iam.AnyPrincipal()],
                    actions=["s3:GetObject*", "s3:PutObject*", "s3:DeleteObject*", "s3:ListBucket*"],
                    resources=[visits.bucket_arn, visits.arn_for_objects("*")],
                    conditions={"ArnNotLike": {"aws:PrincipalArn": admins}, **not_a_service}))
                CfnOutput(self, "VisitLogsBucket", value=visits.bucket_name)

            # --- the public search (13 Nov 2026): /search on the site's own address ---
            # Its own function, carrying only the demo documents redacted on the laptop
            # (search_export.py). Its role gets nothing beyond writing its own logs: no bucket,
            # no key, no secret. It never logs the question (see its handler).
            search_code = bundle_search()
            # hybrid only when the bundle carries up-to-date vectors (bundle.site_vectors)
            meaning = search_mode(search_code) == "hybrid"
            finder = lambda_.Function(
                self, "Search",
                description="Public demo search over redacted demo documents (no data access)",
                runtime=lambda_.Runtime.PYTHON_3_13,
                architecture=lambda_.Architecture.ARM_64,
                handler="handler.handler",
                code=lambda_.Code.from_asset(str(search_code)),
                memory_size=aws["search_memory_mb"],
                timeout=Duration.seconds(aws["search_timeout_seconds"]),
                # a ceiling of its own, so a flood of searches can't use the slots the engine and
                # the Slack listener need (possible since the account quota is 1000, 14 Nov 2026)
                reserved_concurrent_executions=aws["search_max_runs"] or None,
                environment={"TOP_K": str(SETTINGS["search"]["site_top_k"]),
                             "MAX_CHARS": str(aws["search_max_chars"]),
                             "CACHE_SECONDS": str(aws["search_cache_seconds"]),
                             "MODE": "hybrid" if meaning else "keywords",
                             "MCP": "on" if aws.get("mcp_public") else "off",
                             **({"MODEL": SETTINGS["embed"]["model"],
                                 "DIMS": str(SETTINGS["embed"]["dimensions"]),
                                 "REGION": aws["region"]} if meaning else {})},
                log_group=logs.LogGroup(self, "SearchLogs", retention=logs.RetentionDays.ONE_MONTH,
                                        removal_policy=RemovalPolicy.DESTROY))
            if meaning:
                # one model, one action: the question's vector from Titan, nothing else on Bedrock
                # (23 Nov 2026). Still no data bucket, key or secret. The gateway throttle and the
                # hour's cache bound what visitors can make it cost (README, cost).
                finder.add_to_role_policy(iam.PolicyStatement(
                    actions=["bedrock:InvokeModel"],
                    resources=[f"arn:aws:bedrock:{aws['region']}::foundation-model/{SETTINGS['embed']['model']}"]))
            search_api = apigwv2.HttpApi(self, "SearchApi", description="The demo site's search (through CloudFront)",
                                         create_default_stage=False)
            search_api.add_routes(path="/search", methods=[apigwv2.HttpMethod.GET],
                                  integration=integrations.HttpLambdaIntegration("SearchQueries", finder))
            mcp_routes = []
            if aws.get("mcp_public"):
                # the public MCP server (24 Nov 2026): one more route on the same function, POST only
                mcp_routes = search_api.add_routes(path="/mcp", methods=[apigwv2.HttpMethod.POST],
                                                   integration=integrations.HttpLambdaIntegration("McpCalls", finder))
            search_logs = logs.LogGroup(self, "SearchApiAccessLogs", retention=logs.RetentionDays.ONE_MONTH,
                                        removal_policy=RemovalPolicy.DESTROY)
            search_stage = search_api.add_stage(
                "SearchStage", stage_name="$default", auto_deploy=True,
                # one limit for everyone: a flood is cut off at the gateway, before the function
                throttle=apigwv2.ThrottleSettings(rate_limit=aws["search_rate_per_second"],
                                                  burst_limit=aws["search_burst"]))
            if aws.get("mcp_public"):
                # its own, lower limit: MCP clients may call in bursts, and the search page comes first
                search_stage.node.default_child.add_property_override("RouteSettings", {
                    "POST /mcp": {"ThrottlingRateLimit": aws["mcp_rate_per_second"],
                                  "ThrottlingBurstLimit": aws["mcp_burst"]}})
                # the route first, then the stage that limits it: without this CloudFormation updated
                # the stage first and API Gateway refused a limit on a route it didn't have yet
                # (24 Nov 2026, the first deploy: "Unable to find Route by key POST /mcp")
                stage = search_stage.node.default_child
                # add_resource_dependency is the current name; add_dependency is deprecated (CDK
                # warned on 24 Nov 2026) but kept as the fallback for an older aws-cdk-lib
                depend = getattr(stage, "add_resource_dependency", None) or stage.add_dependency
                for r in mcp_routes:
                    depend(r.node.default_child)
            # the access log: when and what we answered, never the question (the route has no
            # query string in it: "GET /search")
            search_stage.node.default_child.access_log_settings = apigwv2.CfnStage.AccessLogSettingsProperty(
                destination_arn=search_logs.log_group_arn,
                format=json.dumps({"requestId": "$context.requestId", "time": "$context.requestTime",
                                   "route": "$context.routeKey", "status": "$context.status",
                                   "latencyMs": "$context.responseLatency"}))
            # CloudFront sends /search to the API and keeps each answer [aws] search_cache_seconds
            # (the function's Cache-Control): the same question twice reaches the function once.
            # Only "q" is passed on: no cookies, no viewer headers.
            search_cache = cloudfront.CachePolicy(
                self, "SearchCache",
                comment="Demo search answers, by question",
                query_string_behavior=cloudfront.CacheQueryStringBehavior.allow_list("q"),
                header_behavior=cloudfront.CacheHeaderBehavior.none(),
                cookie_behavior=cloudfront.CacheCookieBehavior.none(),
                min_ttl=Duration.seconds(0), default_ttl=Duration.seconds(0),
                max_ttl=Duration.seconds(aws["search_cache_seconds"]),
                enable_accept_encoding_gzip=True, enable_accept_encoding_brotli=True)
            search_origin = origins.HttpOrigin(Fn.select(2, Fn.split("/", search_api.api_endpoint)),
                                               protocol_policy=cloudfront.OriginProtocolPolicy.HTTPS_ONLY)
            site.add_behavior(
                "/search",
                search_origin,
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD,
                cache_policy=search_cache,
                response_headers_policy=headers)
            if aws.get("mcp_public"):
                # /mcp: never cached (each call is a POST with a body), and only the headers MCP
                # uses are passed on: no cookies, no query string
                site.add_behavior(
                    "/mcp", search_origin,
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                    cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                    origin_request_policy=cloudfront.OriginRequestPolicy(
                        self, "McpRequests", comment="MCP calls: the body and MCP's own headers only",
                        header_behavior=cloudfront.OriginRequestHeaderBehavior.allow_list(
                            "Accept", "Content-Type", "Mcp-Protocol-Version", "Mcp-Session-Id"),
                        query_string_behavior=cloudfront.OriginRequestQueryStringBehavior.none(),
                        cookie_behavior=cloudfront.OriginRequestCookieBehavior.none()),
                    response_headers_policy=headers)
                CfnOutput(self, "McpUrl", value="https://" + (domain or site.distribution_domain_name) + "/mcp")
            CfnOutput(self, "SearchFunction", value=finder.function_name)
            CfnOutput(self, "SearchUrl", value="https://" + (domain or site.distribution_domain_name) + "/search?q=")
            CfnOutput(self, "SiteUrl", value="https://" + (domain or site.distribution_domain_name))
            # what the domain's CNAME record points at (the DNS entry at the registrar)
            CfnOutput(self, "SiteCloudFrontDomain", value=site.distribution_domain_name)

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

        # --- Run alerts (28 Nov 2026): an email when a run fails, or nears its limits ---
        # The 28 Nov run failed at 06:17 and nobody knew until someone looked. Now: the engine or
        # the check failing, or a run past [aws] budget_share of its memory or time (the engine
        # logs "BUDGET WARNING"), sends an email to the same address as the cost alert. AWS asks
        # once to confirm the address (a link in the first email). Alarm names only: no data.
        if aws.get("cost_alert_email"):
            alerts = sns.Topic(self, "RunAlerts", display_name="Cairnpath run alerts", enforce_ssl=True)
            alerts.add_subscription(subs.EmailSubscription(aws["cost_alert_email"]))
            to_email = cw_actions.SnsAction(alerts)
            for name, fn, what in (("EngineFailed", engine, "the engine run failed: see outputs/last_run.txt"),
                                   ("CheckFailed", fn, "the input check failed to run")):
                alarm = cloudwatch.Alarm(
                    self, name, alarm_description=what,
                    metric=fn.metric_errors(period=Duration.hours(1), statistic="Sum"),
                    threshold=1, evaluation_periods=1,
                    comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                    treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING)
                alarm.add_alarm_action(to_email)
            budget = logs.MetricFilter(
                self, "BudgetWarnings", log_group=engine.log_group,
                filter_pattern=logs.FilterPattern.all_terms("BUDGET", "WARNING"),
                metric_namespace="Cairnpath", metric_name="BudgetWarnings", metric_value="1")
            near = cloudwatch.Alarm(
                self, "EngineNearLimit",
                alarm_description=f"an engine run used over {int(aws['budget_share'] * 100)}% of its memory or time: see run.json",
                metric=budget.metric(period=Duration.hours(1), statistic="Sum"),
                threshold=1, evaluation_periods=1,
                comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING)
            near.add_alarm_action(to_email)

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
