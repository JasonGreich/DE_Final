"""Stage 0 tests: the store database has the nine tables and valid relationships.

The tests build a tiny synthetic seed (a few invented rows, not Olist data) so
they run in a second and do not need the real dataset.
"""
import csv
import sqlite3

import pytest

from src.extract import seed_store

# one invented row per table, keyed by table name, in column order
SAMPLE_ROWS = {
    "customers": ["c1", "u1", "09790", "sao paulo", "SP"],
    "sellers": ["s1", "01151", "sao paulo", "SP"],
    "products": ["p1", "beleza_saude", "40", "287", "1", "225", "16", "10", "14"],
    "product_category_name_translation": ["beleza_saude", "health_beauty"],
    "geolocation": ["09790", "-23.5", "-46.6", "sao paulo", "SP"],
    "orders": ["o1", "c1", "delivered", "2017-01-01 10:00:00", "2017-01-01 10:10:00",
               "2017-01-02 09:00:00", "", "2017-01-20 00:00:00"],
    "order_items": ["o1", "1", "p1", "s1", "2017-01-05 10:00:00", "10.5", "2.0"],
    "order_reviews": ["r1", "o1", "5", "", "", "2017-01-21 00:00:00", "2017-01-22 10:00:00"],
    "order_payments": ["o1", "1", "credit_card", "1", "12.5"],
}


def write_seed(seed_dir, rows=SAMPLE_ROWS):
    """Write one CSV per table with the real headers and the sample row."""
    for name, (csv_name, columns, _) in seed_store.TABLES.items():
        with open(seed_dir / csv_name, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow([c for c, _ in columns])
            writer.writerow(rows[name])


@pytest.fixture()
def built_db(tmp_path):
    """Build a database from the synthetic seed and return its path."""
    write_seed(tmp_path)
    db_path = tmp_path / "olist.db"
    seed_store.build_database(tmp_path, db_path)
    return db_path


def test_nine_tables_exist(built_db):
    conn = sqlite3.connect(built_db)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert names == set(seed_store.TABLES)
    assert len(names) == 9


def test_every_order_item_has_an_order(built_db):
    conn = sqlite3.connect(built_db)
    orphans = conn.execute(
        "SELECT COUNT(*) FROM order_items oi "
        "LEFT JOIN orders o ON o.order_id = oi.order_id WHERE o.order_id IS NULL"
    ).fetchone()[0]
    assert orphans == 0


def test_values_are_kept_and_empty_becomes_null(built_db):
    conn = sqlite3.connect(built_db)
    # leading zero survives because the zip prefix column is TEXT
    assert conn.execute("SELECT customer_zip_code_prefix FROM customers").fetchone()[0] == "09790"
    # an empty delivery date is stored as NULL
    assert conn.execute("SELECT order_delivered_customer_date FROM orders").fetchone()[0] is None


def test_rebuild_gives_same_counts(tmp_path):
    write_seed(tmp_path)
    db_path = tmp_path / "olist.db"
    first = seed_store.build_database(tmp_path, db_path)
    second = seed_store.build_database(tmp_path, db_path)  # drops and recreates
    assert first == second


def test_foreign_key_violation_is_rejected(tmp_path):
    rows = dict(SAMPLE_ROWS)
    rows["order_items"] = ["missing_order", "1", "p1", "s1", "2017-01-05 10:00:00", "1", "1"]
    write_seed(tmp_path, rows)
    with pytest.raises(sqlite3.IntegrityError):
        seed_store.build_database(tmp_path, tmp_path / "olist.db")


def test_wrong_header_is_rejected(tmp_path):
    write_seed(tmp_path)
    (tmp_path / "olist_sellers_dataset.csv").write_text("wrong,header\n1,2\n")
    with pytest.raises(ValueError):
        seed_store.build_database(tmp_path, tmp_path / "olist.db")
