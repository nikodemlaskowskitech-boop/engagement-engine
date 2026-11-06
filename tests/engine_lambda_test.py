"""
Engine Lambda test: does the handler that runs the engine in AWS gate, run and
upload correctly, without AWS?

    python tests/engine_lambda_test.py

Builds the Lambda's folder as the deploy does (infra/bundle.py, without
DuckDB), then runs its handler against a stand-in S3 holding the demo inputs.
  - every Python file the steps import is in the bundle (nothing missing in AWS)
  - the gate: an ERROR from the input check means SKIPPED, nothing computed
  - a run: inputs and the action log in, only new files out, all under outputs/,
    run.json written last; a failing step gives FAILED and raises
If DuckDB is installed here, the real engine steps run and their results must
match the laptop's own data/ files; otherwise stand-in steps check the plumbing.
Needs data/ (run .\\run_all.ps1 first). Exit 1 on any FAIL.
"""

import ast
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "infra")]

from bundle import ENGINE_FILES, NEVER_BUNDLE, bundle_engine     # noqa: E402
from settings import SETTINGS                      # noqa: E402

AWS, D = SETTINGS["aws"], SETTINGS["data"]
OWNER = str(AWS["account_id"])
INPUTS, STATE, OUTPUTS = AWS["inputs_prefix"], AWS["state_prefix"], AWS["outputs_prefix"]
CHECK = AWS["reports_prefix"] + "check_inputs/latest.json"
MARKER = AWS["upload_marker"]
REAL = importlib.util.find_spec("duckdb") is not None
CLICKS = AWS["slack_clicks_prefix"]
LONG_AGO = datetime(2026, 1, 1, tzinfo=timezone.utc)

failed = 0


def check(ok, name, detail=""):
    global failed
    failed += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ->  {detail}" if not ok and detail else ""))


SITE_NAME = "cairnpath-site-test"           # the public site's bucket, as the engine's settings name it


class FakeS3:
    """A bucket in memory: {key: bytes}. Records the order of writes. Writes to the site
    bucket go to .site instead ({key: (bytes, content type, cache control)})."""

    def __init__(self, objects):
        self.objects, self.writes = dict(objects), []
        self.modified = {}                      # key -> when written (default: long ago)
        self.site, self.site_writes = {}, []

    def _owner(self, owner):
        if owner != OWNER:
            raise PermissionError("wrong or missing ExpectedBucketOwner")

    def get_object(self, Bucket, Key, ExpectedBucketOwner=None):
        self._owner(ExpectedBucketOwner)
        import io
        return {"Body": io.BytesIO(self.objects[Key])}

    def get_paginator(self, name):
        s3 = self

        class Pages:
            def paginate(self, Bucket, Prefix="", ExpectedBucketOwner=None):
                s3._owner(ExpectedBucketOwner)
                if Bucket == SITE_NAME:
                    import hashlib
                    yield {"Contents": [{"Key": k, "ETag": '"%s"' % hashlib.md5(v[0]).hexdigest()}
                                        for k, v in sorted(s3.site.items())]}
                    return
                keys = sorted(k for k in s3.objects if k.startswith(Prefix))
                for i in range(0, max(len(keys), 1), 100):
                    yield {"Contents": [{"Key": k, "LastModified": s3.modified.get(k, LONG_AGO)}
                                        for k in keys[i:i + 100]]}
        return Pages()

    def download_file(self, Bucket, Key, Filename, ExtraArgs=None):
        self._owner((ExtraArgs or {}).get("ExpectedBucketOwner"))
        Path(Filename).write_bytes(self.objects[Key])

    def put_object(self, Bucket, Key, Body, ContentType=None, ExpectedBucketOwner=None, CacheControl=None):
        self._owner(ExpectedBucketOwner)
        if Bucket == SITE_NAME:
            self.site[Key] = (Body, ContentType, CacheControl)
            self.site_writes.append(Key)
            return
        self.objects[Key] = Body
        self.writes.append(Key)
        self.modified[Key] = datetime.now(timezone.utc)


