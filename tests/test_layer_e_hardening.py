"""Tests for PR-Layer-E-rev-b — pinned-AsyncConnection AL acquire +
DIRECT-endpoint engine + heartbeat + LockGripLost + per-pass wait_for.

Layer E-rev-a (PR #289) was proven broken by production evidence
(2026-08-06, 23:23Z): pg_locks showed 3/6 lock pids migrated in 10min,
2 locks per pid, ZERO LockGripLost log lines across three grip
migrations. Root cause:

  1. MASTER: DATABASE_URL pointed at Neon's pgbouncer POOLED endpoint
     (transaction-mode). Session-level advisory locks are
     FUNDAMENTALLY INCOMPATIBLE with pgbouncer transaction-mode
     pooling: after each txn boundary pgbouncer may rebind the app-
     side connection to a different backend. The lock lives on one
     backend, the next statement may hit another.

  2. IMPLEMENTATION: SQLAlchemy AsyncSession.commit() RETURNS the
     underlying DBAPI connection to the pool (2.0 default behavior).
     Under NullPool that closes the connection instantly → session-
     level advisory lock released at acquisition time. The next
     session.execute() (heartbeat SELECT 1) autobegin'd a FRESH
     connection from the pool, proving nothing about the original
     lock. The heartbeat was watching the wrong socket.

Layer E-rev-b (2026-08-06) fixes both:

  1. `advisory_lock_engine` bound to `DATABASE_URL_DIRECT` (Neon
     DIRECT endpoint, bypasses pgbouncer). Data engine stays pooled.

  2. `acquire_lock_connection_bounded(engine, key)` uses
     `engine.connect()` (AsyncConnection). AsyncConnection holds the
     underlying DBAPI connection for its explicit CM lifetime;
     commit()/rollback() end transactions but do NOT release the
     connection. Acquire AND heartbeat run on the SAME connection.

  3. `acquire_lock_session_bounded` REMOVED — kept as a raising stub
     so any accidental reference blows up loud instead of silently
     landing on the broken session-mediated path.

  4. `holder_heartbeat(conn, key, name)` takes an AsyncConnection.
     Heartbeat runs SELECT 1 + commit on the pinned conn every
     AL_HEARTBEAT_INTERVAL_S (belt-and-braces vs idle-in-txn).
"""
from __future__ import annotations

import asyncio
import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── The regression test that catches rev-a all over again ──────────

def test_al_path_never_uses_sessionmaker_or_session_commit():
    """LOAD-BEARING regression guard against rev-a's defect.

    The AL acquire path MUST use engine.connect() (AsyncConnection),
    NOT async_sessionmaker + session.commit(). Session.commit() on a
    SQLAlchemy AsyncSession RETURNS the connection to the pool
    (SQLAlchemy 2.0 default); under NullPool that CLOSES the connection
    and RELEASES the session-level advisory lock at acquisition time.

    Rev-a made this exact mistake; three production reads at 23:23Z
    on 2026-08-06 proved it (3/6 lock pids migrated in 10min, zero
    grip-loss detections). If this test starts failing, someone
    reintroduced the session-mediated AL path — do not merge."""
    from ingestion.base import acquire_lock_connection_bounded
    src = inspect.getsource(acquire_lock_connection_bounded)

    # Strip docstring so the assertions target CODE, not narrative
    # references (this function's docstring intentionally mentions
    # the rev-a defect terminology to document the rationale).
    import ast as _ast
    _fn = _ast.parse(src).body[0]
    if (_fn.body and isinstance(_fn.body[0], _ast.Expr)
            and isinstance(_fn.body[0].value, _ast.Constant)
            and isinstance(_fn.body[0].value.value, str)):
        _fn.body = _fn.body[1:]  # drop docstring
    code_src = _ast.unparse(_fn)

    assert "engine.connect()" in code_src, (
        "acquire_lock_connection_bounded MUST use engine.connect() "
        "(AsyncConnection) to pin the DBAPI connection for the lock's "
        "lifetime. Regression to sessionmaker/session-based acquire "
        "reintroduces rev-a's defect."
    )
    # Call-syntax checks: `async_sessionmaker(` and `session.commit(`
    # would indicate actual use, not narrative mentions.
    assert "async_sessionmaker(" not in code_src, (
        "acquire_lock_connection_bounded MUST NOT call "
        "async_sessionmaker — session-mediated AL is unsound "
        "(session.commit releases the DBAPI connection)."
    )
    assert "session.commit(" not in code_src, (
        "acquire_lock_connection_bounded MUST NOT call session.commit "
        "— that releases the DBAPI connection and drops the lock."
    )


