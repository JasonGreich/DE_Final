# Decisions

## D1. dbt on SQLite (dbt-core + dbt-sqlite): WORKABLE, keep SQLite

**Date:** 2026-11-18 (stage 1 spike). **Status:** the spike passed; no switch to DuckDB proposed.

### What was tested
A throwaway dbt project (not in the repo) on a SQLite file holding a 5,000-order sample copied from the real
`olist.db`. Versions: dbt-core 1.11.17, dbt-sqlite 1.10.0, Python 3.13, Windows.

| Check | Result |
|---|---|
| `source()` pointing at tables in the same SQLite file | works |
| Staging model (view) and mart model (table) with `ref()` | works, `dbt build` runs both in order |
| `date()`, `julianday()`, `strftime()`, `CASE` | work (late flag and day differences computed correctly: 302 late of 4,871 delivered in the sample) |
| Window functions (`count() over`, `row_number() over (partition by ... order by ...)`) | work |
| Correlated subquery in a model | works |
| Generic tests `unique`, `not_null`, `accepted_values` | work |
| Singular test (SQL file, passes when it returns no rows) | works |
| Test with `severity: warn` that fails | reported as `WARN 129`, run continues, **exit code 0** |
| Test with `severity: error` that fails | reported as `FAIL 1`, **exit code 1** (so `run_pipeline.py` can stop on it) |
| Re-running `dbt build` | same row count (5,000) and same checksum of the mart table |

### Caveats
- **dbt-sqlite is community-maintained**, not by dbt Labs. It worked for everything we need, but it is the weaker link.
- **SQLite has no real schemas.** dbt-sqlite maps a schema name to a separate database file. We use a single schema (`main`) so the
  staging tables and the marts live in the same `warehouse.db`, which is what the project brief asks for (name prefixes `stg_`, `dim_`, `fact_`).
- **Install:** `pip install dbt-core dbt-sqlite` with no versions hung for 10+ minutes on Python 3.13 (stuck building a dependency).
  Installing `dbt-sqlite` alone resolved in about two minutes. `requirements.txt` pins the working pair.
- dbt prints "your version of dbt-core is out of date"; this is harmless.
- The spike did not test: very large tables (the real fact table is about 112,000 rows, small), custom generic tests written as macros
  (stage 4), or the quarantine step. Quarantine stays in Python (stage 3), which the project brief allows.

### Decision
Stay on SQLite + dbt-sqlite. If stage 4 hits a real blocker, we stop and discuss it as a team before touching DuckDB.
(For reference, the DuckDB change would be: `dbt-duckdb` instead of `dbt-sqlite`, warehouse file `warehouse.duckdb`, `julianday(a)-julianday(b)`
becomes `date_diff('day', b, a)`, and the pandas load code would write to DuckDB.)

## D2. Use Frankfurter with `base=BRL&quotes=USD` directly

The v2 endpoint returns BRL to USD directly (`rate` = USD per 1 BRL), so no inversion is needed.
Response is a flat JSON list, no pagination for 2016 to 2018 (768 rows in one call).
Observed: asking from 2016-01-01 also returns the previous business day (2015-12-31), which helps forward-fill at the start.

## D3. National holidays: filter on `global`, not on type

In Nager's Brazil data the single state holiday (Constitutionalist Revolution of 1932, 9 July, Sao Paulo) has type `Public`, with `global = false`.
So filtering on `types` alone would keep it. Rule: keep `global == true`, then split Carnival (types Bank+Optional) into its own flag (BR2).
