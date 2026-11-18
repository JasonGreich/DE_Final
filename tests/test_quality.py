"""Stage 3 tests: the data tests, severities, and quarantine routing."""
import json
import sqlite3

import pandas as pd
import pytest

from src.quality import tests as dq
from src.quality.quarantine import Quarantine
from src.quality.tests import StopRun
from src.transform import staging


def make_frames():
    """A tiny, fully valid set of raw frames. Each test breaks one thing."""
    return {
        "orders": pd.DataFrame({
            "order_id": ["o1", "o2"], "customer_id": ["c1", "c2"], "order_status": ["delivered", "shipped"],
            "order_purchase_timestamp": ["2017-01-01 10:00:00", "2017-01-02 10:00:00"],
            "order_delivered_customer_date": ["2017-01-10 10:00:00", None],
            "order_estimated_delivery_date": ["2017-01-15 00:00:00", "2017-01-20 00:00:00"]}),
        "order_items": pd.DataFrame({
            "order_id": ["o1", "o2"], "order_item_id": [1, 1], "product_id": ["p1", "p1"],
            "seller_id": ["s1", "s1"], "price": [10.0, 5.0], "freight_value": [2.0, 1.0]}),
        "sellers": pd.DataFrame({"seller_id": ["s1"]}),
        "products": pd.DataFrame({"product_id": ["p1"]}),
        "order_reviews": pd.DataFrame({"review_id": ["r1"], "order_id": ["o1"], "review_score": [5]}),
        "fx_rates": pd.DataFrame({"rate_date": ["2017-01-02", "2017-01-03"], "rate": [0.30, 0.31]}),
        "holidays": pd.DataFrame({"holiday_date": ["2017-01-01"], "name": ["New Year"]}),
        "geolocation": pd.DataFrame({"geo_row_id": [1, 2], "geolocation_zip_code_prefix": ["01000", "02000"],
                                     "geolocation_lat": [-23.5, -10.0], "geolocation_lng": [-46.6, -40.0]}),
        "_full_run": False,
    }


def run(frames):
    """Run the raw tests; return (results, quarantine). StopRun propagates."""
    q = Quarantine("run1", "2026-01-01T00:00:00")
    results = []
    dq.run_raw_tests(frames, q, results)
    return results, q


def status_of(results, number):
    return next(r["status"] for r in results if r["test_no"] == number)


def test_valid_data_passes_every_test_and_test_13_is_skipped_on_incremental_runs():
    results, q = run(make_frames())
    assert len(results) == 14  # test 14 runs later, after staging
    assert {r["test_no"] for r in results if r["status"] == "pass"} == set(range(1, 16)) - {13, 14}
    assert status_of(results, 13) == "skipped"
    assert all(q.count(s) == 0 for s in ("orders", "order_items", "order_reviews", "geolocation"))


# ---------------------------------------------------------------- stop tests

def test_stop_1_duplicate_order_id_aborts():
    frames = make_frames()
    frames["orders"].loc[1, "order_id"] = "o1"
    with pytest.raises(StopRun, match="test 1"):
        run(frames)


def test_stop_2_unparseable_purchase_timestamp_aborts():
    frames = make_frames()
    frames["orders"].loc[0, "order_purchase_timestamp"] = "01/01/2017"
    with pytest.raises(StopRun, match="test 2"):
        run(frames)


def test_stop_3_item_without_order_aborts_and_earlier_results_are_kept():
    frames = make_frames()
    frames["order_items"].loc[0, "order_id"] = "ghost"
    q, results = Quarantine("r", "t"), []
    with pytest.raises(StopRun, match="test 3"):
        dq.run_raw_tests(frames, q, results)
    assert [r["status"] for r in results] == ["pass", "pass", "failed"]  # results so far are still there


def test_stop_13_needs_90000_orders_but_only_on_a_full_run():
    frames = make_frames()
    frames["_full_run"] = True
    with pytest.raises(StopRun, match="test 13"):
        run(frames)


# ---------------------------------------------------------------- quarantine tests keep the reason

def test_missing_seller_is_quarantined_with_reason_and_raw_row():
    frames = make_frames()
    frames["order_items"].loc[0, "seller_id"] = "nobody"
    results, q = run(frames)
    assert status_of(results, 4) == "quarantined"
    entry = q._rows["order_items"]["o1|1"]
    assert entry["reasons"] == ["seller_id_not_in_sellers"]
    assert entry["raw"]["seller_id"] == "nobody" and entry["raw"]["price"] == 10.0


def test_missing_product_negative_price_and_both_reasons_merge_into_one_row():
    frames = make_frames()
    frames["order_items"].loc[0, "product_id"] = "none"
    frames["order_items"].loc[0, "price"] = -1.0
    results, q = run(frames)
    assert q.count("order_items") == 1  # one row, two reasons
    assert q._rows["order_items"]["o1|1"]["reasons"] == [
        "product_id_not_in_products", "negative_or_missing_price_or_freight"]


def test_delivered_without_date_and_delivery_before_purchase_go_to_quarantine():
    frames = make_frames()
    frames["orders"].loc[0, "order_delivered_customer_date"] = None
    frames["orders"].loc[1, "order_status"] = "delivered"
    frames["orders"].loc[1, "order_delivered_customer_date"] = "2016-12-31 10:00:00"
    results, q = run(frames)
    assert q._rows["orders"]["o1"]["reasons"] == ["delivered_without_delivery_date"]
    assert q._rows["orders"]["o2"]["reasons"] == ["delivery_before_purchase"]


