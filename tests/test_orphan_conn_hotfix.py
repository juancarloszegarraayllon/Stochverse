"""Regression guards for the 2026-08-07 orphan-connection hotfix.

Two defects were hunted after E-rev-b's 4-read verification failed:

  A. CHURN-ERA GHOST (confirmed prime cause; operator terminated by
     hand): pre-hotfix rev-b acquires ran via the POOLED endpoint
     due to a shared connect_args bug (advisory_lock_engine sharing
     `application_name = "stochverse-web"` with the data engine and
     — pre-hotfix — the pooled DSN). Under pgbouncer transaction-
     mode, the "session" that pg_try_advisory_lock acquires against
     lives on a SERVER BACKEND that pgbouncer keeps warm across
     client disconnects. When the acquiring client disconnected
     (session.commit release, or worker restart), pgbouncer's
     server-keepalive stranded the lock: backend PID stays alive,
     session-level advisory lock stays held, state_change keeps
     updating from multiplexed traffic. Result: lock survived
     indefinitely across worker restarts.

     Fix: `sweep_orphan_advisory_locks()` — boot-time sweep queries
     pg_stat_activity for any backend holding OUR advisory keys
     whose application_name is NOT the current AL tag
     ("stochverse-web-al"). Log-only WARNING with a
     pg_terminate_backend SQL snippet per orphan so the operator
     can clear by hand. Runs against the DIRECT-endpoint engine
     so its own query is not multiplexed.

  B. CRASH-PATH LEAK (defense-in-depth; no longer prime suspect,
     but still a real leak worth closing): the previous
     acquire_lock_connection_bounded had `try: … except Exception:`
     around the acquire I/O. CancelledError is a BaseException in
     Python 3.8+, so a supervise cancel or wait_for-timeout AFTER
     pg_try_advisory_lock succeeded server-side would bypass the
     cleanup and orphan the connection. Post-E.1-commit the conn
     is idle-out-of-txn → no server timeout reaps it → orphan is
     permanent until process restart. Also: the outer
     `asyncio.wait_for(_do_acquire(), ...)` had a documented
     completion-vs-timeout race that could discard a successful
     result while still raising TimeoutError.

     Fix: inline _do_acquire (no outer wait_for → no return-race),
     per-I/O wait_for wraps, and `except BaseException` with
     asyncio.shield around __aexit__ so a lingering cancel can't
     abort cleanup mid-close.
"""
from __future__ import annotations

import asyncio
import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest


# ── Ghost-sweep helper (Defect A, prime cause) ────────────────────

def test_sweep_orphan_advisory_locks_exists_and_is_wired_at_startup():
    """The sweep helper MUST exist AND be called from startup_event
    against the DIRECT-endpoint engine. Regression to not-called
    silently reopens the churn-era-ghost blind spot."""
    from ingestion.base import sweep_orphan_advisory_locks  # exists
    assert asyncio.iscoroutinefunction(sweep_orphan_advisory_locks)

    import main
    startup_src = inspect.getsource(main.startup_event)
    assert "sweep_orphan_advisory_locks" in startup_src, (
        "sweep_orphan_advisory_locks is not called from startup_event "
        "— boot-time ghost detection missing; regression to 2026-08-07 "
        "morning outage where operator had to hand-terminate pid 11988"
    )
    assert "advisory_lock_engine" in startup_src, (
        "sweep must run against the DIRECT-endpoint engine "
        "(advisory_lock_engine) so its own query isn't multiplexed"
    )


def test_sweep_all_advisory_lock_keys_covers_full_set():
    """`_ALL_ADVISORY_LOCK_KEYS` MUST include every ADVISORY_LOCK_*
    constant. Regression: a new lock key added without updating the
    sweep tuple → sweep misses ghosts on the new key silently."""
    import ingestion.base as base
    module_keys = {
        v for k, v in vars(base).items()
        if k.startswith("ADVISORY_LOCK_") and isinstance(v, int)
    }
    sweep_keys = set(base._ALL_ADVISORY_LOCK_KEYS)
    missing = module_keys - sweep_keys
    assert not missing, (
        f"ADVISORY_LOCK_* keys not in sweep tuple: {[hex(k) for k in missing]}"
    )


