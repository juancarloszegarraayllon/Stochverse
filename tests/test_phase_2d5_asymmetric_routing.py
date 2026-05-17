"""Phase 2D.5 sub-PR #1 — route asymmetric anchor failures to review_queue.

Scaffold-first failing tests. Implementation lands in subsequent
commits on this branch.

## Context

Day-7 retrospective (2026-05-17, PROJECT_STATE.md) surfaced that the
current fuzzy tier collapses ALL `home_anchor_failed OR away_anchor_failed`
records into `fuzzy_no_team_resemblance` no_match. Production sampling
showed two distinct shapes hiding inside that one bucket:

  (a) Both sides anchor-failed — genuinely-unresolvable record
      (typically a sport-mismatch or noise). Correct destination:
      no_match.

  (b) Exactly one side anchor-failed — operator-actionable record
      where the anchored side gives a strong fixture-narrowing
      signal and the failed side just needs a candidate-pick. Current
      destination (no_match) is wrong; correct destination is
      review_queue with top-3 candidates surfaced for the failed
      side.

Day-7 sample: ~1,837 asymmetric records over 28 days. Routing those
to review_queue is the Priority A intervention.

## Routing-shape discriminator

The `reason_detail` JSONB column on `sp.review_queue` carries a
`routing_shape` string field that the admin UI branches on. New
constant: ``"asymmetric_anchor_failure"``. Distinct from the existing
collision shape (which sets `{side}_collision` + `colliding_{side}_team_ids`
keys); collision rows continue to use those keys, asymmetric rows
use the new keys instead.

## Kalshi prop-market exclusion (precision-optimized)

Kalshi market titles like ``"Colorado Rockies vs Arizona Diamondbacks:
Hits"`` parse into two parsed_names with prop-market suffixes attached
inconsistently (sometimes home, sometimes away, sometimes both,
sometimes the prop segment is the entire side value). These would
otherwise route through asymmetric-→-review_queue, polluting the
operator queue with structural false-failures.

The exclusion is **vocabulary-based**: a frozenset of known Kalshi
prop segments (``"Hits"``, ``"Total Goals"``, etc.) seeded from
production sampling. The check inspects BOTH parsed names regardless
of which side anchor-failed.

Fail-open: a new Kalshi prop type not in the vocabulary will route
to review_queue, surfacing as an operator rejection, at which point
the vocabulary gets a one-line addition.

## Test strategy

  - **Resolver unit tests** (TestAsymmetricRouting): construct
    FuzzyTierMatcher with a synthetic CandidateIndex, call match()
    directly, assert reason_code + routing_shape on result.
  - **Admin integration tests** (TestAsymmetricReviewQueueDetail):
    SP_INTEGRATION_DB-gated; seed sp.review_queue row with
    routing_shape, fetch detail-view, assert template branches
    on routing_shape correctly.

Scaffold currently fails because:
  (a) Matcher's anchor-failure branch unconditionally returns
      no_match; doesn't yet split symmetric vs asymmetric.
  (b) `KALSHI_PROP_MARKET_SEGMENTS` / `_looks_like_kalshi_prop_market` /
      `_should_exclude_from_asymmetric_routing` don't exist.
  (c) `ReviewQueueDetail.routing_shape` field doesn't exist.
  (d) Template branch on `routing_shape == "asymmetric_anchor_failure"`
      doesn't exist.

All scaffold tests intentionally fail. Implementation commits will
turn each green in sequence.
"""
from __future__ import annotations

import json
import os
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import bcrypt
import pytest

from resolver import (
    FuzzyTierMatcher,
    FixtureSignal,
    ReasonCode,
    TeamCandidate,
)
from resolver.alias_tier import (
    CandidateIndex,
    StructuredName,
    structurally_normalize,
)
from resolver.alias_tier.candidates import CandidateTeam


# ── Module-level constant pinned by tests ──────────────────────


# String constant that the matcher writes into reason_detail and the
# template branches on. Pinned here so renames become typecheckable.
ASYMMETRIC_ROUTING_SHAPE = "asymmetric_anchor_failure"


# ── Helpers (mirror test_resolver_2d.py) ───────────────────────


_BASEBALL_SPORT_ID = 3
_BASKETBALL_SPORT_ID = 7
_SOCCER_SPORT_ID = 1
_HOCKEY_SPORT_ID = 5

_SPORT_MAP = {
    "Baseball":    _BASEBALL_SPORT_ID, "baseball":   _BASEBALL_SPORT_ID,
    "Basketball":  _BASKETBALL_SPORT_ID, "basketball": _BASKETBALL_SPORT_ID,
    "Soccer":      _SOCCER_SPORT_ID, "soccer":     _SOCCER_SPORT_ID,
    "Hockey":      _HOCKEY_SPORT_ID, "hockey":     _HOCKEY_SPORT_ID,
}


def _tid() -> uuid.UUID:
    return uuid.uuid4()


