"""Phase 2D.2 fuzzy-tier matcher.

Entry point for the third resolution tier: runs after Phase 2C's
alias tier returns NO_MATCH and tries to recover the record via
fuzzy matching. Two paths:

  Personal-name path (sport in INDIVIDUAL_SPORT_CODES):
    - Surname-anchored lookup via the multi-interpretation index
      built in Phase 2D.1 (CandidateIndex.refresh).
    - Quality contribution = MAX of:
        - Initial expansion compatibility (binary +0.30)
        - Remainder token-set ratio ≥ 0.85 (linear +0.20..+0.30)

  Team-name path (everything else):
    - Character-level fuzz.ratio() ≥ 0.85.
    - Quality contribution = linear scaled.

Confidence model (per design rev1 §C):
    anchor (0.40) + quality (up to 0.30) + corroboration (0.30)
    = 1.00 max with all three signals
    = 0.70 max without corroboration → review_queue boundary

Routing:
    confidence ≥ 0.85 → FUZZY auto-apply
    0.70 ≤ confidence < 0.85 → REVIEW_QUEUE
    confidence < 0.70 → NO_MATCH

The matcher writes nothing. The runner (2D.3) writes resolution_log,
provider.fixture_id, sp.team_aliases (write-back as
source='fuzzy_tier'), and sp.review_queue rows in the same atomic
transaction per record.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional

from rapidfuzz import fuzz
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..alias_tier import (
    AUTO_APPLY_THRESHOLD,
    CandidateIndex,
    INDIVIDUAL_SPORT_CODES,
    REVIEW_QUEUE_THRESHOLD,
    StructuredName,
    structurally_normalize,
)
from ..fixtures import find_fixture
from ..types import FixtureSignal, MatchResult, ReasonCode, TeamCandidate
from .initial_expansion import initials_compatible


# Stable resolver version — stamped into resolution_log per design
# doc D.1. Bump on semantic logic change.
RESOLVER_VERSION = "fuzzy@2d.0"


# ── Confidence model constants (per design rev1 §C) ───────────


# Different from 2C alias-tier (0.50). 2D's anchors are structurally
# weaker — initial-expansion is binary, fuzz.ratio is statistically
# noisier than 2C's token-set — so the anchor floor is lower and
# corroboration carries more weight (+0.30 vs 2C's +0.20).
ANCHOR_SCORE = 0.40
TOKEN_SET_MAX_SCORE = 0.30          # ceiling for the linear-scaled
                                    # token-set OR ratio quality bonus
CORROBORATION_SCORE = 0.30          # cross-provider existing-fixture lookup


# Personal-path threshold (initial expansion is binary, no threshold;
# this applies to the alternative remainder-token-set quality signal).
PERSONAL_REMAINDER_TOKEN_SET_THRESHOLD = 0.85
INITIAL_EXPANSION_BONUS = 0.30      # binary signal, not linearly scaled


# Team-path threshold (character-level fuzz.ratio).
TEAM_FUZZ_RATIO_THRESHOLD = 0.85


# Drift window for cross-provider corroboration.
#
# Phase 2D.2.8: widened to 60 min for the fuzzy tier ONLY. Strict tier
# (resolver/matcher.py) and 2C alias tier (resolver/alias_tier/matcher.py)
# stay at 30 min because their tighter anchor signals (exact alias hits)
# don't need slack to meet the same-fixture bar.
#
# Calibration evidence (PR #103, scripts/investigate_corroboration_gap.sql):
#   Q1 tournament overlap: 100%      → tournament gap ruled out
#   Q2 kickoff alignment:  median 30, max 30 → pile-up at the 30-min edge,
#                          confirming many same-fixture pairs sit at
#                          31-60 min offsets and are silently rejected
#   Q3 drift band lift:    85% at ±30min → 100% at ±60min (+15pp),
#                          mean fixture count 9.37 → 17.90 (~2× candidates)
#
# Per Path B in design §E.8: widen drift_sec for fuzzy tier to recover the
# corroboration headroom. Wider drift on the fuzzy tier is consistent with
# its looser confidence model — anchor (0.40) + quality (0.30) +
# corroboration (0.30) — where the corroboration signal is bonus, not
# load-bearing, so a slightly wider candidate window is safe.
KICKOFF_DRIFT_SEC = 60 * 60


# Exact-match ratio for team-path collision tiebreaker.
EXACT_MATCH_RATIO = 1.0


# ── Asymmetric anchor-failure routing (Phase 2D.5 sub-PR #1) ────


# Discriminator string written into reason_detail["routing_shape"]
# when an asymmetric anchor failure routes to REVIEW_QUEUE. Admin
# template branches on this constant. Pinned by test
# tests/test_phase_2d5_asymmetric_routing.py::ASYMMETRIC_ROUTING_SHAPE.
ASYMMETRIC_ANCHOR_FAILURE_ROUTING_SHAPE = "asymmetric_anchor_failure"


# Top-N failed-side trigram candidates surfaced for the operator.
# Matches admin/queries.py:SUGGESTED_TEAMS_PER_SIDE convention so the
# review-queue detail view's anchored-side suggestions and the
# failed-side asymmetric candidates render at consistent breadth.
ASYMMETRIC_FAILED_SIDE_TOP_N = 3


# Minimum trigram similarity for a failed-side candidate to surface.
# Matches admin/queries.py:SUGGESTED_TEAMS_MIN_SIMILARITY. Below this
# the candidate is noise — operators are better served by zero
# candidates than by spurious ones.
ASYMMETRIC_FAILED_SIDE_MIN_SIMILARITY = 0.30


# Kalshi prop-market vocabulary. Records where EITHER parsed_name's
# colon-suffix (or the entire name) matches an entry here are
# excluded from asymmetric routing and stay routed to no_match.
#
# Production-validated against the day-7 retrospective sample
# (2026-05-17). Vocabulary covers Baseball / MMA / Basketball /
# Soccer / Hockey / Football / Esports prop markets in active use
# on Kalshi as of seeding.
#
# Architectural followup tracked in #160: prop markets are
# structurally outside the head-to-head matcher's design contract.
# Proper resolution is ingestion-layer filtering or primary-market
# attachment via event_ticker, not heuristic enhancement. Until
# that lands, this vocabulary needs quarterly review as Kalshi
# introduces new prop types — fail-open by design (unknown
# segments route through to operators rather than getting filtered
# silently), so vocabulary gaps surface as one rejection per new
# prop type.
KALSHI_PROP_MARKET_SEGMENTS: frozenset[str] = frozenset({
    # Baseball props
    "First Inning Run",
    "Team Total",
    "First 5 Spread",
    "First 5 Innings Total",
    "First 5 Innings",
    "Total Runs",
    "Strikeouts",
    "Hits",
    "Extra Innings",
    # MMA props
    "Method of Victory",
    "Round of Finish",
    "Round of Victory",
    "Method of Finish",
    "Go the Distance",
    # Basketball props
    "First Half Winner",
    "First Half Spread",
    "First Half Total",
    "Second Half Winner",
    "Second Half Spread",
    "Second Half Total",
    "Double Doubles",
    "Triple Doubles",
    "Three Pointers",
    "Rebounds",
    "Points Leader",
    "Blocks",
    # Soccer props
    "Total Goals",
    "BTTS",
    "To Advance",
    # Hockey props
    "Overtime",
    "Assists",
    "Points",
    "First Goal",
    "Player Goals",
    "Total Points",
    # Football props
    "4th TD",
    # Esports props
    "Total Maps",
})


def _looks_like_kalshi_prop_market(parsed_name: str) -> bool:
    """Vocabulary-based prop-market detection on a single parsed name.

    Two shapes match:
      - The entire parsed_name is a prop segment (e.g., "Overtime"
        when the parser places the prop segment in one slot and the
        team name in the other).
      - The substring after the first colon is a prop segment
        (e.g., "Colorado: Hits" — substring after ':' is "Hits").

    Strings without a colon AND not equal to a vocabulary entry
    return False. NHL playoff-series titles like "Game 3: Vegas"
    return False because "Game 3" is not in the vocabulary.

    Per #160: this is a precision-optimized heuristic gating an
    architectural workaround. Failopen on unknown segments — they
    reach operators rather than getting filtered silently.
    """
    if not parsed_name:
        return False
    if parsed_name in KALSHI_PROP_MARKET_SEGMENTS:
        return True
    if ":" not in parsed_name:
        return False
    _, _, after_colon = parsed_name.partition(":")
    return after_colon.strip() in KALSHI_PROP_MARKET_SEGMENTS


def _should_exclude_from_asymmetric_routing(
    provider: str,
    home_parsed: str,
    away_parsed: str,
) -> bool:
    """Bilateral prop-market check for the asymmetric routing branch.

    Per production sampling (#160), Kalshi parser places prop
    segments inconsistently — sometimes in home slot, sometimes in
    away slot, sometimes both. Check BOTH parsed names regardless
    of which side anchor-failed.

    Provider-gated on "kalshi" — FL records with colons in team
    names (rare, but the parser preserves them) stay in scope for
    asymmetric routing.
    """
    if provider != "kalshi":
        return False
    return (
        _looks_like_kalshi_prop_market(home_parsed)
        or _looks_like_kalshi_prop_market(away_parsed)
    )


# ── Side-level intermediate ────────────────────────────────────


@dataclass(frozen=True)
class _SideMatch:
    """Per-side outcome of the fuzzy matcher.

    Three terminal states (same shape as 2C `_SideMatch`):

      anchor_failed=True
          Personal: no candidate's interpretation set contains the
            provider surname.
          Team: no candidate's character-level ratio clears the
            0.85 threshold.

      collision=True
          Personal: multiple distinct team_ids returned by
            candidates_for_surname.
          Team: multiple candidates above 0.85 with no single 1.0
            dominator.

      anchor_failed=False, collision=False
          Single winner (personal: the lone candidate; team: the
          single 1.0 if any, else the lone above-threshold).

    `quality_contribution` is the +0..+0.30 quality bonus to add
    on top of the +0.40 anchor floor.
    """
    team_id: Optional[uuid.UUID]
    canonical_name: str
    quality_contribution: float
    anchor_failed: bool
    collision: bool
    colliding_team_ids: tuple[uuid.UUID, ...]


# ── FuzzyTierMatcher ──────────────────────────────────────────


class FuzzyTierMatcher:
    """Phase 2D fuzzy tier — runs after 2C alias tier returns
    NO_MATCH. Stateless apart from injected CandidateIndex +
    sport_id lookup. Paths are sport-driven via
    INDIVIDUAL_SPORT_CODES discrimination (same set as 2C).
    """

    def __init__(
        self,
        candidates: CandidateIndex,
        sport_id_by_code_or_name: dict[str, int],
    ) -> None:
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

        # Gate 2: kickoff required (corroboration + downstream
        # fixture lookup both need it)
        if signal.kickoff_at is None:
            return self._no_match(
                reason_detail, fail_reason="kickoff_at_missing",
            )

        # Gate 3: structurally normalize provider sides
        sport_lower = (signal.sport or "").lower()
        is_personal = sport_lower in INDIVIDUAL_SPORT_CODES
        reason_detail["is_personal"] = is_personal

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

        # Per-side matching
        if is_personal:
            home_match = self._find_personal_match(home_struct, sport_id)
            away_match = self._find_personal_match(away_struct, sport_id)
        else:
            home_match = self._find_team_match(home_struct, sport_id)
            away_match = self._find_team_match(away_struct, sport_id)

        # Preserve parsed names BEFORE any early-return paths below.
        # Phase 2F.1 sub-PR #5: the anchor-failure branch used to drop
        # these, which left the anchor_failed admin surface with no
        # operator-actionable signal (raw_payload.title was the only
        # forward path for Kalshi records). Matched alias tier's
        # already-correct pattern at alias_tier/matcher.py:208-211 by
        # lifting these assignments above the anchor check.
        #
        # `home_provider_normalized` / `away_provider_normalized`: the
        # provider's pre-normalization team string (StructuredName.raw).
        # `home_canonical` / `away_canonical`: matcher's best-effort
        # canonical (may be empty when no candidates existed pre-anchor).
        # Downstream consumer (admin/queries.py:_build_suggested_aliases)
        # prefers _provider_normalized; _canonical is the secondary
        # fallback. Both kept for forensic completeness.
        reason_detail["home_provider_normalized"] = home_struct.raw
        reason_detail["away_provider_normalized"] = away_struct.raw
        reason_detail["home_canonical"] = home_match.canonical_name
        reason_detail["away_canonical"] = away_match.canonical_name

        # Anchor failure — three sub-branches per Phase 2D.5 sub-PR #1:
        #
        #   (i)  Both sides failed (symmetric) — no operator-actionable
        #        signal. Continue routing to no_match. Baseline
        #        behavior, no change from pre-2D.5.
        #
        #   (ii) Exactly one side failed (asymmetric) + bilateral Kalshi
        #        prop-market check matches — record is a prop market,
        #        not a real fixture. Stay routed to no_match with the
        #        asymmetric_excluded forensic marker. See #160 for the
        #        architectural followup (prop markets shouldn't be in
        #        the head-to-head matcher at all).
        #
        #   (iii) Exactly one side failed (asymmetric) + not excluded —
        #         real operator-actionable record. Route to review_queue
        #         with routing_shape set + failed-side top-N trigram
        #         candidates surfaced. Anchored side single-pick, failed
        #         side radio buttons (template branches in sub-PR follow-on).
        home_failed = home_match.anchor_failed
        away_failed = away_match.anchor_failed
        if home_failed or away_failed:
            reason_detail["home_anchor_failed"] = home_failed
            reason_detail["away_anchor_failed"] = away_failed

            # Kalshi prop-market exclusion (bilateral) — applied
            # FIRST regardless of symmetric/asymmetric. Prop markets
            # can present either as symmetric anchor-failure (both
            # sides carry prop suffixes that defeat anchor matching)
            # or asymmetric (prop segment on one side anchors against
            # a team coincidentally, or fails). The forensic marker
            # is meaningful in both cases.
            if _should_exclude_from_asymmetric_routing(
                signal.provider, home_struct.raw, away_struct.raw,
            ):
                reason_detail["asymmetric_excluded"] = "kalshi_prop_market"
                return self._no_match(
                    reason_detail, fail_reason="fuzzy_no_team_resemblance",
                )

            # (i) Symmetric — preserve baseline no_match.
            if home_failed and away_failed:
                return self._no_match(
                    reason_detail, fail_reason="fuzzy_no_team_resemblance",
                )

            # (ii) Asymmetric — route to review_queue.
            reason_detail["routing_shape"] = (
                ASYMMETRIC_ANCHOR_FAILURE_ROUTING_SHAPE
            )
            if home_failed:
                anchored_team_id = away_match.team_id
                failed_parsed = home_struct.raw
            else:
                anchored_team_id = home_match.team_id
                failed_parsed = away_struct.raw
            # Guard: personal-path matcher can return anchor_failed=False
            # with team_id=None when a surname matches multiple candidates
            # (collision case in _find_personal_match). Without this guard
            # the asymmetric branch constructs candidate_fixtures =
            # [None, ...top_n], which fails MatchResult's pydantic
            # validation (candidate_fixtures: list[UUID]) and crashes the
            # resolver. Fall through to no_match — same behavior as
            # pre-PR-#161 for the collision-with-asymmetric-routing case.
            # Issue #170: this was crashing all Tennis (ITF/ATP/Challenger)
            # records + likely MMA/Boxing via the same personal-path code.
            if anchored_team_id is None:
                return self._no_match(
                    reason_detail,
                    fail_reason="fuzzy_collision_no_anchor",
                )
            reason_detail["home_team_id"] = (
                str(home_match.team_id) if home_match.team_id else None
            )
            reason_detail["away_team_id"] = (
                str(away_match.team_id) if away_match.team_id else None
            )
            failed_side_candidates = await self._top_n_trigram_candidates(
                session,
                sport_id=sport_id,
                parsed_name=failed_parsed,
                n=ASYMMETRIC_FAILED_SIDE_TOP_N,
            )
            # LOAD-BEARING INVARIANT — do not reorder without updating
            # downstream consumers:
            #
            #   candidate_fixtures[0]  is the anchored side's team_id
            #                          (matcher's high-confidence pick).
            #   candidate_fixtures[1:] are the failed side's top-N
            #                          trigram candidates (DESC by
            #                          similarity).
            #
            # The admin queries layer (admin/queries.py:
            # get_review_queue_record) slices [1:] for the failed-side
            # candidate list, and the template (admin/templates/
            # _decision_form.html) renders these as radio buttons.
            # Reversing the order, prepending another value, or omitting
            # the anchored team_id will silently corrupt the admin UI
            # AND defeat the asymmetric-validation security gate
            # (admin/queries.py:_validate_candidate_team_id).
            candidate_fixtures = [anchored_team_id] + failed_side_candidates
            return MatchResult(
                fixture_id=None,
                confidence=0.0,
                reason_code=ReasonCode.REVIEW_QUEUE,
                reason_detail=reason_detail,
                resolver_version=RESOLVER_VERSION,
                candidate_fixtures=candidate_fixtures,
            )

        reason_detail["home_team_id"] = (
            str(home_match.team_id) if home_match.team_id else None
        )
        reason_detail["away_team_id"] = (
            str(away_match.team_id) if away_match.team_id else None
        )
        reason_detail["home_quality"] = round(home_match.quality_contribution, 4)
        reason_detail["away_quality"] = round(away_match.quality_contribution, 4)

        # Collision routes to review regardless of confidence
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
                candidate_fixtures=list(home_match.colliding_team_ids) + list(
                    away_match.colliding_team_ids
                ),
            )

        # Fixture-level confidence
        avg_quality = (
            home_match.quality_contribution + away_match.quality_contribution
        ) / 2.0
        confidence = ANCHOR_SCORE + avg_quality
        reason_detail["anchor_score"] = ANCHOR_SCORE
        reason_detail["avg_quality_contribution"] = round(avg_quality, 4)

        # Cross-provider corroboration (per design E.5: 30-min drift,
        # same as 2C). Equal-or-NULL competition_id filter inherited
        # from 2A.6 via find_fixture(competition_id=None).
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
        reason_detail["fuzzy_score_breakdown"] = {
            "anchor_score": ANCHOR_SCORE,
            "avg_quality_contribution": round(avg_quality, 4),
            "corroboration_score": CORROBORATION_SCORE if has_corroboration else 0.0,
            "total": confidence,
        }

        # Routing
        if confidence >= AUTO_APPLY_THRESHOLD:
            # Look up the actual fixture to link. Per design B.1
            # carry-forward, fuzzy tier never creates fixtures —
            # links to existing or returns no_match.
            fixture_id = await self._lookup_fixture_or_none(
                session,
                home_team_id=home_match.team_id,
                away_team_id=away_match.team_id,
                kickoff_at=signal.kickoff_at,
            )
            if fixture_id is None:
                reason_detail["fixture_lookup"] = "miss"
                return self._no_match(
                    reason_detail,
                    fail_reason="fuzzy_no_existing_fixture",
                    confidence=confidence,
                )
            reason_detail["fixture_id"] = str(fixture_id)
            return MatchResult(
                fixture_id=fixture_id,
                confidence=confidence,
                reason_code=ReasonCode.FUZZY,
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

    # ── Personal-path matching ────────────────────────────────

    def _find_personal_match(
        self,
        provider_struct: StructuredName,
        sport_id: int,
    ) -> _SideMatch:
        """Path 1 — surname-anchored via the 2D.1 multi-interpretation
        candidate index.

        Lookup `candidates_for_surname(sport_id, provider.surname)`.
        The candidate-side multi-interpretation index handles the
        "Bautista" → "Roberto Bautista Agut" case (E.3).

        Provider-side fallback (try compound-surname interpretations
        of the provider's tokens) is deferred to a follow-up — the
        candidate-side index handles most cases.
        """
        if not provider_struct.surname:
            # No anchor. Personal path with empty surname can't
            # proceed — usually means structural detection failed.
            return _SideMatch(
                team_id=None, canonical_name="",
                quality_contribution=0.0,
                anchor_failed=True, collision=False,
                colliding_team_ids=(),
            )

        raw_candidates = self.candidates.candidates_for_surname(
            sport_id, provider_struct.surname,
        )
        if not raw_candidates:
            return _SideMatch(
                team_id=None, canonical_name="",
                quality_contribution=0.0,
                anchor_failed=True, collision=False,
                colliding_team_ids=(),
            )

        # De-duplicate by team_id — the multi-interpretation index
        # may surface the same candidate under several keys, but
        # candidates_for_surname returns only the matches for the
        # specific key we passed. So duplicates across interpretation
        # keys aren't an issue here. Still, defensive dedupe in
        # case of repeated team_ids.
        unique_by_team_id: dict[uuid.UUID, "CandidateIndex.CandidateTeam"] = {}
        for c in raw_candidates:
            if c.team_id not in unique_by_team_id:
                unique_by_team_id[c.team_id] = c
        unique_candidates = list(unique_by_team_id.values())

        # Multiple distinct team_ids → collision (per design;
        # senior-vs-reserve ambiguity goes to review queue, same as
        # 2C alias tier).
        if len(unique_candidates) > 1:
            return _SideMatch(
                team_id=None,
                canonical_name=unique_candidates[0].canonical_name,
                quality_contribution=0.0,
                anchor_failed=False, collision=True,
                colliding_team_ids=tuple(c.team_id for c in unique_candidates),
            )

        # Exactly one candidate. Compute quality contribution.
        c = unique_candidates[0]
        quality = self._personal_quality_contribution(provider_struct, c.structured)
        return _SideMatch(
            team_id=c.team_id, canonical_name=c.canonical_name,
            quality_contribution=quality,
            anchor_failed=False, collision=False,
            colliding_team_ids=(c.team_id,),
        )

    @staticmethod
    def _personal_quality_contribution(
        provider_struct: StructuredName,
        candidate_struct: StructuredName,
    ) -> float:
        """Per design C.2: take MAX of (initial expansion binary,
        remainder token-set ratio)."""
        contributions: list[float] = []

        # Initial expansion (binary +0.30)
        if initials_compatible(
            provider_struct.other_tokens,
            candidate_struct.other_tokens,
        ):
            contributions.append(INITIAL_EXPANSION_BONUS)

        # Remainder token-set ratio (linear from threshold → +0.20
        # to 1.0 → +0.30)
        prov = " ".join(provider_struct.other_tokens)
        cand = " ".join(candidate_struct.other_tokens)
        if prov and cand:
            ratio = fuzz.token_set_ratio(prov, cand) / 100.0
            if ratio >= PERSONAL_REMAINDER_TOKEN_SET_THRESHOLD:
                span = 1.0 - PERSONAL_REMAINDER_TOKEN_SET_THRESHOLD
                progress = (ratio - PERSONAL_REMAINDER_TOKEN_SET_THRESHOLD) / span
                contributions.append(0.20 + progress * 0.10)

        if not contributions:
            return 0.0
        return max(contributions)

    # ── Team-path matching ────────────────────────────────────

    def _find_team_match(
        self,
        provider_struct: StructuredName,
        sport_id: int,
    ) -> _SideMatch:
        """Path 2 — character-level fuzz.ratio() across the per-sport
        pool. Threshold 0.85; same exact-match-wins + collision
        rules as 2C alias tier's team path."""
        prov = " ".join(provider_struct.other_tokens)
        if not prov:
            return _SideMatch(
                team_id=None, canonical_name="",
                quality_contribution=0.0,
                anchor_failed=True, collision=False,
                colliding_team_ids=(),
            )

        candidates = self.candidates.candidates_for_sport(sport_id)

        scored: list[tuple] = []  # list[(CandidateTeam, ratio)]
        for c in candidates:
            cand = " ".join(c.structured.other_tokens)
            if not cand:
                continue
            # Character-level ratio (Levenshtein-derived) — different
            # from 2C team path's token-set ratio. Catches
            # misspellings + character-shuffle cases that token-set
            # misses.
            ratio = fuzz.ratio(prov, cand) / 100.0
            if ratio >= TEAM_FUZZ_RATIO_THRESHOLD:
                scored.append((c, ratio))

        if not scored:
            return _SideMatch(
                team_id=None, canonical_name="",
                quality_contribution=0.0,
                anchor_failed=True, collision=False,
                colliding_team_ids=(),
            )

        # Exact-match-wins (same as 2C team path).
        exact_matches = [(c, r) for c, r in scored if r >= EXACT_MATCH_RATIO]
        if len(exact_matches) == 1:
            c, r = exact_matches[0]
            return _SideMatch(
                team_id=c.team_id, canonical_name=c.canonical_name,
                quality_contribution=self._team_quality_contribution(r),
                anchor_failed=False, collision=False,
                colliding_team_ids=(c.team_id,),
            )
        if len(exact_matches) > 1:
            sorted_above = sorted(scored, key=lambda x: x[1], reverse=True)
            top = sorted_above[0]
            return _SideMatch(
                team_id=None, canonical_name=top[0].canonical_name,
                quality_contribution=0.0,
                anchor_failed=False, collision=True,
                colliding_team_ids=tuple(c.team_id for c, _ in sorted_above),
            )
        if len(scored) == 1:
            c, r = scored[0]
            return _SideMatch(
                team_id=c.team_id, canonical_name=c.canonical_name,
                quality_contribution=self._team_quality_contribution(r),
                anchor_failed=False, collision=False,
                colliding_team_ids=(c.team_id,),
            )
        # Multiple non-exact above threshold → collision
        sorted_above = sorted(scored, key=lambda x: x[1], reverse=True)
        top = sorted_above[0]
        return _SideMatch(
            team_id=None, canonical_name=top[0].canonical_name,
            quality_contribution=0.0,
            anchor_failed=False, collision=True,
            colliding_team_ids=tuple(c.team_id for c, _ in sorted_above),
        )

    @staticmethod
    def _team_quality_contribution(ratio: float) -> float:
        """Linear scale 0.85→+0.10, 1.0→+0.30 per design C team-path
        spec. Note: the team path scaling is NARROWER than personal
        path's (which goes 0.85→+0.20). Reasoning: team-path's anchor
        is the same ratio it scales — keeping anchor + quality
        bounded at +0.40 + +0.30 = +0.70 max-without-corroboration
        keeps the math symmetric across paths."""
        if ratio < TEAM_FUZZ_RATIO_THRESHOLD:
            return 0.0
        span = 1.0 - TEAM_FUZZ_RATIO_THRESHOLD
        progress = (ratio - TEAM_FUZZ_RATIO_THRESHOLD) / span
        return 0.10 + progress * 0.20

    # ── Helpers (mostly mirrors 2C alias matcher) ─────────────

    def _resolve_sport_id(self, sport_label: str) -> Optional[int]:
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
        sorted_cands = sorted(team_candidates, key=lambda c: c.weight, reverse=True)
        for cand in sorted_cands:
            struct = structurally_normalize(cand.raw, sport_code=sport_code)
            if struct is not None:
                return struct
        return None

    async def _top_n_trigram_candidates(
        self,
        session: AsyncSession,
        *,
        sport_id: int,
        parsed_name: str,
        n: int,
    ) -> list[uuid.UUID]:
        """pg_trgm-backed top-N similarity lookup against sp.teams for
        the failed-side of an asymmetric anchor failure.

        Mirrors admin/queries.py's SUGGESTED_TEAMS query shape — same
        sport_id gate, same minimum-similarity floor, same DESC
        ordering. Operator UX is consistent across the anchor-failed
        suggest-alias surface and the asymmetric review_queue
        candidate list.

        Returns a possibly-empty list — low-coverage sports may have
        zero candidates clearing the floor for a given parsed_name.
        Zero candidates is acceptable; the operator sees the failed
        parsed_name and can either alias-add a new team or reject
        the record.
        """
        if not parsed_name:
            return []
        rows = (await session.execute(
            text(
                """
                SELECT id
                FROM sp.teams
                WHERE sport_id = :sport_id
                  AND similarity(canonical_name, :parsed) >= :min_sim
                ORDER BY similarity(canonical_name, :parsed) DESC
                LIMIT :limit
                """
            ),
            {
                "sport_id": sport_id,
                "parsed": parsed_name,
                "min_sim": ASYMMETRIC_FAILED_SIDE_MIN_SIMILARITY,
                "limit": n,
            },
        )).all()
        return [r.id for r in rows]

    async def _check_corroboration(
        self,
        session: AsyncSession,
        *,
        home_team_id: uuid.UUID,
        away_team_id: uuid.UUID,
        kickoff_at,
    ) -> bool:
        """Phase 2A.6 equal-or-NULL competition_id filter inherited
        via find_fixture(competition_id=None). Same shape as 2C
        alias tier's _check_corroboration."""
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

    async def _lookup_fixture_or_none(
        self,
        session: AsyncSession,
        *,
        home_team_id: uuid.UUID,
        away_team_id: uuid.UUID,
        kickoff_at,
    ) -> Optional[uuid.UUID]:
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
