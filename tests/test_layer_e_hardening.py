"""Tests for PR-Layer-E — commit-after-acquire + AL heartbeat +
LockGripLost + per-pass wait_for on all main.py holder loops.

Layer E is the fifth PR in the Day-63/64/65 incident-class sequence
(C → A → D → B → E). It closes the mutual-exclusion failure the
Day-65 operator log surfaced: BOTH workers reporting holder_running
for all AL surfaces with independently-advancing stamps and
InterfaceError("connection is closed") on the AL session.

Root cause (Layer A+B+D interaction class):
  1. NullPool AL session (B) was left BEGIN-open after try_acquire
     (no commit). Server-side idle_in_transaction_session_timeout=60s
     terminated the connection. Session-level advisory lock silently
     released. Other worker's Layer-A supervise re-race acquired it.
     Dual holders, both stamping, one on a dead session.
     → Fixed by E.1 (commit-after-acquire).
  2. Even with the commit, Neon proxy TCP-idle reap (~5-10min) causes
     same outcome. Dead-holder loop keeps stamping until next execute.
     → Fixed by E.2 (heartbeat SELECT 1 every 20s + LockGripLost
     sentinel + supervise re-race).
  3. score_flush's D wrap covered only the BOOT path (D.1b) — no
     per-pass wait_for. Hang inside sync_scores_to_db → loop stuck →
     stamp fires once and never advances → operator sees stale
     430-471s with ZERO pass_timeout log lines (there's no wait_for
     to raise TimeoutError).
     → Fixed by E.3 (per-pass wait_for on all 4 main.py holder loops).
  4. E.4 audit confirmed price_prune, multi_stage_disc, bracket_walk
     had the same missing per-pass wait_for. All fixed in this PR.
"""
from __future__ import annotations

import asyncio
import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── E.1: commit-after-acquire ───────────────────────────────────

def test_layer_e_al_session_commits_after_acquire():
    """The AL session MUST commit immediately after try_acquire returns
    True. Pre-E, the implicit BEGIN stayed open forever; server-side
    idle_in_transaction_session_timeout=60s then TERMINATED the
    connection, silently releasing the session-level advisory lock.
    Regression to no-commit reopens the Day-65 dual-holder failure."""
    from ingestion.base import acquire_lock_session_bounded
    src = inspect.getsource(acquire_lock_session_bounded)
    # Must call session.commit() inside _do_acquire after try_acquire
    # returns True. Guarded by `if got_lock:` — no point committing on
    # a lost race (session is about to close anyway).
    assert "await session.commit()" in src, (
        "acquire_lock_session_bounded no longer commits after acquire; "
        "regression reopens the Day-65 dual-holder failure via "
        "idle_in_transaction_session_timeout=60s"
    )
    assert "if got_lock:" in src, (
        "commit-after-acquire must be guarded on got_lock=True; a "
        "commit on a lost race is a wasted round-trip"
    )


@pytest.mark.asyncio
async def test_layer_e_acquire_calls_commit_when_lock_acquired():
    """Behavioral test: with a mocked session, verify session.commit()
    is called when try_acquire returns True."""
    from ingestion.base import acquire_lock_session_bounded
    from sqlalchemy.ext.asyncio import AsyncSession

    mock_session = MagicMock(spec=AsyncSession)
    mock_session.execute = AsyncMock(
        return_value=MagicMock(scalar=MagicMock(return_value=True)),
    )
    mock_session.commit = AsyncMock()

    mock_session_cm = MagicMock()
    mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_cm.__aexit__ = AsyncMock(return_value=None)

    def _factory():
        return mock_session_cm

    session_cm, session, got_lock = await acquire_lock_session_bounded(
        _factory, 0xDEADBEEF, log_name="test",
        timeout_s=5.0, use_null_pool=False,
    )

    assert got_lock is True
    assert mock_session.commit.await_count == 1, (
        f"session.commit() called {mock_session.commit.await_count} "
        f"times; expected exactly 1 (E.1 commit-after-acquire)"
    )