def test_sweep_expected_application_name_matches_al_engine_tag():
    """The sweep's expected_application_name default MUST equal the
    AL engine's `application_name` server_setting in db.py. If they
    drift, EVERY AL connection looks like a ghost → sweep emits
    false-positive terminate suggestions."""
    from ingestion.base import AL_APPLICATION_NAME_EXPECTED
    import db
    db_src = inspect.getsource(db)
    # Assert the string appears in db.py's AL server_settings block.
    assert f'"{AL_APPLICATION_NAME_EXPECTED}"' in db_src, (
        f"AL_APPLICATION_NAME_EXPECTED={AL_APPLICATION_NAME_EXPECTED!r} "
        f"not present in db.py — the sweep will false-positive on "
        f"the AL engine's OWN connections"
    )


@pytest.mark.asyncio
async def test_sweep_returns_empty_on_none_engine():
    """Defensive: if AL engine failed to init (e.g., bad DATABASE_URL_
    DIRECT) the sweep MUST no-op silently, not raise."""
    from ingestion.base import sweep_orphan_advisory_locks
    result = await sweep_orphan_advisory_locks(None)
    assert result == []


@pytest.mark.asyncio
async def test_sweep_uses_expected_app_name_in_sql():
    """The query MUST filter with `application_name IS DISTINCT FROM
    :expected_name` so NULL app_names (pgbouncer server processes
    without a set application_name) are included as ghosts.
    Regression to `!=` would exclude NULL app_names — pgbouncer
    ghosts that never had a client-side application_name would be
    missed."""
    from ingestion.base import sweep_orphan_advisory_locks
    src = inspect.getsource(sweep_orphan_advisory_locks)
    assert "IS DISTINCT FROM" in src, (
        "sweep SQL uses `!=` instead of `IS DISTINCT FROM` — NULL "
        "application_name rows (pgbouncer server processes) will "
        "not be flagged as ghosts"
    )


@pytest.mark.asyncio
async def test_sweep_logs_terminate_sql_per_orphan(caplog):
    """Each detected orphan MUST log a `pg_terminate_backend(<pid>)`
    SQL snippet so the operator can copy-paste to clear. Log level
    WARNING — visible in a busy stream, not lost in INFO noise."""
    import logging as _logging
    from ingestion.base import sweep_orphan_advisory_locks

    # Mock an engine whose sweep query returns one orphan row.
    mock_conn = MagicMock()
    fake_row = {
        "pid": 11988,
        "application_name": "stochverse-web",  # NOT -al → ghost
        "state": "idle",
        "state_change": None,
        "backend_start": None,
        "classid": 0x5350,
        "objid": 0xF106,  # bracket_walk
    }
    result_obj = MagicMock()
    result_obj.mappings.return_value.all.return_value = [fake_row]
    mock_conn.execute = AsyncMock(return_value=result_obj)

    conn_cm = MagicMock()
    conn_cm.__aenter__ = AsyncMock(return_value=mock_conn)
    conn_cm.__aexit__ = AsyncMock(return_value=None)

    mock_engine = MagicMock()
    mock_engine.connect = MagicMock(return_value=conn_cm)

    with caplog.at_level(_logging.WARNING):
        orphans = await sweep_orphan_advisory_locks(mock_engine)

    assert len(orphans) == 1
    assert orphans[0]["pid"] == 11988
    # Use getMessage() — printf-style %s placeholders with positional
    # args need substitution to see the final rendered text (structlog
    # + stdlib caplog quirk).
    warning_msgs = [
        rec.getMessage() for rec in caplog.records
        if rec.levelno == _logging.WARNING
    ]
    # structlog stores positional args separately (JSON keys
    # `positional_args` + `event`) rather than substituting, so we
    # check for the template AND the pid as separate substrings in
    # the rendered log line — both must be present for the operator
    # to have a copy-pasteable SQL snippet.
    assert any(
        "pg_terminate_backend(%s)" in m and "11988" in m
        for m in warning_msgs
    ), f"terminate SQL template or pid missing from log: {warning_msgs}"
    assert any(
        "AL ORPHAN LOCK DETECTED" in m for m in warning_msgs
    ), "loud sentinel missing"