def _candidate_index(*team_names) -> CandidateIndex:
    """Build a CandidateIndex from (sport_code, canonical_name, team_id)
    tuples. Same shape as test_resolver_2d.py:_candidate_index."""
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
    return ci


def _signal(
    *,
    sport: str = "Baseball",
    home_raw: str,
    away_raw: str,
    provider: str = "kalshi",
    provider_record_id: str = "rec-1",
    kickoff_at: datetime | None = None,
) -> FixtureSignal:
    return FixtureSignal(
        provider=provider,
        provider_record_id=provider_record_id,
        sport=sport,
        home_team_candidates=[TeamCandidate(
            raw=home_raw, normalized=home_raw.lower(),
            kind="name", weight=0.9,
        )],
        away_team_candidates=[TeamCandidate(
            raw=away_raw, normalized=away_raw.lower(),
            kind="name", weight=0.9,
        )],
        kickoff_at=kickoff_at or datetime(2026, 5, 17, 18, tzinfo=timezone.utc),
        kickoff_confidence=1.0,
    )


def _session_no_corroboration() -> MagicMock:
    session = MagicMock()

    async def execute(stmt, params=None):
        result = MagicMock()
        result.first = MagicMock(return_value=None)
        return result

    session.execute = AsyncMock(side_effect=execute)
    return session


# ══════════════════════════════════════════════════════════════
# Resolver unit tests
# ══════════════════════════════════════════════════════════════


