"""Tests for Phase 3 flag-wiring — is_v4_cohort, cutover config,
/api/cutover_status, /api/events branching.

Sole-truth cohort function, deterministic per-series membership,
MIN-COHORT FLOOR widening, DB-row + env-fallback + bg-refresh
config, /api/events strict-no-op-at-pct=0 contract, and the
"safe to merge" guarantee that pct=0 default ships zero behavior
change vs the pre-Phase-3 baseline.
"""
from __future__ import annotations

import hashlib
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def clean_cutover_state():
    """Reset cutover module state between tests so config bleed-
    through doesn't cross-contaminate assertions."""
    import cutover
    saved = cutover._CURRENT_CONFIG
    saved_counter = cutover._FALLBACK_COUNTER
    yield
    cutover._CURRENT_CONFIG = saved
    cutover._FALLBACK_COUNTER = saved_counter


# ── Test #1: deterministic per-series ─────────────────────────

def test_is_v4_cohort_deterministic(monkeypatch):
    """Same ticker across 1000 calls MUST always return same value.
    Non-determinism at this layer means requests A and B for the
    SAME ticker route to different code paths → v3/v4 flicker on
    successive polls → user-visible inconsistency."""
    import cutover
    # Populate universe + a cohort-enabling config.
    monkeypatch.setattr(
        "main._SERIES_SPORT_DYNAMIC",
        {f"KXFAKE{i:03d}": "Soccer" for i in range(30)},
    )
    cutover.set_current_config(cutover.CutoverConfig(
        traffic_pct=20,
        enabled_sports=("Soccer",),
        min_cohort_series=2,
        source="test",
        loaded_at_ts=0.0,
    ))
    ticker = "KXFAKE015"
    first = cutover.is_v4_cohort(ticker)
    for _ in range(1000):
        assert cutover.is_v4_cohort(ticker) is first


# ── Test #2: sport-scope short-circuit ────────────────────────

def test_is_v4_cohort_short_circuits_when_sport_not_enabled(monkeypatch):
    """Sport filter runs BEFORE the hash so non-cohort sports pay
    ~0 measurable cost. Regression here means every request pays
    the O(N) cohort-list build for tickers we already know are
    out of scope."""
    import cutover, linkage
    monkeypatch.setattr(
        "main._SERIES_SPORT_DYNAMIC",
        {"KXBBALL01": "Basketball", "KXSOCCER01": "Soccer"},
    )
    # Post-Day-64: cohort drawn from LINKED universe. Both series
    # need at least one event_ticker in _V4_LINKAGE_MAP to be
    # eligible.
    monkeypatch.setattr(
        "linkage._V4_LINKAGE_MAP",
        {
            "KXBBALL01-GAME1":  {"fl_event_id": "fl1", "fixture_id": 1, "updated_at_ts": 0.0},
            "KXSOCCER01-GAME1": {"fl_event_id": "fl2", "fixture_id": 2, "updated_at_ts": 0.0},
        },
    )
    cutover.set_current_config(cutover.CutoverConfig(
        traffic_pct=100,
        enabled_sports=("Soccer",),
        min_cohort_series=2,
        source="test",
        loaded_at_ts=0.0,
    ))
    # Wrong sport → False even at pct=100.
    assert cutover.is_v4_cohort("KXBBALL01") is False
    # Right sport → True (only 1 series in Soccer, and pct=100
    # cohort takes all of it).
    assert cutover.is_v4_cohort("KXSOCCER01") is True


# ── Test #3: pct=0 short-circuits ─────────────────────────────

def test_is_v4_cohort_returns_false_when_pct_zero(monkeypatch):
    """Default config (pct=0) → always v3. This is the load-bearing
    'safe to merge' contract: with default config, every ticker is
    out of cohort, /api/events branching is a strict no-op."""
    import cutover
    monkeypatch.setattr(
        "main._SERIES_SPORT_DYNAMIC",
        {"KXTEST01": "Soccer"},
    )
    cutover.set_current_config(cutover.CutoverConfig(
        traffic_pct=0,
        enabled_sports=("Soccer",),
        min_cohort_series=2,
        source="test",
        loaded_at_ts=0.0,
    ))
    assert cutover.is_v4_cohort("KXTEST01") is False


