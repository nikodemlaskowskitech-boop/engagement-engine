"""
guardduty_report.py test: a stand-in GuardDuty (no AWS, no cost).

    python tests/guardduty_report_test.py

Real findings counted by severity and type, samples left out, more than 50
findings fetched in batches, the cost estimate summed, --samples creates
samples, and GuardDuty switched off is said plainly. Exit 1 on any FAIL.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import guardduty_report as g   # noqa: E402

failed = 0


def check(ok, name, detail=""):
    global failed
    failed += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ->  {detail}" if not ok and detail else ""))


def finding(i, kind, sev, sample=False):
    service = {"AdditionalInfo": {"Value": json.dumps({"sample": True}), "Type": "default"}} if sample else {"Count": 1}
    # like boto3 on 5 Nov 2026: some fields come back as Python dates, not text
    service["EventFirstSeen"] = datetime(2026, 11, 5, 10, 0, tzinfo=timezone.utc)
    updated = datetime(2026, 11, 5 + i % 3, 10, 0, tzinfo=timezone.utc) if i % 2 else f"2026-11-{5 + i % 3:02d}T10:00:00Z"
    return {"Id": f"f{i}", "Type": kind, "Severity": sev, "UpdatedAt": updated, "Service": service}


class FakeGD:
    def __init__(self, findings, detectors=("det1",)):
        self.findings, self.detectors, self.batches, self.samples_made = findings, list(detectors), [], 0

    def list_detectors(self):
        return {"DetectorIds": self.detectors}

    def create_sample_findings(self, DetectorId):
        self.samples_made += 1

    def get_paginator(self, name):
        gd = self

        class P:
            def paginate(self, DetectorId):
                ids = [f["Id"] for f in gd.findings]
                for i in range(0, max(len(ids), 1), 40):
                    yield {"FindingIds": ids[i:i + 40]}
        return P()

    def get_findings(self, DetectorId, FindingIds):
        assert len(FindingIds) <= 50, "GuardDuty takes at most 50 IDs per call"
        self.batches.append(len(FindingIds))
        return {"Findings": [f for f in self.findings if f["Id"] in FindingIds]}

    def get_usage_statistics(self, **kw):
        assert kw["UsageStatisticType"] == "SUM_BY_FEATURE"
        return {"UsageStatistics": {"SumByFeature": [
            {"Feature": "CLOUD_TRAIL", "Total": {"Amount": "0.12", "Unit": "USD"}},
            {"Feature": "S3_DATA_EVENTS", "Total": {"Amount": "0.30", "Unit": "USD"}}]}}


print("\nguardduty_report.py")
fs = ([finding(i, "UnauthorizedAccess:IAMUser/ConsoleLoginSuccess.B", 5.0) for i in range(3)]
      + [finding(3, "CryptoCurrency:Runtime/BitcoinTool.B", 8.0)]
      + [finding(i, "Recon:IAMUser/MaliciousIPCaller", 2.0, sample=True) for i in range(4, 60)])
gd = FakeGD(fs)
text = g.report(gd)
check("**Real findings: 4** (samples: 56" in text, "real findings counted, samples left out", text[:200])
check(text.index("| High | CryptoCurrency") < text.index("| Medium | UnauthorizedAccess"),
      "high severity listed first", text)
check("| Medium | UnauthorizedAccess:IAMUser/ConsoleLoginSuccess.B | 3 |" in text, "counted by severity and type")
check(max(gd.batches) <= 50 and sum(gd.batches) == len(fs), "60 findings fetched in batches of 50 or fewer", gd.batches)
check("Total: 0.42 USD" in text and "S3_DATA_EVENTS: 0.30 USD" in text, "the cost estimate summed by feature")
check(gd.samples_made == 0, "read-only by default: no samples created")
g.report(gd, samples=True)
check(gd.samples_made == 1, "--samples creates sample findings once")
check("off in this region" in g.report(FakeGD([], detectors=())), "GuardDuty switched off: said plainly")

print("\nFAILED" if failed else "\nOK")
sys.exit(1 if failed else 0)
