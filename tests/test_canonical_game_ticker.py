"""Tests for enrichment.aggregate._canonical_game_ticker.

The canonicalizer maps any sibling-market ticker
(KXUCLBTTS-…, KXUCLSPREAD-…, KXUCL1H-…) to its GAME-suffix
sibling so cache lookups land on one entry per fixture instead
of one per market.
"""
from enrichment.aggregate import _canonical_game_ticker


def _records(*tickers):
    return [{"event_ticker": t} for t in tickers]


def test_already_canonical_returns_self():
    records = _records("KXUCLGAME-26MAY06BMUPSG")
    assert _canonical_game_ticker(
        "KXUCLGAME-26MAY06BMUPSG", records
    ) == "KXUCLGAME-26MAY06BMUPSG"


def test_btts_resolves_to_game_sibling():
    records = _records(
        "KXUCLGAME-26MAY06BMUPSG",
        "KXUCLBTTS-26MAY06BMUPSG",
        "KXUCLSPREAD-26MAY06BMUPSG",
    )
    assert _canonical_game_ticker(
        "KXUCLBTTS-26MAY06BMUPSG", records
    ) == "KXUCLGAME-26MAY06BMUPSG"


def test_spread_resolves_to_game_sibling():
    records = _records(
        "KXUCLGAME-26MAY06BMUPSG",
        "KXUCLSPREAD-26MAY06BMUPSG",
    )
    assert _canonical_game_ticker(
        "KXUCLSPREAD-26MAY06BMUPSG", records
    ) == "KXUCLGAME-26MAY06BMUPSG"


def test_no_sibling_returns_input():
    records = _records(
        "KXUCLBTTS-26MAY06BMUPSG",  # only sibling, no GAME ticker
    )
    assert _canonical_game_ticker(
        "KXUCLBTTS-26MAY06BMUPSG", records
    ) == "KXUCLBTTS-26MAY06BMUPSG"


def test_different_fixture_doesnt_resolve():
    """Same series prefix, different date+teams → no match."""
    records = _records(
        "KXUCLGAME-26MAY05ARSATM",  # different fixture
    )
    assert _canonical_game_ticker(
        "KXUCLBTTS-26MAY06BMUPSG", records
    ) == "KXUCLBTTS-26MAY06BMUPSG"


def test_lowercase_input_normalizes_to_upper_match():
    records = _records("KXUCLGAME-26MAY06BMUPSG")
    assert _canonical_game_ticker(
        "kxuclbtts-26may06bmupsg", records
    ) == "KXUCLGAME-26MAY06BMUPSG"


def test_empty_inputs_safe():
    assert _canonical_game_ticker("", []) == ""
    assert _canonical_game_ticker(None, []) == ""
    assert _canonical_game_ticker("INVALID", []) == "INVALID"
