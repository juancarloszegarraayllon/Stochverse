"""Phase 2B strict matcher tests.

Two layers:
  * Unit tests (always run): AliasResolver pure-Python correctness,
    StrictMatcher's four-condition gate, runner argparse + invariants.
  * Integration test stubs (skipped unless SP_INTEGRATION_DB is set):
    document e2e shape; flesh-out is a follow-up CI task.
"""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from resolver import (
    AliasResolver, FixtureSignal, MatchResult, ReasonCode,
    StrictMatcher, TeamCandidate,
)


INTEGRATION_DB = os.environ.get("SP_INTEGRATION_DB", "").strip()


# ── AliasResolver ───────────────────────────────────────────────

class TestAliasResolver:
    def _build(self, entries):
        """Construct an AliasResolver from a flat (alias_norm, sport_id, team_id) list.
        Bypasses the SQL load by directly mutating _index."""
        ar = AliasResolver()
        for alias, sport, tid in entries:
            ar._index[(alias, sport)].add(tid)
        return ar

    def test_resolve_unique_match(self):
        team = uuid.uuid4()
        ar = self._build([("bayern munich", 1, team)])
        cand = TeamCandidate(raw="Bayern Munich", normalized="bayern munich", kind="name")
        assert ar.resolve([cand], sport_id=1) == team

    def test_resolve_returns_none_for_unknown_alias(self):
        ar = self._build([("bayern munich", 1, uuid.uuid4())])
        cand = TeamCandidate(raw="Wolfsburg", normalized="wolfsburg", kind="name")
        assert ar.resolve([cand], sport_id=1) is None

    def test_resolve_returns_none_when_sport_id_is_none(self):
        ar = self._build([("bayern munich", 1, uuid.uuid4())])
        cand = TeamCandidate(raw="Bayern", normalized="bayern munich", kind="name")
        assert ar.resolve([cand], sport_id=None) is None

    def test_resolve_returns_none_when_alias_ambiguous(self):
        # Two team_ids for the same (alias_normalized, sport_id).
        t1, t2 = uuid.uuid4(), uuid.uuid4()
        ar = self._build([
            ("real madrid", 1, t1),
            ("real madrid", 1, t2),
        ])
        cand = TeamCandidate(raw="Real Madrid", normalized="real madrid", kind="name")
        assert ar.resolve([cand], sport_id=1) is None  # strict tier punts

    def test_resolve_tries_highest_weight_first(self):
        # First candidate (lower weight, ambiguous) should NOT win.
        # Second candidate (higher weight, unambiguous) should.
        t_unique = uuid.uuid4()
        ar = self._build([
            ("ambiguous", 1, uuid.uuid4()),
            ("ambiguous", 1, uuid.uuid4()),
            ("unambiguous", 1, t_unique),
        ])
        cands = [
            TeamCandidate(raw="x", normalized="ambiguous",   kind="kalshi_abbr", weight=0.6),
            TeamCandidate(raw="y", normalized="unambiguous", kind="name",         weight=0.9),
        ]
        # Higher-weight tried first; resolves cleanly.
        assert ar.resolve(cands, sport_id=1) == t_unique

    def test_resolve_sport_isolation(self):
        # Same alias_normalized in two different sports → different teams.
        t_soccer = uuid.uuid4()
        t_basket = uuid.uuid4()
        ar = self._build([
            ("real madrid", 1, t_soccer),  # sport_id 1 = soccer
            ("real madrid", 3, t_basket),  # sport_id 3 = basketball
        ])
        cand = TeamCandidate(raw="Real Madrid", normalized="real madrid", kind="name")
        assert ar.resolve([cand], sport_id=1) == t_soccer
        assert ar.resolve([cand], sport_id=3) == t_basket

    def test_stats(self):
        ar = self._build([
            ("a", 1, uuid.uuid4()),
            ("b", 1, uuid.uuid4()),
            ("c", 1, uuid.uuid4()),
            ("c", 1, uuid.uuid4()),  # makes c ambiguous
        ])
        stats = ar.stats()
        assert stats["unique_keys"] == 3
        assert stats["ambiguous_keys"] == 1
        assert stats["unique_teams_reachable"] == 4


# ── StrictMatcher gate logic (mocked DB) ─────────────────────────

def _signal(
    sport: str = "Soccer",
    home_norm: str = "bayern munich",
    away_norm: str = "psg",
    kickoff_at: datetime = None,
    kickoff_confidence: float = 1.0,
) -> FixtureSignal:
    return FixtureSignal(
        provider="test",
        provider_record_id="t1",
        sport=sport,
        home_team_candidates=[TeamCandidate(raw=home_norm, normalized=home_norm, kind="name")],
        away_team_candidates=[TeamCandidate(raw=away_norm, normalized=away_norm, kind="name")],
        kickoff_at=kickoff_at or datetime(2026, 5, 7, 19, tzinfo=timezone.utc),
        kickoff_confidence=kickoff_confidence,
    )


