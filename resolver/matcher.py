"""Strict-tier central matcher.

Per Phase 2B design doc §A. Four conditions must all hold for a
strict-tier auto-apply:

  1. kickoff_confidence >= 0.85
  2. Both teams resolve via exact alias hit (sp.team_aliases)
  3. Kickoff drift <= 30 minutes (vs candidate fixture's kickoff_at)
  4. Competition match — see Phase 2A.6 below for current per-provider
     policy.

Phase 2A.6 competition gate (per provider):
  - Kalshi:
      * hint resolves to a known competition_id → require it; pass
        through to find_fixture (NULL-or-equal filter) and to
        ensure_fixture (write the column on create).
      * hint absent → sport-only fallback ALLOWED, logged as
        `kalshi_no_hint_sport_only: true`. Most Kalshi records carry
        a series_ticker so this is rare in practice.
      * hint present but unresolvable → strict tier FAILS
        (`fail_reason='kalshi_competition_unresolvable'`). Re-running
        bootstrap_sp_competitions.py against fresh Kalshi data fixes
        this; bypassing it would silently link to wrong fixtures.
  - FL:
      * Transitional. sp.fl_events.raw_payload doesn't currently
        carry tournament-level sport_id, so FL competitions can't be
        cleanly seeded until Phase 2C. The matcher therefore
        sport-only-falls-back for ALL FL signals and stamps every
        successful match with `fl_transitional_sport_only: true` in
        reason_detail. Day-7 audit + 2C re-resolution pass can
        easily query for these.

On hit: confidence 0.98, reason_code='strict'. The runner writes
fixture_id to the provider record, appends to sp.resolution_log,
and (when needed) creates a new sp.fixtures row via ensure_fixture.

On miss: reason_code='no_match'. Provider record's fixture_id stays
NULL. Phase 2C+ alias / fuzzy / corroboration tiers retry.

Strict tier does NOT:
  - Create new teams
  - Route to review queue
  - Update fixture metadata (scores/state/venue/etc.)
  - Pretend to know orientation when extraction was ambiguous —
    instead, tries both (home, away) and (away, home) when the
    home/away candidate sets share members
"""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from .aliases import AliasResolver
from .competitions import CompetitionResolver
from .fixtures import ensure_fixture, find_fixture
from .types import FixtureSignal, MatchResult, ReasonCode


# Stable resolver version. Bump on semantic logic change so historical
# resolution_log entries identify the algorithm at decision time.
RESOLVER_VERSION = "strict@2a.6"


