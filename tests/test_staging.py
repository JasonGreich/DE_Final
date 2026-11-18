"""Stage 3 tests: staging end to end on a tiny warehouse, joins, reconciliation, idempotency.

Tiny data (all invented):
  o1 delivered LATE, one item, handoff after the shipping limit  -> cause 'seller', holiday (1 May) in window
  o2 delivered on time, two items
  o3 canceled                                                    -> staged with in_scope = 0
  o4 'delivered' but no delivery date                            -> quarantined (and its item and review follow)
  o1 has two reviews (the later one is kept), geolocation has one point outside Brazil,
  and the holiday files contain one state holiday and Carnival.
"""
import hashlib
import json
import sqlite3

import pandas as pd
import pytest

from src.extract import olist
from src.quality.tests import StopRun
from src.transform import staging


def insert(wh, table, rows):
    marks = ",".join("?" * len(rows[0]))
    wh.executemany(f"INSERT INTO raw_{table} VALUES ({marks})", rows)


def holiday(date, name, types, is_global=True):
    return {"date": date, "localName": name, "name": name, "countryCode": "BR", "global": is_global,
            "counties": None if is_global else ["BR-SP"], "types": types}


@pytest.fixture()
def world(tmp_path):
    """A tiny warehouse (raw_ tables filled) plus a raw folder with holiday and rate files."""
    wh_path, raw_dir = tmp_path / "wh.db", tmp_path / "raw"
    raw_dir.mkdir()
    wh = sqlite3.connect(wh_path)
    olist.ensure_raw_tables(wh)
    insert(wh, "customers", [("c1", "u1", "01000", " Sao Paulo ", "sp"), ("c2", "u2", "02000", "rio", "RJ"),
                             ("c3", "u3", "01000", "sao paulo", "SP"), ("c4", "u4", "03000", "salvador", "BA")])
    insert(wh, "sellers", [("s1", "01000", "sao paulo", "SP"), ("s2", "02000", "rio", "RJ")])
    insert(wh, "products", [("p1", "beleza_saude", 10, 20, 1, 100, 1, 1, 1), ("p2", "pc_gamer", 10, 20, 1, 100, 1, 1, 1),
                            ("p3", None, None, None, None, None, None, None, None)])
    insert(wh, "product_category_name_translation", [("beleza_saude", "health_beauty")])
    insert(wh, "geolocation", [("01000", -23.0, -46.0, "x", "SP"), ("01000", -24.0, -47.0, "x", "SP"),
                               ("02000", -22.9, -43.2, "x", "RJ"), ("04000", 28.0, -15.5, "x", "SP")])
    insert(wh, "orders", [
        ("o1", "c1", "delivered", "2017-04-20 10:00:00", None, "2017-04-28 09:00:00", "2017-05-12 15:00:00", "2017-05-05 00:00:00"),
        ("o2", "c2", "delivered", "2017-06-01 10:00:00", None, "2017-06-02 09:00:00", "2017-06-08 15:00:00", "2017-06-10 00:00:00"),
        ("o3", "c3", "canceled", "2017-06-03 10:00:00", None, None, None, "2017-06-20 00:00:00"),
        ("o4", "c4", "delivered", "2017-06-04 10:00:00", None, "2017-06-05 09:00:00", None, "2017-06-20 00:00:00")])
    insert(wh, "order_items", [
        ("o1", 1, "p1", "s1", "2017-04-25 10:00:00", 10.0, 2.0),
        ("o2", 1, "p1", "s1", "2017-06-02 10:00:00", 20.0, 4.0), ("o2", 2, "p2", "s2", "2017-06-02 10:00:00", 30.0, 3.0),
        ("o3", 1, "p3", "s2", "2017-06-05 10:00:00", 15.0, 5.0), ("o4", 1, "p1", "s1", "2017-06-06 10:00:00", 8.0, 1.0)])
    insert(wh, "order_reviews", [
        ("rv_a", "o1", 1, None, None, "2017-05-13 00:00:00", "2017-05-13 10:00:00"),
        ("rv_b", "o1", 4, None, None, "2017-05-14 00:00:00", "2017-05-14 10:00:00"),   # later: kept
        ("rv_c", "o2", 5, None, None, "2017-06-09 00:00:00", "2017-06-09 10:00:00"),
        ("rv_d", "o4", 3, None, None, "2017-06-09 00:00:00", "2017-06-09 10:00:00")])
    wh.commit()
    wh.close()
    # holiday files: 2017 has Labour Day, Carnival and a state holiday; the other years one holiday each
    files = {2016: [holiday("2016-01-01", "New Year", ["Public"])],
             2017: [holiday("2017-05-01", "Labour Day", ["Public"]),
                    holiday("2017-02-27", "Carnival", ["Bank", "Optional"]),
                    holiday("2017-07-09", "Constitutionalist Revolution", ["Public"], is_global=False)],
             2018: [holiday("2018-01-01", "New Year", ["Public"])]}
    for year, records in files.items():
        (raw_dir / f"nager_holidays_{year}_20260101T000000000000Z.json").write_text(json.dumps(records))
    rates = [{"date": d, "base": "BRL", "quote": "USD", "rate": 0.32} for d in ("2017-04-18", "2017-04-19", "2017-04-20")]
    (raw_dir / "frankfurter_brl_usd_20260101T000000000000Z.json").write_text(json.dumps(rates))
    return wh_path, raw_dir


