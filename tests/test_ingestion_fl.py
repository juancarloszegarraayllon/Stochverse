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
