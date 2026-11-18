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