def bucket_with_demo(status):
    """The demo inputs as upload.py lays them out, the action log, and the check's verdict."""
    objs, data = {}, REPO / "data"
    for key in ("crm_file", "team_file", "holdout_file", "outcomes_file"):
        if (data / D[key]).exists():
            objs[INPUTS + D[key]] = (data / D[key]).read_bytes()
    for g in (D["events_glob"], D["engagement_glob"]):
        for p in data.glob(g):
            objs[INPUTS + p.relative_to(data).as_posix()] = p.read_bytes()
    objs[INPUTS + MARKER] = b"{}"
    if (data / "action_log.csv").exists():
        objs[STATE + "action_log.csv"] = (data / "action_log.csv").read_bytes()
    objs[CHECK] = json.dumps({"status": status, "errors": 1 if status == "ERROR" else 0,
                              "warnings": 2, "checked_at": "2026-11-03T00:00:00Z"}).encode()
    return objs


tmp = Path(tempfile.mkdtemp())

print("\nEngine Lambda: the bundle")
bundle = bundle_engine(tmp / "bundle", deps=False)
check(sorted(p.name for p in bundle.iterdir()) == sorted(ENGINE_FILES + ["handler.py"]),
      "holds the handler and the engine's files, nothing else (never settings.local.toml)")
STEP_FILES = {st.split()[0] for st in AWS["engine_steps"] + [AWS["briefs_step"]]}
check(STEP_FILES <= set(ENGINE_FILES), "every step in [aws] engine_steps (and the briefs step) is bundled")
check((bundle / "stories" / "directory.csv").exists()
      and not any(any((bundle / "stories").rglob(n)) for n in NEVER_BUNDLE),
      "the stories go with it, but never their answer key (testing only, never near the model)")


