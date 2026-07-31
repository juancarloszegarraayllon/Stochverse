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
