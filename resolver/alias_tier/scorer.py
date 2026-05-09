"""Composable confidence scoring for alias-tier matches.

Phase 2C.2 per PHASE_2C_DESIGN.md Question C, signed off in PR #90
(rev1 — Pushback 3 dropped the +0.05 drift term).

Confidence model (per match candidate):

    confidence = anchor (0.50)
                 + token_set_quality (up to 0.30)
                 + corroboration (0.20)

Sum = 1.00 exactly when all three signals agree. No clamp needed.

Path-specific thresholds:

    Personal (Path 1):  remainder token-set ratio ≥ 0.85 contributes
                        linear 0.85→+0.20, 1.0→+0.30; below 0.85 → +0.

    Team    (Path 2):   whole-string token-set ratio ≥ 0.92 IS the
                        anchor pass, AND scales linearly 0.92→+0.20,
                        1.0→+0.30 as the quality signal. Below 0.92
                        → anchor fails entirely (no_match).

Routing thresholds (consumed by 2C.3 matcher):

    confidence ≥ AUTO_APPLY_THRESHOLD (0.85)     → auto-apply
    REVIEW_QUEUE_THRESHOLD (0.70) ≤ c < 0.85     → review queue
    Top-1 within TOP_2_MARGIN (0.05) of top-2    → forced review
                                                    (even if ≥ 0.85)
    confidence < 0.70                            → no_match

This module is pure functions; no DB, no I/O. The matcher in 2C.3
calls score_pair() for each candidate the AliasResolver returned,
applies the routing rules, and writes the resolution_log row.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Optional

from rapidfuzz import fuzz

from .normalize import StructuredName


# ── Score contribution constants ────────────────────────────────

ANCHOR_SCORE = 0.50              # required floor — surname (personal) or team-token-set (team)
TOKEN_SET_MAX_SCORE = 0.30       # ceiling for the linear-scaled token-set quality bonus
CORROBORATION_SCORE = 0.20       # cross-provider existing-fixture lookup (set by matcher)


# ── Token-set ratio thresholds (per Pushback 1, design Q A + D.2) ─

# Personal-name path: remainder ratio must clear 0.85 to contribute.
# At threshold → +0.20; at 1.0 → +0.30 (linear).
PERSONAL_TOKEN_SET_THRESHOLD = 0.85

# Team-name path: whole-string ratio must clear 0.92 to ANCHOR.
# Higher than personal because no surname signal carries the safety
# margin. At threshold → +0.20; at 1.0 → +0.30 (linear).
TEAM_TOKEN_SET_THRESHOLD = 0.92


# ── Routing thresholds (consumed by 2C.3) ───────────────────────

AUTO_APPLY_THRESHOLD = 0.85
REVIEW_QUEUE_THRESHOLD = 0.70
TOP_2_MARGIN = 0.05


# ── Dataclasses ─────────────────────────────────────────────────


@dataclass(frozen=True)
class AliasTierScore:
    """One scored candidate. The matcher (2C.3) writes the
    breakdown verbatim into resolution_log.reason_detail.alias_score_breakdown.

    `candidate_team_id` is None when the anchor failed (the candidate
    didn't reach the scoring stage — surname mismatch for personal
    path, or whole-string ratio < 0.92 for team path).
    """
    confidence: float
    breakdown: dict[str, float]
    candidate_team_id: Optional[uuid.UUID]
    anchor_passed: bool


# ── Score-pair function ─────────────────────────────────────────


def score_pair(
    *,
    provider_side: StructuredName,
    candidate_team_id: uuid.UUID,
    candidate_side: StructuredName,
    has_cross_provider_corroboration: bool = False,
) -> AliasTierScore:
    """Score one (provider-side-name, team-candidate) pair.

    Both sides must have been produced by structurally_normalize()
    with the same sport — the scorer doesn't re-validate, since the
    matcher's caller is responsible for sport-scoping the candidate
    set up front (per AliasResolver.resolve(sport_id) discipline
    inherited from Phase 2B).

    Returns AliasTierScore with confidence in [0, 1].

    The matcher (2C.3) calls this once per (provider, candidate)
    pair, ranks the results, and applies AUTO_APPLY_THRESHOLD /
    REVIEW_QUEUE_THRESHOLD / TOP_2_MARGIN to route.
    """
    if provider_side.is_personal != candidate_side.is_personal:
        # Mixing paths is a programmer error, not a data condition.
        # The matcher routes by sport before calling score_pair.
        raise ValueError(
            f"score_pair: provider/candidate path mismatch "
            f"(provider.is_personal={provider_side.is_personal}, "
            f"candidate.is_personal={candidate_side.is_personal})"
        )

    breakdown: dict[str, float] = {}

    if provider_side.is_personal:
        return _score_personal(
            provider_side=provider_side,
            candidate_team_id=candidate_team_id,
            candidate_side=candidate_side,
            has_cross_provider_corroboration=has_cross_provider_corroboration,
            breakdown=breakdown,
        )
    return _score_team(
        provider_side=provider_side,
        candidate_team_id=candidate_team_id,
        candidate_side=candidate_side,
        has_cross_provider_corroboration=has_cross_provider_corroboration,
        breakdown=breakdown,
    )


# ── Path-specific scoring ───────────────────────────────────────


def _score_personal(
    *,
    provider_side: StructuredName,
    candidate_team_id: uuid.UUID,
    candidate_side: StructuredName,
    has_cross_provider_corroboration: bool,
    breakdown: dict[str, float],
) -> AliasTierScore:
    """Path 1 — surname anchor + remainder token-set ratio."""
    # Anchor: surname must match exactly (after normalization).
    if not provider_side.surname or provider_side.surname != candidate_side.surname:
        breakdown["anchor_failed"] = 0.0
        return AliasTierScore(
            confidence=0.0, breakdown=breakdown,
            candidate_team_id=None, anchor_passed=False,
        )
    breakdown["surname_anchor"] = ANCHOR_SCORE

    # Token-set ratio on remainder tokens (initials, given names).
    prov_remainder = " ".join(provider_side.other_tokens)
    cand_remainder = " ".join(candidate_side.other_tokens)
    if prov_remainder and cand_remainder:
        ratio = fuzz.token_set_ratio(prov_remainder, cand_remainder) / 100.0
        if ratio >= PERSONAL_TOKEN_SET_THRESHOLD:
            # Linear: 0.85 → +0.20, 1.0 → +0.30.
            contribution = _linear_contribution(
                ratio, threshold=PERSONAL_TOKEN_SET_THRESHOLD,
            )
            breakdown["personal_token_set"] = round(contribution, 4)
        else:
            breakdown["personal_token_set_below_threshold"] = round(ratio, 4)

    if has_cross_provider_corroboration:
        breakdown["cross_provider_corroboration"] = CORROBORATION_SCORE

    confidence = sum(
        v for k, v in breakdown.items()
        if not k.endswith("_below_threshold") and not k.endswith("_failed")
    )
    return AliasTierScore(
        confidence=round(confidence, 4),
        breakdown=breakdown,
        candidate_team_id=candidate_team_id,
        anchor_passed=True,
    )


def _score_team(
    *,
    provider_side: StructuredName,
    candidate_team_id: uuid.UUID,
    candidate_side: StructuredName,
    has_cross_provider_corroboration: bool,
    breakdown: dict[str, float],
) -> AliasTierScore:
    """Path 2 — whole-string token-set ratio is BOTH anchor and
    quality signal. Threshold 0.92 to clear anchor; below = no match."""
    prov = " ".join(provider_side.other_tokens)
    cand = " ".join(candidate_side.other_tokens)
    if not prov or not cand:
        breakdown["empty_tokens"] = 0.0
        return AliasTierScore(
            confidence=0.0, breakdown=breakdown,
            candidate_team_id=None, anchor_passed=False,
        )

    ratio = fuzz.token_set_ratio(prov, cand) / 100.0
    if ratio < TEAM_TOKEN_SET_THRESHOLD:
        breakdown["team_anchor_below_threshold"] = round(ratio, 4)
        return AliasTierScore(
            confidence=0.0, breakdown=breakdown,
            candidate_team_id=None, anchor_passed=False,
        )

    breakdown["team_anchor"] = ANCHOR_SCORE
    breakdown["team_token_set"] = round(
        _linear_contribution(ratio, threshold=TEAM_TOKEN_SET_THRESHOLD),
        4,
    )

    if has_cross_provider_corroboration:
        breakdown["cross_provider_corroboration"] = CORROBORATION_SCORE

    confidence = sum(
        v for k, v in breakdown.items()
        if not k.endswith("_below_threshold")
    )
    return AliasTierScore(
        confidence=round(confidence, 4),
        breakdown=breakdown,
        candidate_team_id=candidate_team_id,
        anchor_passed=True,
    )


# ── Linear contribution ─────────────────────────────────────────


def _linear_contribution(ratio: float, *, threshold: float) -> float:
    """Linear scale from threshold→+0.20 to 1.0→+0.30.

    ratio == threshold: returns 0.20
    ratio == 1.0:       returns 0.30
    Between: linear interpolation.

    Caller guarantees ratio >= threshold.
    """
    span = 1.0 - threshold
    if span <= 0:
        return TOKEN_SET_MAX_SCORE
    progress = (ratio - threshold) / span
    return 0.20 + progress * 0.10
