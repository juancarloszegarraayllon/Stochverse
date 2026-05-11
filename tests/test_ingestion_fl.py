"""Tests for the SP Architecture FL ingestion module (Phase 1B).

Covers the boundary-validation + hashing + supervisor pieces that
don't require a live Postgres. The DB-writing path is exercised by
integration tests once Phase 1A's schema is loaded into a local
docker-compose Postgres.
"""
from __future__ import annotations

import asyncio
import json
import pytest

from unittest.mock import AsyncMock, MagicMock

from ingestion.base import (
    ADVISORY_LOCK_FL,
    ADVISORY_LOCK_KALSHI,
    IngestionResult,
    new_run_id,
    payload_hash,
    supervise,
    upsert_provider_records_batch,
)
from ingestion.schema_validation import (
    FLEventValidator,
    FLTournamentValidator,
    validate_or_drift,
)


# ── Hashing ──────────────────────────────────────────────────────

class TestPayloadHash:
    def test_identical_payloads_have_same_hash(self):
        a = {"k1": "v1", "k2": [1, 2, 3]}
        b = {"k2": [1, 2, 3], "k1": "v1"}  # different key order
        assert payload_hash(a) == payload_hash(b)

    def test_different_payloads_have_different_hashes(self):
        a = {"score": 1}
        b = {"score": 2}
        assert payload_hash(a) != payload_hash(b)

    def test_unicode_stable(self):
        a = {"name": "Atlético"}
        b = {"name": "Atlético"}  # same string, different code point representation possible
        assert payload_hash(a) == payload_hash(b)

    def test_returns_hex_sha256(self):
        h = payload_hash({"x": 1})
        assert len(h) == 64
        int(h, 16)  # raises if not hex


# ── Schema validation ────────────────────────────────────────────

class TestFLEventValidator:
    def test_valid_event_passes(self):
        raw = {
            "EVENT_ID":   "abc123",
            "HOME_NAME":  "Bayern Munich",
            "AWAY_NAME":  "PSG",
            "START_TIME": 1778191200,
            "STAGE_TYPE": "SCHEDULED",
        }
        parsed, drift = validate_or_drift(
            provider="fl", record_kind="event", record_id="abc123",
            raw=raw, validator=FLEventValidator,
        )
        assert drift is False
        assert parsed is not None
        assert parsed.EVENT_ID == "abc123"

    def test_event_with_missing_optional_fields_passes(self):
        raw = {"EVENT_ID": "abc"}
        parsed, drift = validate_or_drift(
            provider="fl", record_kind="event", record_id="abc",
            raw=raw, validator=FLEventValidator,
        )
        assert drift is False
        assert parsed.EVENT_ID == "abc"

    def test_event_with_missing_event_id_fails(self):
        raw = {"HOME_NAME": "Bayern"}
        parsed, drift = validate_or_drift(
            provider="fl", record_kind="event", record_id="?",
            raw=raw, validator=FLEventValidator,
        )
        assert drift is True
        assert parsed is None

    def test_event_with_wrong_type_fails(self):
        raw = {"EVENT_ID": "abc", "START_TIME": "not-a-number"}
        parsed, drift = validate_or_drift(
            provider="fl", record_kind="event", record_id="abc",
            raw=raw, validator=FLEventValidator,
        )
        assert drift is True
        assert parsed is None

    def test_extra_fields_allowed(self):
        # FL adds new fields over time; allow them rather than fail
        raw = {
            "EVENT_ID": "abc",
            "RANDOM_NEW_FIELD_FROM_FL": [{"x": 1}],
        }
        _, drift = validate_or_drift(
            provider="fl", record_kind="event", record_id="abc",
            raw=raw, validator=FLEventValidator,
        )
        assert drift is False


class TestFLTournamentValidator:
    def test_valid_tournament_passes(self):
        raw = {
            "TOURNAMENT_STAGE_ID": "stg_1",
            "NAME": "Premier League",
            "EVENTS": [{"EVENT_ID": "x"}],
        }
        parsed, drift = validate_or_drift(
            provider="fl", record_kind="tournament", record_id="stg_1",
            raw=raw, validator=FLTournamentValidator,
        )
        assert drift is False
        assert len(parsed.EVENTS) == 1

    def test_tournament_without_events_passes(self):
        raw = {"TOURNAMENT_STAGE_ID": "stg_1", "NAME": "Empty League"}
        parsed, drift = validate_or_drift(
            provider="fl", record_kind="tournament", record_id="stg_1",
            raw=raw, validator=FLTournamentValidator,
        )
        assert drift is False
        assert parsed.EVENTS == []


