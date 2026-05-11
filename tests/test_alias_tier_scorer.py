"""Tests for resolver/alias_tier/scorer.py — Phase 2C.2.

Unit tests only, no DB. Every signal-contribution branch + every
boundary case for the routing thresholds.

Calibration anchor (the user's review concern): tennis case
"Miomir Kecmanovic" vs "Kecmanovic M. (Srb)" without corroboration
should land at 0.50 → no_match (below 0.70 review threshold). With
corroboration: 0.70 → review-queue boundary. Asserted explicitly
below — if this changes, the day-0 prediction needs to be revisited.
"""
from __future__ import annotations

import uuid

import pytest

from resolver.alias_tier import (
    ANCHOR_SCORE,
    AUTO_APPLY_THRESHOLD,
    AliasTierScore,
    CORROBORATION_SCORE,
    PERSONAL_TOKEN_SET_THRESHOLD,
    REVIEW_QUEUE_THRESHOLD,
    StructuredName,
    TEAM_TOKEN_SET_THRESHOLD,
    TOKEN_SET_MAX_SCORE,
    TOP_2_MARGIN,
    score_pair,
    structurally_normalize,
)


# ── Helpers ─────────────────────────────────────────────────────


def _personal(raw: str) -> StructuredName:
    """Convenience — invoke the normalizer for a personal-sport
    string. The scorer takes structurally_normalize output, so we
    don't need to hand-build StructuredName instances here."""
    sn = structurally_normalize(raw, sport_code="tennis")
    assert sn is not None
    return sn


def _team(raw: str) -> StructuredName:
    sn = structurally_normalize(raw, sport_code="soccer")
    assert sn is not None
    return sn


def _tid() -> uuid.UUID:
    return uuid.uuid4()


# ── Constants — guard against accidental edits ──────────────────


class TestThresholdConstants:
    def test_anchor_plus_max_token_plus_corroboration_equals_one(self):
        # Per design rev1: 0.50 + 0.30 + 0.20 = 1.00 exactly.
        assert ANCHOR_SCORE + TOKEN_SET_MAX_SCORE + CORROBORATION_SCORE == 1.0

    def test_routing_thresholds_separate_buckets(self):
        # AUTO_APPLY > REVIEW > 0
        assert 0 < REVIEW_QUEUE_THRESHOLD < AUTO_APPLY_THRESHOLD < 1.0
        # Margin is small but positive.
        assert 0 < TOP_2_MARGIN <= 0.10

    def test_team_threshold_lower_than_personal_post_2c3(self):
        # Phase 2C.3 lowered TEAM_TOKEN_SET_THRESHOLD from 0.92 to
        # 0.78 after the soccer dry-run revealed legitimate matches
        # consistently scored 0.80. Cross-team collision detection
        # in AliasTierMatcher (not the scorer) protects against
        # false positives — multiple candidates above 0.78 force
        # review_queue. The scorer's job is just to compute the
        # raw score.
        assert TEAM_TOKEN_SET_THRESHOLD < PERSONAL_TOKEN_SET_THRESHOLD


# ── Path 1: personal name ───────────────────────────────────────


class TestPersonalAnchor:
    def test_surname_match_passes_anchor(self):
        prov = _personal("Miomir Kecmanovic")
        cand = _personal("Kecmanovic M. (Srb)")
        result = score_pair(
            provider_side=prov,
            candidate_team_id=(team_id := _tid()),
            candidate_side=cand,
        )
        assert result.anchor_passed is True
        assert result.candidate_team_id == team_id
        assert result.breakdown["surname_anchor"] == ANCHOR_SCORE

    def test_surname_mismatch_returns_anchor_failed(self):
        prov = _personal("Miomir Kecmanovic")
        cand = _personal("Carlos Alcaraz")
        result = score_pair(
            provider_side=prov,
            candidate_team_id=_tid(),
            candidate_side=cand,
        )
        assert result.anchor_passed is False
        assert result.confidence == 0.0
        assert result.candidate_team_id is None
        assert "anchor_failed" in result.breakdown

    def test_empty_surname_anchor_fails(self):
        # Single-token candidate has surname; provider is also single-token.
        # Both are surnames; if they match, anchor passes.
        # If provider's surname is empty (shouldn't happen post-normalize
        # but defensive), anchor fails.
        prov = StructuredName(
            raw="", detection_path="personal_single",
            surname="", other_tokens=(),
            is_personal=True,
        )
        cand = _personal("Djokovic")
        result = score_pair(
            provider_side=prov,
            candidate_team_id=_tid(),
            candidate_side=cand,
        )
        assert result.anchor_passed is False