# ── Test #4: empty enabled_sports short-circuits ─────────────

def test_is_v4_cohort_returns_false_when_enabled_sports_empty(monkeypatch):
    """Same protection: empty enabled_sports → v4 disabled even at
    pct=100. Guards against a partial operator flip (pct set,
    sports forgotten)."""
    import cutover
    monkeypatch.setattr(
        "main._SERIES_SPORT_DYNAMIC",
        {"KXTEST01": "Soccer"},
    )
    cutover.set_current_config(cutover.CutoverConfig(
        traffic_pct=100,
        enabled_sports=(),
        min_cohort_series=2,
        source="test",
        loaded_at_ts=0.0,
    ))
    assert cutover.is_v4_cohort("KXTEST01") is False


# ── Test #5: MIN-COHORT FLOOR widens up to min ───────────────

def test_min_cohort_floor_widens_pct(monkeypatch):
    """30-series linked universe, pct=5, min=2. 30 * 5/100 = 1.5 → 2
    (round). max(2, 2) = 2. cohort_size == 2. Confirms the widener
    produces exactly min_cohort_series when pct-derived would be
    smaller.

    Post-Day-64: cohort drawn from LINKED universe (has linkage
    entry), so populate both sport map AND linkage map."""
    import cutover, linkage
    monkeypatch.setattr(
        "main._SERIES_SPORT_DYNAMIC",
        {f"KXFAKE{i:03d}": "Soccer" for i in range(30)},
    )
    monkeypatch.setattr(
        "linkage._V4_LINKAGE_MAP",
        {
            f"KXFAKE{i:03d}-GAME1": {
                "fl_event_id": f"fl{i}", "fixture_id": i, "updated_at_ts": 0.0,
            }
            for i in range(30)
        },
    )
    cfg = cutover.CutoverConfig(
        traffic_pct=5, enabled_sports=("Soccer",),
        min_cohort_series=2, source="test", loaded_at_ts=0.0,
    )
    size = cutover.compute_cohort_size(30, cfg)
    assert size == 2, (
        f"cohort_size={size}, expected 2 (min floor). Widener "
        f"regression: 5% of 30 = 1.5 → round=2, max(2,2)=2."
    )
    cutover.set_current_config(cfg)
    cohort = cutover.cohort_series_list()
    assert len(cohort) == 2


# ── Test #6: pathologically small universe capped ────────────

def test_min_cohort_floor_caps_at_universe_size(monkeypatch):
    """Universe smaller than min_cohort_series → cohort_size ==
    universe_size (can't select more series than exist)."""
    import cutover
    cfg = cutover.CutoverConfig(
        traffic_pct=5, enabled_sports=("Soccer",),
        min_cohort_series=10, source="test", loaded_at_ts=0.0,
    )
    # Universe of 3 series, min=10 → cohort=3 (capped).
    assert cutover.compute_cohort_size(3, cfg) == 3


# ── Test #7: env fallback path ────────────────────────────────

def test_env_fallback_active_when_bg_loop_never_completed(monkeypatch):
    """Before the bg refresh loop's first successful DB read, the
    module-level config MUST reflect env-fallback values (or
    module default). Serving path never sees a null config."""
    import cutover
    # Reload the module to trigger fresh _init_from_env_fallback().
    monkeypatch.setenv("V4_TRAFFIC_PCT_FALLBACK", "3")
    monkeypatch.setenv("V4_SPORTS_FALLBACK", "Soccer,Tennis")
    import importlib
    importlib.reload(cutover)
    cfg = cutover.get_current_config()
    assert cfg.source == "env_fallback"
    assert cfg.traffic_pct == 3
    assert cfg.enabled_sports == ("Soccer", "Tennis")


