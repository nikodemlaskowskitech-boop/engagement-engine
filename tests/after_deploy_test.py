"""
after_deploy.py test (28 Nov 2026): it starts the engine the one way ({"trigger": "deploy"},
never briefs), waits for that run's run.json (not an older one), reports the result, time and
memory against the limits and the warnings, saves the run.json, and exits 1 on a failed or
unfinished run. Stand-in CloudFormation, Lambda and S3 (no AWS).
    python tests/after_deploy_test.py
"""
import io
import json
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import after_deploy as ad      # noqa: E402

failed = 0


def check(ok, name, detail=""):
    global failed
    failed += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ->  {str(detail)[:300]}" if not ok and detail else ""))


class Cfn:
    def describe_stacks(self, StackName):
        return {"Stacks": [{"Outputs": [{"OutputKey": "EngineFunction", "OutputValue": "Engine-123"}]}]}


class Lam:
    def __init__(self):
        self.calls = []

    def invoke(self, **kw):
        self.calls.append(kw)


class S3:
    def __init__(self, runs):
        self.runs = list(runs)           # what run.json says at each poll

    def get_object(self, **kw):
        body = json.dumps(self.runs.pop(0) if len(self.runs) > 1 else self.runs[0]).encode()
        return {"Body": io.BytesIO(body)}


def go(runs, clock_step=20):
    t = [0]
    def clock():
        t[0] += clock_step
        return t[0]
    lam, out, save = Lam(), io.StringIO(), Path(tempfile.mkdtemp()) / "run.json"
    with redirect_stdout(out):
        code = ad.after_deploy(Cfn(), lam, S3(runs), sleep=lambda s: None, clock=clock, save=save)
    return code, out.getvalue(), lam.calls, save


old = {"triggered_by": "schedule", "started_at": "2026-01-01T00:00:00Z", "status": "FAILED"}
good = {"triggered_by": "deploy", "started_at": "2999-01-01T00:00:00Z", "status": "OK", "seconds": 412.3,
        "step_seconds": {"history.py": 150.0, "flags.py": 12.0, "export_views.py": 20.0},
        "step_peak_mb": {"flags.py": 410, "history.py": 620}, "budgets": {"memory_mb": 2048, "seconds": 900, "warn_at": 0.7},
        "warnings": []}

print("\nafter_deploy.py")
code, out, calls, save = go([old, old, good])
check(len(calls) == 1 and calls[0]["FunctionName"] == "Engine-123" and calls[0]["InvocationType"] == "Event"
      and json.loads(calls[0]["Payload"]) == {"trigger": "deploy"}, "starts the engine once, {\"trigger\": \"deploy\"}, never briefs", calls)
check(code == 0 and "Engine run OK in 412.3 s of 900" in out, "waits past the older run for its own, then reports it", out)
check("peak memory: 620 MB (history.py) of 2,048" in out and "slowest steps: history.py 150 s" in out,
      "the peak memory and the slowest steps, against the limits", out)
check(save.exists() and json.loads(save.read_text())["triggered_by"] == "deploy", "saves the run.json for Claude to read")
warned = {**good, "warnings": ["memory: flags.py peaked at 1,492 MB of 2,048 (72%): over 70% of the function's memory"]}
code, out, _, _ = go([warned])
check(code == 0 and "WARNING: memory: flags.py peaked at 1,492 MB" in out, "a budget warning is printed (the run still worked)", out)
code, out, _, _ = go([{**good, "status": "FAILED", "failed_step": "flags.py"}])
check(code == 1 and "failed step: flags.py" in out, "a failed run: exit 1, naming the step", out)
code, out, _, _ = go([old], clock_step=400)
check(code == 1 and "No finished run" in out, "no run of its own in time: exit 1, not a false OK", out)

ps = (Path(__file__).resolve().parents[1] / "deploy.ps1").read_text(encoding="utf-8")
order = [ps.find(s) for s in ("git pull", "tests\\infra_test.py", "cdk deploy", "security_check.py", "after_deploy.py")]
check(all(i >= 0 for i in order) and order == sorted(order), "deploy.ps1: pull, infra test, deploy, security check, a run, in that order")
check(ps.count("$LASTEXITCODE -ne 0") >= 4 and "nothing deployed" in ps, "...and stops at the first failure (an infra FAIL deploys nothing)")

print(f"\n{'All passed' if not failed else f'{failed} failed'}")
sys.exit(1 if failed else 0)
