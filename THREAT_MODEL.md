# Threat model (one page)

What could go wrong with the Cairnpath engine as it runs today (laptop + AWS
stack `CairnpathData`), what stops it, and how we know the control works.
Written 5 Nov 2026. Review it whenever a new way in is added (a new public
address, a new secret, a new place data goes).

## What we protect

| Asset | Why it matters | Where it lives |
| --- | --- | --- |
| Account data (CRM, usage events, CS history, flags) | Fake here, but handled as if it were customers' | S3 bucket (KMS key), laptop `data/` |
| People's details in the stories (names, emails, phones) | Must never reach the model | `stories/`, redaction keys `_key.json` (laptop / inside the Lambda only) |
| Secrets: Anthropic API key, Slack signing secret and bot token | Spend money, post as the app | `.env` (laptop), Parameter Store SecureStrings (AWS) |
| The AWS account and the Anthropic account | A bill that grows on its own | AWS, Anthropic Console |
| Managers' decisions in the cockpit | Drive who gets which task | The cockpit page's database |

## Ways in (trust boundaries)

1. **Internet → API Gateway → Slack listener**: the only public address.
2. **Laptop → AWS**: `aws login` (admin role), upload.py, cdk deploy.
3. **AWS / laptop → Anthropic API**: redacted text leaves for the model.
4. **Text written by outsiders → the model**: customer emails, call
   transcripts and support cases are read by the model (prompt injection).
5. **Public GitHub repo**: anyone can read the code; a pushed secret is public.
6. **Cockpit page (claude.ai artifact)**: shared by link.

## Threats, controls, proof

| # | Threat | Control | Proven by | What's left |
| --- | --- | --- | --- | --- |
| 1 | A forged or replayed Slack click | HMAC signature with the signing secret, 5-minute window, 401 before any work | `slack_lambda_test.py` (unsigned, wrong, tampered, stale) | None known |
| 2 | A flood of requests to the public address (cost, noise) | API Gateway throttle 5/s, burst 20; Lambda timeout 10 s | `infra_test.py` | A flood still costs a little until throttled |
| 3 | A workload role's credentials stolen | One role per job, least privilege; the Slack listener can't read anything | `security_check.py` (39 live questions) | The engine role can still read all inputs, by design |
| 4 | A careless `s3:*` policy elsewhere in the account | Data perimeter: the bucket refuses everyone but 4 roles and the admins | `security_check.py` perimeter cases | Admins are trusted |
| 5 | Data or a resource reachable from outside | Public access blocks, DenyOutsideThisAccount, HTTPS only | `infra_test.py`, Access Analyzer (`access_report.py`: 0) | None known |
| 6 | Encrypted data copied out and decrypted | Our own KMS key, usable only through S3 (except admins), every use logged | `security_check.py` key cases | A copy made through S3 by an allowed role |
| 7 | A secret leaked (repo, logs, chat) | `.env` and Parameter Store only, never printed; push protection; spend limit | `put_key_test.py`, `slack_lambda_test.py` | Push protection and spend limit are Nikodem's clicks: confirm |
| 8 | People's details sent to the model | Redaction (labels, contact list), then a leak check that blocks the call | `handover_test.py`, `leak_check_test.py`, `contacts_test.py` | Unknown names in free text not shaped like names (PRIVACY.md, upgrade B parked) |
| 9 | Prompt injection: an email tells the model to say something false | The model has no tools and can't act; every claim must cite a source and is checked; the brief stays a draft until a person confirms it | `handover_test.py` (forbidden claims), cockpit Confirm | A misleading but cited brief: the person confirming is the last check |
| 10 | The engine tampering with its own inputs or the input check's verdict | Write access to `outputs/` only; the check writes only `reports/` | `security_check.py` | None known |
| 11 | A bad run or accidental delete | Versioning (30 days), Retain on bucket and key, no delete permission for any role, 30-day key deletion window | `infra_test.py` | Old versions expire after 30 days |
| 12 | A runaway bill (AWS or model calls) | AWS: Cost Anomaly Detection email from $5, Athena scan cap, GuardDuty trial with a decision date. Model calls: a budget per feature checked before every call, every call in the AI spend ledger, the Anthropic spend limit as the ceiling | `infra_test.py`, `ai_spend_test.py`, weekly GuardDuty report | Anomaly detection needs ~10 days of history; the ledger is our estimate, not Anthropic's bill (no reconciliation yet) |
| 13 | Someone active in the account who shouldn't be | GuardDuty (trial), KMS use in CloudTrail | `guardduty_report.py` | After 5 Dec only if we keep GuardDuty |
| 14 | The cockpit link shared too widely | The export holds counts and account names, never user IDs; the page is private until shared; decisions are checked by role | `cockpit_test.js` (the refusals) | Anyone given the link sees the whole book |
| 16 | Someone's action makes the model run (a click, a cockpit decision, an upload), indirectly running up the bill | No automatic run calls the model: the engine only checks the briefs for free (`handover.py --dry-run`, "Out of date" in the cockpit); briefs are written only when the admin asks (`request_briefs.py`, the engine started with {"briefs": true}); the API key is read only then | `engine_lambda_test.py`, `infra_test.py`, `request_briefs_test.py` | Briefs go stale until the admin asks |
| 15 | Someone with edit access writes to the cockpit's database directly, skipping its forms (bad values, someone else's account, a malformed entry to stop the pipeline) | Never trust the page: `cockpit_sync.py` checks every decision again against the CRM and CS team (known account and owner, allowed values, real dates, amounts in range, an owner only on their own accounts, a manager only on their team's); a bad one is skipped and listed, never a crash; free text cut and cleaned | `cockpit_sync_test.py` | Who someone is stays self-declared (next line) |

## Risks we accept, on purpose

- **Workloads in the management account.** The first thing an auditor would
  flag. All in code, so it can move to its own account.
- **No network isolation (VPC).** Identity checks every call; a NAT gateway
  alone would cost more than the project (decided 4 Nov 2026).
- **One admin role plus a break-glass user**, both trusted by the perimeter.
- **Two engine runs can overlap, for now.** One at a time needs a reserved
  slot, and a new account has only 10, all kept free by AWS. Overlaps are rare
  (an upload, the 06:17 schedule, an admin's briefs request) and only the last
  run's outputs stay; the quota increase was asked on 6 Nov 2026, then
  [aws] engine_max_runs = 1 (audit F7).
- **Who did what in the cockpit is self-declared.** The page acts as whichever
  owner or manager the viewer picks in its segment bar; every saved decision
  also records the viewer's real account (`decided_by`), so it's traceable, not
  prevented. A real deployment signs people in (SSO), maps each login to an
  owner, and checks it on the server. In the demo one person plays every role.
- **No organization-wide controls (RCP)**: one account, nothing to gain yet.
- **Fake data.** With real customer data, items 3, 8 and 9 would need more
  (a PII detector, a review of what the engine role reads, a human check on
  every brief before it's sent anywhere).
