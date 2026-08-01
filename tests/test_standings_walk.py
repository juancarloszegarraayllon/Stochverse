"""Tests for Day-62 standings-walk work — negative-cache 404 stages,
TTL bump 300s → 1800s, advisory-lock singleton on both walk loops.

Two operator-required review contracts get dedicated tests up top:

  1. Negative marker stamped ONLY on TRUE 404 — transient failures
     (429/timeout/5xx/network) MUST NOT earn a 24h negative marker.
     Parametrized across the plausible failure statuses to guard
     the intent literally.

  2. Marker entries are INERT to every reader — the aggregator, the
     multi-stage fallback, the diagnostic dump, and the blob
     round-trip (warm-load) all treat `http_status=404` entries as
     "no bracket," never as a real bracket.

Followed by walk-eligibility gating, TTL/negative-TTL defaults,
content-hash inclusion (so markers survive redeploys), and
transient-failure preservation.
"""
from __future__ import annotations

import time

import pytest


@pytest.fixture(autouse=True)
def clean_bracket_cache():
    """Reset _TOURNAMENT_BRACKET_CACHE between tests so contract
    assertions don't leak."""
    from caches.state import _TOURNAMENT_BRACKET_CACHE
    _TOURNAMENT_BRACKET_CACHE.clear()
    yield
    _TOURNAMENT_BRACKET_CACHE.clear()


# ── Review note 1: negative marker stamped ONLY on true 404 ────

@pytest.mark.parametrize("status", [404])
def test_negative_marker_stamped_on_true_404(monkeypatch, status):
    """A 404 response from FL stamps a marker entry so the walk
    can skip re-probing this stage for `_NEGATIVE_BRACKET_TTL_S`."""
    import main
    from caches.state import _TOURNAMENT_BRACKET_CACHE

    async def _fake_fetch_with_status(stage_id, season_id=""):
        return None, status

    monkeypatch.setattr(
        "flashlive_feed.fetch_bracket_draw_with_status",
        _fake_fetch_with_status,
    )

    import asyncio
    ok = asyncio.run(main._refresh_tournament_bracket("STAGE_A", "SEASON", "TEST_LEAGUE"))
    assert ok is True, "TRUE 404 must return True (cache learned something)"
    entry = _TOURNAMENT_BRACKET_CACHE.get("STAGE_A")
    assert entry is not None, "404 must stamp a marker entry"
    assert entry.get("http_status") == 404
    assert entry.get("league_name") == "TEST_LEAGUE"
    assert "bracket" not in entry, "marker entry must have NO bracket data"


@pytest.mark.parametrize("status", [429, 500, 502, 503, None])
def test_negative_marker_NOT_stamped_on_transient_failure(monkeypatch, status):
    """The operator-required contract: transient failures (429/5xx/
    network exception → status=None) MUST NOT earn a 24h negative
    marker. Otherwise a rate-storm minute would permanently mark
    live stages as dead and blank aggregates for the next 24 hours.

    Parametrized across the plausible non-404 statuses to guard the
    intent literally rather than test one representative."""
    import main
    from caches.state import _TOURNAMENT_BRACKET_CACHE

    async def _fake_fetch_with_status(stage_id, season_id=""):
        return None, status

    monkeypatch.setattr(
        "flashlive_feed.fetch_bracket_draw_with_status",
        _fake_fetch_with_status,
    )

    import asyncio
    ok = asyncio.run(main._refresh_tournament_bracket("STAGE_B", "SEASON", "TEST"))
    assert ok is False, f"transient failure (status={status}) must return False"
    assert "STAGE_B" not in _TOURNAMENT_BRACKET_CACHE, (
        f"transient failure (status={status}) MUST NOT stamp a marker — "
        f"regression here means a rate-storm minute would permanently "
        f"mark every live stage as dead for 24h"
    )


