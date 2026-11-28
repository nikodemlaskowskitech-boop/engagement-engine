"""
Each input is read once per run (28 Nov 2026, the rule behind the AWS out-of-memory failure).

A view that reads a file reads it again at every mention, with its own buffer, all held until
the statement ends: with enough views on top, a small file cost the engine more than 2 GB. So:
  - the small inputs are tables once connect() returns: the CRM, the team, the CS history,
    the renewals and the ARR bridge;
  - no view reads a file, except the three named in ALLOWED below, each with its reason.
A new view builds on tables or on other views, never on a file. Adding to ALLOWED needs a
reason as good as these, and tests/memory_test.py still has to pass.
Run: python tests/read_once_test.py
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cairnpath_db                                 # noqa: E402
import flags                                        # noqa: E402

TABLES = {"crm_all", "cs_team", "engagement", "renewal_history", "arr_bridge"}
ALLOWED = {
    "events": "the 3.3M events, too big to hold: the Parquet store, read by column and row group",
    "action_log": "written by this run (actions.py) and read back by later steps",
    "open_flags": "the flags this run wrote (flags_*.csv), read back by later steps",
}
failed = 0


def check(ok, what, detail=""):
    global failed
    print(f"  {'PASS' if ok else 'FAIL'}  {what}" + (f"  ->  {detail}" if detail and not ok else ""))
    failed += not ok


print("\nEach input read once per run")
con = cairnpath_db.connect()
flags.add_views(con)
tables = {r[0] for r in con.sql("SELECT table_name FROM information_schema.tables WHERE table_type = 'BASE TABLE'").fetchall()}
check(TABLES <= tables, f"the small inputs are tables after connect(): {', '.join(sorted(TABLES))}", sorted(TABLES - tables))
readers = {n for n, sql in con.sql("SELECT view_name, sql FROM duckdb_views() WHERE NOT internal").fetchall()
           if re.search(r"\bread_(csv|csv_auto|json|json_auto|parquet)\s*\(", sql or "", re.I)}
check(readers <= set(ALLOWED), "no view reads a file but the three allowed (events, the action log, this run's flags)",
      sorted(readers - set(ALLOWED)))
check(set(ALLOWED) <= readers or not readers,
      "every allowed one is still needed (a stale allowance would hide a new one)", sorted(set(ALLOWED) - readers))
print(f"\n{'All passed' if not failed else f'{failed} failed'}")
sys.exit(1 if failed else 0)
