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
