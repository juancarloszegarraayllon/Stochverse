"""Alias-claim collision audit (Amendment #22 institutionalized).

Phase 2D.5-A institutionalized the pre-apply alias-claim audit
discipline across 9 workstreams (LMB → ABA League). This module
makes the discipline a reusable function instead of a hand-run
SQL query.

## The collision shape

`sp.team_aliases` has a global UNIQUE constraint on
`(alias_normalized, source)` (per PR #200) — but the matcher's
strict-tier `AliasIndex` is keyed on `(alias_normalized, sport_id)`
returning a SET of team_ids. When that set has size > 1, strict
tier punts (`resolver/aliases.py:115-119` returns None on
ambiguous keys). Workstream methodology calls this the
**multi-team_id collision** condition.

Cross-source collisions (legacy_bootstrap row colliding with a
proposed bootstrap_league_coverage row on the same alias_normalized
under the same sport_id but pointing to DIFFERENT team_ids) are
NOT blocked by the UNIQUE constraint. The amendment #22 discipline
exists specifically to catch them pre-apply.

Day-33 AO Mykonou + Day-34 Uralmash variants + Day-35 6-collision
EuroLeague/ABA remediation were all post-apply catches that this
function exists to convert into pre-emit catches.

## Architecture

Pure / impure split:

  - `audit_alias_collisions_pure(proposed, existing) -> Report`
    — pure data function, fully unit-testable without a database
  - `audit_alias_collisions(session, proposed, sport_id) -> Report`
    — async DB wrapper that fetches `existing` and delegates to the
      pure function

This mirrors `scripts/daily_diff._check_pattern_d_endpoint` (also a
pure / impure split per amendment #17).

## Output modes

- Default: `emit_set` returns only `clean` aliases (auto-drops
  colliders). Caller substitutes `emit_set` for `proposed` when
  feeding the bootstrap script.
- Report-only: caller reads `colliding` directly to surface the
  collisions without modifying the proposal set.

`same_team_already_present` is documented as NOT a collision —
it's the BACKFILL case where the legacy stub already carries the
alias that the manifest proposes. Idempotent re-runs land here.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Iterable, Sequence


# ──────────────────────────────────────────────────────────────────────
# Data shapes
# ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ProposedAlias:
    """An alias the caller wants to emit into sp.team_aliases.

    `alias_normalized` is the form the AliasIndex is keyed on (post
    `resolver._normalize.normalize_name`).
    `raw_alias` is the human-readable form for logging.
    `target_team_id` is the team_id the alias should belong to
    after apply (string UUID for portability).
    """
    alias_normalized: str
    raw_alias: str
    target_team_id: str


@dataclass(frozen=True)
class ExistingAliasMapping:
    """One row from `sp.team_aliases` JOIN `sp.teams`.

    `team_id` is the existing owner of the normalized alias.
    `canonical_name` is for human-readable collision reports.
    `source` distinguishes legacy_bootstrap / alias_tier /
    bootstrap_league_coverage / operator_review / fuzzy_tier.
    """
    alias_normalized: str
    team_id: str
    canonical_name: str
    source: str


@dataclass(frozen=True)
class Collision:
    """A proposed alias whose normalized form already maps to one
    or more DIFFERENT team_ids under the given sport_id. Emitting
    this alias would expand the AliasIndex set beyond size 1 and
    trigger strict-tier punt behavior."""
    proposed: ProposedAlias
    conflicting_mappings: tuple[ExistingAliasMapping, ...]


@dataclass(frozen=True)
class CollisionReport:
    """Result of an amendment #22 audit.

    `clean`: safe to emit — no existing mapping under any source
       for this `(alias_normalized, sport_id)`.
    `same_team_already_present`: an existing row already carries
       this alias on the SAME target_team_id (any source) — not a
       collision; the bootstrap script's NOT-EXISTS guard will dedup
       at insert time.
    `colliding`: emitting would create a multi-team_id mapping.
    """
    clean: tuple[ProposedAlias, ...]
    same_team_already_present: tuple[ProposedAlias, ...]
    colliding: tuple[Collision, ...]
    sport_id: int

    @property
    def total_proposed(self) -> int:
        return (
            len(self.clean)
            + len(self.same_team_already_present)
            + len(self.colliding)
        )

    @property
    def emit_set(self) -> tuple[ProposedAlias, ...]:
        """Default mode: auto-drop colliders, include `clean` only.

        `same_team_already_present` is also excluded because the
        bootstrap script's existing NOT-EXISTS guard handles those
        as dedups; including them adds no value and inflates the
        emit count metric.
        """
        return self.clean

    def has_collisions(self) -> bool:
        return bool(self.colliding)

    def summarize(self) -> str:
        return (
            f"audit(sport_id={self.sport_id}, total={self.total_proposed}): "
            f"clean={len(self.clean)}, "
            f"same_team={len(self.same_team_already_present)}, "
            f"colliding={len(self.colliding)}"
        )


# ──────────────────────────────────────────────────────────────────────
# Pure function
# ──────────────────────────────────────────────────────────────────────


def audit_alias_collisions_pure(
    proposed: Sequence[ProposedAlias],
    existing: Sequence[ExistingAliasMapping],
    sport_id: int,
) -> CollisionReport:
    """Classify `proposed` aliases as clean / same-team-already-present
    / colliding against the `existing` set.

    No database access — caller fetches `existing`. Same input always
    produces same output; tested via synthetic fixtures in
    `tests/test_collision_audit.py`.

    `existing` should already be filtered to the same `sport_id` as
    the proposals. This function does not enforce that — caller's
    contract.
    """
    # Bucket existing rows by alias_normalized for O(1) lookup.
    by_normalized: dict[str, list[ExistingAliasMapping]] = {}
    for m in existing:
        by_normalized.setdefault(m.alias_normalized, []).append(m)

    clean: list[ProposedAlias] = []
    same_team: list[ProposedAlias] = []
    colliding: list[Collision] = []

    for p in proposed:
        owners = by_normalized.get(p.alias_normalized, [])
        if not owners:
            clean.append(p)
            continue
        other_team_owners = [
            m for m in owners if m.team_id != p.target_team_id
        ]
        same_team_owners = [
            m for m in owners if m.team_id == p.target_team_id
        ]
        if other_team_owners:
            colliding.append(Collision(
                proposed=p,
                conflicting_mappings=tuple(other_team_owners),
            ))
        elif same_team_owners:
            # Only same-team rows — not a collision, just present
            # already (BACKFILL idempotency case).
            same_team.append(p)
        else:
            # Defensive — partition above is exhaustive but guard
            # against future logic changes.
            clean.append(p)

    return CollisionReport(
        clean=tuple(clean),
        same_team_already_present=tuple(same_team),
        colliding=tuple(colliding),
        sport_id=sport_id,
    )


# ──────────────────────────────────────────────────────────────────────
# Database wrapper (async; SQLAlchemy AsyncSession)
# ──────────────────────────────────────────────────────────────────────


async def audit_alias_collisions(
    session,  # sqlalchemy.ext.asyncio.AsyncSession
    proposed_aliases: Iterable[ProposedAlias],
    sport_id: int,
) -> CollisionReport:
    """Async DB wrapper: fetch existing aliases for the proposed
    normalized forms within `sport_id`, then delegate to
    `audit_alias_collisions_pure`.

    `proposed_aliases` may be a generator or list; consumed once.

    Returns CollisionReport. Caller decides whether to emit
    `report.emit_set` (default auto-drop) or surface
    `report.colliding` for manual review.
    """
    from sqlalchemy import text  # local import — keep pure module pure

    proposed_list = list(proposed_aliases)
    forms = list({p.alias_normalized for p in proposed_list})
    if not forms:
        return CollisionReport(
            clean=tuple(), same_team_already_present=tuple(),
            colliding=tuple(), sport_id=sport_id,
        )

    rows = (await session.execute(
        text(
            "SELECT ta.alias_normalized, "
            "       ta.team_id::text AS team_id, "
            "       ta.source, "
            "       t.canonical_name "
            "FROM sp.team_aliases ta "
            "JOIN sp.teams t ON t.id = ta.team_id "
            "WHERE t.sport_id = :sport_id "
            "  AND ta.alias_normalized = ANY(:forms)"
        ).bindparams(sport_id=sport_id, forms=forms),
    )).all()

    existing = [
        ExistingAliasMapping(
            alias_normalized=r.alias_normalized,
            team_id=str(r.team_id),
            canonical_name=r.canonical_name,
            source=r.source,
        )
        for r in rows
    ]

    return audit_alias_collisions_pure(
        proposed=proposed_list,
        existing=existing,
        sport_id=sport_id,
    )


# ──────────────────────────────────────────────────────────────────────
# Convenience constructors
# ──────────────────────────────────────────────────────────────────────


def propose_alias(
    alias_normalized: str,
    raw_alias: str,
    target_team_id: str | uuid.UUID,
) -> ProposedAlias:
    """Convenience constructor that coerces target_team_id to str."""
    return ProposedAlias(
        alias_normalized=alias_normalized,
        raw_alias=raw_alias,
        target_team_id=str(target_team_id),
    )