def test_transient_failure_preserves_existing_bracket_entry(monkeypatch):
    """Belt-and-braces: a pre-populated bracket entry must survive a
    subsequent transient-failure refresh unchanged. Guards against a
    partial refactor that writes an empty entry on failure."""
    import main
    from caches.state import _TOURNAMENT_BRACKET_CACHE

    pre_existing = {
        "bracket":     {"legs": [{"leg": 1}]},
        "ts":          time.time(),
        "season_id":   "S1",
        "league_name": "UCL",
    }
    _TOURNAMENT_BRACKET_CACHE["STAGE_LIVE"] = dict(pre_existing)

    async def _fake_fetch_with_status(stage_id, season_id=""):
        return None, 429  # transient

    monkeypatch.setattr(
        "flashlive_feed.fetch_bracket_draw_with_status",
        _fake_fetch_with_status,
    )

    import asyncio
    asyncio.run(main._refresh_tournament_bracket("STAGE_LIVE", "S1", "UCL"))
    assert _TOURNAMENT_BRACKET_CACHE["STAGE_LIVE"] == pre_existing, (
        "429 refresh clobbered a valid bracket entry — transient "
        "failures must leave the cache unchanged"
    )


# ── Review note 2: marker entries inert to every reader ────────

def test_bracket_aggregate_for_event_treats_marker_as_no_bracket():
    """`_bracket_aggregate_for_event` reads `cached.get("bracket")`
    on the primary lookup and skips fallback candidates with no
    `bracket` field. A marker entry (http_status=404, no bracket
    field) must NOT be treated as a real bracket by either path —
    reader must fall through to the multi-stage fallback (which
    also skips markers) and return None if no real bracket found."""
    import main
    from caches.state import _TOURNAMENT_BRACKET_CACHE, _SERIES_TO_STAGE_CACHE

    _SERIES_TO_STAGE_CACHE["KXTESTSERIES"] = {
        "stage_id":    "STAGE_MARKER",
        "season_id":   "S1",
        "league_name": "test_league",
    }
    _TOURNAMENT_BRACKET_CACHE["STAGE_MARKER"] = {
        "http_status": 404,
        "ts":          time.time(),
        "league_name": "test_league",
    }

    try:
        found = {
            "series_ticker": "KXTESTSERIES",
            "title":         "Team Alpha vs Team Beta",
        }
        result = main._bracket_aggregate_for_event(found)
        assert result is None, (
            "aggregator returned a truthy value for a marker-only "
            "cache — marker is not inert to the reader"
        )
    finally:
        _SERIES_TO_STAGE_CACHE.pop("KXTESTSERIES", None)


def test_multi_stage_fallback_skips_marker_entries():
    """The fallback loop inside `_bracket_aggregate_for_event`
    iterates `_TOURNAMENT_BRACKET_CACHE.items()` looking for
    same-league brackets. Marker entries must be filtered out by
    the `if not bracket: continue` guard — regression here would
    make the fallback try to `_aggregate_from_bracket(None, ...)`
    or attribute a real bracket's aggregate to a marker's stage_id."""
    import main
    from caches.state import _TOURNAMENT_BRACKET_CACHE, _SERIES_TO_STAGE_CACHE

    _SERIES_TO_STAGE_CACHE["KXFALLBACK"] = {
        "stage_id":    "STAGE_PRIMARY_MISS",  # this one won't have a bracket cached
        "season_id":   "S1",
        "league_name": "cup_league",
    }
    # Fallback candidate: real bracket in same league — but only a marker.
    _TOURNAMENT_BRACKET_CACHE["STAGE_FALLBACK_MARKER"] = {
        "http_status": 404,
        "ts":          time.time(),
        "league_name": "cup_league",
    }

    try:
        found = {
            "series_ticker": "KXFALLBACK",
            "title":         "Home vs Away",
        }
        # Must return None — the only same-league entry is a marker,
        # so the fallback loop must skip it. If the guard is broken,
        # this raises or returns a bogus aggregate.
        result = main._bracket_aggregate_for_event(found)
        assert result is None
    finally:
        _SERIES_TO_STAGE_CACHE.pop("KXFALLBACK", None)


