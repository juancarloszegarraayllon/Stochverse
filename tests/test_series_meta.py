"""Tests for Task #21 Surface A — Kalshi /series metadata cache
persistence, throttle, retry-with-backoff, and exception boundary.

The pre-fix code had three defects producing the 21:04 429 storm and
its silent misclassification aftermath:

  (1) `_SERIES_META_TRIED.add(s)` fired BEFORE the API call, so any
      429 permanently locked the series to `{}` for the process
      lifetime — silent misclassification until the container was
      recycled.

  (2) No throttle: `_build_cache` scanned every unclassified series
      back-to-back, hammering /series with unbounded concurrency.

  (3) No persistence: even without (1), every redeploy re-fetched
      every series cold — routine cause of the storm.

This file locks in the new contract:

  * Throttle: minimum-gap gate is observed process-wide.
  * Retry: 429 triggers exponential backoff up to 3 attempts, then
    the fetch gives up but does NOT lock the series — the next pass
    can retry.
  * Exception boundary: `_resolve_series_meta_dynamic` never raises,
    even when a transient error escapes the retry helper. A network
    error in the classifier loop would abort the entire REST snapshot.
  * Warm-load: TTL-gated. Expired entries drop; non-dict entries
    drop; missing `fetched_at` treated as expired (shape drift).
  * Persist loop: skips when dirty flag is False; saves the merged
    (existing ∪ local) blob when dirty; local wins on collision.
"""
from __future__ import annotations

import asyncio
import time

import pytest


# ── Fixtures ────────────────────────────────────────────────────

@pytest.fixture
def clean_meta_state():
    """Reset the module-level Kalshi /series meta state between tests
    so ordering-dependent assertions can't leak across cases."""
    import main
    main._SERIES_META_DYNAMIC.clear()
    main._SERIES_SPORT_DYNAMIC.clear()
    main._series_meta_dirty = False
    main._kalshi_meta_last_call_ts = 0.0
    yield
    main._SERIES_META_DYNAMIC.clear()
    main._SERIES_SPORT_DYNAMIC.clear()
    main._series_meta_dirty = False
    main._kalshi_meta_last_call_ts = 0.0


class _FakeSeriesResp:
    """Duck-types Kalshi's get_series response — the resolver reads
    `resp.series.category|tags|title`."""
    def __init__(self, category="Sports", tags=None, title=""):
        self.series = type("S", (), {
            "category": category,
            "tags":     list(tags or []),
            "title":    title,
        })()


# ── Throttle ────────────────────────────────────────────────────

def test_throttle_enforces_minimum_gap(monkeypatch, clean_meta_state):
    """Two back-to-back calls into `_kalshi_meta_throttle` must be
    separated by at least `_KALSHI_META_MIN_GAP_S`. Guards against a
    refactor that removes the sleep or the lock."""
    import main
    monkeypatch.setattr(main, "_KALSHI_META_MIN_GAP_S", 0.05)
    main._kalshi_meta_last_call_ts = 0.0
    t0 = time.monotonic()
    main._kalshi_meta_throttle()
    main._kalshi_meta_throttle()
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.04, (
        f"Throttle failed to space calls: {elapsed*1000:.1f}ms < "
        f"{main._KALSHI_META_MIN_GAP_S*1000:.1f}ms floor"
    )


# ── Retry helper ────────────────────────────────────────────────

def test_get_series_with_retry_backs_off_on_429(monkeypatch, clean_meta_state):
    """The retry helper must swallow 429s, retry up to 3 times, and
    return None if all attempts fail — NOT raise."""
    import main
    monkeypatch.setattr(main, "_KALSHI_META_MIN_GAP_S", 0.0)
    monkeypatch.setattr(main.time, "sleep", lambda _: None)
    calls = {"n": 0}

    class _Client:
        def get_series(self, series_ticker):
            calls["n"] += 1
            raise RuntimeError("HTTP 429 Too Many Requests")

    result = main._get_series_with_retry(_Client(), "KXTEST")
    assert result is None, "3× 429 must return None, not raise"
    assert calls["n"] == 3, f"Expected 3 attempts, got {calls['n']}"