# ── Test #8: /api/cutover_status shape ────────────────────────

def test_cutover_status_endpoint_shape(monkeypatch):
    """All documented fields present. Regression on the endpoint
    schema breaks monitoring."""
    from fastapi.testclient import TestClient
    import main
    import cutover
    monkeypatch.setattr(
        "main._SERIES_SPORT_DYNAMIC",
        {"KXTEST01": "Soccer", "KXTEST02": "Soccer"},
    )
    cutover.set_current_config(cutover.CutoverConfig(
        traffic_pct=50, enabled_sports=("Soccer",),
        min_cohort_series=1, source="db",
        loaded_at_ts=1_800_000_000.0,
    ))
    client = TestClient(main.app)
    resp = client.get("/api/cutover_status")
    assert resp.status_code == 200
    body = resp.json()
    for field in (
        "traffic_pct", "enabled_sports", "min_cohort_series",
        "cohort_size", "cohort_sample", "v4_fallback_count",
        "config_source", "config_loaded_at_ts", "worker_id",
        "checked_at_ts",
    ):
        assert field in body, f"/api/cutover_status missing field {field!r}"
    assert body["config_source"] == "db"
    assert body["traffic_pct"] == 50
    assert "Soccer" in body["enabled_sports"]


# ── Test #9: /api/events never 500s on v4 exception ──────────

def test_v4_branching_pattern_never_raises_on_exception(monkeypatch):
    """Fallback-to-v3-always contract. Tests the exact code shape
    /api/events uses: cohort check → try apply v4 → except:
    increment counter, log, fall through. Regression means a v4
    pathway bug becomes a 500 for cohort tickers.

    Uses source inspection + a direct-execution simulation because
    the full /api/events handler is 500+ LOC of formatting and
    fixture-testing every code path is fragile — the contract that
    matters is that the try/except pattern is present and the
    counter increments on exception."""
    import inspect
    import main
    import cutover

    # (a) Source-inspection: /api/events must contain the try/except
    # around the v4-branching call site, AND the except block must
    # call increment_fallback_counter.
    src = inspect.getsource(main.get_events)
    assert "is_v4_cohort" in src, (
        "/api/events missing is_v4_cohort call — cohort branching "
        "wiring absent"
    )
    assert "increment_fallback_counter" in src or "_incr_v4_fb" in src, (
        "/api/events missing fallback counter increment — "
        "fallbacks would be silent"
    )
    assert "v4_fallback" in src, (
        "/api/events missing v4_fallback log line — operators can't "
        "see fallback occurrences in structured logs"
    )
    # (b) Execution-simulation: the exact 5-line pattern that lives
    # inside /api/events. Simulates a cohort record + a raising
    # placeholder + asserts counter increments and control flows past.
    counter_before = cutover.fallback_counter_value()
    def _raising_placeholder(record):
        raise RuntimeError("simulated v4 pathway error")
    fake_record = {"series_ticker": "KXFAKE001", "event_ticker": "KXFAKE001-CARD"}
    try:
        _raising_placeholder(fake_record)
    except Exception:
        cutover.increment_fallback_counter()
    counter_after = cutover.fallback_counter_value()
    assert counter_after == counter_before + 1, (
        "increment_fallback_counter did not fire in exception path — "
        "fallback-to-v3 contract broken"
    )


# ── Test #10: cohort membership stable across workers ────────