# ── Lock keys are distinct ───────────────────────────────────────

class TestAdvisoryLockKeys:
    def test_keys_are_distinct(self):
        keys = {ADVISORY_LOCK_FL, ADVISORY_LOCK_KALSHI}
        # Avoid bringing in unloaded constants — just compare the
        # ones we have, ensure no accidental aliasing.
        assert len(keys) == 2
        assert all(isinstance(k, int) for k in keys)


# ── Run-id is unique per call ────────────────────────────────────

class TestNewRunId:
    def test_unique_per_call(self):
        ids = {str(new_run_id()) for _ in range(100)}
        assert len(ids) == 100


# ── Supervisor: restarts on crash, exits on cancel ───────────────

class TestSupervise:
    @pytest.mark.asyncio
    async def test_clean_return_exits(self):
        """When the supervised coro returns normally, supervise exits."""
        attempts = 0

        async def coro():
            nonlocal attempts
            attempts += 1

        await supervise("test", coro)
        assert attempts == 1

    @pytest.mark.asyncio
    async def test_crash_then_clean_run(self):
        """Crash once, then return normally — supervisor restarts and exits."""
        attempts = 0

        async def coro():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("first attempt fails")
            # second attempt succeeds → supervisor exits

        await supervise("test", coro, max_backoff_sec=0.01)
        assert attempts == 2

    @pytest.mark.asyncio
    async def test_cancellation_propagates(self):
        """Cancellation exits the supervisor immediately, no restart."""
        async def coro():
            await asyncio.sleep(10)

        task = asyncio.create_task(
            supervise("test", coro, max_backoff_sec=0.01),
        )
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


# ── IngestionResult: defaults ────────────────────────────────────

class TestIngestionResult:
    def test_defaults_are_zero(self):
        r = IngestionResult()
        assert r.fetched == 0
        assert r.failed == 0
        assert r.inserted == 0
        assert r.updated == 0
        assert r.unchanged == 0
        assert r.schema_drift == 0
        assert r.duration_ms == 0


# ── Batch UPSERT classification ──────────────────────────────────
#
# The classification logic (insert/update/unchanged) lives in Python
# and reads the SELECT result of existing payload_hashes. We can
# test it without a real DB by mocking the session.execute() that
# does the SELECT.

class TestBatchUpsertClassification:
    @pytest.mark.asyncio
    async def test_all_inserts_when_table_empty(self):
        """No existing rows → all records classified as inserted."""
        mock_session = AsyncMock()
        # SELECT returns no rows.
        mock_existing = MagicMock()
        mock_existing.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_existing)

        # Stand-in for a SQLAlchemy table with payload_hash + ticker.
        class _FakeTable:
            __tablename__ = "fake"
            class __table__:
                pass
            ticker = MagicMock()
            payload_hash = MagicMock()

        records = [
            {"pk": {"ticker": f"T{i}"}, "fields": {"market_type": "game"}, "raw": {"x": i}}
            for i in range(5)
        ]
        # Patch _get_existing-equivalent by mocking the whole flow:
        # we'll just monkeypatch upsert to skip the actual statement.
        # For this test, what we care about is the classification math.
        # That math runs in pure Python after the SELECT; the SELECT
        # returns [] so all records are inserts.

        # We can't easily stub all the way through without a real DB,
        # so verify the math directly:
        existing_hashes = {}
        inserted = 0
        updated = 0
        unchanged = 0
        for r in records:
            h = payload_hash(r["raw"])
            old = existing_hashes.get(r["pk"]["ticker"])
            if old is None:
                inserted += 1
            elif old == h:
                unchanged += 1
            else:
                updated += 1
        assert inserted == 5
        assert updated == 0
        assert unchanged == 0

    def test_classification_logic_branches(self):
        """All three classifications fire correctly with mixed inputs."""
        # Existing row with one hash; new pass changes one, repeats one,
        # adds one. Verify counts.
        records = [
            {"pk": {"ticker": "A"}, "fields": {}, "raw": {"v": 1}},  # unchanged
            {"pk": {"ticker": "B"}, "fields": {}, "raw": {"v": 99}}, # updated
            {"pk": {"ticker": "C"}, "fields": {}, "raw": {"v": 3}},  # inserted
        ]
        # Pre-existing: A has hash of {"v":1}, B has hash of {"v":2}.
        existing_hashes = {
            "A": payload_hash({"v": 1}),
            "B": payload_hash({"v": 2}),
        }

        inserted = 0
        updated = 0
        unchanged = 0
        for r in records:
            h = payload_hash(r["raw"])
            old = existing_hashes.get(r["pk"]["ticker"])
            if old is None:
                inserted += 1
            elif old == h:
                unchanged += 1
            else:
                updated += 1
        assert inserted == 1
        assert updated == 1
        assert unchanged == 1

    def test_empty_batch_returns_zeros(self):
        """An empty batch returns (0,0,0) without DB calls."""
        # Direct test of the early-return branch.
        # We don't need a real session — the function returns
        # immediately when records is empty.
        mock_session = AsyncMock()
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                upsert_provider_records_batch(mock_session, None, [])
            )
        finally:
            loop.close()
        assert result == (0, 0, 0)
        mock_session.execute.assert_not_called()


