"""
access_report.py test: a stand-in Access Analyzer (no AWS, no cost).

    python tests/access_report_test.py

Our analyzer found by name among others; active findings listed, public ones first,
with who can reach them and under which condition; paginated results gathered; no
analyzer and no findings said plainly. With botocore installed, every request is also
checked against AWS's own API model (parameter names, shapes, allowed values), so a
spelling AWS would refuse fails here. Exit 1 on any FAIL.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import access_report as ar   # noqa: E402

failed = 0
REQUESTS = []                # (operation, params) as the script sent them
NAME = ar.AWS["access_analyzer_name"]


def check(ok, name, detail=""):
    global failed
    failed += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ->  {detail}" if not ok and detail else ""))


class FakeAA:
    def __init__(self, analyzers, findings):
        self.analyzers, self.findings = analyzers, findings

    def get_paginator(self, op):
        aa = self

        class P:
            def paginate(self, **kw):
                REQUESTS.append(("ListAnalyzers" if op == "list_analyzers" else "ListFindings", kw))
                if op == "list_analyzers":
                    yield {"analyzers": aa.analyzers[:1]}
                    yield {"analyzers": aa.analyzers[1:]}
                else:
                    for i in range(0, max(len(aa.findings), 1), 2):      # two per page
                        yield {"findings": aa.findings[i:i + 2]}
        return P()


ours = {"name": NAME, "arn": f"arn:aws:access-analyzer:eu-north-1:1:analyzer/{NAME}", "status": "ACTIVE"}
other = {"name": "someone-elses", "arn": "arn:aws:access-analyzer:eu-north-1:1:analyzer/x", "status": "ACTIVE"}
when = datetime(2026, 11, 5, tzinfo=timezone.utc)
findings = [
    {"resource": "arn:aws:lambda:eu-north-1:1:function:SlackListener", "resourceType": "AWS::Lambda::Function",
     "principal": {"Service": "apigateway.amazonaws.com"}, "isPublic": False, "action": ["lambda:InvokeFunction"],
     "condition": {"aws:SourceArn": "arn:aws:execute-api:eu-north-1:1:abc/*/*/slack/actions"}, "createdAt": when},
    {"resource": "arn:aws:s3:::open-bucket", "resourceType": "AWS::S3::Bucket", "principal": {"AWS": "*"},
     "isPublic": True, "action": ["s3:GetObject"], "createdAt": when},
    {"resource": "arn:aws:iam::1:role/Partner", "resourceType": "AWS::IAM::Role", "principal": {"AWS": "999999999999"},
     "isPublic": False, "action": ["sts:AssumeRole"], "createdAt": when},
]

print("\naccess_report.py")
text = ar.report(FakeAA([other, ours], findings))
check(f"Analyzer {NAME} (ACTIVE)" in text, "finds our analyzer by name, among others, across pages")
check("**Reachable from outside this account: 3**" in text, "gathers every active finding across pages")
rows = [l for l in text.splitlines() if l.startswith("| ") and "Resource" not in l and "---" not in l]
check(rows and "open-bucket" in rows[0] and "| YES |" in rows[0], "public ones listed first", rows)
check(any("Service apigateway.amazonaws.com" in r and "slack/actions" in r for r in rows),
      "says who can reach it and under which condition", rows)
check(all(kw.get("filter") == {"status": {"eq": ["ACTIVE"]}} for op, kw in REQUESTS if op == "ListFindings"),
      "asks for active findings only (archived ones are decided already)")
check("Nothing in this account is reachable" in ar.report(FakeAA([ours], [])), "no findings: said plainly")
check("deploy the stack first" in ar.report(FakeAA([other], [])), "no analyzer of ours: said plainly")

try:
    import botocore.session
    from botocore.validate import validate_parameters
except ImportError:
    print("  SKIP  requests vs AWS's API model (botocore not installed here)")
else:
    model = botocore.session.get_session().get_service_model("accessanalyzer")
    problems = []
    for op, kw in REQUESTS:
        try:
            validate_parameters(kw, model.operation_model(op).input_shape)
        except Exception as e:                                       # botocore names what's wrong
            problems.append(f"{op}: {e}")
        status = model.shape_for("FindingStatus").enum
        for value in kw.get("filter", {}).get("status", {}).get("eq", []):
            if value not in status:
                problems.append(f"{op}: status {value} not in {status}")
    check(not problems, "every request matches AWS's own API model (names, shapes, allowed values)", problems)

print("\nFAILED" if failed else "\nOK")
sys.exit(1 if failed else 0)
