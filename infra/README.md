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

## The Slack listener in AWS

Button clicks on the task cards (I'll take it, Log call, Approve hand-back) go
to a public address in AWS instead of `python listener.py` on the laptop. Every
request is checked against Slack's signature before anything happens; see
`lambdas/slack/handler.py`. Clicks are saved under `state/clicks/` and the
engine picks them up on its next run (the daily run, at the latest).

Once, after the deploy that creates it:

1. In `.env`, add `SLACK_SIGNING_SECRET=` with the value from the Slack app
   page (api.slack.com/apps > your app > Basic Information > App Credentials >
   Signing Secret). `SLACK_BOT_TOKEN` is already there.
2. From the repo root: `python put_key.py --slack` (stores both in Parameter
   Store, encrypted; prints only their names).
3. In the Slack app: **Socket Mode** > turn it off. Then **Interactivity &
   Shortcuts** > Request URL = the `SlackRequestUrl` that `cdk deploy` printed
   > Save Changes. Slack only accepts the address if it answers, so do this
   after step 2.
4. Click a button on a card. In CloudWatch Logs (the SlackListener function's
   log group) you'll see one line per click, and a file appears under
   `state/clicks/` in the bucket.
5. `python security_check.py`: now 26 questions, including the listener's.

To go back to the laptop listener: turn Socket Mode on again (the Request URL
is then ignored) and run `python listener.py`.

## The data perimeter (bucket policy)

Since 4 Nov 2026 the bucket itself refuses everyone except our four workload
roles (check, engine, Slack listener, crawler), the role `aws login` gives you
(settings.toml [aws] admin_role_names) and the break-glass user
([aws] break_glass_users). Their own permissions don't matter: an identity
with `s3:*` on everything is still refused the data. Identities outside the
account are refused everything.

If you ever lock yourself out of the data (say, the admin role is renamed),
the bucket's own settings are left open on purpose, so you can still fix it:
correct `admin_role_names` and `cdk deploy`, or as a last resort
`aws s3api delete-bucket-policy` (that also drops HTTPS-only until the next
deploy puts it back). `python security_check.py` checks both sides after
every deploy: the outsider is refused, the admin still gets in.

## GuardDuty (on trial)

`[aws] guardduty = true` turns on GuardDuty for this account and region: it
reads the account's own logs (API calls, the bucket's data events, the
Lambdas' network activity) and raises findings such as credentials used from
an unusual place, crypto-mining, or data pulled from the bucket in bulk.
Plans for services we don't run are off.

It's on the 30-day free trial from 5 Nov 2026. After that it charges, so
decide before about 5 Dec 2026:

```
python guardduty_report.py             what it found, and what the usage so far would have cost
python guardduty_report.py --samples   once: sample findings, to see what they look like
python guardduty_report.py --save      the same, written to data/guardduty_report.md
```

To switch it off: `guardduty = false` in settings.toml, then `cdk deploy`. That
deletes the detector and its findings, so `--save` first.
