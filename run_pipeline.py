"""Run the whole pipeline with one command:  python run_pipeline.py [--full] [--as-of YYYY-MM-DD]

Order: store -> extract (Olist, holidays, rates) -> stage + data tests + quarantine -> dbt build -> load checks.
Writes logs/pipeline_<timestamp>.log and one run_log row (even if the run fails).
Exit code: 0 success, 1 failure (dbt, load check, extraction), 2 a stop-level data test.
"""
import argparse
import logging
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from src.extract import frankfurter, nager, olist, seed_store, watermark
from src.load import load_warehouse, run_log
from src.quality.tests import StopRun
from src.transform import staging

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"


class Tee:
    """Send everything printed to both the screen and the log file."""

    def __init__(self, screen, log_file):
        self.screen, self.log_file = screen, log_file

    def write(self, text):
        self.screen.write(text)
        self.log_file.write(text)

    def flush(self):
        self.screen.flush()
        self.log_file.flush()


def data_paths(data_dir):
    """The four places data lives, under one data directory."""
    data_dir = Path(data_dir)
    return {"seed": data_dir / "seed", "store": data_dir / "store" / "olist.db",
            "warehouse": data_dir / "warehouse" / "warehouse.db", "raw": data_dir / "raw"}


def describe_mode(full, as_of):
    parts = ["full" if full else "incremental"]
    if as_of:
        parts.append(f"as-of {as_of}")
    return " ".join(parts)


def print_summary(row, recon):
    """The final block printed at the end of every run."""
    print("\n" + "=" * 62)
    print(f"RUN SUMMARY  {row['run_id']}  ({row['mode']})  status: {row['status'].upper()}")
    print("=" * 62)
    print(f"  rows extracted    {row['rows_extracted']:>12,}")
    print(f"  rows loaded       {row['rows_loaded']:>12,}   (staged; out-of-scope orders included)")
    print(f"  rows quarantined  {row['rows_quarantined']:>12,}")
    print(f"  tests passed      {row['tests_passed']:>12,}")
    print(f"  tests warned      {row['tests_warned']:>12,}")
    print(f"  tests failed      {row['tests_failed']:>12,}")
    print(f"  duration          {row['duration_seconds']:>11.1f}s")
    if row.get("error"):
        print(f"  error: {row['error']}")
    print("=" * 62)


def ensure_store(paths):
    """Build the store database from the seed CSVs (or Kaggle) if it does not exist yet."""
    if paths["store"].exists():
        print(f"Store database found: {paths['store']}")
        return
    print("Store database missing: building it from the seed files (or Kaggle).")
    seed_store.build_database(paths["seed"], paths["store"])


def run(full, as_of, data_dir):
    paths = data_paths(data_dir)
    started = datetime.now(timezone.utc)
    run_id = started.strftime("%Y%m%dT%H%M%SZ")
    LOG_DIR.mkdir(exist_ok=True)
    log_path = LOG_DIR / f"pipeline_{run_id}.log"
    paths["warehouse"].parent.mkdir(parents=True, exist_ok=True)
    clock = time.time()

    row = {"run_id": run_id, "started_at": started.isoformat(timespec="seconds"),
           "mode": describe_mode(full, as_of), "rows_extracted": 0, "rows_loaded": 0,
           "rows_quarantined": 0, "status": "failed", "error": None}
    exit_code, recon = 1, []
    real_stdout = sys.stdout
    with open(log_path, "w", encoding="utf-8") as log_file:
        sys.stdout = Tee(real_stdout, log_file)
        logging.basicConfig(level=logging.INFO, stream=sys.stdout, force=True,
                            format="%(asctime)s %(levelname)s %(message)s")
        try:
            print(f"Pipeline run {run_id}  mode: {row['mode']}  log: {log_path}")
            print("\n[1/5] Store")
            ensure_store(paths)
            print("\n[2/5] Extract")
            as_of_date = watermark.parse_as_of(as_of) if as_of else None
            extract_summary = olist.run_extract(paths["store"], paths["warehouse"], paths["raw"],
                                                full=full, as_of=as_of_date)
            holiday_counts = nager.extract_holidays(raw_dir=paths["raw"])
            fx_count = frankfurter.extract_rates(raw_dir=paths["raw"])
            row["rows_extracted"] = (sum(extract_summary["counts"].values())
                                     + sum(holiday_counts.values()) + fx_count)
            print("\n[3/5] Stage, data tests, quarantine")
            summary = staging.run_staging(paths["warehouse"], paths["raw"], run_id=run_id, full=full)
            staging.print_report(summary)
            recon = summary["recon"]
            row["rows_extracted"] = sum(r["extracted"] for r in recon)
            row["rows_loaded"] = sum(r["staged"] for r in recon)
            row["rows_quarantined"] = sum(r["quarantined"] for r in recon)
            print("\n[4/5] dbt build (marts, views, tests)")
            load_warehouse.run_dbt(paths["warehouse"])
            print("\n[5/5] Load checks")
            for name, (expected, found) in load_warehouse.load_checks(paths["warehouse"]).items():
                print(f"  OK  {name}: {found:,}")
            row["status"], exit_code = "success", 0
        except StopRun as err:
            row["error"], exit_code = f"stop test: {err}", 2
            print(f"\nRUN STOPPED by a stop-level data test: {err}")
        except Exception as err:  # noqa: BLE001 - any failure must still reach the run_log
            row["error"] = f"{type(err).__name__}: {err}"
            print("\nRUN FAILED:\n" + traceback.format_exc())
        finally:
            finished = datetime.now(timezone.utc)
            row["finished_at"] = finished.isoformat(timespec="seconds")
            row["duration_seconds"] = round(time.time() - clock, 1)
            row["tests_passed"], row["tests_warned"], row["tests_failed"] = \
                run_log.count_test_results(paths["warehouse"], run_id)
            run_log.write_run_log(paths["warehouse"], row)  # always, even after a failure
            print_summary(row, recon)
            sys.stdout = real_stdout
    return exit_code


def main():
    parser = argparse.ArgumentParser(description="Olist late-deliveries pipeline")
    parser.add_argument("--full", action="store_true", help="ignore the watermark and extract everything")
    parser.add_argument("--as-of", help="only extract orders purchased on or before YYYY-MM-DD")
    parser.add_argument("--data-dir", default=str(ROOT / "data"),
                        help="folder with seed/, store/, raw/, warehouse/ (default: ./data)")
    args = parser.parse_args()
    sys.exit(run(args.full, args.as_of, args.data_dir))


if __name__ == "__main__":
    main()