@pytest.mark.asyncio
async def test_layer_e_acquire_does_not_commit_when_lock_not_acquired():
    """Behavioral test: on lost race (try_acquire returns False), do
    NOT commit — the session is about to close anyway and a wasted
    round-trip has no benefit."""
    from ingestion.base import acquire_lock_session_bounded
    from sqlalchemy.ext.asyncio import AsyncSession

    mock_session = MagicMock(spec=AsyncSession)
    mock_session.execute = AsyncMock(
        return_value=MagicMock(scalar=MagicMock(return_value=False)),
    )
    mock_session.commit = AsyncMock()

    mock_session_cm = MagicMock()
    mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_cm.__aexit__ = AsyncMock(return_value=None)

    def _factory():
        return mock_session_cm

    session_cm, session, got_lock = await acquire_lock_session_bounded(
        _factory, 0xDEADBEEF, log_name="test",
        timeout_s=5.0, use_null_pool=False,
    )

    assert got_lock is False
    assert mock_session.commit.await_count == 0, (
        f"session.commit() called {mock_session.commit.await_count} "
        f"times on lost race; expected 0"
    )


# ── E.2: heartbeat + LockGripLost ──────────────────────────────

@pytest.mark.asyncio
async def test_layer_e_heartbeat_raises_lockgriplost_on_interface_error():
    """Behavioral contract for holder_heartbeat: when the heartbeat's
    SELECT 1 raises (simulating Neon-proxy reap / dead socket),
    heartbeat cancels the parent task; the CM re-raises as
    LockGripLost so supervise() can catch it distinctly and re-race."""
    from ingestion.base import (
        holder_heartbeat, LockGripLost, AL_HEARTBEAT_INTERVAL_S,
    )

    class _FakeInterfaceError(Exception):
        """Stand-in for sqlalchemy.exc.InterfaceError."""
        pass

    call_count = {"n": 0}

    async def _fake_execute(*_args, **_kwargs):
        call_count["n"] += 1
        raise _FakeInterfaceError("connection is closed")

    mock_session = MagicMock()
    mock_session.execute = _fake_execute

    async def _hold():
        # Body runs forever until heartbeat cancels via grip-loss.
        async with holder_heartbeat(mock_session, 0xF104, "test_task"):
            await asyncio.sleep(60.0)   # heartbeat will interrupt this

    # Use monkeypatch to shrink heartbeat interval so the test is fast.
    with patch("ingestion.base.AL_HEARTBEAT_INTERVAL_S", 0.05):
        with pytest.raises(LockGripLost) as exc_info:
            await asyncio.wait_for(_hold(), timeout=2.0)

    assert "grip loss" in str(exc_info.value).lower()
    assert "test_task" in str(exc_info.value)
    assert "_FakeInterfaceError" in str(exc_info.value)
    assert call_count["n"] >= 1, "heartbeat should have called execute at least once"


@pytest.mark.asyncio
async def test_layer_e_heartbeat_cancels_cleanly_on_normal_exit():
    """Normal-completion path: heartbeat is cancelled cleanly when the
    holder body exits, without raising LockGripLost."""
    from ingestion.base import holder_heartbeat

    mock_session = MagicMock()
    mock_session.execute = AsyncMock(return_value=MagicMock())

    # Body exits cleanly; heartbeat must be teared down.
    with patch("ingestion.base.AL_HEARTBEAT_INTERVAL_S", 0.05):
        async with holder_heartbeat(mock_session, 0xF104, "test_task"):
            await asyncio.sleep(0.15)   # allow at least one heartbeat


