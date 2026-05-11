"""Bulk alias-resolution helper.

Per Phase 2B design doc: the strict-tier matcher needs to resolve a
team candidate's normalized string to a canonical sp.teams.id. Doing
that per-candidate against the database (one SELECT per candidate
per match attempt) is the same I/O anti-pattern that killed the
first bootstrap run. This helper loads the entire alias table once
into memory at script start, then resolves in microseconds.

Production scale: ~30k aliases (per the 2A.5 baseline). Hashable
dict keyed on (alias_normalized, sport_id) → set of team_ids. ~5-10
MB resident memory, easily under any worker's heap budget.

Strict-tier semantics:
  - A candidate "resolves" when its normalized string maps to
    exactly ONE team_id within the candidate's sport.
  - Multiple team_ids for the same (alias_normalized, sport_id) =
    AMBIGUOUS — strict tier returns None. Phase 2C+ disambiguation
    can use additional signals (kickoff, competition).
  - For a list of candidates, try in order of weight (highest
    first). First unambiguous resolution wins.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Iterable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .types import TeamCandidate


class AliasResolver:
    """In-memory alias resolution.

    Build via `await AliasResolver.load_all(session)`. Then call
    `.resolve(candidates, sport_id)` for each match attempt.

    Re-load via `await self.refresh(session)` if the alias table
    has been mutated mid-run (rare; review-queue approvals would
    do this in Phase 2F, not in 2B).
    """

    def __init__(self) -> None:
        # (alias_normalized, sport_id) → set of team_ids.
        # Set rather than list so duplicates (same team_id under
        # multiple sources) collapse — strict tier only cares about
        # the team_id, not which source supplied the alias.
        self._index: dict[tuple[str, int], set[uuid.UUID]] = defaultdict(set)

    @classmethod
    async def load_all(cls, session: AsyncSession) -> "AliasResolver":
        """Bulk load all aliases joined to teams (for sport_id).

        One SELECT. Postgres planner does the join; we get a flat
        result set we hash into the in-memory index.
        """
        inst = cls()
        await inst.refresh(session)
        return inst

    async def refresh(self, session: AsyncSession) -> None:
        """Reload the in-memory index from sp.team_aliases ⨝ sp.teams.

        Replaces existing state. Use to pick up alias additions
        without restarting the runner.
        """
        rows = (await session.execute(text(
            """
            SELECT a.alias_normalized,
                   t.sport_id,
                   t.id AS team_id
            FROM sp.team_aliases a
            INNER JOIN sp.teams t ON t.id = a.team_id
            """
        ))).all()
        new_index: dict[tuple[str, int], set[uuid.UUID]] = defaultdict(set)
        for row in rows:
            new_index[(row.alias_normalized, row.sport_id)].add(row.team_id)
        # Atomic swap — readers between calls see either the old
        # state or the new state, never a partial.
        self._index = new_index

    def resolve(
        self,
        candidates: Iterable[TeamCandidate],
        sport_id: int | None,
    ) -> uuid.UUID | None:
        """Return the team_id matching the highest-weight candidate
        with an unambiguous alias hit, or None.

        sport_id can be None (signal had no sport classification).
        In that case the resolver still returns None — strict tier
        requires sport context to disambiguate cross-sport name
        collisions ('Manchester United' could be soccer or
        basketball; sport_id pins it).
        """
        if sport_id is None:
            return None

        # Highest weight first; first unambiguous hit wins.
        sorted_candidates = sorted(
            candidates,
            key=lambda c: c.weight,
            reverse=True,
        )

        for cand in sorted_candidates:
            key = (cand.normalized, sport_id)
            team_ids = self._index.get(key)
            if not team_ids:
                continue
            if len(team_ids) == 1:
                return next(iter(team_ids))
            # Ambiguous (>=2 team_ids for the same normalized+sport).
            # Strict tier punts; alias tier (2C) will disambiguate.
            return None
        return None

    def __len__(self) -> int:
        """Number of (alias_normalized, sport_id) keys loaded.
        Useful for runner startup logs."""
        return len(self._index)

    def stats(self) -> dict[str, int]:
        """Return summary counts for the runner's startup log:
        unique keys, ambiguous keys (>=2 team_ids per key), unique
        team_ids reachable."""
        ambiguous = sum(1 for v in self._index.values() if len(v) > 1)
        unique_teams = set()
        for v in self._index.values():
            unique_teams.update(v)
        return {
            "unique_keys":      len(self._index),
            "ambiguous_keys":   ambiguous,
            "unique_teams_reachable": len(unique_teams),
        }