class TestAsymmetricRouting:
    """Matcher-level tests: verify the anchor-failure branch correctly
    distinguishes both-sides-failed (no_match) from one-side-failed
    (review_queue with routing_shape set)."""

    @pytest.mark.asyncio
    async def test_both_anchor_failed_still_routes_no_match(self):
        """Symmetric anchor failure is preserved as no_match. This is
        the baseline behavior — no regression from PR #137/#138's
        reason_detail preservation. Both `home_anchor_failed=True` AND
        `away_anchor_failed=True` → fuzzy_no_team_resemblance."""
        # Empty CandidateIndex — both sides will anchor-fail.
        ci = _candidate_index()
        m = FuzzyTierMatcher(
            candidates=ci, sport_id_by_code_or_name=_SPORT_MAP,
        )
        sig = _signal(
            sport="Baseball",
            home_raw="ZzqxFakeAlpha",
            away_raw="ZzqxFakeOmega",
        )
        result = await m.match(_session_no_corroboration(), sig)
        assert result.reason_code == ReasonCode.NO_MATCH
        assert result.reason_detail["fail_reason"] == "fuzzy_no_team_resemblance"
        assert result.reason_detail["home_anchor_failed"] is True
        assert result.reason_detail["away_anchor_failed"] is True
        # Routing-shape must NOT be set on symmetric failures —
        # downstream consumers branch on its presence.
        assert "routing_shape" not in result.reason_detail

    @pytest.mark.asyncio
    async def test_home_anchor_failed_only_routes_review_queue(self):
        """Home fails, away anchors. routing_shape must be set to
        ASYMMETRIC_ROUTING_SHAPE; reason_code REVIEW_QUEUE."""
        away_team_id = _tid()
        ci = _candidate_index(
            ("baseball", "HawksTestAlpha", away_team_id),
        )
        m = FuzzyTierMatcher(
            candidates=ci, sport_id_by_code_or_name=_SPORT_MAP,
        )
        sig = _signal(
            sport="Baseball",
            home_raw="ZzqxFakeUnknown",
            away_raw="HawksTestAlpha",
        )
        result = await m.match(_session_no_corroboration(), sig)
        assert result.reason_code == ReasonCode.REVIEW_QUEUE
        assert result.reason_detail.get("routing_shape") == ASYMMETRIC_ROUTING_SHAPE
        assert result.reason_detail["home_anchor_failed"] is True
        assert result.reason_detail["away_anchor_failed"] is False

    @pytest.mark.asyncio
    async def test_away_anchor_failed_only_routes_review_queue(self):
        """Mirror of the home-failed case — symmetry guard."""
        home_team_id = _tid()
        ci = _candidate_index(
            ("baseball", "HawksTestAlpha", home_team_id),
        )
        m = FuzzyTierMatcher(
            candidates=ci, sport_id_by_code_or_name=_SPORT_MAP,
        )
        sig = _signal(
            sport="Baseball",
            home_raw="HawksTestAlpha",
            away_raw="ZzqxFakeUnknown",
        )
        result = await m.match(_session_no_corroboration(), sig)
        assert result.reason_code == ReasonCode.REVIEW_QUEUE
        assert result.reason_detail.get("routing_shape") == ASYMMETRIC_ROUTING_SHAPE
        assert result.reason_detail["home_anchor_failed"] is False
        assert result.reason_detail["away_anchor_failed"] is True

    @pytest.mark.asyncio
    async def test_asymmetric_review_queue_candidate_count(self):
        """Failed side surfaces top-3 candidates by trigram similarity
        in candidate_fixtures. Anchored side contributes its single
        matched team_id. Total len(candidate_fixtures) is bounded by
        the failed-side top_n + anchored side's 1 = 4 max, fewer if
        the failed-side trigram lookup returns fewer than 3."""
        anchored_id = _tid()
        # Seed enough teams that the trigram lookup can return 3.
        # (Implementation will run a real similarity() query; for the
        # scaffold we just need to assert the candidate_fixtures shape.)
        ci = _candidate_index(
            ("baseball", "HawksTestAlpha", anchored_id),
            ("baseball", "HawksTestBeta", _tid()),
            ("baseball", "HawksTestGamma", _tid()),
            ("baseball", "HawksTestDelta", _tid()),
        )
        m = FuzzyTierMatcher(
            candidates=ci, sport_id_by_code_or_name=_SPORT_MAP,
        )
        sig = _signal(
            sport="Baseball",
            home_raw="HawksTestAlpha",  # anchors
            away_raw="ZzqxFakeUnknown",  # fails
        )
        result = await m.match(_session_no_corroboration(), sig)
        assert result.reason_code == ReasonCode.REVIEW_QUEUE
        assert result.reason_detail.get("routing_shape") == ASYMMETRIC_ROUTING_SHAPE
        # candidate_fixtures non-empty; at minimum the anchored side's
        # team_id surfaces. Failed-side top-3 lookup populates the rest.
        assert len(result.candidate_fixtures) >= 1
        assert anchored_id in result.candidate_fixtures

    # ── Kalshi prop-market exclusion (bilateral vocab check) ──

    @pytest.mark.asyncio
    async def test_prop_on_home_side_excluded(self):
        """Kalshi prop pattern: `home="Overtime", away="Vegas"`. Vegas
        anchors against the seeded team; "Overtime" is in the prop
        vocabulary. Despite being structurally asymmetric, must route
        to no_match (NOT review_queue) because this is a prop market,
        not a real fixture."""
        vegas_id = _tid()
        ci = _candidate_index(
            ("hockey", "Vegas", vegas_id),
        )
        m = FuzzyTierMatcher(
            candidates=ci, sport_id_by_code_or_name=_SPORT_MAP,
        )
        sig = _signal(
            sport="Hockey",
            home_raw="Overtime",  # in vocab
            away_raw="Vegas",     # anchors
            provider="kalshi",
        )
        result = await m.match(_session_no_corroboration(), sig)
        assert result.reason_code == ReasonCode.NO_MATCH
        assert result.reason_detail["fail_reason"] == "fuzzy_no_team_resemblance"
        # Excluded from asymmetric routing — routing_shape NOT set.
        assert "routing_shape" not in result.reason_detail
        # Forensic marker: why was this excluded?
        assert result.reason_detail.get("asymmetric_excluded") == "kalshi_prop_market"

    @pytest.mark.asyncio
    async def test_prop_on_away_side_excluded(self):
        """Mirror: `home="Colorado", away="Hits"`. Colorado anchors;
        "Hits" is in the prop vocabulary. Must route to no_match."""
        colorado_id = _tid()
        ci = _candidate_index(
            ("baseball", "Colorado", colorado_id),
        )
        m = FuzzyTierMatcher(
            candidates=ci, sport_id_by_code_or_name=_SPORT_MAP,
        )
        sig = _signal(
            sport="Baseball",
            home_raw="Colorado",  # anchors
            away_raw="Hits",      # in vocab
            provider="kalshi",
        )
        result = await m.match(_session_no_corroboration(), sig)
        assert result.reason_code == ReasonCode.NO_MATCH
        assert result.reason_detail["fail_reason"] == "fuzzy_no_team_resemblance"
        assert "routing_shape" not in result.reason_detail
        assert result.reason_detail.get("asymmetric_excluded") == "kalshi_prop_market"

    @pytest.mark.asyncio
    async def test_prop_on_both_sides_excluded(self):
        """Both sides carry prop suffixes: `home="Anaheim: Total Points",
        away="Game 3: Vegas"`. The Anaheim side's suffix matches vocab
        (`Total Points`); even though "Game 3" is NOT in vocab, the
        presence of ANY vocabulary match on EITHER side triggers
        exclusion."""
        # Synthetic candidate that the home structurally-normalized
        # form could match — but the prop-market detection should
        # short-circuit before any candidate matching matters.
        ci = _candidate_index(
            ("hockey", "Anaheim", _tid()),
            ("hockey", "Vegas", _tid()),
        )
        m = FuzzyTierMatcher(
            candidates=ci, sport_id_by_code_or_name=_SPORT_MAP,
        )
        sig = _signal(
            sport="Hockey",
            home_raw="Anaheim: Total Points",
            away_raw="Game 3: Vegas",
            provider="kalshi",
        )
        result = await m.match(_session_no_corroboration(), sig)
        assert result.reason_code == ReasonCode.NO_MATCH
        assert "routing_shape" not in result.reason_detail
        assert result.reason_detail.get("asymmetric_excluded") == "kalshi_prop_market"

    @pytest.mark.asyncio
    async def test_playoff_series_not_excluded(self):
        """Vocabulary-precision test: `home="Anaheim", away="Game 3: Vegas"`.
        Anaheim anchors; "Game 3" is the segment after the colon on the
        away side, but "Game 3" is NOT in KALSHI_PROP_MARKET_SEGMENTS.
        This is a real NHL playoff record — must route to review_queue
        (asymmetric), NOT no_match (excluded).

        This is the operational difference between the prior simple `:`
        heuristic (which would have wrongly filtered ~20-50 records/week
        during NHL playoffs) and the vocabulary-based approach
        (precision-optimized)."""
        anaheim_id = _tid()
        ci = _candidate_index(
            ("hockey", "Anaheim", anaheim_id),
        )
        m = FuzzyTierMatcher(
            candidates=ci, sport_id_by_code_or_name=_SPORT_MAP,
        )
        sig = _signal(
            sport="Hockey",
            home_raw="Anaheim",
            away_raw="Game 3: Vegas",  # colon present, but "Game 3"
                                       # not in prop vocab
            provider="kalshi",
        )
        result = await m.match(_session_no_corroboration(), sig)
        assert result.reason_code == ReasonCode.REVIEW_QUEUE, (
            f"Expected REVIEW_QUEUE (playoff series, vocab miss), got "
            f"{result.reason_code} (detail={result.reason_detail})"
        )
        assert result.reason_detail.get("routing_shape") == ASYMMETRIC_ROUTING_SHAPE

    @pytest.mark.asyncio
    async def test_unknown_prop_segment_routes_to_review_queue_failopen(self):
        """Failopen direction: a new Kalshi prop type not yet in the
        vocabulary (e.g., a hypothetical `"Sacks"` for football props)
        flows through to review_queue rather than getting filtered.
        Operators see it, reject it, and the vocabulary gets a one-line
        update.

        This is the precision-vs-recall tradeoff: vocabulary-based
        exclusion is high-precision (no false positives on real games)
        at the cost of recall (new prop types reach operators until
        cataloged). The failopen direction is the right one — operator
        burden of one rejection per new prop type vs. routing real
        games to no_match silently."""
        away_team_id = _tid()
        ci = _candidate_index(
            ("baseball", "TestTeamRealAlpha", away_team_id),
        )
        m = FuzzyTierMatcher(
            candidates=ci, sport_id_by_code_or_name=_SPORT_MAP,
        )
        sig = _signal(
            sport="Baseball",
            home_raw="TestTeamRealAlpha",
            # Hypothetical new prop type — NOT in vocabulary.
            away_raw="HypotheticalNewPropType",
            provider="kalshi",
        )
        result = await m.match(_session_no_corroboration(), sig)
        # Failopen: routes to review_queue (NOT no_match).
        assert result.reason_code == ReasonCode.REVIEW_QUEUE, (
            f"Failopen contract: unknown segment must route to "
            f"review_queue, not get filtered. Got "
            f"{result.reason_code} (detail={result.reason_detail})"
        )
        assert result.reason_detail.get("routing_shape") == ASYMMETRIC_ROUTING_SHAPE


