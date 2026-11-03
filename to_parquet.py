"""
Writes the engine's results as Parquet tables, for Athena (Phase 6).

    python to_parquet.py

Run after export_views.py (it's the last step of the engine in AWS). Each
table in settings.toml [aws] athena_tables gets its own folder,
data/tables/<name>/<name>.parquet, because Athena treats a folder as a table.

Why Parquet and not the CSV files: Parquet stores each column's type with the
data (dates are dates, ARR is a number) and stores each column separately, so
a query that needs 2 columns reads only those 2. And it has no trouble with
text that contains commas, like the flags' reasons.

Where each table comes from:
  flags_*, account_value   the CSV files flags.py and journeys.py wrote
  tasks                    every task sent to a person, from views_data.json
  crm, cs_team             the engine's own views of the inputs (engine names)
"""

import json
import os
import sys
import tempfile

from cairnpath_db import connect
from settings import OUTPUTS, SETTINGS

sys.stdout.reconfigure(encoding="utf-8")
TABLES = SETTINGS["aws"]["athena_tables"]
OUT = OUTPUTS / "tables"


def sql_path(p):
    return str(p).replace("\\", "/")      # DuckDB wants forward slashes, even on Windows


con = connect()
for name in TABLES:
    target = OUT / name / f"{name}.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    if name in ("crm", "cs_team"):
        source = f"SELECT * FROM {name}"
    elif name == "tasks":
        tasks = json.loads((OUTPUTS / "views_data.json").read_text(encoding="utf-8"))["tasks"]
        tmp = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
        tmp.write("\n".join(json.dumps(t) for t in tasks))
        tmp.close()
        source = f"SELECT * FROM read_json('{sql_path(tmp.name)}', format = 'newline_delimited')"
    else:
        source = f"SELECT * FROM read_csv('{sql_path(OUTPUTS / (name + '.csv'))}', header = true)"
    con.sql(f"COPY ({source}) TO '{sql_path(target)}' (FORMAT parquet)")
    if name == "tasks":
        os.unlink(tmp.name)
    rows = con.sql(f"SELECT count(*) FROM read_parquet('{sql_path(target)}')").fetchone()[0]
    print(f"  {name:<18} {rows:>5} rows  -> {target.relative_to(OUTPUTS.parent).as_posix()}")
print(f"\n{len(TABLES)} tables written for Athena.")
