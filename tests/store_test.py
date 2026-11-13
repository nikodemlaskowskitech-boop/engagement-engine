"""
The events store (ingest.py, ARCHITECTURE.md fix A, 13 Nov 2026): the events are
read once per run into one typed Parquet file, and every step reads that instead
of the JSON. Speed must never cost correctness:

  - without a store, connect() reads the JSON as before;
  - after ingest.py, connect() reads the store, and every row and value is the same;
  - a new, changed or removed event file makes the store stale: connect() goes back
    to the JSON until ingest.py runs again (slower, never old numbers);
  - a store left half-written (no stamp) is never used.

Needs DuckDB, no AWS, no demo data. Run: python tests/store_test.py
"""
import contextlib
import io
import json
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import settings                                   # noqa: E402

failed = 0


def check(ok, what, detail=""):
    global failed
    print(f"  {'PASS' if ok else 'FAIL'}  {what}" + (f"  ->  {detail}" if detail and not ok else ""))
    failed += not ok


def event(acc, d, name, n, user=None):
    return {"event_id": f"{acc}_{d}_{name}_{user}", "event_name": name,
            "event_type": "system" if name == "daily_event_volume" else "product",
            "timestamp": f"{d}T10:00:00Z", "account_id": acc, "user_id": user,
            "properties": {"event_count": n} if n is not None else {}, "context": {"source": "sdk"}}


with tempfile.TemporaryDirectory() as tmp:
    data = Path(tmp) / "data"
    (data / "events").mkdir(parents=True)
    start = date(2026, 10, 1)
    for i in range(5):
        d = start + timedelta(days=i)
        rows = [event("acc_1", d, "daily_event_volume", 1000 + i), event("acc_1", d, "chart_created", None, "u1"),
                event("acc_2", d, "daily_event_volume", 50 * i), event("acc_2", d, "user_invited", None, "u9")]
        (data / "events" / f"{d}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    (data / "crm_accounts.csv").write_text(
        "account_id,account_name,tier,region,contract_start,renewal_date,contracted_events,csm_owner,last_qbr_date,next_qbr_date\n"
        "acc_1,One,Scale,UK&I,2026-01-01,2027-01-01,365000,,2026-08-01,2026-11-20\nacc_2,Two,Scale,UK&I,2026-01-01,2027-01-01,365000,,2026-08-01,2026-11-20\n",
        encoding="utf-8")
    (data / "cs_team.csv").write_text("owner_id,owner_name,owner_type,region,manager\n", encoding="utf-8")

    os.chdir(tmp)
    settings.INPUTS, settings.OUTPUTS = data, data
    import cairnpath_db as db                     # noqa: E402
    import ingest                                 # noqa: E402

    def source():
        con = db.connect()
        sql = con.sql("SELECT sql FROM duckdb_views() WHERE view_name = 'events'").fetchone()[0]
        return ("store" if "read_parquet" in sql else "json"), con

    ALL = "SELECT * FROM events ORDER BY event_id"

    print("\nWithout a store: the JSON, as before")
    kind, con = source()
    check(kind == "json", "connect() reads the JSON files", kind)
    from_json = con.sql(ALL).fetchall()

    print("\nAfter ingest.py: the store, with the same rows")
    with contextlib.redirect_stdout(io.StringIO()):
        ingest.main()
    _, parquet, stamp = db.store_paths()
    check(parquet.exists() and stamp.exists(), "ingest.py writes the store and its stamp")
    kind, con = source()
    check(kind == "store", "connect() reads the store", kind)
    from_store = con.sql(ALL).fetchall()
    check(from_store == from_json and len(from_store) == 20, "every row and value is the same (types included)",
          f"{len(from_store)} rows vs {len(from_json)}")
    types = dict(con.sql("SELECT column_name, column_type FROM (DESCRIBE events)").fetchall())
    check(types.get("timestamp") == "TIMESTAMP" and types.get("event_date") == "DATE"
          and "event_count BIGINT" in types.get("properties", ""), "the agreed schema is kept", types)

    print("\nThe inputs change: the store is stale, the JSON is read again")
    new_day = start + timedelta(days=5)
    (data / "events" / f"{new_day}.jsonl").write_text(json.dumps(event("acc_1", new_day, "daily_event_volume", 7)) + "\n",
                                                       encoding="utf-8")
    kind, con = source()
    check(kind == "json", "a new event file: back to the JSON", kind)
    check(con.sql("SELECT count(*) FROM events").fetchone()[0] == 21, "...which has the new event")
    with contextlib.redirect_stdout(io.StringIO()):
        ingest.main()
    check(source()[0] == "store", "ingest.py again: the store is used again")
    f = data / "events" / f"{start}.jsonl"
    f.write_text(f.read_text(encoding="utf-8") + json.dumps(event("acc_2", start, "chart_created", None, "u2")) + "\n",
                 encoding="utf-8")
    check(source()[0] == "json", "an edited event file: back to the JSON")
    with contextlib.redirect_stdout(io.StringIO()):
        ingest.main()
    (data / "events" / f"{new_day}.jsonl").unlink()
    check(source()[0] == "json", "a removed event file: back to the JSON")

    print("\nA half-written store is never used")
    with contextlib.redirect_stdout(io.StringIO()):
        ingest.main()
    stamp.unlink()
    check(source()[0] == "json", "no stamp (ingest.py stopped half-way): the JSON")

print(f"\n{'All passed' if not failed else f'{failed} failed'}")
sys.exit(1 if failed else 0)