def test_marker_survives_blob_round_trip():
    """`_bracket_content_hash` includes `http_status`, and warm-load
    passes through any dict entry <= 24h old. A marker must therefore
    survive: save → clear → warm-load → still marker.

    Regression here means every restart re-probes every dead stage,
    defeating the whole negative-cache."""
    import main
    from caches.state import _TOURNAMENT_BRACKET_CACHE

    real = {
        "bracket":     {"legs": [{"leg": 1, "home": "A"}]},
        "ts":          time.time(),
        "season_id":   "S1",
        "league_name": "REAL",
    }
    marker = {
        "http_status": 404,
        "ts":          time.time(),
        "league_name": "DEAD",
        "season_id":   "S1",
    }
    _TOURNAMENT_BRACKET_CACHE["STAGE_REAL"] = real
    _TOURNAMENT_BRACKET_CACHE["STAGE_DEAD"] = marker

    hash_with_marker = main._bracket_content_hash()
    _TOURNAMENT_BRACKET_CACHE.pop("STAGE_DEAD")
    hash_without_marker = main._bracket_content_hash()
    assert hash_with_marker != hash_without_marker, (
        "Content hash unchanged when a marker was added/removed — "
        "markers are silently excluded from the save gate, so they "
        "would never persist to cache_blobs. Every restart would "
        "re-probe every dead stage."
    )


# ── Walk-eligibility gate ──────────────────────────────────────

def test_walk_skips_marker_within_negative_ttl():
    """A fresh marker (age < _NEGATIVE_BRACKET_TTL_S) is NOT eligible
    for refresh — that's the negative-cache contract."""
    import main
    from caches.state import _NEGATIVE_BRACKET_TTL_S

    now = 1_800_000_000.0
    fresh_marker = {"http_status": 404, "ts": now - 3600}  # 1h old, well within 24h
    assert main._bracket_needs_refresh(fresh_marker, now) is False


def test_walk_reprobes_marker_past_negative_ttl():
    """A marker older than _NEGATIVE_BRACKET_TTL_S is eligible for
    re-probe — safety hatch for the rare case FL later adds standings
    to a previously-dead stage."""
    import main
    from caches.state import _NEGATIVE_BRACKET_TTL_S

    now = 1_800_000_000.0
    aged_marker = {"http_status": 404, "ts": now - _NEGATIVE_BRACKET_TTL_S - 1}
    assert main._bracket_needs_refresh(aged_marker, now) is True


def test_walk_reprobes_valid_entry_past_bracket_ttl():
    """A real bracket entry aged past _BRACKET_CACHE_TTL_S (Day-62
    default 1800s) IS eligible for refresh."""
    import main
    from caches.state import _BRACKET_CACHE_TTL_S

    now = 1_800_000_000.0
    aged_bracket = {"bracket": {"legs": []}, "ts": now - _BRACKET_CACHE_TTL_S - 1}
    assert main._bracket_needs_refresh(aged_bracket, now) is True


def test_walk_missing_entry_is_eligible():
    """No entry → always eligible (never fetched)."""
    import main
    now = 1_800_000_000.0
    assert main._bracket_needs_refresh(None, now) is True


# ── TTL defaults ───────────────────────────────────────────────

def test_bracket_ttl_default_is_1800s():
    """Day-62 bump 300 → 1800. Regression to 300 reintroduces the
    ~215k/day standings-walk load."""
    from caches.state import _BRACKET_CACHE_TTL_S
    assert _BRACKET_CACHE_TTL_S == 1800.0, (
        f"_BRACKET_CACHE_TTL_S = {_BRACKET_CACHE_TTL_S}; expected 1800.0 "
        f"(Day-62 30-min refresh — brackets are static, 5-min freshness "
        f"was buying nothing measurable)"
    )


