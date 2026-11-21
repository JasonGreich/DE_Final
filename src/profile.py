"""Profile every source before building anything else.

Reads data/store/olist.db with plain SQL, pulls the holidays and the exchange rates
once from the two APIs, prints a report and saves it to docs/profiling.md.
The API responses are saved untouched in data/raw/profiling/ (git-ignored).
Stage 2 builds the real, polite extractors; this script is only for looking at the data.

Run:  python -m src.profile
"""
import json
import sqlite3
import time
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "store" / "olist.db"
REPORT_PATH = ROOT / "docs" / "profiling.md"
RAW_DIR = ROOT / "data" / "raw" / "profiling"
USER_AGENT = "DSC620-olist-student-project/0.1 (university coursework)"

# Brazil bounding box from the project brief (business rule BR4)
LAT_MIN, LAT_MAX, LNG_MIN, LNG_MAX = -34.0, 5.3, -74.0, -34.0

# Columns that hold dates (stored as strings in Olist)
DATE_COLUMNS = {
    "orders": ["order_purchase_timestamp", "order_approved_at", "order_delivered_carrier_date",
               "order_delivered_customer_date", "order_estimated_delivery_date"],
    "order_items": ["shipping_limit_date"],
    "order_reviews": ["review_creation_date", "review_answer_timestamp"],
}

# Natural key per table, used to count duplicate keys
KEYS = {
    "customers": ["customer_id"],
    "sellers": ["seller_id"],
    "products": ["product_id"],
    "product_category_name_translation": ["product_category_name"],
    "geolocation": ["geolocation_zip_code_prefix", "geolocation_lat", "geolocation_lng",
                    "geolocation_city", "geolocation_state"],  # no key: whole-row duplicates
    "orders": ["order_id"],
    "order_items": ["order_id", "order_item_id"],
    "order_reviews": ["review_id", "order_id"],
    "order_payments": ["order_id", "payment_sequential"],
}

# GLOB pattern for 'YYYY-MM-DD HH:MM:SS'
TS_GLOB = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9] [0-9][0-9]:[0-9][0-9]:[0-9][0-9]"

OUT = []  # report lines


def emit(line=""):
    """Print a line and keep it for the markdown file."""
    print(line)
    OUT.append(line)


def table(headers, rows):
    """Emit a markdown table."""
    emit("| " + " | ".join(headers) + " |")
    emit("|" + "|".join("---" for _ in headers) + "|")
    for row in rows:
        emit("| " + " | ".join(str(v) for v in row) + " |")
    emit()


def one(conn, sql, params=()):
    """Return the first column of the first row."""
    return conn.execute(sql, params).fetchone()[0]


# ---------------------------------------------------------------- per-table profile

def profile_table(conn, name):
    """Row count, columns with types and null counts, duplicate keys, date ranges."""
    emit(f"### {name}")
    rows = one(conn, f"SELECT COUNT(*) FROM {name}")
    emit(f"Rows: **{rows:,}**")
    emit()
    cols = conn.execute(f"PRAGMA table_info({name})").fetchall()
    col_rows = []
    for _, col, typ, *_ in cols:
        nulls = one(conn, f"SELECT COUNT(*) FROM {name} WHERE {col} IS NULL")
        col_rows.append((col, typ, f"{nulls:,}", f"{100 * nulls / rows:.2f}%"))
    table(["column", "type", "nulls", "null %"], col_rows)

    key = KEYS[name]
    key_list = ", ".join(key)
    dup_rows = one(conn, f"SELECT COALESCE(SUM(n - 1), 0) FROM "
                         f"(SELECT COUNT(*) n FROM {name} GROUP BY {key_list} HAVING n > 1)")
    emit(f"Duplicate key ({key_list}): **{dup_rows:,}** surplus rows")
    emit()

    for col in DATE_COLUMNS.get(name, []):
        lo, hi = conn.execute(f"SELECT MIN({col}), MAX({col}) FROM {name}").fetchone()
        bad = one(conn, f"SELECT COUNT(*) FROM {name} WHERE {col} IS NOT NULL "
                        f"AND {col} NOT GLOB '{TS_GLOB}'")
        emit(f"- `{col}`: min {lo}, max {hi}, not in 'YYYY-MM-DD HH:MM:SS' format: {bad:,}")
    if name in DATE_COLUMNS:
        emit()


