"""The 15 data tests from CLAUDE.md, each with a severity.

  stop       = abort the run (StopRun is raised)
  quarantine = move the failing rows to a quarantine table with a reason, continue
  warn       = log and continue

Every test writes one result row to the dq_results table (see write_results).
Tests 1 to 13 and 15 look at the raw data before staging. Test 14 compares the
staged late rate with an independent recomputation, so it runs after staging.
"""
import logging
import time
from collections import namedtuple

import pandas as pd

from src.transform import geo

log = logging.getLogger("pipeline")

ORDER_STATUSES = {"delivered", "shipped", "canceled", "unavailable",
                  "invoiced", "processing", "created", "approved"}
TS_FORMAT = "%Y-%m-%d %H:%M:%S"
MIN_ORDERS_FULL_RUN = 90_000
FX_MIN, FX_MAX = 0.15, 0.45
FX_MAX_GAP_DAYS = 5

# What a test function returns. skipped=True means "did not apply to this run".
Outcome = namedtuple("Outcome", "checked failed detail skipped", defaults=("", False))


class StopRun(Exception):
    """A stop-severity problem: the run must abort. The message says what and why."""


# ---------------------------------------------------------------- helpers

def _missing_in(values, allowed):
    """Boolean Series: True where a value is NOT in the allowed collection."""
    return ~values.isin(set(allowed))


def _date_part(series):
    """First 10 characters of each timestamp (the date), as text; missing stays missing."""
    return series.astype("string").str.slice(0, 10)


# ---------------------------------------------------------------- tests 1 to 3 (stop)

def t01_order_id_unique(frames, q):
    orders = frames["orders"]
    return Outcome(len(orders), int(orders["order_id"].duplicated().sum()))


def t02_purchase_timestamp_valid(frames, q):
    orders = frames["orders"]
    parsed = pd.to_datetime(orders["order_purchase_timestamp"], format=TS_FORMAT, errors="coerce")
    return Outcome(len(orders), int(parsed.isna().sum()))  # missing or not parseable


def t03_items_have_order(frames, q):
    items = frames["order_items"]
    bad = _missing_in(items["order_id"], frames["orders"]["order_id"])
    return Outcome(len(items), int(bad.sum()))


# ---------------------------------------------------------------- tests 4 to 5 (quarantine items)

def t04_items_have_seller(frames, q):
    items = frames["order_items"]
    bad = _missing_in(items["seller_id"], frames["sellers"]["seller_id"])
    q.add("order_items", items[bad], ["order_id", "order_item_id"], "seller_id_not_in_sellers")
    return Outcome(len(items), int(bad.sum()))


def t05_items_have_product(frames, q):
    items = frames["order_items"]
    bad = _missing_in(items["product_id"], frames["products"]["product_id"])
    q.add("order_items", items[bad], ["order_id", "order_item_id"], "product_id_not_in_products")
    return Outcome(len(items), int(bad.sum()))


# ---------------------------------------------------------------- test 6 (warn)

def t06_known_order_status(frames, q):
    orders = frames["orders"]
    bad = _missing_in(orders["order_status"], ORDER_STATUSES)
    unknown = sorted(orders.loc[bad, "order_status"].dropna().unique())
    return Outcome(len(orders), int(bad.sum()), f"unknown values: {unknown}" if unknown else "")


# ---------------------------------------------------------------- tests 7 to 8 (quarantine orders)

def t07_delivered_has_delivery_date(frames, q):
    orders = frames["orders"]
    bad = (orders["order_status"] == "delivered") & orders["order_delivered_customer_date"].isna()
    q.add("orders", orders[bad], ["order_id"], "delivered_without_delivery_date")
    return Outcome(int((orders["order_status"] == "delivered").sum()), int(bad.sum()))


def t08_delivery_not_before_purchase(frames, q):
    orders = frames["orders"]
    delivered = _date_part(orders["order_delivered_customer_date"])
    purchased = _date_part(orders["order_purchase_timestamp"])
    bad = (delivered < purchased).fillna(False)  # ISO dates compare correctly as text
    q.add("orders", orders[bad], ["order_id"], "delivery_before_purchase")
    return Outcome(int(delivered.notna().sum()), int(bad.sum()))


# ---------------------------------------------------------------- test 9 (quarantine items)