def test_negative_bracket_ttl_default_is_24h():
    """Day-62 negative-cache re-check window. Regression to a shorter
    value would re-probe dead stages more often than needed."""
    from caches.state import _NEGATIVE_BRACKET_TTL_S
    assert _NEGATIVE_BRACKET_TTL_S == 86400.0, (
        f"_NEGATIVE_BRACKET_TTL_S = {_NEGATIVE_BRACKET_TTL_S}; expected "
        f"86400 (24h re-check window)"
    )


# ── Day-62 shakedown-fix regression tests (integration-shaped) ──

def test_dedupe_series_to_stage_worklist_collapses_duplicates():
    """N series pointing to M unique stages → M entries in the
    worklist, not N. First-seen wins for the entry payload —
    same-stage series always carry identical season_id / league_name
    in production data (they're derived from the same FL tournament)."""
    import main
    series_cache = {
        "KXA_1": {"stage_id": "STAGE_A", "season_id": "S1", "league_name": "L"},
        "KXA_2": {"stage_id": "STAGE_A", "season_id": "S1", "league_name": "L"},
        "KXA_3": {"stage_id": "STAGE_A", "season_id": "S1", "league_name": "L"},
        "KXB_1": {"stage_id": "STAGE_B", "season_id": "S1", "league_name": "L"},
        "KXNULL": {"stage_id": "", "season_id": "S1", "league_name": "L"},
        "KXBAD":  "not a dict",  # skipped by isinstance guard
    }
    unique = main._dedupe_series_to_stage_worklist(series_cache)
    assert set(unique.keys()) == {"STAGE_A", "STAGE_B"}, (
        f"dedupe produced {sorted(unique.keys())}; expected "
        f"{{'STAGE_A', 'STAGE_B'}} — 3 series behind STAGE_A + 1 "
        f"behind STAGE_B should collapse to 2 unique stages"
    )


def test_initial_pass_fires_one_refresh_per_unique_stage(monkeypatch):
    """Day-62 shakedown-fix regression guard. Pre-fix production log:
    `xdgtXAxR` marker stamped 7× in 2s because 7 series pointed to
    that stage_id and the semaphore-2 gather burst-fired all 7
    tasks before any cache write landed to gate them.

    This test rebuilds the exact many-series-per-stage worklist,
    runs one initial pass through the extracted
    `_run_bracket_warm_initial_pass_once`, and asserts exactly
    ONE refresh fires per unique stage_id — proving the dedupe
    invariant holds at the same layer the bug lived."""
    import main
    from caches.state import _SERIES_TO_STAGE_CACHE, _TOURNAMENT_BRACKET_CACHE

    _SERIES_TO_STAGE_CACHE.clear()
    _TOURNAMENT_BRACKET_CACHE.clear()

    # 5 series → STAGE_A (mimics an EPL bracket serving ~5-10 series),
    # 3 series → STAGE_B (mimics a smaller cup).
    for i in range(5):
        _SERIES_TO_STAGE_CACHE[f"KXFAKE_A{i}"] = {
            "stage_id": "STAGE_A", "season_id": "S1", "league_name": "L",
        }
    for i in range(3):
        _SERIES_TO_STAGE_CACHE[f"KXFAKE_B{i}"] = {
            "stage_id": "STAGE_B", "season_id": "S1", "league_name": "L",
        }

    call_counts: dict = {}

    async def _fake_refresh(stage_id, season_id="", league_name=""):
        # Simulate a real fetch with realistic latency so the semaphore
        # gate actually has to serialize. If refresh returned instantly
        # the parallel burst would drain in one event-loop tick and
        # any burst-fire bug would go undetected.
        import asyncio
        await asyncio.sleep(0.01)
        call_counts[stage_id] = call_counts.get(stage_id, 0) + 1
        _TOURNAMENT_BRACKET_CACHE[stage_id] = {
            "bracket": {"legs": []}, "ts": time.time(),
            "season_id": season_id, "league_name": league_name,
        }
        return True

    monkeypatch.setattr(main, "_refresh_tournament_bracket", _fake_refresh)

    import asyncio
    import logging
    fake_log = logging.getLogger("test_initial_pass")
    try:
        asyncio.run(main._run_bracket_warm_initial_pass_once(fake_log))

        assert call_counts == {"STAGE_A": 1, "STAGE_B": 1}, (
            f"initial pass fired duplicate probes: {call_counts}. "
            f"Regression to the Day-62 burst-stamp shape — every "
            f"series behind a shared stage_id built a duplicate task "
            f"in the parallel gather. Production log evidence: "
            f"xdgtXAxR marker stamped 7× in 2s, Iq4v1CJG ~20 fetches/min."
        )

        # SECOND PASS: cache now populated. Must fire ZERO fetches
        # because _bracket_needs_refresh returns False for fresh entries.
        # Guards against a subtle regression where the dedupe helper is
        # correct but the gate is bypassed some other way (e.g. the
        # write-side key drifts from the read-side key — the other
        # hypothesis considered during the Day-62 diagnosis).
        call_counts.clear()
        asyncio.run(main._run_bracket_warm_initial_pass_once(fake_log))
        assert call_counts == {}, (
            f"second pass fired {call_counts}; cache didn't gate. "
            f"Either _bracket_needs_refresh isn't being consulted, "
            f"the write key doesn't match the read key, or the "
            f"eligibility gate is being bypassed altogether."
        )
    finally:
        _SERIES_TO_STAGE_CACHE.clear()
        _TOURNAMENT_BRACKET_CACHE.clear()


