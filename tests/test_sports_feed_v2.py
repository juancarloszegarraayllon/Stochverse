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


# ── Safety-net fallback routing (phase 5 punch list 2026-05-05) ──

def test_v2_safety_net_routes_unpaired_into_existing_fl_tournament():
    """When an unpaired Kalshi fixture has no deterministic hint
    (no in-request paired record, no _SERIES_TOURNAMENT_HINTS entry)
    but its series_base maps to a known league pattern, route the
    synthetic event INTO the matching FL tournament rather than
    spawning a 'Other: <ticker>' sibling tournament. Mirrors the
    NBA day-1 case: every fixture fails to pair via abbr, so the
    paired-record hint never establishes — but we still want the
    Kalshi data alongside the FL rows.
    """
    import main

    # Pre-populate _SERIES_TOURNAMENT_HINTS empty for the test
    main._SERIES_TOURNAMENT_HINTS.clear()

    # FL tournament shaped like NBA Play Offs — has FL events but
    # NONE paired (out_tournaments[0].events[*].kalshi is empty)
    fl_tournament = {
        "TOURNAMENT_STAGE_ID": "tour_nba_playoffs",
        "NAME":                "NBA - Play Offs",
        "NAME_PART_1":         "USA",
        "NAME_PART_2":         "NBA - Play Offs",
        "COUNTRY_NAME":        "USA",
        "events": [
            {
                "EVENT_ID":   "fl_okclal",
                "HOME_NAME":  "Oklahoma City Thunder",
                "AWAY_NAME":  "Los Angeles Lakers",
                "kalshi":     None,  # un-paired due to abbr gap
            },
        ],
    }
    out_tournaments = [fl_tournament]

    # Bucket simulating the unpaired KXNBAGAME-26MAY05LALOKC record
    from datetime import date
    fixture_key = ("Basketball", date(2026, 5, 5), "2030", "LALOKC")
    records = [{
        "event_ticker":  "KXNBAGAME-26MAY05LALOKC",
        "series_ticker": "KXNBAGAME",
        "_sport":        "Basketball",
        "title":         "Lakers at Thunder",
        "outcomes": [
            {"label": "Oklahoma City Thunder",
             "_yb": 87, "_ya": 88, "_na": 12, "ticker": "T-OKC"},
            {"label": "Los Angeles Lakers",
             "_yb": 14, "_ya": 16, "_na": 84, "ticker": "T-LAL"},
        ],
    }]
    buckets = {fixture_key: records}

    synth_tournaments, routed = main._v2_route_unpaired(
        buckets, "Basketball", out_tournaments,
        target_date=date(2026, 5, 5),
    )

    # Synth was attached to the FL tournament — no sibling synthetic
    # tournament was spawned.
    assert synth_tournaments == [], (
        f"Expected zero synthetic tournaments (safety-net should "
        f"route inside FL); got {synth_tournaments!r}"
    )
    assert routed == 1
    # FL tournament now contains the synth event alongside the
    # original FL row
    assert len(fl_tournament["events"]) == 2
    synth_ev = fl_tournament["events"][1]
    assert synth_ev["_kalshi_h2h_only"] is True
    assert synth_ev["EVENT_ID"] == "kalshi-h2h-KXNBAGAME-26MAY05LALOKC"


def test_v2_safety_net_does_not_fire_for_unknown_series():
    """Series with no _V2_SAFETY_NET_LEAGUE_PATTERNS entry must
    fall through to the synthetic-tournament path so untracked
    competitions still surface (just under their own bucket).
    """
    import main
    main._SERIES_TOURNAMENT_HINTS.clear()

    fl_tournament = {
        "TOURNAMENT_STAGE_ID": "tour_random",
        "NAME":                "Some Random League",
        "events": [],
    }
    out_tournaments = [fl_tournament]

    from datetime import date
    fixture_key = ("Basketball", date(2026, 5, 5), "2030", "ABCDEF")
    records = [{
        "event_ticker":  "KXMADEUPGAME-26MAY05ABCDEF",
        "series_ticker": "KXMADEUPGAME",
        "_sport":        "Basketball",
        "title":         "Foo at Bar",
        "outcomes":      [],
    }]
    buckets = {fixture_key: records}

    synth_tournaments, routed = main._v2_route_unpaired(
        buckets, "Basketball", out_tournaments,
        target_date=date(2026, 5, 5),
    )

    # FL tournament untouched
    assert fl_tournament["events"] == []
    # Synthetic tournament created for the unrouted bucket
    assert len(synth_tournaments) == 1
    assert routed == 1