class TestStrictMatcherGates:

    def _matcher(self, alias_entries, sport_map=None):
        ar = AliasResolver()
        for alias, sport, tid in alias_entries:
            ar._index[(alias, sport)].add(tid)
        return StrictMatcher(
            aliases=ar,
            sport_id_by_code_or_name=sport_map or {"Soccer": 1, "soccer": 1},
        )

    @pytest.mark.asyncio
    async def test_gate1_fails_on_low_kickoff_confidence(self):
        m = self._matcher([])
        sig = _signal(kickoff_confidence=0.6)
        result = await m.match(MagicMock(), sig)
        assert result.reason_code == ReasonCode.NO_MATCH
        assert "kickoff_confidence" in result.reason_detail.get("fail_reason", "")

    @pytest.mark.asyncio
    async def test_gate1_fails_when_kickoff_is_none(self):
        m = self._matcher([])
        sig = _signal(kickoff_confidence=0.95)
        # Override kickoff_at to None — Pydantic allows it.
        sig = sig.model_copy(update={"kickoff_at": None})
        result = await m.match(MagicMock(), sig)
        assert result.reason_code == ReasonCode.NO_MATCH
        assert result.reason_detail["fail_reason"] == "kickoff_at_missing"

    @pytest.mark.asyncio
    async def test_gate2_fails_on_unknown_sport(self):
        m = self._matcher([], sport_map={"Soccer": 1})
        sig = _signal(sport="Cricket")  # not in map
        result = await m.match(MagicMock(), sig)
        assert result.reason_code == ReasonCode.NO_MATCH
        assert result.reason_detail["fail_reason"] == "sport_not_classified"

    @pytest.mark.asyncio
    async def test_gate2_passes_with_lowercase_code(self):
        # Signal has 'soccer' (lowercase code form) — should resolve.
        m = self._matcher([
            ("bayern munich", 1, uuid.uuid4()),
            ("psg",           1, uuid.uuid4()),
        ], sport_map={"soccer": 1, "Soccer": 1})
        sig = _signal(sport="soccer")
        # Mock session: find_fixture returns None (no existing); ensure_fixture creates.
        session = MagicMock()
        session.execute = AsyncMock()
        # First execute = find_fixture; returns None
        # Second execute = find_fixture swapped; returns None
        # Third execute = ensure_fixture INSERT; returns new id
        new_fixture_id = uuid.uuid4()
        results = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=new_fixture_id)),
        ]
        session.execute.side_effect = results
        result = await m.match(session, sig)
        assert result.reason_code == ReasonCode.STRICT
        assert result.fixture_id == new_fixture_id
        assert result.confidence == 0.98
        assert result.reason_detail["created_new_fixture"] is True

    @pytest.mark.asyncio
    async def test_gate3_fails_when_only_home_resolves(self):
        m = self._matcher([("bayern munich", 1, uuid.uuid4())])  # away missing
        sig = _signal()
        result = await m.match(MagicMock(), sig)
        assert result.reason_code == ReasonCode.NO_MATCH
        assert result.reason_detail["fail_reason"] == "alias_resolution_incomplete"
        assert result.reason_detail["home_resolved"] is True
        assert result.reason_detail["away_resolved"] is False

    @pytest.mark.asyncio
    async def test_gate3_fails_when_home_and_away_resolve_to_same_team(self):
        team = uuid.uuid4()
        m = self._matcher([
            ("bayern munich", 1, team),
            ("psg",           1, team),  # bug or weird data
        ])
        sig = _signal()
        result = await m.match(MagicMock(), sig)
        assert result.reason_code == ReasonCode.NO_MATCH
        assert result.reason_detail["fail_reason"] == "home_and_away_same_team"

    @pytest.mark.asyncio
    async def test_full_match_existing_fixture(self):
        home, away = uuid.uuid4(), uuid.uuid4()
        existing_fixture = uuid.uuid4()
        m = self._matcher([
            ("bayern munich", 1, home),
            ("psg",           1, away),
        ])
        sig = _signal()
        session = MagicMock()
        # find_fixture in correct orientation returns existing.
        result_obj = MagicMock(scalar_one_or_none=MagicMock(return_value=existing_fixture))
        session.execute = AsyncMock(return_value=result_obj)
        result = await m.match(session, sig)
        assert result.reason_code == ReasonCode.STRICT
        assert result.fixture_id == existing_fixture
        # Did NOT take the create-new path.
        assert "created_new_fixture" not in result.reason_detail
        assert "orientation_flipped" not in result.reason_detail

    @pytest.mark.asyncio
    async def test_orientation_flip_records_in_reason_detail(self):
        home, away = uuid.uuid4(), uuid.uuid4()
        existing_fixture = uuid.uuid4()
        m = self._matcher([
            ("bayern munich", 1, home),
            ("psg",           1, away),
        ])
        sig = _signal()
        session = MagicMock()
        # First execute: find_fixture (home, away) returns None.
        # Second execute: find_fixture (away, home) returns existing.
        results = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=existing_fixture)),
        ]
        session.execute = AsyncMock(side_effect=results)
        result = await m.match(session, sig)
        assert result.reason_code == ReasonCode.STRICT
        assert result.fixture_id == existing_fixture
        assert result.reason_detail["orientation_flipped"] is True