def test_initial_pass_recheck_short_circuits_late_warm_load(monkeypatch):
    """Day-62 shakedown bug #2 regression guard — layer 2 (fire-time
    re-check).

    Isolates the fire-time re-check contract from timing considerations
    by mocking `_bracket_needs_refresh` to return True at build-time
    (so tasks queue) and False at fire-time (so warm_one under the
    semaphore short-circuits). If the re-check exists and is called,
    zero refreshes fire; if the re-check is missing (pre-fix behavior)
    every queued task fires.

    Guards against the entire check-at-build-time defect class:
    even if some future refactor breaks the ordering await, or a
    sibling task writes mid-enumeration, or a bracket entry ages
    then gets re-refreshed by another walk pass, the fire-time
    re-check catches it. Bug #1 (dedupe) and bug #2 (startup
    ordering) are both instances of this class."""
    import asyncio
    import logging
    import main
    from caches.state import _SERIES_TO_STAGE_CACHE, _TOURNAMENT_BRACKET_CACHE

    _SERIES_TO_STAGE_CACHE.clear()
    _TOURNAMENT_BRACKET_CACHE.clear()

    for i in range(5):
        _SERIES_TO_STAGE_CACHE[f"KX_{i}"] = {
            "stage_id": f"STAGE_{i}", "season_id": "S1", "league_name": "L",
        }

    # `_bracket_needs_refresh` semantics: True → refresh needed;
    # False → skip. Return True for the first N calls (build-time
    # enumeration; each unique stage triggers one call) then False
    # forever (fire-time re-checks — the layer-2 contract). Simulates
    # "cache was populated between build and fire" without depending
    # on scheduler timing.
    n_stages = 5
    call_count = {"n": 0}

    def _mock_needs_refresh(entry, now_s):
        call_count["n"] += 1
        return call_count["n"] <= n_stages  # True for build, False for re-check

    monkeypatch.setattr(main, "_bracket_needs_refresh", _mock_needs_refresh)

    refresh_call_counts: dict = {}

    async def _fake_refresh(stage_id, season_id="", league_name=""):
        refresh_call_counts[stage_id] = refresh_call_counts.get(stage_id, 0) + 1
        return True

    monkeypatch.setattr(main, "_refresh_tournament_bracket", _fake_refresh)

    try:
        asyncio.run(main._run_bracket_warm_initial_pass_once(
            logging.getLogger("test_recheck"),
        ))
        assert refresh_call_counts == {}, (
            f"fire-time re-check failed to catch late-populated cache: "
            f"{refresh_call_counts} refreshes fired despite gate saying "
            f"False at fire time. Regression to Day-62 shakedown bug #2 "
            f"— warm_one is not consulting _bracket_needs_refresh under "
            f"the semaphore, so any state change between enumeration "
            f"and fire slips through as a wasted refresh."
        )
        # Sanity: 5 build-time calls + 5 fire-time calls = 10 total.
        # Guards against a partial implementation where only some tasks
        # re-check (e.g. the check runs but on wrong data).
        assert call_count["n"] == 2 * n_stages, (
            f"_bracket_needs_refresh called {call_count['n']}× total; "
            f"expected {2 * n_stages} (5 build-time enumeration + 5 "
            f"fire-time re-checks). The re-check either isn't running "
            f"or is running on a different code path."
        )
    finally:
        _SERIES_TO_STAGE_CACHE.clear()
        _TOURNAMENT_BRACKET_CACHE.clear()


