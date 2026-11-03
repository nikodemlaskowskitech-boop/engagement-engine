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
