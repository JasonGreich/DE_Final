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
