"""
visitors.py and site_logs.py test (18 Nov 2026): the visit count leaves out bots, errors,
assets and the owner's address; nothing that identifies a visitor is ever logged; the
logging is turned on once, updated after, and an organization's refusal is said plainly.
No AWS: made-up log records and stand-in clients.

    python tests/visitors_test.py

Exit 1 on any FAIL.
"""

import contextlib
import gzip
import io
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import site_logs as sl    # noqa: E402
import visitors as v      # noqa: E402
from settings import SETTINGS    # noqa: E402

DOMAIN = "cairnpath.example.com"
CHROME = "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/141.0 Safari/537.36"
IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 19_0) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1"
failed = 0


def check(ok, name, detail=""):
    global failed
    failed += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ->  {detail}" if not ok and detail else ""))


def rec(stem, day="2026-11-19", host=DOMAIN, status=200, agent=CHROME, country="FR", ref="-"):
    return {"date": day, "time": "10:00:00", "x-host-header": host, "cs-uri-stem": stem,
            "sc-status": status, "cs(Referer)": ref, "cs(User-Agent)": agent, "c-country": country}


print("What [visits] logs")
f = SETTINGS["visits"]["fields"]
check(not set(f) & sl.NEVER, "no IP, cookie, search question or port among the logged fields", f)
check({"c-country", "x-host-header", "cs(User-Agent)", "cs-uri-stem", "date"} <= set(f),
      "country, address used, user agent, page and day are logged (what the count needs)", f)
saved = SETTINGS["visits"]["fields"]
SETTINGS["visits"]["fields"] = saved + ["c-ip"]
try:
    sl.fields()
    check(False, "a field list with c-ip is refused")
except SystemExit as e:
    check("identify" in str(e), "a field list with c-ip is refused, saying why", e)
finally:
    SETTINGS["visits"]["fields"] = saved

print("\nCounting")
records = [
    rec("/index.html", ref="https://www.linkedin.com/"),              # a person from LinkedIn
    rec("/leader.html", ref=f"https://{DOMAIN}/index.html"),          # ...moving on: not a new source
    rec("/search"),                                                   # ...and a search
    rec("/index.html", agent=IPHONE, country="PL"),                   # another person, Poland
    rec("/", agent=IPHONE, country="PL", day="2026-11-20"),           # the same phone the next day
    rec("/index.html", agent="LinkedInBot/1.0 (+http://www.linkedin.com)"),   # a link preview
    rec("/index.html", agent="python-requests/2.32"),                 # a script
    rec("/index.html", host="d111.cloudfront.net"),                   # Nikodem, by his address
    rec("/index.html", status=404),                                   # an error
    rec("/fonts/inter.woff2"),                                        # an asset, not a page
    rec("/index.html", agent="-"),                                    # no user agent: not counted
]
s = v.summarize(records, DOMAIN, SETTINGS["visits"]["bots"])
d19, d20 = s["days"].get("2026-11-19", {}), s["days"].get("2026-11-20", {})
check(d19.get("views") == 3 and d19.get("visitors") == 2 and d19.get("searches") == 1,
      "19 Nov: 3 page views by 2 visitors, 1 search", d19)
check(d20.get("visitors") == 1 and d20.get("views") == 1, "20 Nov: the phone again counts as a visitor that day", d20)
check(dict(s["countries"]) == {"FR": 1, "PL": 2}, "countries: one per visitor and day", dict(s["countries"]))
check(s["owner"] == 1 and s["bots"] == 3, "left out: 1 view by the owner's address, 3 by bots, scripts or no agent",
      (s["owner"], s["bots"]))
check(s["sources"].get("linkedin.com") == 1 and DOMAIN not in s["sources"],
      "came from: linkedin.com, and moving between our own pages isn't a source", dict(s["sources"]))
check(s["pages"]["index.html"] == 3 and s["pages"]["leader.html"] == 1, "pages: / counts as index.html", dict(s["pages"]))
text = v.report(s, 30)
check("about 3 visitors" in text and "FR 1" in text and "(you)" in text and "rough count" in text,
      "the report: totals, countries, what was left out, and that it's rough", text)
check("No visits yet" in v.report(v.summarize([records[7]], DOMAIN, []), 30),
      "only the owner's own visits: 'No visits yet'")
check(v.summarize([rec("/index.html", host="d111.cloudfront.net")], "", [])["days"],
      "no site_domain: every address counts (nothing to tell the owner by)")

