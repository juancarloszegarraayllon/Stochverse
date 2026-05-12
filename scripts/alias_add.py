"""Add a single sp.team_aliases row from the command line.

Primitive for two callers:

  1. The Phase 2F.1 anchor_failed admin surface (sub-PR #4) — operator
     clicks "Suggest alias" on an anchor_failed record and gets a
     pre-filled `make alias-add ARGS="..."` command on the clipboard.
     Operator pastes into their terminal and runs it. The script does
     exactly one thing: insert one alias.

  2. The planned 2D.5.1 anchor-failed-report CLI — same primitive,
     called in a loop over a curated batch.

Design intent: small, idempotent, hand-runnable. Does NOT batch, does
NOT prompt, does NOT do fuzzy team matching — the caller (operator or
2D.5.1 CLI) has already decided which sp.teams row this alias should
attach to.

Idempotency: re-running with the same args is a no-op. The (alias_
normalized, source) UNIQUE constraint on sp.team_aliases (per
sp_models.py:201) is the durable guard; this script's pre-check is a
courtesy so the operator sees "already present" instead of an
IntegrityError.

Usage:

    DATABASE_URL=<url> python scripts/alias_add.py \\
        --sport tennis \\
        --team-canonical 'Jannik Sinner' \\
        --alias 'J. Sinner'

    # Or via Makefile:
    make alias-add ARGS="--sport tennis --team-canonical 'Jannik Sinner' --alias 'J. Sinner'"

    # Dry-run prints the resolved (team_id, alias_normalized) without
    # writing. Useful to confirm the operator picked the right team:
    make alias-add ARGS="--sport tennis ... --dry-run"

Exit codes:
  0 — alias inserted OR already present (idempotent path).
  1 — sport not found / team not found / DB error.
  2 — bad arguments.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

# Make project root importable when invoked as `python scripts/...`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from db import async_session  # noqa: E402
from observability import get_logger  # noqa: E402
from resolver._normalize import normalize_name  # noqa: E402


log = get_logger(__name__)


# Conventional sp.team_aliases.source values. The column is TEXT
# (enum-by-convention, not a DB enum), so this list is documentation,
# not enforcement. Adding a new value here means writing it consistently
# across all callers; an audit query should be able to enumerate all
# legitimate sources from this set.
KNOWN_SOURCES = frozenset({
    "legacy_bootstrap",       # Phase 2A.5 bootstrap_sp_teams.py
    "operator_review",        # PR #123 review-queue approve path
    "alias_tier",             # Phase 2C alias-tier auto-apply write-back
    "fuzzy_tier",             # Phase 2D fuzzy-tier auto-apply write-back
    "manual_anchor_failed",   # Phase 2F.1 sub-PR #4 — this script's default
})


DEFAULT_SOURCE = "manual_anchor_failed"


async def add_alias(
    *,
    sport: str,
    team_canonical: str,
    alias: str,
    source: str,
    dry_run: bool,
) -> int:
    """Insert one alias. Returns process exit code."""
    if async_session is None:
        print("ERROR: DATABASE_URL not set or engine unavailable.", file=sys.stderr)
        return 1

    alias_normalized = normalize_name(alias)
    if not alias_normalized:
        print(f"ERROR: --alias normalized to empty string (input: {alias!r})", file=sys.stderr)
        return 2

    async with async_session() as session:
        async with session.begin():
            # 1. Resolve sport_id. sp.sports is ~17 rows; exact-name
            #    lookup is the only safe option (no normalization on
            #    sport names — they're a curated finite set).
            row = (await session.execute(
                text("SELECT id FROM sp.sports WHERE name = :name"),
                {"name": sport},
            )).first()
            if row is None:
                # Surface the available sports so the operator can re-run
                # with the right value without re-grepping the schema.
                available = (await session.execute(
                    text("SELECT name FROM sp.sports ORDER BY name")
                )).scalars().all()
                print(f"ERROR: sport not found: {sport!r}", file=sys.stderr)
                print(f"  Available sports: {', '.join(available)}", file=sys.stderr)
                return 1
            sport_id = row.id

            # 2. Resolve team_id. Exact canonical_name match within the
            #    sport. If not found, surface the closest 3 by trigram
            #    similarity so the operator can spot a typo without a
            #    separate query.
            team_row = (await session.execute(
                text(
                    "SELECT id, canonical_name FROM sp.teams "
                    "WHERE sport_id = :sport_id AND canonical_name = :name"
                ),
                {"sport_id": sport_id, "name": team_canonical},
            )).first()
            if team_row is None:
                similar = (await session.execute(
                    text(
                        "SELECT canonical_name FROM sp.teams "
                        "WHERE sport_id = :sport_id "
                        "ORDER BY similarity(canonical_name, :name) DESC "
                        "LIMIT 3"
                    ),
                    {"sport_id": sport_id, "name": team_canonical},
                )).scalars().all()
                print(
                    f"ERROR: team not found in sport={sport!r}: "
                    f"{team_canonical!r}",
                    file=sys.stderr,
                )
                if similar:
                    print(
                        f"  Closest 3 in this sport: "
                        f"{', '.join(repr(s) for s in similar)}",
                        file=sys.stderr,
                    )
                return 1
            team_id = team_row.id

            # 3. Idempotent check on (alias_normalized, source). The
            #    UNIQUE constraint backs this; the pre-check is a
            #    courtesy so the operator sees "already present" instead
            #    of catching an IntegrityError.
            existing = (await session.execute(
                text(
                    "SELECT team_id FROM sp.team_aliases "
                    "WHERE alias_normalized = :alias_norm AND source = :source"
                ),
                {"alias_norm": alias_normalized, "source": source},
            )).first()
            if existing is not None:
                if existing.team_id == team_id:
                    print(
                        f"  Already present: {alias!r} → {team_canonical!r} "
                        f"(source={source!r}). No change."
                    )
                    return 0
                # Same alias_normalized + source but different team_id.
                # This is a real conflict — the operator wanted to point
                # alias X at team A, but X is already pointed at team B
                # from the same source. Surface and refuse.
                conflict_name = (await session.execute(
                    text(
                        "SELECT canonical_name FROM sp.teams WHERE id = :tid"
                    ),
                    {"tid": existing.team_id},
                )).scalar()
                print(
                    f"ERROR: alias_normalized={alias_normalized!r} + "
                    f"source={source!r} already points to a different team: "
                    f"{conflict_name!r} (team_id={existing.team_id}). "
                    f"Refusing to overwrite — investigate the conflict.",
                    file=sys.stderr,
                )
                return 1

            # 4. Insert.
            if dry_run:
                print(
                    f"  [dry-run] Would insert: alias={alias!r} "
                    f"alias_normalized={alias_normalized!r} → "
                    f"team={team_canonical!r} ({team_id}) "
                    f"source={source!r}"
                )
                return 0

            await session.execute(
                text(
                    "INSERT INTO sp.team_aliases "
                    "(team_id, alias, alias_normalized, source, confidence) "
                    "VALUES (:team_id, :alias, :alias_norm, :source, 1.0) "
                    "ON CONFLICT (alias_normalized, source) DO NOTHING"
                ),
                {
                    "team_id": team_id,
                    "alias": alias,
                    "alias_norm": alias_normalized,
                    "source": source,
                },
            )

    print(
        f"  Inserted: alias={alias!r} alias_normalized={alias_normalized!r} "
        f"→ team={team_canonical!r} ({team_id}) source={source!r}"
    )
    log.info(
        "alias_add.inserted",
        sport=sport,
        team_canonical=team_canonical,
        team_id=str(team_id),
        alias=alias,
        alias_normalized=alias_normalized,
        source=source,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Add one sp.team_aliases row. Idempotent.",
    )
    parser.add_argument(
        "--sport", required=True,
        help="Sport name as it appears in sp.sports.name (e.g. 'tennis').",
    )
    parser.add_argument(
        "--team-canonical", required=True,
        help="Exact canonical_name from sp.teams (case-sensitive). "
             "If unsure, --dry-run will surface the closest 3 matches.",
    )
    parser.add_argument(
        "--alias", required=True,
        help="The provider-supplied alias to attach. Stored as-is in "
             "the `alias` column; normalized (lowercase, accent-strip, "
             "punctuation-strip) for `alias_normalized`.",
    )
    parser.add_argument(
        "--source", default=DEFAULT_SOURCE,
        choices=sorted(KNOWN_SOURCES),
        help=f"sp.team_aliases.source value. Default: {DEFAULT_SOURCE!r}. "
             f"Hard-restricted to KNOWN_SOURCES via argparse `choices` to "
             f"prevent operator typos from polluting the source "
             f"enum-by-convention (e.g. accidentally typing "
             f"'manuel_anchor_failed' and discovering it three months "
             f"later via audit query). To add a new source value, "
             f"update KNOWN_SOURCES in this file FIRST.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Resolve sport + team + check idempotency, but don't INSERT.",
    )
    args = parser.parse_args(argv)

    return asyncio.run(add_alias(
        sport=args.sport,
        team_canonical=args.team_canonical,
        alias=args.alias,
        source=args.source,
        dry_run=args.dry_run,
    ))


if __name__ == "__main__":
    sys.exit(main())
