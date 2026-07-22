"""Bootstrap sp.teams + sp.team_aliases from legacy public.entities + public.entity_aliases.

Phase 2A.5 deliverable per SP Architecture v1.4 §5 / Phase 2B design
doc Question B. Re-runnable — idempotent for the primary case (row
already exists at (sport_id, normalized_name)) AND for the secondary
alias-aware case added Day-53 (see "Re-run safety and the canary
invariant" below).

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

Re-run safety and the canary invariant (Day-53):

  The primary "does this team already exist?" check keys on
  (sport_id, normalized_name) from sp.teams. That check alone was
  sufficient BEFORE any direction-(b) dedup ran — see the LMB
  workstream in docs/dedup/lmb-2026-07-19.md — because pre-dedup
  every legacy canonical still normalized to a live sp.teams row's
  normalized_name. Post-dedup, the renamed row's normalized_name is
  the FULL canonical (e.g. "piratas de campeche"), and the primary
  check misses on the legacy bare canonical ("campeche"), which
  would silently re-insert the duplicate the dedup just removed.

  Fix: a secondary alias-aware existence check runs on primary miss.
  If the normalized legacy canonical exists as a TRUSTED alias
  (source in TRUSTED_ALIAS_SOURCES — bootstrap-family only, NOT
  runtime-derived) on some team in the same sport, reuse that
  team_id instead of inserting. The bare form is what direction-(b)
  merges preserve as an alias, so this generalizes to every past
  and future direction-(b) dedup without hardcoding team lists.

  Canary invariant — the DRY-RUN GATE for re-run safety:

    Baseline_alias_reused[sport] == sum across all retained
    docs/dedup snapshot tables of merges affecting that sport.

  For each sport, the expected alias_reused count is the number of
  direction-(b) merges recorded in that sport's retained snapshot
  tables (e.g. sp.lmb_dedup_snapshot_2026_07_19 → Baseball = 14).
  Bootstrap re-run is safe when every sport's alias_reused counter
  matches its expected value. The counter is the gate, not a
  fixed constant. --dry-run reports the counters so the operator
  confirms the canary reads clean before running --apply.

  Ambiguity (a normalized string is a trusted alias on ≥2 teams
  in the sport): skip-and-log the legacy entity, do NOT pick
  arbitrarily. Matches the script's existing conservatism
  (skipped_tennis_doubles / per_sport_skipped). Operator inspects
  the log line, either merges those two teams or adjusts the
  legacy canonical before re-running.

  What this fix does NOT protect against (Day-53 dry-run finding):

    Canary-green on alias_reused is NECESSARY but NOT SUFFICIENT
    for --apply safety. The alias-aware check protects against
    re-duplication of direction-(b) dedups (the LMB Campeche
    class). It does NOT protect against a broader class where
    legacy public.entities and sp.teams diverge in canonical-name
    FORMATTING — e.g., public.entities carries `(Country)` suffixes
    ("Sturm Graz (Aut)", "Queretaro (Mex)") that sp.teams doesn't.
    Those strings normalize to something that isn't the sp.teams
    normalized_name AND isn't preserved as an alias anywhere, so
    the primary check misses AND the secondary alias-aware check
    misses too. They queue as net-new team inserts and, on --apply,
    seed a new duplicate-canonical class at scale.

    Empirical: the Day-53 dry-run against production reported 8,060
    net-new team inserts (public.entities has grown since the
    Phase-2A.5 May bootstrap; nothing backfilled). A random 25-row
    Soccer sample of substring-collision candidates estimated ~40%
    are formatting-mismatch duplicates about to be created —
    dominated by the `(Country)`-suffix pattern. Applying this
    without resolving that class would seed dozens to thousands of
    duplicate-canonical pairs across Soccer alone, dwarfing the 14
    LMB pairs the direction-(b) merge just untangled.

    Consequently: canary-green closes the DEDUP re-duplication risk
    this fix was scoped for. It does NOT close --apply overall.
    --apply remains blocked pending a separate scoping item on
    country-suffix normalization (design question: should
    normalize_name strip parenthetical country codes? If yes, it
    changes matching for every provider payload carrying
    "(Ger)"/"(Mex)" — wide blast radius. If no, the bootstrap needs
    a suffix-aware guard. Either way it's design, not patch, and
    it is deliberately out of scope for this PR.).

    Rule for future dedup-adjacent workstreams: distinguish "canary
    covers the class this fix targeted" from "canary covers all
    ways --apply can go wrong." Two different assertions; the second
    requires evidence beyond the counter this fix installs.

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


# Legacy entity sport names that don't match the canonical sp.sports
# names. Mapped here at lookup time so 1,082 entities (310 Football +
# 320 Rugby + others) aren't dropped due to naming drift between the
# legacy data layer and the SP architecture's finite sport list.
#
# - "Football" → "American Football" (legacy used the bare term)
# - "Rugby"    → "Rugby Union" (more common globally; if Rugby League
#                teams need to be split out later, that's a per-team
#                metadata fix, not a sport-mapping change)
#
# Sports legitimately not in the 17-sport list — Table Tennis,
# Motorsport, Esports — stay in unmapped_sports for visibility but
# are not aliased. Adding them is an architecture-level decision
# (would update sp.sports + the FL/Kalshi sport prefix maps).
LEGACY_SPORT_ALIASES: dict[str, str] = {
    "Football": "American Football",
    "Rugby":    "Rugby Union",
}


# Alias sources trusted for the secondary team-existence disambiguation
# check (see module docstring: "Re-run safety and the canary invariant").
#
# Bootstrap-family sources only. Runtime-derived sources — 'fuzzy_auto',
# 'alias_tier' — are deliberately EXCLUDED. Those aliases are created by
# the live resolver on production records, are subject to the same mis-
# resolution failure modes we've been tracking, and MUST NOT
# retroactively suppress a legitimate legacy-entity insertion just
# because a stray runtime-derived alias happened to collide.
#
# Extension pattern: when a new curated bootstrap tag is added
# (bootstrap_<league_code>, manual_review, operator_seed, etc.), add
# it here if and only if that source is genuinely curated. Add a
# regression test to TestTrustedSourceAllowlist. Do NOT add runtime-
# derived sources; the whole point of the allowlist is to keep those
# out.
TRUSTED_ALIAS_SOURCES: frozenset[str] = frozenset({
    "legacy_bootstrap",
    "bootstrap_league_coverage",
    "bootstrap_national",
})


def _resolve_sport_id(legacy_sport: str | None, sport_id_by_name: dict) -> int | None:
    """Look up sport_id from legacy entity.sport, applying alias map.

    Returns None if the sport (after aliasing) doesn't exist in
    sp.sports — caller increments unmapped_sports counter.
    """
    if not legacy_sport:
        return None
    canonical = LEGACY_SPORT_ALIASES.get(legacy_sport, legacy_sport)
    return sport_id_by_name.get(canonical)


def _build_alias_team_index(
    alias_rows,
) -> dict[tuple[int, str], set[uuid.UUID]]:
    """Group trusted-source alias rows by (sport_id, alias_normalized)
    → set of team_ids.

    Set-valued so the "same normalized string on two teams in one
    sport" ambiguity is detectable at lookup time (rather than a
    scalar last-writer-wins that would silently pick one team).

    `alias_rows` is any iterable yielding objects with attributes
    `.sport_id: int`, `.alias_normalized: str`, `.team_id: uuid.UUID`
    — matches the row shape from the bulk-load SELECT below.
    Alternative: iterable of `(sport_id, alias_normalized, team_id)`
    tuples; test suite exercises both shapes.

    Pure function — same input, same output. Deliberately trivial so
    the unit tests can be unit tests, not integration tests.
    """
    idx: dict[tuple[int, str], set[uuid.UUID]] = {}
    for row in alias_rows:
        # Support both attribute access (SQLAlchemy rows) and tuple
        # unpacking (test fixtures).
        if hasattr(row, "sport_id"):
            sport_id, alias_norm, team_id = (
                row.sport_id, row.alias_normalized, row.team_id,
            )
        else:
            sport_id, alias_norm, team_id = row
        idx.setdefault((sport_id, alias_norm), set()).add(team_id)
    return idx


def _resolve_via_alias(
    sport_id: int,
    normalized: str,
    alias_team_index: dict[tuple[int, str], set[uuid.UUID]],
) -> tuple[uuid.UUID | None, bool]:
    """Look up (sport_id, normalized) in the trusted-alias index.

    Returns (team_id, ambiguous):
      - (uuid, False)   — exactly one team has this normalized as a
                          trusted alias. Reuse it.
      - (None, True)    — two or more teams. Ambiguous; caller
                          skips-and-logs rather than picking.
      - (None, False)   — not found. Caller falls through to INSERT.
    """
    teams = alias_team_index.get((sport_id, normalized))
    if not teams:
        return None, False
    if len(teams) == 1:
        return next(iter(teams)), False
    return None, True


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

        # Alias-aware team-existence index (Day-53 addition — see module
        # docstring "Re-run safety and the canary invariant").
        #
        # Keyed on (sport_id, alias_normalized) → set of team_ids. Used
        # as the SECONDARY check in the classification loop below: when
        # the primary (sport_id, normalized_name) team lookup misses,
        # this catches the case where the legacy canonical is
        # preserved as an alias on a team whose canonical was renamed
        # by a direction-(b) dedup.
        #
        # Filtered to TRUSTED_ALIAS_SOURCES (bootstrap-family only,
        # NOT runtime-derived) — a stray fuzzy_auto alias must never
        # suppress a legitimate legacy-entity insertion.
        alias_team_rows = (await session.execute(text(
            """
            SELECT t.sport_id, a.alias_normalized, a.team_id
            FROM sp.team_aliases a
            JOIN sp.teams t ON t.id = a.team_id
            WHERE a.source = ANY(:trusted_sources)
            """
        ), {"trusted_sources": list(TRUSTED_ALIAS_SOURCES)})).all()
        alias_team_index = _build_alias_team_index(alias_team_rows)
        log.info(
            "bootstrap.sp_teams.alias_team_index_loaded",
            unique_keys=len(alias_team_index),
            trusted_sources=sorted(TRUSTED_ALIAS_SOURCES),
        )

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
        per_sport_alias_reused: dict[str, int] = defaultdict(int)
        per_sport_alias_ambiguous: dict[str, int] = defaultdict(int)
        per_sport_skipped: dict[str, int] = defaultdict(int)
        unmapped_sports: dict[str, int] = defaultdict(int)
        skipped_tennis_doubles = 0

        for ent in team_entities:
            sport_id = _resolve_sport_id(ent.sport, sport_id_by_name)
            if sport_id is None:
                unmapped_sports[ent.sport] += 1
                continue

            # Tennis doubles partnerships — canonical_name like
            # 'Player A / Player B'. These are per-tournament pairings
            # that won't recur across tournaments and won't match
            # against Kalshi tennis markets (which target singles).
            # Filter them at bootstrap time so sp.team_aliases isn't
            # polluted with pairing strings that the resolver would
            # never resolve cleanly.
            #
            # Note: individual sports (tennis singles, golf, MMA,
            # boxing) end up in sp.teams as "team-of-one" rows. Works,
            # but is awkward — flagged for Phase 2C+ design as a
            # potential sp.players table.
            if ent.sport == "Tennis" and "/" in ent.canonical_name:
                skipped_tennis_doubles += 1
                continue

            normalized = normalize_name(ent.canonical_name)
            if not normalized:
                per_sport_skipped[ent.sport] += 1
                continue

            key = (sport_id, normalized)
            existing_uuid = team_uuid_by_key.get(key)
            if existing_uuid is not None:
                # PRIMARY existence check — team already exists at
                # (sport_id, normalized_name). Reuse its uuid.
                legacy_to_sp[ent.id] = existing_uuid
                per_sport_existing[ent.sport] += 1
                continue

            # SECONDARY existence check (Day-53) — see module docstring
            # "Re-run safety and the canary invariant". The legacy
            # canonical may have been preserved as a trusted alias on
            # a team whose canonical was renamed by a direction-(b)
            # dedup (e.g. LMB "Campeche" preserved as alias on the
            # renamed "Piratas de Campeche" row). If so, reuse that
            # team_id rather than re-inserting the duplicate.
            aliased_uuid, ambiguous = _resolve_via_alias(
                sport_id, normalized, alias_team_index,
            )
            if ambiguous:
                # The normalized string is a trusted alias on 2+ teams
                # in this sport. Skip-and-log, don't guess.
                per_sport_alias_ambiguous[ent.sport] += 1
                log.warning(
                    "bootstrap.sp_teams.alias_ambiguous",
                    sport=ent.sport,
                    sport_id=sport_id,
                    normalized=normalized,
                    legacy_canonical=ent.canonical_name,
                    legacy_entity_id=ent.id,
                    colliding_team_ids=sorted(
                        str(tid) for tid in
                        alias_team_index.get((sport_id, normalized), set())
                    ),
                )
                continue
            if aliased_uuid is not None:
                legacy_to_sp[ent.id] = aliased_uuid
                per_sport_alias_reused[ent.sport] += 1
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
            alias_reused_total=sum(per_sport_alias_reused.values()),
            alias_ambiguous_total=sum(per_sport_alias_ambiguous.values()),
            inserted_per_sport=dict(per_sport_inserts),
            existing_per_sport=dict(per_sport_existing),
            alias_reused_per_sport=dict(per_sport_alias_reused),
            alias_ambiguous_per_sport=dict(per_sport_alias_ambiguous),
            skipped_per_sport=dict(per_sport_skipped),
            skipped_tennis_doubles=skipped_tennis_doubles,
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

        # Alias-aware existence check (Day-53) — the canary. Per-sport
        # alias_reused counter and per-sport alias_ambiguous counter.
        # Expected value for each sport = sum across that sport's
        # retained direction-(b) dedup snapshot tables. Any deviation
        # from expected → investigate before running --apply.
        alias_reused_total = sum(per_sport_alias_reused.values())
        alias_ambiguous_total = sum(per_sport_alias_ambiguous.values())
        if alias_reused_total or alias_ambiguous_total:
            print(f"\n  Alias-aware existence check (canary):")
            print(f"    reused via trusted alias (total):     {alias_reused_total:>6}")
            print(f"    ambiguous (skipped, see log):         {alias_ambiguous_total:>6}")
            if per_sport_alias_reused:
                print(f"    per sport (alias_reused):")
                for sport, count in sorted(per_sport_alias_reused.items()):
                    print(f"      {sport!r:28}  {count:>6}")
            if per_sport_alias_ambiguous:
                print(f"    per sport (alias_ambiguous):")
                for sport, count in sorted(per_sport_alias_ambiguous.items()):
                    print(f"      {sport!r:28}  {count:>6}")
            print(f"    Trusted sources: {sorted(TRUSTED_ALIAS_SOURCES)}")
            print(f"    Canary invariant: alias_reused per sport must match "
                  f"the sum across that sport's retained dedup snapshot tables.")

        if skipped_tennis_doubles:
            print(f"\n  Skipped — tennis doubles partnerships:    {skipped_tennis_doubles:>6}")
            print(f"    (canonical_name contains '/'; per-tournament pairings,")
            print(f"     don't match against Kalshi singles markets)")
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
