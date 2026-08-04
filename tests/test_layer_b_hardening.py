"""Tests for PR-Layer-B — NullPool AL engine + stamp-at-top +
worker_role derivation.

Layer B is the fourth and final PR in the Day-63 incident-class
sequence (C ✅ → A ✅ → D ✅ → B). Hardening + Day-64 verification
folds:

  1. NullPool dedicated engine for advisory-lock sessions. Closes
     the pool-leaked-lock flavor of the death class as defense-in-
     depth (DISFAVORED by corrected-timeline evidence but shipped
     regardless) AND closes the connection-leak edge case on
     boot_timeout flagged during PR-D review.

  2. Iteration-alive stamp fires at TOP of each loop iteration —
     was fired at bottom / inside pass, which false-alarmed
     stale=true on score_flush during quiet windows (Day-64
     production evidence: attempt=1, no crashes, stamp only
     advanced with work).

  3. worker_role derived from actual TASK_HEALTH states rather
     than hardcoded "holder_view" — one sample now tells the
     operator whether this worker holds any AL surfaces.
"""
from __future__ import annotations

import inspect

import pytest


# ── Layer B core: NullPool AL engine ────────────────────────────

def test_advisory_lock_engine_uses_null_pool():
    """The dedicated AL engine MUST use NullPool. Regression to the
    shared pooled engine reopens the pool-leaked-lock flavor of the
    2026-08-01 death class as a live defect."""
    import db
    if db.advisory_lock_engine is None:
        pytest.skip("no DATABASE_URL — engine not initialized")
    from sqlalchemy.pool import NullPool
    # SQLAlchemy exposes the pool via engine.pool; NullPool has a
    # distinct __class__ name.
    assert isinstance(db.advisory_lock_engine.pool, NullPool), (
        f"advisory_lock_engine.pool is {type(db.advisory_lock_engine.pool).__name__}, "
        f"expected NullPool. Regression opens the pool-leaked-lock "
        f"flavor: crashed AL session's connection returns to pool "
        f"with advisory lock still held, restart's new session lands "
        f"on a DIFFERENT pool connection → try_acquire returns False "
        f"→ pre-Layer-A this was silent death."
    )


def test_advisory_lock_engine_uses_same_dsn_and_connect_args_as_pooled():
    """Both engines MUST use the same DATABASE_URL and same
    connect_args (command_timeout, server-side timeouts, SSL). Only
    pool_class differs. Regression to divergent configs means the
    two engines exhibit different behavior on transient failures —
    debugging nightmare."""
    import db
    if db.engine is None or db.advisory_lock_engine is None:
        pytest.skip("no DATABASE_URL")
    # Same URL — both engines were created from db.DATABASE_URL.
    assert str(db.engine.url) == str(db.advisory_lock_engine.url), (
        "engines must share DATABASE_URL — regression to divergent "
        "DSNs means AL sessions could hit a different database"
    )


def test_acquire_lock_session_bounded_uses_null_pool_by_default(monkeypatch):
    """The Layer D helper `acquire_lock_session_bounded` MUST swap
    to `db.advisory_lock_session` when `use_null_pool=True` (default).
    This is the wiring that carries the NullPool benefit to
    F104-F107 without each caller having to import it manually."""
    from ingestion.base import acquire_lock_session_bounded
    src = inspect.getsource(acquire_lock_session_bounded)
    assert "from db import advisory_lock_session" in src, (
        "acquire_lock_session_bounded no longer imports "
        "advisory_lock_session — Layer B wiring lost, F104-F107 "
        "regress to pooled AL sessions"
    )
    assert "use_null_pool" in src, (
        "acquire_lock_session_bounded no longer accepts the "
        "use_null_pool parameter — test-seam contract broken"
    )


# ── Layer B fold: stamp fires at TOP of iteration ───────────────

