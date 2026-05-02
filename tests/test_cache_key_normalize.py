"""Regression test for _cache_key_normalize.

The looser _normalize() folds distinguishing suffixes (FC, City,
United, etc.) for fuzzy match scoring, but using it as a cache key
caused different teams ("Manchester City" / "Manchester United")
to collide on the same key. _cache_key_normalize must NOT collide
across distinct real-world teams.
"""
from flashlive_feed import _cache_key_normalize


def test_manchester_clubs_distinct():
    a = _cache_key_normalize("Manchester United")
    b = _cache_key_normalize("Manchester City")
    assert a != b


def test_diacritics_fold():
    assert _cache_key_normalize("Atlético Madrid") == _cache_key_normalize(
        "Atletico Madrid"
    )


def test_case_fold():
    assert _cache_key_normalize("PSG") == _cache_key_normalize("psg")


def test_whitespace_collapse():
    assert _cache_key_normalize("Bayern  Munich") == _cache_key_normalize(
        "Bayern Munich"
    )


def test_empty_string():
    assert _cache_key_normalize("") == ""
    assert _cache_key_normalize(None) == ""


def test_preserves_fc_suffix():
    # _normalize would strip " fc" — _cache_key_normalize must not.
    assert "fc" in _cache_key_normalize("Liverpool FC")
