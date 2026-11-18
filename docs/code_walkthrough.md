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
