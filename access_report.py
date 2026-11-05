"""
What in this account can be reached from outside it, according to IAM Access
Analyzer: no console needed.

    python access_report.py          active findings: what, reachable by whom, under which condition
    python access_report.py --save   also write the report to data/access_report.md

Read-only. The analyzer (settings.toml [aws] access_analyzer_name, built by the CDK
stack) reads every resource policy in the account: the bucket's, each role's trust
policy, Lambda permissions, secrets, keys. A finding means another account, an AWS
service on someone else's behalf, or the public could reach that resource. Each one is
either on purpose (then archive it with a reason) or a hole to close.

Sign in first: aws login --profile cairnpath.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from settings import SETTINGS

AWS = SETTINGS["aws"]
OUT = Path(__file__).resolve().parent / "data" / "access_report.md"


def analyzer_arn(aa):
    """Our analyzer's ARN, found by name; None when it isn't deployed."""
    for page in aa.get_paginator("list_analyzers").paginate(type="ACCOUNT"):
        for a in page["analyzers"]:
            if a["name"] == AWS["access_analyzer_name"]:
                return a["arn"], a.get("status")
    return None, None


def who(principal):
    """The principal map in plain words: {"AWS": "123..."} -> AWS 123..."""
    return ", ".join(f"{k} {v}" for k, v in (principal or {}).items()) or "unknown"


def report(aa):
    arn, status = analyzer_arn(aa)
    if not arn:
        return f"No analyzer called {AWS['access_analyzer_name']} in {AWS['region']}: deploy the stack first."
    findings = []
    for page in aa.get_paginator("list_findings").paginate(
            analyzerArn=arn, filter={"status": {"eq": ["ACTIVE"]}}):
        findings += page["findings"]
    lines = [f"# Access Analyzer report, {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC", "",
             f"Analyzer {AWS['access_analyzer_name']} ({status}) in {AWS['region']}.", "",
             f"**Reachable from outside this account: {len(findings)}**", ""]
    if findings:
        lines += ["| Resource | Type | Reachable by | Public | Actions | Condition |",
                  "| --- | --- | --- | --- | --- | --- |"]
        for f in sorted(findings, key=lambda f: (not f.get("isPublic"), f.get("resourceType", ""), f.get("resource", ""))):
            actions = ", ".join(f.get("action") or []) or "-"
            condition = json.dumps(f.get("condition") or {}, default=str) if f.get("condition") else "-"
            lines.append(f"| {f.get('resource', '?').split(':')[-1]} | {f.get('resourceType', '?')} | "
                         f"{who(f.get('principal'))} | {'YES' if f.get('isPublic') else 'no'} | {actions} | {condition} |")
        lines += ["", "Each is either on purpose (archive it, with the reason, so it stops showing) or a hole to close."]
    else:
        lines += ["Nothing in this account is reachable by another account or the public."]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    import boto3
    session = boto3.Session(profile_name=AWS.get("profile") or None, region_name=AWS["region"])
    text = report(session.client("accessanalyzer"))
    print(text)
    if "--save" in sys.argv:
        OUT.parent.mkdir(exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"Saved to {OUT.relative_to(Path(__file__).resolve().parent)}")