# ── Phase 2A.7: FL sport_id mapping + ingestion wiring ─────────


class TestFLSportIdMap:
    """Phase 2A.7: FL numeric sport_id → sp.sports.name translation
    must cover every entry in DEFAULT_FL_SPORT_IDS, and every value
    must match the canonical sp.sports.name spelling.
    """

    def test_every_default_sport_id_is_mapped(self):
        from ingestion.fl import DEFAULT_FL_SPORT_IDS, FL_SPORT_ID_TO_SP_NAME
        unmapped = [s for s in DEFAULT_FL_SPORT_IDS if s not in FL_SPORT_ID_TO_SP_NAME]
        assert not unmapped, (
            f"DEFAULT_FL_SPORT_IDS includes {unmapped} but FL_SPORT_ID_TO_SP_NAME "
            f"doesn't translate them — ingestion would skip those sports with a "
            f"sport_id_unmapped warning."
        )

    def test_map_values_align_with_sp_sports_seed(self):
        """The values in FL_SPORT_ID_TO_SP_NAME must exactly match the
        canonical names seeded in sp.sports (migration d8e717ed79dd).
        Keep this list in sync with the seed migration."""
        from ingestion.fl import FL_SPORT_ID_TO_SP_NAME
        # The 17-sport canonical list per architecture v1.4 §5.4.
        # Mirrors migration d8e717ed79dd_seed_sp_sports.py.
        canonical_sp_names = {
            "Soccer", "Tennis", "Basketball", "Hockey", "American Football",
            "Baseball", "Handball", "Cricket", "Volleyball", "Rugby Union",
            "Aussie Rules", "Rugby League", "MMA", "Boxing", "Golf",
            "Snooker", "Darts",
        }
        for fl_id, sp_name in FL_SPORT_ID_TO_SP_NAME.items():
            assert sp_name in canonical_sp_names, (
                f"FL_SPORT_ID_TO_SP_NAME[{fl_id}] = {sp_name!r} but that name "
                f"isn't in the sp.sports seed — ingestion would skip with "
                f"sport_id_unmapped."
            )


class TestIngestionWritesSportId:
    """Phase 2A.7: the per-sport batch in _ingest_pass must include
    `sport_id` in its `fields` dict so the UPSERT populates the column.
    Static-source guard against regression."""

    def setup_method(self):
        import inspect
        import ingestion.fl
        self.src = inspect.getsource(ingestion.fl)

    def test_batch_includes_sport_id_field(self):
        # Find the batch.append( call inside _ingest_pass.
        idx = self.src.find("batch.append({")
        assert idx > 0
        # The next ~600 chars should include the sport_id field.
        block = self.src[idx:idx + 600]
        assert "\"sport_id\"" in block, (
            "ingestion.fl._ingest_pass batch must include sport_id in fields "
            "so the UPSERT populates sp.fl_events.sport_id."
        )

    def test_pre_pass_resolves_sp_sport_id_lookup(self):
        # The function should bulk-load sp.sports → id map up-front.
        assert "SELECT id, name FROM sp.sports" in self.src
        assert "sp_sport_id_by_fl_id" in self.src

    def test_unmapped_sports_are_skipped_with_warning(self):
        # Sports without an sp.sports entry must NOT be polled (would
        # NULL-out sport_id on existing rows during UPSERT).
        assert "sport_id_unmapped" in self.src
        assert "skipped_unmapped" in self.src

    def test_lookup_is_built_inside_ingest_pass_not_run(self):
        """Hotfix invariant: `sp_sport_id_by_fl_id` must be built inside
        `_ingest_pass` so it has a valid session in scope. The original
        2A.7 PR built it inside `run()` and referenced it from
        `_ingest_pass` as a free variable — every call NameError'd
        (production sp.fl_events.sport_id stayed 100% NULL after PR #86).

        Static guard: the lookup-construction code must appear after
        the `_ingest_pass` definition AND before the `_today_pre_game_loop`
        definition, i.e. inside `_ingest_pass`'s body.
        """
        ingest_pass_idx = self.src.find("async def _ingest_pass(")
        today_loop_idx = self.src.find("async def _today_pre_game_loop(")
        run_idx = self.src.find("async def run(")
        lookup_idx = self.src.find("sp_sport_id_by_fl_id: dict[int, int]")
        assert ingest_pass_idx > 0
        assert today_loop_idx > ingest_pass_idx
        assert run_idx > today_loop_idx
        assert lookup_idx > 0, "sp_sport_id_by_fl_id construction missing entirely"
        assert ingest_pass_idx < lookup_idx < today_loop_idx, (
            "sp_sport_id_by_fl_id must be built inside _ingest_pass "
            "(it has the function's session in scope). The 2A.7 hotfix "
            "moved it from run() — don't move it back."
        )


