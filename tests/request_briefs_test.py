"""
request_briefs.py test: does it ask the engine for briefs the one allowed way,
and report the run honestly? Stand-in CloudFormation, Lambda and S3 (no AWS).

    python tests/request_briefs_test.py

Exit 1 on any FAIL.
"""

import io
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import request_briefs as rb   # noqa: E402

failed = 0


def check(ok, name, detail=""):
    global failed
    failed += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ->  {detail}" if not ok and detail else ""))


class Cfn:
    def describe_stacks(self, StackName):
        return {"Stacks": [{"Outputs": [{"OutputKey": "EngineFunction", "OutputValue": "CairnpathData-RunEngine-X"}]}]}


class Lam:
    def __init__(self):
        self.calls = []

    def invoke(self, **kw):
        self.calls.append(kw)
        return {"StatusCode": 202}


class S3:
    """run.json changes after a few polls, like the real run finishing."""
    def __init__(self, runs):
        self.runs, self.reads = runs, 0

    def get_object(self, Bucket, Key, ExpectedBucketOwner=None):
        assert Key.endswith("run.json") and ExpectedBucketOwner == str(rb.AWS["account_id"])
        run = self.runs[min(self.reads, len(self.runs) - 1)]
        self.reads += 1
        if run is None:
            raise KeyError("NoSuchKey")
        return {"Body": io.BytesIO(json.dumps(run).encode())}


clock = [0.0]
fake_sleep = lambda s: clock.__setitem__(0, clock[0] + s)
fake_clock = lambda: clock[0]

print("\nrequest_briefs.py")
lam = Lam()
old = {"briefs": False, "started_at": "2000-01-01T00:00:00Z", "status": "OK"}
done = {"briefs": True, "started_at": "2999-01-01T00:00:00Z", "status": "OK", "seconds": 88, "warnings": [],
        "outputs": ["handover/acc_012.md", "handover/acc_012_meta.json", "ai_spend.csv"]}
code = rb.request(Cfn(), lam, S3([old, old, done]), sleep=fake_sleep, clock=fake_clock)
call = lam.calls[0]
check(len(lam.calls) == 1 and call["FunctionName"] == "CairnpathData-RunEngine-X",
      "starts the engine function the stack names, once")
check(call["InvocationType"] == "Event", "asynchronously: it starts the run and doesn't hold a connection open")
check(json.loads(call["Payload"]) == {"trigger": "briefs", "briefs": True}, "with exactly {\"briefs\": true}")
check(code == 0, "waits for that run (not an older one) and reports OK")
lam2 = Lam()
check(rb.request(Cfn(), lam2, S3([None]), wait=False) == 0 and len(lam2.calls) == 1, "--no-wait: asks and returns")
clock[0] = 0.0
check(rb.request(Cfn(), Lam(), S3([old]), sleep=fake_sleep, clock=fake_clock) == 1,
      "no briefs run shows up in time: says so (exit 1), doesn't pretend")
failed_run = {**done, "status": "FAILED", "failed_step": "flags.py"}
clock[0] = 0.0
check(rb.request(Cfn(), Lam(), S3([failed_run]), sleep=fake_sleep, clock=fake_clock) == 1, "a failed run: exit 1")

try:
    import botocore.session
    from botocore.validate import validate_parameters
except ImportError:
    print("  SKIP  request vs AWS's API model (botocore not installed here)")
else:
    model = botocore.session.get_session().get_service_model("lambda")
    try:
        validate_parameters(call, model.operation_model("Invoke").input_shape)
        check(True, "the invoke request matches AWS's own API model")
    except Exception as e:
        check(False, "the invoke request matches AWS's own API model", str(e))

print("\nFAILED" if failed else "\nOK")
sys.exit(1 if failed else 0)
