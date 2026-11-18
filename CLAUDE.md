# DSC 620 Project: Olist late-deliveries pipeline

Pair project (Nancy Kalmiche 202507819, Jason Greich 202507818), Lebanese American University, DSC 620 Data Engineering, instructor Ahmad Audi. Final submission: Tuesday, December 8, 2026, 9:00 AM (repo tagged v1.0, PDF report, PDF slides). 35% of the course grade. Topic T8, validated by the instructor.

This file is the standing brief. Read it fully before any task. Staged build prompts are in PROMPTS.md.

## Working rules (non-negotiable)

1. Small and correct beats big and half-working. A few thousand rows per source is enough; here we have the full Olist set, which is fine. Do not add features that are not listed here.
2. Both team members must be able to explain every line. Write short functions, plain names, a comment for every non-obvious line, and no clever one-liners. After each stage, add or update `docs/code_walkthrough.md` with a plain-English explanation of each module (what it does, why, what to say if asked).
3. AI use must be disclosed. Maintain `docs/AI_USAGE.md`: after each stage, append what you generated, which files, and what the humans still need to review or change. Do not claim human edits that did not happen.
4. Never commit secrets or data. `.env` and `data/` are git-ignored. A key in the repo costs -10 points. Olist is CC BY-NC-SA: do not commit the Olist files.
5. One command runs everything: `python run_pipeline.py`. Running it twice gives identical tables (idempotent). It writes a log file and a row to a run-log table.
6. Never silently drop rows. Anything removed goes to a quarantine table with a reason. The pipeline prints and logs: rows in, rows loaded, rows quarantined, per source.
7. Both members commit their own work (commit history is graded). Commit after every stage with a clear message. Do not squash history. Use the git identity already configured; do not change it.
8. Be polite to every API: honest User-Agent, timeouts, retries with backoff, handle HTTP errors and rate limits.
9. After each stage stop, summarize what was built, show test output, and wait for the human to continue.

## The question

Headline: Which product categories, sellers and customer states have the highest late-delivery rate per order, do seller-to-customer distance and freight cost explain it, and do national holidays make it worse?

Two extra questions that make the project our own:
- Seller or carrier: for each seller-state to customer-state route, is the delay caused by the seller (handoff after the shipping limit date) or by the carrier (transit after handoff)? Do holidays hit one leg harder?
- Cost of lateness: how much lower is the review score on late orders, and how much revenue (in USD) sits on late orders, by category and seller? Say "revenue on late orders", never "revenue lost".

Primary user: an online store's logistics team deciding which sellers, categories and routes to act on.

## Sources (three, two types)

| Source | Type | Access | Key | Notes |
|---|---|---|---|---|
| Olist Brazilian e-commerce, 9 tables | database | Download CSVs from Kaggle (dataset `olistbr/brazilian-ecommerce`), load into SQLite `data/store/olist.db` as the store's operational DB, then extract with SQL | Kaggle credentials in `.env` (see `.env.example`); fallback: human puts the zip in `data/seed/` | Static 2016-2018, CC BY-NC-SA 4.0 |
| Nager.Date public holidays, Brazil | API | `https://date.nager.at/api/v3/PublicHolidays/{year}/BR`, years 2016-2018 | none | Keep only national holidays (`global` is true); drop the single state holiday (July 9, Sao Paulo) |
| Frankfurter v2 exchange rates, BRL to USD | API | `https://api.frankfurter.dev/v2/rates`, use `providers=ecb` | none | Check the current docs at frankfurter.dev for exact parameters and pagination. ECB data has no weekend rows: forward-fill from the last available business day in the warehouse, and say so |

Expected Olist sizes (from the public dataset; verify during profiling and record the real numbers): orders 99,441; order_items about 112,650; customers 99,441; sellers about 3,095; products about 32,951; reviews about 99,224; payments about 103,886; geolocation about 1,000,163; category translation 71.

Known Olist quirks to confirm in profiling: Portuguese category names with a few missing translations and about 610 products with no category; typo column names in products (`product_name_lenght`, `product_description_lenght`); dates stored as strings; estimated delivery dates always at 00:00:00 (so compare dates, not timestamps); a handful of `delivered` orders with no delivery date; delivery dates before purchase dates; duplicate and multiple reviews per order; geolocation has many rows per zip prefix and some coordinates outside Brazil.

