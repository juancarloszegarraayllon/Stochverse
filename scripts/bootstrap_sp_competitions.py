"""Bootstrap sp.competitions from sp.kalshi_markets (Kalshi-only seed).

Phase 2A.6 deliverable. Seeds the sp.competitions table from distinct
(sport, series_base) tuples observed in sp.kalshi_markets so the
strict-tier matcher's competition gate has data to resolve against.

Scope: Kalshi only. FL competitions remain unseeded until Phase 2C
(blocked on `sp.fl_events.raw_payload` lacking a tournament-level
sport_id). The matcher gate handles this with an FL transitional
fallback ("fl_transitional_sport_only") logged on every successful
FL match.

Algorithm (mirrors bootstrap_sp_teams idempotency pattern):

  1. Bulk-load existing kalshi_series_bases from sp.competitions into
     a Python set. One round-trip.
  2. Bulk-load sp.sports id by name AND lowercase code → dict.
  3. Bulk-fetch DISTINCT (sport, series_ticker) from sp.kalshi_markets
     where series_ticker IS NOT NULL.
  4. For each row: strip_known_suffix(series_ticker) → series_base.
     If (sport_id, series_base) already covered (base in set), skip.
     Else queue a sp.competitions insert with kalshi_series_bases=[base].
  5. Bulk-insert in chunks of 1000 with ON CONFLICT DO NOTHING.

Idempotency: re-running the script after a successful bootstrap is a
no-op. The membership check uses the union of all kalshi_series_bases
arrays already in sp.competitions, so a base seeded in any prior run
(under any sport) will be skipped.

Usage:

    DATABASE_URL=<prod-Neon-URL> python scripts/bootstrap_sp_competitions.py

    # Dry-run (no INSERTs; counts what would be inserted):
    DATABASE_URL=<prod-Neon-URL> python scripts/bootstrap_sp_competitions.py --dry-run

    # Or via Makefile against docker-compose dev DB:
    make bootstrap-sp-competitions
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
import uuid
from collections import defaultdict

# Make project root importable when invoked as `python scripts/...`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


INSERT_CHUNK_SIZE = 1000


# Same legacy alias map used by bootstrap_sp_teams. Kalshi's _sport
# values come from main.py's classification (already canonical), so
# this is only defensive for diff-shaped rows.
LEGACY_SPORT_ALIASES: dict[str, str] = {
    "Football": "American Football",
    "Rugby":    "Rugby Union",
}


def _resolve_sport_id(legacy_sport: str | None, sport_id_by_name: dict) -> int | None:
    if not legacy_sport:
        return None
    canonical = LEGACY_SPORT_ALIASES.get(legacy_sport, legacy_sport)
    return sport_id_by_name.get(canonical)


async def main(dry_run: bool) -> int:
    from sqlalchemy import text
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from db import async_session, DATABASE_URL
    if not DATABASE_URL or async_session is None:
        print("ERROR: DATABASE_URL not set; bootstrap requires Postgres.", file=sys.stderr)
        return 2

    from kalshi_identity import strip_known_suffix
    from observability import get_logger
    from sp_models import Competition

    log = get_logger("bootstrap.sp_competitions")
    started = time.monotonic()
    log.info("bootstrap.sp_competitions.start", dry_run=dry_run)

    async with async_session() as session:
        # ── Step 1: existing state ──────────────────────────────
        sports_rows = (await session.execute(
            text("SELECT id, name FROM sp.sports")
        )).all()
        sport_id_by_name = {row.name: row.id for row in sports_rows}
        if not sport_id_by_name:
            print(
                "ERROR: sp.sports is empty. Run `alembic upgrade head` "
                "to apply the seed_sp_sports migration first.",
                file=sys.stderr,
            )
            return 3
        log.info("bootstrap.sp_competitions.sports_loaded",
                 count=len(sport_id_by_name))

        existing_rows = (await session.execute(text(
            "SELECT id, sport_id, kalshi_series_bases FROM sp.competitions"
        ))).all()
        existing_bases: set[str] = set()
        for row in existing_rows:
            for base in (row.kalshi_series_bases or []):
                existing_bases.add(str(base).upper())
        log.info(
            "bootstrap.sp_competitions.existing_loaded",
            competitions=len(existing_rows),
            bases_indexed=len(existing_bases),
        )

        # ── Step 2: distinct (sport, series_ticker) from sp.kalshi_markets ──
        # _sport carried in raw_payload — set by main.py's classification.
        # Fall back to '' so unclassified markets don't crash the script;
        # they end up in unmapped_sports for visibility.
        rows = (await session.execute(text(
            """
            SELECT DISTINCT
              COALESCE(raw_payload->>'_sport', '') AS sport,
              series_ticker
            FROM sp.kalshi_markets
            WHERE series_ticker IS NOT NULL
              AND series_ticker <> ''
            """
        ))).all()
        log.info("bootstrap.sp_competitions.kalshi_distinct_loaded",
                 count=len(rows))

        # ── Step 3: classify ────────────────────────────────────
        # Aggregate one row per (sport_id, series_base). If multiple
        # series_tickers strip to the same base under the same sport,
        # they collapse into a single competition row.
        new_competitions: dict[tuple[int, str], dict] = {}
        per_sport_inserts: dict[str, int] = defaultdict(int)
        per_sport_existing: dict[str, int] = defaultdict(int)
        unmapped_sports: dict[str, int] = defaultdict(int)
        skipped_unparsed = 0

        for r in rows:
            sport_id = _resolve_sport_id(r.sport, sport_id_by_name)
            if sport_id is None:
                unmapped_sports[r.sport or "(unclassified)"] += 1
                continue

            series_base, _suffix = strip_known_suffix(r.series_ticker)
            if not series_base:
                skipped_unparsed += 1
                continue

            base_upper = series_base.upper()
            if base_upper in existing_bases:
                per_sport_existing[r.sport] += 1
                continue

            key = (sport_id, base_upper)
            if key not in new_competitions:
                new_competitions[key] = {
                    "id":                       uuid.uuid4(),
                    "sport_id":                 sport_id,
                    "canonical_name":           base_upper,
                    "normalized_name":          base_upper.lower(),
                    "country_code":             None,
                    "season":                   None,
                    "competition_type":         None,
                    "kalshi_series_bases":      [base_upper],
                    "fl_tournament_stage_ids":  [],
                    "polymarket_slugs":         [],
                    "oddsapi_keys":             [],
                }
                per_sport_inserts[r.sport] += 1
                # Add to set so further rows with the same base under
                # any sport in this batch are deduped against it.
                existing_bases.add(base_upper)

        comps_to_insert = list(new_competitions.values())
        log.info(
            "bootstrap.sp_competitions.classified",
            queued_for_insert=len(comps_to_insert),
            already_existing=sum(per_sport_existing.values()),
            inserted_per_sport=dict(per_sport_inserts),
            existing_per_sport=dict(per_sport_existing),
            unmapped_sports=dict(unmapped_sports),
            skipped_unparsed_series_ticker=skipped_unparsed,
        )

        # ── Step 4: bulk insert ─────────────────────────────────
        comps_inserted = 0
        comps_chunks_failed = 0

        if not dry_run:
            for chunk_start in range(0, len(comps_to_insert), INSERT_CHUNK_SIZE):
                chunk = comps_to_insert[chunk_start:chunk_start + INSERT_CHUNK_SIZE]
                try:
                    stmt = pg_insert(Competition.__table__).values(chunk)
                    # No native unique constraint on (sport_id,
                    # normalized_name); rely on in-Python dedup above.
                    # Plain INSERT is correct since uuids are freshly
                    # generated each run.
                    await session.execute(stmt)
                    await session.commit()
                    comps_inserted += len(chunk)
                except Exception as e:
                    comps_chunks_failed += 1
                    await session.rollback()
                    log.warning(
                        "bootstrap.sp_competitions.chunk_failed",
                        chunk_index=chunk_start // INSERT_CHUNK_SIZE,
                        chunk_size=len(chunk),
                        error_class=type(e).__name__,
                        error_msg=str(e)[:300],
                    )

        elapsed = time.monotonic() - started
        log.info(
            "bootstrap.sp_competitions.complete",
            dry_run=dry_run,
            elapsed_sec=round(elapsed, 1),
            competitions_inserted=comps_inserted,
            competitions_queued=len(comps_to_insert),
            chunks_failed=comps_chunks_failed,
        )

        verb = "Would insert" if dry_run else "Inserted"
        print(f"\nBootstrap competitions {'dry-run' if dry_run else 'complete'} in {elapsed:.1f}s:")
        print(f"  {verb}: {len(comps_to_insert):>6} competitions ({comps_inserted} actually committed)")
        print(f"  Already present (skipped):       {sum(per_sport_existing.values()):>6}")
        if skipped_unparsed:
            print(f"  Skipped — series_ticker stripped to empty: {skipped_unparsed}")
        if unmapped_sports:
            print(f"\n  Skipped — unmapped/unclassified sports:")
            for sport, count in sorted(unmapped_sports.items()):
                print(f"    {sport!r:30}  {count:>6} distinct series")
        if per_sport_inserts:
            print(f"\n  Inserted per sport:")
            for sport, count in sorted(per_sport_inserts.items()):
                print(f"    {sport!r:30}  {count:>6}")
        if comps_chunks_failed:
            print(f"\n  WARNING: {comps_chunks_failed} chunks failed; see logs.")

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Read everything but don't write. Logs counts of what "
             "would be inserted.",
    )
    args = parser.parse_args()
    rc = asyncio.run(main(dry_run=args.dry_run))
    sys.exit(rc)