def profile_status_counts(conn):
    """Counts of the categorical columns we care about."""
    emit("### Status and category counts")
    emit()
    for tbl, col in [("orders", "order_status"), ("order_payments", "payment_type"),
                     ("order_reviews", "review_score")]:
        rows = conn.execute(f"SELECT {col}, COUNT(*) FROM {tbl} GROUP BY {col} "
                            f"ORDER BY COUNT(*) DESC").fetchall()
        emit(f"`{tbl}.{col}`")
        emit()
        table([col, "rows"], [(v, f"{n:,}") for v, n in rows])


# ---------------------------------------------------------------- quirks from the project brief

def quirks_products(conn):
    """Missing category, missing translation, typo column names."""
    emit("### Quirk: product categories")
    emit()
    no_cat = one(conn, "SELECT COUNT(*) FROM products WHERE product_category_name IS NULL")
    emit(f"- Products with no category: **{no_cat:,}**")
    untrans = conn.execute(
        "SELECT p.product_category_name, COUNT(*) FROM products p "
        "LEFT JOIN product_category_name_translation t "
        "ON t.product_category_name = p.product_category_name "
        "WHERE p.product_category_name IS NOT NULL AND t.product_category_name IS NULL "
        "GROUP BY p.product_category_name").fetchall()
    emit(f"- Categories with no English translation: **{len(untrans)}** "
         f"({sum(n for _, n in untrans)} products): "
         + ", ".join(f"{c} ({n})" for c, n in untrans))
    cols = [c[1] for c in conn.execute("PRAGMA table_info(products)")]
    typos = [c for c in cols if "lenght" in c]
    emit(f"- Typo column names present in products: {typos}")
    emit()


