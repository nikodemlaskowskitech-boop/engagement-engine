"""
site_cert.py test: asks ACM for a certificate once (never twice), in us-east-1's
terms, prints the two DNS records to add at Cloudflare, waits until issued and
gives the settings lines. No AWS: stand-in ACM and CloudFormation.

    python tests/site_cert_test.py

Exit 1 on any FAIL.
"""

import contextlib
import io
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import site_cert as sc    # noqa: E402

DOMAIN = "cairnpath.example.com"
ARN = "arn:aws:acm:us-east-1:809071263914:certificate/abc-123"
failed = 0


def check(ok, name, detail=""):
    global failed
    failed += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ->  {detail}" if not ok and detail else ""))


class FakeACM:
    def __init__(self, certs=None, issue_after=None):
        self.certs = dict(certs or {})        # arn -> {"DomainName", "Status"}
        self.requests, self.described = [], 0
        self.issue_after = issue_after        # describe calls until ISSUED (None = never)

    def get_paginator(self, name):
        acm = self

        class P:
            def paginate(self, CertificateStatuses):
                yield {"CertificateSummaryList": [{"CertificateArn": a, **c} for a, c in acm.certs.items()
                                                  if c["Status"] in CertificateStatuses]}
        return P()

    def request_certificate(self, **kw):
        self.requests.append(kw)
        self.certs[ARN] = {"DomainName": kw["DomainName"], "Status": "PENDING_VALIDATION"}
        return {"CertificateArn": ARN}

    def describe_certificate(self, CertificateArn):
        self.described += 1
        c = self.certs[CertificateArn]
        if self.issue_after is not None and self.described > self.issue_after:
            c["Status"] = "ISSUED"
        return {"Certificate": {"Status": c["Status"], "DomainValidationOptions": [{"ResourceRecord": {
            "Name": "_x1.cairnpath.example.com.", "Type": "CNAME", "Value": "_y2.acm-validations.aws."}}]}}


class FakeCfn:
    def describe_stacks(self, StackName):
        return {"Stacks": [{"Outputs": [{"OutputKey": "SiteUrl", "OutputValue": "https://d1.cloudfront.net"},
                                        {"OutputKey": "SiteCloudFrontDomain", "OutputValue": "d1.cloudfront.net"}]}]}


def run(acm, wait=False, domain=DOMAIN):
    out = io.StringIO()
    t = [0]
    def clock():
        t[0] += 30
        return t[0]
    with contextlib.redirect_stdout(out):
        code = sc.run(acm, FakeCfn(), domain, wait=wait, sleep=lambda s: None, clock=clock)
    return code, out.getvalue()


print("\nsite_cert.py")
check(sc.CERT_REGION == "us-east-1", "certificates are asked for in us-east-1 (CloudFront's rule)")
acm = FakeACM()
code, out = run(acm)
r = acm.requests[0] if acm.requests else {}
check(len(acm.requests) == 1 and r.get("DomainName") == DOMAIN and r.get("ValidationMethod") == "DNS",
      "no certificate yet: asks for one, DNS-validated, for exactly the domain", acm.requests)
check("_x1.cairnpath.example.com" in out and "_y2.acm-validations.aws" in out and "DNS only" in out,
      "prints the validation record for Cloudflare, without trailing dots, DNS only", out)
check(f"Name {DOMAIN}   Target d1.cloudfront.net" in out, "prints the CNAME to CloudFront's own address", out)
check(code == 0 and "PENDING_VALIDATION" in out and "site_certificate_arn" not in out,
      "pending: says to wait, gives no settings lines yet")
code, out = run(acm)
check(len(acm.requests) == 1 and "Found the certificate" in out, "run again: finds it, never asks twice", acm.requests)
acm = FakeACM(issue_after=3)
code, out = run(acm, wait=True)
check(code == 0 and f'site_certificate_arn = "{ARN}"' in out and f'site_domain = "{DOMAIN}"' in out,
      "--wait: once issued, prints the two settings lines", out[-300:])
acm = FakeACM(issue_after=None)
code, out = run(acm, wait=True)
check(code == 1 and "run again with --wait" in out, "never validated within the wait: exit 1, says what to do", out[-200:])
other = FakeACM(certs={"arn:aws:acm:us-east-1:1:certificate/other": {"DomainName": "www.example.com", "Status": "ISSUED"}})
run(other)
check(len(other.requests) == 1, "a certificate for another name isn't reused")
code, out = run(FakeACM(), domain="")
check(code == 1 and "site_domain" in out, "no domain: says to set it, asks for nothing")

print("\nFAILED" if failed else "\nOK")
sys.exit(1 if failed else 0)
