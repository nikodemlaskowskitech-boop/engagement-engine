"""
The engine fits in the AWS function's memory (28 Nov 2026). On the laptop there is plenty, so
a step that needs too much only fails in AWS: flags.py did, at 06:17 on 28 Nov ("Out of Memory
Error ... 1.7 GiB/1.7 GiB used"). The small inputs were views, so every mention of one in the
stack of views re-read its file with a ~30 MB buffer, all held until the statement ended.

Each heavy step runs here with DuckDB capped at half the engine Lambda's memory ([aws]
engine_memory_mb), so it has room to spare there. Needs data/ (run_all.ps1).
Run: python tests/memory_test.py
"""
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from settings import SETTINGS                       # noqa: E402

CAP_MB = SETTINGS["aws"]["engine_memory_mb"] // 2
STEPS = ["flags.py", "journeys.py", "team_report.py", "backtest.py", "history.py", "retention.py", "export_views.py"]
# each step in its own Python, every DuckDB connection capped before the step's first query
RUN = f"""
import runpy, sys
sys.path.insert(0, ".")
import duckdb
real = duckdb.connect
def capped(*a, **k):
    con = real(*a, **k)
    con.sql("SET memory_limit = '{CAP_MB}MB'")
    return con
duckdb.connect = capped
sys.argv = [sys.argv[1]]
runpy.run_path(sys.argv[0], run_name="__main__")
"""

failed = 0
print(f"\nEach heavy step with DuckDB capped at {CAP_MB} MB (half the engine Lambda's {SETTINGS['aws']['engine_memory_mb']} MB)")
for step in STEPS:
    t = time.time()
    r = subprocess.run([sys.executable, "-c", RUN, step], cwd=ROOT, capture_output=True, text=True)
    ok = r.returncode == 0
    failed += not ok
    why = next((l for l in (r.stderr or "").splitlines()[::-1] if "Error" in l), "")[:200]
    print(f"  {'PASS' if ok else 'FAIL'}  {step} ({time.time() - t:.0f} s)" + ("" if ok else f"  ->  {why}"))

print(f"\n{'All passed' if not failed else f'{failed} failed'}")
sys.exit(1 if failed else 0)