def test_get_series_with_retry_propagates_non_429(monkeypatch, clean_meta_state):
    """Non-429 exceptions must bubble up — the resolver's outer
    try/except is the exception boundary, NOT this helper."""
    import main
    monkeypatch.setattr(main, "_KALSHI_META_MIN_GAP_S", 0.0)

    class _Client:
        def get_series(self, series_ticker):
            raise ConnectionError("EOF from server")

    with pytest.raises(ConnectionError):
        main._get_series_with_retry(_Client(), "KXTEST")


# ── Resolver ────────────────────────────────────────────────────

def test_429_does_not_permanently_cache_lock_series(monkeypatch, clean_meta_state):
    """The core Task #21 fix. Pre-fix code did:
        _SERIES_META_TRIED.add(s)  # BEFORE the fetch
        try: ...
    so any exception (including 429) permanently locked the series to
    return {}. Post-fix: a 429-only failure leaves _SERIES_META_DYNAMIC
    untouched so the NEXT pass can retry.

    Simulates 3× 429 (first call), then a success (second call), and
    verifies the second call's meta actually lands in the cache."""
    import main
    monkeypatch.setattr(main, "_KALSHI_META_MIN_GAP_S", 0.0)
    monkeypatch.setattr(main.time, "sleep", lambda _: None)
    call_state = {"n": 0}

    class _Client:
        def get_series(self, series_ticker):
            call_state["n"] += 1
            # First 3 attempts (first resolver call) all 429.
            # 4th call (second resolver call) succeeds.
            if call_state["n"] <= 3:
                raise RuntimeError("HTTP 429 rate limited")
            return _FakeSeriesResp(
                category="Sports", tags=["NBA"], title="NBA Championship",
            )

    monkeypatch.setattr(main, "get_client", lambda: _Client())

    first = main._resolve_series_meta_dynamic("KXNBA")
    assert first == {}, "First call (3× 429) must return empty"
    assert "KXNBA" not in main._SERIES_META_DYNAMIC, (
        "429-only failure must NOT cache — regression to the "
        "_SERIES_META_TRIED lock-on-429 defect"
    )

    second = main._resolve_series_meta_dynamic("KXNBA")
    assert second != {}, "Second call must succeed after 429s clear"
    assert second["category"] == "Sports"
    assert second["title"] == "NBA Championship"
    assert "fetched_at" in second, "Entry must carry fetched_at for TTL"
    assert main._SERIES_META_DYNAMIC["KXNBA"] == second


def test_resolver_never_raises_on_transient_error(monkeypatch, clean_meta_state):
    """Adjustment #1 from operator review: `_resolve_series_meta_dynamic`
    is called from `_build_cache`'s classifier loop. A transient
    network error propagating up would abort the entire Kalshi REST
    snapshot build.

    Monkeypatches `_get_series_with_retry` (the retry helper is the
    boundary between the throttled fetch and the resolver) to raise
    a RuntimeError, and asserts the resolver returns {} without
    raising and without caching."""
    import main

    def _boom(client, series_ticker):
        raise RuntimeError("simulated transient failure inside retry helper")

    monkeypatch.setattr(main, "_get_series_with_retry", _boom)
    monkeypatch.setattr(main, "get_client", lambda: object())

    # Must not raise — this is the exception boundary contract.
    result = main._resolve_series_meta_dynamic("KXBOOM")
    assert result == {}
    assert "KXBOOM" not in main._SERIES_META_DYNAMIC, (
        "Failed fetches must NOT poison the cache"
    )
    assert main._series_meta_dirty is False, (
        "Failed fetch must not mark the cache dirty (nothing to save)"
    )


def test_successful_resolve_populates_entry_shape(monkeypatch, clean_meta_state):
    """Locks in the explicit entry shape declared at
    `_SERIES_META_DYNAMIC`. Any field drift here silently breaks
    warm-load re-derivation."""
    import main
    monkeypatch.setattr(main, "_KALSHI_META_MIN_GAP_S", 0.0)

    class _Client:
        def get_series(self, series_ticker):
            return _FakeSeriesResp(
                category="Sports",
                tags=["NHL", "hockey"],
                title="NHL Championship",
            )

    monkeypatch.setattr(main, "get_client", lambda: _Client())

    result = main._resolve_series_meta_dynamic("KXNHL")
    assert set(result.keys()) == {"category", "tags", "title", "fetched_at"}, (
        f"Entry shape drift — got keys {sorted(result.keys())}; "
        f"expected {'category, tags, title, fetched_at'}"
    )
    assert isinstance(result["tags"], list)
    assert isinstance(result["fetched_at"], float)
    assert main._series_meta_dirty is True, (
        "Successful fetch must mark cache dirty for the persist loop"
    )