## Incremental extraction (pair requirement)

- Extract orders (and their items, reviews) from `olist.db` by `order_purchase_timestamp`.
- Save a watermark in a small `extract_state` table (source name, watermark = max purchase timestamp seen, updated at).
- Every run re-extracts from `watermark - 60 days` (overlap window), because an order changes after it is placed (status, delivery date). Existing orders in that window are updated by key, never appended twice.
- First run, or `--full`, extracts everything. Support `--as-of YYYY-MM-DD` to cap the extraction date so we can demo two runs (for example 2017-06-30 then the full range).
- Proof for the report: an integration test changes one order's status in `olist.db`, runs again, and shows the order was updated, row count unchanged.
- Holidays and exchange rates are small: extract in full each run, replace the raw file.

## Layers and where files live

```
data/seed/        Olist zip/CSVs (git-ignored)
data/store/       olist.db, the store's database (git-ignored)
data/raw/         every API response and extract, untouched, timestamped, plus manifest.jsonl (source, URL or query, time, record count)
data/warehouse/   warehouse.db: stg_*, quarantine_*, dim_*, fact_*, run_log, extract_state (git-ignored)
```

SQLite has no schemas, so use name prefixes: `stg_`, `dim_`, `fact_`, `quarantine_`.

## Business rules (document each in `docs/business_rules.md` with an example)

- BR1 Late: only orders with status `delivered` and a delivery date count. An order is late when `date(order_delivered_customer_date) > date(order_estimated_delivery_date)`. `days_late = max(0, date difference)`. Cancelled and unavailable orders are out of scope for the rate: keep them in staging with `in_scope = 0` and report their count. Delivered with no delivery date goes to quarantine.
- BR2 Holiday window: `holiday_in_window` is true when a national public holiday falls between the purchase date and the estimated delivery date, inclusive. Carnival (types Bank, Optional) is NOT a public holiday in this flag; it has its own flag `carnival_in_window`. The dashboard can toggle it.
- BR3 Delay cause (late orders only): per order item, `handoff_late_days = max(0, date(carrier handoff) - date(shipping_limit_date))` and `transit_days = date(delivered) - date(carrier handoff)`. If `handoff_late_days > 0` the cause is `seller`, otherwise `carrier`. If the carrier date is missing the cause is `unknown` (warn, do not quarantine). State the simplification in the report: we have no promised carrier transit time.
- BR4 Distance and freight: `distance_km` is the haversine distance between seller and customer zip-prefix centroids; `freight_share = freight_value / price`. Geolocation is first aggregated to ONE row per zip prefix (mean lat/lng) so the join cannot multiply rows. Coordinates outside Brazil's bounding box (lat -34 to 5.3, lng -74 to -34) are quarantined.
- Money: `price_usd` and `freight_usd` use the ECB BRL/USD rate for the purchase date, forward-filled.
- Reviews: keep one review per order, the latest by `review_answer_timestamp` (tie-break `review_creation_date`, then `review_id`).
- Rate per order, never per item: the late rate is `count(distinct order_id where is_late) / count(distinct order_id in scope)`. An order with three items must count once. When slicing by category or seller, an order counts once in each category or seller it contains. Say this on the dashboard.

## Star schema (SQLite warehouse)

Grain of `fact_order_item`: one row per order item (`order_id`, `order_item_id`).

- `fact_order_item`: keys `order_id`, `order_item_id`; foreign keys `purchase_date_key`, `estimated_date_key`, `delivered_date_key`, `product_id`, `seller_id`, `customer_id`; measures and flags `price_brl`, `freight_brl`, `price_usd`, `freight_usd`, `freight_share`, `distance_km`, `is_late`, `days_late`, `handoff_late_days`, `transit_days`, `delay_cause`, `review_score`, `holiday_in_window`, `carnival_in_window`, `order_status`, `in_scope`.
- `dim_date` (role-playing for purchase, estimated, delivered): `date_key` (YYYYMMDD), date, year, month, quarter, weekday, is_weekend, is_public_holiday, holiday_name, is_carnival, `brl_usd_rate`. Covers 2016-09-01 to 2018-12-31. This is where holidays and exchange rates meet the fact table.
- `dim_product`: product_id, category_pt, category_en, weight and size columns.
- `dim_seller`: seller_id, city, state, region, zip_prefix, lat, lng.
- `dim_customer`: customer_id (one per order in Olist), customer_unique_id, city, state, region, zip_prefix, lat, lng.
- Region: map the 27 states to the 5 IBGE regions with a small dict in code.
- Order-level views (`vw_order_late`, and one view per dashboard chart) compute the rate with `count(distinct order_id)`.