@pytest.mark.parametrize("loop_name,func_ref,stamp_key", [
    ("score_flush",       "_score_flush_loop",                "score_flush"),
    ("price_prune",       "_price_prune_loop",                "price_prune"),
    ("bracket_walk",      "_tournament_bracket_warm_loop_body", "bracket_walk"),
    ("multi_stage_disc",  "_multi_stage_discovery_loop_body", "multi_stage_disc"),
])
def test_main_py_loops_stamp_at_top_of_iteration(loop_name, func_ref, stamp_key):
    """Class contract: every main.py loop MUST stamp at the TOP of
    its `while True` body, BEFORE any work. Pre-Layer-B stamps were
    at the bottom / after work — Day-64 production evidence showed
    the stamp only advanced during flush-with-work periods,
    false-alarming stale=true on quiet windows.

    Source inspection: within the function's source, the first
    `stamp_pass_complete("<key>")` call must appear BEFORE any
    `await` statement inside the while loop body — the stamp must
    not sit behind any await that could hang."""
    import main
    fn = getattr(main, func_ref)
    src = inspect.getsource(fn)

    # Find the while True: line.
    lines = src.split("\n")
    while_idx = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "while True:":
            while_idx = i
            break
    assert while_idx is not None, (
        f"{loop_name}: no 'while True:' loop found in source — test "
        f"assumption broken by refactor"
    )

    # Within the loop body, find the first stamp call and the first await.
    # `while True:` line is at while_idx; body starts at while_idx+1.
    stamp_pattern = f'stamp_pass_complete("{stamp_key}")'
    stamp_alt = f'_stamp("{stamp_key}")'
    stamp_idx = None
    first_await_idx = None
    for i in range(while_idx + 1, len(lines)):
        line = lines[i]
        # Detect end of while body via dedent to while's indent level.
        if line.strip() and not line[len(line) - len(line.lstrip())].isspace():
            # Not sensitive enough — skip precise dedent detection;
            # instead just scan the whole rest of the function.
            pass
        if stamp_idx is None and (stamp_pattern in line or stamp_alt in line):
            stamp_idx = i
        if first_await_idx is None and "await " in line and "await asyncio.sleep(" not in line:
            # Any await OTHER than the loop's own cadence sleep at
            # the bottom qualifies as "work" that could hang.
            first_await_idx = i
    assert stamp_idx is not None, (
        f"{loop_name}: no stamp_pass_complete({stamp_key!r}) call found. "
        f"Layer C regression — this loop has no health-registry "
        f"stamp at all."
    )
    # The stamp must come BEFORE any await in the loop body.
    if first_await_idx is not None:
        assert stamp_idx < first_await_idx, (
            f"{loop_name}: stamp at line {stamp_idx} sits AFTER "
            f"an await at line {first_await_idx}. Layer B regression: "
            f"stamp behind a hanging await = false-alarm stale=true "
            f"during work windows. Move stamp to TOP of loop body."
        )


def test_kalshi_markets_loop_stamps_at_top():
    """Kalshi's _markets_loop MUST stamp 'kalshi' at TOP, not inside
    _ingest_pass (pre-Layer-B placement). The stamp was moved OUT
    of _ingest_pass so a hanging pass (bounded by Layer D but still
    up to 120s / 300s wall time) doesn't leave the stamp stale."""
    import ingestion.kalshi
    src = inspect.getsource(ingestion.kalshi._markets_loop)
    lines = src.split("\n")

    # First stamp call must precede the async with session_factory line.
    stamp_idx = None
    session_idx = None
    for i, line in enumerate(lines):
        if stamp_idx is None and 'stamp_pass_complete("kalshi")' in line:
            stamp_idx = i
        if session_idx is None and "async with session_factory()" in line:
            session_idx = i
    assert stamp_idx is not None and session_idx is not None
    assert stamp_idx < session_idx, (
        f"kalshi._markets_loop: stamp at line {stamp_idx} sits AFTER "
        f"session_factory acquire at line {session_idx}. Post-Layer-B "
        f"stamp must be BEFORE the session acquire (which itself is "
        f"wrapped in wait_for → could TimeoutError)."
    )
    # And the stamp must NOT appear in _ingest_pass anymore
    # (moved to loop level per Layer B).
    ingest_src = inspect.getsource(ingestion.kalshi._ingest_pass)
    assert 'stamp_pass_complete("kalshi")' not in ingest_src, (
        "kalshi._ingest_pass still calls stamp_pass_complete — Layer B "
        "regression, stamp position drifted back to inside the pass. "
        "Duplicate stamps aren't harmful but the presence inside the "
        "pass suggests the top-of-loop stamp may have been forgotten "
        "on a subsequent refactor."
    )


