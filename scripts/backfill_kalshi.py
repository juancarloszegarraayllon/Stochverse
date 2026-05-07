"""Kalshi backfill — refresh the legacy cache, pump it through ingestion.kalshi.

Phase 1E per SP Architecture v1.3 §11.2.

What it does:
  1. Calls main.get_data() — triggers the legacy paginate() which
     fetches BOTH open and closed Kalshi events (see main.py:1262).
     This is where the 'last 30 days of resolved markets' actually
     lives — Kalshi keeps recently-settled events queryable via the
     same /events endpoint with status=closed.
  2. Calls ingestion.kalshi._ingest_pass() — same UPSERT pipeline
     as the live 30s loop. Reads from the cache populated in step 1
     and writes to sp.kalshi_markets with full hash-based change
     detection, schema-drift counting, etc.

Limitations:
  Kalshi's /events endpoint ages closed events out after some
  retention window (varies by series; typically weeks to months).
  Events older than that retention can't be backfilled via this path.
  For deeper history we'd need Kalshi's event-by-ID retrieval API
  with a list of historical tickers — out of scope for Phase 1E.

  The 'last 30 days' goal in §11.2 is best-effort against whatever
  Kalshi's API still serves. After backfill, query
  `SELECT MIN(last_seen_at), MAX(last_seen_at) FROM sp.kalshi_markets`
  to see the actual date range covered.

Idempotency:
  Same UPSERT machinery as live ingestion. Re-running produces
  all-`unchanged` if the cache hasn't refreshed since the prior run.

Usage:
  DATABASE_URL=... python scripts/backfill_kalshi.py
  DATABASE_URL=... python scripts/backfill_kalshi.py --skip-fetch

Or via Makefile:
  make backfill-kalshi
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time

import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main(skip_fetch: bool) -> int:
    from db import async_session, DATABASE_URL
    if not DATABASE_URL or async_session is None:
        print("ERROR: DATABASE_URL not set; backfill requires Postgres.", file=sys.stderr)
        return 2

    from ingestion.kalshi import _ingest_pass
    from observability import get_logger

    log = get_logger("backfill.kalshi")
    log.info("backfill.kalshi.start", skip_fetch=skip_fetch)
    started = time.monotonic()

    if not skip_fetch:
        # Trigger the legacy fetcher in a thread so we don't block
        # asyncio. paginate() in main.py is synchronous and can take
        # 20-60s to walk all pages. _ingest_pass also has a fallback
        # that triggers get_data() if the cache is empty, but doing
        # it explicitly here keeps the backfill flow obvious in logs.
        log.info("backfill.kalshi.fetching", note="calling main.get_data() in executor")
        import main as _main_mod
        loop = asyncio.get_event_loop()
        try:
            await asyncio.wait_for(
                loop.run_in_executor(None, _main_mod.get_data),
                timeout=300.0,
            )
        except asyncio.TimeoutError:
            log.error(
                "backfill.kalshi.fetch_timeout",
                note="get_data() did not complete within 5 minutes; proceeding with whatever's in cache",
            )
        except Exception as exc:
            log.error(
                "backfill.kalshi.fetch_failed",
                error_class=type(exc).__name__,
                error_msg=str(exc)[:500],
                exc_info=True,
            )
            return 1

    async with async_session() as session:
        try:
            result = await _ingest_pass(session)
            log.info(
                "backfill.kalshi.complete",
                fetched=result.fetched,
                inserted=result.inserted,
                updated=result.updated,
                unchanged=result.unchanged,
                schema_drift=result.schema_drift,
                failed=result.failed,
                duration_ms=result.duration_ms,
                total_seconds=int(time.monotonic() - started),
            )
        except Exception as exc:
            log.error(
                "backfill.kalshi.ingest_failed",
                error_class=type(exc).__name__,
                error_msg=str(exc)[:500],
                exc_info=True,
            )
            return 1

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--skip-fetch", action="store_true",
        help="Skip the legacy get_data() call; ingest whatever is "
             "currently in the in-process cache. Useful for re-running "
             "the ingest pass without re-paginating Kalshi.",
    )
    args = parser.parse_args()
    rc = asyncio.run(main(args.skip_fetch))
    sys.exit(rc)
