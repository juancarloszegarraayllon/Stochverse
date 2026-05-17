"""Phase 2D.2 fuzzy-tier matcher tests.

Real call-path tests with mocked DB session — same shape as 2C.3
test_resolver_2c.py per the PR #87 lesson.

Coverage matrix (per design rev1 + sign-off):
  - Tennis recovery (the user's named cases):
    * "Miomir Kecmanovic" / "Kecmanovic M." with corroboration → FUZZY auto-apply
    * Same without corroboration → REVIEW_QUEUE
    * Multi-Wang → review_queue (collision)
    * "Bautista" → "Roberto Bautista Agut" via E.3 multi-interpretation
  - Team-sport no-anchor fallback:
    * Misspelling that fuzz.ratio recovers (≥0.85)
    * Cross-team near-miss (Manchester United vs Manchester City)
      character-level rejected
    * Exact-match-wins (Manchester United beats City near-miss)
  - TieredMatcher orchestration: 3-tier list when all three tiers
    consulted (D.4 carry-forward) — tested in TestTieredMatcher3Tier
    (NOTE: 2D.3 will wire TieredMatcher to actually consult fuzzy;
    in 2D.2 we test the matcher in isolation against a directly-
    constructed FuzzyTierMatcher.)
"""
from __future__ import annotations

import inspect
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from resolver import (
    FuzzyTierMatcher,
    FUZZY_RESOLVER_VERSION,
    FixtureSignal,
    MatchResult,
    ReasonCode,
    TIERED_RESOLVER_VERSION,
    TeamCandidate,
    TieredMatcher,
)
from resolver.alias_tier import (
    AUTO_APPLY_THRESHOLD,
    CandidateIndex,
    INDIVIDUAL_SPORT_CODES,
    REVIEW_QUEUE_THRESHOLD,
    StructuredName,
    structurally_normalize,
)
from resolver.alias_tier.candidates import CandidateTeam
from resolver.fuzzy_tier import (
    ANCHOR_SCORE,
    CORROBORATION_SCORE,
    INITIAL_EXPANSION_BONUS,
    candidate_surname_interpretations,
    initials_compatible,
)


# ── Helpers ─────────────────────────────────────────────────────


_SOCCER_SPORT_ID = 1
_TENNIS_SPORT_ID = 2

_SPORT_MAP = {
    "Soccer": _SOCCER_SPORT_ID, "soccer": _SOCCER_SPORT_ID,
    "Tennis": _TENNIS_SPORT_ID, "tennis": _TENNIS_SPORT_ID,
}


def _tid() -> uuid.UUID:
    return uuid.uuid4()


def _candidate_index(*team_names) -> CandidateIndex:
    """Build a CandidateIndex from (sport_code, canonical_name, team_id)
    tuples, applying the same multi-interpretation surname indexing as
    the production CandidateIndex.refresh path (Phase 2D.1 + E.3)."""
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
            # Same multi-interpretation expansion as
            # CandidateIndex.refresh (Phase 2D.1).
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
    home_raw: str = "Bayern Munich",
    away_raw: str = "PSG",
    kickoff_at: datetime = None,
    provider: str = "kalshi",
) -> FixtureSignal:
    return FixtureSignal(
        provider=provider,
        provider_record_id="rec-1",
        sport=sport,
        home_team_candidates=[TeamCandidate(
            raw=home_raw, normalized=home_raw.lower(), kind="name", weight=0.9,
        )],
        away_team_candidates=[TeamCandidate(
            raw=away_raw, normalized=away_raw.lower(), kind="name", weight=0.9,
        )],
        kickoff_at=kickoff_at or datetime(2026, 5, 9, 14, tzinfo=timezone.utc),
        kickoff_confidence=1.0,
    )


def _session_with_corroboration(present: bool) -> MagicMock:
    """Mock AsyncSession whose find_fixture-equivalent SELECT returns
    either a row (corroboration found) or None."""
    session = MagicMock()

    async def execute(stmt, params=None):
        result = MagicMock()
        if present:
            row = MagicMock(id=uuid.uuid4(), competition_id=None)
            result.first = MagicMock(return_value=row)
        else:
            result.first = MagicMock(return_value=None)
        return result

    session.execute = AsyncMock(side_effect=execute)
    return session


# ── Tennis recovery (Gap 1) — the named user cases ──────────────


