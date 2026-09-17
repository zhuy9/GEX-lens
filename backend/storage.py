"""DuckDB initialization, latest/save/prune; no provider parsing."""

import json
import threading
import uuid
from datetime import datetime
from pathlib import Path

import duckdb

from models import PricedQuote

_LOCK = threading.Lock()
_SCHEMA_SQL = (Path(__file__).parent / "schema.sql").read_text()
_KEEP_LATEST = 20  # PRD section 3: latest 20 snapshots per (source_mode, symbol)


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
            for pq in priced_quotes:
                q = pq.quote
                conn.execute(
                    """
                    INSERT INTO option_quotes (
                        snapshot_id, symbol, expiration, strike, option_type,
                        bid, ask, last, volume, open_interest, multiplier,
                        provider_contract_id, quote_asof, mid, iv, gamma,
                        exclusion_reason, flags
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
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
                        json.dumps(list(q.flags)),
                    ],
                )

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
