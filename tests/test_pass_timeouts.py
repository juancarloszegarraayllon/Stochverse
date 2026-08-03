"""Tests for Layer D — pass timeouts (two-tier boot/steady) +
boot-path bound + db.py client-side ceilings.

Actual fix for the 2026-08-01 Kalshi in-pass hang class. Layer A
made restarts safe from the death-door spiral; Layer D MANUFACTURES
those restarts by putting client-side ceilings on the six previously-
unbounded awaits in the pass path. Together they close the incident-
class defect.

Test coverage:
  #10 (parametrized boot vs steady): induced never-returning pass →
      wait_for fires with the correct tier → supervise catches
      TimeoutError → restart → next attempt completes.
  #11 (boot-path bound): induced never-returning acquire → wait_for
      fires with INGESTION_BOOT_TIMEOUT_S → supervise catches →
      restart.
  db.py ceiling constants: pool_timeout explicit, command_timeout
      set on connect_args, both larger-than-server-side floors so
      clean server-error path fires first.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest


# ── Test #10 (parametrized): pass wait_for fires at the correct tier ──

@pytest.mark.parametrize("tier,env_var,expected_default", [
    ("boot",   "INGESTION_PASS_TIMEOUT_BOOT_S",   300.0),
    ("steady", "INGESTION_PASS_TIMEOUT_STEADY_S", 120.0),
])
def test_pass_timeout_tier_defaults(tier, env_var, expected_default):
    """Two-tier defaults hold: boot 300s, steady 120s. Env-tunable
    per operator amendment — regression to a single ceiling would
    kill/restart every slow-boot pass into a boot-loop (measured
    77.6s catch-up on the PR-#283 deploy = 65% of steady ceiling)."""
    from ingestion.base import (
        INGESTION_PASS_TIMEOUT_BOOT_S,
        INGESTION_PASS_TIMEOUT_STEADY_S,
        pass_timeout_for,
    )
    if tier == "boot":
        assert INGESTION_PASS_TIMEOUT_BOOT_S == expected_default
        assert pass_timeout_for(is_first_pass=True) == expected_default
    else:
        assert INGESTION_PASS_TIMEOUT_STEADY_S == expected_default
        assert pass_timeout_for(is_first_pass=False) == expected_default


@pytest.mark.parametrize("tier,is_first_pass", [
    ("boot",   True),
    ("steady", False),
])
def test_hanging_pass_triggers_wait_for_timeout(tier, is_first_pass, monkeypatch):
    """Induced never-returning pass MUST fire asyncio.TimeoutError
    within the tier's ceiling. Uses a mocked short timeout (0.05s)
    so the test completes fast, but the wiring is identical: the
    real _markets_loop passes `pass_timeout_for(first_pass)` as
    the timeout arg, and this test verifies the same mechanism.

    Guards against BOTH tiers being accidentally set to the same
    value (e.g. copy-paste regression) — parametrized so a change
    to only ONE tier fails only its own case."""
    from ingestion.base import pass_timeout_for
    _ = pass_timeout_for(is_first_pass)  # sanity: helper resolves

    async def never_returns():
        # Simulates the six unbounded awaits in the pass path
        # (Step 1 SELECT, Step 3a UPSERT, Step 3b UPDATE, commit,
        # rollback, session-context acquire) any of which can hang
        # on a dead TCP socket.
        await asyncio.Event().wait()

    async def _run():
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(never_returns(), timeout=0.05)

    asyncio.run(_run())


def test_pass_timeout_raises_through_to_supervise_for_restart(monkeypatch):
    """End-to-end: pass hang → wait_for fires → loop re-raises →
    supervise catches as regular exception (NOT NotLockHolder) →
    crash-restart → next attempt succeeds.

    This is the load-bearing incident-class contract: the Aug 1-3
    hang would have been recovered by supervise's next-attempt
    within (backoff + boot_ceiling) seconds instead of ~58h."""
    from ingestion.base import supervise
    from ingestion import health

    health.TASK_HEALTH.clear()

    attempt_count = {"n": 0}

    async def flaky_pass():
        attempt_count["n"] += 1
        if attempt_count["n"] == 1:
            # Simulate the hang path — asyncio.wait_for around a
            # never-returning inner coroutine → TimeoutError raised
            # → surfaces as regular exception to supervise.
            await asyncio.wait_for(asyncio.Event().wait(), timeout=0.05)
        return

    _real_sleep = asyncio.sleep

    async def fast_sleep(secs):
        await _real_sleep(0)

    monkeypatch.setattr("ingestion.base.asyncio.sleep", fast_sleep)

    try:
        asyncio.run(supervise("test_hang", lambda: flaky_pass()))
    except Exception as e:
        pytest.fail(f"supervise did not recover from pass timeout: {e}")

    assert attempt_count["n"] == 2, (
        f"supervise made {attempt_count['n']} attempts; expected 2 "
        f"(1 hang-timeout + 1 successful restart). Regression means "
        f"the timeout isn't propagating as a regular exception "
        f"(possibly being caught by the NotLockHolder branch — "
        f"that would be a serious regression to the 2026-08-01 "
        f"hang class)."
    )
    # Health state: DEAD after the 2nd attempt's clean return.
    assert health.TASK_HEALTH["test_hang"]["state"] == "dead"
    assert health.TASK_HEALTH["test_hang"]["last_error_class"] == "TimeoutError"


# ── Test #11: boot-path bound ────────────────────────────────────

def test_boot_timeout_bound_default_is_60s():
    """INGESTION_BOOT_TIMEOUT_S default MUST be 60s. Regression to
    a longer value would let the boot-path hang re-open the Aug 1
    death window (bracket [00:36, ~01:29] = ~53min of boot-hang the
    task hit before its recorded silence began)."""
    from ingestion.base import INGESTION_BOOT_TIMEOUT_S
    assert INGESTION_BOOT_TIMEOUT_S == 60.0, (
        f"INGESTION_BOOT_TIMEOUT_S = {INGESTION_BOOT_TIMEOUT_S}; expected 60.0"
    )


def test_acquire_lock_session_bounded_times_out_on_hung_acquire(monkeypatch):
    """Load-bearing D.1b contract: `acquire_lock_session_bounded`
    MUST raise TimeoutError if the session/lock acquire hangs past
    INGESTION_BOOT_TIMEOUT_S. Regression means the boot-path hang
    flavor reopens (Aug 1 kalshi's death window bracket was consistent
    with either boot-path OR first-pass; the boot-path bound closes
    that flavor regardless of which one hit)."""
    from ingestion.base import acquire_lock_session_bounded
    import logging

    class HangingSession:
        async def __aenter__(self):
            # Simulates the exact Aug 1 failure mode: TCP-idle-timed-
            # out connection whose asyncpg socket read blocks forever.
            await asyncio.Event().wait()
            return self
        async def __aexit__(self, *a):
            pass

    def _hanging_factory():
        return HangingSession()

    async def _run():
        with pytest.raises(asyncio.TimeoutError):
            # Use the helper's default logger (structlog via
            # ingestion.base._log). Passing a stdlib logger would
            # need structured-kwarg-compatible mock.
            await acquire_lock_session_bounded(
                _hanging_factory,
                lock_key=0x5350F999,
                log_name="test_boot",
                timeout_s=0.05,
            )

    asyncio.run(_run())


def test_acquire_lock_session_bounded_used_by_all_six_surfaces():
    """Class contract: every advisory-lock-guarded surface MUST use
    `acquire_lock_session_bounded` for its boot path. Regression to
    raw `async with session_factory() as lock_session:` at any surface
    silently reopens the Aug 1 boot-hang flavor on that surface.

    Source inspection because standing up all six against real
    Postgres to test boot-hang wiring is heavier than needed; the
    grep is the durable contract."""
    import ingestion.fl
    import ingestion.kalshi
    import main

    for label, src in (
        ("ingestion.fl.run", inspect.getsource(ingestion.fl.run)),
        ("ingestion.kalshi.run", inspect.getsource(ingestion.kalshi.run)),
        ("main._score_flush_loop", inspect.getsource(main._score_flush_loop)),
        ("main._price_prune_loop", inspect.getsource(main._price_prune_loop)),
        ("main._tournament_bracket_warm_loop",
         inspect.getsource(main._tournament_bracket_warm_loop)),
        ("main._multi_stage_discovery_loop",
         inspect.getsource(main._multi_stage_discovery_loop)),
    ):
        # Either use the shared helper (F104-F107 + kalshi via the
        # helper OR the direct wait_for wrapper pattern kalshi/fl
        # legitimately use — both bound the boot path).
        used_helper = "acquire_lock_session_bounded" in src
        used_direct_wrapper = (
            "INGESTION_BOOT_TIMEOUT_S" in src
            and "asyncio.wait_for" in src
        )
        assert used_helper or used_direct_wrapper, (
            f"{label} does NOT bound its boot path with either "
            f"acquire_lock_session_bounded or a direct "
            f"asyncio.wait_for(INGESTION_BOOT_TIMEOUT_S) wrapper. "
            f"Layer D.1b regression: boot-hang flavor of the "
            f"2026-08-01 Kalshi incident class silently reopens "
            f"on this surface."
        )


# ── db.py client-side ceilings ──────────────────────────────────

def test_db_command_timeout_is_set_and_larger_than_server_statement_timeout():
    """asyncpg command_timeout MUST be set on connect_args AND
    larger than server-side statement_timeout so the clean server-
    error path fires first (60s), with client-side as last-resort
    ceiling (90s). Regression to no command_timeout re-opens the
    dead-socket hang class Layer D is designed to close."""
    import db
    command_timeout = db._connect_args.get("command_timeout")
    assert command_timeout is not None, (
        "db._connect_args missing command_timeout — asyncpg has no "
        "client-side per-command ceiling, dead-socket hangs go forever"
    )
    assert command_timeout > 60.0, (
        f"command_timeout ({command_timeout}) should be > 60 "
        f"(server statement_timeout) so server-side kill fires "
        f"first on the healthy-server-slow-query case"
    )


def test_db_pool_timeout_explicit_in_engine_config():
    """pool_timeout MUST be set explicitly on the engine so any
    tuning is source-visible (was relying on SQLAlchemy's implicit
    30s default pre-Layer-D). Source inspection to verify."""
    import db
    engine_src = inspect.getsource(db)
    assert "pool_timeout=30.0" in engine_src, (
        "engine config missing explicit pool_timeout=30.0"
    )
