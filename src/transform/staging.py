"""Build the stg_ tables in the warehouse (stage 3).

Input : the raw_ tables written by the extractors, plus the latest holiday and exchange-rate files.
Output: stg_orders, stg_order_items, stg_order_reviews, stg_customers, stg_sellers, stg_products,
        stg_geolocation, stg_holidays, stg_fx_rates, the quarantine_ tables, dq_results,
        join_log.

Order of one run:
  1. load raw data                    5. build the stg_ tables (every join is checked)
  2. data tests 1-13 and 15           6. test 14 (late rate matches a raw recomputation)
  3. quarantine failing rows          7. write everything in ONE transaction
  4. drop quarantined rows            8. reconcile: extracted = staged + quarantined

stg_ tables are rebuilt from scratch each run (DROP + CREATE), so running twice gives the same tables.

Run:  python -m src.transform.staging [--full]
"""
import argparse
import json
import logging
import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from src.extract.olist import RAW_DIR, WAREHOUSE_DB
from src.quality import tests as dq
from src.quality.quarantine import Quarantine
from src.quality.tests import StopRun
from src.transform import geo, rules

log = logging.getLogger("pipeline")

YEARS = (2016, 2017, 2018)


class JoinError(StopRun):
    """A join changed the row count or broke its expected relationship."""


class ReconciliationError(StopRun):
    """Rows extracted do not equal rows staged plus rows quarantined."""


# ---------------------------------------------------------------- small helpers

def clean_text(series, lower=False, upper=False):
    """Trim spaces, turn empty strings into missing, optionally change case."""
    out = series.astype("string").str.strip()
    out = out.mask(out == "", pd.NA)
    if lower:
        out = out.str.lower()
    if upper:
        out = out.str.upper()
    return out


def zip5(series):
    """Zip prefix as 5 characters with leading zeros ('1037' -> '01037')."""
    return clean_text(series).str.zfill(5)


def as_list(series):
    """Series as a plain list with None for missing values (what rules.py expects)."""
    return [None if pd.isna(value) else value for value in series]


def int_column(values):
    """A nullable integer column (whole numbers plus missing values)."""
    return pd.array(values, dtype="Int64")


def checked_join(left, right, on, validate, name, join_log):
    """Left join that must not change the row count, and must match the expected relationship.

    validate: '1:1' (each key at most once on BOTH sides) or 'm:1' (right side unique per key).
    Logs rows before and after, and stops the run if the assumption is wrong.
    """
    before = len(left)
    try:
        joined = left.merge(right, on=on, how="left", validate=validate)
    except pd.errors.MergeError as err:
        raise JoinError(f"join '{name}' broke its {validate} assumption: {err}") from err
    if len(joined) != before:
        raise JoinError(f"join '{name}' changed the row count from {before} to {len(joined)}")
    join_log.append({"join_name": name, "relationship": validate, "left_rows": before,
                     "right_rows": len(right), "rows_after": len(joined)})
    log.info("join %-38s %s  left=%s right=%s after=%s", name, validate, before, len(right), len(joined))
    return joined


# ---------------------------------------------------------------- loading the raw layer

READ_SQL = {  # frame name -> SQL. Ordered, so the same data always gives the same frames.
    "orders": "SELECT * FROM raw_orders ORDER BY order_id",
    "order_items": "SELECT * FROM raw_order_items ORDER BY order_id, order_item_id",
    "order_reviews": "SELECT * FROM raw_order_reviews ORDER BY order_id, review_id",
    "customers": "SELECT * FROM raw_customers ORDER BY customer_id",
    "sellers": "SELECT * FROM raw_sellers ORDER BY seller_id",
    "products": "SELECT * FROM raw_products ORDER BY product_id",
    "translation": "SELECT * FROM raw_product_category_name_translation ORDER BY 1",
    # geo_row_id = row number, because geolocation has no key and identical rows exist
    "geolocation": "SELECT rowid AS geo_row_id, * FROM raw_geolocation ORDER BY rowid",
}


def latest_file(raw_dir, pattern):
    """Newest file matching the pattern (file names contain a sortable UTC timestamp)."""
    files = sorted(Path(raw_dir).glob(pattern))
    if not files:
        raise StopRun(f"no raw file matching {pattern} in {raw_dir}; run the extractor first")
    return files[-1]


