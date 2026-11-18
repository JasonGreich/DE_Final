"""Build the store's operational database (data/store/olist.db) from the Olist CSVs.

This plays the role of the online store's own database. We do NOT clean anything
here: values are copied exactly as they are in the CSVs. The only translation is
that an empty CSV field becomes NULL (a missing value), and numeric columns get
SQLite numeric type affinity.

Where the CSVs come from, in order:
  1. data/seed/ already has all nine CSVs (or a zip with them) -> use them.
  2. Otherwise download from Kaggle using KAGGLE_USERNAME / KAGGLE_KEY from .env.
  3. If that fails, print a clear message and stop.
Use --download to force step 2 even when data/seed/ is complete.

Run:  python -m src.extract.seed_store
"""
import argparse
import csv
import sqlite3
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SEED_DIR = ROOT / "data" / "seed"
DB_PATH = ROOT / "data" / "store" / "olist.db"
KAGGLE_DATASET = "olistbr/brazilian-ecommerce"

# table name -> (csv file name, list of (column, SQLite type), extra constraints)
# Column names are exactly the CSV headers (including the Olist typos "lenght").
# Zip prefixes are TEXT on purpose: "09790" must keep its leading zero.
# Parents come first so foreign keys can be checked while loading.
TABLES = {
    "customers": (
        "olist_customers_dataset.csv",
        [("customer_id", "TEXT"), ("customer_unique_id", "TEXT"),
         ("customer_zip_code_prefix", "TEXT"), ("customer_city", "TEXT"),
         ("customer_state", "TEXT")],
        ["PRIMARY KEY (customer_id)"],
    ),
    "sellers": (
        "olist_sellers_dataset.csv",
        [("seller_id", "TEXT"), ("seller_zip_code_prefix", "TEXT"),
         ("seller_city", "TEXT"), ("seller_state", "TEXT")],
        ["PRIMARY KEY (seller_id)"],
    ),
    "products": (
        "olist_products_dataset.csv",
        [("product_id", "TEXT"), ("product_category_name", "TEXT"),
         ("product_name_lenght", "INTEGER"), ("product_description_lenght", "INTEGER"),
         ("product_photos_qty", "INTEGER"), ("product_weight_g", "INTEGER"),
         ("product_length_cm", "INTEGER"), ("product_height_cm", "INTEGER"),
         ("product_width_cm", "INTEGER")],
        ["PRIMARY KEY (product_id)"],
    ),
    "product_category_name_translation": (
        "product_category_name_translation.csv",
        [("product_category_name", "TEXT"), ("product_category_name_english", "TEXT")],
        ["PRIMARY KEY (product_category_name)"],
    ),
    "geolocation": (
        "olist_geolocation_dataset.csv",
        # No natural key: the same zip prefix appears on many rows.
        [("geolocation_zip_code_prefix", "TEXT"), ("geolocation_lat", "REAL"),
         ("geolocation_lng", "REAL"), ("geolocation_city", "TEXT"),
         ("geolocation_state", "TEXT")],
        [],
    ),
    "orders": (
        "olist_orders_dataset.csv",
        [("order_id", "TEXT"), ("customer_id", "TEXT"), ("order_status", "TEXT"),
         ("order_purchase_timestamp", "TEXT"), ("order_approved_at", "TEXT"),
         ("order_delivered_carrier_date", "TEXT"),
         ("order_delivered_customer_date", "TEXT"),
         ("order_estimated_delivery_date", "TEXT")],
        ["PRIMARY KEY (order_id)",
         "FOREIGN KEY (customer_id) REFERENCES customers (customer_id)"],
    ),
    "order_items": (
        "olist_order_items_dataset.csv",
        [("order_id", "TEXT"), ("order_item_id", "INTEGER"), ("product_id", "TEXT"),
         ("seller_id", "TEXT"), ("shipping_limit_date", "TEXT"),
         ("price", "REAL"), ("freight_value", "REAL")],
        ["PRIMARY KEY (order_id, order_item_id)",
         "FOREIGN KEY (order_id) REFERENCES orders (order_id)",
         "FOREIGN KEY (product_id) REFERENCES products (product_id)",
         "FOREIGN KEY (seller_id) REFERENCES sellers (seller_id)"],
    ),
    "order_reviews": (
        "olist_order_reviews_dataset.csv",
        # review_id alone is NOT unique in Olist (the same id can sit on several
        # orders), so the key is the pair (review_id, order_id).
        [("review_id", "TEXT"), ("order_id", "TEXT"), ("review_score", "INTEGER"),
         ("review_comment_title", "TEXT"), ("review_comment_message", "TEXT"),
         ("review_creation_date", "TEXT"), ("review_answer_timestamp", "TEXT")],
        ["PRIMARY KEY (review_id, order_id)",
         "FOREIGN KEY (order_id) REFERENCES orders (order_id)"],
    ),
    "order_payments": (
        "olist_order_payments_dataset.csv",
        [("order_id", "TEXT"), ("payment_sequential", "INTEGER"),
         ("payment_type", "TEXT"), ("payment_installments", "INTEGER"),
         ("payment_value", "REAL")],
        ["PRIMARY KEY (order_id, payment_sequential)",
         "FOREIGN KEY (order_id) REFERENCES orders (order_id)"],
    ),
}