def local_imports(name, seen):
    """Every repo module a script imports, followed recursively."""
    tree = ast.parse((REPO / name).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        mods = ([a.name for a in node.names] if isinstance(node, ast.Import) else
                [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
        for m in mods:
            f = m.split(".")[0] + ".py"
            if (REPO / f).exists() and f not in seen:
                seen.add(f)
                local_imports(f, seen)
    return seen


needed = set()
for step in STEP_FILES:
    local_imports(step, needed)
check(needed <= set(ENGINE_FILES), "every repo module the steps import is bundled", sorted(needed - set(ENGINE_FILES)))

spec = importlib.util.spec_from_file_location("engine_handler", bundle / "handler.py")
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)
h.BUCKET, h.OWNER, h.CODE = AWS["bucket"], OWNER, ENGINE_FILES
event = {"Records": [{"s3": {"object": {"key": CHECK}}}]}
work = tmp / "work"

print("\nEngine Lambda: the gate")
s3 = FakeS3(bucket_with_demo("ERROR"))
summary = h.handler(event, None, s3=s3, work=work)
check(summary["status"] == "SKIPPED", "the check said ERROR: the engine does not run (SKIPPED)", summary)
check(s3.writes == [OUTPUTS + "run.json"], "...and writes only outputs/run.json, saying why", s3.writes)
check(not work.exists(), "...without downloading anything")

print(f"\nEngine Lambda: a run ({'the real engine, DuckDB found' if REAL else 'stand-in steps: no DuckDB here'})")
if REAL:
    h.STEPS = AWS["engine_steps"]
else:
    # stand-ins that touch the same places: read an input and the action log, write outputs
    stand_in = ("from pathlib import Path\n"
                "assert Path('data/crm_accounts.csv').exists() and Path('data/action_log.csv').exists()\n"
                "assert Path('data/events').is_dir() and not Path('data/" + MARKER + "').exists()\n"
                "Path('data/flags_churn.csv').write_text('account_id,status\\n', encoding='utf-8')\n"
                "Path('data/views_data.json').write_text('{}', encoding='utf-8')\n"
                "Path('data/tables/flags_churn').mkdir(parents=True)\n"
                "Path('data/tables/flags_churn/flags_churn.parquet').write_bytes(b'PAR1\\x00\\xff')\n"
                "print('stand-in step ran')\n")
    (bundle / "stand_in.py").write_text(stand_in, encoding="utf-8")
    h.CODE, h.STEPS = ENGINE_FILES + ["stand_in.py"], ["stand_in.py"]
s3 = FakeS3(bucket_with_demo("WARN"))
summary = h.handler(event, None, s3=s3, work=work)
check(summary["status"] == "OK", "the check said WARN: the engine runs (OK)", summary.get("failed_step"))
check(all(k.startswith(OUTPUTS) for k in s3.writes), "writes only under outputs/ (never inputs/ or reports/: no loops)",
      [k for k in s3.writes if not k.startswith(OUTPUTS)])
check(s3.writes[-1] == OUTPUTS + "run.json", "run.json is written last (whoever reads it sees a finished run)")
out_names = summary["outputs"]
check("views_data.json" in out_names and "flags_churn.csv" in out_names, "uploads what the engine wrote", out_names)
if not REAL:
    check(s3.objects.get(OUTPUTS + "tables/flags_churn/flags_churn.parquet") == b"PAR1\x00\xff",
          "uploads tables in their own folders (outputs/tables/<name>/), binary files intact")
else:
    check(all(OUTPUTS + f"tables/{t}/{t}.parquet" in s3.objects for t in AWS["athena_tables"]),
          "uploads a Parquet table per [aws] athena_tables entry, each in its own folder")
check(not ({"crm_accounts.csv", "action_log.csv", "cs_team.csv"} & set(out_names)),
      "never re-uploads an input or the action log as an output")
check("DONE" in s3.objects[OUTPUTS + "last_run.txt"].decode(), "last_run.txt has every step's output, like run_all")
check(summary["triggered_by"] == CHECK and summary["check"]["status"] == "WARN", "run.json says what woke it and the check's verdict")
check(not work.exists(), "its work folder is cleaned up")
if REAL:
    # byte for byte, order included: a sort with ties can come out in a different
    # order on another machine (account_value.csv did, until account_id broke the ties)
    for name in ("flags_activation.csv", "flags_churn.csv", "flags_expansion.csv", "flags_qbr.csv",
                 "account_value.csv", "flags_journey.csv"):
        same = s3.objects[OUTPUTS + name] == (REPO / "data" / name).read_bytes()
        check(same, f"{name} matches the laptop's, byte for byte")

print("\nEngine Lambda: the daily schedule (a safety net)")
sched = {"trigger": "schedule"}
s3 = FakeS3(bucket_with_demo("WARN"))
first = h.handler(sched, None, s3=s3, work=work)
check(first["status"] == "OK" and first["triggered_by"] == "schedule", "no run yet: the schedule runs the engine", first.get("status"))
before = list(s3.writes)
again = h.handler(sched, None, s3=s3, work=work)
check(again["status"] == "SKIPPED" and "up to date" in again["reason"] and s3.writes == before,
      "next day, nothing new: SKIPPED 'up to date', writes nothing (the good run.json stays)", again)
s3.objects[CHECK] = json.dumps({"status": "WARN", "errors": 0, "warnings": 2,
                                "checked_at": "2026-11-04T00:00:00Z"}).encode()
newer = h.handler(sched, None, s3=s3, work=work)
check(newer["status"] == "OK", "a newer input check than the last run: the schedule catches up", newer.get("status"))
last = json.loads(s3.objects[OUTPUTS + "run.json"]); last["code"] = "0ld0ld0ld0ld"
s3.objects[OUTPUTS + "run.json"] = json.dumps(last).encode()
redeployed = h.handler(sched, None, s3=s3, work=work)
check(redeployed["status"] == "OK" and redeployed["code"] == h.code_version(),
      "a deploy changed the engine's code since the last run: the schedule reruns on the new code", redeployed.get("status"))
check(h.handler(sched, None, s3=s3, work=work)["status"] == "SKIPPED", "...once: then it's up to date again")
last = json.loads(s3.objects[OUTPUTS + "run.json"]); last["status"] = "FAILED"
s3.objects[OUTPUTS + "run.json"] = json.dumps(last).encode()
retry = h.handler(sched, None, s3=s3, work=work)
check(retry["status"] == "OK", "the last run FAILED: the schedule runs it again", retry.get("status"))
s3 = FakeS3(bucket_with_demo("ERROR"))
gated = h.handler(sched, None, s3=s3, work=work)
check(gated["status"] == "SKIPPED" and "errors" in gated["reason"], "the gate still applies on a schedule (check ERROR)")

print("\nEngine Lambda: the handover briefs (optional step, key from Parameter Store)")


class FakeSSM:
    def __init__(self, value):
        self.value, self.asked = value, []

    def get_parameter(self, Name, WithDecryption=False):
        self.asked.append((Name, WithDecryption))
        if self.value is None:
            raise KeyError("ParameterNotFound")
        return {"Parameter": {"Name": Name, "Value": self.value}}


(bundle / "plain.py").write_text(
    "import os\nassert 'ANTHROPIC_API_KEY' not in os.environ, 'the key leaked to a plain step'\n"
    "print('plain step ran without the key')\n", encoding="utf-8")
(bundle / "briefs.py").write_text(
    "import os, sys\nfrom pathlib import Path\n"
    "out = Path('data/handover'); out.mkdir(parents=True, exist_ok=True)\n"
    "if '--dry-run' in sys.argv:\n"
    "    assert 'ANTHROPIC_API_KEY' not in os.environ, 'the key reached the free check'\n"
    "    (out / 'acc_1_stale.json').write_text('{\"changes\": [\"new E9\"]}', encoding='utf-8')\n"
    "    print('checked, no model call'); sys.exit(0)\n"
    "if os.environ.get('BREAK_BRIEFS'): sys.exit('model API down')\n"
    "assert os.environ['ANTHROPIC_API_KEY'] == 'test-key-123' and os.environ['CAIRNPATH_LLM_PROVIDER'] == 'anthropic'\n"
    "for name, text in (('acc_1.md', 'brief'), ('acc_1_meta.json', '{}'), ('acc_1_key.json', '{\"Customer A\": \"Real Name\"}')):\n"
    "    (out / name).write_text(text, encoding='utf-8')\n"
    "(out / 'acc_1_stale.json').write_text('{\"changes\": []}', encoding='utf-8')\n"
    "print('briefs written')\n", encoding="utf-8")
h.CODE, h.STEPS = ENGINE_FILES + ["plain.py", "briefs.py"], ["plain.py", "briefs.py --dry-run"]
h.BRIEFS_STEP, h.DRY_RUN, h.OPTIONAL = "briefs.py", "briefs.py --dry-run", ["briefs.py --dry-run", "briefs.py"]
h.KEY_PARAMETER, h.LLM_PROVIDER = AWS["api_key_parameter"], AWS["briefs_llm_provider"]
ASK = {"trigger": "briefs", "briefs": True}

s3, ssm = FakeS3(bucket_with_demo("OK")), FakeSSM("test-key-123")
auto = h.handler({"trigger": "manual"}, None, s3=s3, work=work, ssm=ssm)
check(auto["status"] == "OK" and not auto["briefs"] and ssm.asked == [],
      "an ordinary run never reads the API key (no model call possible)", (auto.get("warnings"), ssm.asked))
check(OUTPUTS + "handover/acc_1_stale.json" in s3.objects and OUTPUTS + "handover/acc_1.md" not in s3.objects,
      "...it only checks the briefs for free (--dry-run) and marks them out of date")
event_ask = h.handler({"Records": [{"s3": {"object": {"key": CHECK}}}], "briefs": True}, None,
                      s3=FakeS3(bucket_with_demo("OK")), work=work, ssm=(spy := FakeSSM("test-key-123")))
check(not event_ask["briefs"] and spy.asked == [], "an S3 event can't ask for briefs, even with 'briefs' in it")

s3, ssm = FakeS3(bucket_with_demo("OK")), FakeSSM("test-key-123")
run = h.handler(ASK, None, s3=s3, work=work, ssm=ssm)
check(run["status"] == "OK" and run["briefs"] and not run["warnings"],
      "asked for briefs: the briefs step gets the key; plain steps never see it", run.get("warnings"))
check(ssm.asked == [(AWS["api_key_parameter"], True)], "the key is read from Parameter Store, decrypted, once per run")
check(OUTPUTS + "handover/acc_1.md" in s3.objects and OUTPUTS + "handover/acc_1_meta.json" in s3.objects,
      "the briefs and their fingerprints go to outputs/handover/ (the cache for next time)")
s3b = FakeS3({**bucket_with_demo("OK"), OUTPUTS + "handover/acc_1_stale.json": b'{"changes": ["new E9"]}'})
h.handler(ASK, None, s3=s3b, work=work, ssm=FakeSSM("test-key-123"))
check(json.loads(s3b.objects[OUTPUTS + "handover/acc_1_stale.json"]) == {"changes": []},
      "a rewritten brief clears its 'out of date' mark in the bucket (rewritten, since the engine may not delete)")
check(not any(k.endswith("_key.json") for k in s3.objects), "the redaction key never leaves the Lambda")
check("test-key-123" not in s3.objects[OUTPUTS + "last_run.txt"].decode() + json.dumps(run),
      "the key appears in no log and no summary")
again = h.handler(ASK, None, s3=s3, work=work, ssm=FakeSSM("test-key-123"))
check(not any(o.startswith("handover/") for o in again["outputs"]),
      "next run, briefs unchanged: nothing re-uploaded (no new S3 versions for nothing)", again["outputs"])
nokey = h.handler(ASK, None, s3=FakeS3(bucket_with_demo("OK")), work=work, ssm=FakeSSM(None))
check(nokey["status"] == "OK" and any("no API key" in w for w in nokey["warnings"]),
      "no key in Parameter Store: briefs skipped with a warning, the rest still runs", nokey.get("warnings"))
os.environ["BREAK_BRIEFS"] = "1"
down = h.handler(ASK, None, s3=FakeS3(bucket_with_demo("OK")), work=work, ssm=FakeSSM("test-key-123"))
del os.environ["BREAK_BRIEFS"]
check(down["status"] == "OK" and any("briefs.py failed" in w for w in down["warnings"]),
      "the model API fails: a warning, and the cockpit still updates", down.get("warnings"))
h.OPTIONAL = []

print("\nEngine Lambda: the AI spend ledger carried from run to run (outputs/ai_spend.csv)")
LEDGER = h.LEDGER
(bundle / "spends.py").write_text(
    "import os\nfrom pathlib import Path\n"
    f"p = Path('data/{LEDGER}')\n"
    "before = p.read_text(encoding='utf-8') if p.exists() else ''\n"
    "assert 'old-row' in before or not os.environ.get('EXPECT_OLD'), 'last runs ledger not brought back'\n"
    "if os.environ.get('SPEND'):\n"
    "    p.write_text((before or 'header\\n') + 'new-row\\n', encoding='utf-8')\n"
    "print('spend step ran')\n", encoding="utf-8")
h.CODE, h.STEPS = ENGINE_FILES + ["spends.py"], ["spends.py"]
objs = bucket_with_demo("OK")
objs[OUTPUTS + LEDGER] = b"header\nold-row\n"
s3 = FakeS3(objs)
os.environ["EXPECT_OLD"] = "1"
quiet = h.handler({"trigger": "manual"}, None, s3=s3, work=work)
check(quiet["status"] == "OK" and LEDGER not in quiet["outputs"],
      "the ledger so far is brought back before the steps; no model call = not re-uploaded", quiet["outputs"])
os.environ["SPEND"] = "1"
spent = h.handler({"trigger": "manual"}, None, s3=s3, work=work)
del os.environ["SPEND"], os.environ["EXPECT_OLD"]
check(LEDGER in spent["outputs"] and s3.objects[OUTPUTS + LEDGER].decode().splitlines() == ["header", "old-row", "new-row"],
      "a run that called the model adds its rows to the ledger, keeping the old ones", s3.objects.get(OUTPUTS + LEDGER))
first = h.handler({"trigger": "manual"}, None, s3=FakeS3(bucket_with_demo("OK")), work=work)
check(first["status"] == "OK", "no ledger yet (first run ever): not an error")

print("\nEngine Lambda: Slack clicks from the AWS listener (state/clicks/)")


def click_event(n, ts):
    return json.dumps({"event_id": f"ev-{n}", "event_name": "task_claimed", "event_type": "engagement",
                       "timestamp": ts, "account_id": "acc_037", "user_id": None,
                       "properties": {"owner_id": "csm_anna", "topic": "churn", "outcome": None, "note": None,
                                      "slack_user": "U_ANNA"}, "context": {"source": "slack"}}).encode()


(bundle / "reads_clicks.py").write_text(
    "import json\nfrom pathlib import Path\n"
    "lines = Path('data/engagement/slack_clicks.jsonl').read_text(encoding='utf-8').splitlines()\n"
    "ids = [json.loads(l)['event_id'] for l in lines]\n"
    "stamps = [json.loads(l)['timestamp'] for l in lines]\n"
    "assert stamps == sorted(stamps) and set(ids) == {'ev-1', 'ev-2', 'ev-9'}, ids\n"
    "last = max(p.stem for p in Path('data/events').glob('*.jsonl'))\n"
    "import datetime as dt\nrun = (dt.date.fromisoformat(last) + dt.timedelta(days=1)).isoformat()\n"
    "old = next(json.loads(l) for l in lines if json.loads(l)['event_id'] == 'ev-9')\n"
    "assert old['timestamp'] == run + 'T10:00:00Z', old\n"
    "assert not Path('data/clicks').exists(), 'raw click files left behind'\n"
    "print('clicks seen', ids)\n", encoding="utf-8")
h.CODE, h.STEPS = ENGINE_FILES + ["reads_clicks.py"], ["reads_clicks.py"]
s3 = FakeS3(bucket_with_demo("OK"))
s3.objects[CLICKS + "2026-10-19/ev-1.json"] = click_event(1, "2026-10-19T14:00:00Z")
s3.objects[CLICKS + "2026-10-19/ev-2.json"] = click_event(2, "2026-10-19T09:30:00Z")
s3.objects[CLICKS + "2099-01-01/ev-9.json"] = click_event(9, "2099-01-01T10:00:00Z")   # an old card: clock-dated
run = h.handler({"trigger": "manual"}, None, s3=s3, work=work)
check(run["status"] == "OK" and run["slack_clicks"] == 3,
      "the clicks become one engagement file, oldest first, before the steps run", run.get("failed_step"))
check(run["status"] == "OK", "...none dated after the run date (an old card's click is brought back to it)")
check(not any("clicks" in o for o in run["outputs"]), "...and are never uploaded again as outputs", run["outputs"])
s3.objects[CHECK] = json.dumps({"status": "OK", "errors": 0, "warnings": 0, "checked_at": "2026-11-05T00:00:00Z"}).encode()
h.handler(sched, None, s3=s3, work=work)                       # a good scheduled run on this check
quiet = h.handler(sched, None, s3=s3, work=work)
check(quiet["status"] == "SKIPPED", "no click since the last run: the schedule says up to date", quiet)
s3.objects[CLICKS + "2026-10-19/ev-3.json"] = click_event(3, "2026-10-19T15:00:00Z")
s3.modified[CLICKS + "2026-10-19/ev-3.json"] = datetime.now(timezone.utc) + timedelta(minutes=5)
(bundle / "reads_clicks.py").write_text("print('ran')\n", encoding="utf-8")
last = json.loads(s3.objects[OUTPUTS + "run.json"]); last["code"] = h.code_version()    # only the click is new
s3.objects[OUTPUTS + "run.json"] = json.dumps(last).encode()
woke = h.handler(sched, None, s3=s3, work=work)
check(woke["status"] == "OK" and woke["slack_clicks"] == 4,
      "a click after the last run: the daily schedule runs the engine (clicks don't wake it one by one)", woke)

print("\nEngine Lambda: the public demo site (sandbox pages to the site bucket)")
check("roles.py --site" in AWS["engine_steps"] and "roles.py" in ENGINE_FILES,
      "the engine builds the site pages (roles.py --site) and roles.py is bundled")
(bundle / "site_pages.py").write_text(
    "import os\nfrom pathlib import Path\n"
    "d = Path('data/site'); (d / 'sub').mkdir(parents=True)\n"
    "(d / 'index.html').write_text('<p>leader ' + os.environ.get('PAGE_V', '1'), encoding='utf-8')\n"
    "(d / 'csm_mia.html').write_text('<p>mia', encoding='utf-8')\n"
    "(d / 'notes.txt').write_text('not a page', encoding='utf-8')\n"
    "(d / 'sub' / 'x.html').write_text('<p>nested', encoding='utf-8')\n"
    "Path('data/views_data.json').write_text('{}', encoding='utf-8')\n", encoding="utf-8")
h.CODE, h.STEPS = ENGINE_FILES + ["site_pages.py"], ["site_pages.py"]
h.SITE_BUCKET, h.SITE, h.SITE_CACHE = SITE_NAME, AWS["site_folder"], AWS["site_cache_seconds"]
s3 = FakeS3(bucket_with_demo("OK"))
run = h.handler({"trigger": "manual"}, None, s3=s3, work=work)
check(run["status"] == "OK" and sorted(s3.site) == ["csm_mia.html", "index.html"],
      "after a good run, the pages go to the site bucket: HTML only, flat (no folders)", sorted(s3.site))
check(all(v[1] == "text/html; charset=utf-8" and v[2] == f"public, max-age={AWS['site_cache_seconds']}"
          for v in s3.site.values()), "each as HTML, with the cache time from settings")
check(not any(k.startswith(OUTPUTS + "site") for k in s3.objects) and not any(o.startswith("site") for o in run["outputs"]),
      "...and never also to outputs/", run["outputs"])
check(sorted(run["site_pages"]) == ["csm_mia.html", "index.html"], "run.json lists the pages sent")
again = h.handler({"trigger": "manual"}, None, s3=s3, work=work)
check(again["site_pages"] == [], "next run, same pages: nothing sent", again["site_pages"])
os.environ["PAGE_V"] = "2"
changed = h.handler({"trigger": "manual"}, None, s3=s3, work=work)
del os.environ["PAGE_V"]
check(changed["site_pages"] == ["index.html"] and s3.site["index.html"][0] == b"<p>leader 2",
      "a changed page: only that one is sent", changed["site_pages"])
(bundle / "site_then_break.py").write_text(
    "from pathlib import Path\nd = Path('data/site'); d.mkdir(parents=True)\n"
    "(d / 'index.html').write_text('half a run', encoding='utf-8')\nraise SystemExit('broke after the pages')\n",
    encoding="utf-8")
h.CODE, h.STEPS = ENGINE_FILES + ["site_then_break.py"], ["site_then_break.py"]
s3 = FakeS3(bucket_with_demo("OK"))
try:
    h.handler({"trigger": "manual"}, None, s3=s3, work=work)
except RuntimeError:
    pass
check(s3.site_writes == [], "a failed run sends no page (the site keeps the last good one)", s3.site_writes)
h.SITE_BUCKET = ""
h.CODE, h.STEPS = ENGINE_FILES + ["site_pages.py"], ["site_pages.py"]
s3 = FakeS3(bucket_with_demo("OK"))
off = h.handler({"trigger": "manual"}, None, s3=s3, work=work)
check(off["site_pages"] == [] and not s3.site_writes, "no site bucket (site off): nothing sent")

print("\nEngine Lambda: a step that fails")
(bundle / "broken.py").write_text("raise SystemExit('this step broke')\n", encoding="utf-8")
h.CODE, h.STEPS = ENGINE_FILES + ["broken.py"], ["broken.py"]
s3 = FakeS3(bucket_with_demo("OK"))
try:
    h.handler(event, None, s3=s3, work=work)
    check(False, "a failing step raises (the Lambda shows the error)")
except RuntimeError:
    check(True, "a failing step raises (the Lambda shows the error)")
run = json.loads(s3.objects[OUTPUTS + "run.json"])
check(run["status"] == "FAILED" and run["failed_step"] == "broken.py", "run.json says FAILED and names the step", run)
check("this step broke" in s3.objects[OUTPUTS + "last_run.txt"].decode(), "last_run.txt holds the error")

shutil.rmtree(tmp)
print("\nFAILED" if failed else "\nOK")
sys.exit(1 if failed else 0)
