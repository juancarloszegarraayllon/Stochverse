"""Resolver contract types.

Per SP Architecture v1.4 §7. These types are the boundary between
provider-specific resolver modules (extract_signal) and the central
matcher (which compares signals to canonical entities).

Pydantic v2 models — boundary validation lives at extract_signal,
not inside the matcher. The matcher trusts the FixtureSignal it
receives.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ReasonCode(str, Enum):
    """Why the matcher decided what it decided.

    Stored in sp.resolution_log.reason_code so audit + replay can
    reconstruct the resolver's logic for any historical decision.
    """
    # Auto-applied (confidence >= auto_apply_threshold, default 0.85):
    STRICT          = "strict"           # exact alias on both teams + kickoff ±30min + competition match
    ALIAS           = "alias"            # alias on both teams + kickoff within sport's drift threshold
    FUZZY           = "fuzzy"            # name similarity ≥ 0.9 + kickoff ±30min
    CORROBORATION   = "corroboration"    # cross-provider agreement on existing fixture

    # Held for human approval:
    REVIEW_QUEUE    = "review_queue"     # below auto_apply_threshold

    # Terminal states without linkage:
    NO_MATCH        = "no_match"         # nothing close enough; new fixture if other signals OK
    UNRESOLVABLE    = "unresolvable"     # signal extraction failed; provider record will be re-tried later


class TeamCandidate(BaseModel):
    """One possible team identity from a provider record.

    Provider records often give multiple representations of a team
    (canonical name, abbreviation, shortname, country-tag form).
    The matcher tries each candidate against sp.team_aliases until
    one resolves to a canonical team_id, or all fail.
    """
    model_config = ConfigDict(frozen=True)

    raw: str                                  # original encoding (preserved for display + alias seeding)
    normalized: str                           # lowercased + accent-stripped + whitespace-collapsed (matching key)
    kind: str                                 # 'name' | 'shortname' | 'abbr' | 'fl_team_id' | 'kalshi_abbr'
    weight: float = Field(ge=0.0, le=1.0, default=1.0)
    """How strongly this candidate identifies the team. Exact provider
    IDs (fl_team_id) get 1.0; soft hints (kalshi 3-letter abbr that
    might collide with another team) get less."""


class FixtureSignal(BaseModel):
    """Standardized fixture description extracted from a provider record.

    The matcher takes a FixtureSignal and finds (or creates) the
    canonical fixture it belongs to. Identical inputs produce
    identical outputs (per architecture P5 — deterministic resolution).
    """
    model_config = ConfigDict(frozen=True)

    provider: str                             # 'fl' | 'kalshi' | 'polymarket' | 'oddsapi'
    provider_record_id: str                   # primary key into the provider's table

    sport: str                                # canonical sport code; '' if unknown
    home_team_candidates: list[TeamCandidate]
    away_team_candidates: list[TeamCandidate]

    kickoff_at: datetime | None               # UTC; None if not yet known (rare for resolvable records)
    kickoff_confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    """1.0 if exact (provider gave us a timestamp). Lower if inferred
    from ticker date + duration estimate (~0.6) or if only date-level
    precision (~0.8)."""

    competition_hint: str | None = None       # provider's tournament/series identifier; resolver maps to sp.competitions
    raw_signals: dict[str, Any] = Field(default_factory=dict)
    """Anything the resolver might want to log or replay against —
    raw abbr_block, raw stage_id, raw payload field names that fed
    extraction. Goes into resolution_log.reason_detail."""


class MatchResult(BaseModel):
    """Central matcher's decision for one FixtureSignal.

    Written to sp.resolution_log; if confidence >= auto_apply_threshold,
    fixture_id is also linked to the provider record. Otherwise the
    record goes to sp.review_queue and fixture_id stays NULL on the
    provider table.
    """
    model_config = ConfigDict(frozen=True)

    fixture_id: UUID | None                   # canonical fixture; None on no_match / unresolvable
    confidence: float = Field(ge=0.0, le=1.0)
    reason_code: ReasonCode
    reason_detail: dict[str, Any] = Field(default_factory=dict)
    """Inputs the matcher considered: candidate fixtures it weighed,
    drift_minutes between kickoffs, alias match strength per side,
    competition match status. Free-form JSONB destination."""

    candidate_fixtures: list[UUID] = Field(default_factory=list)
    """For review_queue routing: the top-K candidates considered.
    The reviewer sees these in the admin UI and picks one (or rejects)."""

    resolver_version: str
    """The resolver's version string at the time of the decision.
    Replay can re-run with a newer version against the same raw
    payload and diff."""
