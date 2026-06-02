"""Bootstrap Israeli Basketball Premier League (Winner League) team
coverage — Phase 2D.5-A workstream #4.

Data-driven league bootstrap: Israeli BSL teams identified via
asymmetric_anchor_failure resolver signal (Day-31 afternoon
discovery query, post-LBA-apply). ~300+ records/week resolving to
review_queue because sp.teams has no Basketball-sport canonical
for Israeli league team names.

Inserts 14 Israeli BSL teams + their alias coverage into sp.teams
and sp.team_aliases so future BSL records auto-resolve via strict
tier instead of routing to review_queue.

Mirrors scripts/bootstrap_lba.py structure with the same PR #200
alias-safety fix: NOT EXISTS check instead of ON CONFLICT DO NOTHING
on the global (alias_normalized, source) UNIQUE constraint.

Cross-sport collision notes — HIGHEST of Phase 2D.5-A so far:
  - 11 of 14 BSL teams have Israeli football counterparts.
  - Bare-city aliases EXCLUDED for: Tel Aviv, Jerusalem,
    Be'er Sheva/Beer Sheva, Holon, Ra'anana/Raanana, Ness Ziona,
    Ramat Gan, Herzliya, Rishon LeZion/Rishon, Netanya.
  - Bare prefixes EXCLUDED: Maccabi, Hapoel, Ironi, Bnei, Elitzur
    (within-league + future-promotion collision risk).
  - Bare aliases SAFE for: HaEmek/Haemek, Galil Elyon, Kiryat Ata
    (no football collision per operator paste).

Apostrophe + special-character handling:
  - 'Hapoel Be'er Sheva/Dimona' has BOTH apostrophe AND slash.
    Manifest covers all 6 variants (apostrophe×slash×short).
  - 'Maccabi Ironi Ra'anana' has apostrophe in city name.
    Both 'Ra'anana' and 'Raanana' normalize differently;
    manifest covers both forms.

Idempotent — re-running is a no-op for rows + aliases already present.

## Usage

    DATABASE_URL=<url> python scripts/bootstrap_israeli_bsl.py
    DATABASE_URL=<url> python scripts/bootstrap_israeli_bsl.py --dry-run

## Exit codes

  0 — success (writes happened OR no-op idempotent re-run)
  1 — DATABASE_URL not set / engine unavailable
  2 — bad CLI args
  3 — sp.sports missing or doesn't contain 'Basketball', OR
      Pattern D pre-flight failed (endpoint mismatch)
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: E402

from db import async_session  # noqa: E402
from observability import get_logger  # noqa: E402
from resolver._normalize import normalize_name  # noqa: E402
from scripts.daily_diff import _check_pattern_d_endpoint  # noqa: E402
from scripts.israeli_bsl_seed import (  # noqa: E402
    ISRAELI_BSL_ALIAS_SOURCE,
    ISRAELI_BSL_TEAMS_SEED,
)
from sp_models import Team  # noqa: E402


BASKETBALL_SPORT_NAME = "Basketball"


async def bootstrap(dry_run: bool) -> int:
    """Insert/update Israeli BSL team + alias coverage. Returns exit code."""
    log = get_logger("bootstrap.israeli_bsl")
    started = time.monotonic()
    log.info("bootstrap.israeli_bsl.start", dry_run=dry_run,
             manifest_size=len(ISRAELI_BSL_TEAMS_SEED))

    if async_session is None:
        print("ERROR: DATABASE_URL not set or engine unavailable.",
              file=sys.stderr)
        return 1

    # ── Pattern D pre-flight (write-path) ─────────────────────
    allow_non_prod = (
        os.environ.get("DAILY_DIFF_ALLOW_NON_PRODUCTION", "").strip() == "1"
    )
    if not allow_non_prod:
        expected_db_name = (
            os.environ.get("EXPECTED_PRODUCTION_DB_NAME", "").strip()
            or "neondb"
        )
        expected_db_host = (
            os.environ.get("EXPECTED_PRODUCTION_DB_HOST", "").strip() or None
        )
        async with async_session() as preflight_session:
            result = await preflight_session.execute(
                text("SELECT current_database();")
            )
            current_db = result.scalar_one()
        rc, msg = _check_pattern_d_endpoint(
            os.environ.get("DATABASE_URL"),
            current_db,
            expected_db_name=expected_db_name,
            expected_db_host=expected_db_host,
            allow_non_production=False,
        )
        if rc != 0:
            print(f"ERROR: {msg}", file=sys.stderr)
            return 3
        log.info("bootstrap.israeli_bsl.pattern_d.ok",
                 current_database=current_db,
                 expected_db_name=expected_db_name,
                 expected_db_host=expected_db_host)
    else:
        log.info("bootstrap.israeli_bsl.pattern_d.bypass",
                 reason="DAILY_DIFF_ALLOW_NON_PRODUCTION=1")

    async with async_session() as session:
        # ── Step 1: resolve Basketball sport_id ─────────────────────
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
        log.info("bootstrap.israeli_bsl.sport_resolved",
                 sport_id=basketball_sport_id)

        # ── Step 2: bulk-load existing Basketball teams ─────────────
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
        log.info("bootstrap.israeli_bsl.existing_teams_loaded",
                 count=len(existing_teams_by_normalized))

        # ── Step 3: classify team rows (three-branch per KBL) ─────
        teams_to_insert: list[dict] = []
        teams_to_backfill: list[tuple[uuid.UUID, str]] = []
        already_present_count = 0
        empty_normalized_count = 0
        team_ids_by_manifest_index: dict[int, uuid.UUID] = {}

        for idx, (canonical_name, country_code, aliases, _notes) in enumerate(
            ISRAELI_BSL_TEAMS_SEED
        ):
            normalized = normalize_name(canonical_name)
            if not normalized:
                empty_normalized_count += 1
                log.warning(
                    "bootstrap.israeli_bsl.empty_normalized",
                    canonical_name=canonical_name,
                )
                continue
            existing = existing_teams_by_normalized.get(normalized)
            if existing is None:
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
                teams_to_backfill.append((existing_id, country_code))
            else:
                already_present_count += 1

        log.info(
            "bootstrap.israeli_bsl.teams_classified",
            queued_for_insert=len(teams_to_insert),
            queued_for_backfill=len(teams_to_backfill),
            already_present=already_present_count,
            empty_normalized=empty_normalized_count,
        )

        # ── Step 4: bulk-load existing aliases for affected teams ──
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
                    source=ISRAELI_BSL_ALIAS_SOURCE,
                ),
            )).all()
            existing_aliases = {
                (r.team_id, r.alias_normalized) for r in alias_rows
            }
        log.info("bootstrap.israeli_bsl.existing_aliases_loaded",
                 count=len(existing_aliases),
                 source=ISRAELI_BSL_ALIAS_SOURCE)

        # ── Step 5: classify aliases ──────────────────────────────
        # PR #200 lesson: NOT EXISTS check (not ON CONFLICT) on
        # global (alias_normalized, source) UNIQUE constraint.
        aliases_to_insert: list[dict] = []
        aliases_skipped_existing = 0
        aliases_skipped_dup_in_batch = 0
        aliases_skipped_empty_normalized = 0
        aliases_skipped_global_conflict = 0
        in_batch_seen: set[tuple[uuid.UUID, str]] = set()

        all_aliases_for_source: set[str] = set()
        if affected_team_ids:
            global_alias_rows = (await session.execute(
                text(
                    "SELECT alias_normalized "
                    "FROM sp.team_aliases WHERE source = :source"
                ),
                {"source": ISRAELI_BSL_ALIAS_SOURCE},
            )).all()
            all_aliases_for_source = {r.alias_normalized for r in global_alias_rows}

        for idx, (_cname, _ccode, aliases, _notes) in enumerate(
            ISRAELI_BSL_TEAMS_SEED
        ):
            team_id = team_ids_by_manifest_index.get(idx)
            if team_id is None:
                continue
            for alias_raw in aliases:
                alias_normalized = normalize_name(alias_raw)
                if not alias_normalized:
                    aliases_skipped_empty_normalized += 1
                    log.warning(
                        "bootstrap.israeli_bsl.alias_empty_normalized",
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
                if alias_normalized in all_aliases_for_source:
                    aliases_skipped_global_conflict += 1
                    log.warning(
                        "bootstrap.israeli_bsl.alias_global_conflict",
                        alias=alias_raw,
                        alias_normalized=alias_normalized,
                        team_id=str(team_id),
                        note="Same (alias_normalized, source) exists on another team",
                    )
                    continue
                aliases_to_insert.append({
                    "id": uuid.uuid4(),
                    "team_id": team_id,
                    "alias": alias_raw,
                    "alias_normalized": alias_normalized,
                    "source": ISRAELI_BSL_ALIAS_SOURCE,
                    "confidence": 1.0,
                })
                all_aliases_for_source.add(alias_normalized)

        log.info(
            "bootstrap.israeli_bsl.aliases_classified",
            queued_for_insert=len(aliases_to_insert),
            skipped_existing=aliases_skipped_existing,
            skipped_dup_in_batch=aliases_skipped_dup_in_batch,
            skipped_empty_normalized=aliases_skipped_empty_normalized,
            skipped_global_conflict=aliases_skipped_global_conflict,
        )

        # ── Step 6: writes (skipped under --dry-run) ──────────────
        inserted_teams = 0
        backfilled_teams = 0
        inserted_aliases = 0

        if dry_run:
            log.info(
                "bootstrap.israeli_bsl.dry_run_skipping_writes",
                would_insert_teams=len(teams_to_insert),
                would_backfill_teams=len(teams_to_backfill),
                would_insert_aliases=len(aliases_to_insert),
            )
        else:
            if teams_to_insert:
                stmt = pg_insert(Team.__table__).values(teams_to_insert)
                stmt = stmt.on_conflict_do_nothing(index_elements=["id"])
                result = await session.execute(stmt)
                inserted_teams = result.rowcount or 0
            if teams_to_backfill:
                for row_id, code in teams_to_backfill:
                    await session.execute(
                        text(
                            "UPDATE sp.teams SET country_code = :code "
                            "WHERE id = :id AND country_code IS NULL"
                        ),
                        {"id": row_id, "code": code},
                    )
                backfilled_teams = len(teams_to_backfill)

            for alias_data in aliases_to_insert:
                await session.execute(
                    text("""
                        INSERT INTO sp.team_aliases
                          (id, team_id, alias, alias_normalized, source,
                           confidence, created_at)
                        SELECT :id, :team_id, :alias, :alias_normalized,
                               :source, :confidence, NOW()
                        WHERE NOT EXISTS (
                          SELECT 1 FROM sp.team_aliases
                          WHERE alias_normalized = :alias_normalized
                            AND source = :source
                        )
                    """),
                    alias_data,
                )
                inserted_aliases += 1

            await session.commit()

        elapsed = time.monotonic() - started
        log.info(
            "bootstrap.israeli_bsl.complete",
            dry_run=dry_run,
            elapsed_sec=round(elapsed, 2),
            inserted_teams=inserted_teams,
            backfilled_teams=backfilled_teams,
            inserted_aliases=inserted_aliases,
        )

        verb = "Would" if dry_run else ""
        print(f"\nIsraeli BSL bootstrap "
              f"{'dry-run' if dry_run else 'complete'} in {elapsed:.1f}s:")
        print(f"  Manifest entries:               "
              f"{len(ISRAELI_BSL_TEAMS_SEED):>4}")
        print(f"  Teams {verb} insert:            "
              f"{len(teams_to_insert):>4}")
        print(f"  Teams {verb} backfill:          "
              f"{len(teams_to_backfill):>4}")
        print(f"  Teams already present:           {already_present_count:>4}")
        print(f"  Aliases {verb} insert:          "
              f"{len(aliases_to_insert):>4}")
        print(f"  Aliases already present:         {aliases_skipped_existing:>4}")
        if aliases_skipped_global_conflict:
            print(f"  Aliases SKIPPED (global conflict): "
                  f"{aliases_skipped_global_conflict:>4}")
        if aliases_skipped_dup_in_batch:
            print(f"  Aliases dedup'd within batch:    "
                  f"{aliases_skipped_dup_in_batch:>4}")

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bootstrap Israeli BSL (Basketball Premier League) "
                    "team coverage into sp.teams + sp.team_aliases. "
                    "Idempotent.",
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