# ══════════════════════════════════════════════════════════════
# Admin integration tests (SP_INTEGRATION_DB-gated)
# ══════════════════════════════════════════════════════════════


REPO_ROOT = Path(__file__).resolve().parent.parent
INTEGRATION_DB = os.environ.get("SP_INTEGRATION_DB", "").strip()
_TEST_PASSWORD = "test-password-not-real-12345"
_TEST_MARKER = "TEST-2D5-SUB1-ASYM"


@pytest.mark.skipif(
    not INTEGRATION_DB,
    reason="SP_INTEGRATION_DB not set — admin asymmetric routing tests need real Postgres.",
)
class TestAsymmetricReviewQueueDetail:
    """Detail-view rendering for asymmetric review_queue records.
    Mirrors `tests/test_phase_2f1_sub_pr6_asymmetric_anchor_failure.py`
    fixture patterns (engine, setup_schema, _seed_* helpers,
    TestClient app fixture)."""

    @pytest.fixture
    def engine(self):
        from sqlalchemy import create_engine
        url = INTEGRATION_DB
        if "+asyncpg" in url:
            url = url.replace("+asyncpg", "")
        eng = create_engine(url)
        yield eng
        eng.dispose()

    @pytest.fixture(autouse=True)
    def setup_schema(self, engine):
        result = subprocess.run(
            ["alembic", "upgrade", "head"],
            cwd=REPO_ROOT, capture_output=True, text=True,
            env={**os.environ, "DATABASE_URL": INTEGRATION_DB},
        )
        assert result.returncode == 0, (
            f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}"
        )
        self._purge_test_data(engine)
        yield
        self._purge_test_data(engine)

    def _purge_test_data(self, engine):
        from sqlalchemy import text
        with engine.begin() as conn:
            conn.execute(text(
                "DELETE FROM sp.review_queue "
                "WHERE provider_record_id LIKE :marker"
            ), {"marker": f"{_TEST_MARKER}%"})
            conn.execute(text(
                "DELETE FROM sp.resolution_log "
                "WHERE provider_record_id LIKE :marker"
            ), {"marker": f"{_TEST_MARKER}%"})
            conn.execute(text(
                "DELETE FROM sp.kalshi_markets WHERE ticker LIKE :marker"
            ), {"marker": f"{_TEST_MARKER}%"})
            conn.execute(text(
                "DELETE FROM sp.teams "
                "WHERE canonical_name LIKE :marker"
            ), {"marker": f"{_TEST_MARKER}%"})

    def _seed_basketball_team(self, engine, canonical_name: str) -> uuid.UUID:
        from sqlalchemy import text
        team_id = uuid.uuid4()
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO sp.teams "
                "(id, sport_id, canonical_name, normalized_name, country_code) "
                "SELECT :id, s.id, :canonical, :normalized, 'US' "
                "FROM sp.sports s WHERE s.name = 'Basketball'"
            ), {
                "id": team_id,
                "canonical": canonical_name,
                "normalized": canonical_name.lower(),
            })
        return team_id

    def _seed_asymmetric_review_queue_row(
        self,
        engine,
        *,
        pk: str,
        anchored_team_id: uuid.UUID,
        anchored_parsed_name: str,
        failed_parsed_name: str,
        candidate_team_ids: list[uuid.UUID],
    ) -> uuid.UUID:
        """Seed a sp.review_queue row representing an asymmetric
        anchor-failure record. Production rows would be written by
        scripts/run_resolver_pass.py; here we synthesize the same
        shape directly."""
        from sqlalchemy import text
        record_id = uuid.uuid4()
        reason_detail = {
            "provider": "kalshi",
            "provider_record_id": pk,
            "sport": "Basketball",
            "routing_shape": ASYMMETRIC_ROUTING_SHAPE,
            "home_anchor_failed": False,
            "away_anchor_failed": True,
            "home_provider_normalized": anchored_parsed_name,
            "away_provider_normalized": failed_parsed_name,
            "home_canonical": anchored_parsed_name,
            "away_canonical": "",
            "home_team_id": str(anchored_team_id),
            "away_team_id": None,
        }
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO sp.review_queue "
                "(id, provider, provider_record_id, provider_title, "
                " candidate_fixtures, confidence, status, "
                " rejection_count, created_at, reason_detail) "
                "VALUES (:id, 'kalshi', :pk, :title, "
                "        CAST(:cf AS jsonb), 0.0, 'pending', "
                "        0, NOW(), CAST(:rd AS jsonb))"
            ), {
                "id": record_id,
                "pk": pk,
                "title": f"{anchored_parsed_name} vs {failed_parsed_name}",
                "cf": json.dumps(
                    [str(anchored_team_id)] + [str(t) for t in candidate_team_ids]
                ),
                "rd": json.dumps(reason_detail),
            })
        return record_id

    @pytest.fixture
    def app(self, monkeypatch, engine):
        test_hash = bcrypt.hashpw(
            _TEST_PASSWORD.encode(), bcrypt.gensalt()
        ).decode()
        monkeypatch.setenv("OPERATOR_PASSWORD_HASH", test_hash)
        monkeypatch.setenv(
            "OPERATOR_SESSION_SECRET",
            "test-session-secret-not-real-aaaaaaaaaaaaaaaa",
        )
        monkeypatch.setenv("DATABASE_URL", INTEGRATION_DB)
        import sys
        for mod in list(sys.modules):
            if (
                mod == "main"
                or mod.startswith("main.")
                or mod.startswith("admin")
                or mod == "db"
            ):
                del sys.modules[mod]
        import main  # noqa: E402
        from starlette.testclient import TestClient
        client = TestClient(main.app)
        client.post(
            "/admin/login",
            data={"password": _TEST_PASSWORD},
            follow_redirects=False,
        )
        yield client

    def test_review_queue_detail_renders_asymmetric_shape(self, engine, app):
        """Detail-view template branches on routing_shape. For an
        asymmetric record, the rendered HTML must:

          - Show the anchored side as a single-pick (NOT radio buttons
            — single-pick is the existing happy-path shape).
          - Show the failed side as radio buttons over the top-3
            candidate team_ids (NEW shape introduced by this PR).
          - NOT render the collision-style "Multiple matches on both
            sides" panel (collision is a distinct routing shape).
        """
        anchored_canonical = "HawksTestAnchored"
        anchored_id = self._seed_basketball_team(engine, anchored_canonical)
        cand1 = self._seed_basketball_team(engine, f"{_TEST_MARKER}-CANDIDATE-1")
        cand2 = self._seed_basketball_team(engine, f"{_TEST_MARKER}-CANDIDATE-2")
        cand3 = self._seed_basketball_team(engine, f"{_TEST_MARKER}-CANDIDATE-3")

        pk = f"{_TEST_MARKER}-DETAIL"
        record_id = self._seed_asymmetric_review_queue_row(
            engine,
            pk=pk,
            anchored_team_id=anchored_id,
            anchored_parsed_name=anchored_canonical,
            failed_parsed_name="ZzqxFakeFailedSide",
            candidate_team_ids=[cand1, cand2, cand3],
        )

        resp = app.get(f"/admin/review-queue/{record_id}")
        assert resp.status_code == 200, (
            f"Detail-view fetch failed: {resp.status_code} body={resp.text[:500]}"
        )
        body = resp.text

        # Anchored side: single-pick hidden input with the matched team_id.
        assert anchored_canonical in body
        assert str(anchored_id) in body

        # Failed side: 3 radio buttons over the candidate team_ids.
        assert "ZzqxFakeFailedSide" in body, (
            "Failed-side parsed name must surface so operator can "
            "see what didn't anchor."
        )
        for cand_id in (cand1, cand2, cand3):
            assert str(cand_id) in body, (
                f"Candidate {cand_id} for failed side must render in "
                "the radio-button group."
            )

        # Discriminator-presence guard: collision-shape language must
        # NOT appear (collision is the existing distinct shape).
        assert "Multiple matches on both sides" not in body, (
            "Asymmetric records must not render collision-shape "
            "language. They're a separate routing shape."
        )

    def test_approve_asymmetric_record_resolves_fixture(self, engine, app):
        """End-to-end approval: operator submits the decision form
        with `anchored_team_id` (hidden) + `failed_team_id` (one of
        the radio choices). Handler must:

          - Treat both team_ids as the chosen pair.
          - Look up / create the fixture per existing approval flow.
          - Mark the review_queue row status='approved'.
          - Write a sp.resolution_log row with reason_code='manual'
            (or equivalent operator-decision marker).
        """
        anchored_canonical = "HawksTestApproved"
        anchored_id = self._seed_basketball_team(engine, anchored_canonical)
        cand_id = self._seed_basketball_team(engine, f"{_TEST_MARKER}-APPROVE-CAND")
        # Extra candidates that operator could have picked but didn't.
        self._seed_basketball_team(engine, f"{_TEST_MARKER}-APPROVE-OTHER-1")
        self._seed_basketball_team(engine, f"{_TEST_MARKER}-APPROVE-OTHER-2")

        pk = f"{_TEST_MARKER}-APPROVE"
        record_id = self._seed_asymmetric_review_queue_row(
            engine,
            pk=pk,
            anchored_team_id=anchored_id,
            anchored_parsed_name=anchored_canonical,
            failed_parsed_name="ZzqxFakeFailedApprove",
            candidate_team_ids=[cand_id],
        )

        # Submit the decision form. The exact field names/shape will
        # be pinned by the implementation; for the scaffold we use the
        # most-natural shape (matches collision-side conventions in
        # admin/router.py).
        resp = app.post(
            f"/admin/review-queue/{record_id}/approve",
            data={
                "home_team_id": str(anchored_id),
                "away_team_id": str(cand_id),
            },
            follow_redirects=False,
        )
        # Approval routes back to the queue index (302) on success.
        assert resp.status_code in (302, 303), (
            f"Expected redirect after approval, got {resp.status_code} "
            f"body={resp.text[:500]}"
        )

        # Verify queue row marked approved.
        from sqlalchemy import text
        with engine.begin() as conn:
            row = conn.execute(text(
                "SELECT status FROM sp.review_queue WHERE id = :id"
            ), {"id": record_id}).first()
        assert row is not None
        assert row.status == "approved", (
            f"Expected status='approved' after approval submission, "
            f"got status={row.status!r}"
        )


