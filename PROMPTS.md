# Claude Code build prompts

How to use this file
1. Create an empty folder for the repo, put `CLAUDE.md` (the brief) at its root, run `git init`, and start Claude Code there. Claude Code reads CLAUDE.md automatically.
2. Paste the prompts below one at a time, in order. Each ends with a stop point. Read the summary, run the commands it shows, then paste the next prompt.
3. Split the work between the two of you: one person runs stages 0 to 3 and 5, the other runs 4, 6 and 7 (swap if you like), so the commit history shows both. Each person commits from their own machine or git identity. Everyone reads the other half before the presentation.
4. Do not paste secrets into prompts. Kaggle credentials go in `.env` only.
5. When all stages pass, bring the repo (or its `docs/` folder plus a screenshot of the dashboard) back here. I will evaluate it, then we write the report and the slides.

---

## Stage 0: scaffold, environment, data download

```
Read CLAUDE.md fully. Then do stage 0 only.

1. Create the repository layout from CLAUDE.md (empty modules with docstrings are fine), .gitignore (must exclude .env, data/, __pycache__, .venv, dbt target and logs folders), requirements.txt with pinned versions (pandas, requests, python-dotenv, dash, plotly, pytest, dbt-core and dbt-sqlite, and whatever the Kaggle download needs), and .env.example listing KAGGLE_USERNAME and KAGGLE_KEY (check the current Kaggle docs for the right variable names and tell me what you found).
2. Write src/extract/seed_store.py:
   - Download the Olist dataset from Kaggle using code and the credentials in .env. If that fails or credentials are missing, print a clear message telling me to put the zip or the nine CSVs in data/seed/ and continue from there.
   - Create data/store/olist.db with the nine tables, correct types, primary keys and foreign keys (orders, customers, order_items, products, sellers, order_reviews, order_payments, geolocation, product_category_name_translation). Keep values exactly as in the CSVs. Do not clean anything here; this database plays the store's operational system.
   - Re-running must rebuild the same database (drop and recreate), and print row counts per table.
3. Write a small tests/test_seed_store.py that checks the table list and that order_items.order_id all exist in orders.
4. Create docs/AI_USAGE.md and docs/code_walkthrough.md with a first entry for this stage.
5. Do not install anything globally; use a virtual environment and tell me the exact commands to create it.

Show me the row counts and the pytest output. Commit. Stop.
```

## Stage 1: profiling and the dbt decision

```
Do stage 1 only.

1. Profile every source before building anything else. Write src/profile.py (plain pandas/SQL, runnable from the command line) that prints and saves to docs/profiling.md, per table: row count, columns and types, null counts, duplicate keys, min and max dates, status counts, and the specific quirks listed in CLAUDE.md (missing categories, delivered without delivery date, delivery before purchase, multiple reviews per order, geolocation rows per zip prefix, coordinates outside Brazil). Include the numbers, not just the method.
2. Pull the holidays (2016-2018) and the BRL/USD rates once for profiling (read the current Frankfurter docs for the exact v2 call and pagination; use providers=ecb). Report: holiday count, which are Public versus Bank/Optional, which are not global, the date range of the rates, the number of weekend or missing days.
3. dbt spike: in a throwaway folder, check that dbt-core with the dbt-sqlite adapter can run one staging model and one mart model on a SQLite file, run a test with severity warn and one with severity error, and that the SQLite functions we need (date(), julianday(), CASE, window functions) work. Write the result in docs/decisions.md. If dbt with SQLite is not workable, say so plainly and propose the DuckDB alternative with the exact changes needed. Do not switch on your own.

Summarize what profiling revealed (it feeds the report) and the dbt verdict. Commit. Stop.
```

## Stage 2: extraction (raw layer, manifest, watermark)