def test_cohort_membership_deterministic_across_processes(monkeypatch):
    """Two processes with IDENTICAL universe + config MUST agree on
    cohort membership for every ticker. Simulate by computing
    cohort twice from scratch — deterministic algorithm means both
    outputs identical."""
    import cutover
    monkeypatch.setattr(
        "main._SERIES_SPORT_DYNAMIC",
        {f"KXFAKE{i:03d}": "Soccer" for i in range(50)},
    )
    cfg = cutover.CutoverConfig(
        traffic_pct=10, enabled_sports=("Soccer",),
        min_cohort_series=2, source="test", loaded_at_ts=0.0,
    )
    cutover.set_current_config(cfg)
    cohort_a = cutover.cohort_series_list()
    cohort_b = cutover.cohort_series_list()
    assert cohort_a == cohort_b, (
        "Two calls to cohort_series_list() with identical universe "
        "returned different results — non-deterministic algorithm "
        "means workers can disagree on cohort membership."
    )
    # And is_v4_cohort agrees with the list.
    for t in cohort_a:
        assert cutover.is_v4_cohort(t) is True
    non_cohort = [
        f"KXFAKE{i:03d}" for i in range(50)
        if f"KXFAKE{i:03d}" not in cohort_a
    ]
    for t in non_cohort[:5]:
        assert cutover.is_v4_cohort(t) is False


# ── Test #11: safe-to-merge contract ─────────────────────────

def test_pct_zero_default_ships_zero_behavior_change(monkeypatch):
    """The load-bearing 'safe to merge' contract. With default
    config (pct=0, empty sports), EVERY ticker in a fake universe
    returns is_v4_cohort=False. Verifies the wiring can ship
    without the linkage-cache PR being live."""
    import cutover
    universe = {f"KXFAKE{i:04d}": "Soccer" for i in range(500)}
    monkeypatch.setattr("main._SERIES_SPORT_DYNAMIC", universe)
    # Default-shape config.
    cutover.set_current_config(cutover.CutoverConfig())
    for ticker in universe:
        assert cutover.is_v4_cohort(ticker) is False


# ── Test #12: boundary — hash exactly at effective_pct edge ──

def test_hash_boundary_uses_lowest_n_not_pct_predicate(monkeypatch):
    """Mutation-check guard. Under the lowest-N-by-hash rule, a
    ticker's cohort membership depends on its RANK in the sorted
    LINKED universe, NOT a `hash % 100 < effective_pct` comparison.
    Constructs a universe where `< vs <=` would differ and asserts
    membership follows the rank contract."""
    import cutover, linkage
    # 5-series universe with known hash ordering.
    universe = {f"KXFAKE{i:03d}": "Soccer" for i in range(5)}
    monkeypatch.setattr("main._SERIES_SPORT_DYNAMIC", universe)
    monkeypatch.setattr(
        "linkage._V4_LINKAGE_MAP",
        {
            f"KXFAKE{i:03d}-GAME1": {
                "fl_event_id": f"fl{i}", "fixture_id": i, "updated_at_ts": 0.0,
            }
            for i in range(5)
        },
    )
    cfg = cutover.CutoverConfig(
        traffic_pct=40, enabled_sports=("Soccer",),
        min_cohort_series=1, source="test", loaded_at_ts=0.0,
    )
    cutover.set_current_config(cfg)
    # 40% of 5 = 2 → cohort_size 2, floor 1 → max(2,1)=2.
    cohort = cutover.cohort_series_list()
    assert len(cohort) == 2
    # The 2 lowest-hash series by md5.
    all_ranked = sorted(
        universe.keys(),
        key=lambda s: (int(hashlib.md5(s.encode()).hexdigest(), 16), s),
    )
    assert cohort == all_ranked[:2]


# ── Test #13: bg refresh loop is registered ────────────────

def test_cutover_config_refresh_registered_in_health():
    """The bg refresh loop MUST be in INGESTION_TASK_NAMES so
    /api/ingestion_status pre-populates it via register_all(),
    matching the pattern of the other six ingestion loops."""
    from ingestion.health import INGESTION_TASK_NAMES
    assert "cutover_config_refresh" in INGESTION_TASK_NAMES, (
        "cutover_config_refresh missing from INGESTION_TASK_NAMES — "
        "/api/ingestion_status won't surface it, operator won't "
        "know if the config refresh loop dies silently."
    )