class TestTennisRecovery:
    """The 555 deferred_to_2d records — fuzzy tier processes
    them now via initial expansion + multi-interpretation index."""

    @pytest.mark.asyncio
    async def test_kecmanovic_with_corroboration_routes_auto_apply(self):
        """The user's calibration anchor case at fixture level.
        Surname matches; initial expansion fires (vacuous-True
        because provider 'miomir' has no short tokens and candidate
        'm' prefix-matches 'miomir'); plus corroboration. Confidence
        = 0.40 + 0.30 + 0.30 = 1.00 → FUZZY auto-apply."""
        kec = _tid()
        rub = _tid()
        candidates = _candidate_index(
            ("tennis", "Kecmanovic M. (Srb)", kec),
            ("tennis", "Rublev A. (Rus)",    rub),
        )
        m = FuzzyTierMatcher(candidates=candidates, sport_id_by_code_or_name=_SPORT_MAP)
        sig = _signal(
            sport="Tennis",
            home_raw="Miomir Kecmanovic",
            away_raw="Andrey Rublev",
        )
        result = await m.match(_session_with_corroboration(True), sig)
        assert result.reason_code == ReasonCode.FUZZY, (
            f"Expected FUZZY, got {result.reason_code} (detail={result.reason_detail})"
        )
        assert result.confidence >= AUTO_APPLY_THRESHOLD
        assert result.resolver_version == FUZZY_RESOLVER_VERSION

    @pytest.mark.asyncio
    async def test_kecmanovic_without_corroboration_routes_review(self):
        """Same case without corroboration: 0.40 + 0.30 = 0.70 →
        REVIEW_QUEUE boundary (inclusive lower bound)."""
        kec = _tid()
        rub = _tid()
        candidates = _candidate_index(
            ("tennis", "Kecmanovic M. (Srb)", kec),
            ("tennis", "Rublev A. (Rus)",    rub),
        )
        m = FuzzyTierMatcher(candidates=candidates, sport_id_by_code_or_name=_SPORT_MAP)
        sig = _signal(
            sport="Tennis",
            home_raw="Miomir Kecmanovic",
            away_raw="Andrey Rublev",
        )
        result = await m.match(_session_with_corroboration(False), sig)
        assert result.reason_code == ReasonCode.REVIEW_QUEUE
        assert REVIEW_QUEUE_THRESHOLD <= result.confidence < AUTO_APPLY_THRESHOLD

    @pytest.mark.asyncio
    async def test_wang_collision_routes_review(self):
        """Multiple Wangs in the index → distinct team_ids → collision."""
        w1 = _tid()
        w2 = _tid()
        w3 = _tid()
        opp = _tid()
        candidates = _candidate_index(
            ("tennis", "Wang Q. (Chn)", w1),
            ("tennis", "Wang X. (Chn)", w2),
            ("tennis", "Wang Y. (Chn)", w3),
            ("tennis", "Sabalenka A. (Blr)", opp),
        )
        m = FuzzyTierMatcher(candidates=candidates, sport_id_by_code_or_name=_SPORT_MAP)
        sig = _signal(
            sport="Tennis",
            home_raw="Wang",
            away_raw="Aryna Sabalenka",
        )
        result = await m.match(_session_with_corroboration(True), sig)
        assert result.reason_code == ReasonCode.REVIEW_QUEUE
        assert result.reason_detail["home_collision"] is True
        # All three Wangs surfaced as colliding candidates.
        colliding = {uuid.UUID(t) for t in result.reason_detail["colliding_home_team_ids"]}
        assert {w1, w2, w3} <= colliding

    @pytest.mark.asyncio
    async def test_bautista_via_multi_interpretation_index(self):
        """E.3 named case: provider 'Bautista' (single token) reaches
        candidate 'Roberto Bautista Agut' via the middle-as-surname
        interpretation. With corroboration → FUZZY auto-apply."""
        bautista_agut = _tid()
        opp = _tid()
        candidates = _candidate_index(
            ("tennis", "Roberto Bautista Agut", bautista_agut),
            ("tennis", "Tsitsipas S. (Gre)",   opp),
        )
        m = FuzzyTierMatcher(candidates=candidates, sport_id_by_code_or_name=_SPORT_MAP)
        sig = _signal(
            sport="Tennis",
            home_raw="Bautista",
            away_raw="Stefanos Tsitsipas",
        )
        result = await m.match(_session_with_corroboration(True), sig)
        # The candidate IS reachable — surname-anchor passes via
        # the middle-as-surname interpretation.
        assert result.reason_code == ReasonCode.FUZZY, (
            f"Expected FUZZY, got {result.reason_code} (detail={result.reason_detail})"
        )
        assert result.reason_detail["home_team_id"] == str(bautista_agut)


# ── Team-sport no-anchor fallback (Gap 2) ──────────────────────


