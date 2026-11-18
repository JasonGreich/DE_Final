# Code walkthrough (plain English)

One section per module. For each: what it does, why, and what to say if asked.

## Stage 0

### src/extract/seed_store.py
**What it does.** Builds `data/store/olist.db`, a SQLite database that plays the online store's own operational
system. It reads the nine Olist CSVs and creates nine tables with types, primary keys and foreign keys.

**How it works, step by step.**
1. `unpack_zips` extracts the expected CSVs if you put the Kaggle zip in `data/seed/`.
2. `missing_csvs` checks which of the nine files are absent. If any are, `download_from_kaggle` tries the Kaggle API
   using `KAGGLE_USERNAME` and `KAGGLE_KEY` from `.env`. If that fails, the script prints where to put the files and exits.
3. `build_database` deletes any old `olist.db`, creates each table from the `TABLES` dictionary, and inserts the CSV rows.
   Foreign keys are switched on, so a row pointing at a missing parent makes the build fail instead of loading silently.
4. It prints a row count per table.

**Choices to explain.**
- *No cleaning here.* The store is the "raw" operational system; cleaning happens in staging (stage 3), where every removed row is quarantined with a reason.
- *Empty CSV field becomes NULL.* A blank delivery date means "no value", and NULL is how a database says that.
- *Zip prefixes are TEXT.* `09790` would lose its leading zero as a number.
- *Header check.* Each CSV header must equal the expected columns (including Olist's typos `product_name_lenght`), otherwise the build stops.
- *Drop and recreate.* Re-running gives the same database, which is the idempotency rule.
- *Reviews key is (review_id, order_id).* Olist reuses a review_id across orders.

**If asked "why did you do it this way?"** We wanted a realistic source database to extract from with SQL, so the incremental extraction and the watermark in stage 2 have something real to read.

### tests/test_seed_store.py
Builds a few invented rows per table in a temp folder and checks: nine tables exist, no order item without an order,
leading zeros and NULLs are kept, rebuilding gives identical counts, a broken foreign key is rejected, a wrong header is rejected.
It never needs the real dataset, so it runs in under a second.

**BOM note (added after the first real run).** `product_category_name_translation.csv` starts with a hidden
byte-order mark (BOM). We open files with `utf-8-sig`, which silently removes it, so the header check still passes.

## Stage 1

### src/profile.py
**What it does.** Looks at every source before we build anything and writes the result to `docs/profiling.md` (and prints it).
Run with `python -m src.profile`; it takes about 10 seconds.

**How it works.**
- For every Olist table it counts rows, lists columns with their types and null counts, counts duplicate keys, and shows min/max of the date columns
  (and how many dates are not in `YYYY-MM-DD HH:MM:SS` format).
- Then one function per quirk named in CLAUDE.md (`quirks_products`, `quirks_orders`, `quirks_items`, `quirks_reviews`, `quirks_geolocation`, `quirks_keys`), each a few SQL counts.
- `profile_holidays` and `profile_rates` call the two APIs once (30 s timeout, honest User-Agent, 3 tries with growing waits), save the response
  untouched in `data/raw/profiling/` (git-ignored) and describe it.

**Choices to explain.**
- Plain SQL on SQLite, no cleverness: each number in the report is one query you can read.
- Checks like "orders with no review" use `NOT IN (SELECT ...)`. The first version used `NOT EXISTS` and ran for over six minutes, because the
  lookup column had no index so SQLite scanned the whole table for every row. `NOT IN` makes SQLite build a temporary index; it now takes seconds.
- This script is only for looking. The polite extractors that feed the pipeline are written in stage 2.

**If asked "why profile first?"** The business rules depend on facts we only know from the data: for example the estimate is always midnight
(so we compare dates), and 14% of items belong to multi-item orders (so rates must be per order).

### docs/decisions.md
Records the dbt spike verdict (SQLite works, keep it), how we call Frankfurter, and why holidays are filtered on `global` and not on type.

## Stage 2

### src/extract/watermark.py
**What it does.** Decides where an incremental extraction starts, and stores the "watermark" (newest purchase timestamp seen) in the `extract_state` table.
- `overlap_start(watermark)`: watermark minus 60 days, or `None` on the first run (meaning "everything").
- `next_watermark(old, newest_seen)`: the larger of the two, so the watermark never moves backwards.
- `parse_as_of(text)`: checks `--as-of` is a real `YYYY-MM-DD` date.
- `get_watermark` / `save_watermark`: one row per source in `extract_state` (source, watermark, updated_at).

**Why 60 days.** An order changes after it is placed (status, delivery date). Re-reading the last 60 days lets us pick those changes up.
**Honest limit to say if asked:** an order older than the window is not refreshed on a normal run (a test shows this); `--full` refreshes everything.

### src/extract/olist.py
**What it does.** One run: choose the orders (all, or purchased since watermark - 60 days, and not after `--as-of`), read them with SQL plus their items and reviews,
read the five dimension tables in full, save each as a timestamped CSV in `data/raw/`, copy the rows into `raw_*` tables in the warehouse, move the watermark, write manifest lines.

**Key functions.**
- `orders_filter` / `select_sql`: build the SQL. Items and reviews are selected by "order_id is in the window", so they always follow their orders.
- `copy_table`: reads in batches of 10,000, writes the CSV and inserts into `raw_<table>` at the same time (so the 1M-row geolocation table never sits in memory).
- `run_extract`: the whole run inside one warehouse transaction. If anything fails it rolls back, so a failed run changes nothing. Manifest lines are written only after the commit.

**Choices to explain.**
- *Update by key:* `raw_orders` has `order_id` as primary key and uses `INSERT OR REPLACE`, so a re-read order replaces the old row. For items and reviews we delete the window orders' rows first and insert again, so an item removed in the store also disappears.
- *Store is opened read-only:* extraction must never change the source system.
- *CSV, not Parquet:* no extra library, readable in a text editor, values exactly as the store gave them. Cost: bigger files and NULL becomes an empty field.
- *`--full`* ignores the watermark, empties the order tables and reloads. *`--as-of`* caps the purchase date, so we can demo two runs (2017-06-30, then everything).
- *`raw_` tables:* the CSV files are snapshots (one per run). Staging (stage 3) needs one current row per key, so we keep that in `raw_*` tables. This prefix is not in CLAUDE.md; see the deviations in the stage 2 summary.

### src/extract/manifest.py
Appends one JSON line per extraction to `data/raw/manifest.jsonl`: source, URL or SQL (with the real parameter values), time, record count, file, plus extras (watermark before, window start, as_of, full). JSONL so we only ever append.

### src/extract/http.py
One shared `http_get` for both APIs. Honest User-Agent, 30 s timeout, retries with waits of 2, 4, 8 s for timeouts, connection errors, HTTP 429 and 5xx (it obeys `Retry-After`). Other HTTP errors (400, 404) stop immediately with a clear message because retrying cannot fix them. This file is not in the CLAUDE.md layout; it exists so the retry logic is written once.

### src/extract/nager.py
One call per year 2016 to 2018. Each response is saved byte-for-byte and gets a manifest line. An empty or non-JSON answer is an error. No filtering here: dropping the Sao Paulo state holiday happens in staging.

### src/extract/frankfurter.py
One call for BRL to USD, `providers=ecb`, 2016-01-01 to 2018-12-31. The response is saved untouched. `check_coverage` raises an error if the data does not reach both ends of the range (a truncated answer must not look like success). Weekend gaps are not filled here.

### Tests (tests/test_extract_olist.py, tests/test_extract_apis.py)
- Pure: overlap window, watermark never goes backwards, `--as-of` validation.
- Integration on a tiny 5-order store: second run does not duplicate; a changed order is updated and the row count stays the same; an order outside the window is not refreshed (until `--full`); a removed item disappears; `--as-of` run followed by a full catch-up gives no duplicates; the raw CSV matches the store; manifest lines are written.
- APIs with fake responses (no network): User-Agent and timeout, backoff waits, `Retry-After`, no retry on 404, give-up message, untouched bytes, truncated rates rejected.

## Stage 3

How one run flows: load the `raw_` tables and the newest holiday/rate files -> run data tests 1-13 and 15 -> quarantine failing rows ->
remove them -> build the `stg_` tables (every join checked) -> test 14 -> write everything in one transaction -> reconcile.

### src/transform/geo.py
- `haversine_km`: the great-circle distance formula in plain Python (earth radius 6,371 km). Tested against 1 degree of longitude on the equator (111.2 km) and Sao Paulo to Rio (about 358 km).
- `in_brazil`: the bounding-box check from CLAUDE.md. `zip_centroids`: groups geolocation to one row per zip prefix (mean lat/lng, plus `n_points`).
**Why:** the geolocation file has about 1M rows but only 19k zip prefixes. Joining on it raw would multiply every order item by the number of points in its zip.

### src/transform/rules.py
BR1 to BR4 as tiny functions that take plain values: `in_scope`, `days_late`, `is_late`, `holiday_in_window`, `handoff_late_days`, `transit_days`, `delay_cause`, `freight_share`, `order_late_rate`, plus the state-to-region dictionary.
They never touch the database, so each one is tested with a handful of examples (same-day delivery, inclusive holiday ends, same-day handoff, three-item order counted once).
**If asked "why dates, not timestamps?"** The estimate is always 00:00:00; delivered at 15:30 on the estimated day would look late if we compared timestamps.

### src/quality/quarantine.py
A `Quarantine` collector. `add(source, rows, key_cols, reason)` remembers the row, its key and the reason (a row failing twice is stored once with both reasons). `remove_from` returns a frame without quarantined rows.
`write` rebuilds the five `quarantine_*` tables (source_table, row_key, reason, run_id, quarantined_at, raw row as JSON). They are rebuilt each run, so two runs give the same tables.

### src/quality/tests.py
The 15 tests as small functions returning (rows checked, rows failed, detail). A table (`RAW_TESTS`) lists number, name and severity.
- stop (1, 2, 3, 13, 14): raise `StopRun`; results gathered so far are still saved.
- quarantine (4, 5, 7, 8, 9, 10, 15): add the failing rows to the quarantine collector, continue.
- warn (6, 11, 12): logged only.
- Test 13 only applies to a full run (`--full`); otherwise it is recorded as `skipped`.
- Test 14 recomputes the late rate straight from `raw_orders` with plain SQL (not using rules.py or pandas) and compares it exactly with the staged rate.
Every test writes a row to `dq_results` (appended each run: test, severity, rows checked, rows failed, status, detail, seconds).

### src/transform/staging.py
- `checked_join`: a left join that must keep the row count and the stated relationship (`1:1` or `m:1`); otherwise the run stops. Row counts before and after go to the log and to the `join_log` table.
- `apply_quarantine`: items and reviews of a quarantined order are quarantined too (reason `parent_order_quarantined`), so nothing is orphaned.
- `stage_*` functions: one per table. Examples: customers/sellers get cleaned text, region and zip-centroid coordinates; products get the English category (typo columns fixed); reviews are reduced to one per order, the older ones quarantined; holidays keep national ones only; orders get BR1/BR2 flags; order items get BR3/BR4 columns.
- `write_table` replaces a table (DROP, CREATE, INSERT). All tables are written in **one transaction**, so a failed run leaves the previous tables untouched.
- `reconcile`: for each source, rows extracted must equal rows staged plus rows quarantined, read back from the tables just written. Out-of-scope orders are *inside* staged (kept with `in_scope = 0`), and the report shows how many. Geolocation counts the points averaged into each centroid (`n_points`).
**If asked "why quarantine duplicate reviews instead of just dropping them?"** Rule 6 in CLAUDE.md: nothing disappears without a reason, and it keeps the reconciliation exact (99,224 = 98,665 + 559).

### Tests
`test_geo_rules.py` (rules and haversine), `test_quality.py` (every stop test aborts, quarantine keeps reason and raw row, merged reasons, warn does not stop, test 14, quarantine tables), `test_staging.py` (end to end on 4 invented orders: counts, flags, centroids, categories, reviews, holidays, running twice gives identical tables, a stop test leaves the old tables, joins that would multiply rows stop the run, reconciliation catches a silent drop).

## Stage 4: dbt

Python (stages 2-3) extracts, quarantines and stages. dbt starts from the `stg_` tables and builds the star schema and the views.
- `dim_date`: a recursive SQL query makes one row per day; holidays are joined by date; `brl_usd_rate` is the latest ECB rate on or before the day (forward fill). **Why:** this is where holidays and rates meet the fact table.
- `fact_order_item`: one row per order item. Order-level columns (dates, holiday flags, review score) are repeated on every item, so counting rows would overcount orders.
- `vw_order_late`: one row per in-scope order (max of is_late over its items). Every rate is `sum(is_late) / count(*)` on this view.
- `vw_chart_*`: one view per dashboard chart, using `count(distinct order_id)`. They keep order-level columns (state, year, holiday flags) so the dashboard can filter and then add counts up. **Why add counts, not average rates:** an average of rates would weight small groups wrongly.
- Tests: unique/not_null on keys, relationships to every dimension (three date keys to `dim_date`), accepted_values (delay cause: error; order status: warn), plus SQL tests: grain unique, late only when delivered, rate per order equals a recomputation from `raw_orders`, late <= delivered, FX range (warn).
**If asked "why dbt for this?"** The SQL is declarative, tests live next to the models in YAML, and `ref()` gives the build order for free. Quarantine stays in Python because moving rows with reasons is easier to test there.

## Stage 5: run_pipeline, run_log, idempotency

### run_pipeline.py
One command, five steps: make sure the store DB exists; extract (Olist, holidays, rates); stage + data tests + quarantine; `dbt build`; load checks (fact rows = staged items, dims = staged dims, orders in `vw_order_late` = in-scope orders).
- Everything printed also goes to `logs/pipeline_<timestamp>.log` (the `Tee` class).
- `finally:` always writes the `run_log` row, so a failed run is recorded. Exit code 0 ok, 2 stop test, 1 anything else.
- `--data-dir` lets tests run the same code on a temporary folder.
**If asked "why is it idempotent?"** Extraction upserts by key; staging and dbt drop and recreate their tables from the same inputs; the proof compares checksums.

### src/load/run_log.py and load_warehouse.py
`run_log` has one row per run (run_id, start, end, rows extracted/loaded/quarantined, tests passed/warned/failed, status, error). It only grows, so it is not in the checksums. `run_dbt` calls dbt as a subprocess with the warehouse path in an environment variable; `load_checks` compares the marts with staging.

### src/load/checksums.py
Row count plus the sum of a hash of every row: the same table in a different order gives the same number. `run_id` and `quarantined_at` are ignored because they change on every run on purpose.

### tests/test_pipeline.py
Runs the whole pipeline (including dbt) on 60 invented orders with the two APIs replaced by files: about 10 s per run.

## Stage 6: dashboard/app.py
- `read` opens the warehouse read-only and runs one SELECT on a view. `where_clause` turns the three order-level controls (state, year, Carnival) into a WHERE with parameters (never string-pasted values).
- Each view stores order counts per group; the app sums counts after filtering and divides once. **Why:** averaging pre-computed rates would weight small groups wrongly.
- Four callbacks share the same four inputs: header cards, chart 1 (distance and freight bands), chart 2 (seller share of late orders per route, heatmap), chart 3 (review score and revenue by category).
- The minimum-orders slider drops bars, routes or categories with too few orders. An empty result shows a message instead of an error.
- `DEFAULT_STATE` / `DEFAULT_YEAR` environment variables only set the starting values of the dropdowns (used for the screenshot).
**If asked "why a heatmap for chart 2?"** Routes are a state-by-state grid, and the share of seller-caused delay is one number per cell.

## Stage 7: CI and schedule
- `ci.yml`: on push and pull request, checkout, Python 3.13 with pip cache, `pip install -r requirements.txt`, `python -m pytest -q`. The tests include a small end-to-end pipeline run on invented data (no secrets, no real Olist files).
- `schedule.yml`: `cron: "0 5 * * *"` (daily 05:00 UTC) plus `workflow_dispatch` (manual button). The Kaggle credentials come from `secrets.*` into environment variables, which GitHub masks in logs; the pipeline downloads the data, runs, prints `run_log`, and uploads `warehouse.db` and `logs/` as an artifact (`if: always()` so a failed run still uploads its log).
**If asked "what does a scheduled run do on static data?"** It re-runs idempotently and writes a `run_log` row; the data does not change.

## Stage 8: src/report_numbers.py
Reads the warehouse (read-only) and writes `docs/report_numbers.json`: per-source counts, quarantine reasons, joins, test results, rates per order and per item, rankings with minimum volumes, distance bands, seller-vs-carrier shares, review scores, revenue on late orders, holiday effects (with and without Carnival), last two run times. **Why a script:** the report numbers can be regenerated after any run instead of being copied by hand.