# ── Warm-load ───────────────────────────────────────────────────

def test_warm_load_skips_stale_entries(monkeypatch, clean_meta_state):
    """Warm-load must drop entries older than `_KALSHI_META_TTL_S` so
    stale metadata (e.g. a series that got its title updated Kalshi-
    side) is re-fetched on the next pass. Missing `fetched_at` is
    treated as stale — protects against shape drift on old blobs."""
    import main
    monkeypatch.setattr(main, "_KALSHI_META_TTL_S", 30 * 24 * 3600)  # 30 days
    now = time.time()
    old = now - (60 * 24 * 3600)   # 60 days old — expired
    fresh = now - (1 * 24 * 3600)  # 1 day old  — kept

    fake_blob = {
        "KXOLD":       {"category": "Sports", "tags": [], "title": "x", "fetched_at": old},
        "KXFRESH":     {"category": "Sports", "tags": ["NBA"], "title": "NBA", "fetched_at": fresh},
        "KXNOSTAMP":   {"category": "Sports", "tags": [], "title": "y"},   # missing fetched_at
        "KXBAD":       "not a dict",  # ignored
    }

    async def _fake_load(key):
        assert key == "kalshi_series_meta"
        return fake_blob

    import db
    monkeypatch.setattr(db, "load_cache_blob", _fake_load)

    asyncio.run(main._load_series_meta_from_db())

    assert "KXFRESH" in main._SERIES_META_DYNAMIC
    assert "KXOLD" not in main._SERIES_META_DYNAMIC, (
        "Expired entry survived TTL cutoff — check the fetched_at guard"
    )
    assert "KXNOSTAMP" not in main._SERIES_META_DYNAMIC, (
        "Shape-drifted entry (no fetched_at) must be treated as expired"
    )
    assert "KXBAD" not in main._SERIES_META_DYNAMIC


# ── Persist loop ────────────────────────────────────────────────

def test_persist_loop_saves_on_dirty_and_skips_on_clean(monkeypatch, clean_meta_state):
    """The persist loop must call save_cache_blob ONLY when the dirty
    flag is set. Under steady-state (no new fetches) it must idle so
    we don't rewrite the same blob every 60s."""
    import main

    saves: list[dict] = []

    async def _fake_save(key, data):
        saves.append({"key": key, "data": data})
        return True

    async def _fake_load(key):
        return {"KXOLD": {"category": "Sports", "tags": [], "title": "old",
                          "fetched_at": time.time()}}

    import db
    monkeypatch.setattr(db, "save_cache_blob", _fake_save)
    monkeypatch.setattr(db, "load_cache_blob", _fake_load)

    # Patch asyncio.sleep to fast-forward, and stop after one tick by
    # raising CancelledError on the second sleep.
    sleeps = {"n": 0}
    real_sleep = asyncio.sleep

    async def _fast_sleep(_):
        sleeps["n"] += 1
        if sleeps["n"] >= 2:
            raise asyncio.CancelledError
        await real_sleep(0)

    monkeypatch.setattr(main.asyncio, "sleep", _fast_sleep)

    # Case A: clean → no save.
    main._series_meta_dirty = False
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(main._series_meta_persist_loop())
    assert saves == [], "Persist loop must NOT save when clean"

    # Case B: dirty + local entry → merged save under the expected key.
    sleeps["n"] = 0
    main._SERIES_META_DYNAMIC["KXNEW"] = {
        "category": "Sports", "tags": ["NFL"], "title": "NFL",
        "fetched_at": time.time(),
    }
    main._series_meta_dirty = True
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(main._series_meta_persist_loop())
    assert len(saves) == 1, "Persist loop must save exactly once when dirty"
    assert saves[0]["key"] == "kalshi_series_meta"
    saved_data = saves[0]["data"]
    assert "KXNEW" in saved_data, "Local entry missing from saved blob"
    assert "KXOLD" in saved_data, (
        "Merge-on-save failed — existing on-disk entry was clobbered "
        "(would drop a co-worker's fresh discoveries)"
    )
    assert main._series_meta_dirty is False, (
        "Dirty flag must be cleared after a successful save"
    )
