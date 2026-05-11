"""Tests for scripts/dry_run_fuzzy_tier.py — Phase 2D.2.5.

Real call-path tests with mocked DB session per the PR #87 lesson.
Same shape as test_dry_run_alias_tier.py.
"""
from __future__ import annotations

import inspect
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest


# Pre-baked team_ids so the same uuid appears across all mocks.
_KECMANOVIC_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
_RUBLEV_ID     = uuid.UUID("22222222-2222-2222-2222-222222222222")
_BAYERN_ID     = uuid.UUID("33333333-3333-3333-3333-333333333333")
_PSG_ID        = uuid.UUID("44444444-4444-4444-4444-444444444444")


# ── CLI smoke ─────────────────────────────────────────────────


class TestDryRunCli:
    def test_help_works(self):
        r = subprocess.run(
            [sys.executable, "scripts/dry_run_fuzzy_tier.py", "--help"],
            capture_output=True, text=True,
        )
        assert r.returncode == 0
        assert "--provider" in r.stdout
        assert "--sport-code" in r.stdout

    def test_missing_required_args_fails(self):
        r = subprocess.run(
            [sys.executable, "scripts/dry_run_fuzzy_tier.py"],
            capture_output=True, text=True,
        )
        assert r.returncode != 0


# ── Real call-path: end-to-end with mocked DB ────────────────


class _Row:
    """Stand-in for SQLAlchemy Row with attribute access."""
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def _kalshi_payload(*, ticker, home_name, away_name, kickoff_iso, sport):
    return {
        "event_ticker":  ticker,
        "series_ticker": "KXATPMATCH",
        "title":         f"{home_name} vs {away_name}",
        "category":      "Sports",
        "_sport":        sport,
        "_is_sport":     True,
        "_kickoff_dt":   kickoff_iso,
    }


def _build_session_factory(
    *,
    sport_row,
    all_sports_rows,
    team_rows,
    unresolved_rows,
    fixture_present: bool = False,
):
    """Build a session_factory whose async-context-managed sessions
    return controlled rows depending on which SELECT was issued.

    Categories of queries:
      1. SELECT from sp.sports WHERE LOWER(code)=...    → sport_row
      2. SELECT id, code, name FROM sp.sports           → all_sports_rows
      3. CandidateIndex.refresh — sp.teams JOIN sp.sports → team_rows
      4. SELECT from sp.kalshi_markets / sp.fl_events    → unresolved_rows
      5. find_fixture (matcher's corroboration check) — returns
         row or None depending on fixture_present
    """
    call_log: list[str] = []

    async def execute(stmt, params=None):
        s = str(stmt)
        call_log.append(s)
        result = MagicMock()

        if "WHERE LOWER(code)" in s:
            result.first = MagicMock(return_value=sport_row)
            return result

        if "FROM sp.sports" in s and "WHERE" not in s:
            result.all = MagicMock(return_value=all_sports_rows)
            return result

        if "FROM sp.teams" in s:
            result.all = MagicMock(return_value=team_rows)
            return result

        if "FROM sp.kalshi_markets" in s or "FROM sp.fl_events" in s:
            result.all = MagicMock(return_value=unresolved_rows)
            return result

        # find_fixture's SELECT (matcher's corroboration check)
        if fixture_present:
            row = MagicMock(id=uuid.uuid4(), competition_id=None)
            result.first = MagicMock(return_value=row)
        else:
            result.first = MagicMock(return_value=None)
        return result

    class _Session:
        async def __aenter__(self):
            session = AsyncMock()
            session.execute = AsyncMock(side_effect=execute)
            session.commit = AsyncMock()
            return session

        async def __aexit__(self, exc_type, exc, tb):
            return False

    def factory():
        return _Session()
    factory._call_log = call_log
    return factory


