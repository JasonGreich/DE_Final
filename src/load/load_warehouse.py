"""Load checks after dbt has built the marts, plus the dbt call itself.

dbt writes the dim_/fact_ tables (it drops and recreates them every run, so a second run gives the
same tables). Here we run dbt and then check that what landed in the warehouse agrees with staging.
"""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DBT_DIR = ROOT / "dbt_project"


class LoadError(Exception):
    """dbt failed or a load check failed."""


def run_dbt(warehouse_path, log=print):
    """Run `dbt build` (models + tests). Raises LoadError if any error-severity test fails."""
    warehouse_path = Path(warehouse_path).resolve()
    env = dict(os.environ)
    env["WAREHOUSE_PATH"] = str(warehouse_path)          # read by dbt_project/profiles.yml
    env["WAREHOUSE_DIR"] = str(warehouse_path.parent)
    dbt_exe = Path(sys.executable).parent / ("dbt.exe" if os.name == "nt" else "dbt")
    command = [str(dbt_exe), "build", "--profiles-dir", str(DBT_DIR), "--project-dir", str(DBT_DIR)]
    result = subprocess.run(command, cwd=DBT_DIR, env=env, capture_output=True, text=True)
    for line in result.stdout.splitlines():
        log("  dbt: " + line)
    if result.returncode != 0:
        raise LoadError(f"dbt build failed with exit code {result.returncode}")


def scalar(conn, sql):
    return conn.execute(sql).fetchone()[0]


def load_checks(warehouse_path):
    """Compare the marts with staging. Returns {check name: (expected, found)}; raises LoadError on a mismatch."""
    conn = sqlite3.connect(str(warehouse_path))
    try:
        checks = {
            "fact rows = staged order items": (
                scalar(conn, "SELECT COUNT(*) FROM stg_order_items"),
                scalar(conn, "SELECT COUNT(*) FROM fact_order_item")),
            "dim_customer rows = staged customers": (
                scalar(conn, "SELECT COUNT(*) FROM stg_customers"),
                scalar(conn, "SELECT COUNT(*) FROM dim_customer")),
            "dim_seller rows = staged sellers": (
                scalar(conn, "SELECT COUNT(*) FROM stg_sellers"),
                scalar(conn, "SELECT COUNT(*) FROM dim_seller")),
            "dim_product rows = staged products": (
                scalar(conn, "SELECT COUNT(*) FROM stg_products"),
                scalar(conn, "SELECT COUNT(*) FROM dim_product")),
            "orders in vw_order_late = in-scope orders with items": (
                scalar(conn, "SELECT COUNT(DISTINCT order_id) FROM stg_order_items WHERE in_scope = 1"),
                scalar(conn, "SELECT COUNT(*) FROM vw_order_late")),
        }
    finally:
        conn.close()
    bad = {name: pair for name, pair in checks.items() if pair[0] != pair[1]}
    if bad:
        raise LoadError(f"load checks failed: {bad}")
    return checks
