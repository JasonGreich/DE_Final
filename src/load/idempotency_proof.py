"""Run the real pipeline twice and prove the second run changes nothing.

Usage:  python -m src.load.idempotency_proof
Writes docs/idempotency_proof.txt: row counts and an order-independent checksum of every
dim_, fact_, stg_ and quarantine_ table after run 1 and after run 2, plus both run times.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import run_pipeline
from src.load import run_log
from src.load.checksums import MART_TABLES, STG_TABLES, checksums

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "idempotency_proof.txt"
QUARANTINE = ["quarantine_orders", "quarantine_order_items", "quarantine_order_reviews",
              "quarantine_geolocation", "quarantine_holidays"]


def main():
    warehouse = ROOT / "data" / "warehouse" / "warehouse.db"
    tables = MART_TABLES + STG_TABLES + QUARANTINE
    snapshots = []
    for number in (1, 2):
        print(f"\n######## proof run {number} ########")
        code = run_pipeline.run(full=True, as_of=None, data_dir=ROOT / "data")
        if code != 0:
            sys.exit(code)
        snapshots.append(checksums(warehouse, tables))
    history = run_log.read_run_log(warehouse)[-2:]

    lines = [f"Idempotency proof, generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
             "Two consecutive `run_pipeline.py --full` runs on the same input.",
             "Checksum = sum of a hash of every row (order-independent); run_id and quarantined_at are ignored.", "",
             f"{'table':26s}{'rows run 1':>12s}{'rows run 2':>12s}  {'checksum run 1':>20s}  {'checksum run 2':>20s}  same"]
    all_same = True
    for table in tables:
        (rows1, sum1), (rows2, sum2) = snapshots[0][table], snapshots[1][table]
        same = rows1 == rows2 and sum1 == sum2
        all_same = all_same and same
        lines.append(f"{table:26s}{rows1:>12,}{rows2:>12,}  {sum1:>20d}  {sum2:>20d}  {'YES' if same else 'NO'}")
    lines += ["", "run_log rows for the two runs:"]
    for row in history:
        lines.append(f"  {row['run_id']}  {row['status']}  extracted={row['rows_extracted']:,} "
                     f"loaded={row['rows_loaded']:,} quarantined={row['rows_quarantined']:,} "
                     f"tests passed/warned/failed={row['tests_passed']}/{row['tests_warned']}/{row['tests_failed']} "
                     f"duration={row['duration_seconds']}s")
    lines += ["", "RESULT: " + ("IDENTICAL - the second run changed nothing." if all_same else "DIFFERENT - not idempotent!")]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n" + "\n".join(lines))
    sys.exit(0 if all_same else 1)


if __name__ == "__main__":
    main()