class TestPersonalTokenSet:
    """Remainder token-set ratio. Linear scaling 0.85 → +0.20,
    1.0 → +0.30. Below 0.85 → 0 contribution (anchor still passes;
    candidate can still reach review-queue via corroboration)."""

    def test_perfect_remainder_match_max_contribution(self):
        # Provider and candidate both have remainder "miomir".
        prov = _personal("Miomir Kecmanovic")
        cand_norm = StructuredName(
            raw="Kecmanovic Miomir", detection_path="personal_two_token",
            surname="kecmanovic", other_tokens=("miomir",),
            is_personal=True,
        )
        result = score_pair(
            provider_side=prov, candidate_team_id=_tid(),
            candidate_side=cand_norm,
        )
        # 1.0 ratio → +0.30
        assert result.breakdown.get("personal_token_set") == pytest.approx(0.30)
        # Total without corroboration: 0.50 + 0.30 = 0.80 → review queue.
        assert result.confidence == pytest.approx(0.80)

    def test_remainder_below_threshold_zero_contribution(self):
        # The user's calibration case — Kecmanovic.
        prov = _personal("Miomir Kecmanovic")
        cand = _personal("Kecmanovic M. (Srb)")
        result = score_pair(
            provider_side=prov, candidate_team_id=_tid(),
            candidate_side=cand,
        )
        # Remainders "miomir" vs "m" — token_set_ratio ~29 → below 0.85.
        # No personal_token_set in breakdown (only the below_threshold marker).
        assert "personal_token_set" not in result.breakdown
        assert "personal_token_set_below_threshold" in result.breakdown
        # Anchor only: 0.50 — below review threshold (0.70).
        assert result.confidence == pytest.approx(0.50)
        # Routes to no_match (caller's responsibility, but assert
        # we land in the right bucket via the threshold constants).
        assert result.confidence < REVIEW_QUEUE_THRESHOLD

    def test_no_remainder_on_either_side(self):
        # Single-token names: anchor only, no remainder to score.
        prov = _personal("Djokovic")
        cand = _personal("Djokovic")
        result = score_pair(
            provider_side=prov, candidate_team_id=_tid(),
            candidate_side=cand,
        )
        assert result.anchor_passed is True
        assert "personal_token_set" not in result.breakdown
        assert "personal_token_set_below_threshold" not in result.breakdown
        assert result.confidence == pytest.approx(0.50)


class TestPersonalCorroboration:
    """The +0.20 cross-provider boost lifts borderline cases past
    the review and auto-apply thresholds."""

    def test_corroboration_lifts_kecmanovic_to_review_queue(self):
        # The user's calibration anchor — explicitly preserved as
        # an assertion. Tennis without corroboration: 0.50.
        # With corroboration: 0.70 → review-queue boundary.
        prov = _personal("Miomir Kecmanovic")
        cand = _personal("Kecmanovic M. (Srb)")
        result = score_pair(
            provider_side=prov, candidate_team_id=_tid(),
            candidate_side=cand,
            has_cross_provider_corroboration=True,
        )
        assert result.confidence == pytest.approx(0.70)
        # Right at review-queue boundary (≥ 0.70 means review).
        assert result.confidence >= REVIEW_QUEUE_THRESHOLD
        assert result.confidence < AUTO_APPLY_THRESHOLD

    def test_corroboration_added_to_perfect_remainder_match(self):
        # 0.50 + 0.30 + 0.20 = 1.00 exactly.
        prov = _personal("Miomir Kecmanovic")
        cand_norm = StructuredName(
            raw="Kecmanovic Miomir", detection_path="personal_two_token",
            surname="kecmanovic", other_tokens=("miomir",),
            is_personal=True,
        )
        result = score_pair(
            provider_side=prov, candidate_team_id=_tid(),
            candidate_side=cand_norm,
            has_cross_provider_corroboration=True,
        )
        assert result.confidence == pytest.approx(1.00)

    def test_corroboration_does_not_apply_when_anchor_fails(self):
        # Surname mismatch → anchor fails → corroboration irrelevant.
        prov = _personal("Miomir Kecmanovic")
        cand = _personal("Carlos Alcaraz")
        result = score_pair(
            provider_side=prov, candidate_team_id=_tid(),
            candidate_side=cand,
            has_cross_provider_corroboration=True,
        )
        assert result.confidence == 0.0
        assert "cross_provider_corroboration" not in result.breakdown