# ── Crash-path leak (Defect B, defense-in-depth) ──────────────────

def test_acquire_catches_base_exception_not_just_exception():
    """LOAD-BEARING regression guard for Defect B.

    `except Exception` in Python 3.8+ misses `asyncio.CancelledError`
    (moved to BaseException). Under wait_for timeout or supervise
    cancel, the previous shape orphaned the connection because
    CancelledError bypassed the cleanup `__aexit__`. Post-hotfix
    the cleanup block MUST catch BaseException so cancellation is
    routed through __aexit__."""
    from ingestion.base import acquire_lock_connection_bounded
    src = inspect.getsource(acquire_lock_connection_bounded)
    assert "except BaseException" in src, (
        "acquire_lock_connection_bounded no longer catches "
        "BaseException — CancelledError would once again bypass "
        "conn_cm.__aexit__ and leak orphan connections."
    )


def test_acquire_does_not_wrap_body_in_outer_wait_for():
    """Defect B secondary: `asyncio.wait_for(_do_acquire(), ...)`
    had a documented completion-vs-timeout race — a successful
    tuple return discarded by wait_for raising TimeoutError. Fix
    inlines the acquire and puts per-I/O wait_for wraps INSIDE the
    try. Regression guard: no wait_for wrapping the whole
    acquire body."""
    from ingestion.base import acquire_lock_connection_bounded
    import ast as _ast
    raw = inspect.getsource(acquire_lock_connection_bounded)
    # Strip docstring so assertions target CODE only — the function's
    # docstring narrates the bad pattern for historical context.
    _fn = _ast.parse(raw).body[0]
    if (_fn.body and isinstance(_fn.body[0], _ast.Expr)
            and isinstance(_fn.body[0].value, _ast.Constant)
            and isinstance(_fn.body[0].value.value, str)):
        _fn.body = _fn.body[1:]
    src = _ast.unparse(_fn)
    # The bad pattern: wait_for(_do_acquire(), ...).
    assert "wait_for(_do_acquire()" not in src, (
        "outer wait_for around a helper is back — completion-vs-"
        "timeout race can orphan the returned connection"
    )
    # Positive: per-I/O wait_for wraps around conn.execute AND
    # conn_cm.__aenter__ ARE present.
    assert "conn_cm.__aenter__()" in src and "wait_for" in src, (
        "per-I/O wait_for missing"
    )


def test_acquire_shields_aexit_cleanup():
    """The __aexit__ cleanup call in the except blocks MUST be
    wrapped in asyncio.shield. Without shield, a lingering
    CancelledError delivered while cleanup is in-flight would abort
    __aexit__ mid-close — same orphan outcome as the bug we're
    fixing."""
    from ingestion.base import acquire_lock_connection_bounded
    src = inspect.getsource(acquire_lock_connection_bounded)
    assert "asyncio.shield(conn_cm.__aexit__" in src, (
        "asyncio.shield missing around conn_cm.__aexit__ cleanup — "
        "re-cancellation can abort cleanup and leak the connection"
    )


@pytest.mark.asyncio
async def test_acquire_calls_aexit_when_execute_raises_cancelled():
    """Behavioral proof of Defect B fix: inject CancelledError from
    conn.execute (simulating supervise-cancel or wait_for-timeout
    delivered mid-acquire). conn_cm.__aexit__ MUST be called."""
    from ingestion.base import acquire_lock_connection_bounded

    aexit_calls = {"n": 0}

    async def _cancelling_execute(*_args, **_kwargs):
        raise asyncio.CancelledError()

    async def _aexit(*_args, **_kwargs):
        aexit_calls["n"] += 1

    mock_conn = MagicMock()
    mock_conn.execute = _cancelling_execute

    conn_cm = MagicMock()
    conn_cm.__aenter__ = AsyncMock(return_value=mock_conn)
    conn_cm.__aexit__ = _aexit

    mock_engine = MagicMock()
    mock_engine.connect = MagicMock(return_value=conn_cm)

    with pytest.raises(asyncio.CancelledError):
        await acquire_lock_connection_bounded(
            mock_engine, 0xDEADBEEF, log_name="test", timeout_s=5.0,
        )

    assert aexit_calls["n"] == 1, (
        f"conn_cm.__aexit__ called {aexit_calls['n']} times after "
        f"CancelledError from execute; expected 1 (orphan-leak "
        f"regression)"
    )


