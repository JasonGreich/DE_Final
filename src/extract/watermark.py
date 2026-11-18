"""Watermark and overlap-window logic for incremental extraction.

The idea: remember the newest purchase timestamp we have seen (the "watermark").
On the next run we do not start at the watermark, we start 60 days earlier (the
"overlap window"), because an order changes after it is placed (its status and
delivery date are filled in later). Re-reading those orders lets us update them.

The pure functions at the top do no I/O, so they are easy to test.
The two small database functions at the bottom store the watermark in the
`extract_state` table.
"""
from datetime import datetime, timedelta

OVERLAP_DAYS = 60
TS_FORMAT = "%Y-%m-%d %H:%M:%S"  # the format Olist uses for timestamps


def overlap_start(watermark, days=OVERLAP_DAYS):
    """Timestamp to start extracting from: watermark minus the overlap window.

    watermark is a 'YYYY-MM-DD HH:MM:SS' string or None.
    Returns None when there is no watermark (first run: extract everything).
    """
    if watermark is None:
        return None
    start = datetime.strptime(watermark, TS_FORMAT) - timedelta(days=days)
    return start.strftime(TS_FORMAT)


def next_watermark(old_watermark, newest_seen):
    """The watermark to save after a run. It never moves backwards.

    old_watermark: the saved value (or None). newest_seen: the max purchase
    timestamp in this run's extract (or None if the run found no orders).
    Timestamps in this format compare correctly as plain strings.
    """
    candidates = [value for value in (old_watermark, newest_seen) if value is not None]
    return max(candidates) if candidates else None


def parse_as_of(text):
    """Check an --as-of value is a real date in YYYY-MM-DD form and return it as a string."""
    try:
        return datetime.strptime(text, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        raise ValueError(f"--as-of must be a date like 2017-06-30, got {text!r}")


# ---------------------------------------------------------------- extract_state table

def ensure_state_table(conn):
    """Create extract_state if it does not exist yet."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS extract_state ("
        "source TEXT PRIMARY KEY, watermark TEXT, updated_at TEXT)"
    )


def get_watermark(conn, source):
    """Saved watermark for a source, or None if we have never run."""
    row = conn.execute(
        "SELECT watermark FROM extract_state WHERE source = ?", (source,)
    ).fetchone()
    return row[0] if row else None


def save_watermark(conn, source, watermark, updated_at):
    """Insert or update the watermark row for a source (one row per source)."""
    conn.execute(
        "INSERT INTO extract_state (source, watermark, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(source) DO UPDATE SET watermark = excluded.watermark, "
        "updated_at = excluded.updated_at",
        (source, watermark, updated_at),
    )
