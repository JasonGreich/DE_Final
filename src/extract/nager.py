"""Extract Brazilian public holidays from the Nager.Date API.

One call per year (2016 to 2018). Each response is saved exactly as received
(JSON, timestamped) in data/raw/ and one manifest line is written per call.
Holidays are tiny, so every run extracts all three years again; staging uses the
newest file. Filtering (keep only national holidays) is NOT done here: raw means untouched.

Run:  python -m src.extract.nager
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from src.extract import manifest
from src.extract.http import ExtractError, http_get

ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw"
URL = "https://date.nager.at/api/v3/PublicHolidays/{year}/BR"
YEARS = (2016, 2017, 2018)


def extract_holidays(years=YEARS, raw_dir=RAW_DIR, getter=None, sleep=None, now=None):
    """Download holidays for each year. Returns {year: number_of_records}."""
    now = now or datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    options = {}
    if getter is not None:  # tests pass a fake getter; real runs use the default
        options["getter"] = getter
    if sleep is not None:
        options["sleep"] = sleep

    counts = {}
    for year in years:
        url = URL.format(year=year)
        response = http_get(url, **options)
        try:
            records = json.loads(response.content)
        except ValueError:
            raise ExtractError(f"{url} did not return JSON")
        if not isinstance(records, list) or not records:
            raise ExtractError(f"{url} returned no holidays; refusing to continue")
        path = raw_dir / f"nager_holidays_{year}_{stamp}.json"
        path.write_bytes(response.content)  # exactly the bytes we received
        manifest.append_manifest(raw_dir, "nager.public_holidays", url,
                                 now.isoformat(timespec="seconds"), len(records), path, year=year)
        counts[year] = len(records)
        print(f"  Nager {year}: {len(records)} holidays -> {path.name}")
    return counts


if __name__ == "__main__":
    extract_holidays()
