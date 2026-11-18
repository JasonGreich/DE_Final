"""run_log: one row per pipeline run, written even when the run fails.

The table only grows (it is the history of runs), so it is NOT part of the idempotency checksums.
"""
import sqlite3

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS run_log (
    run_id TEXT PRIMARY KEY,
    started_at TEXT,
    finished_at TEXT,
    mode TEXT,                 -- for example 'full' or 'incremental as-of 2017-06-30'
    rows_extracted INTEGER,
    rows_loaded INTEGER,
    rows_quarantined INTEGER,
    tests_passed INTEGER,
    tests_warned INTEGER,
    tests_failed INTEGER,
    duration_seconds REAL,
    status TEXT,               -- success / failed
    error TEXT
)
"""

COLUMNS = ["run_id", "started_at", "finished_at", "mode", "rows_extracted", "rows_loaded",
           "rows_quarantined", "tests_passed", "tests_warned", "tests_failed",
           "duration_seconds", "status", "error"]


def write_run_log(db_path, row):
    """Insert (or replace) the row for this run_id. `row` is a dict with the COLUMNS keys."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(CREATE_SQL)
        marks = ",".join("?" for _ in COLUMNS)
        conn.execute(f"INSERT OR REPLACE INTO run_log ({','.join(COLUMNS)}) VALUES ({marks})",
                     [row.get(col) for col in COLUMNS])
        conn.commit()
    finally:
        conn.close()


def read_run_log(db_path):
    """All run_log rows as dicts, oldest first."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(CREATE_SQL)
        return [dict(r) for r in conn.execute("SELECT * FROM run_log ORDER BY started_at")]
    finally:
        conn.close()


def count_test_results(db_path, run_id):
    """(passed, warned, failed) from dq_results for one run. 'quarantined' counts as passed:
    the test did its job. 'skipped' is not counted."""
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute("SELECT status, COUNT(*) FROM dq_results WHERE run_id = ? GROUP BY status",
                            (run_id,)).fetchall()
    except sqlite3.OperationalError:  # dq_results does not exist yet
        return 0, 0, 0
    finally:
        conn.close()
    by_status = dict(rows)
    passed = by_status.get("pass", 0) + by_status.get("quarantined", 0)
    return passed, by_status.get("warned", 0), by_status.get("failed", 0)
