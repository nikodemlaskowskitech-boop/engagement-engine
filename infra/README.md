# infra: the engine's AWS side, as code (CDK)

What's here: `app.py` (reads settings.toml [aws]) and `data_stack.py` (one
private, encrypted, versioned S3 bucket). Tested by `tests/infra_test.py`.

Run from this folder, in PowerShell, with the project environment active:

```
cd infra
aws login --profile cairnpath             # if the session expired
cdk bootstrap --profile cairnpath         # once per account and region: CDK's own deploy roles and bucket
cdk diff --profile cairnpath              # what would change, before changing it
cdk deploy --profile cairnpath            # build it; prints the bucket name
```

Needs Node.js (for the `cdk` command: `npm install -g aws-cdk`) and the pinned
`aws-cdk-lib` and `constructs` from requirements.txt.

After a deploy, put the printed bucket name in settings.toml [aws] bucket. Then,
from the repo root, `python upload.py --dry-run` and `python upload.py` copy the
engine's inputs into it.

What runs in AWS today: the check Lambda (CheckInputs). Each `python upload.py`
that sends something writes a marker last; the Lambda wakes on it, checks the
inputs with check_inputs.py and writes `reports/check_inputs/latest.txt` (the
report) and `latest.json` (status OK / WARN / ERROR) in the bucket. Its logs are
in CloudWatch Logs (the function's log group), kept 30 days.

And after each clean check, the engine Lambda (RunEngine) runs the engine and
writes its results to `outputs/` in the bucket: the flags, `views_data.json`,
the cockpit page `views.html`, `last_run.txt` (every step's output) and
`run.json` (OK / SKIPPED / FAILED). The first `cdk diff` or `cdk deploy` after
a DuckDB version change downloads DuckDB built for Lambda (a minute); later
runs reuse it from infra/build/.

Athena (ask your own questions of the results): after the engine has run once,
`aws glue start-crawler --name <Crawler output> --profile cairnpath` turns each
folder under `outputs/tables/` into a table in the Glue database `cairnpath`
(about a minute; rerun only when a table's columns change). Then in the Athena
console pick workgroup `cairnpath` and database `cairnpath`, and query.

Handover briefs in AWS: once, from the repo root, `python put_key.py` copies the
API key from your `.env` into Parameter Store (encrypted, never printed). Without
it the engine still runs and says "briefs skipped: no API key" in run.json.