def load_holidays(raw_dir):
    """Holidays from the newest Nager file of each year, one row per holiday."""
    rows = []
    for year in YEARS:
        path = latest_file(raw_dir, f"nager_holidays_{year}_*.json")
        for rec in json.loads(path.read_text(encoding="utf-8")):
            rows.append({"holiday_date": rec["date"], "name": rec["name"], "local_name": rec["localName"],
                         "is_global": int(bool(rec["global"])), "types": "+".join(rec["types"]),
                         "counties": json.dumps(rec["counties"]) if rec["counties"] else None})
    return pd.DataFrame(rows)


def load_fx_rates(raw_dir):
    """BRL to USD rates from the newest Frankfurter file."""
    path = latest_file(raw_dir, "frankfurter_brl_usd_*.json")
    records = json.loads(path.read_text(encoding="utf-8"))
    return pd.DataFrame({"rate_date": [r["date"] for r in records], "rate": [r["rate"] for r in records]})


def load_raw_frames(wh, raw_dir):
    """All raw inputs as DataFrames in a dict."""
    frames = {name: pd.read_sql_query(sql, wh) for name, sql in READ_SQL.items()}
    frames["holidays"] = load_holidays(raw_dir)
    frames["fx_rates"] = load_fx_rates(raw_dir)
    return frames


# ---------------------------------------------------------------- quarantine cascade

CHILD_KEYS = {"order_items": ["order_id", "order_item_id"], "order_reviews": ["review_id", "order_id"]}


def apply_quarantine(frames, q):
    """Quarantine children of quarantined orders, then return frames without quarantined rows."""
    bad_orders = q.keys("orders")
    for source, keys in CHILD_KEYS.items():
        df = frames[source]
        orphaned = df[df["order_id"].astype(str).isin(bad_orders)]
        q.add(source, orphaned, keys, "parent_order_quarantined")
    clean = dict(frames)
    clean["orders"] = q.remove_from(frames["orders"], "orders", ["order_id"])
    for source, keys in CHILD_KEYS.items():
        clean[source] = q.remove_from(frames[source], source, keys)
    clean["geolocation"] = q.remove_from(frames["geolocation"], "geolocation", ["geo_row_id"])
    return clean


# ---------------------------------------------------------------- stg_ builders

def stage_geolocation(geo_rows):
    """One row per zip prefix (mean lat/lng). Rows outside Brazil are already quarantined."""
    points = pd.DataFrame({"zip_prefix": zip5(geo_rows["geolocation_zip_code_prefix"]),
                           "lat": geo_rows["geolocation_lat"], "lng": geo_rows["geolocation_lng"]})
    return geo.zip_centroids(points)


def stage_people(raw, kind, stg_geo, join_log):
    """stg_customers or stg_sellers: cleaned text, region, and coordinates from the zip centroid."""
    df = pd.DataFrame({
        f"{kind}_id": raw[f"{kind}_id"],
        "city": clean_text(raw[f"{kind}_city"], lower=True),
        "state": clean_text(raw[f"{kind}_state"], upper=True),
        "zip_prefix": zip5(raw[f"{kind}_zip_code_prefix"]),
    })
    if kind == "customer":
        df.insert(1, "customer_unique_id", raw["customer_unique_id"])
    df["region"] = df["state"].map(rules.REGION_BY_STATE)
    df = checked_join(df, stg_geo[["zip_prefix", "lat", "lng"]], "zip_prefix", "m:1",
                      f"stg_{kind}s -> stg_geolocation", join_log)
    return df.sort_values(f"{kind}_id").reset_index(drop=True)


