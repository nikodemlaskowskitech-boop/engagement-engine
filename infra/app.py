"""
The AWS side of the engine, as code (AWS CDK, Phase 6).

`cdk deploy` (run from this folder, see infra/README.md) builds what the
stacks describe, in the account and region from settings.toml [aws]. Nothing
here names an account, region or bucket directly: another team edits
settings.toml, never this file.
"""

import sys
from pathlib import Path

import aws_cdk as cdk

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # the repo root, for settings.py
from settings import SETTINGS          # noqa: E402
from data_stack import DataStack       # noqa: E402

AWS = SETTINGS["aws"]

app = cdk.App()
DataStack(app, "CairnpathData",
          env=cdk.Environment(account=str(AWS["account_id"]), region=AWS["region"]),
          description="Cairnpath engagement engine: the data bucket (Phase 6)")
cdk.Tags.of(app).add("project", "cairnpath")        # every resource says what it belongs to (cost reports)
app.synth()
