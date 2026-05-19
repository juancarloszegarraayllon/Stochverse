"""Bootstrap KBL (Korean Basketball League) team coverage — Phase 2C pilot.

Methodology pilot for the 5-sport zero-coverage cohort (Handball,
Snooker, Volleyball, Rugby League, Golf) surfaced in PROJECT_STATE
2026-05-17. KBL extends PR #156's national-teams bootstrap pattern
with an aliases-write dimension.

Inserts/updates 10 KBL teams + their alias coverage into sp.teams
and sp.team_aliases so future KBL records auto-resolve via strict
or alias tier instead of routing to review_queue.

Idempotent — re-running is a no-op for rows + aliases already
present. Same shape as scripts/bootstrap_national_teams.py with
two structural extensions:

  1. Three-branch classifier on sp.teams: INSERT new / BACKFILL
     country_code on existing legacy row / SKIP complete row.
     (Same as PR #156's Phase 1.5 pattern.)
  2. NEW: parallel alias classifier on sp.team_aliases: INSERT
     new alias / SKIP existing alias. Runs as a second sequential
     pass after team-row writes resolve, per team. Keyed on
     (team_id, alias_normalized, source) — the source value is
     'bootstrap_league_coverage' per Q3 decision (see kbl_seed.py
     docstring — generic value reused across the 5-sport cohort).

The (team_id, alias_normalized, source) tuple has a UNIQUE
constraint on (alias_normalized, source) via the sp_models.py
TeamAlias.__table_args__ — that's enforced by Postgres, but we
also dedup in Python before INSERT to avoid the chatty
on_conflict_do_nothing path for the common no-op case.

## Usage

    DATABASE_URL=<url> python scripts/bootstrap_kbl.py

    # Dry-run (no INSERTs/UPDATEs; counts what would change):
    DATABASE_URL=<url> python scripts/bootstrap_kbl.py --dry-run

    # Or via Makefile against docker-compose dev DB:
    make bootstrap-kbl
    make bootstrap-kbl ARGS="--dry-run"

## Exit codes

  0 — success (writes happened OR no-op idempotent re-run)
  1 — DATABASE_URL not set / engine unavailable
  2 — bad CLI args
  3 — sp.sports missing or doesn't contain 'Basketball' (run alembic
      upgrade head first)

## Production deployment

Per the PR's operator-action-after-merge runbook:

  1. Create Neon dev branch.
  2. Apply against dev branch with --dry-run; verify expected
     counts match the manifest (10 teams; ~2 backfills for Goyang
     Sono + KCC Egis; ~8 new INSERTs; ~24+ aliases).
  3. Apply against dev branch for real; spot-check via the SELECT
     in kbl_seed.py "Re-curation runbook" step 8.
  4. Apply against production.
  5. Capture row-count output as PR verification comment per
     Issue #129 convention.
  6. Wait one cron cycle; spot-check that current KBL pending
     records (e.g., the Goyang Skygunners vs KCC Egis matchup
     surfaced via Query 1 on 2026-05-19) route differently than
     fuzzy_no_team_resemblance no_match.
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
from scripts.kbl_seed import KBL_ALIAS_SOURCE, KBL_TEAMS_SEED  # noqa: E402
from sp_models import Team, TeamAlias  # noqa: E402


BASKETBALL_SPORT_NAME = "Basketball"


async def bootstrap(dry_run: bool) -> int:
    """Insert/update KBL team + alias coverage. Returns process exit code."""
    log = get_logger("bootstrap.kbl")
    started = time.monotonic()
    log.info("bootstrap.kbl.start", dry_run=dry_run,
             manifest_size=len(KBL_TEAMS_SEED))

    if async_session is None:
        print("ERROR: DATABASE_URL not set or engine unavailable.",
              file=sys.stderr)
        return 1

    async with async_session() as session:
        # ── Step 1: resolve Basketball sport_id ────────────────────
        row = (await session.execute(
            text("SELECT id FROM sp.sports WHERE name = :name"),
            {"name": BASKETBALL_SPORT_NAME},
        )).first()
        if row is None:
            print(
                f"ERROR: sp.sports has no row for {BASKETBALL_SPORT_NAME!r}. "
                "Run `alembic upgrade head` to apply the seed_sp_sports "
                "migration first.",
                file=sys.stderr,
            )
            return 3
        basketball_sport_id = row.id
        log.info("bootstrap.kbl.basketball_sport_resolved",
                 sport_id=basketball_sport_id)

        # ── Step 2: bulk-load existing Basketball teams ────────────
        # Same shape as PR #156. Keyed on normalized_name →
        # (uuid, current_country_code). Loads ALL Basketball teams
        # (not just KOR-country) so the lookup correctly identifies
        # cross-country potential collisions if any.
        existing_team_rows = (await session.execute(
            text(
                "SELECT id, normalized_name, country_code "
                "FROM sp.teams WHERE sport_id = :sport_id"
            ),
            {"sport_id": basketball_sport_id},
        )).all()
        existing_teams_by_normalized: dict[
            str, tuple[uuid.UUID, str | None]
        ] = {
            r.normalized_name: (r.id, r.country_code)
            for r in existing_team_rows
        }
        log.info("bootstrap.kbl.existing_teams_loaded",
                 count=len(existing_teams_by_normalized))

        # ── Step 3: classify team rows (three-branch per PR #156) ──
        #
        # Same three classification branches as PR #156:
        #
        #   (a) Not in existing → queue INSERT (new row).
        #   (b) In existing AND country_code IS NULL → queue UPDATE
        #       (Phase 1.5 backfill — 2A.5 legacy bootstrap interaction).
        #   (c) In existing with country_code already set → skip the
        #       team row write (aliases still processed in Step 5).
        teams_to_insert: list[dict] = []
        teams_to_backfill: list[tuple[uuid.UUID, str]] = []
        already_present_count = 0
        empty_normalized_count = 0

        # Track the resolved team_id per manifest entry so Step 5
        # (alias classifier) can attach aliases to the correct team_id
        # regardless of whether the team was INSERTed, BACKFILLed, or
        # SKIPPED. Indexed by manifest position.
        team_ids_by_manifest_index: dict[int, uuid.UUID] = {}

        for idx, (canonical_name, country_code, aliases, _notes) in enumerate(
            KBL_TEAMS_SEED
        ):
            normalized = normalize_name(canonical_name)
            if not normalized:
                empty_normalized_count += 1
                log.warning(
                    "bootstrap.kbl.empty_normalized",
                    canonical_name=canonical_name,
                )
                continue
            existing = existing_teams_by_normalized.get(normalized)
            if existing is None:
                # Branch (a): new row.
                new_id = uuid.uuid4()
                teams_to_insert.append({
                    "id": new_id,
                    "sport_id": basketball_sport_id,
                    "canonical_name": canonical_name,
                    "normalized_name": normalized,
                    "country_code": country_code,
                })
                team_ids_by_manifest_index[idx] = new_id
                continue
            existing_id, existing_country_code = existing
            team_ids_by_manifest_index[idx] = existing_id
            if existing_country_code is None and country_code is not None:
                # Branch (b): backfill country_code on legacy row.
                teams_to_backfill.append((existing_id, country_code))
            else:
                # Branch (c): team row already complete.
                already_present_count += 1

        log.info(
            "bootstrap.kbl.teams_classified",
            queued_for_insert=len(teams_to_insert),
            queued_for_backfill=len(teams_to_backfill),
            already_present=already_present_count,
            empty_normalized=empty_normalized_count,
        )

        # ── Step 4: bulk-load existing aliases for affected teams ──
        # Once we know the team_ids that aliases will attach to, load
        # their existing aliases (by team_id + source) to dedup
        # in Python before the alias INSERT pass. The UNIQUE constraint
        # on (alias_normalized, source) at the table level enforces
        # idempotency at the DB layer too, but we dedup here to keep
        # the alias-INSERT batch tight and the operator-facing counts
        # accurate.
        affected_team_ids = list(team_ids_by_manifest_index.values())
        existing_aliases: set[tuple[uuid.UUID, str]] = set()
        if affected_team_ids:
            alias_rows = (await session.execute(
                text(
                    "SELECT team_id, alias_normalized "
                    "FROM sp.team_aliases "
                    "WHERE team_id = ANY(CAST(:team_ids AS uuid[])) "
                    "AND source = :source"
                ).bindparams(
                    team_ids=[str(t) for t in affected_team_ids],
                    source=KBL_ALIAS_SOURCE,
                ),
            )).all()
            existing_aliases = {
                (r.team_id, r.alias_normalized) for r in alias_rows
            }
        log.info("bootstrap.kbl.existing_aliases_loaded",
                 count=len(existing_aliases),
                 source=KBL_ALIAS_SOURCE)

        # ── Step 5: classify aliases ───────────────────────────────
        #
        # Two-branch classifier on sp.team_aliases:
        #
        #   (a) Not in existing → queue INSERT.
        #   (b) In existing → skip (idempotent re-run).
        #
        # Within a single bootstrap run, additionally dedup on
        # (team_id, alias_normalized) — multiple manifest entries
        # with the same normalized alias would otherwise INSERT
        # twice. Shouldn't happen with the current seed but defensive.
        aliases_to_insert: list[dict] = []
        aliases_skipped_existing = 0
        aliases_skipped_dup_in_batch = 0
        aliases_skipped_empty_normalized = 0
        in_batch_seen: set[tuple[uuid.UUID, str]] = set()

        for idx, (_cname, _ccode, aliases, _notes) in enumerate(
            KBL_TEAMS_SEED
        ):
            team_id = team_ids_by_manifest_index.get(idx)
            if team_id is None:
                # Team row classification failed (empty_normalized
                # branch). Skip its aliases — they can't attach.
                continue
            for alias_raw in aliases:
                alias_normalized = normalize_name(alias_raw)
                if not alias_normalized:
                    aliases_skipped_empty_normalized += 1
                    log.warning(
                        "bootstrap.kbl.alias_empty_normalized",
                        alias=alias_raw, team_id=str(team_id),
                    )
                    continue
                key = (team_id, alias_normalized)
                if key in in_batch_seen:
                    aliases_skipped_dup_in_batch += 1
                    continue
                in_batch_seen.add(key)
                if key in existing_aliases:
                    aliases_skipped_existing += 1
                    continue
                aliases_to_insert.append({
                    "id": uuid.uuid4(),
                    "team_id": team_id,
                    "alias": alias_raw,
                    "alias_normalized": alias_normalized,
                    "source": KBL_ALIAS_SOURCE,
                    "confidence": 1.0,
                })

        log.info(
            "bootstrap.kbl.aliases_classified",
            queued_for_insert=len(aliases_to_insert),
            skipped_existing=aliases_skipped_existing,
            skipped_dup_in_batch=aliases_skipped_dup_in_batch,
            skipped_empty_normalized=aliases_skipped_empty_normalized,
        )

        # ── Step 6: writes (skipped under --dry-run) ───────────────
        inserted_teams = 0
        backfilled_teams = 0
        inserted_aliases = 0

        if dry_run:
            log.info(
                "bootstrap.kbl.dry_run_skipping_writes",
                would_insert_teams=len(teams_to_insert),
                would_backfill_teams=len(teams_to_backfill),
                would_insert_aliases=len(aliases_to_insert),
            )
        else:
            # Team-row writes (mirrors PR #156 shape).
            if teams_to_insert:
                stmt = pg_insert(Team.__table__).values(teams_to_insert)
                stmt = stmt.on_conflict_do_nothing(index_elements=["id"])
                result = await session.execute(stmt)
                inserted_teams = result.rowcount or 0
            if teams_to_backfill:
                await session.execute(
                    text(
                        "UPDATE sp.teams "
                        "SET country_code = v.country_code "
                        "FROM (VALUES " + ", ".join(
                            f"(CAST(:id_{i} AS uuid), :code_{i})"
                            for i in range(len(teams_to_backfill))
                        ) + ") AS v(id, country_code) "
                        "WHERE sp.teams.id = v.id "
                        "AND sp.teams.country_code IS NULL"
                    ),
                    {
                        k: v
                        for i, (row_id, code) in enumerate(teams_to_backfill)
                        for k, v in (
                            (f"id_{i}", str(row_id)),
                            (f"code_{i}", code),
                        )
                    },
                )
                backfilled_teams = len(teams_to_backfill)

            # Alias writes — second sequential pass after team rows.
            # on_conflict_do_nothing on (alias_normalized, source)
            # UNIQUE constraint defends against a concurrent insert
            # from another process AND against any in-batch duplicate
            # the Python dedup missed (vanishingly unlikely; defensive).
            if aliases_to_insert:
                stmt = pg_insert(TeamAlias.__table__).values(aliases_to_insert)
                stmt = stmt.on_conflict_do_nothing(
                    index_elements=["alias_normalized", "source"],
                )
                result = await session.execute(stmt)
                inserted_aliases = result.rowcount or 0

            await session.commit()

        elapsed = time.monotonic() - started
        log.info(
            "bootstrap.kbl.complete",
            dry_run=dry_run,
            elapsed_sec=round(elapsed, 2),
            inserted_teams=inserted_teams,
            backfilled_teams=backfilled_teams,
            inserted_aliases=inserted_aliases,
            queued_for_insert=len(teams_to_insert),
            queued_for_backfill=len(teams_to_backfill),
            queued_for_alias_insert=len(aliases_to_insert),
            already_present=already_present_count,
        )

        # ── Final stdout summary (operator-facing) ─────────────────
        team_insert_verb = "Would insert" if dry_run else "Inserted"
        team_backfill_verb = "Would backfill" if dry_run else "Backfilled"
        alias_insert_verb = "Would insert" if dry_run else "Inserted"
        print(f"\nKBL bootstrap "
              f"{'dry-run' if dry_run else 'complete'} in {elapsed:.1f}s:")
        print(f"  Manifest entries:               {len(KBL_TEAMS_SEED):>4}")
        print(f"  Teams {team_insert_verb}:                 "
              f"{len(teams_to_insert):>4}"
              f"{' (' + str(inserted_teams) + ' actually committed)' if not dry_run else ''}")
        print(f"  Teams {team_backfill_verb}:               "
              f"{len(teams_to_backfill):>4}"
              f"{' (' + str(backfilled_teams) + ' actually committed)' if not dry_run else ''}")
        print(f"  Teams already present:           {already_present_count:>4}")
        print(f"  Aliases {alias_insert_verb}:               "
              f"{len(aliases_to_insert):>4}"
              f"{' (' + str(inserted_aliases) + ' actually committed)' if not dry_run else ''}")
        print(f"  Aliases already present:         {aliases_skipped_existing:>4}")
        if aliases_skipped_dup_in_batch:
            print(f"  Aliases dedup'd within batch:    "
                  f"{aliases_skipped_dup_in_batch:>4}")
        if empty_normalized_count:
            print(f"  WARNINGS: {empty_normalized_count} team entries "
                  f"normalized to empty string — see logs for canonicals")
        if aliases_skipped_empty_normalized:
            print(f"  WARNINGS: {aliases_skipped_empty_normalized} alias "
                  f"entries normalized to empty string — see logs")

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bootstrap KBL (Korean Basketball League) team coverage "
                    "into sp.teams + sp.team_aliases. Idempotent.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Load existing state + classify manifest entries, but "
             "don't write. Prints counts of what would happen.",
    )
    args = parser.parse_args(argv)
    return asyncio.run(bootstrap(dry_run=args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
