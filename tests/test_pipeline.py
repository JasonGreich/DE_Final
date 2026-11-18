"""End-to-end tests of run_pipeline.py on the small invented fixture (offline: the two APIs are faked).

Covers: a full run works and loads the marts, running twice gives identical tables (idempotent),
an incremental update changes an order without duplicating it, and a stop test still writes run_log.
"""
import sqlite3

import pytest

import run_pipeline
from src.load import run_log
from src.load.checksums import MART_TABLES, STG_TABLES, checksums
from tests.fixtures import synthetic

QUARANTINE_TABLES = ["quarantine_orders", "quarantine_order_items", "quarantine_order_reviews",
                     "quarantine_geolocation", "quarantine_holidays"]
ALL_TABLES = MART_TABLES + STG_TABLES + QUARANTINE_TABLES


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """A fixture data folder, with the APIs replaced by files from the fixture and logs sent to tmp."""
    synthetic.make_fixture(tmp_path)

    def fake_holidays(raw_dir, **_):
        synthetic.write_api_files(raw_dir)
        return {2016: 4, 2017: 4, 2018: 4}

    def fake_rates(raw_dir, **_):
        synthetic.write_api_files(raw_dir)
        return 100

    monkeypatch.setattr(run_pipeline.nager, "extract_holidays", fake_holidays)
    monkeypatch.setattr(run_pipeline.frankfurter, "extract_rates", fake_rates)
    monkeypatch.setattr(run_pipeline, "LOG_DIR", tmp_path / "logs")
    return tmp_path


def wh_path(data_dir):
    return data_dir / "warehouse" / "warehouse.db"


def query(data_dir, sql):
    conn = sqlite3.connect(wh_path(data_dir))
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def test_full_pipeline_loads_marts_and_logs_the_run(data_dir):
    assert run_pipeline.run(full=False, as_of=None, data_dir=data_dir) == 0
    # 60 orders: 1 delivered-without-date quarantined, so 59 staged; items follow
    assert query(data_dir, "SELECT COUNT(*) FROM stg_orders")[0][0] == 59
    assert query(data_dir, "SELECT COUNT(*) FROM fact_order_item")[0][0] == \
        query(data_dir, "SELECT COUNT(*) FROM stg_order_items")[0][0]
    assert query(data_dir, "SELECT COUNT(*) FROM dim_date")[0][0] == 852
    log = run_log.read_run_log(wh_path(data_dir))
    assert len(log) == 1 and log[0]["status"] == "success" and log[0]["tests_failed"] == 0
    assert log[0]["rows_quarantined"] > 0
    assert list((data_dir / "logs").glob("pipeline_*.log"))  # a log file was written


def test_per_order_rate_counts_multi_item_orders_once(data_dir):
    run_pipeline.run(full=False, as_of=None, data_dir=data_dir)
    orders, late = query(data_dir, "SELECT COUNT(*), SUM(is_late) FROM vw_order_late")[0]
    per_item = query(data_dir, "SELECT COUNT(*) FROM fact_order_item WHERE in_scope = 1")[0][0]
    assert orders < per_item          # some orders have 2-3 items, so rows != orders
    # the order with a same-date, later-time delivery (o003) is not late
    assert query(data_dir, "SELECT is_late FROM vw_order_late WHERE order_id = 'o003'")[0][0] == 0


def test_running_twice_gives_identical_tables(data_dir):
    run_pipeline.run(full=False, as_of=None, data_dir=data_dir)
    first = checksums(wh_path(data_dir), ALL_TABLES)
    run_pipeline.run(full=False, as_of=None, data_dir=data_dir)
    second = checksums(wh_path(data_dir), ALL_TABLES)
    assert first == second
    assert len(run_log.read_run_log(wh_path(data_dir))) == 2   # but the run history grows


def test_incremental_update_changes_the_order_without_duplicating_it(data_dir):
    run_pipeline.run(full=False, as_of=None, data_dir=data_dir)
    rows_before = query(data_dir, "SELECT COUNT(*) FROM stg_orders")[0][0]
    store = sqlite3.connect(data_dir / "store" / "olist.db")
    store.execute("UPDATE orders SET order_status = 'canceled' WHERE order_id = 'o058'")  # a recent order
    store.commit()
    store.close()
    run_pipeline.run(full=False, as_of=None, data_dir=data_dir)
    assert query(data_dir, "SELECT order_status FROM stg_orders WHERE order_id = 'o058'") == [("canceled",)]
    assert query(data_dir, "SELECT COUNT(*) FROM stg_orders")[0][0] == rows_before
    assert query(data_dir, "SELECT COUNT(*) FROM stg_orders WHERE order_id = 'o058'")[0][0] == 1


def test_as_of_limits_the_first_run_then_a_second_run_completes_it(data_dir):
    run_pipeline.run(full=False, as_of="2017-06-30", data_dir=data_dir)
    partial = query(data_dir, "SELECT COUNT(*) FROM stg_orders")[0][0]
    run_pipeline.run(full=False, as_of=None, data_dir=data_dir)
    complete = query(data_dir, "SELECT COUNT(*) FROM stg_orders")[0][0]
    assert 0 < partial < complete == 59


def test_a_stop_test_exits_2_and_still_writes_run_log(data_dir):
    # --full on 60 orders breaks test 13 (at least 90,000 orders), a stop-level test
    assert run_pipeline.run(full=True, as_of=None, data_dir=data_dir) == 2
    log = run_log.read_run_log(wh_path(data_dir))
    assert log[-1]["status"] == "failed" and "stop test" in log[-1]["error"]
    assert log[-1]["tests_failed"] >= 1
