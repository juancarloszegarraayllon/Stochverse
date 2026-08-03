"""Tests for Layer C — ingestion.health registry + endpoint.

Load-bearing observability layer added after the 2026-08-01 → 08-03
Kalshi persistence outage exposed the gap: task alive but blocked
for ~58h; only external signal was fossil absence, spotted manually.
These tests lock in:

  1. Registry auto-registers tasks on first stamp/set_state.
  2. `stamp_pass_complete` moves state to holder_running and updates
     `last_pass_complete_ts`.
  3. `set_state` accepts arbitrary extras and stores them.
  4. `snapshot()` computes `seconds_since_last_pass` + `stale` flag
     from the stored `last_pass_complete_ts` and env-tunable threshold.
  5. `/api/ingestion_status` endpoint returns the snapshot in the
     documented shape.
  6. `/api/kalshi_status` mirrors the kalshi registry entry via the
     same underlying TASK_HEALTH — no independent state.
  7. Step 3b rowcount captured in the 4-tuple return.
  8. `pass_complete` log line surfaces freshness_bumped.
"""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def clean_health():
    """Reset the module-level registry between tests."""
    from ingestion.health import TASK_HEALTH
    TASK_HEALTH.clear()
    yield
    TASK_HEALTH.clear()


# ── Registry primitives ──────────────────────────────────────────

def test_register_is_idempotent_and_populates_defaults():
    from ingestion.health import register, TASK_HEALTH, STATE_UNKNOWN
    register("kalshi")
    entry = TASK_HEALTH["kalshi"]
    assert entry["name"] == "kalshi"
    assert entry["state"] == STATE_UNKNOWN
    assert entry["last_pass_complete_ts"] == 0.0
    # kalshi default staleness threshold per DEFAULT_STALENESS_THRESHOLDS_S
    assert entry["staleness_threshold_sec"] == 120
    # Second register should be a no-op — preserves existing state.
    entry["last_pass_complete_ts"] = 999.0
    register("kalshi")
    assert TASK_HEALTH["kalshi"]["last_pass_complete_ts"] == 999.0


def test_stamp_pass_complete_auto_registers_and_updates():
    from ingestion.health import stamp_pass_complete, TASK_HEALTH, STATE_HOLDER_RUNNING
    # No prior register() call — stamp should auto-register.
    before = time.time()
    stamp_pass_complete("fl")
    after = time.time()
    entry = TASK_HEALTH["fl"]
    assert entry["state"] == STATE_HOLDER_RUNNING, (
        "stamp_pass_complete must transition state to holder_running "
        "— a successful pass unambiguously proves the task is the "
        "holder and actively running"
    )
    assert before <= entry["last_pass_complete_ts"] <= after


def test_set_state_accepts_extras():
    from ingestion.health import set_state, TASK_HEALTH, STATE_CRASHED_RESTARTING
    set_state(
        "kalshi", STATE_CRASHED_RESTARTING,
        recent_crashes_window=3,
        last_error_class="TimeoutError",
        last_error_msg="mock hang",
    )
    entry = TASK_HEALTH["kalshi"]
    assert entry["state"] == STATE_CRASHED_RESTARTING
    assert entry["recent_crashes_window"] == 3
    assert entry["last_error_class"] == "TimeoutError"
    assert entry["last_error_msg"] == "mock hang"


# ── Snapshot semantics ───────────────────────────────────────────

def test_snapshot_computes_seconds_since_last_pass_and_stale_flag():
    from ingestion.health import snapshot, register, TASK_HEALTH, STATE_HOLDER_RUNNING
    register("kalshi")
    now = time.time()
    # Simulate a stamp 300s ago; kalshi threshold is 120s → stale=True.
    TASK_HEALTH["kalshi"]["last_pass_complete_ts"] = now - 300
    TASK_HEALTH["kalshi"]["state"] = STATE_HOLDER_RUNNING
    snap = snapshot()
    assert len(snap) == 1
    entry = snap[0]
    assert entry["name"] == "kalshi"
    assert 299 <= entry["seconds_since_last_pass"] <= 301
    assert entry["stale"] is True, (
        "seconds_since_last_pass (300) > threshold (120) → stale=True; "
        "regression here means monitoring can't alert on staleness"
    )

    # Now simulate a fresh stamp; stale should flip False.
    TASK_HEALTH["kalshi"]["last_pass_complete_ts"] = time.time()
    snap2 = snapshot()
    assert snap2[0]["stale"] is False


def test_snapshot_never_stamped_task_has_null_seconds_and_not_stale():
    """A task that just registered but has never completed a pass
    (`last_pass_complete_ts == 0.0`) should NOT be flagged stale
    — otherwise every task alerts immediately at startup before
    it's had a chance to run its first cycle."""
    from ingestion.health import snapshot, register
    register("kalshi")
    snap = snapshot()
    assert snap[0]["seconds_since_last_pass"] is None
    assert snap[0]["stale"] is False


# ── Endpoint shape ───────────────────────────────────────────────