def stage_products(products, translation, join_log):
    """Products with the English category. Typo column names are fixed here."""
    df = pd.DataFrame({
        "product_id": products["product_id"],
        "category_pt": clean_text(products["product_category_name"], lower=True),
        "name_length": int_column(products["product_name_lenght"].tolist()),
        "description_length": int_column(products["product_description_lenght"].tolist()),
        "photos_qty": int_column(products["product_photos_qty"].tolist()),
        "weight_g": int_column(products["product_weight_g"].tolist()),
        "length_cm": int_column(products["product_length_cm"].tolist()),
        "height_cm": int_column(products["product_height_cm"].tolist()),
        "width_cm": int_column(products["product_width_cm"].tolist()),
    })
    lookup = pd.DataFrame({"category_pt": clean_text(translation["product_category_name"], lower=True),
                           "translated": clean_text(translation["product_category_name_english"], lower=True)})
    df = checked_join(df, lookup, "category_pt", "m:1", "stg_products -> category translation", join_log)
    # A category with no translation keeps its Portuguese name, flagged. No category stays missing.
    df["category_en_is_fallback"] = (df["category_pt"].notna() & df["translated"].isna()).astype(int)
    df["category_en"] = df["translated"].fillna(df["category_pt"])
    return df.drop(columns="translated").sort_values("product_id").reset_index(drop=True)


def stage_reviews(reviews, q):
    """Keep one review per order: the latest by answer timestamp, then creation date, then review_id.
    The older reviews are quarantined with a reason (never dropped silently)."""
    ranked = reviews.sort_values(["review_answer_timestamp", "review_creation_date", "review_id"],
                                 ascending=False, na_position="last")
    is_loser = ranked.duplicated("order_id", keep="first")
    q.add("order_reviews", ranked[is_loser], ["review_id", "order_id"], "superseded_by_later_review_for_same_order")
    kept = ranked[~is_loser]
    df = pd.DataFrame({
        "review_id": kept["review_id"], "order_id": kept["order_id"],
        "review_score": int_column(kept["review_score"].tolist()),
        "review_creation_date": kept["review_creation_date"],
        "review_answer_ts": kept["review_answer_timestamp"],
    })
    return df.sort_values("order_id").reset_index(drop=True)


def stage_holidays(holidays, q):
    """National holidays only (global = true). Carnival is kept but flagged separately (BR2)."""
    state_level = holidays[holidays["is_global"] == 0]
    q.add("holidays", state_level, ["holiday_date", "name"], "not_national_global_false")
    national = holidays[holidays["is_global"] == 1]
    repeats = national[national.duplicated(["holiday_date", "name"], keep="first")]
    q.add("holidays", repeats, ["holiday_date", "name"], "duplicate_date_and_name")
    df = national.drop_duplicates(["holiday_date", "name"], keep="first").copy()
    df["is_carnival"] = (df["name"] == "Carnival").astype(int)
    df["is_public_national"] = (df["types"].str.contains("Public") & (df["is_carnival"] == 0)).astype(int)
    return df.sort_values(["holiday_date", "name"]).reset_index(drop=True)


def stage_fx_rates(fx):
    """Business-day rates as received. Forward-filling weekends happens in dim_date (stage 4)."""
    df = pd.DataFrame({"rate_date": fx["rate_date"], "brl_usd_rate": fx["rate"].astype(float)})
    return df.sort_values("rate_date").reset_index(drop=True)


def holiday_dates(stg_holidays, flag):
    """Set of date objects for the holidays where the given flag column is 1."""
    return {date.fromisoformat(d) for d in stg_holidays.loc[stg_holidays[flag] == 1, "holiday_date"]}


def stage_orders(orders, stg_customers, stg_holidays, join_log):
    """Orders with the BR1 (late) and BR2 (holiday window) flags."""
    df = pd.DataFrame({
        "order_id": orders["order_id"], "customer_id": orders["customer_id"],
        "order_status": clean_text(orders["order_status"], lower=True),
        "purchase_ts": clean_text(orders["order_purchase_timestamp"]),
        "approved_ts": clean_text(orders["order_approved_at"]),
        "carrier_ts": clean_text(orders["order_delivered_carrier_date"]),
        "delivered_ts": clean_text(orders["order_delivered_customer_date"]),
        "estimated_ts": clean_text(orders["order_estimated_delivery_date"]),
    })
    customer_state = stg_customers[["customer_id", "state"]].rename(columns={"state": "customer_state"})
    df = checked_join(df, customer_state, "customer_id", "1:1", "stg_orders -> stg_customers", join_log)

    status, delivered = as_list(df["order_status"]), as_list(df["delivered_ts"])
    purchase, estimated = as_list(df["purchase_ts"]), as_list(df["estimated_ts"])
    public = holiday_dates(stg_holidays, "is_public_national")
    carnival = holiday_dates(stg_holidays, "is_carnival")
    df["in_scope"] = [int(rules.in_scope(s, d)) for s, d in zip(status, delivered)]
    df["days_late"] = [rules.days_late(s, d, e) for s, d, e in zip(status, delivered, estimated)]
    df["is_late"] = [int(rules.is_late(s, d, e)) for s, d, e in zip(status, delivered, estimated)]
    df["holiday_in_window"] = [int(rules.holiday_in_window(p, e, public)) for p, e in zip(purchase, estimated)]
    df["carnival_in_window"] = [int(rules.holiday_in_window(p, e, carnival)) for p, e in zip(purchase, estimated)]
    for name, source in [("purchase_date", "purchase_ts"), ("estimated_date", "estimated_ts"),
                         ("delivered_date", "delivered_ts")]:
        df[name] = df[source].str.slice(0, 10)
    return df.sort_values("order_id").reset_index(drop=True)