```
Do stage 2 only. Follow CLAUDE.md sections "Sources", "Incremental extraction" and "Layers".

1. src/extract/olist.py: extract orders, order_items, order_reviews (and the dimension tables: customers, sellers, products, geolocation, category translation) from data/store/olist.db with SQL. Orders are incremental by order_purchase_timestamp with a watermark saved in the extract_state table and a 60-day overlap window. Support --full and --as-of YYYY-MM-DD. Each extract is saved untouched under data/raw/ with a timestamp in the file name (Parquet or CSV, your choice, tell me why).
2. src/extract/nager.py: years 2016 to 2018, Brazil, with timeouts, retries with backoff, an honest User-Agent, and clear errors. Save each response as received (JSON) in data/raw/.
3. src/extract/frankfurter.py: BRL to USD, 2016-01-01 to 2018-12-31, providers=ecb, handle pagination if the API paginates. Save responses untouched.
4. src/extract/manifest.py: append one line to data/raw/manifest.jsonl per extraction: source, URL or SQL, extracted_at, records, file path, and for Olist the watermark used.
5. Tests: watermark and overlap-window calculation (pure function), and an integration test with a tiny SQLite store that shows (a) a second run does not duplicate orders and (b) an order whose status I change in the store is updated on the next run.

Show: manifest contents after two runs, and the pytest output. Commit. Stop.
```

## Stage 3: staging, joins and quarantine

```
Do stage 3 only. Follow CLAUDE.md "Business rules" and "Data tests".

1. src/transform/staging.py: one stg_ table per source group (orders, order_items, customers, sellers, products with English category, reviews reduced to one per order, geolocation aggregated to one row per zip prefix, holidays, fx rates). Rename, cast types, standardize values, turn placeholders into nulls, remove duplicates, flag invalid rows. Write stg_ tables into data/warehouse/warehouse.db. Write them idempotently (replace by key, never append twice).
2. src/transform/geo.py: haversine in plain Python, with a unit test against a known distance. src/transform/rules.py: BR1 to BR4 as small pure functions with unit tests (late rule with date-only comparison, holiday window, delay cause, per-order rate with a three-item order counted once).
3. src/quality/tests.py and quarantine.py: implement the 15 data tests from CLAUDE.md with their severities. stop aborts the run with a clear message, quarantine moves rows to quarantine_* tables with source_table, row_key, reason, run_id, quarantined_at and the raw row as JSON, warn only logs. Every test writes a row to dq_results.
4. Every join: log row counts before and after, and assert the expected relationship (for example orders to customers is 1:1, order_items to orders is many-to-one, geolocation join does not change the row count). If a join changes row counts unexpectedly, stop the run.
5. Print a reconciliation per source: rows extracted = rows staged + rows quarantined + rows out of scope. If it does not add up, fail.

Show: the reconciliation table with real numbers, the dq_results table, and pytest output. Commit. Stop.
```

## Stage 4: dbt marts and tests (extension 1)

```
Do stage 4 only. Use the decision recorded in docs/decisions.md (SQLite with dbt-sqlite unless we changed it).

1. Create dbt_project/ with sources.yml (the stg_ tables as source()), staging views if useful, and mart models using ref(): dim_date (with holiday and brl_usd_rate columns, 2016-09-01 to 2018-12-31, forward-filled rates), dim_product, dim_seller, dim_customer, fact_order_item at the grain stated in CLAUDE.md, with all measures and flags. Then the order-level view vw_order_late and one view per dashboard chart.
2. schema.yml tests: unique and not_null on every key, relationships from the fact table to every dimension (all three date keys to dim_date), accepted_values for delay_cause and order_status, plus custom tests for "rate per order equals independent recomputation" and "is_late only when delivered". Set severity (error or warn) in YAML to match CLAUDE.md. Where dbt cannot do the quarantine step, keep the Python quarantine from stage 3 and say clearly in docs/architecture.md which layer does what.
3. Add a README section "dbt" with the exact commands (dbt deps/build or run/test) and how run_pipeline.py calls dbt.
4. Verify by running dbt build twice: same row counts and a checksum per table.

Show: the model list, test results, row counts of each mart table, and one sanity query: late rate per order overall and by customer region, computed in SQL. Commit. Stop.
```

## Stage 5: orchestration, idempotent load, logging, run-log

