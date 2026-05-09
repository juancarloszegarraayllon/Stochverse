"""Phase 2C.3 alias-tier matcher.

Entry point for the second resolution tier: runs after strict tier
returns NO_MATCH and tries to recover the record via fuzzy
team-name matching, cross-team-collision detection,
exact-match-wins disambiguation, and cross-provider corroboration.

Per-record decision tree:

  1. Sport-classified? Otherwise NO_MATCH (fail_reason=
     'sport_not_classified').
  2. INDIVIDUAL_SPORT_CODES (tennis/mma/boxing/golf/snooker/darts)?
     Defer to Phase 2D: NO_MATCH (fail_reason='deferred_to_2d').
     The personal-name structural path is committed but unused.
  3. kickoff_at present? Otherwise NO_MATCH (fail_reason=
     'kickoff_at_missing'). The corroboration check needs it; the
     downstream fixture lookup also needs it.
  4. Structurally-normalize provider home + away. If either side
     produces no StructuredName: NO_MATCH (fail_reason=
     'structural_normalize_failed').
  5. Per-side find_best_match against CandidateIndex. Apply
     exact-match-wins: if exactly ONE candidate scores 1.0, it wins
     even with other near-misses. If MULTIPLE candidates score 1.0
     OR multiple candidates score above threshold without any
     exact match, the side has a collision.
  6. If anchor failed on either side: NO_MATCH (fail_reason=
     'alias_no_team_resemblance'). The user wants this fail_reason
     surfaced via day-7 query so operators can extend
     _OUTRIGHT_SERIES_PREFIXES / _KALSHI_PROP_TITLE_SUFFIXES.
  7. If collision on either side: REVIEW_QUEUE (regardless of
     fixture-level confidence). The senior-vs-reserve disambiguation
     (Phase 2C.4 roadmap) will reduce this volume by auto-applying
     senior teams over II/U19/B variants.
  8. Otherwise: compute fixture confidence (anchor 0.50 + linear
     avg-of-ratios up to 0.30 + corroboration 0.20). Cross-provider
     corroboration check via find_fixture against sp.fixtures.
     - confidence ≥ 0.85: ALIAS (auto-apply)
     - 0.70 ≤ confidence < 0.85: REVIEW_QUEUE
     - confidence < 0.70: NO_MATCH

The matcher writes nothing. The runner writes resolution_log,
provider.fixture_id, sp.team_aliases (write-back), and
sp.review_queue rows in the same atomic transaction per record
(per Phase 2A.6 design §1).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional

from rapidfuzz import fuzz
from sqlalchemy.ext.asyncio import AsyncSession

from ..fixtures import find_fixture
from ..types import FixtureSignal, MatchResult, ReasonCode, TeamCandidate
from .candidates import CandidateIndex, CandidateTeam
from .normalize import (
    INDIVIDUAL_SPORT_CODES,
    StructuredName,
    structurally_normalize,
)
from .scorer import (
    ANCHOR_SCORE,
    AUTO_APPLY_THRESHOLD,
    CORROBORATION_SCORE,
    PERSONAL_TOKEN_SET_THRESHOLD,
    REVIEW_QUEUE_THRESHOLD,
    TEAM_TOKEN_SET_THRESHOLD,
    TOKEN_SET_MAX_SCORE,
)


# Stable resolver version — stamped into resolution_log per design
# doc D.4. Bump on semantic logic change.
RESOLVER_VERSION = "alias@2c.0"


# Drift window for cross-provider corroboration. Same 30 min as
# strict tier — the corroboration signal is "another provider's
# record points at a fixture in roughly this kickoff slot."
KICKOFF_DRIFT_SEC = 30 * 60


# Exact-match ratio. rapidfuzz returns floats in [0, 100]; after
# /100.0 normalization, perfect string identity (or full-subset
# token coverage like "Brighton" ⊂ "Brighton & Hove Albion")
# yields 1.0.
EXACT_MATCH_RATIO = 1.0


# ── Side-level intermediate ────────────────────────────────────


@dataclass(frozen=True)
class _SideMatch:
    """Per-side outcome of the collision-aware matcher.

    Three terminal states:

      anchor_failed=True
          No candidate scored above the path's threshold. Fixture
          must NO_MATCH.
      collision=True
          Multiple candidates above threshold AND no single 1.0
          dominator. Fixture goes to review_queue regardless of
          fixture-level confidence.
      anchor_failed=False, collision=False
          Single winner identified (either the only above-threshold
          candidate or the unique 1.0 exact-match-wins case).

    `ratio` is the winning candidate's token-set ratio (or the
    highest above-threshold ratio when collision=True; surfaced for
    audit even though it doesn't drive routing).

    `colliding_team_ids` lists every above-threshold candidate's
    team_id (length ≥ 2 when collision=True). Reviewer in 2F sees
    these as candidate_fixtures; Phase 2C.4 senior-team
    disambiguation reads this list.
    """
    team_id: Optional[uuid.UUID]
    canonical_name: str
    ratio: float
    anchor_failed: bool
    collision: bool
    colliding_team_ids: tuple[uuid.UUID, ...]


# ── AliasTierMatcher ───────────────────────────────────────────


class AliasTierMatcher:
    """Phase 2C alias tier — fuzzy team-name matching with
    collision detection. Stateless apart from injected
    CandidateIndex + sport_id lookup.
    """

    def __init__(
        self,
        candidates: CandidateIndex,
        sport_id_by_code_or_name: dict[str, int],
    ) -> None:
        """sport_id_by_code_or_name: same dict shape as 2B's
        StrictMatcher — maps both lowercase code ('soccer') and
        legacy name ('Soccer') to sp.sports.id."""
        self.candidates = candidates
        self.sport_id_by_code_or_name = sport_id_by_code_or_name

    async def match(
        self,
        session: AsyncSession,
        signal: FixtureSignal,
    ) -> MatchResult:
        reason_detail: dict = {
            "provider":           signal.provider,
            "provider_record_id": signal.provider_record_id,
            "sport":              signal.sport,
        }

        # Gate 1: sport classified
        sport_id = self._resolve_sport_id(signal.sport)
        if sport_id is None:
            return self._no_match(
                reason_detail, fail_reason="sport_not_classified",
            )
        reason_detail["sport_id"] = sport_id

        # Gate 2: tennis (and individual sports) deferred to 2D.
        # The personal-name structural normalize + scoring code
        # stays committed; this gate is a runtime filter only.
        sport_lower = (signal.sport or "").lower()
        if sport_lower in INDIVIDUAL_SPORT_CODES:
            reason_detail["individual_sport"] = True
            return self._no_match(
                reason_detail, fail_reason="deferred_to_2d",
            )

        # Gate 3: kickoff required for corroboration + downstream
        # fixture lookup
        if signal.kickoff_at is None:
            return self._no_match(
                reason_detail, fail_reason="kickoff_at_missing",
            )

        # Step 4: structurally normalize provider sides
        home_struct = self._best_normalized_provider_side(
            signal.home_team_candidates, sport_lower,
        )
        away_struct = self._best_normalized_provider_side(
            signal.away_team_candidates, sport_lower,
        )
        if home_struct is None or away_struct is None:
            reason_detail["home_normalize_succeeded"] = home_struct is not None
            reason_detail["away_normalize_succeeded"] = away_struct is not None
            return self._no_match(
                reason_detail, fail_reason="structural_normalize_failed",
            )

        # Step 5: per-side best-match with collision detection
        sport_candidates = self.candidates.candidates_for_sport(sport_id)
        home_match = self._find_best_team_match(home_struct, sport_candidates)
        away_match = self._find_best_team_match(away_struct, sport_candidates)

        # Step 6: anchor failure
        if home_match.anchor_failed or away_match.anchor_failed:
            reason_detail["home_anchor_failed"] = home_match.anchor_failed
            reason_detail["away_anchor_failed"] = away_match.anchor_failed
            reason_detail["home_provider_normalized"] = home_struct.raw
            reason_detail["away_provider_normalized"] = away_struct.raw
            return self._no_match(
                reason_detail, fail_reason="alias_no_team_resemblance",
            )

        # Track candidate ids for the audit trail (review-queue
        # candidate_fixtures field reads from here).
        reason_detail["home_team_id"] = (
            str(home_match.team_id) if home_match.team_id else None
        )
        reason_detail["away_team_id"] = (
            str(away_match.team_id) if away_match.team_id else None
        )
        reason_detail["home_canonical"] = home_match.canonical_name
        reason_detail["away_canonical"] = away_match.canonical_name
        reason_detail["home_ratio"] = round(home_match.ratio, 4)
        reason_detail["away_ratio"] = round(away_match.ratio, 4)

        # Step 7: collision routes to review regardless of confidence
        if home_match.collision or away_match.collision:
            reason_detail["home_collision"] = home_match.collision
            reason_detail["away_collision"] = away_match.collision
            reason_detail["colliding_home_team_ids"] = [
                str(t) for t in home_match.colliding_team_ids
            ]
            reason_detail["colliding_away_team_ids"] = [
                str(t) for t in away_match.colliding_team_ids
            ]
            return MatchResult(
                fixture_id=None,
                confidence=0.0,
                reason_code=ReasonCode.REVIEW_QUEUE,
                reason_detail=reason_detail,
                resolver_version=RESOLVER_VERSION,
                # Surface candidate fixtures the reviewer in 2F
                # would consider. We don't have fixture_ids yet (no
                # ensure_fixture in alias tier per design B.1) — the
                # candidates are team_id pairs.
                candidate_fixtures=list(home_match.colliding_team_ids) + list(
                    away_match.colliding_team_ids
                ),
            )

        # Step 8: fixture-level confidence
        # Anchor floor (both sides anchored, no collision) + linear
        # avg-of-ratios up to 0.30. Corroboration check below adds
        # 0.20 if a fixture exists at this kickoff for the candidate
        # team pair.
        avg_ratio = (home_match.ratio + away_match.ratio) / 2.0
        # Path 2 (team) — same threshold for the anchor and for the
        # quality scaling. Personal path is deferred to 2D.
        token_threshold = TEAM_TOKEN_SET_THRESHOLD

        confidence = ANCHOR_SCORE  # 0.50 floor
        token_contribution = 0.0
        if avg_ratio >= token_threshold:
            span = 1.0 - token_threshold
            progress = (avg_ratio - token_threshold) / span
            token_contribution = 0.20 + progress * 0.10
        confidence += token_contribution
        reason_detail["anchor_score"] = ANCHOR_SCORE
        reason_detail["token_set_contribution"] = round(token_contribution, 4)

        # Cross-provider corroboration: equal-or-NULL competition_id
        # filter inherited from 2A.6 (D.3 lock).
        has_corroboration = await self._check_corroboration(
            session,
            home_team_id=home_match.team_id,
            away_team_id=away_match.team_id,
            kickoff_at=signal.kickoff_at,
        )
        if has_corroboration:
            confidence += CORROBORATION_SCORE
            reason_detail["corroboration_score"] = CORROBORATION_SCORE
        reason_detail["has_cross_provider_corroboration"] = has_corroboration

        confidence = round(confidence, 4)
        reason_detail["alias_score_breakdown"] = {
            "anchor_score": ANCHOR_SCORE,
            "token_set_contribution": round(token_contribution, 4),
            "corroboration_score": CORROBORATION_SCORE if has_corroboration else 0.0,
            "total": confidence,
        }

        # Step 9: routing
        if confidence >= AUTO_APPLY_THRESHOLD:
            # The runner will UPDATE provider.fixture_id and write
            # back to sp.team_aliases. Phase 2C ensure_fixture is
            # NOT called (per design B.1) — the alias tier links to
            # an existing fixture only.
            #
            # We need a fixture_id to write to provider.fixture_id.
            # If corroboration found one (has_corroboration=True),
            # we have it. Otherwise we need to look it up here.
            fixture_id = await self._lookup_or_none(
                session,
                home_team_id=home_match.team_id,
                away_team_id=away_match.team_id,
                kickoff_at=signal.kickoff_at,
            )
            if fixture_id is None:
                # Anchor-passed + above-threshold ratios + no
                # existing fixture. Per design B.1, alias tier
                # doesn't create — fall through to NO_MATCH so the
                # next pass (when a fixture is created elsewhere)
                # can pick it up.
                reason_detail["fixture_lookup"] = "miss"
                return self._no_match(
                    reason_detail,
                    fail_reason="alias_no_existing_fixture",
                    confidence=confidence,
                )
            reason_detail["fixture_id"] = str(fixture_id)
            return MatchResult(
                fixture_id=fixture_id,
                confidence=confidence,
                reason_code=ReasonCode.ALIAS,
                reason_detail=reason_detail,
                resolver_version=RESOLVER_VERSION,
            )

        if confidence >= REVIEW_QUEUE_THRESHOLD:
            return MatchResult(
                fixture_id=None,
                confidence=confidence,
                reason_code=ReasonCode.REVIEW_QUEUE,
                reason_detail=reason_detail,
                resolver_version=RESOLVER_VERSION,
                candidate_fixtures=[home_match.team_id, away_match.team_id],
            )

        return self._no_match(
            reason_detail, fail_reason="below_review_threshold",
            confidence=confidence,
        )

    # ── Helpers ────────────────────────────────────────────────

    def _resolve_sport_id(self, sport_label: str) -> Optional[int]:
        """Same lookup shape as 2B StrictMatcher._resolve_sport_id —
        either lowercase code or capitalized name."""
        if not sport_label:
            return None
        if sport_label in self.sport_id_by_code_or_name:
            return self.sport_id_by_code_or_name[sport_label]
        return self.sport_id_by_code_or_name.get(sport_label.lower())

    @staticmethod
    def _best_normalized_provider_side(
        team_candidates: list[TeamCandidate],
        sport_code: str,
    ) -> Optional[StructuredName]:
        """Pick the highest-weight TeamCandidate whose raw form
        produces a non-None StructuredName."""
        sorted_cands = sorted(team_candidates, key=lambda c: c.weight, reverse=True)
        for cand in sorted_cands:
            struct = structurally_normalize(cand.raw, sport_code=sport_code)
            if struct is not None:
                return struct
        return None

    @staticmethod
    def _find_best_team_match(
        provider_struct: StructuredName,
        candidates: list[CandidateTeam],
    ) -> _SideMatch:
        """Score every candidate (within sport) against the provider
        side. Apply exact-match-wins + cross-team-collision rules.

        Phase 2C.3 only handles the team-name path (Path 2). Personal-
        name candidates would need a different per-side scoring logic
        — deferred to Phase 2D.
        """
        prov_str = " ".join(provider_struct.other_tokens)
        if not prov_str:
            return _SideMatch(
                team_id=None, canonical_name="", ratio=0.0,
                anchor_failed=True, collision=False,
                colliding_team_ids=(),
            )

        # Score every candidate.
        scored: list[tuple[CandidateTeam, float]] = []
        for c in candidates:
            cand_str = " ".join(c.structured.other_tokens)
            if not cand_str:
                continue
            ratio = fuzz.token_set_ratio(prov_str, cand_str) / 100.0
            scored.append((c, ratio))

        above_threshold = [
            (c, r) for c, r in scored if r >= TEAM_TOKEN_SET_THRESHOLD
        ]
        if not above_threshold:
            # No candidate clears the bar — anchor failed.
            return _SideMatch(
                team_id=None, canonical_name="", ratio=0.0,
                anchor_failed=True, collision=False,
                colliding_team_ids=(),
            )

        exact_matches = [
            (c, r) for c, r in above_threshold if r >= EXACT_MATCH_RATIO
        ]

        # Exact-match-wins: a single 1.0 candidate dominates near-
        # misses. ("Manchester United" beats "Manchester City"
        # near-miss because United is the exact match.)
        if len(exact_matches) == 1:
            c, r = exact_matches[0]
            return _SideMatch(
                team_id=c.team_id, canonical_name=c.canonical_name,
                ratio=r, anchor_failed=False, collision=False,
                colliding_team_ids=(c.team_id,),
            )

        # Multiple exact matches: collision (e.g., "Real Sociedad"
        # subset-matches both "Real Sociedad" and "Real Sociedad II"
        # at 1.0 because token-set is direction-agnostic). Senior-
        # team disambiguation (2C.4 roadmap) lifts these out.
        if len(exact_matches) > 1:
            sorted_above = sorted(above_threshold, key=lambda x: x[1], reverse=True)
            top = sorted_above[0]
            return _SideMatch(
                team_id=None, canonical_name=top[0].canonical_name,
                ratio=top[1], anchor_failed=False, collision=True,
                colliding_team_ids=tuple(c.team_id for c, _ in sorted_above),
            )

        # Zero exacts:
        if len(above_threshold) == 1:
            c, r = above_threshold[0]
            return _SideMatch(
                team_id=c.team_id, canonical_name=c.canonical_name,
                ratio=r, anchor_failed=False, collision=False,
                colliding_team_ids=(c.team_id,),
            )

        # Multiple non-exact above threshold: collision.
        sorted_above = sorted(above_threshold, key=lambda x: x[1], reverse=True)
        top = sorted_above[0]
        return _SideMatch(
            team_id=None, canonical_name=top[0].canonical_name,
            ratio=top[1], anchor_failed=False, collision=True,
            colliding_team_ids=tuple(c.team_id for c, _ in sorted_above),
        )

    async def _check_corroboration(
        self,
        session: AsyncSession,
        *,
        home_team_id: uuid.UUID,
        away_team_id: uuid.UUID,
        kickoff_at,
    ) -> bool:
        """Phase 2A.6 equal-or-NULL competition_id filter is inherited
        via find_fixture(competition_id=None) — when competition_id
        is None on the call, find_fixture skips the filter. This is
        the design D.3 carry-forward.

        Tries (home, away) and (away, home) orientations.
        """
        fid, _ = await find_fixture(
            session,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            kickoff_at=kickoff_at,
            drift_sec=KICKOFF_DRIFT_SEC,
        )
        if fid is not None:
            return True
        fid, _ = await find_fixture(
            session,
            home_team_id=away_team_id,
            away_team_id=home_team_id,
            kickoff_at=kickoff_at,
            drift_sec=KICKOFF_DRIFT_SEC,
        )
        return fid is not None

    async def _lookup_or_none(
        self,
        session: AsyncSession,
        *,
        home_team_id: uuid.UUID,
        away_team_id: uuid.UUID,
        kickoff_at,
    ) -> Optional[uuid.UUID]:
        """Like _check_corroboration but returns the fixture_id."""
        fid, _ = await find_fixture(
            session,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            kickoff_at=kickoff_at,
            drift_sec=KICKOFF_DRIFT_SEC,
        )
        if fid is not None:
            return fid
        fid, _ = await find_fixture(
            session,
            home_team_id=away_team_id,
            away_team_id=home_team_id,
            kickoff_at=kickoff_at,
            drift_sec=KICKOFF_DRIFT_SEC,
        )
        return fid

    @staticmethod
    def _no_match(
        reason_detail: dict,
        *,
        fail_reason: str,
        confidence: float = 0.0,
    ) -> MatchResult:
        reason_detail["fail_reason"] = fail_reason
        return MatchResult(
            fixture_id=None,
            confidence=confidence,
            reason_code=ReasonCode.NO_MATCH,
            reason_detail=reason_detail,
            resolver_version=RESOLVER_VERSION,
        )
