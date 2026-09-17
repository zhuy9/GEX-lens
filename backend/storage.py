"""DuckDB initialization, latest/save/prune; no provider parsing."""

import contextlib
import csv
import json
import os
import tempfile
import threading
import uuid
from datetime import datetime
from pathlib import Path

import duckdb

from models import PricedQuote

_LOCK = threading.Lock()
_SCHEMA_SQL = (Path(__file__).parent / "schema.sql").read_text()
_KEEP_LATEST = 20  # PRD section 3: latest 20 snapshots per (source_mode, symbol)

# DuckDB's Python parameter binding (execute/executemany) costs ~0.5ms per
# bound value -- fine for one row, ruinous for thousands (a 2000-contract
# refresh would take 15s+, well past PRD 3.5's 5s budget). read_csv is
# DuckDB's actual bulk-load path and is ~1000x faster for this volume.
_OPTION_QUOTES_COLUMNS = {
    "snapshot_id": "UUID",
    "symbol": "VARCHAR",
    "expiration": "DATE",
    "strike": "DECIMAL(18,6)",
    "option_type": "VARCHAR",
    "bid": "DOUBLE",
    "ask": "DOUBLE",
    "last": "DOUBLE",
    "volume": "BIGINT",
    "open_interest": "BIGINT",
    "multiplier": "INTEGER",
    "provider_contract_id": "VARCHAR",
    "quote_asof": "VARCHAR",  # cast to TIMESTAMPTZ in the INSERT SELECT below
    "mid": "DOUBLE",
    "iv": "DOUBLE",
    "gamma": "DOUBLE",
    "exclusion_reason": "VARCHAR",
    "flags": "JSON",
}


def _csv_cell(value: object) -> object:
    if value is None:
        return ""
    if isinstance(value, tuple):
        return json.dumps(list(value))
    return value


def _row_dict(snapshot_id: uuid.UUID, pq: PricedQuote) -> dict[str, object]:
    q = pq.quote
    return {
        "snapshot_id": snapshot_id,
        "symbol": q.symbol,
        "expiration": q.expiration,
        "strike": q.strike,
        "option_type": q.option_type,
        "bid": q.bid,
        "ask": q.ask,
        "last": q.last,
        "volume": q.volume,
        "open_interest": q.open_interest,
        "multiplier": q.multiplier,
        "provider_contract_id": q.provider_contract_id,
        "quote_asof": q.quote_asof,
        "mid": pq.mid,
        "iv": pq.iv,
        "gamma": pq.gamma,
        "exclusion_reason": pq.exclusion_reason,
        "flags": q.flags,
    }


def _bulk_insert_option_quotes(
    conn: duckdb.DuckDBPyConnection, snapshot_id: uuid.UUID, priced_quotes: tuple[PricedQuote, ...]
) -> None:
    # Column order lives in exactly one place -- _OPTION_QUOTES_COLUMNS'
    # key order -- and drives the CSV row order, the read_csv() type map,
    # and both the INSERT target and SELECT column lists below. Previously
    # these were four independently hand-typed lists that all had to agree
    # by inspection; a silent reorder in any one of them would have
    # misrouted values into the wrong column without any error.
    column_names = list(_OPTION_QUOTES_COLUMNS)
    fd, csv_path = tempfile.mkstemp(suffix=".csv")
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            for pq in priced_quotes:
                row = _row_dict(snapshot_id, pq)
                writer.writerow(_csv_cell(row[name]) for name in column_names)
        columns_sql = ", ".join(
            f"'{name}': '{sql_type}'" for name, sql_type in _OPTION_QUOTES_COLUMNS.items()
        )
        target_cols = ", ".join(column_names)
        select_cols = ", ".join(
            f"{name}::TIMESTAMPTZ" if name == "quote_asof" else name for name in column_names
        )
        conn.execute(
            f"""
            INSERT INTO option_quotes ({target_cols})
            SELECT {select_cols}
            FROM read_csv(?, header=false, columns={{{columns_sql}}})
            """,
            [csv_path],
        )
    finally:
        # A cleanup failure here must not replace/hide an exception from the
        # block above -- suppress only this secondary failure.
        with contextlib.suppress(OSError):
            os.unlink(csv_path)