def t09_money_not_negative(frames, q):
    items = frames["order_items"]
    bad = ~((items["price"] >= 0) & (items["freight_value"] >= 0))  # missing also fails
    q.add("order_items", items[bad], ["order_id", "order_item_id"], "negative_or_missing_price_or_freight")
    return Outcome(len(items), int(bad.sum()))


# ---------------------------------------------------------------- test 10 (quarantine reviews)

def t10_reviews_valid(frames, q):
    reviews = frames["order_reviews"]
    bad_score = ~reviews["review_score"].between(1, 5)  # missing also fails
    bad_order = _missing_in(reviews["order_id"], frames["orders"]["order_id"])
    keys = ["review_id", "order_id"]
    q.add("order_reviews", reviews[bad_score], keys, "review_score_not_1_to_5")
    q.add("order_reviews", reviews[bad_order], keys, "review_order_not_in_orders")
    return Outcome(len(reviews), int((bad_score | bad_order).sum()))


# ---------------------------------------------------------------- test 11 (warn)

def t11_fx_rates_sane(frames, q):
    fx = frames["fx_rates"].sort_values("rate_date")
    out_of_range = int((~fx["rate"].between(FX_MIN, FX_MAX)).sum())
    days = pd.to_datetime(fx["rate_date"])
    long_gaps = int((days.diff().dt.days > FX_MAX_GAP_DAYS).sum())
    detail = f"{out_of_range} rates outside {FX_MIN}-{FX_MAX}; {long_gaps} gaps longer than {FX_MAX_GAP_DAYS} days"
    return Outcome(len(fx), out_of_range + long_gaps, detail)


# ---------------------------------------------------------------- test 12 (warn)

def t12_holidays_unique(frames, q):
    holidays = frames["holidays"]
    return Outcome(len(holidays), int(holidays.duplicated(["holiday_date", "name"]).sum()))


# ---------------------------------------------------------------- test 13 (stop)

def t13_enough_orders(frames, q):
    n = len(frames["orders"])
    if not frames.get("_full_run", False):
        return Outcome(n, 0, "skipped: only checked on a full run", True)
    return Outcome(n, 0 if n >= MIN_ORDERS_FULL_RUN else 1, f"{n:,} orders, need at least {MIN_ORDERS_FULL_RUN:,}")


# ---------------------------------------------------------------- test 15 (quarantine geolocation)

def t15_geolocation_in_brazil(frames, q):
    geo_rows = frames["geolocation"]
    inside = [geo.in_brazil(None if pd.isna(la) else la, None if pd.isna(ln) else ln)
              for la, ln in zip(geo_rows["geolocation_lat"], geo_rows["geolocation_lng"])]
    bad = ~pd.Series(inside, index=geo_rows.index)
    # geo_row_id is the row number in the raw table: geolocation has no natural key and
    # identical rows exist, so without it two identical bad rows would merge into one entry.
    q.add("geolocation", geo_rows[bad], ["geo_row_id"], "coordinates_outside_brazil")
    return Outcome(len(geo_rows), int(bad.sum()))


# (number, name, severity, function). Test 14 is separate: it needs the staged tables.
RAW_TESTS = [
    (1, "orders.order_id unique", "stop", t01_order_id_unique),
    (2, "orders.order_purchase_timestamp not null and parseable", "stop", t02_purchase_timestamp_valid),
    (3, "order_items.order_id exists in orders", "stop", t03_items_have_order),
    (4, "order_items.seller_id exists in sellers", "quarantine", t04_items_have_seller),
    (5, "order_items.product_id exists in products", "quarantine", t05_items_have_product),
    (6, "orders.order_status in the 8 known values", "warn", t06_known_order_status),
    (7, "delivered orders have a delivery date", "quarantine", t07_delivered_has_delivery_date),
    (8, "delivery date not before purchase date", "quarantine", t08_delivery_not_before_purchase),
    (9, "price >= 0 and freight_value >= 0", "quarantine", t09_money_not_negative),
    (10, "reviews: score 1-5 and order exists", "quarantine", t10_reviews_valid),
    (11, "BRL/USD rate in range, no gap over 5 days", "warn", t11_fx_rates_sane),
    (12, "holidays (date, name) unique", "warn", t12_holidays_unique),
    (13, "orders extracted >= 90,000 on a full run", "stop", t13_enough_orders),
    (15, "geolocation coordinates inside Brazil", "quarantine", t15_geolocation_in_brazil),
]


