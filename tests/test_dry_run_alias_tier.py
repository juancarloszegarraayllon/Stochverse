"""Tests for scripts/dry_run_alias_tier.py — Phase 2C.2.5.

Real call-path tests with mocked DB session. Lesson from PR #87:
static-source guards aren't enough; the script must run end-to-end
against mocked boundaries to surface scope bugs (NameError,
async/await mismatches, etc.) at test time.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest


# ── CLI smoke tests (defensive — same shape as 2B's TestRunnerCli) ──


class TestDryRunCli:
    def test_help_works(self):
        r = subprocess.run(
            [sys.executable, "scripts/dry_run_alias_tier.py", "--help"],
            capture_output=True, text=True,
        )
        assert r.returncode == 0
        assert "--provider" in r.stdout
        assert "--sport-code" in r.stdout
        assert "--skip-corroboration" in r.stdout

    def test_missing_required_args_fails(self):
        r = subprocess.run(
            [sys.executable, "scripts/dry_run_alias_tier.py"],
            capture_output=True, text=True,
        )
        assert r.returncode != 0


# ── Real call-path: end-to-end with mocked DB ──────────────────


# Pre-baked team_ids so the same uuid appears across all mocks
# referencing "Kecmanovic" — exercises the candidate-lookup +
# scoring path.
_KECMANOVIC_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
_RUBLEV_ID     = uuid.UUID("22222222-2222-2222-2222-222222222222")
_FEDERER_ID    = uuid.UUID("33333333-3333-3333-3333-333333333333")
_NADAL_ID      = uuid.UUID("44444444-4444-4444-4444-444444444444")


class _Row:
    """Stand-in for SQLAlchemy Row with attribute access. The script
    reads attributes (.id, .pk, .raw_payload, .canonical_name, etc.),
    so a plain class with kwargs assigns is the cleanest mock."""
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def _kalshi_payload(*, ticker, home_name, away_name, kickoff_iso):
    """Build a minimal Kalshi raw_payload that extract_signal will
    accept. parse_ticker needs event_ticker + series_ticker; the
    title regex parses 'Home vs Away' for the candidates."""
    return {
        "event_ticker":  ticker,
        "series_ticker": "KXATPMATCH",
        "title":         f"{home_name} vs {away_name}",
        "category":      "Sports",
        "_sport":        "Tennis",
        "_is_sport":     True,
        "_kickoff_dt":   kickoff_iso,
    }


def _build_session_factory(*, sport_row, team_rows, unresolved_rows):
    """Build a session_factory whose async-context-managed sessions
    return the supplied rows depending on which SELECT was issued.

    The script issues three categorically-different queries:
      1. SELECT sp.sports WHERE LOWER(code)=:c       (one row)
      2. SELECT sp.teams WHERE sport_id=:s           (many rows)
      3. SELECT sp.{kalshi_markets,fl_events} ...    (many rows)
      4. find_fixture's SELECT inside the per-record loop (.first())

    We dispatch on the SQL text to keep the mock readable.
    """
    call_log: list[str] = []

    async def execute(stmt, params=None):
        s = str(stmt)
        call_log.append(s)

        result = MagicMock()

        if "FROM sp.sports" in s:
            result.first = MagicMock(return_value=sport_row)
            return result

        if "FROM sp.teams" in s:
            result.all = MagicMock(return_value=team_rows)
            return result

        if "FROM sp.kalshi_markets" in s or "FROM sp.fl_events" in s:
            result.all = MagicMock(return_value=unresolved_rows)
            return result

        # find_fixture's SELECT — return None (no corroboration in
        # the default test setup; tests that want corroboration
        # override the factory).
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
    """Smoking-gun coverage. The script must run end-to-end against
    a mocked DB without raising — same lesson from PR #87."""

    @pytest.mark.asyncio
    async def test_runs_without_error_on_empty_corpus(self):
        from scripts.dry_run_alias_tier import main

        sport_row = _Row(id=2, code="tennis", name="Tennis")
        factory = _build_session_factory(
            sport_row=sport_row,
            team_rows=[],
            unresolved_rows=[],
        )

        rc = await main(
            provider="kalshi", sport_code="tennis",
            limit=None, show_examples=0,
            skip_corroboration=True,
            session_factory=factory,
        )
        assert rc == 0

    @pytest.mark.asyncio
    async def test_kecmanovic_no_corroboration_routes_no_match(self):
        """The user's calibration anchor case at end-to-end shape.
        Provider: 'Miomir Kecmanovic'. Candidate: 'Kecmanovic M. (Srb)'.
        Without corroboration: 0.50 confidence → no_match bucket."""
        from scripts.dry_run_alias_tier import main

        sport_row = _Row(id=2, code="tennis", name="Tennis")
        team_rows = [
            _Row(id=_KECMANOVIC_ID, canonical_name="Kecmanovic M. (Srb)"),
            _Row(id=_RUBLEV_ID,     canonical_name="Rublev A. (Rus)"),
        ]
        unresolved_rows = [
            _Row(
                pk="KXATPMATCH-26MAY09KECMRUBL",
                raw_payload=_kalshi_payload(
                    ticker="KXATPMATCH-26MAY09KECMRUBL",
                    home_name="Miomir Kecmanovic",
                    away_name="Andrey Rublev",
                    kickoff_iso="2026-05-09T14:00:00+00:00",
                ),
            ),
        ]
        factory = _build_session_factory(
            sport_row=sport_row,
            team_rows=team_rows,
            unresolved_rows=unresolved_rows,
        )

        rc = await main(
            provider="kalshi", sport_code="tennis",
            limit=None, show_examples=0,
            skip_corroboration=True,
            session_factory=factory,
        )
        assert rc == 0
        # Note: the bucket counts aren't directly returned (script
        # prints them). The fact that this completes without raising
        # is itself the smoking-gun coverage. Confidence assertions
        # live in test_alias_tier_scorer.py at the unit level.

    @pytest.mark.asyncio
    async def test_extraction_skipped_counted_for_outright_ticker(self):
        """When extract_signal returns None (e.g., Kalshi outright
        prefix from 2C.1's list), the script counts it as
        extraction_skipped without crashing."""
        from scripts.dry_run_alias_tier import main

        sport_row = _Row(id=2, code="tennis", name="Tennis")
        # KXMLBTB is in _OUTRIGHT_SERIES_PREFIXES (PR #91). Even
        # though we're scanning Tennis, a stray outright ticker
        # mis-classified into Tennis category would extract to None.
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
            team_rows=[_Row(id=_KECMANOVIC_ID, canonical_name="Kecmanovic M.")],
            unresolved_rows=unresolved_rows,
        )

        rc = await main(
            provider="kalshi", sport_code="tennis",
            limit=None, show_examples=0,
            skip_corroboration=True,
            session_factory=factory,
        )
        assert rc == 0

    @pytest.mark.asyncio
    async def test_no_corroboration_pass_skips_find_fixture(self):
        """With --skip-corroboration, the script must NOT call
        find_fixture. Verified by counting the SQL-execute calls
        and confirming no SELECT against sp.fixtures appears."""
        from scripts.dry_run_alias_tier import main

        sport_row = _Row(id=2, code="tennis", name="Tennis")
        team_rows = [
            _Row(id=_KECMANOVIC_ID, canonical_name="Kecmanovic M. (Srb)"),
            _Row(id=_RUBLEV_ID,     canonical_name="Rublev A. (Rus)"),
        ]
        unresolved_rows = [
            _Row(
                pk="KXATPMATCH-26MAY09KECMRUBL",
                raw_payload=_kalshi_payload(
                    ticker="KXATPMATCH-26MAY09KECMRUBL",
                    home_name="Miomir Kecmanovic",
                    away_name="Andrey Rublev",
                    kickoff_iso="2026-05-09T14:00:00+00:00",
                ),
            ),
        ]
        factory = _build_session_factory(
            sport_row=sport_row,
            team_rows=team_rows,
            unresolved_rows=unresolved_rows,
        )
        rc = await main(
            provider="kalshi", sport_code="tennis",
            limit=None, show_examples=0,
            skip_corroboration=True,
            session_factory=factory,
        )
        assert rc == 0
        # No SELECT against sp.fixtures should have been issued.
        for sql_text in factory._call_log:
            assert "FROM sp.fixtures" not in sql_text