def table_checksum(wh_path, table):
    """Order-independent checksum of a table."""
    wh = sqlite3.connect(wh_path)
    rows = sorted(repr(r) for r in wh.execute(f"SELECT * FROM {table}"))
    wh.close()
    return hashlib.sha256("\n".join(rows).encode()).hexdigest()


def query(wh_path, sql):
    wh = sqlite3.connect(wh_path)
    try:
        return wh.execute(sql).fetchall()
    finally:
        wh.close()


def test_staging_end_to_end_counts_and_flags(world):
    wh_path, raw_dir = world
    summary = staging.run_staging(wh_path, raw_dir)
    recon = {r["source"]: r for r in summary["recon"]}
    assert all(r["ok"] for r in recon.values())
    assert (recon["orders"]["extracted"], recon["orders"]["quarantined"], recon["orders"]["staged"]) == (4, 1, 3)
    assert recon["orders"]["of_which_out_of_scope"] == 1           # the canceled order, kept with in_scope = 0
    assert (recon["order_items"]["quarantined"], recon["order_items"]["staged"]) == (1, 4)
    assert (recon["order_reviews"]["quarantined"], recon["order_reviews"]["staged"]) == (2, 2)  # 1 older + 1 orphaned
    assert (recon["geolocation"]["quarantined"], recon["geolocation"]["staged"]) == (1, 3)      # staged = points averaged
    assert (recon["holidays"]["quarantined"], recon["holidays"]["staged"]) == (1, 4)
    # business rules applied in the staged tables
    o1 = query(wh_path, "SELECT in_scope, is_late, days_late, holiday_in_window, carnival_in_window, customer_state "
                        "FROM stg_orders WHERE order_id='o1'")[0]
    assert o1 == (1, 1, 7, 1, 0, "SP")   # late by 7 days, 1 May inside the window, state upper-cased
    assert query(wh_path, "SELECT is_late, holiday_in_window FROM stg_orders WHERE order_id='o2'") == [(0, 0)]
    assert query(wh_path, "SELECT in_scope, is_late FROM stg_orders WHERE order_id='o3'") == [(0, 0)]
    assert query(wh_path, "SELECT delay_cause, handoff_late_days, transit_days FROM stg_order_items "
                          "WHERE order_id='o1'") == [("seller", 3, 14)]
    assert query(wh_path, "SELECT delay_cause FROM stg_order_items WHERE order_id='o2'") == [(None,), (None,)]


def test_staging_geography_products_and_reviews(world):
    wh_path, raw_dir = world
    staging.run_staging(wh_path, raw_dir)
    # one centroid per zip prefix; the outside-Brazil row is gone
    assert query(wh_path, "SELECT zip_prefix, lat, lng, n_points FROM stg_geolocation ORDER BY 1") == [
        ("01000", -23.5, -46.5, 2), ("02000", -22.9, -43.2, 1)]
    # customer c1 and seller s1 share the prefix 01000, so the distance is 0; o2 item 1 goes SP -> RJ
    dist = dict(query(wh_path, "SELECT order_id || order_item_id, distance_km FROM stg_order_items"))
    assert dist["o11"] == 0 and 330 < dist["o21"] < 380
    assert query(wh_path, "SELECT lat FROM stg_customers WHERE customer_id='c4'") == [(None,)]  # no centroid: stays NULL
    assert query(wh_path, "SELECT freight_share FROM stg_order_items WHERE order_id='o1'") == [(0.2,)]
    # categories: translated, untranslated (falls back, flagged) and missing
    cats = {r[0]: r[1:] for r in query(wh_path, "SELECT product_id, category_en, category_en_is_fallback FROM stg_products")}
    assert cats == {"p1": ("health_beauty", 0), "p2": ("pc_gamer", 1), "p3": (None, 0)}
    # the later of the two reviews of o1 is kept; text was trimmed and lower-cased
    assert query(wh_path, "SELECT review_id, review_score FROM stg_order_reviews WHERE order_id='o1'") == [("rv_b", 4)]
    assert query(wh_path, "SELECT city FROM stg_customers WHERE customer_id='c1'") == [("sao paulo",)]
    reasons = dict(query(wh_path, "SELECT row_key, reason FROM quarantine_order_reviews"))
    assert reasons == {"rv_a|o1": "superseded_by_later_review_for_same_order", "rv_d|o4": "parent_order_quarantined"}


