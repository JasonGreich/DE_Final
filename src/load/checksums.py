"""Row counts and order-independent checksums of warehouse tables (used to prove idempotency).

The checksum of a table is the sum of a hash of every row, so the order of the rows does not matter.
"""
import hashlib
import sqlite3

MART_TABLES = ["dim_date", "dim_product", "dim_seller", "dim_customer", "fact_order_item"]
STG_TABLES = ["stg_orders", "stg_order_items", "stg_order_reviews", "stg_customers", "stg_sellers",
              "stg_products", "stg_geolocation", "stg_holidays", "stg_fx_rates"]


def row_hash(row):
    """A 64-bit number from the text of one row."""
    text = "|".join("" if value is None else repr(value) for value in row)
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:15], 16)


# These columns change on every run by design, so they are left out of the checksum.
RUN_COLUMNS = ("run_id", "quarantined_at")


def table_checksum(conn, table):
    """(row count, sum of row hashes modulo 2**61) for one table, ignoring the per-run columns."""
    count = 0
    total = 0
    cursor = conn.execute(f"SELECT * FROM {table}")
    keep = [i for i, col in enumerate(cursor.description) if col[0] not in RUN_COLUMNS]
    for row in cursor:
        count += 1
        total += row_hash([row[i] for i in keep])
    return count, total % (2 ** 61)


def checksums(db_path, tables):
    """{table: (rows, checksum)} for the given tables."""
    conn = sqlite3.connect(str(db_path))
    try:
        return {table: table_checksum(conn, table) for table in tables}
    finally:
        conn.close()