# ── Path 2: team name ───────────────────────────────────────────


class TestTeamAnchor:
    """Path 2 anchor IS the token-set ratio at threshold 0.92.
    Below threshold → anchor fails entirely (no_match)."""

    def test_perfect_match_full_score(self):
        prov = _team("Real Madrid")
        cand = _team("Real Madrid")
        result = score_pair(
            provider_side=prov, candidate_team_id=_tid(),
            candidate_side=cand,
        )
        # 1.0 ratio → anchor (+0.50) + linear-max (+0.30) = 0.80
        assert result.confidence == pytest.approx(0.80)
        assert result.breakdown["team_anchor"] == ANCHOR_SCORE
        assert result.breakdown["team_token_set"] == pytest.approx(0.30)

    def test_qualifier_suffix_dropped_passes(self):
        # "São Paulo FC" vs "Sao Paulo" — diacritic + qualifier diff.
        # token_set_ratio: 100 (FC is set-only on one side; intersection
        # of {sao, paulo} carries it).
        prov = _team("São Paulo FC")
        cand = _team("Sao Paulo")
        result = score_pair(
            provider_side=prov, candidate_team_id=_tid(),
            candidate_side=cand,
        )
        assert result.anchor_passed is True
        # Ratio is 100 → max contribution.
        assert result.confidence == pytest.approx(0.80)

    def test_localized_variant_passes(self):
        # "Bayern München" vs "Bayern Munich" — character diff.
        # After diacritic strip: "munchen" vs "munich". Tokens
        # share "bayern" only. ratio ~89 → passes 0.78 (post-2C.3
        # threshold). Pre-2C.3 (0.92 threshold) this was rejected;
        # the dry-run showed the rejection was suppressing real
        # recall.
        prov = _team("Bayern München")
        cand = _team("Bayern Munich")
        result = score_pair(
            provider_side=prov, candidate_team_id=_tid(),
            candidate_side=cand,
        )
        assert result.anchor_passed is True

    def test_cross_team_near_miss_passes_anchor_post_2c3(self):
        # "Manchester United" vs "Manchester City" — shares
        # "manchester". ratio ~81. POST-2C.3 (threshold 0.78) this
        # passes the SCORER's anchor check. Cross-team-rejection is
        # NOT the scorer's job — AliasTierMatcher detects this case
        # via cross-team-collision (multiple candidates above
        # threshold for the same provider input → review queue).
        prov = _team("Manchester United")
        cand = _team("Manchester City")
        result = score_pair(
            provider_side=prov, candidate_team_id=_tid(),
            candidate_side=cand,
        )
        assert result.anchor_passed is True
        # Confidence is in review-queue range without corroboration.
        assert REVIEW_QUEUE_THRESHOLD <= result.confidence < AUTO_APPLY_THRESHOLD

    def test_completely_different_teams_rejected(self):
        # "Real Madrid" vs "Atletico Madrid" — shares "madrid".
        # ratio ~71. Below 0.78 threshold. Anchor fails.
        prov = _team("Real Madrid")
        cand = _team("Atletico Madrid")
        result = score_pair(
            provider_side=prov, candidate_team_id=_tid(),
            candidate_side=cand,
        )
        assert result.anchor_passed is False


class TestTeamCorroboration:
    """Cross-provider corroboration on Path 2 — same +0.20 boost,
    same conditions."""

    def test_perfect_team_match_with_corroboration_hits_one(self):
        prov = _team("Real Madrid")
        cand = _team("Real Madrid")
        result = score_pair(
            provider_side=prov, candidate_team_id=_tid(),
            candidate_side=cand,
            has_cross_provider_corroboration=True,
        )
        assert result.confidence == pytest.approx(1.00)

    def test_below_threshold_anchor_fails_even_with_corroboration(self):
        # Real Madrid vs Atletico Madrid — ratio ~71. Below 0.78
        # threshold even after the 2C.3 lowering. Corroboration
        # cannot save below-anchor cases (design doc Q B: tiebreaker
        # for already-anchored candidates only).
        prov = _team("Real Madrid")
        cand = _team("Atletico Madrid")
        result = score_pair(
            provider_side=prov, candidate_team_id=_tid(),
            candidate_side=cand,
            has_cross_provider_corroboration=True,
        )
        assert result.confidence == 0.0


# ── Routing implications (constants, not the scorer) ───────────


