"""
One pipeline list and one list of file names (ARCHITECTURE.md fix B, 13 Nov 2026).

  - the laptop's steps (run_all.ps1) and the engine Lambda's come from settings.toml
    [pipeline], so they can't drift apart (they had: retention.py was in one list only);
  - every step names a script that exists; the AWS-only and laptop-only steps land where
    they should; -Regenerate and -Actions add their step;
  - no script types a path to a file another step reads ('data/flags_churn.csv'): each
    asks settings.output_file / out_sql for it by its [outputs] name.

No DuckDB, no AWS. Run: python tests/pipeline_test.py
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import settings                                   # noqa: E402

failed = 0


def check(ok, what, detail=""):
    global failed
    print(f"  {'PASS' if ok else 'FAIL'}  {what}" + (f"  ->  {detail}" if detail and not ok else ""))
    failed += not ok


laptop, aws = settings.pipeline("laptop"), settings.pipeline("aws")
print("\nOne list, two places")
check(settings.SETTINGS["aws"]["engine_steps"] == aws, "the Lambda's steps are [pipeline]'s AWS steps", aws)
check(all((ROOT / s.split()[0]).exists() for s in laptop + aws), "every step is a script in the repo")
shared = [s for s in laptop if s in aws]
check([s for s in laptop if s in shared] == [s for s in aws if s in shared],
      "the steps both places run come in the same order", shared)
check(aws[0] == "ingest.py" and aws[-2:] == ["roles.py --site", "to_parquet.py"],
      "AWS: the events store first, the site pages and Athena's tables last", aws)
check(not {"pull_clicks.py", "cockpit_sync.py", "check_flags.py", "actions.py"} & set(aws),
      "laptop-only steps (Slack, the cockpit's database, the answer key) never run in AWS")
check("roles.py --site" not in laptop and "actions.py" not in laptop and "generate_events.py" not in laptop,
      "the laptop: no site pages; actions and a regenerate only when asked")
check(settings.pipeline("laptop", regenerate=True)[0] == "generate_events.py"
      and "actions.py" in settings.pipeline("laptop", actions=True), "-Regenerate and -Actions add their step")
check(settings.SETTINGS["aws"]["engine_optional_steps"] == ["handover.py --dry-run", settings.SETTINGS["aws"]["briefs_step"]],
      "the optional steps: the briefs' dry run (both places) and the real briefs (AWS, on request)")
out = subprocess.run([sys.executable, "settings.py", "--steps", "laptop", "actions"], cwd=ROOT,
                     capture_output=True, text=True).stdout.split()
check(out and "?handover.py" in out and "actions.py" in out, "settings.py --steps prints run_all's list, optional steps marked", out[:6])

print("\nFiles by name, never by a typed path")
typed = []
for py in sorted(ROOT.glob("*.py")):
    if py.name in ("generate_events.py", "check_inputs.py", "upload.py"):   # they write or read the inputs themselves
        continue
    for n, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
        code = line.split("#")[0]
        if re.search(r"""['"]data/(%s)\.(csv|json)""" % "|".join(settings.SETTINGS["outputs"]), code) \
                and not code.strip().startswith(("print", "sys.exit")):
            typed.append(f"{py.name}:{n}")
check(not typed, "no script reads or writes a hand-off file by a typed path", typed)
check(settings.out_sql("flags_churn").endswith("/data/flags_churn.csv") and "\\" not in settings.out_sql("flags_churn"),
      "out_sql gives DuckDB a forward-slash path under data/", settings.out_sql("flags_churn"))

print(f"\n{'All passed' if not failed else f'{failed} failed'}")
sys.exit(1 if failed else 0)
