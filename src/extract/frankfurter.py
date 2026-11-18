"""Extract BRL to USD exchange rates from the Frankfurter v2 API (ECB data).

One call returns the whole 2016-2018 series as a flat JSON list; the API did not
paginate for this range when we profiled it (768 rows). We still check that the
data reaches both ends of the range, so a truncated answer is an error and not a
silent gap. The response is saved exactly as received.

ECB publishes no rates on weekends or ECB holidays. We do not fill those here
(raw stays raw); the warehouse forward-fills from the last business day.

Run:  python -m src.extract.frankfurter
"""
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

from src.extract import manifest
from src.extract.http import ExtractError, http_get

ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw"
URL = "https://api.frankfurter.dev/v2/rates"
PARAMS = {"base": "BRL", "quotes": "USD", "from": "2016-01-01", "to": "2018-12-31",
          "providers": "ecb"}
SLACK_DAYS = 7  # the series may start/end a few days inside the range (weekends, holidays)


def check_coverage(records, date_from, date_to):
    """Raise ExtractError if the rows do not reach both ends of the requested range."""
    if not isinstance(records, list) or not records:
        raise ExtractError("Frankfurter returned no rates")
    days = sorted(r["date"] for r in records)
    first, last = date.fromisoformat(days[0]), date.fromisoformat(days[-1])
    if first > date.fromisoformat(date_from) + timedelta(days=SLACK_DAYS):
        raise ExtractError(f"rates start at {first}, expected about {date_from}")
    if last < date.fromisoformat(date_to) - timedelta(days=SLACK_DAYS):
        raise ExtractError(f"rates end at {last}, expected about {date_to} (truncated response?)")


def extract_rates(raw_dir=RAW_DIR, params=None, getter=None, sleep=None, now=None):
    """Download the rate series. Returns the number of records."""
    params = params or PARAMS
    now = now or datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    options = {}
    if getter is not None:
        options["getter"] = getter
    if sleep is not None:
        options["sleep"] = sleep

    response = http_get(URL, params=params, **options)
    try:
        records = json.loads(response.content)
    except ValueError:
        raise ExtractError(f"{URL} did not return JSON")
    check_coverage(records, params["from"], params["to"])
    path = raw_dir / f"frankfurter_brl_usd_{stamp}.json"
    path.write_bytes(response.content)  # exactly the bytes we received
    manifest.append_manifest(raw_dir, "frankfurter.brl_usd", f"{URL}?{urlencode(params)}",
                             now.isoformat(timespec="seconds"), len(records), path)
    print(f"  Frankfurter: {len(records)} rates -> {path.name}")
    return len(records)


if __name__ == "__main__":
    extract_rates()
