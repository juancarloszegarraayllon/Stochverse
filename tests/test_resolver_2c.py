"""Phase 2C.3 alias-tier matcher tests.

Real call-path tests with mocked DB session. Per the PR #87 lesson,
static-source guards exist as backstop only — these tests exercise
AliasTierMatcher.match() and TieredMatcher.match() end-to-end against
hand-built CandidateIndex + mocked find_fixture lookups.

Coverage matrix (per the user's sign-off message):
  - "Real Sociedad" with multiple candidates above threshold → review_queue
  - "Bolton" with one candidate above threshold → auto_apply
  - "Manchester United" exact + "Manchester City" near → auto_apply
  - "Manchester" → "United" AND "City" → review_queue (collision)
  - "Tokyo" → multiple → review_queue
  - Tennis (and INDIVIDUAL_SPORT_CODES) early-exit → deferred_to_2d
  - Both tiers logged when alias rescues strict miss (D.4)
"""
from __future__ import annotations

import inspect
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from resolver import (
    AliasResolver,
    AliasTierMatcher,
    CandidateIndex,
    FixtureSignal,
    MatchResult,
    ReasonCode,
    StrictMatcher,
    TIERED_RESOLVER_VERSION,
    TeamCandidate,
    TieredMatcher,
)
from resolver.alias_tier import (
    ALIAS_RESOLVER_VERSION,
    AUTO_APPLY_THRESHOLD,
    INDIVIDUAL_SPORT_CODES,
    REVIEW_QUEUE_THRESHOLD,
    StructuredName,
    structurally_normalize,
)
from resolver.alias_tier.candidates import CandidateTeam


# ── Helpers ─────────────────────────────────────────────────────


_SOCCER_SPORT_ID = 1
_TENNIS_SPORT_ID = 2

_SPORT_MAP = {
    "Soccer": _SOCCER_SPORT_ID, "soccer": _SOCCER_SPORT_ID,
    "Tennis": _TENNIS_SPORT_ID, "tennis": _TENNIS_SPORT_ID,
}


def _tid() -> uuid.UUID:
    return uuid.uuid4()


