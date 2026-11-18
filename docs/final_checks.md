# Final checks (stage 8)

## Fresh-clone check (2026-11-18)
Cloned the repo into a temporary folder, created a new virtual environment (Python 3.13.12) and followed the README.
- `pip install -r requirements.txt`: worked.
- Copied the Olist CSVs into `data/seed/` (the README's option 1; Kaggle download was not tested because this machine has no `.env`).
- `python run_pipeline.py`: **worked first time** (store built, 1,447,775 rows extracted, dbt 33 tests passed, exit code 0). A second run also exited 0.
- `python -m pytest -q`: **86 passed** in about 85 s.
- Things that needed a guess or are worth knowing:
  - The first run is not flagged `--full`, so data test 13 (at least 90,000 orders) is recorded as `skipped`; it only runs with `--full`. README says so.
  - Without `data/seed/` and without Kaggle credentials the pipeline stops with a clear message (tested in `tests/test_seed_store.py`); not re-tested from the clone.
  - `dashboard/app.py` was run from the original working copy, not from the clone.
  - GitHub Actions workflows have not run on GitHub yet.

## Secret scan
- No `.env` file exists in this working copy (the data was supplied as files), so there was no real key to search for.
- `.env`, `data/`, `.venv/`, `logs/` are git-ignored. `git ls-files` shows no `.env`, no `data/`, no `.csv`, `.db`, `.zip` or `kaggle.json`.
- Across all commits: no file with those names was ever added, and a pattern search for `KAGGLE_KEY`, `api_key`, `secret`, `password` or `token` followed by a 16+ character value found nothing.
- The only credential-like text is the empty template `.env.example`.