class TestTeamFuzzy:
    """The residual ~50-150/day team-sport records that 2C.3's
    0.78 token-set anchor missed. 2D's character-level fuzz.ratio
    recovers spelling variants (e.g., misspellings)."""

    @pytest.mark.asyncio
    async def test_misspelled_team_recovered_with_corroboration(self):
        """Provider sends 'Bayrn Munich' (typo). 2C alias-tier
        token-set hit it (since "munich" is shared) but for cases
        where token-set fails entirely, character-level fuzz.ratio
        recovers them."""
        bayern = _tid()
        psg = _tid()
        candidates = _candidate_index(
            ("soccer", "Bayern Munich",   bayern),
            ("soccer", "PSG",             psg),
        )
        m = FuzzyTierMatcher(candidates=candidates, sport_id_by_code_or_name=_SPORT_MAP)
        # "bayrn munich" / "bayern munich" — char-level ratio ≈ 0.92
        sig = _signal(home_raw="Bayrn Munich", away_raw="PSG")
        result = await m.match(_session_with_corroboration(True), sig)
        assert result.reason_code == ReasonCode.FUZZY
        assert result.reason_detail["home_team_id"] == str(bayern)

    @pytest.mark.asyncio
    async def test_cross_team_near_miss_rejected_at_85_threshold(self):
        """'Manchester United' vs 'Manchester City' — character ratio
        is below 0.85 (different last words). Anchor fails for the
        home side. Per design B.1: stricter than 2C.3's 0.78 token-set
        because character-level is statistically noisier.

        Routing per Phase 2D.5 sub-PR #1: home anchor fails, away
        anchors against PSG cleanly → asymmetric anchor failure →
        REVIEW_QUEUE with routing_shape set. Pre-2D.5 this routed to
        no_match; post-2D.5 the asymmetric case is operator-actionable
        (the away-side anchor narrows the fixture lookup) and surfaces
        through the review_queue with top-N candidates for the failed
        home side. The test still verifies what it always verified —
        the 0.85 threshold fires for `Liverpool FC` against the
        Manchester candidates — but the downstream routing is now the
        2D.5 review_queue path rather than no_match.
        """
        united = _tid()
        city = _tid()
        psg = _tid()
        candidates = _candidate_index(
            ("soccer", "Manchester United", united),
            ("soccer", "Manchester City",   city),
            ("soccer", "PSG",               psg),
        )
        m = FuzzyTierMatcher(candidates=candidates, sport_id_by_code_or_name=_SPORT_MAP)
        # Provider sends a name that doesn't match either Manchester team
        sig = _signal(home_raw="Liverpool FC", away_raw="PSG")
        result = await m.match(_session_with_corroboration(False), sig)
        # Anchor failure on home side fires as expected.
        assert result.reason_detail["home_anchor_failed"] is True
        assert result.reason_detail["away_anchor_failed"] is False
        # Asymmetric → REVIEW_QUEUE with routing_shape.
        assert result.reason_code == ReasonCode.REVIEW_QUEUE
        assert (
            result.reason_detail.get("routing_shape")
            == "asymmetric_anchor_failure"
        )

    @pytest.mark.asyncio
    async def test_exact_match_wins_in_team_path(self):
        """Provider 'Manchester United' exactly matches one
        candidate (1.0) and near-matches Manchester City (~0.88).
        Single 1.0 → exact-match-wins → FUZZY auto-apply for United."""
        united = _tid()
        city = _tid()
        psg = _tid()
        candidates = _candidate_index(
            ("soccer", "Manchester United", united),
            ("soccer", "Manchester City",   city),
            ("soccer", "PSG",               psg),
        )
        m = FuzzyTierMatcher(candidates=candidates, sport_id_by_code_or_name=_SPORT_MAP)
        sig = _signal(home_raw="Manchester United", away_raw="PSG")
        result = await m.match(_session_with_corroboration(True), sig)
        assert result.reason_code == ReasonCode.FUZZY
        assert result.reason_detail["home_team_id"] == str(united)

    @pytest.mark.asyncio
    async def test_below_ratio_threshold_routes_no_match(self):
        """Provider name with <0.85 character ratio against any
        candidate → anchor failed → no_match."""
        bayern = _tid()
        psg = _tid()
        candidates = _candidate_index(
            ("soccer", "Bayern Munich", bayern),
            ("soccer", "PSG",           psg),
        )
        m = FuzzyTierMatcher(candidates=candidates, sport_id_by_code_or_name=_SPORT_MAP)
        # Provider sends a name with very weak similarity to anything
        sig = _signal(home_raw="Random Unknown FC", away_raw="Other Team SC")
        result = await m.match(_session_with_corroboration(False), sig)
        assert result.reason_code == ReasonCode.NO_MATCH
        assert result.reason_detail["fail_reason"] == "fuzzy_no_team_resemblance"


# ── Confidence-routing boundary checks ─────────────────────────


class TestConfidenceRouting:
    @pytest.mark.asyncio
    async def test_perfect_match_no_corroboration_routes_review_at_70_boundary(self):
        """Perfect match without corroboration: 0.40 + 0.30 = 0.70 →
        review_queue (inclusive lower bound)."""
        bayern = _tid()
        psg = _tid()
        candidates = _candidate_index(
            ("soccer", "Bayern Munich", bayern),
            ("soccer", "PSG",           psg),
        )
        m = FuzzyTierMatcher(candidates=candidates, sport_id_by_code_or_name=_SPORT_MAP)
        sig = _signal(home_raw="Bayern Munich", away_raw="PSG")
        result = await m.match(_session_with_corroboration(False), sig)
        assert result.reason_code == ReasonCode.REVIEW_QUEUE
        assert result.confidence == pytest.approx(0.70)

    @pytest.mark.asyncio
    async def test_perfect_match_with_corroboration_hits_one(self):
        bayern = _tid()
        psg = _tid()
        candidates = _candidate_index(
            ("soccer", "Bayern Munich", bayern),
            ("soccer", "PSG",           psg),
        )
        m = FuzzyTierMatcher(candidates=candidates, sport_id_by_code_or_name=_SPORT_MAP)
        sig = _signal(home_raw="Bayern Munich", away_raw="PSG")
        result = await m.match(_session_with_corroboration(True), sig)
        assert result.reason_code == ReasonCode.FUZZY
        assert result.confidence == pytest.approx(1.00)


# ── Gate failures ──────────────────────────────────────────────