@pytest.mark.asyncio
async def test_layer_e_external_cancel_does_not_become_lockgriplost():
    """When supervise (or any other party) cancels the parent task
    while the heartbeat is still healthy, the CM must propagate
    CancelledError unchanged — NOT translate it into LockGripLost.
    Grip-loss translation must be gated on grip_ref['lost']."""
    from ingestion.base import holder_heartbeat, LockGripLost

    mock_session = MagicMock()
    mock_session.execute = AsyncMock(return_value=MagicMock())

    task_ref = {"t": None}

    async def _hold():
        async with holder_heartbeat(mock_session, 0xF104, "test_task"):
            task_ref["t"] = asyncio.current_task()
            await asyncio.sleep(10.0)   # will be externally cancelled

    with patch("ingestion.base.AL_HEARTBEAT_INTERVAL_S", 0.05):
        t = asyncio.create_task(_hold())
        await asyncio.sleep(0.15)   # let heartbeat get running
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t

    # If translation ran wrongly, we'd have caught LockGripLost above,
    # not CancelledError. The pytest.raises above is the guard.


# ── E.2 wiring: supervise catches LockGripLost as a peer of NotLockHolder ───

def test_layer_e_supervise_catches_lockgriplost_before_generic_exception():
    """supervise() MUST have a `except LockGripLost` handler BEFORE
    `except Exception` — peer of NotLockHolder. Regression to
    falling-through to generic Exception counts grip-loss as a crash
    (incorrect — infrastructural signal, not bug), triggers exponential
    backoff (wrong retry cadence), and pollutes the crash_alert_threshold.
    Class contract: LockGripLost, like NotLockHolder, is a first-class
    sentinel with distinct log line + STATE_NOT_HOLDER_POLLING semantics."""
    from ingestion.base import supervise
    src = inspect.getsource(supervise)

    # LockGripLost handler must exist.
    assert "except LockGripLost" in src, (
        "supervise() no longer catches LockGripLost specifically; "
        "regression makes grip-loss look like a crash and pollutes "
        "crash_alert_threshold"
    )

    # LockGripLost handler must precede the generic Exception handler
    # (Python evaluates except clauses in order; misordering makes
    # `except Exception` swallow LockGripLost first).
    idx_grip = src.find("except LockGripLost")
    idx_generic = src.find("except Exception as exc:")
    assert idx_grip < idx_generic, (
        "except LockGripLost must precede except Exception in supervise(); "
        "misordering makes generic Exception swallow the sentinel first"
    )

    # NotLockHolder is peer sentinel — both must be caught before Exception.
    idx_nlh = src.find("except NotLockHolder")
    assert idx_nlh < idx_generic and idx_grip < idx_generic, (
        "Both NotLockHolder and LockGripLost must precede generic "
        "Exception — they're first-class infrastructural signals"
    )

    # Grip-loss path must NOT increment crash_times (it's not a crash).
    grip_block = src[idx_grip:idx_generic]
    assert "crash_times.append" not in grip_block, (
        "LockGripLost handler must NOT append to crash_times — grip loss "
        "is infrastructural, not a bug, and shouldn't count against "
        "crash_alert_threshold"
    )

    # Grip-loss path must sleep + continue (re-race), not return/raise.
    assert "continue" in grip_block, (
        "LockGripLost handler must `continue` back to the while True "
        "re-race loop; regression to `return` or `raise` leaves the "
        "workload dormant"
    )


# ── E.3: score_flush per-pass wait_for wrap ────────────────────

