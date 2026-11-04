"""
DataStack: one private S3 bucket for the pipeline's data (inputs, outputs,
briefs) once it runs in AWS.

Safe by default, each line on purpose:
  - encrypted at rest (S3-managed keys)
  - no public access, ever (all four "block public access" switches on)
  - HTTPS only (a bucket policy refuses plain HTTP)
  - versioned: an overwrite or delete keeps the old copy, so a bad run can be
    rolled back; old copies expire after [aws] keep_old_versions_days
  - kept if the stack is deleted (RETAIN): data outlives the code that made it

And the first piece of code that runs in AWS: a Lambda that checks the inputs
after each upload (lambdas/check_inputs/handler.py). It wakes up only when
upload.py writes its "upload complete" marker, may only read inputs/ and write
reports/check_inputs/, and runs the same check_inputs.py as the laptop.
"""

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Size, Stack
from aws_cdk import aws_athena as athena
from aws_cdk import aws_glue as glue
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_notifications as s3n
from aws_cdk import aws_scheduler as scheduler
from constructs import Construct

import json

from bundle import ENGINE_FILES, bundle_check_inputs, bundle_engine
from settings import SETTINGS


class DataStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        keep_days = SETTINGS["aws"]["keep_old_versions_days"]

        self.bucket = s3.Bucket(
            self, "Data",
            encryption=s3.BucketEncryption.S3_MANAGED,
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
                         "ENGINE_STEPS": json.dumps(aws["engine_steps"]),
                         "ENGINE_CODE": json.dumps(ENGINE_FILES),
                         "OPTIONAL_STEPS": json.dumps(aws["engine_optional_steps"]),
                         # the key's NAME, not the key: the value is read at run time (never shown here)
                         "API_KEY_PARAMETER": aws["api_key_parameter"],
                         "LLM_PROVIDER": aws["briefs_llm_provider"]},
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
