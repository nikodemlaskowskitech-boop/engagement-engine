"""
Is anyone looking at the demo site? (18 Nov 2026.) Counts CloudFront's visit logs (see
site_logs.py): visitors, page views, searches and countries per day, the pages opened and
where people came from. Counts only: the logs hold no IP address and no cookie.

    python visitors.py            the last [aws] site_visit_days days (all the logs kept)

Left out: bots and link previews (a user agent with a [visits] bots word), errors, and every
visit through the xxxx.cloudfront.net address, which is how Nikodem opens the site; only
visits to [aws] site_domain count. A "visitor" is one browser in one country on one day: a
rough count, as nothing identifies a person.

Sign in first: aws login --profile cairnpath.
"""

import gzip
import json
import sys
from collections import Counter
from urllib.parse import urlparse

from settings import SETTINGS

AWS, VISITS = SETTINGS["aws"], SETTINGS["visits"]
STACK = "CairnpathData"


def parse(blob):
    """The records in one log file: JSON lines (or one JSON list), gzipped or not."""
    if blob[:2] == b"\x1f\x8b":
        blob = gzip.decompress(blob)
    text = blob.decode("utf-8").strip()
    if not text:
        return []
    if text.startswith("["):
        return json.loads(text)
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def val(r, key):
    v = r.get(key)
    return None if v in (None, "", "-") else str(v)


def summarize(records, domain, bots):
    """Counts per day and overall. Pure: the tests feed it made-up records."""
    bots = [b.lower() for b in bots]
    days, pages, sources, countries = {}, Counter(), Counter(), Counter()
    owner = robots = 0
    for r in records:
        stem, status = val(r, "cs-uri-stem") or "/", int(val(r, "sc-status") or 0)
        page = stem == "/" or stem.endswith(".html")
        search = stem == "/search"
        if not (page or search) or not 200 <= status < 400:
            continue                                   # fonts, icons, errors: not a visit
        if domain and (val(r, "x-host-header") or "").lower() != domain.lower():
            owner += page
            continue                                   # the cloudfront.net address: Nikodem
        agent = val(r, "cs(User-Agent)") or ""
        if not agent or any(b in agent.lower() for b in bots):
            robots += page
            continue
        day, country = val(r, "date") or "?", val(r, "c-country") or "??"
        d = days.setdefault(day, {"views": 0, "searches": 0, "visitors": set(), "countries": Counter()})
        if search:
            d["searches"] += 1
            continue
        d["views"] += 1
        if (day, country, agent) not in d["visitors"]:
            d["countries"][country] += 1
            countries[country] += 1
        d["visitors"].add((day, country, agent))
        pages[stem.rsplit("/", 1)[-1] or "index.html"] += 1
        ref = urlparse(val(r, "cs(Referer)") or "").netloc.lower().removeprefix("www.")
        if not domain or ref != domain.lower():                       # moving between our own pages isn't a source
            sources[ref or "(direct or hidden)"] += 1
    return {"days": {k: {**v, "visitors": len(v["visitors"])} for k, v in sorted(days.items())},
            "pages": pages, "sources": sources, "countries": countries, "owner": owner, "bots": robots}


def report(s, keep_days):
    lines = [f"Demo site visits, the last {keep_days} days (counts only: no IP address or cookie is logged)"]
    if not s["days"]:
        lines.append("  No visits yet (besides yours and bots).")
    else:
        lines.append(f"  {'date':<11} {'visitors':>8} {'page views':>11} {'searches':>9}  countries")
        for day, d in s["days"].items():
            lines.append(f"  {day:<11} {d['visitors']:>8} {d['views']:>11} {d['searches']:>9}  "
                         + ", ".join(f"{c} {n}" for c, n in d["countries"].most_common()))
        tv = sum(d["visitors"] for d in s["days"].values())
        lines.append(f"  Total: about {tv} visitor{'s' if tv != 1 else ''}, "
                     f"{sum(d['views'] for d in s['days'].values())} page views, "
                     f"{sum(d['searches'] for d in s['days'].values())} searches")
        lines.append("  Countries: " + ", ".join(f"{c} {n}" for c, n in s["countries"].most_common()))
        lines.append("  Pages: " + ", ".join(f"{p} {n}" for p, n in s["pages"].most_common(8)))
        lines.append("  Came from: " + ", ".join(f"{h} {n}" for h, n in s["sources"].most_common(6)))
    lines.append(f"  Left out: {s['owner']} page views through the cloudfront.net address (you), "
                 f"{s['bots']} by bots and link previews")
    lines.append("  A visitor = one browser in one country on one day: a rough count.")
    return "\n".join(lines)


def read_all(s3, bucket):
    records = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            records += parse(s3.get_object(Bucket=bucket, Key=obj["Key"])["Body"].read())
    return records


def main():
    import boto3
    session = boto3.Session(profile_name=AWS.get("profile") or None, region_name=AWS["region"])
    outs = session.client("cloudformation").describe_stacks(StackName=STACK)["Stacks"][0].get("Outputs", [])
    bucket = next((o["OutputValue"] for o in outs if o["OutputKey"] == "VisitLogsBucket"), None)
    if not bucket:
        print("No VisitLogsBucket in the stack's outputs: cdk deploy with [aws] site_visits = true, "
              "then python site_logs.py.")
        return 1
    records = read_all(session.client("s3"), bucket)
    if not records:
        print("No visit logs yet. After python site_logs.py the first ones arrive within a few hours.")
        return 0
    print(report(summarize(records, AWS["site_domain"], VISITS["bots"]), AWS["site_visit_days"]))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
