"""Bulk competition-resolution helper.

Phase 2A.6: the strict-tier matcher needs to map a provider-supplied
competition_hint to a canonical sp.competitions.id without per-record
DB round-trips. Same I/O pattern as AliasResolver — load the whole
table once into memory, resolve in microseconds.

Production scale: a few hundred competitions across all sports +
providers. Tiny memory footprint.

Indexes maintained:
  - kalshi_series_bases: each base in array → competition_id
  - fl_tournament_stage_ids: each stage_id in array → competition_id

(Polymarket / OddsAPI indexes can follow the same pattern in later
phases when those providers are wired into the resolver.)

Resolve semantics:
  resolve('kalshi', hint) → (competition_id, kind) where kind ∈
    'no_hint'      hint absent / empty — sport-only fallback allowed
    'explicit'     hint mapped to a known competition
    'unresolvable' hint provided but didn't match any seeded base —
                   strict tier MUST punt; this is an unknown competition
                   and auto-applying would silently link to wrong fixture
"""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class CompetitionResolver:
    """In-memory provider-hint → competition_id lookup.

    Construct via `await CompetitionResolver.load_all(session)`. Then
    call `.resolve(provider, hint)` for each match attempt.
    """

    def __init__(self) -> None:
        # Keys are normalized to upper-case for Kalshi (series_ticker
        # convention) and verbatim for FL (stage IDs are opaque tokens).
        self._kalshi_base_index: dict[str, uuid.UUID] = {}
        self._fl_stage_index: dict[str, uuid.UUID] = {}

    @classmethod
    async def load_all(cls, session: AsyncSession) -> "CompetitionResolver":
        inst = cls()
        await inst.refresh(session)
        return inst

    async def refresh(self, session: AsyncSession) -> None:
        rows = (await session.execute(text(
            """
            SELECT id, kalshi_series_bases, fl_tournament_stage_ids
            FROM sp.competitions
            """
        ))).all()
        new_kalshi: dict[str, uuid.UUID] = {}
        new_fl: dict[str, uuid.UUID] = {}
        for row in rows:
            for base in (row.kalshi_series_bases or []):
                new_kalshi[str(base).upper()] = row.id
            for sid in (row.fl_tournament_stage_ids or []):
                new_fl[str(sid)] = row.id
        # Atomic swap.
        self._kalshi_base_index = new_kalshi
        self._fl_stage_index = new_fl

    def resolve(
        self,
        provider: str,
        hint: Optional[str],
    ) -> tuple[Optional[uuid.UUID], str]:
        """Return (competition_id, kind).

        kind values:
          'no_hint'      — hint is None / empty; matcher's policy
                            decides whether sport-only fallback is OK.
          'explicit'     — competition_id resolved from hint.
          'unresolvable' — hint was provided but not in the index.

        For Kalshi the hint may arrive as either a raw series_ticker
        (e.g., 'KXEPLGAME') or a stripped series_base ('KXEPL'). Both
        are tried — strip_known_suffix handles the conversion.
        """
        if hint is None:
            return None, "no_hint"
        h = str(hint).strip()
        if not h:
            return None, "no_hint"

        if provider == "kalshi":
            # Try as-is first (covers callers that already strip).
            cid = self._kalshi_base_index.get(h.upper())
            if cid is not None:
                return cid, "explicit"
            # Strip known sub-market suffix and try again.
            from kalshi_identity import strip_known_suffix
            base, _ = strip_known_suffix(h)
            if base and base != h.upper():
                cid = self._kalshi_base_index.get(base)
                if cid is not None:
                    return cid, "explicit"
            return None, "unresolvable"

        if provider == "fl":
            cid = self._fl_stage_index.get(h)
            if cid is not None:
                return cid, "explicit"
            return None, "unresolvable"

        # Unknown provider — treat as no_hint so the matcher's
        # provider-specific policy can decide.
        return None, "no_hint"

    def __len__(self) -> int:
        return len(self._kalshi_base_index) + len(self._fl_stage_index)

    def stats(self) -> dict[str, int]:
        unique_comps = set(self._kalshi_base_index.values()) | set(self._fl_stage_index.values())
        return {
            "kalshi_bases_indexed":    len(self._kalshi_base_index),
            "fl_stage_ids_indexed":    len(self._fl_stage_index),
            "unique_competitions":     len(unique_comps),
        }