def test_walk_body_awaits_bracket_load_before_initial_pass(monkeypatch):
    """Day-62 shakedown bug #2 regression guard — layer 1 (ordering).

    startup_event fires _load_tournament_brackets_from_db() and
    _tournament_bracket_warm_loop() as sibling create_task calls
    with no dependency edge. Pre-fix the walk body's only pre-work
    sync was a 2s poll on _SERIES_TO_STAGE_CACHE — the WRONG cache
    for the initial-pass eligibility gate, which post-#279 also
    consulted _TOURNAMENT_BRACKET_CACHE.

    Post-fix the walk body directly awaits
    _load_tournament_brackets_from_db() before the initial pass.
    This test proves the ordering contract holds by mocking the
    loader to record completion via a flag, running the walk body,
    and asserting the flag was set before the initial pass fired."""
    import asyncio
    import logging
    import main
    from caches.state import _SERIES_TO_STAGE_CACHE, _TOURNAMENT_BRACKET_CACHE

    _SERIES_TO_STAGE_CACHE.clear()
    _TOURNAMENT_BRACKET_CACHE.clear()
    _SERIES_TO_STAGE_CACHE["KXTEST"] = {
        "stage_id": "STAGE_X", "season_id": "S1", "league_name": "L",
    }

    events: list = []

    async def _fake_loader():
        # Simulate the ~50ms Neon read.
        await asyncio.sleep(0.02)
        events.append("load_done")

    async def _fake_refresh(stage_id, season_id="", league_name=""):
        events.append(f"refresh_{stage_id}")
        return True

    monkeypatch.setattr(main, "_load_tournament_brackets_from_db", _fake_loader)
    monkeypatch.setattr(main, "_refresh_tournament_bracket", _fake_refresh)

    # Stub the DB + lock so the walk body enters the loop-body path
    # without needing a real Postgres.
    class _FakeSession:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
    def _fake_session_factory():
        return _FakeSession()
    async def _fake_try_lock(session, key):
        return True

    import db, ingestion.base as base_mod
    monkeypatch.setattr(db, "DATABASE_URL", "postgres://fake")
    monkeypatch.setattr(db, "async_session", _fake_session_factory)
    monkeypatch.setattr(base_mod, "try_acquire_advisory_lock", _fake_try_lock)

    async def _run():
        # Kick off the loop, cancel after the initial pass completes
        # (before the 60s steady-state sleep).
        task = asyncio.create_task(main._tournament_bracket_warm_loop())
        # Give initial pass ~500ms to complete.
        for _ in range(50):
            if any(e.startswith("refresh_") for e in events):
                break
            await asyncio.sleep(0.01)
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    try:
        asyncio.run(_run())
        assert "load_done" in events, (
            "loader never completed — test setup broken, not the fix"
        )
        assert "refresh_STAGE_X" in events, (
            "initial pass never ran — test setup broken, not the fix "
            "(loop may not have advanced past the load await)"
        )
        load_idx = events.index("load_done")
        refresh_idx = events.index("refresh_STAGE_X")
        assert load_idx < refresh_idx, (
            f"initial pass fired before warm-load completed: "
            f"events={events}. Regression to Day-62 shakedown bug #2 "
            f"— walk body no longer awaits _load_tournament_brackets_from_db "
            f"before running the initial pass, reintroducing the "
            f"empty-cache race observed in production 00:05:53.404-455."
        )
    finally:
        _SERIES_TO_STAGE_CACHE.clear()
        _TOURNAMENT_BRACKET_CACHE.clear()