def test_children_of_a_quarantined_order_follow_it_with_a_reason():
    frames = make_frames()
    frames["orders"].loc[0, "order_delivered_customer_date"] = None   # o1 gets quarantined
    results, q = run(frames)
    clean = staging.apply_quarantine(frames, q)
    assert list(clean["orders"]["order_id"]) == ["o2"]
    assert list(clean["order_items"]["order_id"]) == ["o2"]
    assert list(clean["order_reviews"]["order_id"]) == []
    assert q._rows["order_items"]["o1|1"]["reasons"] == ["parent_order_quarantined"]
    assert q.count("order_reviews") == 1


def test_review_with_two_problems_is_stored_once_with_both_reasons():
    frames = make_frames()
    frames["order_reviews"] = pd.DataFrame({"review_id": ["r9"], "order_id": ["ghost"], "review_score": [6]})
    results, q = run(frames)
    assert q.count("order_reviews") == 1
    assert q._rows["order_reviews"]["r9|ghost"]["reasons"] == [
        "review_score_not_1_to_5", "review_order_not_in_orders"]


def test_geolocation_outside_brazil_is_quarantined_row_by_row():
    frames = make_frames()
    frames["geolocation"] = pd.DataFrame({
        "geo_row_id": [1, 2, 3], "geolocation_zip_code_prefix": ["01000"] * 3,
        "geolocation_lat": [-23.5, 28.0, 28.0], "geolocation_lng": [-46.6, -15.5, -15.5]})  # two identical bad rows
    results, q = run(frames)
    assert status_of(results, 15) == "quarantined"
    assert q.count("geolocation") == 2   # identical rows are still counted separately


# ---------------------------------------------------------------- warn tests continue the run

def test_warn_tests_do_not_stop_the_run():
    frames = make_frames()
    frames["orders"].loc[0, "order_status"] = "teleported"
    frames["fx_rates"] = pd.DataFrame({"rate_date": ["2017-01-02", "2017-01-20"], "rate": [0.30, 0.90]})
    frames["holidays"] = pd.DataFrame({"holiday_date": ["2017-01-01"] * 2, "name": ["New Year"] * 2})
    results, q = run(frames)
    assert [status_of(results, n) for n in (6, 11, 12)] == ["warned", "warned", "warned"]
    assert "teleported" in next(r["detail"] for r in results if r["test_no"] == 6)
    assert "1 rates outside" in next(r["detail"] for r in results if r["test_no"] == 11)
    assert "1 gaps longer than 5" in next(r["detail"] for r in results if r["test_no"] == 11)


# ---------------------------------------------------------------- test 14

def raw_orders_connection():
    wh = sqlite3.connect(":memory:")
    wh.execute("CREATE TABLE raw_orders (order_id TEXT, order_status TEXT, order_purchase_timestamp TEXT, "
               "order_delivered_customer_date TEXT, order_estimated_delivery_date TEXT)")
    wh.executemany("INSERT INTO raw_orders VALUES (?,?,?,?,?)", [
        ("a", "delivered", "2017-01-01 10:00:00", "2017-01-12 10:00:00", "2017-01-10 00:00:00"),  # late
        ("b", "delivered", "2017-01-01 10:00:00", "2017-01-10 10:00:00", "2017-01-10 00:00:00"),  # same day: on time
        ("c", "canceled", "2017-01-01 10:00:00", None, "2017-01-10 00:00:00")])
    return wh


def test_14_passes_when_the_staged_rate_matches_the_raw_recomputation():
    stg = pd.DataFrame({"order_id": ["a", "b", "c"], "in_scope": [1, 1, 0], "is_late": [1, 0, 0]})
    results = []
    dq.run_late_rate_test(stg, raw_orders_connection(), results)
    assert results[0]["status"] == "pass" and results[0]["rows_checked"] == 2
    assert "1/2" in results[0]["detail"]


def test_14_stops_the_run_when_the_staged_rate_is_wrong():
    stg = pd.DataFrame({"order_id": ["a", "b", "c"], "in_scope": [1, 1, 0], "is_late": [1, 1, 0]})  # b wrongly late
    with pytest.raises(StopRun, match="test 14"):
        dq.run_late_rate_test(stg, raw_orders_connection(), [])


# ---------------------------------------------------------------- quarantine tables

def test_quarantine_write_stores_reason_run_id_and_json_and_is_rebuilt_not_appended():
    q = Quarantine("run7", "2026-01-01T00:00:00")
    q.add("orders", pd.DataFrame({"order_id": ["o1"], "x": [None]}), ["order_id"], "because")
    wh = sqlite3.connect(":memory:")
    q.write(wh)
    q.write(wh)  # writing twice must not duplicate
    rows = wh.execute("SELECT source_table, row_key, reason, run_id, quarantined_at, raw_row "
                      "FROM quarantine_orders").fetchall()
    assert len(rows) == 1
    assert rows[0][:5] == ("orders", "o1", "because", "run7", "2026-01-01T00:00:00")
    assert json.loads(rows[0][5]) == {"order_id": "o1", "x": None}   # a missing value is JSON null
    assert wh.execute("SELECT COUNT(*) FROM quarantine_holidays").fetchone() == (0,)  # empty tables exist too


def test_dq_results_gets_one_row_per_test_and_appends_per_run():
    results, _ = run(make_frames())
    wh = sqlite3.connect(":memory:")
    dq.write_results(wh, "run1", "t1", results)
    dq.write_results(wh, "run2", "t2", results)
    assert wh.execute("SELECT COUNT(*) FROM dq_results WHERE run_id='run1'").fetchone() == (14,)
    assert wh.execute("SELECT COUNT(*) FROM dq_results").fetchone() == (28,)
    cols = [c[1] for c in wh.execute("PRAGMA table_info(dq_results)")]
    assert {"test", "severity", "rows_checked", "rows_failed", "run_seconds"} <= set(cols)
