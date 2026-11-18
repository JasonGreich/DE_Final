"""Incremental extraction of the Olist tables from the store database (olist.db).

What one run does:
  1. Work out which orders to read:
       first run or --full : every order (up to --as-of, if given)
       later runs          : orders purchased on or after (watermark - 60 days)
  2. Read those orders, plus their items and reviews, with SQL.
     Also read the dimension tables (customers, sellers, products, geolocation,
     category translation) in full.
  3. Save every extract untouched as a timestamped CSV in data/raw/
     and add a line to data/raw/manifest.jsonl.
  4. Copy the rows into the warehouse tables raw_orders, raw_order_items, ...
     An order that is already there is REPLACED by key, never added twice.
  5. Move the watermark forward (saved in the extract_state table).

Why CSV for the raw files: no extra library, you can open it in a text editor,
and every value is kept as the store gave it. (Parquet would keep types and be
smaller, but needs pyarrow and cannot be read by eye.) A NULL is written as an
empty field.

Run:  python -m src.extract.olist [--full] [--as-of YYYY-MM-DD]
"""
import argparse
import csv
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src.extract import manifest, watermark
from src.extract.seed_store import TABLES, create_table_sql

ROOT = Path(__file__).resolve().parents[2]
STORE_DB = ROOT / "data" / "store" / "olist.db"
WAREHOUSE_DB = ROOT / "data" / "warehouse" / "warehouse.db"
RAW_DIR = ROOT / "data" / "raw"

SOURCE = "olist_orders"  # name of the watermark row in extract_state
BATCH = 10_000           # rows copied at a time, so big tables do not fill memory

# Tables that follow the orders in the window (selected by order_id)
ORDER_CHILDREN = ["order_items", "order_reviews"]
# Tables extracted in full on every run
DIMENSIONS = ["customers", "sellers", "products", "product_category_name_translation", "geolocation"]
# Sort order for each table, so the same data always gives the same file
ORDER_BY = {
    "orders": "order_id", "order_items": "order_id, order_item_id",
    "order_reviews": "order_id, review_id", "customers": "customer_id",
    "sellers": "seller_id", "products": "product_id",
    "product_category_name_translation": "product_category_name",
    "geolocation": "rowid",
}


def utc_now():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- warehouse side