def create_table_sql(name, columns, constraints):
    """Build the CREATE TABLE statement for one table."""
    parts = [f"{col} {typ}" for col, typ in columns] + constraints
    return f"CREATE TABLE {name} (\n    " + ",\n    ".join(parts) + "\n)"


def unpack_zips(seed_dir):
    """If a zip is in the seed folder, extract the CSVs we expect from it."""
    wanted = {spec[0] for spec in TABLES.values()}
    for zpath in sorted(seed_dir.glob("*.zip")):
        with zipfile.ZipFile(zpath) as zf:
            for member in zf.namelist():
                base = Path(member).name  # ignore any folders inside the zip
                if base in wanted and not (seed_dir / base).exists():
                    # write by our own safe name, never by the path in the zip
                    (seed_dir / base).write_bytes(zf.read(member))
                    print(f"  unpacked {base} from {zpath.name}")


def missing_csvs(seed_dir):
    """Names of expected CSV files that are not in the seed folder."""
    return [spec[0] for spec in TABLES.values() if not (seed_dir / spec[0]).exists()]


def download_from_kaggle(seed_dir):
    """Download and unzip the dataset into seed_dir. Returns True on success."""
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")  # puts KAGGLE_USERNAME / KAGGLE_KEY in the environment
    try:
        # Imported here because the kaggle package tries to authenticate on import.
        from kaggle.api.kaggle_api_extended import KaggleApi
        api = KaggleApi()
        api.authenticate()
        seed_dir.mkdir(parents=True, exist_ok=True)
        print(f"Downloading {KAGGLE_DATASET} from Kaggle ...")
        api.dataset_download_files(KAGGLE_DATASET, path=str(seed_dir), unzip=False)
        return True
    except BaseException as err:  # the kaggle package can even call sys.exit
        print(f"Kaggle download failed: {type(err).__name__}: {err}")
        return False


def read_csv_rows(csv_path, expected_columns):
    """Yield rows as tuples; empty fields become None. Checks the header first."""
    with open(csv_path, newline="", encoding="utf-8-sig") as handle:  # utf-8-sig drops a BOM if present
        reader = csv.reader(handle)
        header = next(reader)
        if header != expected_columns:
            raise ValueError(
                f"{csv_path.name}: header {header} does not match expected {expected_columns}")
        for row in reader:
            yield tuple(value if value != "" else None for value in row)


def build_database(seed_dir=SEED_DIR, db_path=DB_PATH):
    """Drop and recreate olist.db from the CSVs. Returns {table: row_count}."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()  # rebuild from scratch so a re-run gives the same database
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")  # reject rows that break a foreign key
    counts = {}
    try:
        for name, (csv_name, columns, constraints) in TABLES.items():
            conn.execute(create_table_sql(name, columns, constraints))
            col_names = [c for c, _ in columns]
            marks = ",".join("?" * len(col_names))
            sql = f"INSERT INTO {name} ({','.join(col_names)}) VALUES ({marks})"
            conn.executemany(sql, read_csv_rows(Path(seed_dir) / csv_name, col_names))
            counts[name] = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        conn.commit()
    finally:
        conn.close()
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--download", action="store_true",
                        help="download from Kaggle even if data/seed/ is complete")
    args = parser.parse_args()

    unpack_zips(SEED_DIR)
    if args.download or missing_csvs(SEED_DIR):
        download_from_kaggle(SEED_DIR)
        unpack_zips(SEED_DIR)
    missing = missing_csvs(SEED_DIR)
    if missing:
        print("\nCould not find all nine Olist CSVs.")
        print(f"Missing: {', '.join(missing)}")
        print(f"Please download the dataset from https://www.kaggle.com/datasets/{KAGGLE_DATASET}")
        print(f"and put the zip, or the nine CSVs, in: {SEED_DIR}")
        print("Then run this command again.")
        sys.exit(1)

    counts = build_database()
    print(f"\nBuilt {DB_PATH}")
    for name, n in counts.items():
        print(f"  {name:38s} {n:>10,}")


if __name__ == "__main__":
    main()