def quirks_orders(conn):
    """Delivered without date, delivery before purchase, estimate time of day."""
    emit("### Quirk: order dates and statuses")
    emit()
    delivered = one(conn, "SELECT COUNT(*) FROM orders WHERE order_status = 'delivered'")
    no_date = one(conn, "SELECT COUNT(*) FROM orders WHERE order_status = 'delivered' "
                        "AND order_delivered_customer_date IS NULL")
    before = one(conn, "SELECT COUNT(*) FROM orders WHERE order_delivered_customer_date "
                       "< order_purchase_timestamp")
    not_deliv_with_date = one(conn, "SELECT COUNT(*) FROM orders WHERE order_status <> 'delivered' "
                                    "AND order_delivered_customer_date IS NOT NULL")
    est_not_midnight = one(conn, "SELECT COUNT(*) FROM orders "
                                 "WHERE time(order_estimated_delivery_date) <> '00:00:00'")
    carrier_before = one(conn, "SELECT COUNT(*) FROM orders WHERE order_delivered_carrier_date "
                               "< order_purchase_timestamp")
    emit(f"- Delivered orders: **{delivered:,}**")
    emit(f"- Delivered with no delivery date: **{no_date:,}** (go to quarantine, BR1)")
    emit(f"- Delivery date earlier than purchase date: **{before:,}**")
    emit(f"- Carrier handoff date earlier than purchase date: {carrier_before:,}")
    emit(f"- Non-delivered orders that do have a delivery date: {not_deliv_with_date:,}")
    emit(f"- Estimated delivery dates not at 00:00:00: **{est_not_midnight:,}** "
         "(0 means the estimate is always midnight, so compare dates not timestamps)")
    # Facts that BR3 (delay cause) depends on
    no_carrier = one(conn, "SELECT COUNT(*) FROM orders WHERE order_status = 'delivered' "
                           "AND order_delivered_carrier_date IS NULL")
    carrier_after_deliv = one(conn, "SELECT COUNT(*) FROM orders WHERE order_delivered_carrier_date "
                                    "> order_delivered_customer_date")
    far_limit = one(conn, "SELECT COUNT(*) FROM order_items WHERE shipping_limit_date >= '2019-01-01'")
    far_limit_orders = one(conn, "SELECT COUNT(DISTINCT order_id) FROM order_items "
                                 "WHERE shipping_limit_date >= '2019-01-01'")
    emit(f"- Delivered orders with no carrier handoff date (BR3 cause = unknown): {no_carrier:,}")
    emit(f"- Carrier handoff later than customer delivery: {carrier_after_deliv:,}")
    emit(f"- Items with shipping_limit_date in 2019 or later (max is 2020): "
         f"{far_limit:,} items on {far_limit_orders:,} orders")
    # Preview of BR1 on date-only comparison, with a timestamp comparison for contrast
    late_date = one(conn, "SELECT COUNT(*) FROM orders WHERE order_status = 'delivered' "
                          "AND order_delivered_customer_date IS NOT NULL "
                          "AND date(order_delivered_customer_date) > date(order_estimated_delivery_date)")
    late_ts = one(conn, "SELECT COUNT(*) FROM orders WHERE order_status = 'delivered' "
                        "AND order_delivered_customer_date IS NOT NULL "
                        "AND order_delivered_customer_date > order_estimated_delivery_date")
    with_date = delivered - no_date
    emit(f"- Late by date-only rule (BR1): **{late_date:,}** of {with_date:,} "
         f"delivered orders with a date ({100 * late_date / with_date:.2f}%)")
    emit(f"- Late if we wrongly compared full timestamps: {late_ts:,} "
         f"({late_ts - late_date:,} extra orders delivered on the estimated day itself)")
    emit()


def quirks_items(conn):
    """Items per order, orders without items, negative money, orphans."""
    emit("### Quirk: order items (why the rate must be per order)")
    emit()
    items = one(conn, "SELECT COUNT(*) FROM order_items")
    orders_with_items = one(conn, "SELECT COUNT(DISTINCT order_id) FROM order_items")
    multi = one(conn, "SELECT COUNT(*) FROM (SELECT order_id FROM order_items "
                      "GROUP BY order_id HAVING COUNT(*) > 1)")
    max_items = one(conn, "SELECT MAX(n) FROM (SELECT COUNT(*) n FROM order_items GROUP BY order_id)")
    multi_seller = one(conn, "SELECT COUNT(*) FROM (SELECT order_id FROM order_items "
                             "GROUP BY order_id HAVING COUNT(DISTINCT seller_id) > 1)")
    no_items = one(conn, "SELECT COUNT(*) FROM orders WHERE order_id NOT IN "
                         "(SELECT order_id FROM order_items)")
    neg = one(conn, "SELECT COUNT(*) FROM order_items WHERE price < 0 OR freight_value < 0")
    zero_price = one(conn, "SELECT COUNT(*) FROM order_items WHERE price = 0")
    emit(f"- Order items: {items:,} over {orders_with_items:,} orders "
         f"(per-item vs per-order inflation: {items / orders_with_items:.3f}x)")
    emit(f"- Orders with more than one item: **{multi:,}** (max items in one order: {max_items})")
    emit(f"- Orders with more than one seller: {multi_seller:,}")
    emit(f"- Orders with no items: **{no_items:,}**")
    emit(f"- Items with negative price or freight: {neg:,}; items with price 0: {zero_price:,}")
    emit()


