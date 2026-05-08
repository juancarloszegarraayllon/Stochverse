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
    AliasResolver, CompetitionResolver, FixtureSignal, MatchResult,
    ReasonCode, StrictMatcher, TeamCandidate,
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


# ── CompetitionResolver ──────────────────────────────────────────

class TestCompetitionResolver:
    def _build(self, *, kalshi: dict[str, uuid.UUID] | None = None,
               fl: dict[str, uuid.UUID] | None = None) -> CompetitionResolver:
        cr = CompetitionResolver()
        cr._kalshi_base_index = dict(kalshi or {})
        cr._fl_stage_index = dict(fl or {})
        return cr

    def test_resolve_kalshi_explicit_by_base(self):
        cid = uuid.uuid4()
        cr = self._build(kalshi={"KXEPL": cid})
        out_id, kind = cr.resolve("kalshi", "KXEPL")
        assert (out_id, kind) == (cid, "explicit")

    def test_resolve_kalshi_explicit_strips_suffix(self):
        # Hint comes in as a full series_ticker; strip_known_suffix
        # gets us to KXEPL → resolves.
        cid = uuid.uuid4()
        cr = self._build(kalshi={"KXEPL": cid})
        out_id, kind = cr.resolve("kalshi", "KXEPLGAME")
        assert (out_id, kind) == (cid, "explicit")

    def test_resolve_kalshi_no_hint(self):
        cr = self._build(kalshi={"KXEPL": uuid.uuid4()})
        for hint in (None, "", "   "):
            out_id, kind = cr.resolve("kalshi", hint)
            assert out_id is None
            assert kind == "no_hint"

    def test_resolve_kalshi_unresolvable(self):
        cr = self._build(kalshi={"KXEPL": uuid.uuid4()})
        out_id, kind = cr.resolve("kalshi", "KXNOSUCHGAME")
        assert out_id is None
        assert kind == "unresolvable"

    def test_resolve_fl_explicit(self):
        cid = uuid.uuid4()
        cr = self._build(fl={"123": cid})
        out_id, kind = cr.resolve("fl", "123")
        assert (out_id, kind) == (cid, "explicit")

    def test_resolve_fl_unresolvable(self):
        cr = self._build(fl={"123": uuid.uuid4()})
        out_id, kind = cr.resolve("fl", "999")
        assert out_id is None
        assert kind == "unresolvable"

    def test_resolve_unknown_provider_is_no_hint(self):
        cr = self._build(kalshi={"KXEPL": uuid.uuid4()})
        out_id, kind = cr.resolve("polymarket", "anything")
        assert out_id is None
        assert kind == "no_hint"

    def test_stats(self):
        c1, c2 = uuid.uuid4(), uuid.uuid4()
        cr = self._build(
            kalshi={"KXEPL": c1, "KXUCL": c2},
            fl={"abc": c1},
        )
        s = cr.stats()
        assert s["kalshi_bases_indexed"] == 2
        assert s["fl_stage_ids_indexed"] == 1
        assert s["unique_competitions"] == 2


# ── Matcher competition gate (Phase 2A.6) ────────────────────────