def stage_order_items(items, stg_orders, stg_sellers, stg_customers, join_log):
    """Order items with BR3 (delay cause), BR4 (distance, freight share) and the order's scope/late flags."""
    df = pd.DataFrame({
        "order_id": items["order_id"], "order_item_id": items["order_item_id"],
        "product_id": items["product_id"], "seller_id": items["seller_id"],
        "shipping_limit_ts": clean_text(items["shipping_limit_date"]),
        "price": items["price"].astype(float), "freight_value": items["freight_value"].astype(float),
    })
    order_cols = stg_orders[["order_id", "customer_id", "order_status", "carrier_ts", "delivered_ts",
                             "in_scope", "is_late"]]
    df = checked_join(df, order_cols, "order_id", "m:1", "stg_order_items -> stg_orders", join_log)
    sellers = stg_sellers[["seller_id", "state", "lat", "lng"]].rename(
        columns={"state": "seller_state", "lat": "seller_lat", "lng": "seller_lng"})
    df = checked_join(df, sellers, "seller_id", "m:1", "stg_order_items -> stg_sellers", join_log)
    customers = stg_customers[["customer_id", "state", "lat", "lng"]].rename(
        columns={"state": "customer_state", "lat": "customer_lat", "lng": "customer_lng"})
    df = checked_join(df, customers, "customer_id", "m:1", "stg_order_items -> stg_customers", join_log)

    carrier, delivered = as_list(df["carrier_ts"]), as_list(df["delivered_ts"])
    limit, late = as_list(df["shipping_limit_ts"]), as_list(df["is_late"])
    scope = as_list(df["in_scope"])
    # handoff and transit only make sense for orders that were delivered (in scope)
    df["handoff_late_days"] = int_column([rules.handoff_late_days(c, lim) if s else None
                                          for c, lim, s in zip(carrier, limit, scope)])
    df["transit_days"] = int_column([rules.transit_days(d, c) if s else None
                                     for d, c, s in zip(delivered, carrier, scope)])
    df["delay_cause"] = [rules.delay_cause(bool(lt), c, lim) for lt, c, lim in zip(late, carrier, limit)]
    df["freight_share"] = pd.Series([rules.freight_share(f, p) for f, p in
                                     zip(df["freight_value"], df["price"])], dtype="float64")
    coords = zip(as_list(df["seller_lat"]), as_list(df["seller_lng"]),
                 as_list(df["customer_lat"]), as_list(df["customer_lng"]))
    df["distance_km"] = pd.Series([geo.haversine_km(*c) if None not in c else None for c in coords],
                                  dtype="float64")
    columns = ["order_id", "order_item_id", "product_id", "seller_id", "customer_id", "shipping_limit_ts",
               "price", "freight_value", "freight_share", "distance_km", "order_status", "in_scope",
               "is_late", "handoff_late_days", "transit_days", "delay_cause", "seller_state", "customer_state"]
    return df[columns].sort_values(["order_id", "order_item_id"]).reset_index(drop=True)


