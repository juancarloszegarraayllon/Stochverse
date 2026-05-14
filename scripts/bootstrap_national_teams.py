"""Bootstrap men's senior national-team rows into sp.teams (Issue #136).

Phase 1 deliverable per Issue #136. Inserts FIFA member nations as
Soccer national-team rows so the anchor_failed admin surface
(PR #133 / sub-PR #4) can match Kalshi market titles like "France
vs Senegal" against a real canonical_name in sp.teams.

Idempotent — re-running is a no-op for rows already present (same
shape as scripts/bootstrap_sp_teams.py:
  1. Bulk-load existing sp.teams keyed on (sport_id, normalized_name)
  2. Walk the manifest (scripts/national_teams_seed.py)
  3. Skip entries already in the existing-state dict
  4. Bulk INSERT genuinely-new rows with on_conflict_do_nothing on
     the primary key column

The (sport_id, normalized_name) tuple has no native UNIQUE constraint
on sp.teams (Index only); idempotency is enforced in Python before
the INSERT, not by the database. Same model as bootstrap_sp_teams.py.

## Usage

    DATABASE_URL=<url> python scripts/bootstrap_national_teams.py

    # Dry-run (no INSERTs; counts what would be inserted):
    DATABASE_URL=<url> python scripts/bootstrap_national_teams.py --dry-run

    # Or via Makefile against docker-compose dev DB:
    make bootstrap-national-teams

## Exit codes

  0 — success (insert OR no-op idempotent re-run)
  1 — DATABASE_URL not set / engine unavailable
  2 — bad CLI args
  3 — sp.sports missing or doesn't contain 'Soccer' (run alembic
      upgrade head first)

## Production deployment

Per the PR's operator-action-after-merge runbook:

  1. Create Neon dev branch.
  2. Apply against dev branch with --dry-run; verify expected
     insert count matches the manifest size (~213) minus any
     pre-existing matching rows.
  3. Apply against dev branch for real; spot-check via
     `SELECT canonical_name, country_code FROM sp.teams t
      JOIN sp.sports s ON t.sport_id = s.id
      WHERE s.name = 'Soccer' AND t.country_code IS NOT NULL
      ORDER BY canonical_name LIMIT 20;`
  4. Apply against production.
  5. Capture row-count output as PR verification comment per
     Issue #129 convention.
  6. Wait one cron cycle; spot-check that France/Senegal record
     (KXWCGAME-26JUN16FRASEN) routes differently than
     fuzzy_no_team_resemblance (24-hour-lag verification).
  7. Delete the dev branch.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
import uuid

# Make project root importable when invoked as `python scripts/...`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: E402

from db import async_session  # noqa: E402
from observability import get_logger  # noqa: E402
from resolver._normalize import normalize_name  # noqa: E402
from scripts.national_teams_seed import NATIONAL_TEAMS_SEED  # noqa: E402
from sp_models import Team  # noqa: E402


SOCCER_SPORT_NAME = "Soccer"


async def bootstrap(dry_run: bool) -> int:
    """Insert national-team rows for Soccer. Returns process exit code."""
    log = get_logger("bootstrap.national_teams")
    started = time.monotonic()
    log.info("bootstrap.national_teams.start", dry_run=dry_run,
             manifest_size=len(NATIONAL_TEAMS_SEED))

    if async_session is None:
        print("ERROR: DATABASE_URL not set or engine unavailable.",
              file=sys.stderr)
        return 1

    async with async_session() as session:
        # ── Step 1: resolve Soccer sport_id ────────────────────────
        row = (await session.execute(
            text("SELECT id FROM sp.sports WHERE name = :name"),
            {"name": SOCCER_SPORT_NAME},
        )).first()
        if row is None:
            print(
                f"ERROR: sp.sports has no row for {SOCCER_SPORT_NAME!r}. "
                "Run `alembic upgrade head` to apply the seed_sp_sports "
                "migration first.",
                file=sys.stderr,
            )
            return 3
        soccer_sport_id = row.id
        log.info("bootstrap.national_teams.soccer_sport_resolved",
                 sport_id=soccer_sport_id)

        # ── Step 2: bulk-load existing Soccer teams ────────────────
        # Keyed on (sport_id, normalized_name) → uuid. One bulk
        # SELECT; no per-row lookups during dedup.
        existing_rows = (await session.execute(
            text(
                "SELECT id, normalized_name FROM sp.teams "
                "WHERE sport_id = :sport_id"
            ),
            {"sport_id": soccer_sport_id},
        )).all()
        existing_by_normalized: dict[str, uuid.UUID] = {
            r.normalized_name: r.id for r in existing_rows
        }
        log.info("bootstrap.national_teams.existing_loaded",
                 count=len(existing_by_normalized))

        # ── Step 3: classify manifest entries in Python ────────────
        teams_to_insert: list[dict] = []
        already_present_count = 0
        empty_normalized_count = 0  # diagnostic; should never trigger

        for canonical_name, country_code, _notes in NATIONAL_TEAMS_SEED:
            normalized = normalize_name(canonical_name)
            if not normalized:
                # Defensive — would only fire if normalize_name returns
                # empty for a non-empty canonical, which shouldn't
                # happen for any FIFA-style name.
                empty_normalized_count += 1
                log.warning(
                    "bootstrap.national_teams.empty_normalized",
                    canonical_name=canonical_name,
                )
                continue
            if normalized in existing_by_normalized:
                already_present_count += 1
                continue
            teams_to_insert.append({
                "id": uuid.uuid4(),
                "sport_id": soccer_sport_id,
                "canonical_name": canonical_name,
                "normalized_name": normalized,
                "country_code": country_code,
            })

        log.info(
            "bootstrap.national_teams.classified",
            queued_for_insert=len(teams_to_insert),
            already_present=already_present_count,
            empty_normalized=empty_normalized_count,
        )

        # ── Step 4: insert (skipped under --dry-run) ───────────────
        inserted = 0
        if dry_run:
            log.info("bootstrap.national_teams.dry_run_skipping_insert")
        elif teams_to_insert:
            # on_conflict_do_nothing on the primary key — same shape
            # as bootstrap_sp_teams.py. Defends against a freshly-
            # generated uuid colliding with an existing row's uuid
            # (vanishingly unlikely but the guard is free) AND
            # against a concurrent insert from another connection
            # (also unlikely in practice; bootstrap is a one-shot
            # operator action).
            stmt = pg_insert(Team.__table__).values(teams_to_insert)
            stmt = stmt.on_conflict_do_nothing(index_elements=["id"])
            result = await session.execute(stmt)
            inserted = result.rowcount or 0
            await session.commit()

        elapsed = time.monotonic() - started
        log.info(
            "bootstrap.national_teams.complete",
            dry_run=dry_run,
            elapsed_sec=round(elapsed, 2),
            inserted=inserted,
            queued_for_insert=len(teams_to_insert),
            already_present=already_present_count,
        )

        # ── Final stdout summary (operator-facing) ─────────────────
        verb = "Would insert" if dry_run else "Inserted"
        print(f"\nNational-team bootstrap "
              f"{'dry-run' if dry_run else 'complete'} in {elapsed:.1f}s:")
        print(f"  Manifest entries:    {len(NATIONAL_TEAMS_SEED):>4}")
        print(f"  {verb}:           "
              f"{len(teams_to_insert):>4}"
              f"{' (' + str(inserted) + ' actually committed)' if not dry_run else ''}")
        print(f"  Already present:     {already_present_count:>4}")
        if empty_normalized_count:
            print(f"  WARNINGS: {empty_normalized_count} entries "
                  f"normalized to empty string — see logs for canonicals")

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bootstrap men's senior national-team rows into "
                    "sp.teams. Idempotent.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Load existing state + classify manifest entries, but "
             "don't INSERT. Prints counts of what would be inserted.",
    )
    args = parser.parse_args(argv)
    return asyncio.run(bootstrap(dry_run=args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
