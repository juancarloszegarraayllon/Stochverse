"""Phase 2F.1 sub-PR #5 — fuzzy tier reason_detail preservation
regression tests.

Today's France/Senegal smoke test on PR #133 (Kalshi ticker
`KXWCGAME-26JUN16FRASEN`) AND a second UFC Fight Night record
(`KXUFCFIGHT-26MAY16TGTERS`, MMA) surfaced that
`resolver/fuzzy_tier/matcher.py:217-221` early-returns on anchor
failure without preserving the parsed home/away names in
reason_detail:

    if home_match.anchor_failed or away_match.anchor_failed:
        reason_detail["home_anchor_failed"] = home_match.anchor_failed
        reason_detail["away_anchor_failed"] = away_match.anchor_failed
        return self._no_match(
            reason_detail, fail_reason="fuzzy_no_team_resemblance",
        )
    # ↓ ONLY set BELOW the early-return:
    reason_detail["home_canonical"] = home_match.canonical_name
    reason_detail["away_canonical"] = away_match.canonical_name

By contrast, the alias tier preserves them at
`resolver/alias_tier/matcher.py:208-211`:

    if home_match.anchor_failed or away_match.anchor_failed:
        reason_detail["home_anchor_failed"] = home_match.anchor_failed
        reason_detail["away_anchor_failed"] = away_match.anchor_failed
        reason_detail["home_provider_normalized"] = home_struct.raw
        reason_detail["away_provider_normalized"] = away_struct.raw
        return self._no_match(
            reason_detail, fail_reason="alias_no_team_resemblance",
        )

Effect on downstream: `admin/queries.py:_build_suggested_aliases`
falls back to `reason_detail["{side}_provider_normalized"]` or
`["{side}_canonical"]` to query the suggest-alias trigram lookup.
For fuzzy_no_team_resemblance records, both are missing → the
function returns the `no_parsed_names` state → template surfaces
the raw payload below (PR #137 ships this gracefully; this PR
closes the underlying data loss so records route to Path B or
`ok` state instead).

This file captures the contract: after sub-PR #5 ships, every
fuzzy_no_team_resemblance MatchResult must have parsed names in
reason_detail. Unit tests against the matcher directly; no DB
required for the resolver-side regression.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from resolver import (
    FuzzyTierMatcher,
    FixtureSignal,
    TeamCandidate,
)
from resolver.alias_tier import (
    AliasTierMatcher,
    CandidateIndex,
    structurally_normalize,
)
from resolver.alias_tier.candidates import CandidateTeam
from resolver.fuzzy_tier import candidate_surname_interpretations


# ── Minimal test helpers (mirrored from test_resolver_2c.py /
#    test_resolver_2d.py — kept local so this scaffold is
#    self-contained for the sub-PR #5 contract). ──────────────


_SOCCER_SPORT_ID = 1
_SPORT_MAP = {
    "Soccer": _SOCCER_SPORT_ID, "soccer": _SOCCER_SPORT_ID,
}


def _tid() -> uuid.UUID:
    return uuid.uuid4()


def _candidate_index(*team_names) -> CandidateIndex:
    """Build a CandidateIndex from (sport_code, canonical_name,
    team_id) tuples. Mirrors test_resolver_2d.py's _candidate_index
    helper."""
    ci = CandidateIndex()
    for sport_code, canonical_name, team_id in team_names:
        structured = structurally_normalize(canonical_name, sport_code=sport_code)
        if structured is None:
            continue
        ct = CandidateTeam(
            team_id=team_id,
            canonical_name=canonical_name,
            structured=structured,
        )
        sport_id = _SPORT_MAP[sport_code]
        ci._by_sport.setdefault(sport_id, []).append(ct)
        if structured.is_personal and structured.surname:
            if structured.detection_path == "personal_initial":
                interpretations = (structured.surname,)
            else:
                reconstructed = (
                    list(structured.other_tokens) + [structured.surname]
                )
                interpretations = candidate_surname_interpretations(reconstructed)
            for surname_key in interpretations:
                ci._by_sport_surname.setdefault(
                    (sport_id, surname_key), [],
                ).append(ct)
    return ci


def _signal(
    *,
    sport: str = "Soccer",
    home_raw: str = "Liverpool FC",
    away_raw: str = "Chelsea FC",
) -> FixtureSignal:
    return FixtureSignal(
        provider="kalshi",
        provider_record_id="rec-2f1-sub5-test",
        sport=sport,
        home_team_candidates=[TeamCandidate(
            raw=home_raw, normalized=home_raw.lower(),
            kind="name", weight=0.9,
        )],
        away_team_candidates=[TeamCandidate(
            raw=away_raw, normalized=away_raw.lower(),
            kind="name", weight=0.9,
        )],
        kickoff_at=datetime(2026, 6, 16, 14, tzinfo=timezone.utc),
        kickoff_confidence=1.0,
    )


def _session_with_no_corroboration() -> MagicMock:
    """Mock AsyncSession whose find_fixture-equivalent SELECT returns
    None (no corroboration). Anchor failure short-circuits before
    corroboration is consulted, so the mock's response doesn't matter
    for these tests — kept consistent with test_resolver_2d for
    pattern conformance."""
    session = MagicMock()

    async def execute(stmt, params=None):
        result = MagicMock()
        result.first.return_value = None
        result.scalars.return_value.all.return_value = []
        return result

    session.execute = execute
    return session


# ── Tests ──────────────────────────────────────────────────────


class TestFuzzyTierPreservesParsedNamesOnAnchorFailure:
    """The fuzzy tier must preserve the provider-supplied parsed
    names in reason_detail BEFORE returning fuzzy_no_team_resemblance,
    so the anchor_failed admin surface can suggest alias candidates
    or surface them in the operator-action handoff.

    Before sub-PR #5: these tests FAIL — reason_detail has only the
    anchor_failed flags + sport, no parsed names.
    After sub-PR #5: these tests PASS — parsed names preserved per the
    pattern at resolver/alias_tier/matcher.py:208-211."""

    def test_fuzzy_no_team_resemblance_preserves_home_provider_normalized(self):
        """When home_match.anchor_failed is True, the reason_detail
        on the returned MatchResult must contain
        `home_provider_normalized` set to the raw provider home
        string (StructuredName.raw)."""
        result = _run_fuzzy_against_anchor_failure_fixture()
        assert result.reason_code.value == "no_match"
        assert result.reason_detail.get("fail_reason") == "fuzzy_no_team_resemblance"
        assert "home_provider_normalized" in result.reason_detail, (
            "Sub-PR #5 contract: fuzzy_no_team_resemblance must "
            "preserve home_provider_normalized (match alias tier "
            "pattern at alias_tier/matcher.py:208-211)."
        )
        assert result.reason_detail["home_provider_normalized"], (
            "home_provider_normalized must be non-empty when set "
            "(empty string defeats downstream suggest-alias lookup)."
        )

    def test_fuzzy_no_team_resemblance_preserves_away_provider_normalized(self):
        result = _run_fuzzy_against_anchor_failure_fixture()
        assert "away_provider_normalized" in result.reason_detail, (
            "Sub-PR #5 contract: fuzzy_no_team_resemblance must "
            "preserve away_provider_normalized."
        )
        assert result.reason_detail["away_provider_normalized"]

    def test_fuzzy_no_team_resemblance_preserves_home_canonical(self):
        """Preserve home_canonical (the matcher's best-effort guess at
        the canonical name, may be empty when no candidates exist).
        The KEY presence is the contract — empty-string value is OK
        because the downstream consumer falls back to
        _provider_normalized when canonical is empty."""
        result = _run_fuzzy_against_anchor_failure_fixture()
        assert "home_canonical" in result.reason_detail, (
            "Sub-PR #5 contract: home_canonical key must be present "
            "on anchor-failure return (value may be empty string when "
            "no candidates existed pre-anchor)."
        )

    def test_fuzzy_no_team_resemblance_preserves_away_canonical(self):
        result = _run_fuzzy_against_anchor_failure_fixture()
        assert "away_canonical" in result.reason_detail, (
            "Sub-PR #5 contract: away_canonical key must be present "
            "on anchor-failure return."
        )

    def test_alias_tier_pattern_remains_intact(self):
        """Sanity check: sub-PR #5 must not regress the alias tier's
        already-correct preservation of home_provider_normalized /
        away_provider_normalized at alias_tier/matcher.py:208-211."""
        result = _run_alias_against_anchor_failure_fixture()
        assert result.reason_code.value == "no_match"
        assert result.reason_detail.get("fail_reason") == "alias_no_team_resemblance"
        assert "home_provider_normalized" in result.reason_detail
        assert "away_provider_normalized" in result.reason_detail


# ── Fixtures (real matcher runs, no DB) ────────────────────────


def _run_fuzzy_against_anchor_failure_fixture():
    """Construct a FuzzyTierMatcher with a CandidateIndex that doesn't
    contain the provider's home/away strings → both sides anchor-fail
    → returns fuzzy_no_team_resemblance MatchResult.

    Same shape as test_resolver_2d.py's
    test_cross_team_near_miss_rejected_at_85_threshold pattern
    (line ~283): provider sends a team not in the candidate index,
    fuzzy.ratio of "Liverpool FC" against "Manchester United" / "PSG"
    is well below the 0.85 anchor threshold, anchor_failed=True
    both sides.
    """
    candidates = _candidate_index(
        ("soccer", "Manchester United", _tid()),
        ("soccer", "Manchester City",   _tid()),
        ("soccer", "PSG",               _tid()),
    )
    m = FuzzyTierMatcher(
        candidates=candidates,
        sport_id_by_code_or_name=_SPORT_MAP,
    )
    sig = _signal(home_raw="Liverpool FC", away_raw="Chelsea FC")
    return asyncio.run(m.match(_session_with_no_corroboration(), sig))


def _run_alias_against_anchor_failure_fixture():
    """Same shape, alias tier. Provider sends a team not in the alias-
    tier candidate index, no candidate clears the anchor threshold,
    returns alias_no_team_resemblance MatchResult.

    Mirrors test_resolver_2c.py's
    TestAnchorFailure.test_no_candidate_above_threshold_routes_no_match.
    """
    candidates = _candidate_index(
        ("soccer", "Real Madrid",     _tid()),
        ("soccer", "Atletico Madrid", _tid()),
    )
    m = AliasTierMatcher(
        candidates=candidates,
        sport_id_by_code_or_name=_SPORT_MAP,
    )
    sig = _signal(home_raw="Random Unknown FC", away_raw="Some Other Team")
    return asyncio.run(m.match(_session_with_no_corroboration(), sig))