class TestDryRunEndToEnd:
    """Smoking-gun coverage. The script must run end-to-end without
    raising — same lesson from PR #87."""

    @pytest.mark.asyncio
    async def test_runs_without_error_on_empty_corpus(self):
        from scripts.dry_run_fuzzy_tier import main

        sport_row = _Row(id=2, code="tennis", name="Tennis")
        factory = _build_session_factory(
            sport_row=sport_row,
            all_sports_rows=[sport_row],
            team_rows=[],
            unresolved_rows=[],
        )

        rc = await main(
            provider="kalshi", sport_code="tennis",
            limit=None, show_examples=0,
            session_factory=factory,
        )
        assert rc == 0

    @pytest.mark.asyncio
    async def test_kecmanovic_with_corroboration_routes_auto_apply(self):
        """The user's calibration anchor at end-to-end shape.
        Provider: 'Miomir Kecmanovic' / 'Andrey Rublev'.
        Candidates: 'Kecmanovic M. (Srb)' / 'Rublev A. (Rus)'.
        With corroboration: confidence = 1.00 → auto_apply.
        """
        from scripts.dry_run_fuzzy_tier import main

        sport_row = _Row(id=2, code="tennis", name="Tennis")
        team_rows = [
            _Row(team_id=_KECMANOVIC_ID, sport_id=2,
                 canonical_name="Kecmanovic M. (Srb)", sport_code="tennis"),
            _Row(team_id=_RUBLEV_ID, sport_id=2,
                 canonical_name="Rublev A. (Rus)", sport_code="tennis"),
        ]
        unresolved_rows = [
            _Row(
                pk="KXATPMATCH-26MAY09KECMRUBL",
                raw_payload=_kalshi_payload(
                    ticker="KXATPMATCH-26MAY09KECMRUBL",
                    home_name="Miomir Kecmanovic",
                    away_name="Andrey Rublev",
                    kickoff_iso="2026-05-09T14:00:00+00:00",
                    sport="Tennis",
                ),
            ),
        ]
        factory = _build_session_factory(
            sport_row=sport_row,
            all_sports_rows=[sport_row],
            team_rows=team_rows,
            unresolved_rows=unresolved_rows,
            fixture_present=True,    # corroboration fires
        )

        rc = await main(
            provider="kalshi", sport_code="tennis",
            limit=None, show_examples=0,
            session_factory=factory,
        )
        assert rc == 0
        # End-to-end completion is the smoking-gun coverage. Bucket
        # counts aren't returned (script prints them); confidence
        # math is asserted at the unit level in test_resolver_2d.py.

    @pytest.mark.asyncio
    async def test_extraction_skipped_for_outright_ticker(self):
        """Outright Kalshi tickers (KXMLBTB-shape from 2C.1) extract
        as None and increment extraction_skipped without crashing."""
        from scripts.dry_run_fuzzy_tier import main

        sport_row = _Row(id=2, code="tennis", name="Tennis")
        unresolved_rows = [
            _Row(
                pk="KXMLBTB-26APR15GLEYBER",
                raw_payload={
                    "event_ticker":  "KXMLBTB-26APR15GLEYBER",
                    "series_ticker": "KXMLBTB",
                    "title":         "Gleyber Torres total bases",
                    "category":      "Sports",
                    "_sport":        "Tennis",   # mis-classified upstream
                    "_is_sport":     True,
                    "_kickoff_dt":   "2026-04-15T18:00:00+00:00",
                },
            ),
        ]
        factory = _build_session_factory(
            sport_row=sport_row,
            all_sports_rows=[sport_row],
            team_rows=[
                _Row(team_id=_KECMANOVIC_ID, sport_id=2,
                     canonical_name="Kecmanovic M.", sport_code="tennis"),
            ],
            unresolved_rows=unresolved_rows,
        )

        rc = await main(
            provider="kalshi", sport_code="tennis",
            limit=None, show_examples=0,
            session_factory=factory,
        )
        assert rc == 0

    @pytest.mark.asyncio
    async def test_kalshi_query_filters_by_sport_name(self):
        """Verify the Kalshi SQL filters by raw_payload->>'_sport'
        for the provided sport. Inspect the call log."""
        from scripts.dry_run_fuzzy_tier import main

        sport_row = _Row(id=2, code="tennis", name="Tennis")
        factory = _build_session_factory(
            sport_row=sport_row,
            all_sports_rows=[sport_row],
            team_rows=[],
            unresolved_rows=[],
        )
        rc = await main(
            provider="kalshi", sport_code="tennis",
            limit=None, show_examples=0,
            session_factory=factory,
        )
        assert rc == 0
        # The Kalshi SQL must filter by _sport.
        kalshi_sql = [s for s in factory._call_log if "FROM sp.kalshi_markets" in s]
        assert len(kalshi_sql) == 1
        assert "raw_payload->>'_sport'" in kalshi_sql[0]

    @pytest.mark.asyncio
    async def test_unknown_sport_code_returns_error(self):
        """If --sport-code doesn't match any sp.sports row, the
        script exits with code 3."""
        from scripts.dry_run_fuzzy_tier import main

        factory = _build_session_factory(
            sport_row=None,    # no row found
            all_sports_rows=[],
            team_rows=[],
            unresolved_rows=[],
        )

        rc = await main(
            provider="kalshi", sport_code="curling",
            limit=None, show_examples=0,
            session_factory=factory,
        )
        assert rc == 3

    @pytest.mark.asyncio
    async def test_invalid_provider_returns_error(self):
        """Unknown provider rejected at function entry."""
        from scripts.dry_run_fuzzy_tier import main

        rc = await main(
            provider="polymarket", sport_code="tennis",
            limit=None, show_examples=0,
            session_factory=lambda: None,    # never called; fail-fast first
        )
        assert rc == 2


# ── Counterfactual logic (unit tests for the helper) ──────────


