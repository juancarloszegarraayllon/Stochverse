"""Bulk-loaded candidate index for alias-tier matching.

Phase 2C.3 — same lifecycle pattern as resolver.aliases.AliasResolver:
load once at runner startup, scan in-memory per match() call.

Reads sp.teams + sp.sports, structurally-normalizes each team's
canonical_name with the team's sport_code, and indexes by:

  by_sport: sport_id → list[CandidateTeam]
      The full per-sport candidate pool. Alias tier scans this when
      the personal-name path can't pre-filter (team-name path has
      no anchor to filter by).

  by_sport_surname: (sport_id, surname) → list[CandidateTeam]
      Personal-name pre-filter. Tennis "Kecmanovic M." normalizes to
      surname='kecmanovic'; lookup is O(1) instead of scanning all
      ~3,500 tennis candidates.

The personal-name pre-filter is built but currently unused — Phase
2C.3 ships with INDIVIDUAL_SPORT_CODES early-exit (deferred_to_2d).
The pre-filter machinery stays so Phase 2D can wire it up without
rebuilding the index.

Memory footprint: ~24,400 teams × ~200 bytes per StructuredName
≈ 5MB resident. Well under any worker's heap budget.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .normalize import StructuredName, structurally_normalize


@dataclass(frozen=True)
class CandidateTeam:
    """One team in the alias-tier candidate pool.

    canonical_name is preserved for the breakdown / review-queue
    audit output (humans want to see "Brighton & Hove Albion", not
    the lowercased token bag).
    """
    team_id: uuid.UUID
    canonical_name: str
    structured: StructuredName


class CandidateIndex:
    """In-memory candidate-team index for alias-tier matching.

    Build via `await CandidateIndex.load_all(session)`. Then call
    .candidates_for_sport(sport_id) (team-name path) or
    .candidates_for_surname(sport_id, surname) (personal-name path,
    Phase 2D).
    """

    def __init__(self) -> None:
        self._by_sport: dict[int, list[CandidateTeam]] = defaultdict(list)
        self._by_sport_surname: dict[tuple[int, str], list[CandidateTeam]] = defaultdict(list)

    @classmethod
    async def load_all(cls, session: AsyncSession) -> "CandidateIndex":
        inst = cls()
        await inst.refresh(session)
        return inst

    async def refresh(self, session: AsyncSession) -> None:
        """Reload the index from sp.teams ⨝ sp.sports.

        Atomic swap on the in-memory structure: builds new dicts
        first, then replaces. Readers between calls see either the
        old state or the new state, never a partial.
        """
        rows = (await session.execute(text(
            """
            SELECT t.id            AS team_id,
                   t.sport_id      AS sport_id,
                   t.canonical_name AS canonical_name,
                   s.code          AS sport_code
            FROM sp.teams t
            INNER JOIN sp.sports s ON s.id = t.sport_id
            """
        ))).all()

        by_sport: dict[int, list[CandidateTeam]] = defaultdict(list)
        by_sport_surname: dict[tuple[int, str], list[CandidateTeam]] = defaultdict(list)

        for row in rows:
            structured = structurally_normalize(
                row.canonical_name, sport_code=row.sport_code,
            )
            if structured is None:
                # Team's canonical_name normalized to nothing — drop.
                # Cause is usually pure-punctuation names; rare.
                continue

            ct = CandidateTeam(
                team_id=row.team_id,
                canonical_name=row.canonical_name,
                structured=structured,
            )
            by_sport[row.sport_id].append(ct)
            if structured.is_personal and structured.surname:
                by_sport_surname[(row.sport_id, structured.surname)].append(ct)

        self._by_sport = by_sport
        self._by_sport_surname = by_sport_surname

    def candidates_for_sport(self, sport_id: int) -> list[CandidateTeam]:
        return self._by_sport.get(sport_id, [])

    def candidates_for_surname(self, sport_id: int, surname: str) -> list[CandidateTeam]:
        """Personal-name pre-filter. Returns candidates whose
        structurally-normalized surname matches exactly. Phase 2D
        uses this; Phase 2C.3 defers personal sports to 2D so
        callers within 2C don't hit this path."""
        return self._by_sport_surname.get((sport_id, surname), [])

    def __len__(self) -> int:
        """Total candidate count across all sports."""
        return sum(len(v) for v in self._by_sport.values())

    def stats(self) -> dict[str, int]:
        """Summary counters for the runner's startup log."""
        unique_sports = len(self._by_sport)
        total_teams = len(self)
        ambiguous_surnames = sum(
            1 for v in self._by_sport_surname.values() if len(v) > 1
        )
        return {
            "unique_sports": unique_sports,
            "total_teams": total_teams,
            "ambiguous_surnames": ambiguous_surnames,
        }