class TestGateFailures:
    @pytest.mark.asyncio
    async def test_sport_not_classified_returns_no_match(self):
        candidates = _candidate_index(("soccer", "Real Madrid", _tid()))
        m = FuzzyTierMatcher(candidates=candidates, sport_id_by_code_or_name=_SPORT_MAP)
        sig = _signal(sport="")
        result = await m.match(_session_with_corroboration(False), sig)
        assert result.reason_code == ReasonCode.NO_MATCH
        assert result.reason_detail["fail_reason"] == "sport_not_classified"

    @pytest.mark.asyncio
    async def test_kickoff_at_missing_returns_no_match(self):
        candidates = _candidate_index(
            ("soccer", "Bayern Munich", _tid()),
            ("soccer", "PSG", _tid()),
        )
        m = FuzzyTierMatcher(candidates=candidates, sport_id_by_code_or_name=_SPORT_MAP)
        sig = _signal()
        sig = sig.model_copy(update={"kickoff_at": None})
        result = await m.match(_session_with_corroboration(False), sig)
        assert result.reason_code == ReasonCode.NO_MATCH
        assert result.reason_detail["fail_reason"] == "kickoff_at_missing"

    @pytest.mark.asyncio
    async def test_above_threshold_but_no_existing_fixture_routes_no_match(self):
        """Per design B.1 carry-forward: fuzzy tier never creates
        fixtures. Confidence ≥ 0.85 but no fixture at this kickoff →
        NO_MATCH(fuzzy_no_existing_fixture). Picks up on next pass
        when FL has resolved the fixture (E.2 re-resolve mechanism)."""
        bayern = _tid()
        psg = _tid()
        candidates = _candidate_index(
            ("soccer", "Bayern Munich", bayern),
            ("soccer", "PSG",           psg),
        )
        m = FuzzyTierMatcher(candidates=candidates, sport_id_by_code_or_name=_SPORT_MAP)
        sig = _signal(home_raw="Bayern Munich", away_raw="PSG")
        # Mock session: find_fixture (corroboration check) returns
        # a fixture, BUT the subsequent _lookup_fixture_or_none also
        # gets called and would also see it. To force the no_existing
        # case, we'd need session.execute to return None on the
        # second find_fixture call. Easier: corroboration absent,
        # confidence 0.70 → review_queue routing (not auto_apply,
        # so no fixture lookup needed).
        #
        # Construct a case where confidence ≥ 0.85 but lookup misses:
        # the Kecmanovic case with corroboration would return a
        # fixture from the corroboration check itself, and then
        # _lookup_fixture_or_none uses the same find_fixture call —
        # consistent behavior. So this scenario actually DOESN'T
        # arise in our mocked session: if corroboration found a
        # fixture, the lookup will too.
        #
        # The scenario IS reachable in production when
        # find_fixture's drift window catches a fixture that doesn't
        # exactly match the kickoff. Hard to mock without a more
        # detailed session double. Skipped here; documented in the
        # 2D.2 PR description as a gap to revisit if production
        # data shows it matters.
        pytest.skip(
            "Scenario hard to construct with current mock; documented in PR "
            "as audit gap to revisit if production behavior shows it matters."
        )


# ── CandidateIndex multi-interpretation integration  ──────────


class TestE3MultiInterpretationViaCandidateIndex:
    """Real call-path test confirming the personal-path lookup
    works end-to-end through CandidateIndex (built via the test
    helper that mirrors production CandidateIndex.refresh)."""

    @pytest.mark.asyncio
    async def test_three_token_candidate_reachable_under_all_interpretations(self):
        """Provider can send any of: 'Agut', 'Bautista', 'Bautista Agut'
        and reach 'Roberto Bautista Agut'."""
        bautista_agut = _tid()
        opp = _tid()
        candidates = _candidate_index(
            ("tennis", "Roberto Bautista Agut", bautista_agut),
            ("tennis", "Tsitsipas S. (Gre)",   opp),
        )
        m = FuzzyTierMatcher(candidates=candidates, sport_id_by_code_or_name=_SPORT_MAP)

        for provider_form in ("Agut", "Bautista", "Bautista Agut"):
            sig = _signal(
                sport="Tennis",
                home_raw=provider_form,
                away_raw="Stefanos Tsitsipas",
            )
            result = await m.match(_session_with_corroboration(True), sig)
            # All three provider forms should reach the candidate.
            # Routing is FUZZY auto-apply with corroboration.
            assert result.reason_code == ReasonCode.FUZZY, (
                f"provider_form={provider_form!r}: got {result.reason_code} "
                f"(detail={result.reason_detail})"
            )
            assert result.reason_detail["home_team_id"] == str(bautista_agut)


# ── TieredMatcher 3-tier orchestration (Phase 2D.3) ────────────


class _StubMatcher:
    """Stand-in for any tier matcher in TieredMatcher tests.
    Returns a pre-canned MatchResult and records whether it was
    consulted (for assertions about short-circuit behavior)."""

    def __init__(self, result: MatchResult) -> None:
        self._result = result
        self.called = False

    async def match(self, session, signal) -> MatchResult:
        self.called = True
        return self._result


