"""DuckDB initialization, latest/save/prune; no provider parsing."""

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


def _bulk_insert_option_quotes(
    conn: duckdb.DuckDBPyConnection, snapshot_id: uuid.UUID, priced_quotes: tuple[PricedQuote, ...]
) -> None:
    fd, csv_path = tempfile.mkstemp(suffix=".csv")
    try:
        with os.fdopen(fd, "w", newline="") as f:
            writer = csv.writer(f)
            for pq in priced_quotes:
                q = pq.quote
                writer.writerow(
                    _csv_cell(v)
                    for v in (
                        snapshot_id,
                        q.symbol,
                        q.expiration,
                        q.strike,
                        q.option_type,
                        q.bid,
                        q.ask,
                        q.last,
                        q.volume,
                        q.open_interest,
                        q.multiplier,
                        q.provider_contract_id,
                        q.quote_asof,
                        pq.mid,
                        pq.iv,
                        pq.gamma,
                        pq.exclusion_reason,
                        q.flags,
                    )
                )
        columns_sql = ", ".join(
            f"'{name}': '{sql_type}'" for name, sql_type in _OPTION_QUOTES_COLUMNS.items()
        )
        conn.execute(
            f"""
            INSERT INTO option_quotes
            SELECT
                snapshot_id, symbol, expiration, strike, option_type,
                bid, ask, last, volume, open_interest, multiplier,
                provider_contract_id, quote_asof::TIMESTAMPTZ, mid, iv, gamma,
                exclusion_reason, flags
            FROM read_csv(?, header=false, columns={{{columns_sql}}})
            """,
            [csv_path],
        )
    finally:
        os.unlink(csv_path)


def init_schema(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        conn = duckdb.connect(db_path)
        try:
            conn.execute(_SCHEMA_SQL)
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
                    json.dumps(dashboard_json, default=str),
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
