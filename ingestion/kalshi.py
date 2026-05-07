"""Kalshi REST ingestion module.

Phase 1C per SP Architecture v1.3 §11.2. Reads from the Kalshi
cache populated by the legacy poller (main._cache['data_all']),
parses each ticker via kalshi_identity.parse_ticker(), UPSERTs into
sp.kalshi_markets with extracted abbr_block / parsed_home_abbr /
parsed_away_abbr fields ready for the resolver.

Same coupling pattern Phase 1B used for FL: the ingestion module
hooks into the legacy fetcher rather than duplicating auth +
pagination. This keeps Phase 1C focused; a direct Kalshi REST
poller can replace this read-from-cache path in Phase 2 if isolation
becomes more important than code reuse.

Cadence (architecture §6.2):
  * Markets >24h from kickoff: 30–60s
  * Active market prices (websocket primary in Phase 1D): 2–5s
    REST fallback when WS is disconnected. Phase 1C uses 30s as a
    middle ground until 1D's WS supersedes the hot path.

Singleton enforcement: Postgres advisory lock with key
ADVISORY_LOCK_KALSHI. With WEB_CONCURRENCY≥2, only one worker writes.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from observability import get_logger
from sp_models import KalshiMarket

from .base import (
    ADVISORY_LOCK_KALSHI,
    IngestionResult,
    new_run_id,
    try_acquire_advisory_lock,
    upsert_provider_records_batch,
)
from .schema_validation import (
    KalshiMarketValidator,
    validate_or_drift,
)


_log = get_logger("ingestion.kalshi")


# ── Ticker parsing → kalshi_markets fields ──────────────────────

def _extract_resolver_fields(record: dict) -> dict:
    """Run kalshi_identity.parse_ticker on the record and return the
    fields the resolver will key on.

    Returns dict with: market_type, series_ticker, abbr_block,
    parsed_home_abbr, parsed_away_abbr. Missing values are None.

    market_type comes from Identity.kind:
      per_fixture → 'game'   (or finer when series suffix narrows it)
      per_leg     → 'leg'
      series      → 'series'
      tournament  → 'tournament'
      outright    → 'outright'
      unparsed    → 'unparsed'

    The finer per_fixture classification (game vs total vs spread vs
    btts) is encoded in series_ticker suffix — the resolver doesn't
    need that distinction at the matching layer; the serving layer
    can derive it from series_ticker if needed.
    """
    from kalshi_identity import parse_ticker

    event_ticker = record.get("event_ticker") or ""
    series_ticker = record.get("series_ticker") or ""
    sport = record.get("_sport") or ""

    ident = parse_ticker(event_ticker, series_ticker, sport)

    market_type_map = {
        "per_fixture": "game",
        "per_leg":     "leg",
        "series":      "series",
        "tournament":  "tournament",
        "outright":    "outright",
        "unparsed":    "unparsed",
    }
    market_type = market_type_map.get(ident.kind, "unknown")

    abbr_block = ident.abbr_block or None
    home_abbr = None
    away_abbr = None
    # abbr_block is a concatenation; we don't split it here because
    # the home/away orientation is ambiguous from the ticker alone.
    # The resolver disambiguates using FL's SHORTNAME_HOME/AWAY plus
    # alias table lookups. For Phase 1C we just store abbr_block as
    # a whole; parsed_home_abbr / parsed_away_abbr are reserved for
    # cases where Kalshi later ships a structured representation.

    return {
        "market_type":      market_type,
        "series_ticker":    series_ticker or None,
        "abbr_block":       abbr_block,
        "parsed_home_abbr": home_abbr,
        "parsed_away_abbr": away_abbr,
    }


# ── Cadence ──────────────────────────────────────────────────────

async def _ingest_pass(session: AsyncSession) -> IngestionResult:
    """One pass: walk the legacy Kalshi cache, UPSERT each record.

    Reads main._cache['data_all'] (or 'data' as fallback) — same
    snapshot the legacy v3 serving path uses. This is the staging
    point for Phase 1C; Phase 2's resolver will read from
    sp.kalshi_markets directly.

    If the cache is empty (cold-start before any user has hit the
    legacy poller), trigger get_data() on a thread so we don't
    block the asyncio event loop, and retry once. After that, if
    still empty, log and return — the next pass tries again.
    """
    import asyncio
    import main as _main_mod
    run_id = new_run_id()
    result = IngestionResult()
    started = time.monotonic()

    cache = _main_mod._cache
    records = cache.get("data_all") or cache.get("data") or []
    if not records:
        # Cold-cache priming. Run the legacy fetcher in a thread so
        # the event loop can keep handling other tasks (FL ingestion,
        # serving, etc.) while Kalshi's slow REST pagination runs.
        # Bound with a timeout so a hung Kalshi call can't pin us.
        _log.info(
            "ingestion.kalshi.cache_warming",
            run_id=str(run_id),
            note="legacy cache empty; triggering get_data() in executor",
        )
        loop = asyncio.get_event_loop()
        try:
            await asyncio.wait_for(
                loop.run_in_executor(None, _main_mod.get_data),
                timeout=90.0,
            )
        except asyncio.TimeoutError:
            _log.warning(
                "ingestion.kalshi.cache_warm_timeout",
                run_id=str(run_id),
                note="get_data() did not complete within 90s; will retry next pass",
            )
        except Exception as exc:
            _log.warning(
                "ingestion.kalshi.cache_warm_failed",
                run_id=str(run_id),
                error_class=type(exc).__name__,
                error_msg=str(exc)[:300],
            )
        records = cache.get("data_all") or cache.get("data") or []

    if not records:
        _log.info(
            "ingestion.kalshi.empty_cache",
            run_id=str(run_id),
            note="legacy Kalshi cache is empty; ingestion pass is a no-op",
        )
        result.duration_ms = int((time.monotonic() - started) * 1000)
        return result

    # Build the batch — validate, extract resolver fields, append.
    batch: list[dict] = []
    seen_tickers: set = set()
    for record in records:
        if not isinstance(record, dict):
            continue
        ticker = (record.get("event_ticker") or "").strip()
        if not ticker or ticker in seen_tickers:
            # Dedup on ticker — multi-row INSERT cannot have duplicate
            # PK values in the VALUES clause (Postgres forbids it).
            # Legacy cache occasionally has duplicates from sibling
            # bundling; we keep the first occurrence.
            continue
        seen_tickers.add(ticker)

        _, drift = validate_or_drift(
            provider="kalshi",
            record_kind="market",
            record_id=ticker,
            raw=record,
            validator=KalshiMarketValidator,
        )
        if drift:
            result.schema_drift += 1
            # Persist anyway — P4. The resolver decides whether to
            # link based on what it can extract.

        try:
            extracted = _extract_resolver_fields(record)
        except Exception as exc:
            _log.warning(
                "ingestion.kalshi.parse_failed",
                run_id=str(run_id),
                ticker=ticker,
                error_class=type(exc).__name__,
                error_msg=str(exc)[:300],
            )
            extracted = {
                "market_type":      "unparsed",
                "series_ticker":    record.get("series_ticker") or None,
                "abbr_block":       None,
                "parsed_home_abbr": None,
                "parsed_away_abbr": None,
            }

        batch.append({
            "pk":     {"ticker": ticker},
            "fields": extracted,
            "raw":    record,
        })

    # Multi-row UPSERT in chunks. Chunking keeps individual INSERT
    # statement size bounded (Postgres handles huge VALUES lists but
    # very large statements get parsed/planned slowly). 1000 rows per
    # chunk is a comfortable middle ground.
    CHUNK_SIZE = 1000
    for i in range(0, len(batch), CHUNK_SIZE):
        chunk = batch[i:i + CHUNK_SIZE]
        try:
            inserted, updated, unchanged = await upsert_provider_records_batch(
                session, KalshiMarket, chunk,
            )
            result.inserted += inserted
            result.updated += updated
            result.unchanged += unchanged
            result.fetched += len(chunk)
            await session.commit()
        except Exception as exc:
            result.failed += len(chunk)
            await session.rollback()
            _log.warning(
                "ingestion.kalshi.upsert_batch_failed",
                run_id=str(run_id),
                chunk_size=len(chunk),
                chunk_index=i // CHUNK_SIZE,
                error_class=type(exc).__name__,
                error_msg=str(exc)[:300],
            )

    result.duration_ms = int((time.monotonic() - started) * 1000)
    _log.info(
        "ingestion.kalshi.pass_complete",
        run_id=str(run_id),
        fetched=result.fetched,
        failed=result.failed,
        inserted=result.inserted,
        updated=result.updated,
        unchanged=result.unchanged,
        schema_drift=result.schema_drift,
        duration_ms=result.duration_ms,
    )
    return result


async def _markets_loop(
    session_factory,
    interval_sec: float = 30.0,
) -> None:
    """Loop: every 30s, refresh sp.kalshi_markets from the cache."""
    while True:
        try:
            async with session_factory() as session:
                await _ingest_pass(session)
        except Exception:
            # Surface crashes to the supervisor.
            raise
        await asyncio.sleep(interval_sec)


# ── Entry point ──────────────────────────────────────────────────

async def run(session_factory) -> None:
    """Top-level Kalshi ingestion entry. Acquires the singleton
    advisory lock, then runs the cadence loop.

    `session_factory`: callable returning an AsyncSession context
    manager. In production this is db.async_session.

    Returns when cancelled. Crashes inside the loop are caught by
    the surrounding supervisor (ingestion.base.supervise).
    """
    async with session_factory() as lock_session:
        got_lock = await try_acquire_advisory_lock(
            lock_session, ADVISORY_LOCK_KALSHI,
        )
        if not got_lock:
            _log.info(
                "ingestion.kalshi.skipping",
                reason="another worker holds the Kalshi ingestion advisory lock",
            )
            return

        _log.info(
            "ingestion.kalshi.starting",
            cadences={"markets_sec": 30},
        )

        await _markets_loop(session_factory, interval_sec=30.0)