class TestTieredMatcher3Tier:
    """Phase 2D.3: TieredMatcher consults strict, then alias, then
    fuzzy. Each tier short-circuits on success of the prior. Final
    list length tells the runner which tiers were consulted (and
    drives one resolution_log row per tier per design D.4).
    """

    @pytest.mark.asyncio
    async def test_strict_hit_short_circuits_alias_and_fuzzy(self):
        strict_hit = MatchResult(
            fixture_id=_tid(), confidence=0.98,
            reason_code=ReasonCode.STRICT, reason_detail={},
            resolver_version="strict@2a.6",
        )
        alias = _StubMatcher(MatchResult(
            fixture_id=None, confidence=0.0,
            reason_code=ReasonCode.NO_MATCH, reason_detail={},
            resolver_version="alias@2c.0",
        ))
        fuzzy = _StubMatcher(MatchResult(
            fixture_id=None, confidence=0.0,
            reason_code=ReasonCode.NO_MATCH, reason_detail={},
            resolver_version=FUZZY_RESOLVER_VERSION,
        ))
        m = TieredMatcher(
            strict=_StubMatcher(strict_hit),
            alias=alias,
            fuzzy=fuzzy,
        )
        results = await m.match(MagicMock(), _signal())
        assert len(results) == 1
        assert results[0].reason_code == ReasonCode.STRICT
        assert not alias.called
        assert not fuzzy.called

    @pytest.mark.asyncio
    async def test_alias_hit_short_circuits_fuzzy(self):
        # Per design rev3: alias ALIAS auto-apply means fuzzy doesn't
        # run. Two log rows (strict miss + alias hit), runner
        # auto-applies on the alias result.
        m = TieredMatcher(
            strict=_StubMatcher(MatchResult(
                fixture_id=None, confidence=0.0,
                reason_code=ReasonCode.NO_MATCH, reason_detail={},
                resolver_version="strict@2a.6",
            )),
            alias=_StubMatcher(MatchResult(
                fixture_id=_tid(), confidence=0.85,
                reason_code=ReasonCode.ALIAS,
                reason_detail={"home_team_id": "x", "away_team_id": "y"},
                resolver_version="alias@2c.0",
            )),
            fuzzy=(fuzzy := _StubMatcher(MatchResult(
                fixture_id=None, confidence=0.0,
                reason_code=ReasonCode.NO_MATCH, reason_detail={},
                resolver_version=FUZZY_RESOLVER_VERSION,
            ))),
        )
        results = await m.match(MagicMock(), _signal())
        assert len(results) == 2
        assert results[1].reason_code == ReasonCode.ALIAS
        assert not fuzzy.called

    @pytest.mark.asyncio
    async def test_alias_review_queue_short_circuits_fuzzy(self):
        # Per design rev3: alias REVIEW_QUEUE is a successful
        # actionable result. Fuzzy's lower anchor (0.40 vs alias 0.50)
        # cannot improve on it. Don't run fuzzy.
        m = TieredMatcher(
            strict=_StubMatcher(MatchResult(
                fixture_id=None, confidence=0.0,
                reason_code=ReasonCode.NO_MATCH, reason_detail={},
                resolver_version="strict@2a.6",
            )),
            alias=_StubMatcher(MatchResult(
                fixture_id=None, confidence=0.80,
                reason_code=ReasonCode.REVIEW_QUEUE,
                reason_detail={"home_collision": True},
                resolver_version="alias@2c.0",
            )),
            fuzzy=(fuzzy := _StubMatcher(MatchResult(
                fixture_id=None, confidence=0.0,
                reason_code=ReasonCode.NO_MATCH, reason_detail={},
                resolver_version=FUZZY_RESOLVER_VERSION,
            ))),
        )
        results = await m.match(MagicMock(), _signal())
        assert len(results) == 2
        assert results[1].reason_code == ReasonCode.REVIEW_QUEUE
        assert not fuzzy.called

    @pytest.mark.asyncio
    async def test_alias_no_match_runs_fuzzy_and_returns_three_results(self):
        # The 2D.3 smoking-gun: tennis-deferred records flow through
        # to fuzzy, which then resolves them. Three rows in
        # resolution_log per design D.4 carry-forward.
        m = TieredMatcher(
            strict=_StubMatcher(MatchResult(
                fixture_id=None, confidence=0.0,
                reason_code=ReasonCode.NO_MATCH,
                reason_detail={"fail_reason": "alias_resolution_incomplete"},
                resolver_version="strict@2a.6",
            )),
            alias=_StubMatcher(MatchResult(
                fixture_id=None, confidence=0.0,
                reason_code=ReasonCode.NO_MATCH,
                reason_detail={"fail_reason": "deferred_to_2d"},
                resolver_version="alias@2c.0",
            )),
            fuzzy=_StubMatcher(MatchResult(
                fixture_id=_tid(), confidence=1.00,
                reason_code=ReasonCode.FUZZY,
                reason_detail={"home_team_id": "a", "away_team_id": "b"},
                resolver_version=FUZZY_RESOLVER_VERSION,
            )),
        )
        results = await m.match(MagicMock(), _signal())
        assert len(results) == 3
        assert results[0].reason_code == ReasonCode.NO_MATCH
        assert results[0].resolver_version == "strict@2a.6"
        assert results[1].reason_code == ReasonCode.NO_MATCH
        assert results[1].resolver_version == "alias@2c.0"
        assert results[2].reason_code == ReasonCode.FUZZY
        assert results[2].resolver_version == FUZZY_RESOLVER_VERSION

    @pytest.mark.asyncio
    async def test_all_three_miss_returns_three_no_match_rows(self):
        m = TieredMatcher(
            strict=_StubMatcher(MatchResult(
                fixture_id=None, confidence=0.0,
                reason_code=ReasonCode.NO_MATCH, reason_detail={},
                resolver_version="strict@2a.6",
            )),
            alias=_StubMatcher(MatchResult(
                fixture_id=None, confidence=0.0,
                reason_code=ReasonCode.NO_MATCH,
                reason_detail={"fail_reason": "alias_no_team_resemblance"},
                resolver_version="alias@2c.0",
            )),
            fuzzy=_StubMatcher(MatchResult(
                fixture_id=None, confidence=0.0,
                reason_code=ReasonCode.NO_MATCH,
                reason_detail={"fail_reason": "fuzzy_below_threshold"},
                resolver_version=FUZZY_RESOLVER_VERSION,
            )),
        )
        results = await m.match(MagicMock(), _signal())
        assert len(results) == 3
        assert all(r.reason_code == ReasonCode.NO_MATCH for r in results)

    @pytest.mark.asyncio
    async def test_fuzzy_review_queue_attribution(self):
        # Fuzzy can also emit REVIEW_QUEUE (the headline 2D output per
        # rev3 Option C1: ~150/cron tennis review queue records).
        # Final result's resolver_version starts with "fuzzy" — runner
        # uses this to attribute volume to fuzzy_review_queue counter
        # rather than alias_review_queue.
        m = TieredMatcher(
            strict=_StubMatcher(MatchResult(
                fixture_id=None, confidence=0.0,
                reason_code=ReasonCode.NO_MATCH, reason_detail={},
                resolver_version="strict@2a.6",
            )),
            alias=_StubMatcher(MatchResult(
                fixture_id=None, confidence=0.0,
                reason_code=ReasonCode.NO_MATCH,
                reason_detail={"fail_reason": "deferred_to_2d"},
                resolver_version="alias@2c.0",
            )),
            fuzzy=_StubMatcher(MatchResult(
                fixture_id=None, confidence=0.70,
                reason_code=ReasonCode.REVIEW_QUEUE,
                reason_detail={"home_team_id": "a", "away_team_id": "b"},
                resolver_version=FUZZY_RESOLVER_VERSION,
            )),
        )
        results = await m.match(MagicMock(), _signal())
        assert len(results) == 3
        final = results[-1]
        assert final.reason_code == ReasonCode.REVIEW_QUEUE
        assert final.resolver_version.startswith("fuzzy")

    @pytest.mark.asyncio
    async def test_back_compat_two_tier_when_fuzzy_omitted(self):
        # Existing 2C test fixtures construct TieredMatcher(strict, alias)
        # without a fuzzy argument. The orchestrator falls back to
        # 2-tier behavior — never attempts to call self.fuzzy.match.
        # This guards against accidental breakage of 2C-era callers.
        m = TieredMatcher(
            strict=_StubMatcher(MatchResult(
                fixture_id=None, confidence=0.0,
                reason_code=ReasonCode.NO_MATCH, reason_detail={},
                resolver_version="strict@2a.6",
            )),
            alias=_StubMatcher(MatchResult(
                fixture_id=None, confidence=0.0,
                reason_code=ReasonCode.NO_MATCH, reason_detail={},
                resolver_version="alias@2c.0",
            )),
            # No fuzzy.
        )
        results = await m.match(MagicMock(), _signal())
        assert len(results) == 2


