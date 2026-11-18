"""Append one line per extraction to data/raw/manifest.jsonl.

JSONL means one JSON object per line, so we can append without rewriting the file.
Each line says: what was extracted (source), from where (URL or SQL), when,
how many records, which file it was saved to, and any extra facts (for Olist
the watermark and window used).
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw"
MANIFEST_NAME = "manifest.jsonl"


def display_path(path):
    """Show a path relative to the project when possible (so the manifest is portable)."""
    path = Path(path).resolve()
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def append_manifest(raw_dir, source, url_or_query, extracted_at, records, file_path, **extra):
    """Write one manifest line and return it as a dict."""
    entry = {
        "source": source,
        "url_or_query": url_or_query,
        "extracted_at": extracted_at,
        "records": records,
        "file": display_path(file_path),
    }
    entry.update(extra)  # e.g. watermark, window_start, as_of
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    with open(raw_dir / MANIFEST_NAME, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")
    return entry


def read_manifest(raw_dir):
    """Return all manifest lines as a list of dicts (empty list if no file yet)."""
    path = Path(raw_dir) / MANIFEST_NAME
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]