def build_staging(clean, q, join_log):
    """Build every stg_ table from the cleaned raw frames. Returns {table_name: DataFrame}."""
    stg = {}
    stg["stg_geolocation"] = stage_geolocation(clean["geolocation"])
    stg["stg_customers"] = stage_people(clean["customers"], "customer", stg["stg_geolocation"], join_log)
    stg["stg_sellers"] = stage_people(clean["sellers"], "seller", stg["stg_geolocation"], join_log)
    stg["stg_products"] = stage_products(clean["products"], clean["translation"], join_log)
    stg["stg_order_reviews"] = stage_reviews(clean["order_reviews"], q)
    stg["stg_holidays"] = stage_holidays(clean["holidays"], q)
    stg["stg_fx_rates"] = stage_fx_rates(clean["fx_rates"])
    stg["stg_orders"] = stage_orders(clean["orders"], stg["stg_customers"], stg["stg_holidays"], join_log)
    stg["stg_order_items"] = stage_order_items(clean["order_items"], stg["stg_orders"],
                                               stg["stg_sellers"], stg["stg_customers"], join_log)
    return stg


# ---------------------------------------------------------------- writing

def sql_type(series):
    """SQLite column type for a DataFrame column."""
    if pd.api.types.is_bool_dtype(series) or pd.api.types.is_integer_dtype(series):
        return "INTEGER"
    if pd.api.types.is_float_dtype(series):
        return "REAL"
    return "TEXT"


def write_table(conn, name, df):
    """Replace a table with the DataFrame's rows (DROP + CREATE + INSERT). The caller commits."""
    columns = ", ".join(f"{col} {sql_type(df[col])}" for col in df.columns)
    conn.execute(f"DROP TABLE IF EXISTS {name}")
    conn.execute(f"CREATE TABLE {name} ({columns})")
    plain = df.astype(object).where(df.notna(), None)  # missing values become NULL
    marks = ",".join("?" * len(df.columns))
    conn.executemany(f"INSERT INTO {name} VALUES ({marks})", plain.itertuples(index=False, name=None))


JOIN_LOG_COLUMNS = ("run_id TEXT, join_name TEXT, relationship TEXT, left_rows INTEGER, "
                    "right_rows INTEGER, rows_after INTEGER, logged_at TEXT")


def save_logs(wh, run_id, ran_at, results, join_log):
    """Append test results and join counts. Runs even when the run failed, in its own transaction."""
    dq.write_results(wh, run_id, ran_at, results)
    wh.execute(f"CREATE TABLE IF NOT EXISTS join_log ({JOIN_LOG_COLUMNS})")
    wh.executemany("INSERT INTO join_log VALUES (?,?,?,?,?,?,?)",
                   [(run_id, j["join_name"], j["relationship"], j["left_rows"], j["right_rows"],
                     j["rows_after"], ran_at) for j in join_log])
    wh.commit()


# ---------------------------------------------------------------- reconciliation

RECON = [  # source, stg table, quarantine source, SQL for rows staged, SQL for rows out of scope (or None)
    ("orders", "stg_orders", "orders", "SELECT COUNT(*) FROM stg_orders",
     "SELECT COUNT(*) FROM stg_orders WHERE in_scope = 0"),
    ("order_items", "stg_order_items", "order_items", "SELECT COUNT(*) FROM stg_order_items",
     "SELECT COUNT(*) FROM stg_order_items WHERE in_scope = 0"),
    ("order_reviews", "stg_order_reviews", "order_reviews", "SELECT COUNT(*) FROM stg_order_reviews", None),
    ("customers", "stg_customers", "customers", "SELECT COUNT(*) FROM stg_customers", None),
    ("sellers", "stg_sellers", "sellers", "SELECT COUNT(*) FROM stg_sellers", None),
    ("products", "stg_products", "products", "SELECT COUNT(*) FROM stg_products", None),
    # many geolocation rows are averaged into one row per zip prefix: n_points counts them
    ("geolocation", "stg_geolocation", "geolocation", "SELECT COALESCE(SUM(n_points), 0) FROM stg_geolocation", None),
    ("holidays", "stg_holidays", "holidays", "SELECT COUNT(*) FROM stg_holidays", None),
    ("fx_rates", "stg_fx_rates", "fx_rates", "SELECT COUNT(*) FROM stg_fx_rates", None),
]