# ---------------------------------------------------------------- running tests

def status_of(severity, failed, skipped):
    """pass / skipped / quarantined / warned / failed, from the severity and the fail count."""
    if skipped:
        return "skipped"
    if failed == 0:
        return "pass"
    return {"quarantine": "quarantined", "warn": "warned", "stop": "failed"}[severity]


def record(results, number, name, severity, outcome, seconds):
    """Add one result row and log it. Returns the row."""
    row = {"test_no": number, "test": name, "severity": severity,
           "rows_checked": outcome.checked, "rows_failed": outcome.failed,
           "status": status_of(severity, outcome.failed, outcome.skipped),
           "detail": outcome.detail, "run_seconds": round(seconds, 4)}
    results.append(row)
    level = logging.WARNING if row["status"] in ("warned", "quarantined", "failed") else logging.INFO
    log.log(level, "test %02d [%s] %s: %s (%s of %s rows failed) %s", number, severity, name,
            row["status"], outcome.failed, outcome.checked, outcome.detail)
    return row


def stop_if_failed(row):
    if row["status"] == "failed":
        raise StopRun(f"STOP test {row['test_no']} failed: {row['test']} "
                      f"({row['rows_failed']} of {row['rows_checked']} rows) {row['detail']}")


def run_raw_tests(frames, q, results):
    """Run tests 1-13 and 15 in order. Results are appended to the list as we go,
    so a stop test still leaves the earlier results to be saved."""
    for number, name, severity, func in RAW_TESTS:
        started = time.perf_counter()
        outcome = func(frames, q)
        row = record(results, number, name, severity, outcome, time.perf_counter() - started)
        stop_if_failed(row)


def run_late_rate_test(stg_orders, wh, results):
    """Test 14 (stop): late <= delivered, and the staged per-order rate equals an
    independent recomputation straight from raw_orders with plain SQL."""
    started = time.perf_counter()
    scope = stg_orders[stg_orders["in_scope"] == 1]
    delivered = scope["order_id"].nunique()
    late = scope.loc[scope["is_late"] == 1, "order_id"].nunique()
    # Independent recomputation: different code path (SQL on raw), no pandas, no rules.py.
    raw_late, raw_delivered = wh.execute(
        "SELECT COUNT(DISTINCT CASE WHEN date(order_delivered_customer_date) > "
        "date(order_estimated_delivery_date) THEN order_id END), COUNT(DISTINCT order_id) "
        "FROM raw_orders WHERE order_status = 'delivered' "
        "AND order_delivered_customer_date IS NOT NULL "
        "AND date(order_delivered_customer_date) >= date(order_purchase_timestamp)").fetchone()
    problems = []
    if late > delivered:
        problems.append("late orders > delivered orders")
    # compare late/delivered with raw_late/raw_delivered without dividing (exact)
    if late * raw_delivered != raw_late * delivered:
        problems.append(f"staged rate {late}/{delivered} differs from raw recomputation {raw_late}/{raw_delivered}")
    rate = f"{late}/{delivered} = {late / delivered:.4%}" if delivered else "no delivered orders"
    outcome = Outcome(delivered, len(problems), "; ".join(problems) or f"per-order late rate {rate}, matches raw")
    row = record(results, 14, "late orders <= delivered, per-order rate = raw recomputation",
                 "stop", outcome, time.perf_counter() - started)
    stop_if_failed(row)


# ---------------------------------------------------------------- dq_results table

DQ_COLUMNS = ("run_id TEXT, test_no INTEGER, test TEXT, severity TEXT, rows_checked INTEGER, "
              "rows_failed INTEGER, status TEXT, detail TEXT, run_seconds REAL, ran_at TEXT")


def write_results(conn, run_id, ran_at, results):
    """Append one row per test to dq_results (history of every run)."""
    conn.execute(f"CREATE TABLE IF NOT EXISTS dq_results ({DQ_COLUMNS})")
    conn.executemany(
        "INSERT INTO dq_results VALUES (?,?,?,?,?,?,?,?,?,?)",
        [(run_id, r["test_no"], r["test"], r["severity"], r["rows_checked"], r["rows_failed"],
          r["status"], r["detail"], r["run_seconds"], ran_at) for r in sorted(results, key=lambda r: r["test_no"])])