class TestRoutingImplications:
    """The scorer returns confidences; the matcher (2C.3) routes.
    These tests document the expected bucketing for typical cases —
    if any of them shift, day-0 prediction needs reassessment."""

    def test_anchor_only_personal_routes_no_match(self):
        # 0.50 confidence < 0.70 review threshold.
        prov = _personal("Miomir Kecmanovic")
        cand = _personal("Kecmanovic M. (Srb)")
        result = score_pair(
            provider_side=prov, candidate_team_id=_tid(),
            candidate_side=cand,
        )
        assert result.confidence < REVIEW_QUEUE_THRESHOLD

    def test_anchor_plus_corroboration_personal_routes_review(self):
        # 0.70 confidence == review-queue lower bound (inclusive).
        prov = _personal("Miomir Kecmanovic")
        cand = _personal("Kecmanovic M. (Srb)")
        result = score_pair(
            provider_side=prov, candidate_team_id=_tid(),
            candidate_side=cand,
            has_cross_provider_corroboration=True,
        )
        assert REVIEW_QUEUE_THRESHOLD <= result.confidence < AUTO_APPLY_THRESHOLD

    def test_full_signal_personal_routes_auto_apply(self):
        # surname + perfect remainder + corroboration = 1.00 → auto-apply.
        prov = _personal("Miomir Kecmanovic")
        cand_norm = StructuredName(
            raw="Kecmanovic Miomir", detection_path="personal_two_token",
            surname="kecmanovic", other_tokens=("miomir",),
            is_personal=True,
        )
        result = score_pair(
            provider_side=prov, candidate_team_id=_tid(),
            candidate_side=cand_norm,
            has_cross_provider_corroboration=True,
        )
        assert result.confidence >= AUTO_APPLY_THRESHOLD

    def test_perfect_team_match_no_corroboration_routes_review(self):
        # 0.80 → review-queue (not auto-apply).
        # This is the design-doc-spec'd behavior: a perfect team
        # whole-string match without corroboration is review-tier.
        prov = _team("Real Madrid")
        cand = _team("Real Madrid")
        result = score_pair(
            provider_side=prov, candidate_team_id=_tid(),
            candidate_side=cand,
        )
        assert REVIEW_QUEUE_THRESHOLD <= result.confidence < AUTO_APPLY_THRESHOLD


# ── Mismatched paths (programmer error) ─────────────────────────


class TestPathMismatchRejected:
    def test_personal_provider_team_candidate_raises(self):
        prov = _personal("Roger Federer")
        cand = _team("Real Madrid")
        with pytest.raises(ValueError, match="path mismatch"):
            score_pair(
                provider_side=prov, candidate_team_id=_tid(),
                candidate_side=cand,
            )

    def test_team_provider_personal_candidate_raises(self):
        prov = _team("Real Madrid")
        cand = _personal("Roger Federer")
        with pytest.raises(ValueError, match="path mismatch"):
            score_pair(
                provider_side=prov, candidate_team_id=_tid(),
                candidate_side=cand,
            )


# ── Linear contribution math ────────────────────────────────────


class TestLinearContribution:
    """Direct math check on _linear_contribution. Boundary cases
    matter — the design-doc spec is explicit on these values."""

    def test_personal_at_threshold_yields_point_two(self):
        # ratio == 0.85 → +0.20. Construct a case that hits 0.85
        # exactly. This is hard with token_set_ratio — most cases
        # are 0/29/36/89/100. So we test the math directly.
        from resolver.alias_tier.scorer import _linear_contribution
        assert _linear_contribution(0.85, threshold=0.85) == pytest.approx(0.20)

    def test_personal_at_one_yields_point_three(self):
        from resolver.alias_tier.scorer import _linear_contribution
        assert _linear_contribution(1.0, threshold=0.85) == pytest.approx(0.30)

    def test_team_at_threshold_yields_point_two(self):
        from resolver.alias_tier.scorer import _linear_contribution
        # Post-2C.3 threshold = 0.78.
        assert _linear_contribution(0.78, threshold=0.78) == pytest.approx(0.20)

    def test_team_at_one_yields_point_three(self):
        from resolver.alias_tier.scorer import _linear_contribution
        assert _linear_contribution(1.0, threshold=0.78) == pytest.approx(0.30)

    def test_personal_midpoint(self):
        from resolver.alias_tier.scorer import _linear_contribution
        # ratio == 0.925 (halfway between 0.85 and 1.0) → +0.25
        assert _linear_contribution(0.925, threshold=0.85) == pytest.approx(0.25)