def test_acquire_lock_session_bounded_is_removed_and_raises():
    """Rev-a's helper name is retained as a raising stub — any
    accidental import + call must fail LOUD, not silently land on
    the broken session-mediated path."""
    from ingestion.base import acquire_lock_session_bounded

    async def _try():
        await acquire_lock_session_bounded(None, 0x1234)

    with pytest.raises(RuntimeError, match="removed in Layer E-rev-b"):
        asyncio.run(_try())


def test_advisory_lock_session_sessionmaker_is_not_exposed():
    """`db.advisory_lock_session` MUST be None (or absent). The
    sessionmaker exposure was the enabler for rev-a's defect — a
    caller could `from db import advisory_lock_session` and get a
    working-looking factory that was actually broken for AL purposes.
    Rev-b sets it to None so any such import fails at call time."""
    import db
    assert db.advisory_lock_session is None, (
        "db.advisory_lock_session must be None in rev-b — the "
        "sessionmaker enabled rev-a's session-mediated AL defect. "
        "Callers must use db.advisory_lock_engine (an Engine) via "
        "ingestion.base.acquire_lock_connection_bounded."
    )


# ── DATABASE_URL_DIRECT split ──────────────────────────────────────

def test_db_module_reads_database_url_direct_env():
    """db.DATABASE_URL_DIRECT MUST exist and fall back to DATABASE_URL
    when unset. Neon pgbouncer transaction-mode pooling is
    fundamentally incompatible with session-level advisory locks —
    the AL engine MUST bind to Neon's direct endpoint."""
    import db
    assert hasattr(db, "DATABASE_URL_DIRECT"), (
        "db module missing DATABASE_URL_DIRECT — required for AL "
        "engine to bypass pgbouncer txn-mode pooling"
    )


def test_advisory_lock_engine_source_mentions_direct_endpoint():
    """The AL engine creation site MUST reference DATABASE_URL_DIRECT.
    Regression to a DATABASE_URL-bound AL engine reopens the master
    root cause: pgbouncer txn-mode multiplexing kills session-level
    advisory locks regardless of application code correctness."""
    import db
    import inspect as _inspect
    src = _inspect.getsource(db)
    # The advisory_lock_engine assignment must reference DATABASE_URL_DIRECT.
    # Locate the block and assert.
    al_block_start = src.find("advisory_lock_engine = create_async_engine(")
    assert al_block_start > 0, "advisory_lock_engine creation not found"
    al_block = src[al_block_start:al_block_start + 400]
    assert "DATABASE_URL_DIRECT" in al_block, (
        "advisory_lock_engine must be bound to DATABASE_URL_DIRECT, "
        "not DATABASE_URL — pgbouncer txn-mode pooling breaks "
        "session-level advisory locks"
    )


# ── Pinned-connection acquire behavior ─────────────────────────────

@pytest.mark.asyncio
async def test_acquire_calls_conn_commit_when_lock_acquired():
    """Behavioral: with a mocked engine.connect(), acquire calls
    conn.commit() when try_acquire returns True. AsyncConnection.
    commit() ends the txn but does NOT release the DBAPI connection
    (unlike Session.commit which does)."""
    from ingestion.base import acquire_lock_connection_bounded

    mock_conn = MagicMock()
    mock_conn.execute = AsyncMock(
        return_value=MagicMock(scalar=MagicMock(return_value=True)),
    )
    mock_conn.commit = AsyncMock()

    mock_conn_cm = MagicMock()
    mock_conn_cm.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn_cm.__aexit__ = AsyncMock(return_value=None)

    mock_engine = MagicMock()
    mock_engine.connect = MagicMock(return_value=mock_conn_cm)

    conn_cm, conn, got_lock = await acquire_lock_connection_bounded(
        mock_engine, 0xDEADBEEF, log_name="test", timeout_s=5.0,
    )

    assert got_lock is True
    assert mock_conn.commit.await_count == 1, (
        f"conn.commit() called {mock_conn.commit.await_count} times; "
        f"expected exactly 1 (end implicit BEGIN so idle-in-txn "
        f"floor cannot fire)"
    )
    assert mock_engine.connect.call_count == 1, (
        "engine.connect() must be called exactly once per acquire"
    )


@pytest.mark.asyncio
async def test_acquire_does_not_commit_when_lock_not_acquired():
    """On lost race (try_acquire returns False), do NOT commit — the
    connection is about to close and the extra round-trip is wasted."""
    from ingestion.base import acquire_lock_connection_bounded

    mock_conn = MagicMock()
    mock_conn.execute = AsyncMock(
        return_value=MagicMock(scalar=MagicMock(return_value=False)),
    )
    mock_conn.commit = AsyncMock()

    mock_conn_cm = MagicMock()
    mock_conn_cm.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn_cm.__aexit__ = AsyncMock(return_value=None)

    mock_engine = MagicMock()
    mock_engine.connect = MagicMock(return_value=mock_conn_cm)

    _cm, _conn, got_lock = await acquire_lock_connection_bounded(
        mock_engine, 0xDEADBEEF, log_name="test", timeout_s=5.0,
    )

    assert got_lock is False
    assert mock_conn.commit.await_count == 0


