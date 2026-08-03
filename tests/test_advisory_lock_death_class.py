"""Tests for Layer A — NotLockHolder sentinel + supervise re-race
branch + register-all-at-startup + worker_id in endpoint payload.

Class-defect guard: pre-Layer-A, every advisory-lock-guarded task's
non-holder path returned cleanly; supervise treated the clean return
as `ingestion.task.complete` and exited without restart; task DEAD
from boot on the non-holder worker. Any subsequent holder-crash
(supervise-restart landing on a pool-leaked lock) then killed BOTH
workers' tasks silently — root cause of the 2026-08-01 Kalshi
persistence outage.

Post-Layer-A, non-holder paths raise NotLockHolder; supervise catches
this SPECIFICALLY (before the generic Exception handler), transitions
health state to `not_holder_polling`, sleeps, re-invokes coro_factory
to re-race. Preserves a live non-holder task that takes over on
holder-death.

The class-defect test #1 fires the same contract against EVERY
supervised surface (fl, kalshi, score_flush, price_prune, bracket_walk,
multi_stage_disc) so any future task added to the AL family gets
tested by adding one line to the parametrize decorator.
"""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import patch, AsyncMock, MagicMock

import pytest


# ── Class defect: non-holder path must raise NotLockHolder ─────

@pytest.mark.parametrize("surface_name", [
    "fl",
    "kalshi",
    "score_flush",
    "price_prune",
    "bracket_walk",
    "multi_stage_disc",
])
def test_non_holder_path_raises_NotLockHolder(surface_name, monkeypatch):
    """Class contract: every advisory-lock-guarded task's non-holder
    path MUST raise NotLockHolder — NEVER clean-return.

    Under pre-Layer-A code, all six surfaces returned cleanly; this
    parametrized test asserts each one now raises. Regression to
    clean-return on ANY surface silently reopens the 2026-08-01
    death door for that task. Adding a new AL task = add one line
    to the parametrize decorator + one `raise NotLockHolder()` in
    the code = the test wires up automatically.

    Uses source inspection (grep for `raise NotLockHolder` on the
    non-holder branch) because standing up a real Postgres +
    advisory-lock race to test all six functions runtime-wise is
    heavier than the contract deserves. The grep is the durable
    contract: if the raise line exists on the non-holder path, the
    supervise-catches-NotLockHolder branch handles the rest."""
    import inspect

    if surface_name == "fl":
        import ingestion.fl as mod
        src = inspect.getsource(mod.run)
    elif surface_name == "kalshi":
        import ingestion.kalshi as mod
        src = inspect.getsource(mod.run)
    elif surface_name == "score_flush":
        import main
        src = inspect.getsource(main._score_flush_loop)
    elif surface_name == "price_prune":
        import main
        src = inspect.getsource(main._price_prune_loop)
    elif surface_name == "bracket_walk":
        import main
        src = inspect.getsource(main._tournament_bracket_warm_loop)
    elif surface_name == "multi_stage_disc":
        import main
        src = inspect.getsource(main._multi_stage_discovery_loop)
    else:
        pytest.fail(f"unknown surface_name={surface_name}")

    assert "raise NotLockHolder()" in src, (
        f"{surface_name}'s non-holder path does NOT raise NotLockHolder. "
        f"Regression to pre-Layer-A clean-return silently reopens the "
        f"2026-08-01 death door — supervise would treat as complete "
        f"and exit permanently, leaving the task DEAD on the non-holder "
        f"worker until the container recycles."
    )
    # Corollary — the advisory-lock non-holder branch's log line must
    # use the "not_holder" vocabulary (post-Layer-A), NOT "skipping"
    # (pre-Layer-A). Note: the DATABASE_URL absence path in the
    # F104-F107 loops legitimately uses `.info("skipping — no
    # DATABASE_URL")` and is not part of this contract; only the
    # advisory-lock branch's language is being audited here.
    assert "not_holder" in src, (
        f"{surface_name}'s advisory-lock non-holder branch does NOT "
        f"log the 'not_holder' vocabulary. Post-Layer-A semantics is "
        f"'task is yielding, will re-race' — 'skipping' misleads "
        f"operators reading logs into thinking the task exited."
    )


# ── supervise re-race branch fires on NotLockHolder ────────────