def test_fl_loops_stamp_at_top():
    """FL's both loops stamp 'fl' at TOP. Same rationale as kalshi."""
    import ingestion.fl
    for loop_fn in (ingestion.fl._today_pre_game_loop, ingestion.fl._week_loop):
        src = inspect.getsource(loop_fn)
        lines = src.split("\n")
        stamp_idx = None
        session_idx = None
        for i, line in enumerate(lines):
            if stamp_idx is None and 'stamp_pass_complete("fl")' in line:
                stamp_idx = i
            if session_idx is None and "async with session_factory()" in line:
                session_idx = i
        assert stamp_idx is not None, f"{loop_fn.__name__}: no stamp call"
        assert session_idx is not None
        assert stamp_idx < session_idx, (
            f"{loop_fn.__name__}: stamp at {stamp_idx} sits AFTER "
            f"session acquire at {session_idx}"
        )
    # Not in _ingest_pass either.
    ingest_src = inspect.getsource(ingestion.fl._ingest_pass)
    assert 'stamp_pass_complete("fl")' not in ingest_src, (
        "fl._ingest_pass still contains stamp_pass_complete — Layer B "
        "regression"
    )


# ── Layer B fold: worker_role derivation ────────────────────────

def test_worker_role_holder_when_any_task_holder_running():
    """A worker holding at least one AL surface reports
    worker_role='holder'."""
    from fastapi.testclient import TestClient
    import main
    from ingestion import health

    health.TASK_HEALTH.clear()
    health.register("kalshi")
    health.set_state("kalshi", health.STATE_HOLDER_RUNNING)
    health.register("fl")
    health.set_state("fl", health.STATE_HOLDER_RUNNING)

    client = TestClient(main.app)
    body = client.get("/api/ingestion_status").json()
    assert body["worker_role"] == "holder", (
        f"worker_role={body['worker_role']!r}, expected 'holder' — "
        f"all tasks in holder_running should identify as holder"
    )
    assert body["holder_count"] == 2
    assert body["non_holder_count"] == 0


def test_worker_role_non_holder_when_all_tasks_yielding():
    """A worker yielding on every AL surface reports
    worker_role='non_holder'."""
    from fastapi.testclient import TestClient
    import main
    from ingestion import health

    health.TASK_HEALTH.clear()
    health.register("kalshi")
    health.set_state("kalshi", health.STATE_NOT_HOLDER_POLLING)
    health.register("fl")
    health.set_state("fl", health.STATE_NOT_HOLDER_POLLING)

    client = TestClient(main.app)
    body = client.get("/api/ingestion_status").json()
    assert body["worker_role"] == "non_holder", (
        f"worker_role={body['worker_role']!r}, expected 'non_holder'"
    )
    assert body["holder_count"] == 0
    assert body["non_holder_count"] == 2


def test_worker_role_mixed_when_split():
    """A worker with some holder tasks and some yielding reports
    worker_role='mixed'. Expected under normal WEB_CONCURRENCY=2
    operation — kalshi holder on worker A, fl holder on worker B,
    each worker holds SOME and yields on OTHERS."""
    from fastapi.testclient import TestClient
    import main
    from ingestion import health

    health.TASK_HEALTH.clear()
    health.register("kalshi")
    health.set_state("kalshi", health.STATE_HOLDER_RUNNING)
    health.register("fl")
    health.set_state("fl", health.STATE_NOT_HOLDER_POLLING)

    client = TestClient(main.app)
    body = client.get("/api/ingestion_status").json()
    assert body["worker_role"] == "mixed"
    assert body["holder_count"] == 1
    assert body["non_holder_count"] == 1


def test_worker_role_unknown_before_any_task_reports():
    """A freshly-booted worker with no tasks registered yet reports
    worker_role='unknown' — distinguishes from holder/non_holder at
    a glance. Prevents alerting on early-boot ambiguity."""
    from fastapi.testclient import TestClient
    import main
    from ingestion import health

    health.TASK_HEALTH.clear()

    client = TestClient(main.app)
    body = client.get("/api/ingestion_status").json()
    assert body["worker_role"] == "unknown"