# ── Runner CLI ───────────────────────────────────────────────────

class TestRunnerCli:
    def test_help_works(self):
        r = subprocess.run(
            [sys.executable, "scripts/run_resolver_pass.py", "--help"],
            capture_output=True, text=True,
        )
        assert r.returncode == 0
        assert "provider" in r.stdout.lower()
        assert "--run-mode" in r.stdout

    def test_missing_database_url_exits_2(self):
        env = {**os.environ, "DATABASE_URL": ""}
        r = subprocess.run(
            [sys.executable, "scripts/run_resolver_pass.py", "--provider", "kalshi"],
            capture_output=True, text=True, env=env,
        )
        assert r.returncode == 2
        assert "DATABASE_URL not set" in r.stderr

    def test_run_mode_live_rejected(self):
        # 'live' is reserved for Phase 2E; runner should reject it.
        env = {**os.environ, "DATABASE_URL": "postgresql://x/y"}
        r = subprocess.run(
            [sys.executable, "scripts/run_resolver_pass.py",
             "--provider", "kalshi", "--run-mode", "live"],
            capture_output=True, text=True, env=env,
        )
        # argparse rejects 'live' because it's not in choices.
        # Either argparse exit 2 with "invalid choice", or our script
        # raises explicitly. Either way: non-zero exit.
        assert r.returncode != 0


# ── Static guards: atomic transaction shape, run_mode hygiene ──

class TestStaticInvariants:
    def setup_method(self):
        import inspect
        import scripts.run_resolver_pass
        self.src = inspect.getsource(scripts.run_resolver_pass)

    def test_chunked_transactions(self):
        """The runner must process records in chunks, each within
        its own session.begin() block — per the leak-fix discipline."""
        assert "CHUNK_SIZE" in self.src
        assert "for chunk_start in range" in self.src
        assert "async with session.begin():" in self.src

    def test_atomic_link_and_log(self):
        """Per design doc §1: UPDATE provider table fixture_id AND
        INSERT resolution_log row in the SAME transaction. Static
        check that both writes are inside the session.begin() block."""
        # Find the inner session.begin() block.
        begin_idx = self.src.find("async with session.begin():")
        assert begin_idx > 0
        # Look for both writes in the next ~3000 chars (the chunk loop body).
        body = self.src[begin_idx:begin_idx + 5000]
        assert "UPDATE sp." in body, "fixture_id link UPDATE missing in atomic block"
        assert "session.add(ResolutionLog" in body, \
            "resolution_log INSERT missing in atomic block"

    def test_metrics_written_at_end(self):
        """A sp.resolver_runs row must be emitted at the end of the run."""
        assert "session.add(ResolverRun(" in self.src
        # Must include run_mode (per design doc — distinguishes
        # parallel-run from live-runner data).
        assert "run_mode=run_mode" in self.src


# ── Integration test stub (gated on SP_INTEGRATION_DB) ───────────

pytestmark_integration = pytest.mark.skipif(
    not INTEGRATION_DB,
    reason="SP_INTEGRATION_DB not set — integration tests require a "
           "Postgres URL with the sp schema migration applied.",
)


@pytestmark_integration
class TestResolverIntegration:
    @pytest.mark.asyncio
    async def test_placeholder_documents_e2e_shape(self):
        """Stub. When implemented:
          1. Seed sp.sports + sp.teams + sp.team_aliases with known data.
          2. Insert a few sp.kalshi_markets rows with crafted raw_payload.
          3. Run scripts/run_resolver_pass.py --provider kalshi --limit 5.
          4. Assert sp.resolution_log has the expected entries.
          5. Assert sp.kalshi_markets.fixture_id is set on the matched rows.
          6. Assert sp.resolver_runs row is written with run_mode='standalone'.
          7. Re-run; assert second run is a no-op (records_scanned=0).
        """
        assert INTEGRATION_DB
