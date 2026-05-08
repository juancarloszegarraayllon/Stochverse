"""Tests for the transaction-leak fix in db.py.

Phase: hotfix for production transaction leak (2026-05-08).

Scope of these tests:
  - Verify the chunking math in upsert_entities + sync_events_to_db
    produces the expected number of chunks.
  - Verify per-chunk failure isolation: one bad chunk doesn't abort
    later chunks.
  - Verify engine.dispose() is on the finally path in
    sync_events_to_db (covered by static inspection — DB-level test
    requires Postgres).
  - Verify _connect_args carries the server_settings timeouts.

DB-roundtrip tests are gated behind SP_INTEGRATION_DB env var.
Without it, these tests verify only the wiring + chunking logic.
"""
from __future__ import annotations

import asyncio
import importlib
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def db_module():
    """Reload db module so server_settings are evaluated against
    whatever DATABASE_URL is in the test env."""
    import db
    importlib.reload(db)
    return db


# ── Server-side timeouts wired into connect_args ────────────────

class TestServerSettings:
    def test_idle_in_transaction_timeout_set(self, db_module):
        ss = db_module._connect_args.get("server_settings", {})
        assert ss.get("idle_in_transaction_session_timeout") == "60000"

    def test_statement_timeout_set(self, db_module):
        ss = db_module._connect_args.get("server_settings", {})
        assert ss.get("statement_timeout") == "60000"

    def test_lock_timeout_set(self, db_module):
        ss = db_module._connect_args.get("server_settings", {})
        assert ss.get("lock_timeout") == "30000"

    def test_application_name_set(self, db_module):
        ss = db_module._connect_args.get("server_settings", {})
        assert ss.get("application_name") == "stochverse-web"


# ── Chunking math: number of chunks for N teams ─────────────────

class TestChunkingMath:
    """Black-box verification: feed the function N teams, count how
    many distinct sessions/transactions get opened. Each chunk = one
    session.begin() block."""

    @pytest.mark.asyncio
    async def test_upsert_entities_chunks_at_100(self, db_module, monkeypatch):
        """250 teams → 3 chunks of (100, 100, 50)."""
        sessions_opened = []

        # Fake AsyncSession context manager that records each enter.
        class _FakeSession:
            async def __aenter__(self):
                sessions_opened.append("session")
                return self
            async def __aexit__(self, *a):
                return False
            def begin(self):
                return _FakeTransaction()
            async def execute(self, *a, **kw):
                # Returns a result-like object whose rowcount = 1
                # so the function counts "new entities" / "new aliases".
                m = MagicMock()
                m.rowcount = 1
                m.scalar_one_or_none = MagicMock(return_value=1)
                return m

        class _FakeTransaction:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a):
                return False

        # Replace async_session() callable with a factory that yields
        # our fake. async_session is a callable that returns an
        # AsyncContextManager on call.
        monkeypatch.setattr(db_module, "async_session", lambda: _FakeSession())
        monkeypatch.setattr(db_module, "DATABASE_URL", "postgresql://fake")

        # Build 250 teams.
        teams = [
            {
                "canonical_name": f"Team {i}",
                "entity_type": "team",
                "sport": "Soccer",
                "aliases": [{"alias": f"T{i}", "source": "test", "normalized": f"t{i}"}],
            }
            for i in range(250)
        ]
        await db_module.upsert_entities(teams)
        assert len(sessions_opened) == 3, f"Expected 3 chunks, got {len(sessions_opened)}"

    @pytest.mark.asyncio
    async def test_upsert_entities_handles_empty_list(self, db_module):
        # Should be a no-op; no crash.
        await db_module.upsert_entities([])

    @pytest.mark.asyncio
    async def test_upsert_entities_chunk_failure_isolation(
        self, db_module, monkeypatch,
    ):
        """If chunk 1 raises during commit, chunks 0 and 2 still
        complete. Documents the failure-isolation guarantee."""
        chunk_attempts = [0]
        successful_executes = [0]

        class _FlakyTransaction:
            def __init__(self, fail_on_exit: bool):
                self.fail = fail_on_exit
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a):
                if self.fail and a[0] is None:
                    # Raise from commit (simulates Neon connection blip).
                    raise RuntimeError("simulated commit failure")
                return False

        class _FlakySession:
            def __init__(self, idx):
                self.idx = idx
            async def __aenter__(self):
                chunk_attempts[0] += 1
                return self
            async def __aexit__(self, *a):
                return False
            def begin(self):
                # Chunk 1 (idx=1, the second chunk) fails on commit.
                return _FlakyTransaction(fail_on_exit=(self.idx == 1))
            async def execute(self, *a, **kw):
                successful_executes[0] += 1
                m = MagicMock()
                m.rowcount = 1
                m.scalar_one_or_none = MagicMock(return_value=1)
                return m

        # Closure captures session count and yields the matching
        # session per chunk index.
        idx_box = [0]
        def _factory():
            s = _FlakySession(idx_box[0])
            idx_box[0] += 1
            return s
        monkeypatch.setattr(db_module, "async_session", _factory)
        monkeypatch.setattr(db_module, "DATABASE_URL", "postgresql://fake")

        # 250 teams = 3 chunks. Chunk 1 (middle) will fail commit.
        teams = [
            {"canonical_name": f"T{i}", "entity_type": "team", "sport": "Soccer", "aliases": []}
            for i in range(250)
        ]
        # Should NOT raise — per-chunk error is caught + logged.
        await db_module.upsert_entities(teams)
        # All 3 chunks were attempted.
        assert chunk_attempts[0] == 3


# ── Static inspection: sync_events_to_db has finally → dispose ─

class TestEngineLifecycle:
    def test_sync_events_to_db_uses_try_finally_for_dispose(self, db_module):
        """Read the function's source and confirm the dispose() call
        is in a `finally:` block, not just the success path or an
        ad-hoc except branch. Static guard against regressing to
        the leaky pattern.
        """
        import inspect
        src = inspect.getsource(db_module.sync_events_to_db)
        # The finally clause must contain dispose().
        assert "finally:" in src, "sync_events_to_db must have a finally block"
        # Find the finally and confirm dispose is in it (loose match —
        # implementation may be `await _engine.dispose()` or similar).
        finally_idx = src.find("finally:")
        post_finally = src[finally_idx:]
        assert ".dispose()" in post_finally, \
            "dispose() must be inside the finally block"

    def test_sync_events_to_db_chunks(self, db_module):
        """Confirm chunk-loop pattern is in place. Static check
        against regressing to a single mega-transaction."""
        import inspect
        src = inspect.getsource(db_module.sync_events_to_db)
        assert "CHUNK_SIZE" in src, "sync_events_to_db must chunk records"
        assert "for chunk_start in range" in src, \
            "sync_events_to_db must iterate chunks via range(0, len, CHUNK_SIZE)"

    def test_upsert_entities_chunks(self, db_module):
        """Same static guard for upsert_entities."""
        import inspect
        src = inspect.getsource(db_module.upsert_entities)
        assert "CHUNK_SIZE" in src, "upsert_entities must chunk teams"
        assert "for chunk_start in range" in src
