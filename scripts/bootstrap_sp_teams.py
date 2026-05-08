"""Bootstrap sp.teams + sp.team_aliases from legacy public.entities + public.entity_aliases.

Phase 2A.5 deliverable per SP Architecture v1.4 §5 / Phase 2B design
doc Question B. One-time migration (idempotent — re-running is a
no-op for any rows already present).

I/O pattern: bulk loads, in-memory dedup, bulk inserts. NOT per-row
round-trips. The original implementation issued one SELECT per
entity to check for existing teams + one INSERT per alias, which at
27k entities and ~50k aliases meant ~80,000 network round-trips at
~75ms each = 1-2 hours. This rewrite collapses to ~80 round-trips
total (2 bulk SELECTs + ~30 chunked INSERTs for teams + ~50 for
aliases).

Algorithm:

  1. Bulk-load existing state in two queries:
       - All sp.teams keyed on (sport_id, normalized_name) → uuid
       - All sp.team_aliases.alias_normalized WHERE source='legacy_bootstrap' → set

  2. Walk legacy public.entities (team-typed) once in Python:
       - Resolve sport_id from sp.sports name lookup
       - Normalize canonical_name
       - If (sport_id, normalized) already exists → reuse uuid
       - Otherwise generate a new uuid, queue for bulk insert,
         add to in-memory map so duplicate normalizations within
         this batch dedup correctly
       - Build legacy_entity_id → sp_team_id map

  3. Walk legacy public.entity_aliases once in Python:
       - Look up sp_team_id via the map (skip if entity wasn't team-typed)
       - Normalize alias
       - If alias_normalized already in the legacy_bootstrap set → skip
       - Otherwise queue for bulk insert, add to set for in-batch dedup

  4. Bulk-insert teams in chunks of 1000 (single multi-row
     INSERT ... ON CONFLICT DO NOTHING per chunk).

  5. Bulk-insert aliases in chunks of 1000 (same shape).

Each chunk = one short transaction (per the leak-fix discipline in
db.py). Per-chunk failure is caught + logged; subsequent chunks
proceed.

Idempotency guarantees survive the rewrite:
  - sp.teams unique-ish via (sport_id, normalized_name) check + on-
    conflict-do-nothing on the existing index. Duplicates within a
    batch are deduped in Python first.
  - sp.team_aliases idempotent via the (alias_normalized, source)
    UNIQUE constraint, with ON CONFLICT DO NOTHING.

Re-running this script after a successful bootstrap produces zero
new inserts.

Usage:

    DATABASE_URL=<prod-Neon-URL> python scripts/bootstrap_sp_teams.py

    # Dry-run (no INSERTs; counts what would be inserted):
    DATABASE_URL=<prod-Neon-URL> python scripts/bootstrap_sp_teams.py --dry-run

    # Or via Makefile against docker-compose dev DB:
    make bootstrap-sp-teams
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


# Bulk-INSERT chunk size. Postgres handles huge VALUES lists but
# parse/plan time is super-linear for very large statements; 1000
# rows is a comfortable middle ground.
INSERT_CHUNK_SIZE = 1000


async def main(dry_run: bool) -> int:
    from sqlalchemy import text
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from db import async_session, DATABASE_URL
    if not DATABASE_URL or async_session is None:
        print("ERROR: DATABASE_URL not set; bootstrap requires Postgres.", file=sys.stderr)
        return 2

    from observability import get_logger
    from resolver._normalize import normalize_name
    from sp_models import Team, TeamAlias

    log = get_logger("bootstrap.sp_teams")
    started = time.monotonic()
    log.info("bootstrap.sp_teams.start", dry_run=dry_run)

    async with async_session() as session:
        # ── Step 1: bulk-load existing state ────────────────────

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
        log.info("bootstrap.sp_teams.sports_loaded", count=len(sport_id_by_name))

        # Existing sp.teams keyed on (sport_id, normalized_name) → uuid.
        # In-memory dict; one bulk SELECT, no per-row lookups later.
        existing_team_rows = (await session.execute(text(
            "SELECT id, sport_id, normalized_name FROM sp.teams"
        ))).all()
        team_uuid_by_key: dict[tuple[int, str], uuid.UUID] = {
            (row.sport_id, row.normalized_name): row.id
            for row in existing_team_rows
        }
        log.info("bootstrap.sp_teams.existing_teams_loaded",
                 count=len(team_uuid_by_key))

        # Existing legacy_bootstrap aliases by alias_normalized → set.
        # Used to skip already-bootstrapped aliases without ever issuing
        # a per-alias SELECT.
        existing_alias_rows = (await session.execute(text(
            "SELECT alias_normalized FROM sp.team_aliases "
            "WHERE source = 'legacy_bootstrap'"
        ))).all()
        existing_aliases: set[str] = {row.alias_normalized for row in existing_alias_rows}
        log.info("bootstrap.sp_teams.existing_legacy_aliases_loaded",
                 count=len(existing_aliases))

        # ── Step 2: read legacy entities (team-typed) ───────────

        team_entities = (await session.execute(text(
            """
            SELECT id, canonical_name, sport
            FROM public.entities
            WHERE entity_type = 'team' AND sport IS NOT NULL
            """
        ))).all()
        log.info("bootstrap.sp_teams.legacy_entities_loaded",
                 count=len(team_entities))

        # ── Step 3: classify in Python ──────────────────────────

        legacy_to_sp: dict[int, uuid.UUID] = {}
        teams_to_insert: list[dict] = []
        per_sport_inserts: dict[str, int] = defaultdict(int)
        per_sport_existing: dict[str, int] = defaultdict(int)
        per_sport_skipped: dict[str, int] = defaultdict(int)
        unmapped_sports: dict[str, int] = defaultdict(int)

        for ent in team_entities:
            sport_id = sport_id_by_name.get(ent.sport)
            if sport_id is None:
                unmapped_sports[ent.sport] += 1
                continue

            normalized = normalize_name(ent.canonical_name)
            if not normalized:
                per_sport_skipped[ent.sport] += 1
                continue

            key = (sport_id, normalized)
            existing_uuid = team_uuid_by_key.get(key)
            if existing_uuid is not None:
                # Already in sp.teams — reuse its uuid for alias FKs.
                legacy_to_sp[ent.id] = existing_uuid
                per_sport_existing[ent.sport] += 1
                continue

            # New team — generate uuid, queue insert, add to in-memory
            # map so a later legacy entity with the same normalized
            # name maps to the same new uuid (in-batch dedup).
            new_uuid = uuid.uuid4()
            teams_to_insert.append({
                "id":              new_uuid,
                "sport_id":        sport_id,
                "canonical_name":  ent.canonical_name,
                "normalized_name": normalized,
            })
            team_uuid_by_key[key] = new_uuid
            legacy_to_sp[ent.id] = new_uuid
            per_sport_inserts[ent.sport] += 1

        log.info(
            "bootstrap.sp_teams.teams_classified",
            queued_for_insert=len(teams_to_insert),
            already_existing=sum(per_sport_existing.values()),
            inserted_per_sport=dict(per_sport_inserts),
            existing_per_sport=dict(per_sport_existing),
            skipped_per_sport=dict(per_sport_skipped),
            unmapped_sports=dict(unmapped_sports),
        )

        # ── Step 4: read aliases, classify ──────────────────────

        aliases = (await session.execute(text(
            """
            SELECT a.entity_id, a.alias, a.source
            FROM public.entity_aliases a
            INNER JOIN public.entities e ON e.id = a.entity_id
            WHERE e.entity_type = 'team' AND e.sport IS NOT NULL
            """
        ))).all()
        log.info("bootstrap.sp_teams.legacy_aliases_loaded", count=len(aliases))

        aliases_to_insert: list[dict] = []
        alias_skipped_no_team = 0
        alias_skipped_empty_norm = 0
        alias_skipped_already_present = 0

        for a in aliases:
            sp_team_id = legacy_to_sp.get(a.entity_id)
            if sp_team_id is None:
                alias_skipped_no_team += 1
                continue

            alias_norm = normalize_name(a.alias)
            if not alias_norm:
                alias_skipped_empty_norm += 1
                continue

            if alias_norm in existing_aliases:
                # Either already in sp.team_aliases from a prior run,
                # OR already queued in this batch with the same
                # normalization. Skip — first writer wins on
                # (alias_normalized, source).
                alias_skipped_already_present += 1
                continue

            aliases_to_insert.append({
                "team_id":          sp_team_id,
                "alias":            a.alias,
                "alias_normalized": alias_norm,
                "source":           "legacy_bootstrap",
                "confidence":       0.95,
            })
            existing_aliases.add(alias_norm)

        log.info(
            "bootstrap.sp_teams.aliases_classified",
            queued_for_insert=len(aliases_to_insert),
            skipped_no_team_in_map=alias_skipped_no_team,
            skipped_empty_normalized=alias_skipped_empty_norm,
            skipped_already_present=alias_skipped_already_present,
        )

        # ── Step 5: bulk insert (skip on dry-run) ───────────────

        teams_inserted = 0
        teams_chunks_failed = 0
        aliases_inserted = 0
        aliases_chunks_failed = 0

        if not dry_run:
            # Teams.
            for chunk_start in range(0, len(teams_to_insert), INSERT_CHUNK_SIZE):
                chunk = teams_to_insert[chunk_start:chunk_start + INSERT_CHUNK_SIZE]
                try:
                    stmt = pg_insert(Team.__table__).values(chunk)
                    # No native unique constraint on (sport_id,
                    # normalized_name); rely on in-Python dedup above.
                    # Use the primary key column as the conflict
                    # target so a re-run (where uuid was newly
                    # generated last time) doesn't double-insert
                    # under a freshly minted uuid. BUT: each run
                    # generates new uuids per Python session, so
                    # primary-key conflict is impossible here
                    # unless someone re-ran and the script
                    # generated the same uuid twice (~zero
                    # probability with uuid4). Plain INSERT is
                    # correct.
                    await session.execute(stmt)
                    await session.commit()
                    teams_inserted += len(chunk)
                except Exception as e:
                    teams_chunks_failed += 1
                    await session.rollback()
                    log.warning(
                        "bootstrap.sp_teams.team_chunk_failed",
                        chunk_index=chunk_start // INSERT_CHUNK_SIZE,
                        chunk_size=len(chunk),
                        error_class=type(e).__name__,
                        error_msg=str(e)[:300],
                    )

            # Aliases.
            for chunk_start in range(0, len(aliases_to_insert), INSERT_CHUNK_SIZE):
                chunk = aliases_to_insert[chunk_start:chunk_start + INSERT_CHUNK_SIZE]
                try:
                    stmt = pg_insert(TeamAlias.__table__).values(chunk)
                    stmt = stmt.on_conflict_do_nothing(
                        # The (alias_normalized, source) unique
                        # constraint — UQ_TEAM_ALIASES_ALIAS_NORMALIZED_SOURCE
                        index_elements=["alias_normalized", "source"],
                    )
                    await session.execute(stmt)
                    await session.commit()
                    aliases_inserted += len(chunk)
                except Exception as e:
                    aliases_chunks_failed += 1
                    await session.rollback()
                    log.warning(
                        "bootstrap.sp_teams.alias_chunk_failed",
                        chunk_index=chunk_start // INSERT_CHUNK_SIZE,
                        chunk_size=len(chunk),
                        error_class=type(e).__name__,
                        error_msg=str(e)[:300],
                    )

        # ── Final report ────────────────────────────────────────

        elapsed = time.monotonic() - started
        log.info(
            "bootstrap.sp_teams.complete",
            dry_run=dry_run,
            elapsed_sec=round(elapsed, 1),
            teams_inserted=teams_inserted,
            teams_queued=len(teams_to_insert),
            teams_chunks_failed=teams_chunks_failed,
            aliases_inserted=aliases_inserted,
            aliases_queued=len(aliases_to_insert),
            aliases_chunks_failed=aliases_chunks_failed,
        )

        # Final stdout summary.
        verb = "Would insert" if dry_run else "Inserted"
        print(f"\nBootstrap {'dry-run' if dry_run else 'complete'} in {elapsed:.1f}s:")
        print(f"  {verb}: {len(teams_to_insert):>6} teams ({teams_inserted} actually committed)")
        print(f"  {verb}: {len(aliases_to_insert):>6} aliases ({aliases_inserted} actually committed)")
        print(f"\n  Already present (skipped):")
        print(f"    teams already in sp.teams:       {sum(per_sport_existing.values()):>6}")
        print(f"    aliases already (legacy_bootstrap): {len(existing_aliases) - len(aliases_to_insert):>6}")
        if unmapped_sports:
            print(f"\n  Skipped — unmapped sports in legacy data:")
            for sport, count in sorted(unmapped_sports.items()):
                print(f"    {sport!r:30}  {count:>6} entities")
        if teams_chunks_failed or aliases_chunks_failed:
            print(f"\n  WARNINGS: {teams_chunks_failed} team chunks + {aliases_chunks_failed} alias chunks failed")

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Read everything but don't write. Logs counts of what "
             "would be inserted. Use to verify mapping coverage "
             "before committing.",
    )
    args = parser.parse_args()
    rc = asyncio.run(main(dry_run=args.dry_run))
    sys.exit(rc)