def quirks_reviews(conn):
    """Multiple reviews per order, repeated review ids."""
    emit("### Quirk: reviews")
    emit()
    total = one(conn, "SELECT COUNT(*) FROM order_reviews")
    orders_rev = one(conn, "SELECT COUNT(DISTINCT order_id) FROM order_reviews")
    multi = one(conn, "SELECT COUNT(*) FROM (SELECT order_id FROM order_reviews "
                      "GROUP BY order_id HAVING COUNT(*) > 1)")
    surplus = total - orders_rev
    dup_ids = one(conn, "SELECT COUNT(*) FROM (SELECT review_id FROM order_reviews "
                        "GROUP BY review_id HAVING COUNT(*) > 1)")
    no_review = one(conn, "SELECT COUNT(*) FROM orders WHERE order_id NOT IN "
                          "(SELECT order_id FROM order_reviews)")
    bad_score = one(conn, "SELECT COUNT(*) FROM order_reviews WHERE review_score NOT BETWEEN 1 AND 5")
    emit(f"- Review rows: {total:,} for {orders_rev:,} distinct orders")
    emit(f"- Orders with more than one review: **{multi:,}** ({surplus:,} surplus rows to drop)")
    emit(f"- review_id values used on more than one row: {dup_ids:,}")
    emit(f"- Orders with no review: {no_review:,}")
    emit(f"- Scores outside 1 to 5: {bad_score:,}")
    emit()


def quirks_geolocation(conn):
    """Rows per zip prefix, coordinates outside Brazil, zips with no geolocation."""
    emit("### Quirk: geolocation")
    emit()
    rows = one(conn, "SELECT COUNT(*) FROM geolocation")
    prefixes = one(conn, "SELECT COUNT(DISTINCT geolocation_zip_code_prefix) FROM geolocation")
    per = sorted(r[0] for r in conn.execute(
        "SELECT COUNT(*) FROM geolocation GROUP BY geolocation_zip_code_prefix"))
    outside = one(conn, "SELECT COUNT(*) FROM geolocation WHERE geolocation_lat NOT BETWEEN ? AND ? "
                        "OR geolocation_lng NOT BETWEEN ? AND ?", (LAT_MIN, LAT_MAX, LNG_MIN, LNG_MAX))
    outside_prefixes = one(conn, "SELECT COUNT(DISTINCT geolocation_zip_code_prefix) FROM geolocation "
                                 "WHERE geolocation_lat NOT BETWEEN ? AND ? OR geolocation_lng NOT BETWEEN ? AND ?",
                           (LAT_MIN, LAT_MAX, LNG_MIN, LNG_MAX))
    only_outside = one(conn, "SELECT COUNT(*) FROM (SELECT geolocation_zip_code_prefix FROM geolocation "
                             "GROUP BY geolocation_zip_code_prefix HAVING MIN("
                             "geolocation_lat NOT BETWEEN ? AND ? OR geolocation_lng NOT BETWEEN ? AND ?) = 1)",
                       (LAT_MIN, LAT_MAX, LNG_MIN, LNG_MAX))
    cust_missing = one(conn, "SELECT COUNT(DISTINCT customer_zip_code_prefix) FROM customers "
                             "WHERE customer_zip_code_prefix NOT IN "
                             "(SELECT geolocation_zip_code_prefix FROM geolocation)")
    sell_missing = one(conn, "SELECT COUNT(DISTINCT seller_zip_code_prefix) FROM sellers "
                             "WHERE seller_zip_code_prefix NOT IN "
                             "(SELECT geolocation_zip_code_prefix FROM geolocation)")
    emit(f"- Rows: {rows:,}; distinct zip prefixes: **{prefixes:,}**")
    emit(f"- Rows per zip prefix: min {per[0]}, median {per[len(per) // 2]}, max {per[-1]:,}")
    emit(f"- Rows with coordinates outside Brazil's box: **{outside:,}** "
         f"(touching {outside_prefixes:,} prefixes; {only_outside:,} prefixes have ONLY bad rows)")
    emit(f"- Customer zip prefixes with no geolocation row: {cust_missing:,}; "
         f"seller zip prefixes with none: {sell_missing:,}")
    emit()