@pytest.mark.asyncio
async def test_acquire_calls_aexit_when_commit_raises_cancelled():
    """Same as above but the cancellation lands during commit()
    (after pg_try_advisory_lock succeeded). This is the exact
    Defect B scenario: lock held server-side, then cancel → without
    the fix, orphan is permanent."""
    from ingestion.base import acquire_lock_connection_bounded

    aexit_calls = {"n": 0}

    async def _ok_execute(*_args, **_kwargs):
        r = MagicMock()
        r.scalar = MagicMock(return_value=True)   # got the lock
        return r

    async def _cancelling_commit(*_args, **_kwargs):
        raise asyncio.CancelledError()

    async def _aexit(*_args, **_kwargs):
        aexit_calls["n"] += 1

    mock_conn = MagicMock()
    mock_conn.execute = _ok_execute
    mock_conn.commit = _cancelling_commit

    conn_cm = MagicMock()
    conn_cm.__aenter__ = AsyncMock(return_value=mock_conn)
    conn_cm.__aexit__ = _aexit

    mock_engine = MagicMock()
    mock_engine.connect = MagicMock(return_value=conn_cm)

    with pytest.raises(asyncio.CancelledError):
        await acquire_lock_connection_bounded(
            mock_engine, 0xDEADBEEF, log_name="test", timeout_s=5.0,
        )

    assert aexit_calls["n"] == 1, (
        f"conn_cm.__aexit__ called {aexit_calls['n']} times after "
        f"CancelledError during commit (lock held server-side); "
        f"expected 1 (permanent orphan regression)"
    )


@pytest.mark.asyncio
async def test_acquire_calls_aexit_when_execute_times_out():
    """Behavioral proof: per-I/O wait_for firing on conn.execute
    triggers cleanup. Boot timeout surface preserved."""
    from ingestion.base import acquire_lock_connection_bounded

    aexit_calls = {"n": 0}

    async def _hanging_execute(*_args, **_kwargs):
        await asyncio.Event().wait()   # never returns

    async def _aexit(*_args, **_kwargs):
        aexit_calls["n"] += 1

    mock_conn = MagicMock()
    mock_conn.execute = _hanging_execute

    conn_cm = MagicMock()
    conn_cm.__aenter__ = AsyncMock(return_value=mock_conn)
    conn_cm.__aexit__ = _aexit

    mock_engine = MagicMock()
    mock_engine.connect = MagicMock(return_value=conn_cm)

    with pytest.raises(asyncio.TimeoutError):
        await acquire_lock_connection_bounded(
            mock_engine, 0xDEADBEEF, log_name="test", timeout_s=0.05,
        )

    assert aexit_calls["n"] == 1, (
        "conn_cm.__aexit__ not called after wait_for timeout on execute"
    )


@pytest.mark.asyncio
async def test_acquire_calls_aexit_when_aenter_times_out():
    """If the connection-open itself times out, conn is still None —
    the except MUST not attempt __aexit__ on a half-opened CM (would
    raise). Regression guard against a naive `except: __aexit__()`
    that assumes conn is set."""
    from ingestion.base import acquire_lock_connection_bounded

    aexit_calls = {"n": 0}

    async def _hanging_aenter(*_args, **_kwargs):
        await asyncio.Event().wait()

    async def _aexit(*_args, **_kwargs):
        aexit_calls["n"] += 1

    conn_cm = MagicMock()
    conn_cm.__aenter__ = _hanging_aenter
    conn_cm.__aexit__ = _aexit

    mock_engine = MagicMock()
    mock_engine.connect = MagicMock(return_value=conn_cm)

    with pytest.raises(asyncio.TimeoutError):
        await acquire_lock_connection_bounded(
            mock_engine, 0xDEADBEEF, log_name="test", timeout_s=0.05,
        )

    # conn was never entered → __aexit__ MUST NOT be called (0 calls).
    assert aexit_calls["n"] == 0, (
        f"conn_cm.__aexit__ called {aexit_calls['n']} times on a "
        f"half-opened CM (conn was None); expected 0"
    )