@pytest.mark.asyncio
async def test_acquire_raises_when_engine_is_none():
    """Engine argument is required. Rev-a fell back to caller's
    factory; rev-b's stricter contract forces callers to pass the
    correct engine or fail loud at boot."""
    from ingestion.base import acquire_lock_connection_bounded
    with pytest.raises(RuntimeError, match="engine is None"):
        await acquire_lock_connection_bounded(
            None, 0xDEADBEEF, log_name="test", timeout_s=5.0,
        )


# ── Heartbeat behavior on pinned AsyncConnection ───────────────────

@pytest.mark.asyncio
async def test_heartbeat_raises_lockgriplost_on_conn_execute_error():
    """When the heartbeat's SELECT 1 on the pinned AsyncConnection
    raises (simulating Neon-proxy reap / dead socket), heartbeat
    cancels the parent task; the CM re-raises as LockGripLost."""
    from ingestion.base import holder_heartbeat, LockGripLost

    class _FakeInterfaceError(Exception):
        """Stand-in for sqlalchemy.exc.InterfaceError."""

    call_count = {"n": 0}

    async def _fake_execute(*_args, **_kwargs):
        call_count["n"] += 1
        raise _FakeInterfaceError("connection is closed")

    mock_conn = MagicMock()
    mock_conn.execute = _fake_execute
    mock_conn.commit = AsyncMock()

    async def _hold():
        async with holder_heartbeat(mock_conn, 0xF104, "test_task"):
            await asyncio.sleep(60.0)   # heartbeat will interrupt this

    with patch("ingestion.base.AL_HEARTBEAT_INTERVAL_S", 0.05):
        with pytest.raises(LockGripLost) as exc_info:
            await asyncio.wait_for(_hold(), timeout=2.0)

    assert "grip loss" in str(exc_info.value).lower()
    assert "test_task" in str(exc_info.value)
    assert "_FakeInterfaceError" in str(exc_info.value)
    assert call_count["n"] >= 1


@pytest.mark.asyncio
async def test_heartbeat_commits_on_the_pinned_conn():
    """The heartbeat's SELECT 1 MUST be followed by a commit on the
    SAME conn. Without commit, we accumulate idle-in-txn state on
    the AL conn between pulses (server-side idle_in_transaction_
    session_timeout=60s would eventually kill the connection)."""
    from ingestion.base import holder_heartbeat

    exec_count = {"n": 0}
    commit_count = {"n": 0}

    async def _fake_execute(*_args, **_kwargs):
        exec_count["n"] += 1
        return MagicMock()

    async def _fake_commit():
        commit_count["n"] += 1

    mock_conn = MagicMock()
    mock_conn.execute = _fake_execute
    mock_conn.commit = _fake_commit

    with patch("ingestion.base.AL_HEARTBEAT_INTERVAL_S", 0.02):
        async with holder_heartbeat(mock_conn, 0xF104, "test_task"):
            await asyncio.sleep(0.12)  # ~5 heartbeat pulses

    # Every SELECT MUST be followed by a commit — 1:1 ratio.
    assert exec_count["n"] >= 3, (
        f"heartbeat ran only {exec_count['n']} times in 0.12s "
        f"@ 0.02s interval — expected ≥3"
    )
    assert commit_count["n"] == exec_count["n"], (
        f"SELECT count ({exec_count['n']}) must equal commit count "
        f"({commit_count['n']}) — regression would leave AL conn "
        f"idle-in-txn between pulses"
    )


@pytest.mark.asyncio
async def test_external_cancel_does_not_become_lockgriplost():
    """When supervise (or any external party) cancels the parent
    task while the heartbeat is healthy, the CM must propagate
    CancelledError unchanged — NOT translate it into LockGripLost."""
    from ingestion.base import holder_heartbeat

    mock_conn = MagicMock()
    mock_conn.execute = AsyncMock(return_value=MagicMock())
    mock_conn.commit = AsyncMock()

    async def _hold():
        async with holder_heartbeat(mock_conn, 0xF104, "test_task"):
            await asyncio.sleep(10.0)

    with patch("ingestion.base.AL_HEARTBEAT_INTERVAL_S", 0.05):
        t = asyncio.create_task(_hold())
        await asyncio.sleep(0.15)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t


# ── supervise catches LockGripLost as a peer of NotLockHolder ──────