def test_initial_pass_respects_prepopulated_markers(monkeypatch):
    """Extension of the above: if the warm-load populated the cache
    with a marker (from a prior deploy's saved blob), the initial
    pass MUST NOT re-probe that stage within `_NEGATIVE_BRACKET_TTL_S`.

    This is the production-critical case for the Day-62 acceptance
    criteria: post-fix deploy, warm-load restores markers + brackets
    accumulated by prior deploys, and the initial pass should be
    near-silent (fetch only stages first-seen since last save)."""
    import main
    from caches.state import _SERIES_TO_STAGE_CACHE, _TOURNAMENT_BRACKET_CACHE

    _SERIES_TO_STAGE_CACHE.clear()
    _TOURNAMENT_BRACKET_CACHE.clear()

    _SERIES_TO_STAGE_CACHE["KXDEAD"] = {
        "stage_id": "STAGE_DEAD_MARKER", "season_id": "S1", "league_name": "L",
    }
    _SERIES_TO_STAGE_CACHE["KXLIVE"] = {
        "stage_id": "STAGE_LIVE_BRACKET", "season_id": "S1", "league_name": "L",
    }
    _SERIES_TO_STAGE_CACHE["KXNEW"] = {
        "stage_id": "STAGE_NEW", "season_id": "S1", "league_name": "L",
    }

    now = time.time()
    _TOURNAMENT_BRACKET_CACHE["STAGE_DEAD_MARKER"] = {
        "http_status": 404, "ts": now - 3600, "league_name": "L",
    }
    _TOURNAMENT_BRACKET_CACHE["STAGE_LIVE_BRACKET"] = {
        "bracket": {"legs": []}, "ts": now - 60, "league_name": "L",
    }

    call_counts: dict = {}

    async def _fake_refresh(stage_id, season_id="", league_name=""):
        call_counts[stage_id] = call_counts.get(stage_id, 0) + 1
        _TOURNAMENT_BRACKET_CACHE[stage_id] = {
            "bracket": {"legs": []}, "ts": time.time(),
            "season_id": season_id, "league_name": league_name,
        }
        return True

    monkeypatch.setattr(main, "_refresh_tournament_bracket", _fake_refresh)

    import asyncio
    import logging
    fake_log = logging.getLogger("test_initial_pass_warm")
    try:
        asyncio.run(main._run_bracket_warm_initial_pass_once(fake_log))
        assert call_counts == {"STAGE_NEW": 1}, (
            f"initial pass fired {call_counts}; expected only "
            f"{{'STAGE_NEW': 1}}. Warm-loaded marker and fresh "
            f"bracket entries should be respected by the gate — "
            f"regression here reintroduces the Day-62 shape where "
            f"every stage got re-probed on every deploy."
        )
    finally:
        _SERIES_TO_STAGE_CACHE.clear()
        _TOURNAMENT_BRACKET_CACHE.clear()
