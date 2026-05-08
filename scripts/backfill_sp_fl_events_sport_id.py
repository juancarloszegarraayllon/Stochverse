"""Phase 2A.7 — backfill sp.fl_events.sport_id on existing rows.

After the 2A.7 migration adds the column, every existing row has
sport_id = NULL until ingestion re-UPSERTs it. The simplest path: run
the same FL backfill that operators already use for fresh databases —
it iterates indent_days from -7 to +7 and re-UPSERTs every event
inside the FL ±7 day window. With Phase 2A.7's ingestion change in
place, those UPSERTs populate sport_id from the per-sport loop's
context.

This script is a thin wrapper that runs the standard backfill and
then reports residual NULLs so operators can see the gap before
re-running the resolver pass.

Usage:

    DATABASE_URL=<prod-Neon> python scripts/backfill_sp_fl_events_sport_id.py
    DATABASE_URL=<prod-Neon> python scripts/backfill_sp_fl_events_sport_id.py --skip-backfill

The `--skip-backfill` mode runs only the residual-counts reporting,
useful for repeat audits without burning FL API quota.

Rows that remain sport_id = NULL after this script:
  - Events whose START_TIME is outside the FL ±7 day window. FL's
    /v1/events/list won't return them; they only get refreshed when
    a per-tournament historical fetch is added (out of scope until
    Phase 2 historical-fetch PR).
  - Events whose FL sport_id is not in FL_SPORT_ID_TO_SP_NAME or
    whose mapped sp.sports.name doesn't exist in sp.sports.

Both cases are surfaced in the residual report below.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

# Make project root importable when invoked as `python scripts/...`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main(skip_backfill: bool, days: int) -> int:
    from sqlalchemy import text

    from db import async_session, DATABASE_URL
    if not DATABASE_URL or async_session is None:
        print("ERROR: DATABASE_URL not set; backfill requires Postgres.", file=sys.stderr)
        return 2

    from observability import get_logger

    log = get_logger("backfill.sp_fl_events_sport_id")
    started = time.monotonic()

    # ── Pre-counts ─────────────────────────────────────────────
    async with async_session() as session:
        pre_total = (await session.execute(
            text("SELECT COUNT(*) FROM sp.fl_events")
        )).scalar()
        pre_null = (await session.execute(
            text("SELECT COUNT(*) FROM sp.fl_events WHERE sport_id IS NULL")
        )).scalar()
    print(f"Pre-backfill state:")
    print(f"  total fl_events:           {pre_total:>8}")
    print(f"  sport_id IS NULL:          {pre_null:>8}  ({100*pre_null/(pre_total or 1):.1f}%)")

    # ── Run the standard FL backfill ──────────────────────────
    if skip_backfill:
        print("\n--skip-backfill set — running residual report only.")
    else:
        print(f"\nRunning FL backfill for indent_days -{days}..{days} ...")
        from scripts.backfill_fl import main as backfill_main
        rc = await backfill_main(days=days, sport_ids=None)
        if rc != 0:
            print(f"ERROR: backfill_fl exited with rc={rc}", file=sys.stderr)
            return rc

    # ── Post-counts + per-sport breakdown ─────────────────────
    async with async_session() as session:
        post_total = (await session.execute(
            text("SELECT COUNT(*) FROM sp.fl_events")
        )).scalar()
        post_null = (await session.execute(
            text("SELECT COUNT(*) FROM sp.fl_events WHERE sport_id IS NULL")
        )).scalar()
        per_sport = (await session.execute(text(
            """
            SELECT s.name AS sport, COUNT(fle.fl_event_id) AS n
            FROM sp.fl_events fle
            INNER JOIN sp.sports s ON s.id = fle.sport_id
            GROUP BY 1
            ORDER BY 2 DESC
            """
        ))).all()

    print(f"\nPost-backfill state:")
    print(f"  total fl_events:           {post_total:>8}")
    print(f"  sport_id IS NULL:          {post_null:>8}  ({100*post_null/(post_total or 1):.1f}%)")
    print(f"  recovered this run:        {pre_null - post_null:>8}")
    print(f"\nPer-sport coverage:")
    for row in per_sport:
        print(f"    {row.sport:<25}  {row.n:>8}")

    if post_null > 0:
        print(
            f"\n{post_null} rows remain sport_id IS NULL. Likely causes:\n"
            "  - START_TIME outside FL's ±7 day /v1/events/list window\n"
            "  - FL sport_id not in FL_SPORT_ID_TO_SP_NAME\n"
            "  - Mapped sp.sports.name missing from sp.sports table\n"
            "Check ingestion.fl.sport_id_unmapped warnings in logs to\n"
            "distinguish the latter two cases."
        )

    elapsed = time.monotonic() - started
    log.info(
        "backfill.sp_fl_events_sport_id.complete",
        elapsed_sec=round(elapsed, 1),
        pre_total=pre_total,
        pre_null=pre_null,
        post_total=post_total,
        post_null=post_null,
        recovered=pre_null - post_null,
    )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--skip-backfill", action="store_true",
        help="Skip the FL re-fetch; just print the residual NULL report. "
             "Use for repeat audits without burning FL API quota.",
    )
    parser.add_argument(
        "--days", type=int, default=7,
        help="Range to backfill: indent_days from -DAYS to +DAYS. "
             "Capped at 7 by FL API. Default 7.",
    )
    args = parser.parse_args()
    rc = asyncio.run(main(
        skip_backfill=args.skip_backfill,
        days=args.days,
    ))
    sys.exit(rc)