def test_supervise_catches_lockgriplost_before_generic_exception():
    """supervise() MUST have `except LockGripLost` BEFORE
    `except Exception` — peer of NotLockHolder. Regression to
    falling-through to generic Exception counts grip-loss as a crash
    (wrong — infrastructural signal), triggers exponential backoff,
    and pollutes crash_alert_threshold."""
    from ingestion.base import supervise
    src = inspect.getsource(supervise)

    assert "except LockGripLost" in src, (
        "supervise() no longer catches LockGripLost specifically"
    )

    idx_grip = src.find("except LockGripLost")
    idx_generic = src.find("except Exception as exc:")
    assert idx_grip < idx_generic, (
        "except LockGripLost must precede except Exception"
    )

    idx_nlh = src.find("except NotLockHolder")
    assert idx_nlh < idx_generic and idx_grip < idx_generic

    grip_block = src[idx_grip:idx_generic]
    assert "crash_times.append" not in grip_block, (
        "LockGripLost must NOT append to crash_times"
    )
    assert "continue" in grip_block, (
        "LockGripLost handler must continue back to re-race"
    )


# ── E.3 wraps preserved from rev-a ─────────────────────────────────

def test_score_flush_pass_timeout_wraps_work():
    """_score_flush_loop MUST wrap per-iteration work in
    asyncio.wait_for(pass_timeout_for(first_pass))."""
    import main
    src = inspect.getsource(main._score_flush_loop)
    assert "asyncio.wait_for" in src
    assert "pass_timeout_for" in src
    assert "ingestion.task.pass_timeout" in src
    assert "holder_heartbeat" in src


# ── E.4 audit: all 4 main.py holder loops migrated to pinned-conn ──

@pytest.mark.parametrize("func_name,task_name", [
    ("_score_flush_loop",                     "score_flush"),
    ("_price_prune_loop",                     "price_prune"),
    ("_multi_stage_discovery_loop_body",      "multi_stage_disc"),
    ("_tournament_bracket_warm_loop_body",    "bracket_walk"),
])
def test_all_main_loops_have_pass_timeout_wrap(func_name, task_name):
    """Every main.py holder loop MUST wrap per-pass or per-op
    blocking work in asyncio.wait_for + pass_timeout_for."""
    import main
    fn = getattr(main, func_name, None)
    assert fn is not None
    src = inspect.getsource(fn)

    assert "asyncio.wait_for" in src
    assert "pass_timeout_for" in src
    assert f"task={task_name}" in src


@pytest.mark.parametrize("wrapper_func_name,task_name", [
    ("_score_flush_loop",                    "score_flush"),
    ("_price_prune_loop",                    "price_prune"),
    ("_multi_stage_discovery_loop",          "multi_stage_disc"),
    ("_tournament_bracket_warm_loop",        "bracket_walk"),
])
def test_all_main_wrappers_use_pinned_conn_api(
    wrapper_func_name, task_name,
):
    """Every main.py holder loop wrapper MUST use the pinned-conn
    API: acquire_lock_connection_bounded + holder_heartbeat +
    conn_cm / lock_conn variable names. Regression to session_cm/
    lock_session names strongly implies session-mediated AL crept
    back in."""
    import main
    fn = getattr(main, wrapper_func_name, None)
    assert fn is not None
    src = inspect.getsource(fn)

    assert "acquire_lock_connection_bounded" in src, (
        f"main.{wrapper_func_name} not migrated to "
        f"acquire_lock_connection_bounded — see rev-b design"
    )
    assert "acquire_lock_session_bounded" not in src, (
        f"main.{wrapper_func_name} still references the removed "
        f"acquire_lock_session_bounded"
    )
    assert "holder_heartbeat" in src
    assert f'"{task_name}"' in src
    assert "advisory_lock_engine" in src, (
        f"main.{wrapper_func_name} must reference "
        f"db.advisory_lock_engine (the DIRECT-endpoint engine)"
    )
    assert "conn_cm" in src and "lock_conn" in src, (
        f"main.{wrapper_func_name} must use conn_cm/lock_conn "
        f"names — session_cm/lock_session indicates rev-a regression"
    )


# ── kalshi.run / fl.run also migrated ──────────────────────────────

@pytest.mark.parametrize("module_name", ["ingestion.kalshi", "ingestion.fl"])
def test_ingestion_run_uses_pinned_conn_api(module_name):
    """ingestion.kalshi.run and ingestion.fl.run — the two long-lived
    ingestion holders — MUST use the pinned-conn API. They were on
    the same session-mediated pattern as the main.py loops and are
    equally vulnerable to rev-a's defect."""
    import importlib
    mod = importlib.import_module(module_name)
    src = inspect.getsource(mod.run)

    assert "acquire_lock_connection_bounded" in src
    assert "advisory_lock_engine" in src
    assert "holder_heartbeat" in src
    assert "conn_cm" in src and "lock_conn" in src