def _candidate_index(*team_names: tuple[str, str, uuid.UUID]) -> CandidateIndex:
    """Build a CandidateIndex from (sport_code, canonical_name, team_id) tuples."""
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
            ci._by_sport_surname.setdefault(
                (sport_id, structured.surname), [],
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
    either a row (corroboration found) or None (none found)."""
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


# ── INDIVIDUAL_SPORT_CODES early-exit (tennis defer) ────────────


class TestTennisDeferredToPhase2D:
    """Per design Q D.1 + 2C.2.5 dry-run: individual sports early-exit
    with reason_code='no_match', fail_reason='deferred_to_2d'.
    """

    @pytest.mark.asyncio
    async def test_tennis_signal_returns_no_match_deferred_to_2d(self):
        candidates = _candidate_index(
            ("tennis", "Kecmanovic M.", _tid()),
        )
        m = AliasTierMatcher(candidates=candidates, sport_id_by_code_or_name=_SPORT_MAP)
        sig = _signal(sport="Tennis", home_raw="Miomir Kecmanovic", away_raw="Andrey Rublev")

        result = await m.match(_session_with_corroboration(False), sig)
        assert result.reason_code == ReasonCode.NO_MATCH
        assert result.reason_detail["fail_reason"] == "deferred_to_2d"
        assert result.reason_detail["individual_sport"] is True
        assert result.resolver_version == ALIAS_RESOLVER_VERSION

    @pytest.mark.asyncio
    @pytest.mark.parametrize("sport_code", sorted(INDIVIDUAL_SPORT_CODES))
    async def test_every_individual_sport_in_set_defers(self, sport_code):
        # Sanity: every entry in the constant routes to deferred_to_2d.
        # If a sport gets removed from the set later, this test fails
        # loudly — caller has to update intentionally.
        # Build a one-off CandidateIndex inline so we're not coupled
        # to the test helper's _SPORT_MAP.
        sport_id = 999  # arbitrary; only used inside this test.
        ci = CandidateIndex()
        struct = structurally_normalize("Some Person", sport_code=sport_code)
        ct = CandidateTeam(
            team_id=_tid(),
            canonical_name="Some Person",
            structured=struct,
        )
        ci._by_sport.setdefault(sport_id, []).append(ct)
        m = AliasTierMatcher(
            candidates=ci,
            sport_id_by_code_or_name={
                sport_code.title(): sport_id, sport_code: sport_id,
            },
        )
        sig = _signal(sport=sport_code, home_raw="A B", away_raw="C D")
        result = await m.match(_session_with_corroboration(False), sig)
        assert result.reason_code == ReasonCode.NO_MATCH
        assert result.reason_detail["fail_reason"] == "deferred_to_2d"


# ── Per-side collision detection (the user's named test cases) ──


class TestSideCollisionDetection:
    """The user's sign-off message: verify the four explicit cases.
    These exercise AliasTierMatcher._find_best_team_match indirectly
    via the full match() flow — input is a complete signal, output
    is the routing decision."""

    @pytest.mark.asyncio
    async def test_brighton_single_candidate_resolves(self):
        """'Brighton' against a candidate pool with only one Brighton —
        no collision. Resolves to the candidate. Final routing
        depends on confidence + corroboration."""
        only_brighton = _tid()
        away_team = _tid()
        candidates = _candidate_index(
            ("soccer", "Brighton & Hove Albion", only_brighton),
            ("soccer", "Bournemouth", away_team),
        )
        m = AliasTierMatcher(candidates=candidates, sport_id_by_code_or_name=_SPORT_MAP)
        sig = _signal(home_raw="Brighton", away_raw="Bournemouth")
        # With corroboration → should auto-apply.
        result = await m.match(_session_with_corroboration(True), sig)
        assert result.reason_code == ReasonCode.ALIAS, (
            f"Expected ALIAS, got {result.reason_code} (detail={result.reason_detail})"
        )
        assert result.reason_detail["home_team_id"] == str(only_brighton)
        assert result.reason_detail["away_team_id"] == str(away_team)
        assert result.confidence >= AUTO_APPLY_THRESHOLD

    @pytest.mark.asyncio
    async def test_real_sociedad_collision_routes_review(self):
        """'Real Sociedad' subset-matches BOTH 'Real Sociedad' (senior)
        AND 'Real Sociedad II' (reserve) at 1.0 — multiple exact
        matches → collision → review_queue regardless of confidence."""
        senior = _tid()
        reserve = _tid()
        away = _tid()
        candidates = _candidate_index(
            ("soccer", "Real Sociedad",    senior),
            ("soccer", "Real Sociedad II", reserve),
            ("soccer", "Athletic Bilbao",  away),
        )
        m = AliasTierMatcher(candidates=candidates, sport_id_by_code_or_name=_SPORT_MAP)
        sig = _signal(home_raw="Real Sociedad", away_raw="Athletic Bilbao")
        result = await m.match(_session_with_corroboration(True), sig)
        assert result.reason_code == ReasonCode.REVIEW_QUEUE
        assert result.reason_detail["home_collision"] is True
        # Both colliding team_ids surfaced for the reviewer.
        assert senior in [uuid.UUID(t) for t in result.reason_detail["colliding_home_team_ids"]]
        assert reserve in [uuid.UUID(t) for t in result.reason_detail["colliding_home_team_ids"]]

    @pytest.mark.asyncio
    async def test_manchester_collision_routes_review(self):
        """'Manchester' alone matches both United and City above
        the 0.78 threshold (no exact match in either). Collision."""
        united = _tid()
        city = _tid()
        away = _tid()
        candidates = _candidate_index(
            ("soccer", "Manchester United", united),
            ("soccer", "Manchester City",   city),
            ("soccer", "Liverpool",         away),
        )
        m = AliasTierMatcher(candidates=candidates, sport_id_by_code_or_name=_SPORT_MAP)
        sig = _signal(home_raw="Manchester", away_raw="Liverpool")
        result = await m.match(_session_with_corroboration(False), sig)
        assert result.reason_code == ReasonCode.REVIEW_QUEUE
        assert result.reason_detail["home_collision"] is True

    @pytest.mark.asyncio
    async def test_manchester_united_exact_match_wins_over_near_miss(self):
        """The user's exact-match-wins case: 'Manchester United'
        exactly matches 'Manchester United' (1.0) AND near-matches
        'Manchester City' (~0.81). Single 1.0 → exact-match-wins
        rule applies → no collision → auto_apply candidate."""
        united = _tid()
        city = _tid()
        away = _tid()
        candidates = _candidate_index(
            ("soccer", "Manchester United", united),
            ("soccer", "Manchester City",   city),
            ("soccer", "Liverpool",         away),
        )
        m = AliasTierMatcher(candidates=candidates, sport_id_by_code_or_name=_SPORT_MAP)
        sig = _signal(home_raw="Manchester United", away_raw="Liverpool")
        # With corroboration → auto-apply.
        result = await m.match(_session_with_corroboration(True), sig)
        assert result.reason_code == ReasonCode.ALIAS
        assert result.reason_detail["home_team_id"] == str(united)
        assert result.reason_detail.get("home_collision") is not True

    @pytest.mark.asyncio
    async def test_tokyo_multiple_candidates_routes_review(self):
        """'Tokyo' matches 'Tokyo Verdy 1969' AND 'FC Tokyo' at 1.0
        each (subset matches in both directions). Collision."""
        verdy = _tid()
        fc_tokyo = _tid()
        away = _tid()
        candidates = _candidate_index(
            ("soccer", "Tokyo Verdy 1969", verdy),
            ("soccer", "FC Tokyo",         fc_tokyo),
            ("soccer", "Yokohama",         away),
        )
        m = AliasTierMatcher(candidates=candidates, sport_id_by_code_or_name=_SPORT_MAP)
        sig = _signal(home_raw="Tokyo", away_raw="Yokohama")
        result = await m.match(_session_with_corroboration(False), sig)
        assert result.reason_code == ReasonCode.REVIEW_QUEUE
        assert result.reason_detail["home_collision"] is True


# ── Anchor failure (alias_no_team_resemblance) ─────────────────


class TestAnchorFailure:
    @pytest.mark.asyncio
    async def test_no_candidate_above_threshold_routes_no_match(self):
        # Provider input doesn't resemble any candidate.
        candidates = _candidate_index(
            ("soccer", "Real Madrid",     _tid()),
            ("soccer", "Atletico Madrid", _tid()),
        )
        m = AliasTierMatcher(candidates=candidates, sport_id_by_code_or_name=_SPORT_MAP)
        sig = _signal(home_raw="Random Unknown FC", away_raw="Some Other Team")
        result = await m.match(_session_with_corroboration(False), sig)
        assert result.reason_code == ReasonCode.NO_MATCH
        assert result.reason_detail["fail_reason"] == "alias_no_team_resemblance"


# ── Confidence routing ─────────────────────────────────────────


class TestConfidenceRouting:
    @pytest.mark.asyncio
    async def test_perfect_match_no_corroboration_routes_review(self):
        """0.50 (anchor) + 0.30 (linear max from avg=1.0) = 0.80
        → review_queue (between 0.70 and 0.85)."""
        h, a = _tid(), _tid()
        candidates = _candidate_index(
            ("soccer", "Bayern Munich", h),
            ("soccer", "PSG",           a),
        )
        m = AliasTierMatcher(candidates=candidates, sport_id_by_code_or_name=_SPORT_MAP)
        sig = _signal(home_raw="Bayern Munich", away_raw="PSG")
        result = await m.match(_session_with_corroboration(False), sig)
        assert result.reason_code == ReasonCode.REVIEW_QUEUE
        assert REVIEW_QUEUE_THRESHOLD <= result.confidence < AUTO_APPLY_THRESHOLD

    @pytest.mark.asyncio
    async def test_perfect_match_with_corroboration_routes_auto_apply(self):
        """0.80 + 0.20 corroboration = 1.00 → auto_apply."""
        h, a = _tid(), _tid()
        candidates = _candidate_index(
            ("soccer", "Bayern Munich", h),
            ("soccer", "PSG",           a),
        )
        m = AliasTierMatcher(candidates=candidates, sport_id_by_code_or_name=_SPORT_MAP)
        sig = _signal(home_raw="Bayern Munich", away_raw="PSG")
        result = await m.match(_session_with_corroboration(True), sig)
        assert result.reason_code == ReasonCode.ALIAS
        assert result.confidence == pytest.approx(1.00)


# ── Gate failures (sport / kickoff) ────────────────────────────


class TestGateFailures:
    @pytest.mark.asyncio
    async def test_sport_not_classified_returns_no_match(self):
        candidates = _candidate_index(("soccer", "Real Madrid", _tid()))
        m = AliasTierMatcher(candidates=candidates, sport_id_by_code_or_name=_SPORT_MAP)
        sig = _signal(sport="")  # empty sport
        result = await m.match(_session_with_corroboration(False), sig)
        assert result.reason_code == ReasonCode.NO_MATCH
        assert result.reason_detail["fail_reason"] == "sport_not_classified"

    @pytest.mark.asyncio
    async def test_kickoff_at_missing_returns_no_match(self):
        candidates = _candidate_index(
            ("soccer", "Bayern Munich", _tid()),
            ("soccer", "PSG", _tid()),
        )
        m = AliasTierMatcher(candidates=candidates, sport_id_by_code_or_name=_SPORT_MAP)
        sig = _signal()
        sig = sig.model_copy(update={"kickoff_at": None})
        result = await m.match(_session_with_corroboration(False), sig)
        assert result.reason_code == ReasonCode.NO_MATCH
        assert result.reason_detail["fail_reason"] == "kickoff_at_missing"


# ── TieredMatcher orchestration (D.4 dual-tier logging) ────────


class _StubMatcher:
    """Stand-in for StrictMatcher / AliasTierMatcher in TieredMatcher
    tests. Returns a pre-canned MatchResult."""
    def __init__(self, result: MatchResult) -> None:
        self._result = result

    async def match(self, session, signal) -> MatchResult:
        return self._result


class TestTieredMatcherOrchestration:
    """Per design D.4: when alias picks up a record strict missed,
    BOTH MatchResults are returned. Runner writes one resolution_log
    row per result so 'I tried and failed' is forensic data."""

    @pytest.mark.asyncio
    async def test_strict_hit_returns_one_result(self):
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
        m = TieredMatcher(strict=_StubMatcher(strict_hit), alias=alias)
        sig = _signal()
        results = await m.match(MagicMock(), sig)
        assert len(results) == 1
        assert results[0].reason_code == ReasonCode.STRICT

    @pytest.mark.asyncio
    async def test_strict_miss_alias_hit_returns_both(self):
        """The smoking-gun for D.4: both rows must be written."""
        strict_miss = MatchResult(
            fixture_id=None, confidence=0.0,
            reason_code=ReasonCode.NO_MATCH,
            reason_detail={"fail_reason": "alias_resolution_incomplete"},
            resolver_version="strict@2a.6",
        )
        alias_hit = MatchResult(
            fixture_id=_tid(), confidence=0.85,
            reason_code=ReasonCode.ALIAS,
            reason_detail={"home_team_id": "x", "away_team_id": "y"},
            resolver_version="alias@2c.0",
        )
        m = TieredMatcher(
            strict=_StubMatcher(strict_miss),
            alias=_StubMatcher(alias_hit),
        )
        sig = _signal()
        results = await m.match(MagicMock(), sig)
        assert len(results) == 2
        # Order matters: strict first, alias second.
        assert results[0].reason_code == ReasonCode.NO_MATCH
        assert results[0].resolver_version == "strict@2a.6"
        assert results[1].reason_code == ReasonCode.ALIAS
        assert results[1].resolver_version == "alias@2c.0"

    @pytest.mark.asyncio
    async def test_both_miss_returns_both_no_match_rows(self):
        strict_miss = MatchResult(
            fixture_id=None, confidence=0.0,
            reason_code=ReasonCode.NO_MATCH,
            reason_detail={"fail_reason": "alias_resolution_incomplete"},
            resolver_version="strict@2a.6",
        )
        alias_miss = MatchResult(
            fixture_id=None, confidence=0.0,
            reason_code=ReasonCode.NO_MATCH,
            reason_detail={"fail_reason": "alias_no_team_resemblance"},
            resolver_version="alias@2c.0",
        )
        m = TieredMatcher(
            strict=_StubMatcher(strict_miss),
            alias=_StubMatcher(alias_miss),
        )
        sig = _signal()
        results = await m.match(MagicMock(), sig)
        assert len(results) == 2
        assert all(r.reason_code == ReasonCode.NO_MATCH for r in results)

    @pytest.mark.asyncio
    async def test_strict_miss_alias_review_returns_both(self):
        strict_miss = MatchResult(
            fixture_id=None, confidence=0.0,
            reason_code=ReasonCode.NO_MATCH, reason_detail={},
            resolver_version="strict@2a.6",
        )
        alias_review = MatchResult(
            fixture_id=None, confidence=0.80,
            reason_code=ReasonCode.REVIEW_QUEUE,
            reason_detail={"home_collision": True},
            resolver_version="alias@2c.0",
        )
        m = TieredMatcher(
            strict=_StubMatcher(strict_miss),
            alias=_StubMatcher(alias_review),
        )
        results = await m.match(MagicMock(), _signal())
        assert len(results) == 2
        assert results[1].reason_code == ReasonCode.REVIEW_QUEUE


# ── CandidateIndex bulk-load ───────────────────────────────────


class TestCandidateIndex:
    @pytest.mark.asyncio
    async def test_load_all_groups_by_sport(self):
        # Mock a session whose execute returns 3 team rows across 2 sports.
        class _Row:
            def __init__(self, **kw):
                for k, v in kw.items():
                    setattr(self, k, v)

        rows = [
            _Row(team_id=uuid.uuid4(), sport_id=1, canonical_name="Bayern Munich", sport_code="soccer"),
            _Row(team_id=uuid.uuid4(), sport_id=1, canonical_name="PSG",           sport_code="soccer"),
            _Row(team_id=uuid.uuid4(), sport_id=2, canonical_name="Federer R.",    sport_code="tennis"),
        ]
        result = MagicMock()
        result.all = MagicMock(return_value=rows)
        session = AsyncMock()
        session.execute = AsyncMock(return_value=result)

        ci = await CandidateIndex.load_all(session)
        assert len(ci.candidates_for_sport(1)) == 2
        assert len(ci.candidates_for_sport(2)) == 1
        # Personal sport surname index built (for Phase 2D).
        assert len(ci.candidates_for_surname(2, "federer")) == 1
        # Stats reasonable.
        s = ci.stats()
        assert s["total_teams"] == 3
        assert s["unique_sports"] == 2


# ── Static guards (backstop) ───────────────────────────────────


class TestStaticGuards:
    """Backstop only — primary surface is the call-path tests above.
    Lesson from PR #87: don't rely on these alone."""

    def setup_method(self):
        import resolver.alias_tier.matcher
        self.matcher_src = inspect.getsource(resolver.alias_tier.matcher)
        import scripts.run_resolver_pass
        self.runner_src = inspect.getsource(scripts.run_resolver_pass)

    def test_alias_matcher_writes_nothing(self):
        # Per design B.1 + atomic-discipline carry-forward: the
        # matcher returns MatchResult; the runner does ALL writes.
        forbidden = ["session.add(", "INSERT INTO", "UPDATE sp.", "DELETE FROM"]
        for pat in forbidden:
            assert pat not in self.matcher_src, (
                f"AliasTierMatcher must not write to DB; found {pat!r}"
            )

    def test_runner_writes_alias_back_on_alias_auto_apply(self):
        # Per design §3 + D.5: alias-tier auto-apply writes back to
        # sp.team_aliases with source='alias_tier' so the next
        # strict-tier pass picks up at 0.98 confidence.
        assert "INSERT INTO sp.team_aliases" in self.runner_src
        assert "'alias_tier'" in self.runner_src
        assert "ON CONFLICT (alias_normalized, source)" in self.runner_src

    def test_runner_inserts_review_queue_row_on_review(self):
        assert "session.add(ReviewQueue(" in self.runner_src
        assert "candidate_fixtures=" in self.runner_src

    def test_runner_iterates_tier_results(self):
        # D.4 dual-tier logging — every tier result writes a
        # resolution_log row.
        assert "for tier_result in tier_results:" in self.runner_src

    def test_runner_uses_tiered_matcher(self):
        assert "TieredMatcher(" in self.runner_src
        assert "AliasTierMatcher(" in self.runner_src

    def test_alias_resolver_version_distinct_from_strict(self):
        assert ALIAS_RESOLVER_VERSION != "strict@2a.6"
        assert ALIAS_RESOLVER_VERSION == "alias@2c.0"

    def test_tiered_resolver_version_distinct_from_per_tier(self):
        # The orchestrator version stamp must remain distinct from
        # per-tier versions. Phase 2D.3 bumped it to tiered@2d.0;
        # the spirit of this 2C-era guard (orchestrator stamp tracks
        # the resolver topology, not any single tier) carries forward.
        assert TIERED_RESOLVER_VERSION == "tiered@2d.0"
        assert TIERED_RESOLVER_VERSION != "strict@2a.6"
        assert TIERED_RESOLVER_VERSION != ALIAS_RESOLVER_VERSION