def test_layer_e_score_flush_pass_timeout_wraps_work():
    """_score_flush_loop MUST wrap its per-iteration work in
    asyncio.wait_for(..., timeout=pass_timeout_for(first_pass)).
    Pre-E, the loop had NO wait_for wrap — a hang inside
    sync_scores_to_db blocked the loop forever; stamp-at-top fired
    once and never advanced (operator's Day-65 evidence: stale
    430-471s with zero pass_timeout log lines). D.1b covered only
    the BOOT path, not per-pass work."""
    import main
    src = inspect.getsource(main._score_flush_loop)

    assert "asyncio.wait_for" in src, (
        "_score_flush_loop no longer wraps per-pass work in "
        "asyncio.wait_for; regression reopens Day-65 F104 stale-with-"
        "no-pass_timeout failure"
    )
    assert "pass_timeout_for" in src, (
        "_score_flush_loop must use pass_timeout_for(first_pass) to "
        "get the boot-vs-steady cadence"
    )
    assert "ingestion.task.pass_timeout" in src, (
        "TimeoutError path must emit the standard "
        "'ingestion.task.pass_timeout task=score_flush ...' log line"
    )
    assert "holder_heartbeat" in src, (
        "_score_flush_loop must be wrapped in holder_heartbeat CM "
        "so grip loss on the AL session triggers supervise re-race"
    )


# ── E.4 audit: all 4 main.py-hosted holder loops have wait_for + heartbeat ───

@pytest.mark.parametrize("func_name,task_name", [
    ("_score_flush_loop",                     "score_flush"),
    ("_price_prune_loop",                     "price_prune"),
    ("_multi_stage_discovery_loop_body",      "multi_stage_disc"),
    ("_tournament_bracket_warm_loop_body",    "bracket_walk"),
])
def test_layer_e_all_main_loops_have_pass_timeout_wrap(func_name, task_name):
    """E.4 audit: every main.py-hosted holder loop MUST wrap its
    per-pass or per-op blocking work in asyncio.wait_for +
    pass_timeout_for. Pre-E, all four lacked this — kalshi.run /
    fl.run have it (Layer D), but main.py loops used the D.1b boot-
    path helper without extending the pattern to per-pass.

    Regression guard: adding a new main.py holder loop without a
    per-pass wait_for wrap trips this test (add the new func_name
    to the parametrize list once the loop is introduced)."""
    import main
    fn = getattr(main, func_name, None)
    assert fn is not None, f"main.{func_name} not found"
    src = inspect.getsource(fn)

    assert "asyncio.wait_for" in src, (
        f"main.{func_name} has no asyncio.wait_for wrap — regression "
        f"to unbounded per-pass work reopens the F104-class hang "
        f"failure (silent staleness, no pass_timeout log line)"
    )
    assert "pass_timeout_for" in src, (
        f"main.{func_name} does not use pass_timeout_for — must use "
        f"the standard boot/steady cadence for consistency with "
        f"kalshi.run and fl.run"
    )
    assert f"task={task_name}" in src, (
        f"main.{func_name} pass_timeout log line must include "
        f"task={task_name} so operators can grep it in production"
    )


@pytest.mark.parametrize("wrapper_func_name,task_name", [
    ("_score_flush_loop",                    "score_flush"),
    ("_price_prune_loop",                    "price_prune"),
    ("_multi_stage_discovery_loop",          "multi_stage_disc"),
    ("_tournament_bracket_warm_loop",        "bracket_walk"),
])
def test_layer_e_all_main_holder_wrappers_use_holder_heartbeat(
    wrapper_func_name, task_name,
):
    """E.2 wiring: every main.py holder loop wrapper (the fn that
    calls acquire_lock_session_bounded) MUST wrap its body in
    holder_heartbeat CM. Regression re-opens the Neon-proxy TCP-idle
    reap failure mode: dead-holder loop keeps stamping until next
    real execute surfaces the InterfaceError."""
    import main
    fn = getattr(main, wrapper_func_name, None)
    assert fn is not None, f"main.{wrapper_func_name} not found"
    src = inspect.getsource(fn)

    assert "holder_heartbeat" in src, (
        f"main.{wrapper_func_name} does not use holder_heartbeat CM; "
        f"regression re-opens Neon-proxy TCP-idle reap failure"
    )
    assert f'"{task_name}"' in src, (
        f"main.{wrapper_func_name} must pass task={task_name!r} to "
        f"holder_heartbeat for log-line grep discipline"
    )
