"""Kalshi WebSocket ingestion — Phase 1D.

Per SP Architecture v1.3 §6.4. Taps the legacy kalshi_ws.run_ws_client's
LIVE_PRICES dict (always-current price state per ticker), snapshots
it every ~1s, diffs against the previous snapshot, and bulk-writes
changes to sp.kalshi_markets.

Zero modification to legacy kalshi_ws.py — we read its public
LIVE_PRICES dict and don't influence its behavior. One WebSocket
connection serves both the legacy in-process consumers and this
ingestion path.

Schema (architecture §5.2): WS price updates are merged into the
existing kalshi_markets.raw_payload via PostgreSQL's `||` operator
(JSONB concatenation, where right-side keys override). This keeps
raw_payload as a single canonical record per ticker — REST writes
the snapshot, WS overlays fresh prices. The provider record stays
queryable as a unified shape.

Cadence: 1-second snapshot loop. Configurable via the `interval_sec`
parameter. With Kalshi's WS update rate (sub-second per ticker) and
~7000 active tickers, expect 50-500 changed tickers per snapshot
during normal market hours.

Polling fallback: not needed here. The legacy kalshi_ws client
already handles WS reconnection with exponential backoff. If the
WS is disconnected, LIVE_PRICES still holds the last known state
and our diff produces zero changes — silently a no-op. The 30s REST
ingestion in kalshi.py handles freshness during prolonged WS
outages.

Singleton enforcement: shares ADVISORY_LOCK_KALSHI_WS (distinct from
ADVISORY_LOCK_KALSHI for the REST loop). With WEB_CONCURRENCY=2,
only one worker writes WS updates.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from observability import get_logger

from .base import (
    new_run_id,
    try_acquire_advisory_lock,
)


_log = get_logger("ingestion.kalshi_ws")


# Distinct from ADVISORY_LOCK_KALSHI (REST loop). Both can run in
# the same worker without contention.
ADVISORY_LOCK_KALSHI_WS = 0x5350_F104


# Fields we care about from each WS price update. Anything else in
# the LIVE_PRICES value dict is ignored. Kalshi's "ticker" channel
# emits these via _extract_update in legacy kalshi_ws.py.
_WS_PRICE_FIELDS = (
    "yes_bid", "yes_ask", "no_bid", "no_ask",
    "last_price", "volume", "open_interest",
)


def _extract_price_dict(live_value: Any) -> Optional[dict]:
    """Pull the canonical price fields out of a LIVE_PRICES value.

    Returns a dict with ONLY fields that are present (no None
    placeholders), so JSONB concatenation doesn't write nulls into
    raw_payload that would clobber values from the REST snapshot.

    Returns None if no price fields are present — caller skips the
    ticker.
    """
    if not isinstance(live_value, dict):
        return None
    out = {
        k: live_value[k]
        for k in _WS_PRICE_FIELDS
        if k in live_value and live_value[k] is not None
    }
    return out or None


async def _flush_changes(
    session: AsyncSession,
    changes: dict[str, dict],
) -> int:
    """Bulk-merge WS price updates into sp.kalshi_markets.raw_payload.

    Uses a single UPDATE ... FROM (VALUES ...) statement so all N
    ticker updates land in one round trip. Postgres handles the
    per-row merge via the JSONB `||` operator: existing raw_payload
    keys win unless overridden by the WS update, which is what we
    want (REST snapshot stays authoritative for non-price fields).

    Returns the count of rows actually updated. Tickers that don't
    exist in sp.kalshi_markets yet (REST ingestion hasn't seen them)
    are silently skipped — they'll get the price baseline when REST
    catches up.
    """
    if not changes:
        return 0

    # Build the VALUES list as Python params; SQLAlchemy will
    # parameterize properly. Each row: (ticker, prices_jsonb_str).
    rows = [
        {"ticker": t, "prices": json.dumps(p)}
        for t, p in changes.items()
    ]

    # UPDATE ... FROM (VALUES ...) AS v(ticker, prices) is the
    # canonical Postgres pattern for bulk per-row updates. Single
    # round trip regardless of len(changes).
    #
    # NOTE: we cannot pass a list of dicts to text().execute here
    # because we need a single statement, not executemany. Instead,
    # construct the VALUES inline via :param expansion, then bind
    # the params as one list. Use a positional bind for the array.
    if len(rows) == 1:
        # Special-case: single-row UPDATE is cleaner without the
        # FROM (VALUES ...) ceremony.
        row = rows[0]
        result = await session.execute(
            text(
                "UPDATE sp.kalshi_markets "
                "SET raw_payload    = raw_payload || (:prices)::jsonb, "
                "    last_seen_at   = NOW(), "
                "    last_changed_at = NOW() "
                "WHERE ticker = :ticker"
            ),
            row,
        )
        return result.rowcount or 0

    # Multi-row: build a (VALUES (...), (...), ...) clause with
    # numbered placeholders so the binding is unambiguous.
    placeholders = []
    bindings: dict[str, Any] = {}
    for i, row in enumerate(rows):
        placeholders.append(f"(:t{i}, :p{i})")
        bindings[f"t{i}"] = row["ticker"]
        bindings[f"p{i}"] = row["prices"]
    values_clause = ", ".join(placeholders)
    sql = (
        "UPDATE sp.kalshi_markets AS km "
        "SET raw_payload     = km.raw_payload || (v.prices)::jsonb, "
        "    last_seen_at    = NOW(), "
        "    last_changed_at = NOW() "
        f"FROM (VALUES {values_clause}) AS v(ticker, prices) "
        "WHERE km.ticker = v.ticker"
    )
    result = await session.execute(text(sql), bindings)
    return result.rowcount or 0


async def _snapshot_loop(
    session_factory,
    interval_sec: float = 1.0,
) -> None:
    """Poll LIVE_PRICES every interval_sec; diff vs prior snapshot;
    bulk-flush changes to sp.kalshi_markets.

    Diff strategy: simple equality check on the price-fields dict.
    Since legacy kalshi_ws merges incremental updates into LIVE_PRICES
    in-place, the dict's identity is stable per ticker; we compare
    by content. False positives (no actual change) are unlikely
    because the legacy code only writes when a field changes — but
    if they happen, the SQL UPDATE is cheap (no last_changed_at
    bump beyond what we'd do anyway in this minute).
    """
    try:
        from kalshi_ws import LIVE_PRICES
    except Exception as exc:
        _log.warning(
            "ingestion.kalshi_ws.import_failed",
            error_class=type(exc).__name__,
            error_msg=str(exc)[:200],
        )
        return

    prior_snapshot: dict[str, dict] = {}

    while True:
        run_id = new_run_id()
        started = time.monotonic()

        # Snapshot the dict (shallow copy of keys + values so we
        # don't race with the WS thread mutating it).
        try:
            current_snapshot = {
                t: dict(v) for t, v in LIVE_PRICES.items()
                if isinstance(v, dict)
            }
        except RuntimeError:
            # "dictionary changed size during iteration" — rare but
            # possible since LIVE_PRICES is mutated by the WS task.
            # Skip this tick; try again next interval.
            await asyncio.sleep(interval_sec)
            continue

        # Diff: tickers whose extracted price-dict differs from prior.
        changes: dict[str, dict] = {}
        for ticker, live_v in current_snapshot.items():
            new_prices = _extract_price_dict(live_v)
            if new_prices is None:
                continue
            old_prices = prior_snapshot.get(ticker)
            if old_prices != new_prices:
                changes[ticker] = new_prices

        if changes:
            try:
                async with session_factory() as session:
                    rows_updated = await _flush_changes(session, changes)
                    await session.commit()
                _log.info(
                    "ingestion.kalshi_ws.flush",
                    run_id=str(run_id),
                    changes=len(changes),
                    rows_updated=rows_updated,
                    tickers_in_live=len(current_snapshot),
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
            except Exception as exc:
                _log.warning(
                    "ingestion.kalshi_ws.flush_failed",
                    run_id=str(run_id),
                    changes=len(changes),
                    error_class=type(exc).__name__,
                    error_msg=str(exc)[:300],
                )
                # Don't update prior_snapshot on failure — retry next tick.
                await asyncio.sleep(interval_sec)
                continue

        # Update prior_snapshot to the price-only view (smaller; no
        # need to compare full live_v dicts which include other fields).
        prior_snapshot = {
            t: _extract_price_dict(v)
            for t, v in current_snapshot.items()
            if _extract_price_dict(v) is not None
        }

        await asyncio.sleep(interval_sec)


async def run(session_factory) -> None:
    """Top-level WS ingestion entry. Acquires the singleton lock,
    then runs the snapshot loop.

    Returns when cancelled. Crashes inside the loop are caught by
    the surrounding supervisor (ingestion.base.supervise).
    """
    async with session_factory() as lock_session:
        got_lock = await try_acquire_advisory_lock(
            lock_session, ADVISORY_LOCK_KALSHI_WS,
        )
        if not got_lock:
            _log.info(
                "ingestion.kalshi_ws.skipping",
                reason="another worker holds the Kalshi WS ingestion advisory lock",
            )
            return

        _log.info(
            "ingestion.kalshi_ws.starting",
            cadence={"snapshot_sec": 1},
        )

        await _snapshot_loop(session_factory, interval_sec=1.0)