def quirks_keys(conn):
    """Relationships and 1:1 claims we rely on later."""
    emit("### Relationships we rely on")
    emit()
    orph_cust = one(conn, "SELECT COUNT(*) FROM orders WHERE customer_id NOT IN "
                          "(SELECT customer_id FROM customers)")
    cust_unique = one(conn, "SELECT COUNT(DISTINCT customer_unique_id) FROM customers")
    cust_orders = one(conn, "SELECT COUNT(*) FROM (SELECT customer_id FROM orders "
                            "GROUP BY customer_id HAVING COUNT(*) > 1)")
    emit(f"- Orders whose customer_id is missing from customers: {orph_cust:,}")
    emit(f"- Customers: {one(conn, 'SELECT COUNT(*) FROM customers'):,} customer_id values for "
         f"{cust_unique:,} distinct customer_unique_id (repeat buyers)")
    emit(f"- customer_id values with more than one order: {cust_orders:,} (0 means one customer_id per order)")
    emit()


# ---------------------------------------------------------------- APIs

def get_json(url, params=None, tries=3):
    """GET with timeout, honest User-Agent and simple backoff. Returns (response, parsed json)."""
    for attempt in range(1, tries + 1):
        try:
            resp = requests.get(url, params=params, timeout=30, headers={"User-Agent": USER_AGENT})
            if resp.status_code == 429 or resp.status_code >= 500:
                raise requests.HTTPError(f"HTTP {resp.status_code}")
            resp.raise_for_status()
            return resp, resp.json()
        except (requests.RequestException, ValueError) as err:
            if attempt == tries:
                raise
            wait = 2 ** attempt
            print(f"  retry {attempt} after error: {err} (waiting {wait}s)")
            time.sleep(wait)