class TestMatcherCompetitionGate:

    def _matcher_with_competitions(
        self,
        *,
        alias_entries,
        kalshi_index: dict[str, uuid.UUID] | None = None,
        sport_map=None,
    ) -> StrictMatcher:
        ar = AliasResolver()
        for alias, sport, tid in alias_entries:
            ar._index[(alias, sport)].add(tid)
        cr = CompetitionResolver()
        cr._kalshi_base_index = dict(kalshi_index or {})
        return StrictMatcher(
            aliases=ar,
            sport_id_by_code_or_name=sport_map or {"Soccer": 1, "soccer": 1},
            competitions=cr,
        )

    def _signal_kalshi(self, *, hint: str | None) -> FixtureSignal:
        return FixtureSignal(
            provider="kalshi",
            provider_record_id="KX-EVT-1",
            sport="Soccer",
            home_team_candidates=[TeamCandidate(
                raw="Bayern", normalized="bayern munich", kind="name")],
            away_team_candidates=[TeamCandidate(
                raw="PSG", normalized="psg", kind="name")],
            kickoff_at=datetime(2026, 5, 7, 19, tzinfo=timezone.utc),
            kickoff_confidence=1.0,
            competition_hint=hint,
        )

    def _signal_fl(self, *, hint: str | None = None) -> FixtureSignal:
        return FixtureSignal(
            provider="fl",
            provider_record_id="fl-1",
            sport="Soccer",
            home_team_candidates=[TeamCandidate(
                raw="Bayern", normalized="bayern munich", kind="name")],
            away_team_candidates=[TeamCandidate(
                raw="PSG", normalized="psg", kind="name")],
            kickoff_at=datetime(2026, 5, 7, 19, tzinfo=timezone.utc),
            kickoff_confidence=1.0,
            competition_hint=hint,
        )

    @pytest.mark.asyncio
    async def test_kalshi_explicit_hint_passes_gate_and_filters_fixture(self):
        comp_id = uuid.uuid4()
        home, away = uuid.uuid4(), uuid.uuid4()
        m = self._matcher_with_competitions(
            alias_entries=[
                ("bayern munich", 1, home),
                ("psg",           1, away),
            ],
            kalshi_index={"KXUCL": comp_id},
        )
        sig = self._signal_kalshi(hint="KXUCLGAME")
        existing_fixture = uuid.uuid4()
        session = MagicMock()
        session.execute = AsyncMock(return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=existing_fixture)
        ))
        result = await m.match(session, sig)
        assert result.reason_code == ReasonCode.STRICT
        assert result.fixture_id == existing_fixture
        assert result.reason_detail["competition_resolution"] == "explicit"
        assert result.reason_detail["competition_id"] == str(comp_id)
        # FL flag never set for Kalshi.
        assert "fl_transitional_sport_only" not in result.reason_detail

    @pytest.mark.asyncio
    async def test_kalshi_unresolvable_hint_fails_strict(self):
        m = self._matcher_with_competitions(
            alias_entries=[
                ("bayern munich", 1, uuid.uuid4()),
                ("psg",           1, uuid.uuid4()),
            ],
            kalshi_index={"KXEPL": uuid.uuid4()},  # Champions League not seeded
        )
        sig = self._signal_kalshi(hint="KXUCLGAME")
        result = await m.match(MagicMock(), sig)
        assert result.reason_code == ReasonCode.NO_MATCH
        assert result.reason_detail["fail_reason"] == "kalshi_competition_unresolvable"
        assert result.reason_detail["competition_resolution"] == "unresolvable"

    @pytest.mark.asyncio
    async def test_kalshi_no_hint_falls_back_to_sport_only(self):
        home, away = uuid.uuid4(), uuid.uuid4()
        m = self._matcher_with_competitions(
            alias_entries=[
                ("bayern munich", 1, home),
                ("psg",           1, away),
            ],
            kalshi_index={"KXEPL": uuid.uuid4()},
        )
        sig = self._signal_kalshi(hint=None)
        existing_fixture = uuid.uuid4()
        session = MagicMock()
        session.execute = AsyncMock(return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=existing_fixture)
        ))
        result = await m.match(session, sig)
        assert result.reason_code == ReasonCode.STRICT
        assert result.reason_detail["kalshi_no_hint_sport_only"] is True
        assert result.reason_detail["competition_resolution"] == "no_hint"

    @pytest.mark.asyncio
    async def test_fl_always_logs_transitional_sport_only_on_success(self):
        home, away = uuid.uuid4(), uuid.uuid4()
        m = self._matcher_with_competitions(
            alias_entries=[
                ("bayern munich", 1, home),
                ("psg",           1, away),
            ],
            kalshi_index={"KXEPL": uuid.uuid4()},
        )
        sig = self._signal_fl(hint="some-stage-id")  # would be unresolvable for kalshi
        existing_fixture = uuid.uuid4()
        session = MagicMock()
        session.execute = AsyncMock(return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=existing_fixture)
        ))
        result = await m.match(session, sig)
        assert result.reason_code == ReasonCode.STRICT
        assert result.reason_detail["fl_transitional_sport_only"] is True
        # FL bypasses competition resolution entirely in 2A.6.
        assert "competition_resolution" not in result.reason_detail
        assert "competition_id" not in result.reason_detail

    @pytest.mark.asyncio
    async def test_matcher_without_competitions_index_degrades_gracefully(self):
        """Pre-2A.6 unit tests construct StrictMatcher without a
        CompetitionResolver. That path must keep working — Kalshi
        with a hint should still match (sport-only) and log the
        misconfiguration flag."""
        home, away = uuid.uuid4(), uuid.uuid4()
        ar = AliasResolver()
        ar._index[("bayern munich", 1)].add(home)
        ar._index[("psg", 1)].add(away)
        m = StrictMatcher(
            aliases=ar,
            sport_id_by_code_or_name={"Soccer": 1, "soccer": 1},
            competitions=None,
        )
        sig = self._signal_kalshi(hint="KXUCLGAME")
        existing_fixture = uuid.uuid4()
        session = MagicMock()
        session.execute = AsyncMock(return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=existing_fixture)
        ))
        result = await m.match(session, sig)
        assert result.reason_code == ReasonCode.STRICT
        assert result.reason_detail["competitions_index_unavailable"] is True


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