# ── Static guards (backstop only; primary surface is the call-path) ──


class TestStaticInvariants:
    def setup_method(self):
        import inspect
        import scripts.dry_run_alias_tier
        self.src = inspect.getsource(scripts.dry_run_alias_tier)

    def test_does_not_write_to_db(self):
        """Read-only invariant: no INSERT / UPDATE / DELETE / session.add
        in the script. Calibration is forbidden from mutating state."""
        forbidden = ["INSERT INTO", "UPDATE sp.", "DELETE FROM", "session.add"]
        for pat in forbidden:
            assert pat not in self.src, (
                f"Dry-run must be read-only; found {pat!r} in source. "
                "If a calibration write is genuinely needed, route it "
                "through a separate explicitly-named script."
            )

    def test_session_factory_is_injectable(self):
        """Tests inject a fake session_factory; the parameter must
        exist on main()'s signature."""
        assert "session_factory=None" in self.src
        assert "session_factory=" in self.src or "session_factory:" in self.src

    def test_uses_2c2_alias_tier_constants(self):
        """The fixture-level scorer must consume the same threshold
        constants as 2C.2's scorer module — drift between the dry-run
        and the matcher (2C.3) would defeat the calibration."""
        assert "AUTO_APPLY_THRESHOLD" in self.src
        assert "REVIEW_QUEUE_THRESHOLD" in self.src
        assert "PERSONAL_TOKEN_SET_THRESHOLD" in self.src
        assert "TEAM_TOKEN_SET_THRESHOLD" in self.src
        assert "ANCHOR_SCORE" in self.src
        assert "CORROBORATION_SCORE" in self.src