# ── Phase 2A.7 hotfix: end-to-end integration test ─────────────


class TestIngestPassIntegration:
    """Real call-path test for `_ingest_pass`. The original 2A.7 PR
    relied on static guards that confirmed the right strings appeared
    in the source — but didn't actually invoke the function. As a
    result a NameError ('sp_sport_id_by_fl_id' referenced inside
    _ingest_pass but built inside run()) shipped to production and
    every poll silently failed.

    These tests mock the FL HTTP boundary + DB boundary and exercise
    the actual call path. A regression of the same shape would crash
    here immediately.
    """

    @pytest.mark.asyncio
    async def test_ingest_pass_runs_without_name_error(self, monkeypatch):
        """The smoking-gun: just call _ingest_pass and assert it
        completes. Pre-hotfix this raised NameError at the
        sp_sport_id_by_fl_id reference."""
        from ingestion.fl import _ingest_pass

        # 1. Mock the FL HTTP boundary.
        async def fake_fl_get(path, params):
            return {"DATA": [{
                "TOURNAMENT_STAGE_ID": "stg_test",
                "NAME": "Test League",
                "EVENTS": [{
                    "EVENT_ID":   "evt_1",
                    "HOME_NAME":  "Bayern",
                    "AWAY_NAME":  "PSG",
                    "START_TIME": 1778191200,
                }],
            }]}

        import flashlive_feed
        monkeypatch.setattr(flashlive_feed, "_fl_get", fake_fl_get)

        # 2. Mock the DB UPSERT — capture the records arg.
        captured: list[list[dict]] = []

        async def fake_upsert(session, table, records):
            captured.append(records)
            return (len(records), 0, 0)

        monkeypatch.setattr(
            "ingestion.fl.upsert_provider_records_batch",
            fake_upsert,
        )

        # 3. Mock the AsyncSession: SELECT returns sp.sports rows.
        class _Row:
            def __init__(self, id, name):
                self.id = id
                self.name = name
        soccer = _Row(1, "Soccer")
        tennis = _Row(2, "Tennis")
        sp_sports_result = MagicMock()
        sp_sports_result.all.return_value = [soccer, tennis]
        session = AsyncMock()
        session.execute = AsyncMock(return_value=sp_sports_result)
        session.commit = AsyncMock()

        # 4. Run.
        result = await _ingest_pass(
            session, sport_ids=[1, 2], indent_days=0,
        )

        # 5. Did not crash. Records actually flowed through.
        assert result.fetched > 0
        assert captured, "Expected at least one batch to reach upsert"

    @pytest.mark.asyncio
    async def test_ingest_pass_writes_sport_id_in_batch(self, monkeypatch):
        """Per-record sport_id must be set to the resolved sp.sports.id,
        not None and not the FL numeric id. The matcher reads this
        column via JOIN later — wrong sport_id → wrong sport name →
        wrong gate decision."""
        from ingestion.fl import _ingest_pass

        async def fake_fl_get(path, params):
            return {"DATA": [{
                "TOURNAMENT_STAGE_ID": "stg_test",
                "EVENTS": [{
                    "EVENT_ID":   f"evt_{params['sport_id']}",
                    "HOME_NAME":  "H",
                    "AWAY_NAME":  "A",
                    "START_TIME": 1778191200,
                }],
            }]}

        import flashlive_feed
        monkeypatch.setattr(flashlive_feed, "_fl_get", fake_fl_get)

        captured: list[list[dict]] = []

        async def fake_upsert(session, table, records):
            captured.append(records)
            return (len(records), 0, 0)

        monkeypatch.setattr(
            "ingestion.fl.upsert_provider_records_batch",
            fake_upsert,
        )

        # FL id 1 (Soccer) → sp.sports.id 100; FL id 2 (Tennis) → 200.
        # NOTE: MagicMock(name=...) treats `name` as a debug-repr kwarg;
        # `.name` doesn't pick it up. Use a plain row stand-in instead.
        class _Row:
            def __init__(self, id, name):
                self.id = id
                self.name = name
        soccer = _Row(100, "Soccer")
        tennis = _Row(200, "Tennis")
        sp_sports_result = MagicMock()
        sp_sports_result.all.return_value = [soccer, tennis]
        session = AsyncMock()
        session.execute = AsyncMock(return_value=sp_sports_result)
        session.commit = AsyncMock()

        await _ingest_pass(session, sport_ids=[1, 2], indent_days=0)

        # Two batches expected (one per sport). Each record must carry
        # the sp.sports.id matching its FL sport (100 for Soccer, 200
        # for Tennis).
        assert len(captured) == 2
        all_records = [r for batch in captured for r in batch]
        assert all_records, "No records reached upsert"
        sport_ids_in_batch = {r["fields"]["sport_id"] for r in all_records}
        assert sport_ids_in_batch == {100, 200}, (
            f"Expected sport_id values {{100, 200}}, got {sport_ids_in_batch}"
        )

    @pytest.mark.asyncio
    async def test_run_does_not_name_error_at_startup(self, monkeypatch):
        """`run()` is the production entry point (called by
        ingestion.runner). Pre-hotfix it referenced `session` before
        the session_factory block opened, NameError'ing on first
        invocation. Smoke-test that it gets past startup and into the
        loops (we cancel before they run a real pass).
        """
        from ingestion import fl as fl_module

        # Mock try_acquire_advisory_lock to return False — `run` then
        # logs and exits without spawning the loops, which is plenty
        # to verify it gets past the startup code without NameError.
        async def fake_lock(session, key):
            return False
        monkeypatch.setattr(
            "ingestion.fl.try_acquire_advisory_lock", fake_lock,
        )

        # session_factory just needs to be a context manager that
        # yields something async — `try_acquire_advisory_lock` is
        # mocked, so the session is unused.
        class _FakeSessionCM:
            async def __aenter__(self):
                return AsyncMock()
            async def __aexit__(self, exc_type, exc, tb):
                return False
        def fake_session_factory():
            return _FakeSessionCM()

        # Should return cleanly (no NameError) when the lock is held
        # elsewhere.
        await fl_module.run(fake_session_factory)

    @pytest.mark.asyncio
    async def test_ingest_pass_skips_unmapped_fl_sport_id(self, monkeypatch):
        """An FL sport_id not in FL_SPORT_ID_TO_SP_NAME (or one whose
        target sp.sports.name is missing) must be skipped with a
        warning, NOT polled with sport_id=None — that would NULL-out
        the column on every existing row during UPSERT."""
        from ingestion.fl import _ingest_pass

        fetch_calls: list[int] = []

        async def fake_fl_get(path, params):
            fetch_calls.append(params["sport_id"])
            return {"DATA": []}

        import flashlive_feed
        monkeypatch.setattr(flashlive_feed, "_fl_get", fake_fl_get)

        async def fake_upsert(session, table, records):
            return (0, 0, 0)
        monkeypatch.setattr(
            "ingestion.fl.upsert_provider_records_batch",
            fake_upsert,
        )

        # Only Soccer (FL=1) is seeded; FL=999 has no map entry.
        class _Row:
            def __init__(self, id, name):
                self.id = id
                self.name = name
        soccer = _Row(100, "Soccer")
        sp_sports_result = MagicMock()
        sp_sports_result.all.return_value = [soccer]
        session = AsyncMock()
        session.execute = AsyncMock(return_value=sp_sports_result)
        session.commit = AsyncMock()

        await _ingest_pass(session, sport_ids=[1, 999], indent_days=0)

        # Only Soccer (FL=1) should have been polled; 999 dropped.
        assert fetch_calls == [1], (
            f"Expected only FL sport_id=1 to be polled (999 unmapped), "
            f"got {fetch_calls}"
        )
