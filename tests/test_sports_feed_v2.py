"""Integration test for sports_feed_v2 (phase 5).

Verifies that the v2 feed handler produces the same response shape
as v1 and includes the right kalshi blocks for paired/unpaired events.

Mocks _fl_get and the Kalshi cache so the test runs without real I/O.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from unittest.mock import patch, MagicMock

import pytest


def _ts(y, mo, d, h=0, mi=0):
    return int(datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp())


@pytest.fixture
def fake_cache_records():
    """Three Soccer cache records: a paired fixture (Arsenal-Atletico)
    with GAME + TOTAL + SPREAD, plus one Kalshi-only fixture."""
    return [
        # Arsenal vs Atl. Madrid — paired with FL
        {
            "event_ticker":  "KXUCLGAME-26MAY05ARSATM",
            "series_ticker": "KXUCLGAME",
            "_sport":        "Soccer",
            "title":         "Atletico at Arsenal",
            "outcomes": [
                {"label": "Arsenal",  "_yb": 47, "_ya": 50, "_na": 51, "ticker": "T1-Y"},
                {"label": "Atletico", "_yb": 28, "_ya": 30, "_na": 70, "ticker": "T1-A"},
                {"label": "Tie",      "_yb": 26, "_ya": 28, "_na": 73, "ticker": "T1-T"},
            ],
        },
        {
            "event_ticker":  "KXUCLTOTAL-26MAY05ARSATM",
            "series_ticker": "KXUCLTOTAL",
            "_sport":        "Soccer",
            "title":         "Atletico at Arsenal: Totals",
            "outcomes": [
                {"label": "Over 1.5 goals scored", "_yb": 80, "_ya": 82, "_na": 19, "ticker": "T2-1"},
                {"label": "Over 2.5 goals scored", "_yb": 50, "_ya": 52, "_na": 49, "ticker": "T2-2"},
                {"label": "Over 3.5 goals scored", "_yb": 25, "_ya": 27, "_na": 74, "ticker": "T2-3"},
                {"label": "Over 4.5 goals scored", "_yb": 10, "_ya": 12, "_na": 89, "ticker": "T2-4"},
            ],
        },
        {
            "event_ticker":  "KXUCLSPREAD-26MAY05ARSATM",
            "series_ticker": "KXUCLSPREAD",
            "_sport":        "Soccer",
            "title":         "Atletico at Arsenal: Spreads",
            "outcomes": [
                {"label": "Arsenal wins by over 1.5 goals",  "_yb": 30, "_ya": 32, "_na": 69, "ticker": "T3-1"},
                {"label": "Arsenal wins by over 2.5 goals",  "_yb": 15, "_ya": 17, "_na": 84, "ticker": "T3-2"},
                {"label": "Atletico wins by over 1.5 goals", "_yb": 20, "_ya": 22, "_na": 79, "ticker": "T3-3"},
                {"label": "Atletico wins by over 2.5 goals", "_yb": 8,  "_ya": 10, "_na": 91, "ticker": "T3-4"},
            ],
        },
        # Kalshi-only future fixture — Bayern vs PSG, no FL pair
        {
            "event_ticker":  "KXUCLGAME-26MAY13BMUPSG",
            "series_ticker": "KXUCLGAME",
            "_sport":        "Soccer",
            "title":         "PSG at Bayern Munich",
            "outcomes": [
                {"label": "Bayern Munich", "_yb": 50, "_ya": 52, "_na": 49, "ticker": "U1-H"},
                {"label": "PSG",           "_yb": 30, "_ya": 32, "_na": 69, "ticker": "U1-A"},
                {"label": "Tie",           "_yb": 22, "_ya": 24, "_na": 77, "ticker": "U1-T"},
            ],
        },
    ]


@pytest.fixture
def fake_fl_data():
    """One FL tournament with one fixture (Arsenal vs Atl. Madrid)."""
    return {
        "DATA": [
            {
                "TOURNAMENT_STAGE_ID": "tour_ucl_playoffs",
                "NAME":                "Europe: Champions League - Play Offs",
                "NAME_PART_1":         "Europe",
                "NAME_PART_2":         "Champions League - Play Offs",
                "COUNTRY_NAME":        "Europe",
                "EVENTS": [
                    {
                        "EVENT_ID":        "fl_arsatm",
                        "HOME_NAME":       "Arsenal",
                        "AWAY_NAME":       "Atl. Madrid",
                        "SHORTNAME_HOME":  "ARS",
                        "SHORTNAME_AWAY":  "ATM",
                        "START_TIME":      _ts(2026, 5, 5, 15, 0),
                        "STAGE_TYPE":      "SCHEDULED",
                    },
                ],
            },
        ],
    }


# ── End-to-end response shape ────────────────────────────────────

def test_v2_response_shape(fake_cache_records, fake_fl_data):
    """sports_feed_v2 returns the v1-compatible response shape."""
    import main

    async def fake_fl_get(path, params=None):
        return fake_fl_data

    main._cache["data_all"] = fake_cache_records

    # Mock _fl_get and the get_data() trigger
    with patch("flashlive_feed._fl_get", side_effect=fake_fl_get):
        with patch.object(main, "get_data", lambda: None):
            result = asyncio.run(main.sports_feed_v2(
                sport_id=1, timezone=0, indent_days=0,
            ))

    # Top-level shape
    assert result["fl_sport_id"] == 1
    assert result["kalshi_sport"] == "Soccer"
    assert result["source"] == "flashlive+kalshi+v2"
    assert "tournaments" in result
    assert "matched_kalshi_count" in result


def test_paired_event_has_kalshi_block(fake_cache_records, fake_fl_data):
    """Arsenal vs Atletico FL event should be paired with all 3 kalshi records."""
    import main

    async def fake_fl_get(path, params=None):
        return fake_fl_data

    main._cache["data_all"] = fake_cache_records
    with patch("flashlive_feed._fl_get", side_effect=fake_fl_get):
        with patch.object(main, "get_data", lambda: None):
            result = asyncio.run(main.sports_feed_v2(
                sport_id=1, timezone=0, indent_days=0,
            ))

    # Find the FL tournament
    fl_tournaments = [t for t in result["tournaments"]
                       if t.get("TOURNAMENT_STAGE_ID") == "tour_ucl_playoffs"]
    assert len(fl_tournaments) == 1
    t = fl_tournaments[0]
    assert len(t["events"]) == 1
    ev = t["events"][0]
    assert ev["EVENT_ID"] == "fl_arsatm"

    # kalshi block must be populated with all 3 records
    k = ev["kalshi"]
    assert k is not None
    assert k["count"] == 3
    assert len(k["markets"]) == 3
    # Tickers all from the same fixture
    for m in k["markets"]:
        assert "26MAY05ARSATM" in m["event_ticker"]


def test_unpaired_kalshi_only_fixture_appears(
    fake_cache_records, fake_fl_data,
):
    """Bayern-PSG (Kalshi-only) should appear as a synthetic event
    in a separate tournament from the paired Arsenal-Atletico fixture.
    """
    import main

    async def fake_fl_get(path, params=None):
        return fake_fl_data

    main._cache["data_all"] = fake_cache_records
    with patch("flashlive_feed._fl_get", side_effect=fake_fl_get):
        with patch.object(main, "get_data", lambda: None):
            result = asyncio.run(main.sports_feed_v2(
                sport_id=1, timezone=0, indent_days=8,  # 8 days out
            ))

    # All synthetic kalshi-only events have EVENT_ID prefixed with kalshi-h2h
    synth_events = []
    for t in result["tournaments"]:
        for ev in t.get("events") or []:
            if ev.get("_kalshi_h2h_only"):
                synth_events.append(ev)
    # The Bayern-PSG fixture should appear (its date is 2026-05-13,
    # which is the target date when indent_days=8 from May 5)
    bmu_psg = [e for e in synth_events if "BMUPSG" in (e.get("EVENT_ID") or "")]
    assert len(bmu_psg) == 1
    ev = bmu_psg[0]
    assert ev["HOME_NAME"]  # parsed from title
    assert ev["AWAY_NAME"]
    assert ev["kalshi"] is not None


def test_no_duplicate_event_across_tournaments(
    fake_cache_records, fake_fl_data,
):
    """Same fixture must never appear in two tournaments — the bug v1
    had with Arsenal-Atletico showing in 'Champions League' AND
    'Champions League - Play Offs'."""
    import main

    async def fake_fl_get(path, params=None):
        return fake_fl_data

    main._cache["data_all"] = fake_cache_records
    with patch("flashlive_feed._fl_get", side_effect=fake_fl_get):
        with patch.object(main, "get_data", lambda: None):
            result = asyncio.run(main.sports_feed_v2(
                sport_id=1, timezone=0, indent_days=0,
            ))

    # Collect all kalshi event_tickers across all tournaments
    seen_tickers: list = []
    for t in result["tournaments"]:
        for ev in t.get("events") or []:
            k = ev.get("kalshi")
            if k:
                for m in k.get("markets", []):
                    seen_tickers.append(m.get("event_ticker"))
    # Each kalshi ticker should appear at most once
    assert len(seen_tickers) == len(set(seen_tickers)), (
        f"Duplicate tickers across tournaments: {seen_tickers}"
    )


def test_matched_kalshi_count(fake_cache_records, fake_fl_data):
    """matched_kalshi_count tracks unique tickers attached to events."""
    import main

    async def fake_fl_get(path, params=None):
        return fake_fl_data

    main._cache["data_all"] = fake_cache_records
    with patch("flashlive_feed._fl_get", side_effect=fake_fl_get):
        with patch.object(main, "get_data", lambda: None):
            result = asyncio.run(main.sports_feed_v2(
                sport_id=1, timezone=0, indent_days=0,
            ))

    # 3 paired Arsenal-Atletico tickers + at most 3 unpaired (depends on date)
    # When indent_days=0 and target_date=today, Bayern-PSG (May 13) is
    # filtered out. So only the 3 paired tickers count.
    assert result["matched_kalshi_count"] >= 3


def test_invalid_sport_id_returns_error():
    import main
    result = asyncio.run(main.sports_feed_v2(
        sport_id=99, timezone=0, indent_days=0,
    ))
    assert "error" in result


def test_invalid_timezone_returns_error():
    import main
    result = asyncio.run(main.sports_feed_v2(
        sport_id=1, timezone=99, indent_days=0,
    ))
    assert "error" in result