print("\nReading the files")
lines = "\n".join(json.dumps(r) for r in records[:3]).encode()
check(len(v.parse(gzip.compress(lines))) == 3 and len(v.parse(lines)) == 3, "JSON lines, gzipped or not")
check(len(v.parse(json.dumps(records[:2]).encode())) == 2 and v.parse(b"") == [], "a JSON list too; an empty file is nothing")


class Logs:
    """Stand-in CloudWatch Logs in us-east-1."""
    def __init__(self, deliveries=(), deny=False):
        self.deliveries, self.calls, self.deny = list(deliveries), [], deny

    def _call(self, call, **kw):
        if self.deny:
            raise Exception("AccessDeniedException: ... with an explicit deny in a service control policy")
        self.calls.append((call, kw))
        return kw

    def put_delivery_destination(self, **kw):
        self._call("put_delivery_destination", **kw)
        return {"deliveryDestination": {"arn": "arn:aws:logs:us-east-1:1:delivery-destination:x"}}

    def put_delivery_source(self, **kw): return self._call("put_delivery_source", **kw)
    def describe_deliveries(self): return {"deliveries": self.deliveries}
    def create_delivery(self, **kw): return self._call("create_delivery", **kw)
    def update_delivery_configuration(self, **kw): return self._call("update_delivery_configuration", **kw)
    def delete_delivery(self, **kw): return self._call("delete_delivery", **kw)
    def delete_delivery_source(self, **kw): return self._call("delete_delivery_source", **kw)
    def delete_delivery_destination(self, **kw): return self._call("delete_delivery_destination", **kw)


class Cfn:
    def __init__(self, outs): self.outs = outs
    def describe_stacks(self, StackName):
        return {"Stacks": [{"Outputs": [{"OutputKey": k, "OutputValue": val} for k, val in self.outs.items()]}]}


OUTS = {"SiteDistributionId": "E2ABC", "VisitLogsBucket": "cairnpathdata-visitlogs-x"}


def run(logs, outs=OUTS, off=False):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = sl.run(logs, Cfn(outs), "809071263914", off=off)
    return code, out.getvalue()


print("\nTurning the logs on (site_logs.py)")
logs = Logs()
code, out = run(logs)
names = [c[0] for c in logs.calls]
src = next(kw for n, kw in logs.calls if n == "put_delivery_source")
dst = next(kw for n, kw in logs.calls if n == "put_delivery_destination")
made = next((kw for n, kw in logs.calls if n == "create_delivery"), {})
check(code == 0 and names == ["put_delivery_destination", "put_delivery_source", "create_delivery"],
      "first run: destination, source, then one delivery", names)
check(src["resourceArn"] == "arn:aws:cloudfront::809071263914:distribution/E2ABC" and src["logType"] == "ACCESS_LOGS",
      "the source is this distribution's access logs", src)
check(dst["deliveryDestinationConfiguration"]["destinationResourceArn"] == "arn:aws:s3:::cairnpathdata-visitlogs-x"
      and dst["outputFormat"] == "json", "into the VisitLogs bucket, as JSON", dst)
check(made.get("recordFields") == SETTINGS["visits"]["fields"], "with exactly the [visits] fields", made)
check("no IP address" in out and "visitors.py" in out, "says what it logs and what to run next", out)
logs = Logs(deliveries=[{"id": "d-1", "deliverySourceName": SETTINGS["visits"]["source_name"]}])
code, out = run(logs)
names = [c[0] for c in logs.calls]
check(code == 0 and "create_delivery" not in names and "update_delivery_configuration" in names,
      "second run: the delivery is updated, never doubled", names)
code, out = run(Logs(), outs={})
check(code == 1 and "cdk deploy" in out, "no outputs yet: says to deploy first", out)
code, out = run(Logs(deny=True))
check(code == 2 and "SCP" in out and "us-east-1" in out, "refused by the organization's rules: says so plainly", out)
logs = Logs(deliveries=[{"id": "d-1", "deliverySourceName": SETTINGS["visits"]["source_name"]}])
code, out = run(logs, off=True)
check(code == 0 and [c[0] for c in logs.calls] == ["delete_delivery", "delete_delivery_source", "delete_delivery_destination"],
      "--off: delivery, source and destination removed", logs.calls)

print("\nFAILED" if failed else "\nAll passed")
sys.exit(1 if failed else 0)
