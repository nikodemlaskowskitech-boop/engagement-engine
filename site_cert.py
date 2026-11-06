"""
The certificate for the public site's own address ([aws] site_domain), and the
two DNS records to add at the registrar (Cloudflare). Read-only except for one
thing: it asks AWS Certificate Manager for the certificate if there isn't one.

    python site_cert.py cairnpath.example.com          ask (once), print the DNS records and the status
    python site_cert.py cairnpath.example.com --wait   ...then wait until the certificate is issued
    (without a name: [aws] site_domain)

Why a script and not CDK: CloudFront only takes certificates from us-east-1,
and a CDK stack there needs its own bootstrap (a bucket, roles) in a region our
organization keeps closed. Asking ACM directly needs only ACM in us-east-1.
Why by hand at Cloudflare: the DNS lives there, outside AWS (option B, 6 Nov
2026). Both records are "DNS only" (grey cloud), never proxied: CloudFront is
already the CDN and holds the certificate.

Sign in first: aws login --profile cairnpath.
"""

import sys
import time

from settings import SETTINGS

AWS = SETTINGS["aws"]
STACK = "CairnpathData"
CERT_REGION = "us-east-1"          # AWS's rule for CloudFront certificates, not a choice
WAIT_SECONDS, POLL_SECONDS = 1800, 20


def find_or_request(acm, domain):
    """The certificate for exactly this domain (newest usable one), else a new DNS-validated one."""
    found = []
    for page in acm.get_paginator("list_certificates").paginate(
            CertificateStatuses=["ISSUED", "PENDING_VALIDATION"]):
        found += [c for c in page["CertificateSummaryList"] if c["DomainName"] == domain]
    if found:
        issued = [c for c in found if c.get("Status") == "ISSUED"]
        return (issued or found)[0]["CertificateArn"], False
    arn = acm.request_certificate(DomainName=domain, ValidationMethod="DNS",
                                  IdempotencyToken="cairnpathsite",
                                  Tags=[{"Key": "project", "Value": "cairnpath"}])["CertificateArn"]
    return arn, True


def describe(acm, arn, sleep=time.sleep):
    """(status, the validation record) once ACM has made the record (a few seconds after a request)."""
    for _ in range(10):
        cert = acm.describe_certificate(CertificateArn=arn)["Certificate"]
        options = cert.get("DomainValidationOptions", [])
        record = options[0].get("ResourceRecord") if options else None
        if record:
            return cert["Status"], record
        sleep(3)
    return cert["Status"], None


def cloudfront_domain(cfn):
    outputs = cfn.describe_stacks(StackName=STACK)["Stacks"][0].get("Outputs", [])
    return next((o["OutputValue"] for o in outputs if o["OutputKey"] == "SiteCloudFrontDomain"), None) \
        or next((o["OutputValue"].replace("https://", "") for o in outputs if o["OutputKey"] == "SiteUrl"
                 and o["OutputValue"].endswith(".cloudfront.net")), None)


def run(acm, cfn, domain, wait=False, sleep=time.sleep, clock=time.time):
    if not domain:
        print("No [aws] site_domain in settings.toml: set it first (e.g. cairnpath.yourname.com).")
        return 1
    arn, new = find_or_request(acm, domain)
    print(f"{'Asked for' if new else 'Found'} the certificate for {domain} (us-east-1):\n  {arn}")
    status, record = describe(acm, arn, sleep=sleep)
    target = cloudfront_domain(cfn)
    print("\nAt Cloudflare (DNS > Records > Add record), both with Proxy status = DNS only (grey cloud):")
    if record:
        print(f"  1. Type {record['Type']}   Name {record['Name'].rstrip('.')}   Target {record['Value'].rstrip('.')}")
        print("     (proves to AWS that you own the domain; keep it: renewals use it too)")
    else:
        print("  1. (ACM hasn't made the validation record yet: run this again in a minute)")
    print(f"  2. Type CNAME   Name {domain}   Target {target or '<SiteCloudFrontDomain from cdk deploy>'}")
    print("     (points your address at CloudFront)")
    start = clock()
    while wait and status != "ISSUED" and clock() - start < WAIT_SECONDS:
        sleep(POLL_SECONDS)
        status, _ = describe(acm, arn, sleep=sleep)
        print(f"  ... {status}")
    print(f"\nCertificate status: {status}")
    if status == "ISSUED":
        print("\nPut these two lines in settings.toml [aws], then cdk deploy:")
        print(f'  site_domain = "{domain}"')
        print(f'  site_certificate_arn = "{arn}"')
        return 0
    if status == "PENDING_VALIDATION":
        print("Waiting for record 1 at Cloudflare (usually minutes after you add it): run again with --wait.")
        return 0 if not wait else 1
    print("Not usable: see the status above (FAILED or VALIDATION_TIMED_OUT means ask again: delete it and rerun).")
    return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    import boto3
    domain = next((a for a in sys.argv[1:] if not a.startswith("--")), AWS.get("site_domain", ""))
    session = boto3.Session(profile_name=AWS.get("profile") or None)
    sys.exit(run(session.client("acm", region_name=CERT_REGION),
                 session.client("cloudformation", region_name=AWS["region"]),
                 domain, wait="--wait" in sys.argv))
