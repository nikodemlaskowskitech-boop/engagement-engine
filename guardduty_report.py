"""
What GuardDuty has seen, and what it would cost: no console needed.

    python guardduty_report.py             findings by severity and type, plus the cost estimate
    python guardduty_report.py --samples   first create one sample finding of each type (free,
                                           marked [SAMPLE]), so you can see what they look like
    python guardduty_report.py --save      also write the report to data/guardduty_report.md

Read-only, apart from --samples. GuardDuty is on trial (settings.toml [aws] guardduty): the
cost estimate is what its usage so far would have cost without the trial, the number to weigh
against what it found before the trial ends.

Sign in first: aws login --profile cairnpath.
"""

import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from settings import SETTINGS

AWS = SETTINGS["aws"]
OUT = Path(__file__).resolve().parent / "data" / "guardduty_report.md"


def severity(score):
    """GuardDuty's own bands: 7+ high, 4 to 6.9 medium, below 4 low."""
    return "High" if score >= 7 else "Medium" if score >= 4 else "Low"


def is_sample(finding):
    """Sample findings say so in their service details (the field's spelling varies by API version)."""
    service = json.dumps(finding.get("Service", {})).lower().replace("\\", "").replace(" ", "")
    return '"sample":true' in service


def report(gd, samples=False):
    detectors = gd.list_detectors()["DetectorIds"]
    if not detectors:
        return "GuardDuty is off in this region (settings.toml [aws] guardduty = false)."
    det = detectors[0]
    if samples:
        gd.create_sample_findings(DetectorId=det)
    ids = []
    for page in gd.get_paginator("list_findings").paginate(DetectorId=det):
        ids += page["FindingIds"]
    findings = []
    for i in range(0, len(ids), 50):                     # the API takes 50 at a time
        findings += gd.get_findings(DetectorId=det, FindingIds=ids[i:i + 50])["Findings"]
    real = [f for f in findings if not is_sample(f)]
    sample = len(findings) - len(real)

    lines = [f"# GuardDuty report, {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC", "",
             f"Detector {det} in {AWS['region']}.", "",
             f"**Real findings: {len(real)}** (samples: {sample}, ignored below).", ""]
    if real:
        lines += ["| Severity | Type | Count | Last seen |", "| --- | --- | --- | --- |"]
        by = Counter((severity(f["Severity"]), f["Type"]) for f in real)
        last = {}
        for f in real:
            key = (severity(f["Severity"]), f["Type"])
            last[key] = max(last.get(key, ""), f.get("UpdatedAt", ""))
        order = {"High": 0, "Medium": 1, "Low": 2}
        for (sev, kind), n in sorted(by.items(), key=lambda x: (order[x[0][0]], -x[1])):
            lines.append(f"| {sev} | {kind} | {n} | {last[(sev, kind)][:10]} |")
        lines.append("")
    # the cost estimate: what the usage so far would have cost without the trial, by feature
    usage = gd.get_usage_statistics(DetectorId=det, UsageStatisticType="SUM_BY_FEATURE",
                                    UsageCriteria={"Features": ["CLOUD_TRAIL", "DNS_LOGS", "FLOW_LOGS",
                                                                "S3_DATA_EVENTS", "LAMBDA_NETWORK_LOGS"]})
    rows = usage.get("UsageStatistics", {}).get("SumByFeature", [])
    total = sum(float(r["Total"]["Amount"]) for r in rows)
    lines += ["**Estimated cost so far this period** (what GuardDuty would charge; free during the trial):", ""]
    lines += [f"- {r['Feature']}: {float(r['Total']['Amount']):.2f} {r['Total']['Unit']}" for r in rows]
    lines += [f"- **Total: {total:.2f} USD**", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    import boto3
    session = boto3.Session(profile_name=AWS.get("profile") or None, region_name=AWS["region"])
    text = report(session.client("guardduty"), samples="--samples" in sys.argv)
    print(text)
    if "--save" in sys.argv:
        OUT.parent.mkdir(exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"\nSaved to {OUT.relative_to(Path(__file__).resolve().parent)}")
