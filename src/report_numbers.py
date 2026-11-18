"""Collect every number the report needs from the warehouse into docs/report_numbers.json.

Usage:  python -m src.report_numbers      (after python run_pipeline.py)
All rates are per ORDER (count distinct order_id). Only reads the warehouse.
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WAREHOUSE = ROOT / "data" / "warehouse" / "warehouse.db"
OUT = ROOT / "docs" / "report_numbers.json"
MIN_ORDERS_GROUP = 100     # categories and states need this many orders to be ranked
MIN_ORDERS_SELLER = 50     # sellers need this many orders


def rows(conn, sql, params=()):
    """Query result as a list of dicts."""
    cursor = conn.execute(sql, params)
    names = [c[0] for c in cursor.description]
    return [dict(zip(names, r)) for r in cursor.fetchall()]


def one(conn, sql):
    return conn.execute(sql).fetchone()[0]


def rate(late, orders):
    return round(late / orders, 6) if orders else None


def sources_section(conn):
    """Rows extracted, staged, quarantined (by reason) and loaded per source."""
    def reasons(table):
        return {r["reason"]: r["n"] for r in rows(conn, f"SELECT reason, COUNT(*) n FROM {table} GROUP BY reason")}
    quarantined = {s: one(conn, f"SELECT COUNT(*) FROM quarantine_{s}")
                   for s in ("orders", "order_items", "order_reviews", "geolocation", "holidays")}
    staged = {
        "orders": one(conn, "SELECT COUNT(*) FROM stg_orders"),
        "order_items": one(conn, "SELECT COUNT(*) FROM stg_order_items"),
        "order_reviews": one(conn, "SELECT COUNT(*) FROM stg_order_reviews"),
        "customers": one(conn, "SELECT COUNT(*) FROM stg_customers"),
        "sellers": one(conn, "SELECT COUNT(*) FROM stg_sellers"),
        "products": one(conn, "SELECT COUNT(*) FROM stg_products"),
        "geolocation": one(conn, "SELECT SUM(n_points) FROM stg_geolocation"),   # raw points averaged into centroids
        "holidays": one(conn, "SELECT COUNT(*) FROM stg_holidays"),
        "fx_rates": one(conn, "SELECT COUNT(*) FROM stg_fx_rates"),
    }
    result = {}
    for source, n_staged in staged.items():
        n_quarantined = quarantined.get(source, 0)
        result[source] = {
            "extracted": n_staged + n_quarantined, "staged": n_staged, "quarantined": n_quarantined,
            "quarantined_by_reason": reasons(f"quarantine_{source}") if source in quarantined else {},
            "loaded_to_marts": {
                "order_items": one(conn, "SELECT COUNT(*) FROM fact_order_item"),
                "customers": one(conn, "SELECT COUNT(*) FROM dim_customer"),
                "sellers": one(conn, "SELECT COUNT(*) FROM dim_seller"),
                "products": one(conn, "SELECT COUNT(*) FROM dim_product"),
            }.get(source),
        }
    result["geolocation"]["staged_zip_centroids"] = one(conn, "SELECT COUNT(*) FROM stg_geolocation")
    result["orders"]["out_of_scope"] = one(conn, "SELECT COUNT(*) FROM stg_orders WHERE in_scope = 0")
    result["orders"]["out_of_scope_by_status"] = {
        r["order_status"]: r["n"] for r in rows(
            conn, "SELECT order_status, COUNT(*) n FROM stg_orders WHERE in_scope = 0 GROUP BY order_status")}
    return result


def ranking(conn, group_sql, min_orders, top=10):
    """Top groups by late rate (orders counted once per group). group_sql selects: key, order_id, is_late."""
    sql = (f"WITH g AS ({group_sql}) SELECT key, COUNT(DISTINCT order_id) AS orders, "
           "COUNT(DISTINCT CASE WHEN is_late = 1 THEN order_id END) AS late_orders FROM g "
           f"GROUP BY key HAVING orders >= {min_orders}")
    data = rows(conn, sql)
    for r in data:
        r["late_rate"] = rate(r["late_orders"], r["orders"])
    return sorted(data, key=lambda r: -r["late_rate"])[:top]


def late_rate_section(conn):
    orders, late = conn.execute("SELECT COUNT(*), SUM(is_late) FROM vw_order_late").fetchone()
    items, late_items = conn.execute(
        "SELECT COUNT(*), SUM(is_late) FROM fact_order_item WHERE in_scope = 1").fetchone()
    return {
        "per_order": {"orders_in_scope": orders, "late_orders": late, "late_rate": rate(late, orders)},
        "per_item_wrong": {"items_in_scope": items, "late_items": late_items, "late_rate": rate(late_items, items)},
        "by_customer_region": rows(
            conn, "SELECT customer_region AS region, COUNT(*) orders, SUM(is_late) late_orders, "
                  "ROUND(1.0*SUM(is_late)/COUNT(*), 6) late_rate FROM vw_order_late GROUP BY 1 ORDER BY 4 DESC"),
        "by_year": rows(
            conn, "SELECT purchase_year AS year, COUNT(*) orders, SUM(is_late) late_orders, "
                  "ROUND(1.0*SUM(is_late)/COUNT(*), 6) late_rate FROM vw_order_late GROUP BY 1 ORDER BY 1"),
    }


def cost_section(conn):
    review = rows(conn, "SELECT is_late, COUNT(*) orders, ROUND(AVG(review_score), 3) avg_review, "
                        "ROUND(SUM(revenue_usd), 2) revenue_usd FROM vw_order_late GROUP BY is_late")
    by_flag = {("late" if r["is_late"] else "on_time"): r for r in review}
    top_cat = rows(conn, "SELECT category, ROUND(SUM(revenue_usd), 2) revenue_on_late_orders_usd, SUM(orders) late_orders "
                         "FROM vw_chart_category_cost WHERE is_late = 1 GROUP BY category "
                         "ORDER BY 2 DESC LIMIT 10")
    top_sellers = rows(conn, "SELECT seller_id, ROUND(SUM(price_usd), 2) revenue_on_late_orders_usd, "
                             "COUNT(DISTINCT order_id) late_orders FROM fact_order_item "
                             "WHERE in_scope = 1 AND is_late = 1 GROUP BY seller_id ORDER BY 2 DESC LIMIT 10")
    return {
        "review_score_late": by_flag["late"]["avg_review"], "review_score_on_time": by_flag["on_time"]["avg_review"],
        "review_score_difference": round(by_flag["on_time"]["avg_review"] - by_flag["late"]["avg_review"], 3),
        "revenue_on_late_orders_usd": by_flag["late"]["revenue_usd"],
        "revenue_on_on_time_orders_usd": by_flag["on_time"]["revenue_usd"],
        "top_categories_by_revenue_on_late_orders": top_cat,
        "top_sellers_by_revenue_on_late_orders": top_sellers,
    }


def cause_section(conn):
    overall = rows(conn, "SELECT delay_cause, COUNT(*) orders FROM vw_order_late WHERE is_late = 1 GROUP BY 1")
    counts = {r["delay_cause"]: r["orders"] for r in overall}
    total = sum(counts.values())
    routes = rows(conn, "SELECT seller_state, customer_state, SUM(late_orders) late_orders, "
                        "SUM(seller_late_orders) seller_late, SUM(carrier_late_orders) carrier_late "
                        "FROM vw_chart_route_cause GROUP BY 1, 2 HAVING SUM(orders) >= 100")
    for r in routes:
        r["seller_share"] = rate(r["seller_late"], r["seller_late"] + r["carrier_late"])
    routes = [r for r in routes if r["seller_share"] is not None]   # routes with no late order have no share
    by_holiday = rows(conn, "SELECT holiday_in_window, SUM(seller_late_orders) seller_late, "
                            "SUM(carrier_late_orders) carrier_late, SUM(orders) orders "
                            "FROM vw_chart_route_cause GROUP BY 1")
    return {
        "late_orders_by_cause": counts,
        "seller_share": rate(counts.get("seller", 0), total), "carrier_share": rate(counts.get("carrier", 0), total),
        "routes_with_100_plus_orders_highest_seller_share": sorted(routes, key=lambda r: -r["seller_share"])[:10],
        "routes_with_100_plus_orders_lowest_seller_share": sorted(routes, key=lambda r: r["seller_share"])[:10],
        "by_holiday_in_window": by_holiday,
        "note": "seller = parcel handed to the carrier after the shipping limit date; otherwise carrier. No promised transit time exists.",
    }


def holiday_section(conn):
    def group(where):
        r = rows(conn, f"SELECT COUNT(*) orders, SUM(is_late) late FROM vw_order_late WHERE {where}")[0]
        return {"orders": r["orders"], "late_orders": r["late"], "late_rate": rate(r["late"], r["orders"])}
    return {
        "holiday_in_window": group("holiday_in_window = 1"),
        "no_holiday_in_window": group("holiday_in_window = 0"),
        "carnival_in_window": group("carnival_in_window = 1"),
        "no_carnival_in_window": group("carnival_in_window = 0"),
        "holiday_window_excluding_carnival_orders": group("holiday_in_window = 1 AND carnival_in_window = 0"),
        "no_holiday_excluding_carnival_orders": group("holiday_in_window = 0 AND carnival_in_window = 0"),
    }


def distance_freight_section(conn):
    out = {}
    for kind in ("distance", "freight"):
        out[kind] = rows(conn, "SELECT band, SUM(orders) orders, SUM(late_orders) late_orders, "
                               "ROUND(1.0*SUM(late_orders)/SUM(orders), 6) late_rate "
                               f"FROM vw_chart_distance_freight WHERE band_type = '{kind}' GROUP BY band ORDER BY band")
    out["items_without_distance"] = one(conn, "SELECT COUNT(*) FROM fact_order_item WHERE distance_km IS NULL")
    return out


def run(conn):
    base = "SELECT {key} AS key, f.order_id, f.is_late FROM fact_order_item f {join} WHERE f.in_scope = 1"
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sources": sources_section(conn),
        "joins_latest_run": rows(conn, "SELECT join_name, relationship, left_rows, rows_after FROM join_log "
                                       "WHERE run_id = (SELECT MAX(run_id) FROM join_log)"),
        "data_tests_latest_run": rows(conn, "SELECT test_no, test, severity, status, rows_checked, rows_failed FROM dq_results "
                                            "WHERE run_id = (SELECT MAX(run_id) FROM dq_results) ORDER BY test_no"),
        "warehouse_rows": {t: one(conn, f"SELECT COUNT(*) FROM {t}") for t in
                           ("dim_date", "dim_product", "dim_seller", "dim_customer", "fact_order_item")},
        "late_rate": late_rate_section(conn),
        "top_categories_by_late_rate": ranking(conn, base.format(
            key="p.category_en", join="JOIN dim_product p ON p.product_id = f.product_id"), MIN_ORDERS_GROUP),
        "top_sellers_by_late_rate": ranking(conn, base.format(key="f.seller_id", join=""), MIN_ORDERS_SELLER),
        "top_customer_states_by_late_rate": ranking(conn, base.format(
            key="c.state", join="JOIN dim_customer c ON c.customer_id = f.customer_id"), MIN_ORDERS_GROUP),
        "ranking_minimums": {"category_and_state_orders": MIN_ORDERS_GROUP, "seller_orders": MIN_ORDERS_SELLER},
        "distance_and_freight": distance_freight_section(conn),
        "seller_vs_carrier": cause_section(conn),
        "cost_of_lateness": cost_section(conn),
        "holiday_effect": holiday_section(conn),
        "other_findings": {
            "items_with_negative_transit_days": one(conn, "SELECT COUNT(*) FROM fact_order_item WHERE transit_days < 0"),
            "orders_out_of_scope": one(conn, "SELECT COUNT(*) FROM stg_orders WHERE in_scope = 0"),
        },
        "run_history_last_two": rows(conn, "SELECT run_id, mode, status, rows_extracted, rows_loaded, rows_quarantined, "
                                           "tests_passed, tests_warned, tests_failed, duration_seconds FROM "
                                           "(SELECT * FROM run_log ORDER BY started_at DESC LIMIT 2) ORDER BY started_at"),
    }


def main():
    conn = sqlite3.connect(f"{WAREHOUSE.resolve().as_uri()}?mode=ro", uri=True)
    try:
        numbers = run(conn)
    finally:
        conn.close()
    OUT.write_text(json.dumps(numbers, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