# ══════════════════════════════════════════════════════════════
# Matcher-level integration test: real pg_trgm trigram lookup
# ══════════════════════════════════════════════════════════════


@pytest.mark.skipif(
    not INTEGRATION_DB,
    reason="SP_INTEGRATION_DB not set — real-trigram top-N lookup needs real Postgres.",
)
class TestAsymmetricRoutingRealTrigram:
    """Matcher-level integration test: real pg_trgm-backed top-N lookup
    against sp.teams. Mocked unit tests pin routing-decision logic;
    this test pins three load-bearing properties of the failed-side
    candidate lookup that mocks can't honestly cover:

      (1) **Sport-gated**: cross-sport candidates are filtered out by
          the sport_id predicate. Without this, NBA teams could surface
          as suggestions for an MLB record's failed side.

      (2) **Order-preserved**: trigram similarity DESC ordering survives
          the round-trip through SQLAlchemy. Operators see best matches
          first; a silent reordering bug would degrade UX without
          breaking correctness assertions.

      (3) **Bounded count**: LIMIT 3 is enforced. Tolerates low-coverage
          sports returning fewer than 3 (zero candidates is acceptable
          when no team in the same sport clears the similarity floor).

    Real Postgres + real pg_trgm is the right test surface — mocking
    similarity() would test the mock, not the matcher.
    """

    _LOCAL_MARKER = "TEST-2D5-TRIGRAM"

    @pytest.fixture
    def engine(self):
        from sqlalchemy import create_engine
        url = INTEGRATION_DB
        if "+asyncpg" in url:
            url = url.replace("+asyncpg", "")
        eng = create_engine(url)
        yield eng
        eng.dispose()

    @pytest.fixture(autouse=True)
    def setup_schema(self, engine):
        result = subprocess.run(
            ["alembic", "upgrade", "head"],
            cwd=REPO_ROOT, capture_output=True, text=True,
            env={**os.environ, "DATABASE_URL": INTEGRATION_DB},
        )
        assert result.returncode == 0, (
            f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}"
        )
        self._purge_test_data(engine)
        yield
        self._purge_test_data(engine)

    def _purge_test_data(self, engine):
        from sqlalchemy import text
        with engine.begin() as conn:
            conn.execute(text(
                "DELETE FROM sp.teams WHERE canonical_name LIKE :marker"
            ), {"marker": f"{self._LOCAL_MARKER}%"})

    def _seed_team(
        self, engine, *, sport_name: str, canonical_name: str,
    ) -> uuid.UUID:
        from sqlalchemy import text
        team_id = uuid.uuid4()
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO sp.teams "
                "(id, sport_id, canonical_name, normalized_name, country_code) "
                "SELECT :id, s.id, :canonical, :normalized, 'US' "
                "FROM sp.sports s WHERE s.name = :sport_name"
            ), {
                "id": team_id, "canonical": canonical_name,
                "normalized": canonical_name.lower(),
                "sport_name": sport_name,
            })
        return team_id

    def _sport_id(self, engine, sport_name: str) -> int:
        from sqlalchemy import text
        with engine.begin() as conn:
            row = conn.execute(text(
                "SELECT id FROM sp.sports WHERE name = :name"
            ), {"name": sport_name}).first()
        assert row is not None, f"sp.sports missing {sport_name!r}"
        return row.id

    @pytest.mark.asyncio
    async def test_asymmetric_review_queue_top_3_candidates_real_trigram(
        self, engine, monkeypatch,
    ):
        """Setup: away side anchors against `<MARKER>-Tigers`; home side
        is `<MARKER>-Tigres-Lookalike` (close trigram to several seeded
        Baseball candidates). Cross-sport decoys in Basketball must NOT
        surface; Baseball candidates must surface DESC by similarity;
        result count is bounded 1..3.
        """
        from sqlalchemy import text

        marker = self._LOCAL_MARKER

        # ── Step 1: seed teams ────────────────────────────────────
        # Anchored side (Baseball): the away_raw will match this.
        anchored_id = self._seed_team(
            engine, sport_name="Baseball",
            canonical_name=f"{marker}-Tigers",
        )
        # Failed-side candidates (Baseball): close trigram matches to
        # the home_raw "<marker>-Tigres-Lookalike". Trigram overlap on
        # "tig"+"igr" shingles. Seed 4 — top-3 LIMIT must drop the
        # weakest.
        cand_strong = self._seed_team(
            engine, sport_name="Baseball",
            canonical_name=f"{marker}-Tigres-Real",
        )
        cand_mid = self._seed_team(
            engine, sport_name="Baseball",
            canonical_name=f"{marker}-Tigris-Variant",
        )
        cand_weak = self._seed_team(
            engine, sport_name="Baseball",
            canonical_name=f"{marker}-Tigwood-Distant",
        )
        cand_weakest = self._seed_team(
            engine, sport_name="Baseball",
            canonical_name=f"{marker}-Tiglet-Faintest",
        )

        # Cross-sport decoy (Basketball): canonical_name shares high
        # trigram overlap with the failed-side parsed name. The
        # sport_id predicate MUST filter this out — if it surfaces in
        # candidate_fixtures we have a cross-sport leak.
        decoy_id = self._seed_team(
            engine, sport_name="Basketball",
            canonical_name=f"{marker}-Tigres-CrossSport-Decoy",
        )

        # ── Step 2: build matcher with real DB-backed CandidateIndex
        monkeypatch.setenv("DATABASE_URL", INTEGRATION_DB)
        import sys
        for mod in list(sys.modules):
            if mod == "db" or mod.startswith("resolver"):
                del sys.modules[mod]

        from db import async_session  # noqa: E402
        from resolver import (  # noqa: E402
            FuzzyTierMatcher,
            FixtureSignal,
            ReasonCode,
            TeamCandidate,
        )
        from resolver.alias_tier import CandidateIndex  # noqa: E402

        # Build sport-id map from sp.sports.
        baseball_sport_id = self._sport_id(engine, "Baseball")
        basketball_sport_id = self._sport_id(engine, "Basketball")
        sport_map = {
            "Baseball": baseball_sport_id, "baseball": baseball_sport_id,
            "Basketball": basketball_sport_id, "basketball": basketball_sport_id,
        }

        # CandidateIndex.refresh loads from sp.teams. Use real async
        # session.
        ci = CandidateIndex()
        async with async_session() as session:
            await ci.refresh(session)

        m = FuzzyTierMatcher(
            candidates=ci, sport_id_by_code_or_name=sport_map,
        )

        # ── Step 3: build signal where home anchor-fails, away anchors
        signal = FixtureSignal(
            provider="kalshi",
            provider_record_id=f"{marker}-REAL-TRIGRAM",
            sport="Baseball",
            home_team_candidates=[TeamCandidate(
                raw=f"{marker}-Tigres-Lookalike",  # close trigram —
                                                    # anchor-fails team
                                                    # path (no fuzz.ratio
                                                    # ≥ 0.85 to any seeded
                                                    # team) but matches
                                                    # several via trigram
                                                    # similarity ≥ 0.30.
                normalized=f"{marker}-tigres-lookalike".lower(),
                kind="name", weight=0.9,
            )],
            away_team_candidates=[TeamCandidate(
                raw=f"{marker}-Tigers",  # anchors against the seeded
                                          # Baseball team via exact match.
                normalized=f"{marker}-tigers".lower(),
                kind="name", weight=0.9,
            )],
            kickoff_at=datetime(2026, 5, 17, 18, tzinfo=timezone.utc),
            kickoff_confidence=1.0,
        )

        # ── Step 4: call matcher
        async with async_session() as session:
            result = await m.match(session, signal)

        # ── Step 5: assert routing
        assert result.reason_code == ReasonCode.REVIEW_QUEUE, (
            f"Expected REVIEW_QUEUE for real-trigram asymmetric, got "
            f"{result.reason_code} (detail={result.reason_detail})"
        )
        assert (
            result.reason_detail.get("routing_shape")
            == ASYMMETRIC_ROUTING_SHAPE
        )
        assert result.reason_detail["home_anchor_failed"] is True
        assert result.reason_detail["away_anchor_failed"] is False

        candidates = list(result.candidate_fixtures)

        # ── Property 1: anchored team FIRST in candidate_fixtures
        # (operator-side rendering convention).
        assert candidates[0] == anchored_id, (
            f"Anchored team must be first in candidate_fixtures. "
            f"Got candidates[0]={candidates[0]}, expected "
            f"anchored_id={anchored_id}"
        )

        failed_candidates = candidates[1:]

        # ── Property 2: bounded count (1..3, LIMIT 3 enforced)
        assert 1 <= len(failed_candidates) <= 3, (
            f"Failed-side candidates must be 1..3 (LIMIT 3, allow "
            f"low-coverage sports). Got {len(failed_candidates)}: "
            f"{failed_candidates}"
        )

        # ── Property 3: sport-gated (no cross-sport leak)
        assert decoy_id not in failed_candidates, (
            f"Cross-sport decoy {decoy_id} (Basketball) must NOT "
            f"surface in Baseball-sport failed-side candidates. "
            f"sport_id predicate not enforced: leak detected."
        )
        with engine.begin() as conn:
            cand_sport_rows = conn.execute(text(
                "SELECT id, sport_id FROM sp.teams "
                "WHERE id = ANY(CAST(:ids AS uuid[]))"
            ), {"ids": [str(c) for c in failed_candidates]}).all()
        for r in cand_sport_rows:
            assert r.sport_id == baseball_sport_id, (
                f"Failed-side candidate {r.id} sport_id={r.sport_id}, "
                f"expected Baseball ({baseball_sport_id}). Cross-sport "
                f"leak."
            )

        # ── Property 4: order-preserved (DESC similarity)
        # Re-run the trigram similarity query server-side and verify
        # the matcher's ordering matches.
        failed_parsed = signal.home_team_candidates[0].raw
        with engine.begin() as conn:
            scored = conn.execute(text(
                "SELECT id, similarity(canonical_name, :name) AS sim "
                "FROM sp.teams "
                "WHERE id = ANY(CAST(:ids AS uuid[])) "
                "ORDER BY similarity(canonical_name, :name) DESC"
            ), {
                "name": failed_parsed,
                "ids": [str(c) for c in failed_candidates],
            }).all()
        expected_order = [r.id for r in scored]
        assert list(failed_candidates) == expected_order, (
            f"Failed-side candidates must be ordered by similarity "
            f"DESC. Matcher returned {failed_candidates}; expected "
            f"{expected_order} (re-scored against seeded teams)."
        )

        # ── Strong-candidate sanity check: the closest-trigram seeded
        # team must surface as the top failed-side candidate.
        assert failed_candidates[0] == cand_strong, (
            f"Highest-similarity candidate {cand_strong} "
            f"({marker}-Tigres-Real) should rank first for "
            f"failed_parsed={failed_parsed!r}. Got "
            f"{failed_candidates[0]}."
        )
