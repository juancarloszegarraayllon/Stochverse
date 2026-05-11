"""sp.fixtures helpers — find existing, or ensure (DO-NOTHING + re-fetch).

Per Phase 2B design doc §1, ensure_fixture must NOT modify fixture
metadata (scores, state, venue, score_source, score_as_of,
neutral_ground, behind_closed_doors, stage, tie_id) on conflict.
Those columns are owned by score-aware ingestion paths or future
state-update paths; the resolver's only job is "ensure a row exists
for this team-pair + kickoff".

Two-step pattern: INSERT ... ON CONFLICT DO NOTHING RETURNING id;
if RETURNING is empty (conflict path), SELECT to fetch existing row's
id by the same lookup key. Audit-friendly via reason_detail.created
flag in the resolver's resolution_log row — the matcher records
whether ensure_fixture took the insert path or the conflict path.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from sp_models import Fixture


async def find_fixture(
    session: AsyncSession,
    *,
    home_team_id: uuid.UUID,
    away_team_id: uuid.UUID,
    kickoff_at: datetime,
    drift_sec: int = 30 * 60,           # 30 min — strict tier default
    competition_id: Optional[uuid.UUID] = None,
) -> tuple[Optional[uuid.UUID], Optional[uuid.UUID]]:
    """Return `(fixture_id, fixture_competition_id)` for an existing
    sp.fixtures row matching the lookup, or `(None, None)`.

    Phase 2A.6: returns competition_id alongside id so the matcher can
    audit the equal-or-NULL filter outcome (a Kalshi explicit-comp
    signal that links to a NULL-comp fixture is a transitional case
    that needs flagging for the post-2C backfill).

    Resolves orientation deterministically: home_team_id is exactly
    matched as home; away_team_id as away. The matcher is responsible
    for orientation — it tries (home, away) once and (away, home) on
    miss when extraction was orientation-ambiguous.

    `competition_id` (Phase 2A.6): when provided, restricts matches to
    fixtures whose competition_id is either equal to the filter OR
    NULL. The NULL fallback exists because Phase 2A.6 only seeds
    Kalshi competitions; FL still creates fixtures with NULL
    competition_id (transitional, until Phase 2C). A Kalshi signal
    with explicit competition_id arriving on a fixture FL created
    earlier should still link rather than fork into a duplicate. The
    matcher records `linked_to_null_comp_fixture: true` and the
    fixture id in `null_comp_fixture_pending_backfill` when this
    happens, so the post-2C reconciliation pass can backfill the
    column from `sp.resolution_log`.

    On multiple candidates (e.g., a doubleheader scheduled within
    the drift window with the same teams — extremely rare), returns
    the one closest to kickoff_at by absolute time difference.
    """
    drift = timedelta(seconds=drift_sec)
    earliest = kickoff_at - drift
    latest = kickoff_at + drift

    stmt = select(Fixture.id, Fixture.competition_id).where(
        Fixture.home_team_id == home_team_id,
        Fixture.away_team_id == away_team_id,
        Fixture.kickoff_at >= earliest,
        Fixture.kickoff_at <= latest,
    )
    if competition_id is not None:
        # Match on equal-or-NULL competition_id. See docstring rationale.
        stmt = stmt.where(
            (Fixture.competition_id == competition_id)
            | (Fixture.competition_id.is_(None))
        )
    stmt = stmt.order_by(
        # Closest kickoff first.
        text("ABS(EXTRACT(EPOCH FROM (kickoff_at - :pivot)))").bindparams(
            pivot=kickoff_at,
        )
    ).limit(1)

    row = (await session.execute(stmt)).first()
    if row is None:
        return None, None
    return row.id, row.competition_id


async def ensure_fixture(
    session: AsyncSession,
    *,
    home_team_id: uuid.UUID,
    away_team_id: uuid.UUID,
    kickoff_at: datetime,
    competition_id: Optional[uuid.UUID] = None,
) -> tuple[uuid.UUID, bool]:
    """Ensure a sp.fixtures row exists for this team-pair + kickoff.

    Returns (fixture_id, created_new). `created_new` is True if this
    call inserted a new row, False if it found an existing one. The
    matcher records this in resolution_log.reason_detail so a post-
    parallel-run audit can count "fixtures created by the resolver"
    vs "fixtures linked-to existing rows."

    Strict semantics (per Phase 2B design doc §1):
      - DO NOT modify fixture metadata (scores, state, venue, etc.)
        on conflict. Resolver only ensures the row exists.
      - DO NOT update competition_id on conflict either — if a row
        exists with a different competition_id, that's the existing
        row's truth.
      - Two-step: INSERT ... ON CONFLICT DO NOTHING RETURNING id;
        if RETURNING empty, SELECT existing row by lookup key.

    Lookup key on conflict: (home_team_id, away_team_id, kickoff_at).
    Note this is exact-match on kickoff_at, not the drift window.
    The drift window is only for find_fixture's read-only search;
    ensure_fixture writes at the exact kickoff_at the signal carries.
    """
    new_id = uuid.uuid4()

    # First attempt: INSERT. competition_id may be NULL.
    insert_stmt = text(
        """
        INSERT INTO sp.fixtures
          (id, home_team_id, away_team_id, kickoff_at, competition_id, state)
        VALUES
          (:id, :home_team_id, :away_team_id, :kickoff_at, :competition_id, 'scheduled')
        ON CONFLICT DO NOTHING
        RETURNING id
        """
    )
    result = await session.execute(insert_stmt, {
        "id":             new_id,
        "home_team_id":   home_team_id,
        "away_team_id":   away_team_id,
        "kickoff_at":     kickoff_at,
        "competition_id": competition_id,
    })
    inserted_id = result.scalar_one_or_none()
    if inserted_id is not None:
        # Insert path — new fixture created.
        return inserted_id, True

    # Conflict path — fetch existing row by lookup key.
    # NOTE: sp.fixtures has no UNIQUE constraint on
    # (home_team_id, away_team_id, kickoff_at), only an index for
    # find_fixture's drift-window scan. So the conflict here was on
    # the primary key (id) — extremely unlikely with uuid4.  The
    # real "row already exists" case is when the matcher should have
    # called find_fixture first and got a hit.  This branch is
    # defensive: if we somehow get here, fetch by exact-match lookup
    # and use that id.
    fallback_stmt = select(Fixture.id).where(
        Fixture.home_team_id == home_team_id,
        Fixture.away_team_id == away_team_id,
        Fixture.kickoff_at == kickoff_at,
    ).limit(1)
    existing_id = (await session.execute(fallback_stmt)).scalar_one_or_none()
    if existing_id is None:
        # Nothing matched — implausible since the INSERT just
        # conflicted. Treat as a hard error so the runner logs it.
        raise RuntimeError(
            f"ensure_fixture: INSERT conflicted but lookup found nothing "
            f"({home_team_id}, {away_team_id}, {kickoff_at})"
        )
    return existing_id, False