def test_v2_safety_net_target_helper():
    """Direct unit test for the _v2_safety_net_target helper:
    case-insensitive substring match, first-match-wins.
    """
    import main
    out = [
        {"NAME": "USA: NBA - Play Offs"},
        {"NAME": "Spain: ACB"},
        {"NAME": "Other: UEFA Champions League - Group Stage"},
    ]
    # NBA series_base → NBA tournament. Keys are post-strip_known_suffix.
    t = main._v2_safety_net_target("KXNBA", out)
    assert t is not None
    assert "NBA" in t["NAME"]
    # UCL series_base → Champions League tournament
    t = main._v2_safety_net_target("KXUCL", out)
    assert t is not None
    assert "Champions League" in t["NAME"]
    # Unknown series → no match
    assert main._v2_safety_net_target("KXMADEUP", out) is None
    # Empty series → no match
    assert main._v2_safety_net_target("", out) is None


# ── _v2_pick_primary preference for GAME/MATCH suffix ────────────

def test_v2_pick_primary_prefers_game_suffix_over_series():
    """Phase 5 punch list 2026-05-05 — empty WINNER tab on NBA
    fixtures because KXNBASERIES tickers (no market_type) were
    being chosen as primary over KXNBAGAME (the actual 2-way
    Winner). _extract_winner_prices then ran on series-level
    outcomes that have no home/away prices and the WINNER tab
    rendered empty. Primary should prefer GAME/MATCH suffix.
    """
    import main
    records = [
        # KXNBASERIES comes first — no market_type, no GAME suffix.
        {
            "event_ticker":  "KXNBASERIES-26CLEDETR2",
            "series_ticker": "KXNBASERIES",
            "title":         "Cleveland Cavaliers vs Detroit Pistons playoffs series",
            "outcomes": [
                {"label": "Cavaliers win 4-0", "_yb": 10},
                {"label": "Cavaliers win 4-1", "_yb": 30},
            ],
        },
        # KXNBAGAME — the actual 2-way headline Winner. Should be
        # picked even though it's not first in the list.
        {
            "event_ticker":  "KXNBAGAME-26MAY05CLEDET",
            "series_ticker": "KXNBAGAME",
            "title":         "Cleveland Cavaliers vs Detroit Pistons",
            "outcomes": [
                {"label": "Cleveland Cavaliers", "_yb": 60},
                {"label": "Detroit Pistons",     "_yb": 40},
            ],
        },
    ]
    primary = main._v2_pick_primary(records)
    assert primary["series_ticker"] == "KXNBAGAME", (
        f"Expected KXNBAGAME (suffix=GAME) to win over KXNBASERIES "
        f"(no suffix). Got {primary['series_ticker']!r}"
    )


def test_v2_pick_primary_falls_back_when_no_game_suffix():
    """When NO record has a GAME/MATCH suffix (e.g., outright-only
    pairing or a sport whose game ticker doesn't follow that convention),
    fall through to the legacy any-empty-market_type heuristic.
    """
    import main
    records = [
        {
            "event_ticker":  "KXFOO-26-XYZ",
            "series_ticker": "KXFOO",
            "title":         "Foo vs Bar",
            "outcomes":      [],
        },
    ]
    primary = main._v2_pick_primary(records)
    assert primary["series_ticker"] == "KXFOO"


def test_v2_pick_primary_soccer_kxuclgame_still_wins():
    """Soccer canonical headline (KXUCLGAME, GAME-suffixed) keeps
    being picked over sub-market tickers — sanity check we didn't
    regress the working case.
    """
    import main
    records = [
        {
            "event_ticker":  "KXUCLTOTAL-26MAY05ARSATM",
            "series_ticker": "KXUCLTOTAL",
            "title":         "Atletico at Arsenal: Totals",
            "outcomes":      [],
        },
        {
            "event_ticker":  "KXUCLGAME-26MAY05ARSATM",
            "series_ticker": "KXUCLGAME",
            "title":         "Atletico at Arsenal",
            "outcomes":      [],
        },
    ]
    primary = main._v2_pick_primary(records)
    assert primary["series_ticker"] == "KXUCLGAME"


# ── /sports v3 — registry-based handler (Phase C2c-c2) ────────────

def test_v3_response_shape(fake_cache_records, fake_fl_data):
    """sports_feed_v3 returns the same v1/v2-compatible response
    shape with `source: 'flashlive+kalshi+v3'`."""
    import main

    async def fake_fl_get(path, params=None):
        return fake_fl_data

    main._cache["data_all"] = fake_cache_records

    with patch("flashlive_feed._fl_get", side_effect=fake_fl_get):
        with patch.object(main, "get_data", lambda: None):
            result = asyncio.run(main.sports_feed_v3(
                sport_id=1, timezone=0, indent_days=0,
            ))

    assert result["fl_sport_id"]   == 1
    assert result["kalshi_sport"]  == "Soccer"
    assert result["source"]        == "flashlive+kalshi+v3"
    assert "tournaments"           in result
    assert "matched_kalshi_count"  in result