def test_ingestion_status_endpoint_returns_documented_shape():
    from fastapi.testclient import TestClient
    import main
    from ingestion.health import register, stamp_pass_complete, TASK_HEALTH

    register("kalshi")
    stamp_pass_complete("kalshi")
    register("fl")

    client = TestClient(main.app)
    resp = client.get("/api/ingestion_status")
    assert resp.status_code == 200
    body = resp.json()

    assert "tasks" in body
    assert "task_count" in body
    assert "any_stale" in body
    assert "any_dead" in body
    assert "checked_at_ts" in body
    assert body["task_count"] == len(body["tasks"])

    names = {t["name"] for t in body["tasks"]}
    assert {"kalshi", "fl"} <= names

    kalshi_entry = next(t for t in body["tasks"] if t["name"] == "kalshi")
    for field in (
        "name", "state", "last_pass_complete_ts",
        "staleness_threshold_sec", "seconds_since_last_pass",
        "stale", "attempt", "recent_crashes_window",
        "last_error_class", "last_error_msg",
    ):
        assert field in kalshi_entry, (
            f"/api/ingestion_status must surface {field!r} — schema "
            f"contract for the operator alert rules that depend on it"
        )


def test_kalshi_status_mirrors_kalshi_registry_entry():
    """Nicety mirror route reads from the SAME TASK_HEALTH entry as
    /api/ingestion_status — no independent state, no double-write
    risk. If the aggregate is right the per-provider view is right."""
    from fastapi.testclient import TestClient
    import main
    from ingestion.health import register, stamp_pass_complete, TASK_HEALTH

    register("kalshi")
    stamp_pass_complete("kalshi")
    saved_ts = TASK_HEALTH["kalshi"]["last_pass_complete_ts"]

    client = TestClient(main.app)
    resp = client.get("/api/kalshi_status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["running"] is True
    assert body["state"] == "holder_running"
    assert body["last_pass_complete_ts"] == saved_ts
    assert body["staleness_threshold_sec"] == 120


def test_kalshi_status_returns_not_running_before_task_registers():
    """If the kalshi task hasn't hit its register() call yet (early
    startup window), /api/kalshi_status returns running=False with
    a diagnostic reason — not a 500 or a KeyError."""
    from fastapi.testclient import TestClient
    import main

    client = TestClient(main.app)
    resp = client.get("/api/kalshi_status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["running"] is False
    assert "reason" in body


# ── Counter fix: Step 3b rowcount + upsert 4-tuple contract ─────

def test_upsert_returns_four_tuple_with_freshness_bumped_field():
    """PR-Layer-C counter fix contract: the return signature is now
    a 4-tuple (inserted, updated, unchanged, freshness_bumped). The
    fourth element captures Postgres's actual rowcount from Step 3b
    — the number that was invisible before the fix and let the
    2026-08-01 Kalshi outage hide behind an "unchanged=~9500 upd=0
    ins=0" log line compatible with EITHER "everything fresh" or
    "task hung, zero writes.\"

    Source inspection because standing up an integration-shaped
    test against real Postgres for this one contract is heavier
    than needed; the return-shape guarantee is what the caller
    code depends on, and the callers already validate against the
    shape (kalshi.py and fl.py both unpack 4 values)."""
    import inspect
    from ingestion.base import upsert_provider_records_batch
    src = inspect.getsource(upsert_provider_records_batch)

    # Return statement must produce a 4-tuple with the fourth
    # element being the freshness_bumped counter.
    assert (
        "return (inserted_count, updated_count, unchanged_count, freshness_bumped)"
        in src
    ), (
        "upsert_provider_records_batch must return 4-tuple ending "
        "in freshness_bumped. Regression to a 3-tuple silently drops "
        "the Step 3b rowcount → PR #279's counter-shape observability "
        "gap re-opens."
    )
    # freshness_bumped must be captured from Step 3b's result.rowcount.
    assert "freshness_bumped = result.rowcount or 0" in src, (
        "Step 3b's rowcount must be captured via result.rowcount. "
        "Regression means the counter is always 0 → same failure "
        "shape as pre-Layer-C: pass_complete logs can't distinguish "
        "'everything fresh' from 'task hung with zero writes'."
    )


def test_pass_complete_log_line_includes_freshness_bumped():
    """Downstream contract: the ingestion pass_complete log line MUST
    surface freshness_bumped so /var/log grep and monitoring can
    distinguish 'idle-but-alive' from 'hung' at the log layer even
    if the endpoint isn't queried. Source inspection covers both
    call sites (kalshi + fl)."""
    import inspect
    import ingestion.kalshi
    import ingestion.fl

    kalshi_src = inspect.getsource(ingestion.kalshi)
    assert 'freshness_bumped=total_freshness_bumped' in kalshi_src, (
        "kalshi pass_complete log must include freshness_bumped= field"
    )

    fl_src = inspect.getsource(ingestion.fl)
    assert 'freshness_bumped=getattr(result, "_freshness_bumped", 0)' in fl_src, (
        "fl pass_complete log must include freshness_bumped= field"
    )