def reconcile(wh, extracted, q):
    """Check extracted = staged + quarantined for every source, reading the tables just written.

    Out-of-scope rows (cancelled, unavailable, not yet delivered) are KEPT in staging with
    in_scope = 0, so they are part of 'staged'. Raises ReconciliationError on any mismatch.
    """
    rows = []
    for source, _, q_source, staged_sql, scope_sql in RECON:
        staged = wh.execute(staged_sql).fetchone()[0]
        out_of_scope = wh.execute(scope_sql).fetchone()[0] if scope_sql else 0
        quarantined = q.count(q_source)
        rows.append({"source": source, "extracted": extracted[source], "quarantined": quarantined,
                     "staged": staged, "of_which_out_of_scope": out_of_scope,
                     "ok": extracted[source] == staged + quarantined})
    bad = [r["source"] for r in rows if not r["ok"]]
    if bad:
        raise ReconciliationError(f"reconciliation failed for: {', '.join(bad)}")
    return rows


# ---------------------------------------------------------------- the run

def run_staging(wh_path=WAREHOUSE_DB, raw_dir=RAW_DIR, run_id=None, full=False, now=None):
    """Run stage 3 end to end. Returns a summary dict. Raises StopRun on a stop-level problem."""
    now = now or datetime.now(timezone.utc)
    ran_at = now.isoformat(timespec="seconds")
    run_id = run_id or now.strftime("%Y%m%dT%H%M%SZ")
    wh = sqlite3.connect(wh_path)
    results, join_log = [], []
    try:
        frames = load_raw_frames(wh, raw_dir)
        frames["_full_run"] = full  # test 13 only applies to a full run
        extracted = {name: len(df) for name, df in frames.items() if not name.startswith("_")}
        q = Quarantine(run_id, ran_at)
        dq.run_raw_tests(frames, q, results)
        clean = apply_quarantine(frames, q)
        stg = build_staging(clean, q, join_log)
        dq.run_late_rate_test(stg["stg_orders"], wh, results)

        wh.execute("BEGIN")  # from here on, everything is written together or not at all
        for name, df in stg.items():
            write_table(wh, name, df)
        q.write(wh)
        recon = reconcile(wh, extracted, q)
        wh.commit()
    except BaseException:
        wh.rollback()  # a failed run leaves the previous tables untouched
        raise
    finally:
        save_logs(wh, run_id, ran_at, results, join_log)
        wh.close()
    return {"run_id": run_id, "recon": recon, "tests": results, "joins": join_log,
            "quarantine": {s: q.reason_counts(s) for s in ("orders", "order_items", "order_reviews",
                                                           "geolocation", "holidays")},
            "stg_rows": {name: len(df) for name, df in stg.items()}}


def print_report(summary):
    """Print the reconciliation, test results and join counts as plain text tables."""
    print("\nRECONCILIATION (extracted = staged + quarantined; out-of-scope rows are inside 'staged')")
    print(f"{'source':14s}{'extracted':>11s}{'quarantined':>13s}{'staged':>10s}{'out_of_scope':>14s}  check")
    for r in summary["recon"]:
        print(f"{r['source']:14s}{r['extracted']:>11,}{r['quarantined']:>13,}{r['staged']:>10,}"
              f"{r['of_which_out_of_scope']:>14,}  {'OK' if r['ok'] else 'MISMATCH'}")
    print("\nQUARANTINE REASONS")
    for source, reasons in summary["quarantine"].items():
        for reason, n in reasons.items():
            print(f"  {source:14s}{n:>9,}  {reason}")
    print("\nDATA TESTS (dq_results)")
    print(f"{'#':>3s}  {'severity':12s}{'status':13s}{'checked':>10s}{'failed':>9s}  test")
    for r in sorted(summary["tests"], key=lambda r: r["test_no"]):
        print(f"{r['test_no']:>3d}  {r['severity']:12s}{r['status']:13s}{r['rows_checked']:>10,}"
              f"{r['rows_failed']:>9,}  {r['test']}")
    print("\nJOINS (rows before -> after)")
    for j in summary["joins"]:
        print(f"  {j['join_name']:40s}{j['relationship']:>4s}  {j['left_rows']:>9,} -> {j['rows_after']:>9,}")


def main():
    parser = argparse.ArgumentParser(description="Stage 3: staging, joins, data tests, quarantine")
    parser.add_argument("--full", action="store_true", help="treat this as a full run (enables test 13)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        summary = run_staging(full=args.full)
    except StopRun as err:
        print(f"\nRUN STOPPED: {err}")
        sys.exit(2)
    print_report(summary)


if __name__ == "__main__":
    main()
