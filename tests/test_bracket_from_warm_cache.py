"""Regression tests for _bracket_from_warm_cache.

Background: /api/event/<ticker>/capabilities and /normalized used to
gate the "Draw" tab purely on a fresh /v1/tournaments/standings?type=
draw probe. When that probe blipped (rate limit, transient failure,
FL hiccup), the Draw tab silently disappeared even though the warm
loop had the bracket cached. This helper provides the cache-as-
fallback signal so the tab survives probe flakiness.
"""
from enrichment.aggregate import _bracket_from_warm_cache


def test_returns_bracket_when_cache_has_data():
    cache = {
        "lMPimXln": {
            "bracket": [{"round_num": 2, "label": "Semi-finals", "pairs": []}],
            "ts": 1234567890,
            "season_id": "bLJeeS2d",
            "league_name": "Champions League",
        },
    }
    out = _bracket_from_warm_cache("lMPimXln", cache)
    assert out == [{"round_num": 2, "label": "Semi-finals", "pairs": []}]


def test_returns_none_when_stage_missing_from_cache():
    cache = {"other_stage": {"bracket": [{"round_num": 1}]}}
    assert _bracket_from_warm_cache("absent_stage", cache) is None


def test_returns_none_when_stage_id_empty():
    cache = {"any": {"bracket": [{"x": 1}]}}
    assert _bracket_from_warm_cache("", cache) is None
    assert _bracket_from_warm_cache(None, cache) is None


def test_returns_none_when_cached_entry_has_no_bracket_field():
    # A cache entry whose bracket field is None / missing should
    # NOT cause the helper to claim the tab is available.
    cache = {"stage_a": {"bracket": None, "ts": 1}}
    assert _bracket_from_warm_cache("stage_a", cache) is None
    cache_b = {"stage_b": {"ts": 1}}  # no bracket key at all
    assert _bracket_from_warm_cache("stage_b", cache_b) is None


def test_returns_none_when_cache_arg_is_not_a_dict():
    # Defensive: caller passing a None or non-dict shouldn't crash.
    assert _bracket_from_warm_cache("stage", None) is None
    assert _bracket_from_warm_cache("stage", []) is None