def save_raw(name, resp):
    """Save the response body exactly as received."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / name
    path.write_bytes(resp.content)
    return path


def profile_holidays():
    """Pull Nager.Date 2016-2018 and describe it."""
    emit("## Source: Nager.Date public holidays (Brazil)")
    emit()
    items = []
    for year in (2016, 2017, 2018):
        url = f"https://date.nager.at/api/v3/PublicHolidays/{year}/BR"
        resp, data = get_json(url)
        save_raw(f"nager_{year}.json", resp)
        items.extend(data)
    emit(f"- Records: **{len(items)}** over 2016 to 2018")
    emit("- Per year: " + ", ".join(f"{y}: {sum(1 for h in items if h['date'].startswith(str(y)))}"
                                    for y in (2016, 2017, 2018)))
    types = Counter("+".join(h["types"]) for h in items)
    emit("- By type: " + ", ".join(f"{t}: {n}" for t, n in types.items()))
    not_global = [h for h in items if not h["global"]]
    emit(f"- Not global (state holidays, to drop): **{len(not_global)}** "
         + ", ".join(f"{h['date']} {h['name']} counties={h['counties']}" for h in not_global))
    carnival = [h for h in items if h["name"] == "Carnival"]
    emit(f"- Carnival rows: {len(carnival)} ({', '.join(h['date'] for h in carnival)}), types "
         f"{sorted({'+'.join(h['types']) for h in carnival})}")
    dup = [k for k, n in Counter((h["date"], h["name"]) for h in items).items() if n > 1]
    emit(f"- Duplicate (date, name) pairs: {len(dup)}")
    emit()
    table(["date", "name", "types", "global"],
          [(h["date"], h["name"], "+".join(h["types"]), h["global"]) for h in items])
    return items


def profile_rates():
    """Pull Frankfurter v2 BRL->USD (ECB) 2016-2018 and describe it."""
    emit("## Source: Frankfurter v2 exchange rates, BRL to USD (ECB)")
    emit()
    url = "https://api.frankfurter.dev/v2/rates"
    params = {"base": "BRL", "quotes": "USD", "from": "2016-01-01", "to": "2018-12-31",
              "providers": "ecb"}
    resp, data = get_json(url, params)
    save_raw("frankfurter_brl_usd.json", resp)
    emit(f"- Request: `{resp.url}`")
    emit(f"- Response is a flat JSON list of {len(data):,} rows; pagination headers seen: "
         f"{[h for h in resp.headers if h.lower() in ('link', 'x-total-count', 'x-next')] or 'none'}")
    days = sorted(date.fromisoformat(r["date"]) for r in data)
    rates = [r["rate"] for r in data]
    emit(f"- Date range returned: {days[0]} to {days[-1]} "
         "(the first row is the last business day BEFORE the requested start)")
    emit(f"- Rate min {min(rates)}, max {max(rates)} (test 11 expects 0.15 to 0.45)")
    emit(f"- Weekend rows present: {sum(1 for d in days if d.weekday() >= 5)}")
    # Calendar days in the covered span that have no row (weekends and ECB holidays)
    have = set(days)
    span = [days[0] + timedelta(n) for n in range((days[-1] - days[0]).days + 1)]
    missing = [d for d in span if d not in have]
    weekend_missing = [d for d in missing if d.weekday() >= 5]
    weekday_missing = [d for d in missing if d.weekday() < 5]
    emit(f"- Calendar days in span with no row: **{len(missing)}** "
         f"({len(weekend_missing)} weekend days, {len(weekday_missing)} weekdays such as ECB holidays)")
    longest, run = 0, 0
    for d in span:
        run = run + 1 if d not in have else 0
        longest = max(longest, run)
    emit(f"- Longest run of consecutive days with no row: {longest} (test 11 warns above 5)")
    emit(f"- Weekdays with no row: " + ", ".join(str(d) for d in weekday_missing[:12])
         + (" ..." if len(weekday_missing) > 12 else ""))
    emit("- Forward-fill needed for Olist purchase dates (2016-09 to 2018-10): "
         "weekends and the weekdays above take the last available business-day rate.")
    emit()
    return data


# ---------------------------------------------------------------- main

def main():
    emit("# Data profiling")
    emit()
    emit(f"Generated {datetime.now():%Y-%m-%d %H:%M} by `python -m src.profile`. "
         "Numbers come straight from `data/store/olist.db` and the two APIs.")
    emit()
    emit("## Source: Olist store database (SQLite)")
    emit()
    conn = sqlite3.connect(DB_PATH)
    emit("### Table sizes")
    emit()
    sizes = [(t, f"{one(conn, f'SELECT COUNT(*) FROM {t}'):,}") for t in KEYS]
    table(["table", "rows"], sizes)
    emit("## Per-table profile")
    emit()
    for name in KEYS:
        profile_table(conn, name)
    profile_status_counts(conn)
    emit("## Quirks listed in CLAUDE.md")
    emit()
    quirks_products(conn)
    quirks_orders(conn)
    quirks_items(conn)
    quirks_reviews(conn)
    quirks_geolocation(conn)
    quirks_keys(conn)
    conn.close()
    holidays = profile_holidays()
    rates = profile_rates()

    # Olist purchase range vs the rate range, so we know forward-fill is enough
    emit("## Cross-check: does the rate series cover Olist purchase dates?")
    emit()
    conn = sqlite3.connect(DB_PATH)
    lo, hi = conn.execute("SELECT MIN(date(order_purchase_timestamp)), "
                          "MAX(date(order_purchase_timestamp)) FROM orders").fetchone()
    conn.close()
    emit(f"- Olist purchase dates: {lo} to {hi}; rate series: {min(r['date'] for r in rates)} "
         f"to {max(r['date'] for r in rates)}; holidays: {min(h['date'] for h in holidays)} "
         f"to {max(h['date'] for h in holidays)}")
    emit()
    REPORT_PATH.write_text("\n".join(OUT) + "\n", encoding="utf-8")
    print(f"\nSaved {REPORT_PATH}")


if __name__ == "__main__":
    main()