def ensure_raw_tables(wh):
    """Create raw_<table> in the warehouse, same columns as the store, with primary keys.

    These hold the latest copy of every extracted row, one row per key. Foreign
    keys are left out on purpose: checking relationships is a data test (stage 3).
    """
    for name, (_, columns, constraints) in TABLES.items():
        keys = [c for c in constraints if c.startswith("PRIMARY KEY")]
        sql = create_table_sql(f"raw_{name}", columns, keys)
        wh.execute(sql.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS", 1))
    watermark.ensure_state_table(wh)


# ---------------------------------------------------------------- reading from the store

def orders_filter(start, as_of):
    """WHERE clause and parameters that pick the orders to extract."""
    clauses, params = [], []
    if start is not None:
        clauses.append("order_purchase_timestamp >= ?")
        params.append(start)
    if as_of is not None:
        clauses.append("date(order_purchase_timestamp) <= ?")
        params.append(as_of)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    return where, params


def select_sql(table, start, as_of):
    """SQL and parameters to read one table for this run."""
    order_by = ORDER_BY[table]
    if table == "orders":
        where, params = orders_filter(start, as_of)
        return f"SELECT * FROM orders{where} ORDER BY {order_by}", params
    if table in ORDER_CHILDREN:
        where, params = orders_filter(start, as_of)
        sub = f"SELECT order_id FROM orders{where}"
        return f"SELECT * FROM {table} WHERE order_id IN ({sub}) ORDER BY {order_by}", params
    return f"SELECT * FROM {table} ORDER BY {order_by}", []  # dimensions: everything


def readable_query(sql, params):
    """SQL with its parameter values filled in, for the manifest."""
    for value in params:
        sql = sql.replace("?", repr(value), 1)
    return sql


def copy_table(store, wh, table, start, as_of, csv_path, replace_orders=False):
    """Stream one table: write the raw CSV and load the rows into raw_<table>.

    Returns (rows_copied, newest_purchase_timestamp_or_None, sql_text).
    The caller owns the warehouse transaction.
    """
    sql, params = select_sql(table, start, as_of)
    cursor = store.execute(sql, params)
    columns = [d[0] for d in cursor.description]
    marks = ",".join("?" * len(columns))
    verb = "INSERT OR REPLACE" if replace_orders else "INSERT"
    insert = f"{verb} INTO raw_{table} ({','.join(columns)}) VALUES ({marks})"
    purchase_pos = columns.index("order_purchase_timestamp") if table == "orders" else None
    newest, total = None, 0
    with open(csv_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        while True:
            rows = cursor.fetchmany(BATCH)
            if not rows:
                break
            writer.writerows(rows)          # NULL becomes an empty field
            wh.executemany(insert, rows)
            total += len(rows)
            if purchase_pos is not None:
                newest = max([r[purchase_pos] for r in rows] + ([newest] if newest else []))
    return total, newest, readable_query(sql, params)


# ---------------------------------------------------------------- the run

def run_extract(store_path=STORE_DB, warehouse_path=WAREHOUSE_DB, raw_dir=RAW_DIR,
                full=False, as_of=None, now=None):
    """Run one extraction. Returns a summary dict (rows per table, watermarks)."""
    now = now or utc_now()
    stamp = now.strftime("%Y%m%dT%H%M%S%fZ")  # in the file names, so files never clash
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    Path(warehouse_path).parent.mkdir(parents=True, exist_ok=True)

    # Open the store read-only: extraction must never change the source system.
    # as_uri() percent-encodes spaces in the path (our folder is called "DE Project").
    store = sqlite3.connect(f"{Path(store_path).resolve().as_uri()}?mode=ro", uri=True)
    wh = sqlite3.connect(warehouse_path)
    try:
        ensure_raw_tables(wh)
        old_mark = watermark.get_watermark(wh, SOURCE)
        # --full ignores the saved watermark and re-reads everything
        start = None if full else watermark.overlap_start(old_mark)
        print(f"Olist extract: watermark={old_mark}, window start={start}, "
              f"as_of={as_of}, full={full}")

        counts, entries = {}, []
        newest_seen = None
        wh.execute("BEGIN")
        if full:  # a full run rebuilds the order tables from scratch
            for table in ["orders"] + ORDER_CHILDREN:
                wh.execute(f"DELETE FROM raw_{table}")
        for table in ["orders"] + ORDER_CHILDREN + DIMENSIONS:
            if table in ORDER_CHILDREN and not full:
                # Items and reviews of an order we re-read are replaced as a group,
                # so an item removed in the store disappears here too.
                where, params = orders_filter(start, as_of)
                ids = [r[0] for r in store.execute(f"SELECT order_id FROM orders{where}", params)]
                wh.executemany(f"DELETE FROM raw_{table} WHERE order_id = ?", [(i,) for i in ids])
            if table in DIMENSIONS:
                wh.execute(f"DELETE FROM raw_{table}")  # dimensions are replaced whole
            csv_path = raw_dir / f"olist_{table}_{stamp}.csv"
            n, newest, query = copy_table(store, wh, table, start, as_of, csv_path,
                                          replace_orders=(table == "orders"))
            counts[table] = n
            if table == "orders":
                newest_seen = newest
            entries.append((table, query, n, csv_path))

        new_mark = watermark.next_watermark(None if full else old_mark, newest_seen)
        if new_mark is not None:
            watermark.save_watermark(wh, SOURCE, new_mark, now.isoformat(timespec="seconds"))
        wh.commit()
    except BaseException:
        wh.rollback()  # a failed run changes nothing
        raise
    finally:
        store.close()
        wh.close()

    # Manifest lines are written only after the data is safely committed.
    for table, query, n, csv_path in entries:
        manifest.append_manifest(
            raw_dir, f"olist.{table}", query, now.isoformat(timespec="seconds"), n, csv_path,
            watermark_before=old_mark, window_start=start, as_of=as_of, full=full)
    summary = {"counts": counts, "watermark_before": old_mark, "window_start": start,
               "watermark_after": new_mark}
    for table, n in counts.items():
        print(f"  {table:38s} {n:>10,} rows extracted")
    print(f"  watermark: {old_mark} -> {new_mark}")
    return summary


def main():
    parser = argparse.ArgumentParser(description="Incremental Olist extraction")
    parser.add_argument("--full", action="store_true", help="ignore the watermark, extract everything")
    parser.add_argument("--as-of", help="only extract orders purchased on or before this date (YYYY-MM-DD)")
    args = parser.parse_args()
    as_of = watermark.parse_as_of(args.as_of) if args.as_of else None
    run_extract(full=args.full, as_of=as_of)


if __name__ == "__main__":
    main()
