"""Stage 2 tests: watermark logic (pure) and incremental extraction on a tiny store.

The tiny store has 5 invented orders. Purchase dates are chosen so that the
60-day window after the first run covers orders o3, o4, o5 but not o1, o2:
    o1 2017-01-10   o2 2017-03-01   o3 2017-05-01   o4 2017-06-20   o5 2017-06-25
Watermark after a full run = o5's timestamp, so the window starts 2017-04-26.
"""
import csv
import sqlite3

import pytest

from src.extract import manifest, olist, watermark
from src.extract.seed_store import TABLES, create_table_sql

# ---------------------------------------------------------------- pure functions


def test_overlap_start_is_60_days_before_watermark():
    assert watermark.overlap_start("2017-06-25 10:00:00") == "2017-04-26 10:00:00"


def test_overlap_start_without_watermark_means_extract_everything():
    assert watermark.overlap_start(None) is None


def test_overlap_start_crosses_a_year_boundary():
    assert watermark.overlap_start("2018-01-15 08:30:00") == "2017-11-16 08:30:00"


def test_watermark_never_moves_backwards():
    assert watermark.next_watermark("2017-06-25 10:00:00", "2017-03-01 10:00:00") == "2017-06-25 10:00:00"
    assert watermark.next_watermark("2017-03-01 10:00:00", "2017-06-25 10:00:00") == "2017-06-25 10:00:00"
    assert watermark.next_watermark(None, "2017-03-01 10:00:00") == "2017-03-01 10:00:00"
    assert watermark.next_watermark("2017-03-01 10:00:00", None) == "2017-03-01 10:00:00"
    assert watermark.next_watermark(None, None) is None


def test_as_of_must_be_a_real_date():
    assert watermark.parse_as_of("2017-06-30") == "2017-06-30"
    with pytest.raises(ValueError):
        watermark.parse_as_of("30/06/2017")
    with pytest.raises(ValueError):
        watermark.parse_as_of("2017-02-30")


# ---------------------------------------------------------------- tiny store fixture

ORDERS = [  # order_id, status, purchase, delivered, estimated
    ("o1", "delivered", "2017-01-10 09:00:00", "2017-01-20 12:00:00", "2017-01-25 00:00:00"),
    ("o2", "delivered", "2017-03-01 10:00:00", "2017-03-09 12:00:00", "2017-03-15 00:00:00"),
    ("o3", "shipped", "2017-05-01 11:00:00", None, "2017-05-20 00:00:00"),
    ("o4", "delivered", "2017-06-20 12:00:00", "2017-06-28 12:00:00", "2017-07-05 00:00:00"),
    ("o5", "processing", "2017-06-25 13:00:00", None, "2017-07-10 00:00:00"),
]


@pytest.fixture()
def store(tmp_path):
    """Build a small olist.db (same schema as the real one) and return its path."""
    path = tmp_path / "olist.db"
    conn = sqlite3.connect(path)
    for name, (_, columns, constraints) in TABLES.items():
        conn.execute(create_table_sql(name, columns, constraints))
    conn.execute("INSERT INTO customers VALUES ('c1','u1','01000','sao paulo','SP')")
    conn.execute("INSERT INTO sellers VALUES ('s1','02000','sao paulo','SP')")
    conn.execute("INSERT INTO products VALUES ('p1','beleza_saude',40,287,1,225,16,10,14)")
    conn.execute("INSERT INTO product_category_name_translation VALUES ('beleza_saude','health_beauty')")
    conn.execute("INSERT INTO geolocation VALUES ('01000',-23.5,-46.6,'sao paulo','SP')")
    for oid, status, purchase, delivered, estimated in ORDERS:
        conn.execute("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?)",
                     (oid, "c1", status, purchase, purchase, purchase, delivered, estimated))
        conn.execute("INSERT INTO order_items VALUES (?,1,'p1','s1',?,10.0,2.0)", (oid, purchase))
        conn.execute("INSERT INTO order_reviews VALUES (?,?,5,NULL,NULL,?,?)",
                     ("r_" + oid, oid, purchase, purchase))
    # o4 has a second item, to prove children are replaced as a group
    conn.execute("INSERT INTO order_items VALUES ('o4',2,'p1','s1','2017-06-20 12:00:00',5.0,1.0)")
    conn.commit()
    conn.close()
    return path


def extract(store, tmp_path, **kwargs):
    """Run one extraction into temp folders and return the summary."""
    return olist.run_extract(store_path=store, warehouse_path=tmp_path / "wh.db",
                             raw_dir=tmp_path / "raw", **kwargs)


def wh_query(tmp_path, sql):
    conn = sqlite3.connect(tmp_path / "wh.db")
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


# ---------------------------------------------------------------- integration tests

def test_first_run_extracts_everything(store, tmp_path):
    summary = extract(store, tmp_path)
    assert summary["window_start"] is None
    assert summary["counts"]["orders"] == 5
    assert summary["counts"]["order_items"] == 6
    assert summary["watermark_after"] == "2017-06-25 13:00:00"


