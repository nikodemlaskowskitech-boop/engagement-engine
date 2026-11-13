"""
Reads the product events once per run into a typed store (ARCHITECTURE.md, fix A).

The events arrive as one JSON file per day (a year of them: about 485 MB). Every
step used to parse all of it again, several times: flags.py alone spent about
2.5 minutes of the AWS run doing that. This step reads them once, with the
agreed schema, and writes one Parquet file: columns with their types, compressed,
read in a fraction of the time. Every later step reads that file instead
(cairnpath_db.connect()).

The store remembers which input files it was built from (name, size, time
changed). If the inputs change and nobody runs this step, connect() notices and
reads the JSON again, so a stale store can slow a run down but never give it old
numbers.

    python ingest.py

First engine step after the input checks, on the laptop (run_all.ps1) and in AWS
([aws] engine_steps). Its output stays in the run's work folder: it is never
uploaded (data/<[data] store_folder>/).
"""
import json
import time

import duckdb

import cairnpath_db as db


def main():
    started = time.time()
    store, parquet, stamp = db.store_paths()
    files = db.event_files()
    if not files:
        print("Ingest: no event files found; the steps will report it")
        return
    store.mkdir(parents=True, exist_ok=True)
    stamp.unlink(missing_ok=True)          # until the new store is complete, nobody trusts the old one
    fingerprint = db.inputs_fingerprint(files)
    tmp = parquet.with_name(parquet.name + ".tmp")
    con = duckdb.connect()
    # the same typed read the steps used to do, written once, in the order it was read
    con.sql(f"COPY ({db.events_from_json()}) TO '{db._sql_path(tmp)}' (FORMAT parquet, COMPRESSION zstd)")
    tmp.replace(parquet)
    stamp.write_text(json.dumps(fingerprint), encoding="utf-8")
    rows = con.sql(f"SELECT count(*) FROM read_parquet('{db._sql_path(parquet)}')").fetchone()[0]
    print(f"Ingest: {len(files)} event files, {rows:,} events -> {parquet.relative_to(db.settings.OUTPUTS)} "
          f"({parquet.stat().st_size / 1e6:.0f} MB) in {time.time() - started:.1f}s")


if __name__ == "__main__":
    main()
