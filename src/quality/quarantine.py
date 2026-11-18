"""Quarantine: nothing is dropped silently.

A row that fails a test (or is removed during staging, for example an older duplicate
review) is collected here with the reason. At the end of the run the collector is
written to one table per source: quarantine_orders, quarantine_order_items, ...

Each quarantine row has: source_table, row_key, reason, run_id, quarantined_at and the
raw row as JSON. If one row fails for two reasons it is stored once, reasons joined by '; '.

The quarantine tables describe the LATEST run (they are rebuilt every run), so running
the pipeline twice gives the same tables.
"""
import json

SOURCES = ["orders", "order_items", "order_reviews", "geolocation", "holidays"]

COLUMNS = ("source_table TEXT, row_key TEXT, reason TEXT, run_id TEXT, "
           "quarantined_at TEXT, raw_row TEXT")


def rows_as_dicts(df):
    """DataFrame rows as plain dicts, with missing values as None (so JSON gets null)."""
    plain = df.astype(object).where(df.notna(), None)
    return plain.to_dict("records")


def make_key(record, key_cols):
    """Row key as text: the key columns joined by '|' (for example 'order_id|order_item_id')."""
    return "|".join(str(record[col]) for col in key_cols)


class Quarantine:
    def __init__(self, run_id, quarantined_at):
        self.run_id = run_id
        self.quarantined_at = quarantined_at
        # source -> row_key -> {"reasons": [...], "raw": {...}}
        self._rows = {source: {} for source in SOURCES}

    def add(self, source, df, key_cols, reason):
        """Quarantine every row of df for this reason. Returns how many rows were added."""
        for record in rows_as_dicts(df):
            key = make_key(record, key_cols)
            entry = self._rows[source].setdefault(key, {"reasons": [], "raw": record})
            if reason not in entry["reasons"]:
                entry["reasons"].append(reason)
        return len(df)

    def keys(self, source):
        """Set of row keys already quarantined for a source."""
        return set(self._rows[source])

    def count(self, source):
        """Number of quarantined rows for a source (0 for a source we never quarantine)."""
        return len(self._rows.get(source, {}))

    def reason_counts(self, source):
        """{reason: rows} for a source. A row with two reasons is counted under each."""
        counts = {}
        for entry in self._rows[source].values():
            for reason in entry["reasons"]:
                counts[reason] = counts.get(reason, 0) + 1
        return counts

    def remove_from(self, df, source, key_cols):
        """Return df without the rows that are quarantined for this source."""
        bad = self.keys(source)
        if not bad or df.empty:
            return df
        if len(key_cols) == 1:  # fast path: one key column, no row-by-row joining (geolocation has 1M rows)
            keys = df[key_cols[0]].astype(str)
        else:
            keys = df[key_cols].astype(str).agg("|".join, axis=1)
        return df[~keys.isin(bad)].reset_index(drop=True)

    def write(self, conn):
        """Rebuild all quarantine_<source> tables. The caller commits."""
        for source in SOURCES:
            table = f"quarantine_{source}"
            conn.execute(f"DROP TABLE IF EXISTS {table}")
            conn.execute(f"CREATE TABLE {table} ({COLUMNS})")
            rows = [
                (source, key, "; ".join(entry["reasons"]), self.run_id, self.quarantined_at,
                 json.dumps(entry["raw"], default=str, ensure_ascii=False, sort_keys=True))
                for key, entry in sorted(self._rows[source].items())  # sorted: same order every run
            ]
            conn.executemany(f"INSERT INTO {table} VALUES (?,?,?,?,?,?)", rows)
