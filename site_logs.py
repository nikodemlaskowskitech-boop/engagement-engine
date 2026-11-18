"""
Turns on the demo site's visit logs (18 Nov 2026): CloudFront writes one line per request to
the VisitLogs bucket, with only the fields in settings.toml [visits] (no IP address, no
cookie, no search question). visitors.py counts them. Run once after the cdk deploy that made
the bucket; running it again only brings the field list up to date.

    python site_logs.py          turn on (or update the fields)
    python site_logs.py --off    turn off (the logs already written expire on their own)

Why a script and not CDK: AWS takes CloudFront's log delivery only in us-east-1, and a CDK
stack there needs its own bootstrap in a region our organization keeps closed (the same
reason as site_cert.py). If the organization's rules refuse the call, this says so.

Sign in first: aws login --profile cairnpath.
"""

import sys

from settings import SETTINGS

AWS, VISITS = SETTINGS["aws"], SETTINGS["visits"]
STACK = "CairnpathData"
LOG_REGION = "us-east-1"            # AWS's rule for CloudFront's log delivery, not a choice
NEVER = {"c-ip", "x-forwarded-for", "cs(Cookie)", "cs-uri-query", "c-port"}   # would identify someone


def outputs(cfn):
    rows = cfn.describe_stacks(StackName=STACK)["Stacks"][0].get("Outputs", [])
    return {o["OutputKey"]: o["OutputValue"] for o in rows}


def fields():
    """The fields from [visits], refused if any could identify a visitor."""
    chosen = list(VISITS["fields"])
    bad = sorted(set(chosen) & NEVER)
    if bad:
        raise SystemExit(f"[visits] fields may not include {', '.join(bad)}: they identify a visitor")
    return chosen


def blocked(err):
    """A refusal from the organization's rules (an SCP), said plainly."""
    msg = str(err)
    if "service control policy" in msg or "explicit deny" in msg:
        return ("Refused by the organization's rules (an SCP) in us-east-1. CloudFront's log delivery "
                "must be set up there: allow logs:*Delivery* in us-east-1 for the admin role, then run "
                "this again.")
    return None


def turn_on(logs, out, account):
    dist, bucket = out.get("SiteDistributionId"), out.get("VisitLogsBucket")
    if not dist or not bucket:
        print("No SiteDistributionId or VisitLogsBucket in the stack's outputs: run cdk deploy first "
              "([aws] site and site_visits on).")
        return 1
    dest = logs.put_delivery_destination(
        name=VISITS["destination_name"], outputFormat="json",
        deliveryDestinationConfiguration={"destinationResourceArn": f"arn:aws:s3:::{bucket}"},
        tags={"project": "cairnpath"})["deliveryDestination"]["arn"]
    logs.put_delivery_source(
        name=VISITS["source_name"], logType="ACCESS_LOGS",
        resourceArn=f"arn:aws:cloudfront::{account}:distribution/{dist}", tags={"project": "cairnpath"})
    chosen = fields()
    mine = [d for d in logs.describe_deliveries().get("deliveries", [])
            if d.get("deliverySourceName") == VISITS["source_name"]]
    if mine:
        logs.update_delivery_configuration(id=mine[0]["id"], recordFields=chosen)
        print(f"Visit logs already on; fields brought up to date ({len(chosen)}).")
    else:
        logs.create_delivery(deliverySourceName=VISITS["source_name"], deliveryDestinationArn=dest,
                             recordFields=chosen, tags={"project": "cairnpath"})
        print("Visit logs on.")
    print(f"  CloudFront distribution {dist} -> bucket {bucket}")
    print(f"  fields: {', '.join(chosen)} (no IP address, no cookie, no search question)")
    print(f"  kept {AWS['site_visit_days']} days. The first logs arrive within a few hours: then python visitors.py")
    return 0


def turn_off(logs):
    for d in logs.describe_deliveries().get("deliveries", []):
        if d.get("deliverySourceName") == VISITS["source_name"]:
            logs.delete_delivery(id=d["id"])
    for call, name in ((logs.delete_delivery_source, VISITS["source_name"]),
                       (logs.delete_delivery_destination, VISITS["destination_name"])):
        try:
            call(name=name)
        except Exception as err:                       # already gone is fine
            if "ResourceNotFound" not in type(err).__name__ + str(err):
                raise
    print("Visit logs off. The files already written expire on their own.")
    return 0


def run(logs, cfn, account, off=False):
    try:
        return turn_off(logs) if off else turn_on(logs, outputs(cfn), account)
    except Exception as err:
        why = blocked(err)
        if why:
            print(why)
            return 2
        raise


def main(argv):
    import boto3
    session = boto3.Session(profile_name=AWS.get("profile") or None)
    return run(session.client("logs", region_name=LOG_REGION),
               session.client("cloudformation", region_name=AWS["region"]),
               str(AWS["account_id"]), off="--off" in argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
