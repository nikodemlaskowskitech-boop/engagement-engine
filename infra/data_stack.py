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
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_notifications as s3n
from constructs import Construct

from bundle import bundle_check_inputs
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