def test_holidays_state_holiday_quarantined_and_carnival_flagged(world):
    wh_path, raw_dir = world
    staging.run_staging(wh_path, raw_dir)
    rows = query(wh_path, "SELECT holiday_date, is_carnival, is_public_national FROM stg_holidays ORDER BY 1")
    assert ("2017-02-27", 1, 0) in rows and ("2017-05-01", 0, 1) in rows
    assert "2017-07-09" not in [r[0] for r in rows]
    assert query(wh_path, "SELECT reason FROM quarantine_holidays") == [("not_national_global_false",)]


def test_running_twice_gives_identical_tables_and_appends_logs(world):
    wh_path, raw_dir = world
    staging.run_staging(wh_path, raw_dir, run_id="run1")
    first = {t: table_checksum(wh_path, t) for t in
             ["stg_orders", "stg_order_items", "stg_order_reviews", "stg_customers", "stg_sellers",
              "stg_products", "stg_geolocation", "stg_holidays", "stg_fx_rates"]}
    staging.run_staging(wh_path, raw_dir, run_id="run2")
    second = {t: table_checksum(wh_path, t) for t in first}
    assert first == second
    assert query(wh_path, "SELECT COUNT(*) FROM quarantine_orders") == [(1,)]    # rebuilt, not doubled
    assert query(wh_path, "SELECT COUNT(*) FROM dq_results") == [(30,)]          # 15 tests x 2 runs
    assert query(wh_path, "SELECT run_id FROM quarantine_orders") == [("run2",)]


def test_dq_results_has_15_tests_per_run_including_test_14(world):
    wh_path, raw_dir = world
    staging.run_staging(wh_path, raw_dir, run_id="r")
    rows = query(wh_path, "SELECT test_no, severity, status FROM dq_results ORDER BY test_no")
    assert [r[0] for r in rows] == list(range(1, 16))
    assert dict((r[0], r[2]) for r in rows)[7] == "quarantined"
    assert dict((r[0], r[2]) for r in rows)[13] == "skipped"


def test_a_stop_test_aborts_keeps_old_tables_and_still_logs_the_failure(world):
    wh_path, raw_dir = world
    staging.run_staging(wh_path, raw_dir, run_id="good")
    before = table_checksum(wh_path, "stg_orders")
    wh = sqlite3.connect(wh_path)
    insert(wh, "order_items", [("ghost", 1, "p1", "s1", "2017-06-02 10:00:00", 1.0, 1.0)])  # item with no order
    wh.commit()
    wh.close()
    with pytest.raises(StopRun, match="test 3"):
        staging.run_staging(wh_path, raw_dir, run_id="bad")
    assert table_checksum(wh_path, "stg_orders") == before       # the good tables are untouched
    assert query(wh_path, "SELECT status FROM dq_results WHERE run_id='bad' AND test_no=3") == [("failed",)]


# ---------------------------------------------------------------- joins

def test_join_that_would_multiply_rows_stops_the_run():
    left = pd.DataFrame({"zip": ["a", "b"]})
    right = pd.DataFrame({"zip": ["a", "a", "b"], "lat": [1, 2, 3]})   # two rows for 'a': would give 3 rows
    with pytest.raises(staging.JoinError, match="m:1"):
        staging.checked_join(left, right, "zip", "m:1", "test join", [])


def test_good_join_keeps_row_count_and_is_logged():
    left = pd.DataFrame({"zip": ["a", "b", "c"]})
    right = pd.DataFrame({"zip": ["a", "b"], "lat": [1, 2]})
    join_log = []
    out = staging.checked_join(left, right, "zip", "m:1", "test join", join_log)
    assert len(out) == 3 and out["lat"].isna().sum() == 1             # unmatched rows are kept (left join)
    assert join_log == [{"join_name": "test join", "relationship": "m:1", "left_rows": 3,
                         "right_rows": 2, "rows_after": 3}]


def test_one_to_one_join_rejects_a_repeated_key_on_the_left():
    left = pd.DataFrame({"customer_id": ["c1", "c1"]})
    right = pd.DataFrame({"customer_id": ["c1"], "state": ["SP"]})
    with pytest.raises(staging.JoinError, match="1:1"):
        staging.checked_join(left, right, "customer_id", "1:1", "orders -> customers", [])


def test_reconciliation_fails_when_rows_go_missing(world):
    wh_path, raw_dir = world
    staging.run_staging(wh_path, raw_dir)
    wh = sqlite3.connect(wh_path)
    wh.execute("DELETE FROM stg_orders WHERE order_id='o2'")          # simulate a silent drop
    q = staging.Quarantine("r", "t")
    with pytest.raises(staging.ReconciliationError, match="orders"):
        staging.reconcile(wh, {"orders": 4, "order_items": 5, "order_reviews": 4, "customers": 4, "sellers": 2,
                               "products": 3, "geolocation": 4, "holidays": 5, "fx_rates": 3}, q)
    wh.close()