def test_v3_paired_event_has_kalshi_block(
    fake_cache_records, fake_fl_data,
):
    """Arsenal-Atletico FL event paired via registry → Kalshi block
    populated with all 3 records (Game / Total / Spread). Same end-
    to-end behavior as v2 for the canonical-pair case."""
    import main

    async def fake_fl_get(path, params=None):
        return fake_fl_data

    main._cache["data_all"] = fake_cache_records

    with patch("flashlive_feed._fl_get", side_effect=fake_fl_get):
        with patch.object(main, "get_data", lambda: None):
            result = asyncio.run(main.sports_feed_v3(
                sport_id=1, timezone=0, indent_days=0,
            ))

    fl_tournaments = [t for t in result["tournaments"]
                       if t.get("TOURNAMENT_STAGE_ID") == "tour_ucl_playoffs"]
    assert len(fl_tournaments) == 1
    t = fl_tournaments[0]
    assert len(t["events"]) == 1
    ev = t["events"][0]
    assert ev["EVENT_ID"] == "fl_arsatm"
    k = ev["kalshi"]
    assert k is not None
    assert k["count"] == 3
    for m in k["markets"]:
        assert "26MAY05ARSATM" in m["event_ticker"]


def test_v3_invalid_sport_id_returns_error():
    import main
    result = asyncio.run(main.sports_feed_v3(
        sport_id=99, timezone=0, indent_days=0,
    ))
    assert "error" in result


def test_v3_invalid_timezone_returns_error():
    import main
    result = asyncio.run(main.sports_feed_v3(
        sport_id=1, timezone=99, indent_days=0,
    ))
    assert "error" in result


def test_v3_route_dispatches_via_v_param():
    """`?v=3` query param on /api/sports/{sport_id}/feed should
    invoke sports_feed_v3, not v2 or v1. Quick sanity check on
    the route-level dispatcher.

    NOTE: temporarily monkeypatches `_SPORTS_FL_ONLY = False` so
    the FL-only kill-switch (TEMPORARY user request) doesn't
    intercept dispatch. Remove the patch once the kill-switch is
    reverted.
    """
    import main
    # Mock both v2 and v3 to record which got called.
    called = {"v2": False, "v3": False}

    async def fake_v2(*args, **kwargs):
        called["v2"] = True
        return {"source": "flashlive+kalshi+v2"}

    async def fake_v3(*args, **kwargs):
        called["v3"] = True
        return {"source": "flashlive+kalshi+v3"}

    with patch.object(main, "_SPORTS_FL_ONLY", False):
        with patch.object(main, "sports_feed_v2", side_effect=fake_v2):
            with patch.object(main, "sports_feed_v3", side_effect=fake_v3):
                # v=3 routes to v3
                result = asyncio.run(main.sports_feed(
                    sport_id=1, timezone=0, indent_days=0, v=3,
                ))
                assert called["v3"] is True
                assert called["v2"] is False
                assert result["source"] == "flashlive+kalshi+v3"


def test_default_route_dispatches_to_v3():
    """Phase C2c-c2 stage-2 promotion — unflagged
    /api/sports/{sport_id}/feed should now route through v3 by
    default (registry-based pairing). v2 and v1 remain accessible
    via explicit ?v=2 / ?v=1 query params for the safety-window /
    rollback paths.

    NOTE: temporarily monkeypatches `_SPORTS_FL_ONLY = False` so
    the FL-only kill-switch (TEMPORARY user request) doesn't
    intercept dispatch.
    """
    import main
    called = {"v2": False, "v3": False}

    async def fake_v2(*args, **kwargs):
        called["v2"] = True
        return {"source": "flashlive+kalshi+v2"}

    async def fake_v3(*args, **kwargs):
        called["v3"] = True
        return {"source": "flashlive+kalshi+v3"}

    with patch.object(main, "_SPORTS_FL_ONLY", False):
        with patch.object(main, "sports_feed_v2", side_effect=fake_v2):
            with patch.object(main, "sports_feed_v3", side_effect=fake_v3):
                # No `v` argument → uses route default. After C2c-c2
                # stage-2, that's v=3.
                result = asyncio.run(main.sports_feed(
                    sport_id=1, timezone=0, indent_days=0,
                ))
                assert called["v3"] is True
                assert called["v2"] is False
                assert result["source"] == "flashlive+kalshi+v3"


def test_fl_only_kill_switch_intercepts_dispatch():
    """TEMPORARY: when _SPORTS_FL_ONLY=True, dispatch is intercepted
    by the FL-only helper before it reaches v2/v3. This test guards
    against accidentally breaking the kill-switch while it's in
    effect. Remove this test when the kill-switch is reverted.
    """
    import main
    called = {"v3": False, "fl_only": False}

    async def fake_v3(*args, **kwargs):
        called["v3"] = True
        return {}

    async def fake_fl_only(*args, **kwargs):
        called["fl_only"] = True
        return {"source": "flashlive_only"}

    with patch.object(main, "_SPORTS_FL_ONLY", True):
        with patch.object(main, "sports_feed_v3", side_effect=fake_v3):
            with patch.object(main, "_sports_feed_fl_only",
                                side_effect=fake_fl_only):
                result = asyncio.run(main.sports_feed(
                    sport_id=1, timezone=0, indent_days=0, v=3,
                ))
                assert called["fl_only"] is True
                assert called["v3"] is False
                assert result["source"] == "flashlive_only"