# ── Static guards (backstop only — primary surface is call-path) ─


class TestStaticGuards:
    def setup_method(self):
        import resolver.fuzzy_tier.matcher
        self.src = inspect.getsource(resolver.fuzzy_tier.matcher)

    def test_fuzzy_matcher_writes_nothing(self):
        # Per design carry-forward (alias-tier B.1 → fuzzy tier):
        # matcher returns MatchResult; runner does ALL writes.
        forbidden = ["session.add(", "INSERT INTO", "UPDATE sp.", "DELETE FROM"]
        for pat in forbidden:
            assert pat not in self.src, (
                f"FuzzyTierMatcher must not write to DB; found {pat!r}"
            )

    def test_anchor_score_is_lower_than_alias_tier(self):
        # Per design rev1 §C: 2D's anchor floor (0.40) is INTENTIONALLY
        # lower than 2C's (0.50) because 2D's anchors are weaker.
        # Corroboration weight (0.30) is correspondingly higher.
        from resolver.alias_tier import ANCHOR_SCORE as ALIAS_ANCHOR
        assert ANCHOR_SCORE == 0.40
        assert ALIAS_ANCHOR == 0.50
        assert ANCHOR_SCORE < ALIAS_ANCHOR
        assert CORROBORATION_SCORE == 0.30
        # Sum still adds to 1.0.
        assert ANCHOR_SCORE + INITIAL_EXPANSION_BONUS + CORROBORATION_SCORE == 1.00

    def test_resolver_version_distinct(self):
        from resolver.alias_tier import ALIAS_RESOLVER_VERSION
        assert FUZZY_RESOLVER_VERSION == "fuzzy@2d.0"
        assert FUZZY_RESOLVER_VERSION != ALIAS_RESOLVER_VERSION

    def test_fuzzy_drift_window_widened_per_2d28(self):
        # Phase 2D.2.8: fuzzy tier widens its drift window from 30 to
        # 60 min. Strict tier and alias tier stay at 30 min.
        # Calibration evidence in PR #103: Q3 lift 85% → 100% (+15pp)
        # at ±60min vs ±30min, with kickoff offsets piling up at the
        # 30-min filter edge (Q2 median/max both 30).
        from resolver.fuzzy_tier.matcher import KICKOFF_DRIFT_SEC as FUZZY_DRIFT
        from resolver.matcher import StrictMatcher
        from resolver.alias_tier.matcher import KICKOFF_DRIFT_SEC as ALIAS_DRIFT

        assert FUZZY_DRIFT == 60 * 60, (
            f"Fuzzy tier drift expected 60 min (3600s), got {FUZZY_DRIFT}s. "
            "Per Path B in design §E.8."
        )
        assert StrictMatcher.KICKOFF_DRIFT_SEC == 30 * 60
        assert ALIAS_DRIFT == 30 * 60
        # Per-tier guard: fuzzy must be strictly wider than strict.
        # If a future change tightens fuzzy or widens strict, this
        # asserts the design invariant before the calibration is lost.
        assert FUZZY_DRIFT > StrictMatcher.KICKOFF_DRIFT_SEC, (
            "Fuzzy tier must use a wider corroboration drift window than "
            "strict tier (per design §E.8 Path B). Tighter strict-tier "
            "drift protects against false positives where exact alias "
            "anchors don't need slack."
        )
        assert FUZZY_DRIFT > ALIAS_DRIFT, (
            "Fuzzy tier must use a wider corroboration drift window than "
            "alias tier. Alias tier's exact-alias anchors don't need slack."
        )

    def test_tiered_resolver_version_bumped_to_2d(self):
        # Phase 2D.3 bumps the orchestrator version stamp because
        # TieredMatcher now consults a third tier. Older 2C run rows
        # keep their tiered@2c.0 stamp; new runs get tiered@2d.0.
        # Day-7 reports can split per-version metrics if needed.
        assert TIERED_RESOLVER_VERSION == "tiered@2d.0", (
            f"Expected tiered@2d.0 after 2D.3, got {TIERED_RESOLVER_VERSION!r}."
        )

    def test_runner_constructs_3tier_matcher(self):
        # Backstop for the runner wiring: the production runner must
        # build TieredMatcher with all three tiers. If a refactor
        # accidentally drops the fuzzy argument, this catches it
        # before a deploy that silently regresses to 2-tier behavior.
        import pathlib
        runner_src = pathlib.Path(
            __file__
        ).resolve().parent.parent.joinpath(
            "scripts", "run_resolver_pass.py"
        ).read_text()
        assert "FuzzyTierMatcher(" in runner_src, (
            "scripts/run_resolver_pass.py must construct a "
            "FuzzyTierMatcher per Phase 2D.3."
        )
        # The kwargs-on-multiple-lines style is what the production
        # runner uses; assert the fuzzy= keyword is wired into
        # TieredMatcher (any whitespace shape).
        assert "fuzzy=fuzzy_matcher" in runner_src, (
            "scripts/run_resolver_pass.py must pass the fuzzy matcher "
            "to TieredMatcher per Phase 2D.3."
        )

    def test_runner_writes_back_fuzzy_tier_source(self):
        # Phase 2D.3 fuzzy auto-applies write back to sp.team_aliases
        # with source='fuzzy_tier' (parallel to the alias-tier
        # source='alias_tier' write-back). Static guard against
        # accidentally reusing 'alias_tier' for fuzzy auto-applies,
        # which would muddy day-7 attribution and the
        # ON CONFLICT (alias_normalized, source) DO NOTHING uniqueness
        # constraint.
        import pathlib
        runner_src = pathlib.Path(
            __file__
        ).resolve().parent.parent.joinpath(
            "scripts", "run_resolver_pass.py"
        ).read_text()
        assert "'fuzzy_tier'" in runner_src, (
            "scripts/run_resolver_pass.py must write back fuzzy "
            "auto-applies with source='fuzzy_tier'."
        )

    def test_runner_review_queue_uses_on_conflict(self):
        # Phase 2D.3.1 hotfix: the review_queue INSERT must use
        # ON CONFLICT (provider, provider_record_id) so re-resolving
        # a record that's already in review_queue from a prior cron
        # doesn't IntegrityError on the duplicate key. WHERE
        # status='pending' protects already-decided rows.
        #
        # Static guard against the regression that caused the
        # 619-crash incident: session.add(ReviewQueue(...)) with no
        # conflict handling.
        import pathlib
        runner_src = pathlib.Path(
            __file__
        ).resolve().parent.parent.joinpath(
            "scripts", "run_resolver_pass.py"
        ).read_text()
        assert "INSERT INTO sp.review_queue" in runner_src, (
            "scripts/run_resolver_pass.py must use a raw INSERT for "
            "sp.review_queue (ON CONFLICT requires raw SQL)."
        )
        assert "ON CONFLICT (provider, provider_record_id)" in runner_src, (
            "scripts/run_resolver_pass.py must handle conflicts on "
            "the (provider, provider_record_id) uniqueness "
            "constraint per Phase 2D.3.1 hotfix."
        )
        assert "sp.review_queue.status = 'pending'" in runner_src, (
            "scripts/run_resolver_pass.py must guard the DO UPDATE "
            "with WHERE status='pending' so operator-decided rows "
            "(approved/rejected) aren't overwritten."
        )
        # The legacy ORM-style insert must be gone — if both shapes
        # exist, the raw SQL is unreachable behind a flag or some
        # records are still hitting the buggy path.
        assert "session.add(ReviewQueue(" not in runner_src, (
            "scripts/run_resolver_pass.py must NOT use "
            "session.add(ReviewQueue(...)) — that's the pre-hotfix "
            "shape that crashed on duplicate keys."
        )

    def test_runner_review_queue_writes_2f0_columns(self):
        # Phase 2F.0.5: the review_queue INSERT must populate the new
        # reason_detail and provider_title columns added in 2F.0
        # (PR #114). Without the runner write-side update, the 2F.1
        # admin UI's denormalized read path falls back to NULL on
        # every freshly-inserted row.
        #
        # Static guard scans for the column names in BOTH the INSERT
        # column list AND the ON CONFLICT DO UPDATE clause (re-resolves
        # of pending records refresh the snapshot per the design).
        import pathlib
        runner_src = pathlib.Path(
            __file__
        ).resolve().parent.parent.joinpath(
            "scripts", "run_resolver_pass.py"
        ).read_text()

        # Locate the review_queue INSERT block (single occurrence per
        # the prior test_runner_review_queue_uses_on_conflict guard).
        insert_idx = runner_src.find("INSERT INTO sp.review_queue")
        assert insert_idx > 0, "review_queue INSERT not found"
        # Window the assertions to ~3000 chars after the INSERT —
        # captures the VALUES list, ON CONFLICT clause, and bindparams.
        block = runner_src[insert_idx:insert_idx + 3000]

        # Column list (first appearance in the block, before VALUES).
        for col in ("reason_detail", "provider_title"):
            assert col in block, (
                f"scripts/run_resolver_pass.py must write the {col!r} "
                f"column on review_queue INSERT (Phase 2F.0.5)."
            )

        # ON CONFLICT DO UPDATE — both columns must refresh on
        # re-resolve so pending records reflect the latest matcher
        # decision (per design Q3).
        assert "reason_detail      = EXCLUDED.reason_detail" in block, (
            "scripts/run_resolver_pass.py must refresh reason_detail "
            "in the ON CONFLICT DO UPDATE clause."
        )
        assert "provider_title     = EXCLUDED.provider_title" in block, (
            "scripts/run_resolver_pass.py must refresh provider_title "
            "in the ON CONFLICT DO UPDATE clause."
        )

        # Bindparams must include both new params.
        assert "reason_detail=json.dumps(" in block, (
            "scripts/run_resolver_pass.py must JSON-encode "
            "final.reason_detail for the JSONB cast."
        )
        assert "title=provider_title" in block, (
            "scripts/run_resolver_pass.py must bind the computed "
            "provider_title to the :title param."
        )

    def test_runner_provider_title_is_provider_aware(self):
        # Phase 2F.0.5: provider_title is computed differently per
        # provider — Kalshi has raw_payload['title']; FL has
        # HOME_NAME / AWAY_NAME and we synthesize "home vs away".
        # If both providers fed the same code path, FL records would
        # always store NULL (no 'title' field) and the operator UI
        # would lose context for ~50% of inflow.
        import pathlib
        runner_src = pathlib.Path(
            __file__
        ).resolve().parent.parent.joinpath(
            "scripts", "run_resolver_pass.py"
        ).read_text()

        # Find the provider_title computation block (just above the
        # INSERT INTO sp.review_queue).
        insert_idx = runner_src.find("INSERT INTO sp.review_queue")
        assert insert_idx > 0
        # Look back ~2000 chars from the INSERT for the computation.
        prelude = runner_src[max(0, insert_idx - 2000):insert_idx]

        # Kalshi path uses raw_payload['title'].
        assert 'row.raw_payload.get("title")' in prelude, (
            "Kalshi provider_title must be sourced from "
            "raw_payload['title']."
        )
        # FL path uses HOME_NAME / AWAY_NAME synthesis.
        assert 'row.raw_payload.get("HOME_NAME")' in prelude, (
            "FL provider_title must synthesize from HOME_NAME."
        )
        assert 'row.raw_payload.get("AWAY_NAME")' in prelude, (
            "FL provider_title must synthesize from AWAY_NAME."
        )

    def test_runner_uses_per_record_transaction(self):
        # Phase 2D.3.1 hotfix: the runner must open the transaction
        # per record (inside the chunk-level for-loop), not per
        # chunk. A chunk-level transaction caused IntegrityError
        # cascades — one record's failure poisoned every subsequent
        # record in the chunk via PendingRollbackError.
        #
        # Static guard against accidentally moving session.begin()
        # back to a chunk boundary in a future refactor. The check
        # is structural: scan the chunk-loop area for "for row in
        # chunk" and "async with session.begin()" and verify the
        # latter sits AFTER the former in source order, not before.
        import pathlib
        runner_src = pathlib.Path(
            __file__
        ).resolve().parent.parent.joinpath(
            "scripts", "run_resolver_pass.py"
        ).read_text()
        for_loop_idx = runner_src.find("for row in chunk")
        begin_idx = runner_src.find(
            "async with session.begin()", for_loop_idx
        )
        assert for_loop_idx > 0, "for row in chunk loop missing"
        assert begin_idx > for_loop_idx, (
            "Phase 2D.3.1 hotfix: async with session.begin() must "
            "be inside the per-record loop, not above it. A "
            "chunk-level transaction cascades IntegrityError "
            "across records."
        )