class TestCounterfactualBucket:
    """The dry-run's counterfactual analysis: given a record that
    auto-applied with corroboration, would it still auto-apply
    without? Subtracting CORROBORATION_SCORE from confidence and
    re-routing answers the question. Tested at unit level so the
    script's headline calibration metric is correct."""

    def test_auto_apply_with_corroboration_drops_to_review_when_corr_subtracted(self):
        from scripts.dry_run_fuzzy_tier import _counterfactual_bucket
        # confidence = 1.00 (auto_apply with corr); without corr
        # = 0.70 (review_queue boundary)
        assert _counterfactual_bucket(1.00, has_corroboration=True, bucket="auto_apply") == "review_queue"

    def test_auto_apply_without_corroboration_unchanged(self):
        from scripts.dry_run_fuzzy_tier import _counterfactual_bucket
        # If the record auto_applied WITHOUT corroboration, the
        # counterfactual is the same — no subtraction needed.
        assert _counterfactual_bucket(0.95, has_corroboration=False, bucket="auto_apply") == "auto_apply"

    def test_review_queue_bucket_unchanged_regardless_of_corr(self):
        from scripts.dry_run_fuzzy_tier import _counterfactual_bucket
        # Non-auto-apply buckets aren't subjected to counterfactual
        # subtraction (the question only matters for auto-applies).
        assert _counterfactual_bucket(0.80, has_corroboration=True, bucket="review_queue") == "review_queue"
        assert _counterfactual_bucket(0.65, has_corroboration=False, bucket="no_match") == "no_match"

    def test_corroboration_dependent_auto_apply_drops_to_no_match(self):
        from scripts.dry_run_fuzzy_tier import _counterfactual_bucket
        # confidence = 0.95 (auto_apply with corr); without corr
        # = 0.65 → no_match (below 0.70)
        # Hypothetical: anchor (0.40) + token_set (0.25) + corr (0.30) = 0.95
        assert _counterfactual_bucket(0.95, has_corroboration=True, bucket="auto_apply") == "no_match"

    def test_no_anchor_strong_auto_applies_in_2d_by_construction(self):
        """For fuzzy tier specifically: anchor (0.40) + quality
        (max 0.30) = 0.70 max-without-corroboration. That's exactly
        at the review_queue lower bound, never auto_apply. So in
        actual production data, NO 2D auto-apply can survive
        subtraction of CORROBORATION_SCORE — every fuzzy auto-apply
        is corroboration-dependent. The dry-run report's
        'corroboration-dependent auto-applies' figure should equal
        the total auto-applies; 'would auto-apply WITHOUT
        corroboration' should be 0.

        Verify the math at the boundary: 1.00 - 0.30 = 0.70 →
        review_queue (≥0.70 inclusive, <0.85)."""
        from scripts.dry_run_fuzzy_tier import _counterfactual_bucket
        # Real 2D auto-apply: confidence = 1.00 (perfect signal).
        # Subtract corroboration → 0.70. Routes to review_queue.
        assert _counterfactual_bucket(1.00, has_corroboration=True, bucket="auto_apply") == "review_queue"
        # Slightly higher hypothetical (would be capped in practice
        # but tests the math): 1.10 - 0.30 = 0.80 → still review_queue.
        assert _counterfactual_bucket(1.10, has_corroboration=True, bucket="auto_apply") == "review_queue"

    def test_pct_helper(self):
        from scripts.dry_run_fuzzy_tier import _pct
        assert _pct(0, 0) == "0.0%"
        assert _pct(1, 4) == "25.0%"
        assert _pct(7, 10) == "70.0%"


# ── Static guards ─────────────────────────────────────────────


class TestStaticGuards:
    def setup_method(self):
        import scripts.dry_run_fuzzy_tier
        self.src = inspect.getsource(scripts.dry_run_fuzzy_tier)

    def test_does_not_write_to_db(self):
        """Read-only invariant: no INSERT / UPDATE / DELETE / session.add."""
        forbidden = ["INSERT INTO", "UPDATE sp.", "DELETE FROM", "session.add"]
        for pat in forbidden:
            assert pat not in self.src, (
                f"Dry-run must be read-only; found {pat!r} in source."
            )

    def test_session_factory_is_injectable(self):
        assert "session_factory=None" in self.src

    def test_uses_2d_fuzzy_tier_constants(self):
        """The counterfactual logic must reference the SAME
        thresholds and corroboration weight as the matcher."""
        assert "CORROBORATION_SCORE" in self.src
        assert "AUTO_APPLY_THRESHOLD" in self.src
        assert "REVIEW_QUEUE_THRESHOLD" in self.src

    def test_calibration_warning_message_present(self):
        """Per design rev1 Pushback 5 — the dry-run is the calibration
        gate. If empirical corroboration rate diverges from the
        20-40% range, the operator needs to see the warning."""
        assert "BELOW design rev1 range" in self.src
        assert "ABOVE design rev1 range" in self.src
        assert "20" in self.src
        assert "40" in self.src