def init_schema(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        conn = duckdb.connect(db_path)
        try:
            conn.execute(_SCHEMA_SQL)
        finally:
            conn.close()


def health_check(db_path: str) -> None:
    """Confirm the database is reachable without decoding an analytical
    snapshot -- a health check has no reason to pay that cost."""
    with _LOCK:
        conn = duckdb.connect(db_path)
        try:
            conn.execute("SELECT 1")
        finally:
            conn.close()


def get_latest_dashboard(db_path: str, source_mode: str, symbol: str) -> dict | None:
    with _LOCK:
        conn = duckdb.connect(db_path)
        try:
            row = conn.execute(
                """
                SELECT dashboard_json FROM snapshots
                WHERE source_mode = ? AND symbol = ?
                ORDER BY collected_at DESC, snapshot_id DESC
                LIMIT 1
                """,
                [source_mode, symbol],
            ).fetchone()
        finally:
            conn.close()
    return None if row is None else json.loads(row[0])


def get_reference_cache(
    db_path: str, *, kind: str, provider_id: str, subject: str
) -> dict | None:
    """Latest cached fetch for this (kind, provider_id, subject), or None.
    The cache is an optimization (Section 8.1): callers decide TTL eligibility
    themselves from the returned fetched_at."""
    with _LOCK:
        conn = duckdb.connect(db_path)
        try:
            row = conn.execute(
                """
                SELECT fetched_at, normalized_json, raw_payload_json FROM reference_cache
                WHERE kind = ? AND provider_id = ? AND subject = ?
                """,
                [kind, provider_id, subject],
            ).fetchone()
        finally:
            conn.close()
    if row is None:
        return None
    fetched_at, normalized_json, raw_payload_json = row
    return {
        "fetched_at": fetched_at,
        "normalized_json": json.loads(normalized_json),
        "raw_payload_json": raw_payload_json,
    }


def upsert_reference_cache(
    db_path: str,
    *,
    kind: str,
    provider_id: str,
    subject: str,
    fetched_at: datetime,
    normalized_json: dict,
    raw_payload_json: str,
) -> None:
    """Replace this (kind, provider_id, subject)'s cached entry. Never
    called on a failed fetch, so a prior successful entry survives one."""
    with _LOCK:
        conn = duckdb.connect(db_path)
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO reference_cache
                    (kind, provider_id, subject, fetched_at, normalized_json, raw_payload_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [kind, provider_id, subject, fetched_at, json.dumps(normalized_json), raw_payload_json],
            )
        finally:
            conn.close()


def save_snapshot(
    db_path: str,
    *,
    source_mode: str,
    symbol: str,
    snapshot_id: uuid.UUID,
    collected_at: datetime,
    valuation_at: datetime,
    raw_payload_json: str,
    dashboard_json: dict,
    priced_quotes: tuple[PricedQuote, ...],
) -> None:
    """Insert one snapshot and its contracts, then prune to the newest
    _KEEP_LATEST for this (source_mode, symbol) pair, all in one transaction."""
    with _LOCK:
        conn = duckdb.connect(db_path)
        try:
            conn.execute("BEGIN TRANSACTION")
            conn.execute(
                """
                INSERT INTO snapshots (
                    snapshot_id, symbol, collected_at, valuation_at,
                    source_mode, raw_payload, dashboard_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    snapshot_id,
                    symbol,
                    collected_at,
                    valuation_at,
                    source_mode,
                    raw_payload_json,
                    # No default=str fallback: dashboard_json is already
                    # model_dump(mode="json") output, fully JSON-serializable
                    # on its own. Silently stringifying anything else would
                    # conceal a real serialization bug instead of raising it.
                    json.dumps(dashboard_json),
                ],
            )
            if priced_quotes:
                _bulk_insert_option_quotes(conn, snapshot_id, priced_quotes)

            stale = conn.execute(
                """
                SELECT snapshot_id FROM snapshots
                WHERE source_mode = ? AND symbol = ?
                ORDER BY collected_at DESC, snapshot_id DESC
                OFFSET ?
                """,
                [source_mode, symbol, _KEEP_LATEST],
            ).fetchall()
            for (stale_id,) in stale:
                conn.execute("DELETE FROM option_quotes WHERE snapshot_id = ?", [stale_id])
                conn.execute("DELETE FROM snapshots WHERE snapshot_id = ?", [stale_id])

            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