def test_second_run_rereads_window_but_does_not_duplicate(store, tmp_path):
    extract(store, tmp_path)
    second = extract(store, tmp_path)
    assert second["window_start"] == "2017-04-26 13:00:00"
    assert second["counts"]["orders"] == 3  # o3, o4, o5 are inside the window
    assert wh_query(tmp_path, "SELECT COUNT(*) FROM raw_orders") == [(5,)]
    assert wh_query(tmp_path, "SELECT COUNT(*) FROM raw_order_items") == [(6,)]
    assert wh_query(tmp_path, "SELECT COUNT(*) FROM raw_order_reviews") == [(5,)]
    assert wh_query(tmp_path, "SELECT COUNT(*) FROM raw_customers") == [(1,)]


def test_changed_order_is_updated_not_appended(store, tmp_path):
    extract(store, tmp_path)
    conn = sqlite3.connect(store)  # the store changes: o3 gets delivered
    conn.execute("UPDATE orders SET order_status='delivered', "
                 "order_delivered_customer_date='2017-05-15 10:00:00' WHERE order_id='o3'")
    conn.commit()
    conn.close()
    extract(store, tmp_path)
    assert wh_query(tmp_path, "SELECT COUNT(*) FROM raw_orders") == [(5,)]  # row count unchanged
    assert wh_query(tmp_path, "SELECT order_status, order_delivered_customer_date "
                              "FROM raw_orders WHERE order_id='o3'") == [("delivered", "2017-05-15 10:00:00")]


def test_order_older_than_the_window_is_not_refreshed(store, tmp_path):
    # Honest limit of the 60-day rule: o1 is outside the window, so a change to it is not seen.
    extract(store, tmp_path)
    conn = sqlite3.connect(store)
    conn.execute("UPDATE orders SET order_status='canceled' WHERE order_id='o1'")
    conn.commit()
    conn.close()
    extract(store, tmp_path)
    assert wh_query(tmp_path, "SELECT order_status FROM raw_orders WHERE order_id='o1'") == [("delivered",)]
    extract(store, tmp_path, full=True)  # --full picks it up
    assert wh_query(tmp_path, "SELECT order_status FROM raw_orders WHERE order_id='o1'") == [("canceled",)]


def test_item_removed_from_a_window_order_disappears(store, tmp_path):
    extract(store, tmp_path)
    conn = sqlite3.connect(store)
    conn.execute("DELETE FROM order_items WHERE order_id='o4' AND order_item_id=2")
    conn.commit()
    conn.close()
    extract(store, tmp_path)
    assert wh_query(tmp_path, "SELECT COUNT(*) FROM raw_order_items WHERE order_id='o4'") == [(1,)]


def test_as_of_run_then_catch_up(store, tmp_path):
    first = extract(store, tmp_path, as_of="2017-03-31")  # demo run 1: orders up to March
    assert first["counts"]["orders"] == 2
    assert first["watermark_after"] == "2017-03-01 10:00:00"
    assert wh_query(tmp_path, "SELECT COUNT(*) FROM raw_orders") == [(2,)]
    second = extract(store, tmp_path)  # demo run 2: the full range
    assert second["window_start"] == "2016-12-31 10:00:00"
    assert wh_query(tmp_path, "SELECT COUNT(*) FROM raw_orders") == [(5,)]  # no duplicates
    assert second["watermark_after"] == "2017-06-25 13:00:00"


def test_full_run_gives_same_tables_as_incremental(store, tmp_path):
    extract(store, tmp_path)
    extract(store, tmp_path)
    before = wh_query(tmp_path, "SELECT * FROM raw_orders ORDER BY order_id")
    extract(store, tmp_path, full=True)
    assert wh_query(tmp_path, "SELECT * FROM raw_orders ORDER BY order_id") == before


def test_raw_csv_matches_the_store_and_manifest_lines_are_written(store, tmp_path):
    extract(store, tmp_path)
    lines = manifest.read_manifest(tmp_path / "raw")
    assert len(lines) == 8  # orders, items, reviews and the five dimensions
    by_source = {line["source"]: line for line in lines}
    orders_line = by_source["olist.orders"]
    assert orders_line["records"] == 5
    assert orders_line["watermark_before"] is None
    assert "SELECT * FROM orders" in orders_line["url_or_query"]
    csv_files = sorted((tmp_path / "raw").glob("olist_orders_*.csv"))
    assert len(csv_files) == 1
    with open(csv_files[0], newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    assert len(rows) == 1 + 5  # header + 5 orders
    assert rows[0][0] == "order_id"
    o3 = [r for r in rows if r[0] == "o3"][0]
    assert o3[rows[0].index("order_delivered_customer_date")] == ""  # NULL is an empty field


def test_each_run_adds_new_timestamped_files(store, tmp_path):
    extract(store, tmp_path)
    extract(store, tmp_path)
    assert len(list((tmp_path / "raw").glob("olist_orders_*.csv"))) == 2
    assert len(manifest.read_manifest(tmp_path / "raw")) == 16