```
Do stage 5 only.

1. run_pipeline.py: one command that runs extract, transform, test, dbt build, load checks, in order, with --full and --as-of options, writes logs/pipeline_<timestamp>.log, and exits with a non-zero code on a stop test.
2. src/load/run_log.py: a run_log table with run_id, started_at, finished_at, rows_extracted, rows_loaded, rows_quarantined, tests_failed, status. One row per run, written even if the run fails.
3. Idempotency proof: write tests/test_idempotent.py (or a script) that runs the pipeline twice on the same input and compares row counts and an order-independent checksum of every dim_ and fact_ table. Save the output in docs/idempotency_proof.txt.
4. Print a final summary block at the end of each run: rows in, loaded, quarantined, tests passed/warned/failed, duration.

Show the summary of two consecutive runs and the idempotency proof. Commit. Stop.
```

## Stage 6: Dash dashboard

```
Do stage 6 only. Follow CLAUDE.md "Dashboard (Dash)".

Build dashboard/app.py reading only from the warehouse views. Three charts, controls for customer state, year, Carnival toggle and minimum orders, and at least two callbacks that really change the charts. Add the note "rate per order, orders counted once" under each chart. Add a header block with the overall late rate, delivered orders, and the number of orders set aside. Keep the styling simple and readable. Handle an empty selection without crashing. Read the official Dash tutorial structure (https://dash.plotly.com/tutorial) but write your own code.

Run it, take screenshots of each chart state you can (default and one filter), save them in docs/screenshots/, and tell me the three most striking findings with their numbers. Commit. Stop.
```

## Stage 7: unit tests, CI, scheduling (extension 2)

```
Do stage 7 only.

1. Make sure pytest has at least 9 unit tests covering the list in CLAUDE.md, and that they run fast with a small fixture, not the full Olist data. Put the fixture in tests/fixtures/ (synthetic or a few dozen invented rows; do not commit real Olist files).
2. .github/workflows/ci.yml: on every push, install requirements, run pytest, and run a small end-to-end pipeline on the fixture.
3. .github/workflows/schedule.yml: a daily schedule (cron) plus manual dispatch. It runs the pipeline and uploads the warehouse and logs as an artifact. Olist needs Kaggle credentials, so read them from GitHub Actions secrets (KAGGLE_USERNAME, KAGGLE_KEY), and explain in the README how to add them. Explain honestly in docs/architecture.md that the Olist data is static, so a scheduled run is an idempotent re-run that still writes a run_log row.
4. Never print secrets in logs.

Show the workflow files and explain every line in docs/code_walkthrough.md. Commit. Stop.
```

## Stage 8: README, docs, final checks

```
Do stage 8 only.

1. README.md: the question, the three sources with links and terms, how to run from a fresh clone (virtual environment, .env, python run_pipeline.py, dashboard command, pytest, dbt), one dashboard screenshot, and the repository layout.
2. docs/architecture.md with a diagram (Mermaid is fine) from sources through raw, staging, quarantine and warehouse to the dashboard. docs/schema.md with a star schema diagram, the stated grain and why. docs/business_rules.md complete with examples.
3. docs/report_numbers.json: every number the report needs: rows extracted, staged, quarantined (by reason) and loaded per source; join row counts before and after; data test results with severities; dim and fact row counts; the overall late rate per order and the same rate per item (to show the difference); top categories, sellers and states by late rate with minimum volume; distance and freight findings; seller versus carrier shares; review score late versus on time; revenue on late orders in USD; holiday window effect with and without Carnival; run times of two consecutive runs.
4. Fresh-clone check: clone the repo into a temporary folder, follow the README exactly, and report every place it failed or needed a guess. Fix the README until it works.
5. Grep the repo and git history for secrets (.env values, Kaggle keys). Report the result.
6. Finish docs/code_walkthrough.md and docs/AI_USAGE.md so each module has: purpose, key functions, one thing to say if asked "why did you do it this way".

Do not tag v1.0 yet. Commit. Tell me exactly what remains and anything that deviates from CLAUDE.md. Stop.
```

---

## When you come back to me

Send: the repo (or a zip without data/ and .env), `docs/report_numbers.json`, `docs/profiling.md`, the dashboard screenshots, and the last two run summaries. I will check it against the grading rubric (extraction 15, transformation 12, data quality 8, warehouse 10, dashboard 5, engineering 5), look for the common traps (late rate per item instead of per order, joins that multiply rows, silent drops, secrets, non-idempotent loads), and then write the 8 to 12 page report and the 15-minute deck.