## Data tests (at least 8 required; implement all of these)

Severity: stop = abort the run; quarantine = move failing rows to a quarantine table with a reason, continue; warn = log and continue.

1. orders.order_id unique: stop
2. orders.order_purchase_timestamp not null and parseable: stop
3. order_items.order_id exists in orders (relationships): stop
4. order_items.seller_id exists in sellers (relationships): quarantine
5. order_items.product_id exists in products (relationships): quarantine
6. orders.order_status in the 8 known values (delivered, shipped, canceled, unavailable, invoiced, processing, created, approved): warn
7. delivered orders with no delivery date: quarantine
8. delivery date earlier than purchase date: quarantine
9. price >= 0 and freight_value >= 0: quarantine
10. reviews: score between 1 and 5, and order_id exists in orders: quarantine
11. BRL/USD rate between 0.15 and 0.45, and no gap longer than 5 days: warn
12. holidays: (date, name) unique: warn
13. row count: orders extracted at least 90,000 on a full run: stop
14. late orders <= delivered orders, and the per-order rate equals an independent recomputation from raw: stop
15. geolocation coordinates inside the Brazil bounding box: quarantine

Every test run writes one row per test to `dq_results` (test, severity, rows checked, rows failed, run time). Quarantine tables carry `source_table`, `row_key`, `reason`, `run_id`, `quarantined_at`, and the raw row as JSON.

## Extensions (the two we named)

1. dbt: staging-to-mart models and YAML tests as a dbt project (`source()`, `ref()`, tests in YAML). First check that dbt with SQLite (dbt-sqlite adapter, community-maintained) works for what we need. If it does not, tell the humans and propose moving only the warehouse to DuckDB (dbt-duckdb) before going further. Do not decide silently.
2. Scheduling: GitHub Actions scheduled daily run plus a `run_log` table (run_id, start, end, rows in, rows loaded, rows quarantined, status). The scheduled run needs Kaggle secrets or a fixture; see PROMPTS.md stage 7.

## Dashboard (Dash)

Three charts and at least two callbacks:
1. Late rate by distance band and by freight-share band (bar or line).
2. Seller share versus carrier share of late orders, by seller-state to customer-state route (heatmap or stacked bar).
3. Review score and revenue (USD) on late versus on-time orders, by category (grouped bar).

Controls: customer state dropdown, year dropdown, Carnival toggle, minimum-orders slider. Each control feeds at least one callback. Include a one-line note on each chart: "rate per order, orders counted once". Read only from the warehouse views.

## Unit tests (at least 6, pytest)

Late rule (including date-only comparison and the 00:00:00 estimate); holiday window flag; delay-cause rule; haversine distance; per-order rate with a multi-item order counted once; watermark and overlap window calculation; quarantine routing keeps the reason; idempotent load (run twice, identical counts and checksums); incremental update (mutate an order, re-run, row updated not duplicated).

## Repository layout

```
README.md  requirements.txt  .env.example  .gitignore  run_pipeline.py
src/extract/      olist.py  nager.py  frankfurter.py  seed_store.py  manifest.py  watermark.py
src/transform/    staging.py  geo.py  rules.py
src/quality/      tests.py  quarantine.py
src/load/         schema.sql  load_warehouse.py  run_log.py
dbt_project/      models/staging  models/marts  schema.yml  sources.yml
dashboard/app.py
tests/
docs/             architecture.md  schema.md  business_rules.md  profiling.md  code_walkthrough.md  AI_USAGE.md  report_numbers.json
.github/workflows/ci.yml  schedule.yml
```

If dbt owns some of the SQL, keep `src/transform/` for the pandas-only work (cleaning, haversine, geolocation aggregation) and say in `docs/architecture.md` exactly who does what.

## Definition of done

A stranger can clone the repo, follow README.md, run `python run_pipeline.py`, then `python dashboard/app.py`, and get the dashboard. A second run changes nothing. `pytest` passes. All numbers needed for the report are in `docs/report_numbers.json`.
