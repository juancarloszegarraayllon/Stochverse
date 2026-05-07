"""FL backfill — pump indent_days range through ingestion.fl pipeline.

Phase 1E per SP Architecture v1.3 §11.2.

What it does:
  Loops indent_days from -N to +N (default ±7), and for each day calls
  ingestion.fl._ingest_pass(sport_ids=DEFAULT, indent_days=d). Same
  function the live ingestion calls — same UPSERT, same Pydantic
  validation, same schema-drift detection, same advisory-lock-free
  path (no lock needed; this is a one-shot script, not a loop).

Limitations:
  FlashLive's /v1/events/list endpoint serves ±7 days of data. Beyond
  that we'd need different endpoints (per-tournament historical
  queries) which aren't wired today. So '30 days of FL backfill' is
  capped at 14 days (±7) until a Phase 2 PR adds historical fetches.

Idempotency:
  Same UPSERT machinery as live ingestion. Re-running an already-
  applied backfill produces all-`unchanged` counts on the next pass.

Usage:
  DATABASE_URL=... python scripts/backfill_fl.py
  DATABASE_URL=... python scripts/backfill_fl.py --days 7
  DATABASE_URL=... python scripts/backfill_fl.py --days 7 --sport-ids 1,2,3

Or via Makefile:
  make backfill-fl
  make backfill-fl ARGS="--days 7"
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time

# Make project root importable when this is invoked as `python scripts/...`.
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main(days: int, sport_ids: list[int] | None) -> int:
    from db import async_session, DATABASE_URL
    if not DATABASE_URL or async_session is None:
        print("ERROR: DATABASE_URL not set; backfill requires Postgres.", file=sys.stderr)
        return 2

    from ingestion.fl import _ingest_pass, DEFAULT_FL_SPORT_IDS
    from observability import get_logger

    log = get_logger("backfill.fl")
    sports = sport_ids if sport_ids is not None else DEFAULT_FL_SPORT_IDS

    log.info(
        "backfill.fl.start",
        days=days,
        sport_ids=sports,
        indent_days_range=[-days, days],
    )
    started = time.monotonic()

    totals = {
        "fetched": 0, "failed": 0,
        "inserted": 0, "updated": 0, "unchanged": 0,
        "schema_drift": 0,
    }

    async with async_session() as session:
        for d in range(-days, days + 1):
            try:
                result = await _ingest_pass(
                    session, sport_ids=sports, indent_days=d,
                )
                for k in totals:
                    totals[k] += getattr(result, k, 0)
                log.info(
                    "backfill.fl.day_complete",
                    indent_days=d,
                    fetched=result.fetched,
                    inserted=result.inserted,
                    updated=result.updated,
                    unchanged=result.unchanged,
                    schema_drift=result.schema_drift,
                    failed=result.failed,
                    duration_ms=result.duration_ms,
                )
            except Exception as exc:
                log.error(
                    "backfill.fl.day_failed",
                    indent_days=d,
                    error_class=type(exc).__name__,
                    error_msg=str(exc)[:500],
                    exc_info=True,
                )

    log.info(
        "backfill.fl.complete",
        total_seconds=int(time.monotonic() - started),
        **totals,
    )
    return 0


def _parse_sport_ids(s: str) -> list[int]:
    return [int(x) for x in s.split(",") if x.strip()]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--days", type=int, default=7,
        help="Range to backfill: indent_days from -DAYS to +DAYS. Capped at 7 by FL API. Default 7.",
    )
    parser.add_argument(
        "--sport-ids", type=_parse_sport_ids, default=None,
        help="Comma-separated FL sport_id list. Defaults to DEFAULT_FL_SPORT_IDS in ingestion/fl.py.",
    )
    args = parser.parse_args()

    if args.days > 7:
        print(
            "WARNING: --days > 7. FL's /v1/events/list serves ±7 days only; "
            "extra days will return empty. Capping conceptually at ±7.",
            file=sys.stderr,
        )

    rc = asyncio.run(main(args.days, args.sport_ids))
    sys.exit(rc)