def test_supervise_reraces_on_NotLockHolder(monkeypatch):
    """supervise MUST catch NotLockHolder specifically, transition
    health state to `not_holder_polling`, sleep the poll interval,
    and re-invoke coro_factory. Without this branch, the incident-
    class defect reopens even with the raise-side of Layer A in
    place: an unhandled NotLockHolder just crashes into the generic
    Exception handler, exponential-backoff-restart, same failure."""
    from ingestion.base import supervise, NotLockHolder
    from ingestion import health

    health.TASK_HEALTH.clear()

    attempt_count = {"n": 0}

    async def flaky_coro():
        attempt_count["n"] += 1
        if attempt_count["n"] <= 3:
            raise NotLockHolder()
        # Fourth attempt succeeds → clean return → supervise exits.
        return

    # Mock asyncio.sleep so the test runs in <1s.
    _real_sleep = asyncio.sleep

    async def fast_sleep(secs):
        # Only the actual re-race wait matters for the semantics;
        # allow other sleeps (e.g. inside coro_factory) to be real.
        await _real_sleep(0)

    monkeypatch.setattr("ingestion.base.asyncio.sleep", fast_sleep)
    monkeypatch.setenv("INGESTION_NOT_HOLDER_POLL_S", "0.01")

    try:
        asyncio.run(supervise("test_race", lambda: flaky_coro()))
    except Exception as e:
        pytest.fail(f"supervise raised unexpectedly: {e}")

    assert attempt_count["n"] == 4, (
        f"supervise made {attempt_count['n']} attempts; expected 4 "
        f"(3 NotLockHolder yields + 1 clean-return exit). Regression "
        f"means either NotLockHolder isn't being caught, or supervise "
        f"exits early instead of re-invoking coro_factory."
    )
    # Final state after clean return: DEAD (pre-Layer-A residual
    # semantics preserved for the "task actually finished" case;
    # NotLockHolder is the only clean-completion signal that shouldn't
    # be treated as terminal, and that's exactly what the branch
    # translates).
    assert health.TASK_HEALTH["test_race"]["state"] == "dead"


def test_supervise_still_restarts_on_regular_exception(monkeypatch):
    """Regression guard: the NotLockHolder branch must NOT swallow
    regular exceptions. Backoff-restart behavior on Exception must
    continue to fire as before Layer A."""
    from ingestion.base import supervise
    from ingestion import health

    health.TASK_HEALTH.clear()

    attempt_count = {"n": 0}

    async def crashing_coro():
        attempt_count["n"] += 1
        if attempt_count["n"] == 1:
            raise RuntimeError("first-attempt boom")
        return

    _real_sleep = asyncio.sleep  # capture BEFORE monkeypatch

    async def fast_sleep(secs):
        await _real_sleep(0)

    monkeypatch.setattr("ingestion.base.asyncio.sleep", fast_sleep)

    try:
        asyncio.run(supervise("test_crash", lambda: crashing_coro()))
    except Exception as e:
        pytest.fail(f"supervise did not recover from crash: {e}")

    assert attempt_count["n"] == 2, (
        f"supervise made {attempt_count['n']} attempts; expected 2 "
        f"(1 crash + 1 successful restart). NotLockHolder branch "
        f"accidentally swallowed the regular exception."
    )
    entry = health.TASK_HEALTH["test_crash"]
    # Final state DEAD (clean return from attempt 2), but the
    # per-attempt transitions should have logged the crash.
    assert entry["state"] == "dead"


def test_supervise_updates_health_state_through_not_holder_yield(monkeypatch):
    """The NotLockHolder branch must update health state to
    `not_holder_polling` so /api/ingestion_status shows the correct
    liveness signal for the non-holder worker's tasks — that's the
    Layer C observability contract Layer A is designed to feed."""
    from ingestion.base import supervise, NotLockHolder
    from ingestion import health

    health.TASK_HEALTH.clear()

    async def always_not_holder():
        raise NotLockHolder()

    _real_sleep = asyncio.sleep  # capture BEFORE monkeypatch

    async def fast_sleep(secs):
        await _real_sleep(0)

    monkeypatch.setattr("ingestion.base.asyncio.sleep", fast_sleep)
    monkeypatch.setenv("INGESTION_NOT_HOLDER_POLL_S", "0.01")

    # Cancel after a few iterations to exit the infinite re-race.
    async def _run():
        task = asyncio.create_task(supervise("test_ny", lambda: always_not_holder()))
        # Give supervise a few event-loop ticks to enter the yield branch.
        for _ in range(20):
            await asyncio.sleep(0)
            if health.TASK_HEALTH.get("test_ny", {}).get("state") == "not_holder_polling":
                break
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    asyncio.run(_run())
    assert health.TASK_HEALTH.get("test_ny", {}).get("state") == "not_holder_polling", (
        f"supervise did NOT set state=not_holder_polling on NotLockHolder "
        f"— /api/ingestion_status would show wrong liveness for non-holder "
        f"workers. State was: "
        f"{health.TASK_HEALTH.get('test_ny', {}).get('state')!r}"
    )