class StrictMatcher:
    """Strict-tier central matcher.

    Stateless aside from the AliasResolver + CompetitionResolver +
    sport_id lookup table passed at construction. Each match() call
    does at most:
      - Pure-Python alias + competition resolution (microseconds)
      - One SELECT to find_fixture (DB round-trip)
      - One INSERT or one SELECT in ensure_fixture (DB round-trip)
    Total per match: <=2 round-trips. Bulk runners can wrap many
    match() calls in one transaction per chunk for further savings.
    """

    KICKOFF_DRIFT_SEC = 30 * 60                  # 30 min, hard-coded for strict
    MIN_KICKOFF_CONFIDENCE = 0.85
    AUTO_APPLY_CONFIDENCE = 0.98                 # 1.0 reserved for human-verified

    def __init__(
        self,
        aliases: AliasResolver,
        sport_id_by_code_or_name: dict[str, int],
        competitions: Optional[CompetitionResolver] = None,
    ) -> None:
        """sport_id_by_code_or_name maps both lowercase code
        ('soccer', 'tennis') AND legacy name ('Soccer', 'Tennis')
        to the same sp.sports.id. Built at runner startup from
        sp.sports + LEGACY_SPORT_ALIASES.

        `competitions` is the CompetitionResolver. Optional only so
        existing unit tests (mocked DB, no competitions table) keep
        working — production runners always pass one. When None, the
        matcher behaves as if every signal had `competition_hint=None`
        (sport-only fallback for Kalshi + FL transitional path).
        """
        self.aliases = aliases
        self.sport_id_by_code_or_name = sport_id_by_code_or_name
        self.competitions = competitions

    async def match(
        self,
        session: AsyncSession,
        signal: FixtureSignal,
    ) -> MatchResult:
        """Run the four-condition gate. Return MatchResult."""
        # Default reason_detail keys; we mutate-and-return.
        reason_detail: dict = {
            "provider":           signal.provider,
            "provider_record_id": signal.provider_record_id,
            "sport":              signal.sport,
        }
        if signal.competition_hint is not None:
            reason_detail["competition_hint"] = signal.competition_hint

        # ── Gate 1: kickoff confidence ─────────────────────────
        if signal.kickoff_confidence < self.MIN_KICKOFF_CONFIDENCE:
            reason_detail["fail_reason"] = "kickoff_confidence_below_threshold"
            reason_detail["kickoff_confidence"] = signal.kickoff_confidence
            return MatchResult(
                fixture_id=None,
                confidence=0.0,
                reason_code=ReasonCode.NO_MATCH,
                reason_detail=reason_detail,
                resolver_version=RESOLVER_VERSION,
            )
        if signal.kickoff_at is None:
            reason_detail["fail_reason"] = "kickoff_at_missing"
            return MatchResult(
                fixture_id=None,
                confidence=0.0,
                reason_code=ReasonCode.NO_MATCH,
                reason_detail=reason_detail,
                resolver_version=RESOLVER_VERSION,
            )

        # ── Gate 2: sport classification ───────────────────────
        sport_id = self._resolve_sport_id(signal.sport)
        if sport_id is None:
            reason_detail["fail_reason"] = "sport_not_classified"
            return MatchResult(
                fixture_id=None,
                confidence=0.0,
                reason_code=ReasonCode.NO_MATCH,
                reason_detail=reason_detail,
                resolver_version=RESOLVER_VERSION,
            )
        reason_detail["sport_id"] = sport_id

        # ── Gate 3: alias resolution for both sides ────────────
        home_id = self.aliases.resolve(signal.home_team_candidates, sport_id)
        away_id = self.aliases.resolve(signal.away_team_candidates, sport_id)
        if home_id is None or away_id is None:
            reason_detail["fail_reason"] = "alias_resolution_incomplete"
            reason_detail["home_resolved"] = home_id is not None
            reason_detail["away_resolved"] = away_id is not None
            return MatchResult(
                fixture_id=None,
                confidence=0.0,
                reason_code=ReasonCode.NO_MATCH,
                reason_detail=reason_detail,
                resolver_version=RESOLVER_VERSION,
            )
        if home_id == away_id:
            # Both candidate sets resolved to the same team — bug or
            # extremely unusual data. Strict tier punts.
            reason_detail["fail_reason"] = "home_and_away_same_team"
            reason_detail["team_id"] = str(home_id)
            return MatchResult(
                fixture_id=None,
                confidence=0.0,
                reason_code=ReasonCode.NO_MATCH,
                reason_detail=reason_detail,
                resolver_version=RESOLVER_VERSION,
            )
        reason_detail["home_team_id"] = str(home_id)
        reason_detail["away_team_id"] = str(away_id)

        # ── Gate 4: competition gate (per-provider policy) ─────
        competition_id_filter, gate_failure = self._competition_gate(
            signal=signal,
            reason_detail=reason_detail,
        )
        if gate_failure is not None:
            reason_detail["fail_reason"] = gate_failure
            return MatchResult(
                fixture_id=None,
                confidence=0.0,
                reason_code=ReasonCode.NO_MATCH,
                reason_detail=reason_detail,
                resolver_version=RESOLVER_VERSION,
            )

        # ── Find or create fixture ─────────────────────────────
        fixture_id = await find_fixture(
            session,
            home_team_id=home_id,
            away_team_id=away_id,
            kickoff_at=signal.kickoff_at,
            drift_sec=self.KICKOFF_DRIFT_SEC,
            competition_id=competition_id_filter,
        )
        if fixture_id is None:
            # Try the swapped orientation in case extraction was
            # ambiguous (Kalshi abbr_block direction-blind, FL
            # missing). If swapped finds a hit, log the orientation
            # flip in reason_detail so reviewers can verify.
            swapped_id = await find_fixture(
                session,
                home_team_id=away_id,
                away_team_id=home_id,
                kickoff_at=signal.kickoff_at,
                drift_sec=self.KICKOFF_DRIFT_SEC,
                competition_id=competition_id_filter,
            )
            if swapped_id is not None:
                fixture_id = swapped_id
                reason_detail["orientation_flipped"] = True
            else:
                # No existing fixture in either orientation. Create
                # one in the signal's orientation, stamping
                # competition_id when the gate produced one.
                fixture_id, created_new = await ensure_fixture(
                    session,
                    home_team_id=home_id,
                    away_team_id=away_id,
                    kickoff_at=signal.kickoff_at,
                    competition_id=competition_id_filter,
                )
                reason_detail["created_new_fixture"] = created_new

        return MatchResult(
            fixture_id=fixture_id,
            confidence=self.AUTO_APPLY_CONFIDENCE,
            reason_code=ReasonCode.STRICT,
            reason_detail=reason_detail,
            resolver_version=RESOLVER_VERSION,
        )

    def _competition_gate(
        self,
        *,
        signal: FixtureSignal,
        reason_detail: dict,
    ) -> tuple[Optional[uuid.UUID], Optional[str]]:
        """Apply the per-provider competition policy.

        Returns (competition_id_filter, gate_failure_reason).
          - On success, gate_failure_reason is None and
            competition_id_filter is the uuid (may be None when
            sport-only fallback is allowed).
          - On failure, gate_failure_reason is a fail_reason string
            and the matcher returns NO_MATCH.

        Mutates reason_detail in-place to record the gate's decision.
        """
        provider = signal.provider

        # FL: transitional sport-only path until Phase 2C. Always
        # stamp the audit flag on success so day-7 review can trivially
        # filter for these and so the 2C re-resolution pass knows where
        # to redo the competition assignment.
        if provider == "fl":
            reason_detail["fl_transitional_sport_only"] = True
            return None, None

        # Kalshi: full gate.
        if provider == "kalshi":
            if self.competitions is None:
                # No CompetitionResolver wired — degrade to sport-only
                # for Kalshi too, but log the fact so misconfigured
                # runners stand out in resolution_log.
                reason_detail["competitions_index_unavailable"] = True
                return None, None
            cid, kind = self.competitions.resolve("kalshi", signal.competition_hint)
            reason_detail["competition_resolution"] = kind
            if kind == "explicit":
                reason_detail["competition_id"] = str(cid)
                return cid, None
            if kind == "no_hint":
                reason_detail["kalshi_no_hint_sport_only"] = True
                return None, None
            # kind == 'unresolvable' — hint was provided but unknown.
            # Strict tier punts to avoid silently linking to the wrong
            # fixture (e.g., Premier League Cup vs Premier League proper).
            return None, "kalshi_competition_unresolvable"

        # Other providers (polymarket, oddsapi) not yet wired through
        # the matcher. Treat as sport-only fallback.
        return None, None

    def _resolve_sport_id(self, sport_label: str) -> Optional[int]:
        """Look up sport_id by either lowercase code ('soccer') or
        legacy name ('Soccer'). Returns None if neither matches —
        e.g., signal.sport == '' (unclassified) or a sport not in
        sp.sports.
        """
        if not sport_label:
            return None
        # Exact match first (Kalshi extraction uses 'Soccer' shape).
        if sport_label in self.sport_id_by_code_or_name:
            return self.sport_id_by_code_or_name[sport_label]
        # Lowercase code form.
        return self.sport_id_by_code_or_name.get(sport_label.lower())