# ── Layer C snapshot-analysis folds ─────────────────────────────

def test_register_all_populates_every_ingestion_task_at_startup():
    """Snapshot-analysis gap #2: bracket_walk / multi_stage_disc
    were absent from the non-holder worker's snapshot because they
    registered only inside their holder-entry advisory-lock block.

    `register_all()` pre-populates every INGESTION_TASK_NAMES entry
    at startup so absence-from-registry never happens — the operator
    can distinguish 'absent (programming bug)' from 'never started
    (STATE_UNKNOWN)' at a glance."""
    from ingestion import health

    health.TASK_HEALTH.clear()
    health.register_all()

    for name in health.INGESTION_TASK_NAMES:
        assert name in health.TASK_HEALTH, (
            f"register_all() did not populate {name!r} — snapshot-"
            f"analysis regression: absent tasks will look identical "
            f"to never-started tasks on the /api/ingestion_status "
            f"payload"
        )
        assert health.TASK_HEALTH[name]["state"] == health.STATE_UNKNOWN


def test_endpoint_payload_includes_worker_id(monkeypatch):
    """Snapshot-analysis gap #1: /api/ingestion_status is per-worker
    (TASK_HEALTH is process-local); the operator resampled to figure
    out which worker was which. worker_id in the payload makes a
    single sample unambiguous about scope."""
    from fastapi.testclient import TestClient
    import main
    from ingestion import health

    health.TASK_HEALTH.clear()

    client = TestClient(main.app)
    resp = client.get("/api/ingestion_status")
    assert resp.status_code == 200
    body = resp.json()

    assert "worker_id" in body, (
        "endpoint payload must include worker_id (os.getpid()) so "
        "a single sample tells the operator which worker's view "
        "they're looking at — pre-fix required cross-sampling"
    )
    assert isinstance(body["worker_id"], int)
    assert body["worker_id"] > 0
    assert "worker_role" in body


def test_kalshi_status_payload_includes_worker_id():
    """Same worker_id requirement on the per-provider mirror route."""
    from fastapi.testclient import TestClient
    import main
    from ingestion import health

    health.TASK_HEALTH.clear()

    client = TestClient(main.app)
    resp = client.get("/api/kalshi_status")
    assert resp.status_code == 200
    body = resp.json()
    assert "worker_id" in body


# ── Snapshot fold #3: stamp fires every iteration regardless of work ──

def test_score_flush_stamp_fires_every_iteration_not_only_on_work():
    """Snapshot analysis surfaced score_flush at stale=true after
    119s on a fresh boot. The concern was that its stamp fires only
    on flush-with-work → quiet periods false-alarm the staleness flag.

    Source inspection confirms stamp_pass_complete is placed at the
    end of the outer try (post-seed section, not inside the
    `if flashlive_snap:` gate), so it fires on EVERY successful
    iteration regardless of whether flashlive_snap was populated
    or entity seeding fired. Regression to gating the stamp on
    work-done would reintroduce the false-alarm risk."""
    import inspect
    import main
    src = inspect.getsource(main._score_flush_loop)

    # The stamp line must appear at the end of the try block,
    # after the seed section, not gated inside the flashlive_snap
    # or seed conditionals.
    assert 'stamp_pass_complete("score_flush")' in src, (
        "score_flush must call stamp_pass_complete every iteration"
    )
    # Sanity: NOT inside the seed conditional (which fires every 5th
    # cycle). The stamp indentation should match the outer try body's
    # depth, not the deeper conditional's depth.
    lines = src.split("\n")
    stamp_lines = [
        (i, line) for i, line in enumerate(lines)
        if 'stamp_pass_complete("score_flush")' in line
    ]
    assert len(stamp_lines) == 1, (
        f"expected exactly one stamp_pass_complete call in score_flush; "
        f"got {len(stamp_lines)}"
    )
    _idx, stamp_line = stamp_lines[0]
    # Indentation depth — outer try body is at ~16 spaces (4 levels).
    # Seed body is at ~24 spaces (6 levels). Enforce the stamp is at
    # 16 spaces or shallower.
    stamp_indent = len(stamp_line) - len(stamp_line.lstrip())
    assert stamp_indent <= 20, (
        f"stamp_pass_complete indentation is {stamp_indent} spaces — "
        f"looks like it's gated inside a conditional. Snapshot-"
        f"analysis regression: stamp must fire every iteration, not "
        f"only on work-done."
    )
