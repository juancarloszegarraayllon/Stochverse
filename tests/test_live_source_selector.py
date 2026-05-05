"""pytest suite for live_source_selector.py.

Phase 4 verification per SPORTS_V2_PLAN.md:
  - per-sport priority chain dispatches in correct order
  - falls through when a source returns None
  - aggregate overlay only runs for soccer cup ties
  - aggregate overlay doesn't override existing fields
  - enrich_for_record produces canonical _live_state schema

Tests inject fake source callables — no real-feed I/O.
"""
from __future__ import annotations

from typing import Callable, Optional

import pytest

from live_source_selector import (
    select_live_source, overlay_soccer_aggregate, is_cup_series,
    enrich_for_record,
    _SPORT_PRIORITY,
)


# ── Helpers ──────────────────────────────────────────────────────

def _fake_source(returns: Optional[dict]) -> Callable:
    """Build a fake match_game-style callable that always returns the
    same dict (or None). Test injection target."""
    def caller(title: str, sport: str) -> Optional[dict]:
        if returns is None:
            return None
        return dict(returns)  # copy so callers can mutate
    return caller


def _fake_raises(exc=RuntimeError("boom")):
    """Source that raises — verifies select_live_source isolates errors."""
    def caller(title: str, sport: str) -> Optional[dict]:
        raise exc
    return caller


def _game(state="in", **overrides):
    """Quick game dict builder."""
    g = {
        "state": state,
        "display_clock": "1H 23:14",
        "period": 1,
        "home_score": "1",
        "away_score": "0",
    }
    g.update(overrides)
    return g


# ── select_live_source — priority chain ──────────────────────────

class TestSelectPriority:

    def test_first_source_returns_match(self):
        sources = {"espn": _fake_source(_game()), "fl": _fake_source(None)}
        g = select_live_source("Lakers vs OKC", "Basketball", sources=sources)
        assert g is not None
        assert g["_source"] == "espn"

    def test_falls_through_to_second(self):
        sources = {
            "espn": _fake_source(None),  # ESPN miss
            "fl":   _fake_source(_game()),
        }
        g = select_live_source("Lakers vs OKC", "Basketball", sources=sources)
        assert g is not None
        assert g["_source"] == "fl"

    def test_falls_through_all_sources(self):
        sources = {
            "espn": _fake_source(None),
            "fl":   _fake_source(None),
            "sportsdb": _fake_source(None),
            "sofascore": _fake_source(None),
        }
        g = select_live_source("X", "Basketball", sources=sources)
        assert g is None

    def test_source_exception_is_isolated(self):
        """An exception from one source must not crash the chain."""
        sources = {
            "espn": _fake_raises(),
            "fl":   _fake_source(_game()),
        }
        g = select_live_source("X", "Basketball", sources=sources)
        assert g is not None
        assert g["_source"] == "fl"

    def test_unknown_sport_returns_none(self):
        sources = {"fl": _fake_source(_game())}
        g = select_live_source("X", "Quidditch", sources=sources)
        assert g is None

    def test_empty_title_returns_none(self):
        sources = {"fl": _fake_source(_game())}
        g = select_live_source("", "Soccer", sources=sources)
        assert g is None

    def test_kalshi_only_sport_returns_none(self):
        """Sports with no live source (Chess, Lacrosse) get None."""
        sources = {"fl": _fake_source(_game()), "espn": _fake_source(_game())}
        g = select_live_source("X", "Chess", sources=sources)
        assert g is None


class TestSportSpecificPriority:

    def test_soccer_prefers_fl(self):
        """Soccer's first source is FL, not ESPN."""
        sources = {
            "espn": _fake_source(_game(state="espn_marker")),
            "fl":   _fake_source(_game(state="fl_marker")),
        }
        g = select_live_source("Arsenal vs Atletico", "Soccer", sources=sources)
        assert g["state"] == "fl_marker"
        assert g["_source"] == "fl"

    def test_basketball_prefers_espn(self):
        """Basketball's first source is ESPN."""
        sources = {
            "espn": _fake_source(_game(state="espn_marker")),
            "fl":   _fake_source(_game(state="fl_marker")),
        }
        g = select_live_source("Lakers vs OKC", "Basketball", sources=sources)
        assert g["state"] == "espn_marker"
        assert g["_source"] == "espn"

    def test_hockey_prefers_espn(self):
        sources = {
            "espn": _fake_source(_game()),
            "fl":   _fake_source(None),
        }
        g = select_live_source("X", "Hockey", sources=sources)
        assert g["_source"] == "espn"

    def test_tennis_only_fl(self):
        """Tennis has only FL in the chain."""
        sources = {"espn": _fake_source(_game()), "fl": _fake_source(None)}
        g = select_live_source("X", "Tennis", sources=sources)
        # ESPN result should be ignored (Tennis chain doesn't include ESPN)
        assert g is None

    def test_priority_chain_stable(self):
        """The priority chain matches what's documented in the audit."""
        assert _SPORT_PRIORITY["Basketball"][0] == "espn"
        assert _SPORT_PRIORITY["Soccer"][0] == "fl"
        assert _SPORT_PRIORITY["Hockey"][0] == "espn"
        assert _SPORT_PRIORITY["Tennis"] == ["fl"]
        assert _SPORT_PRIORITY["Chess"] == []


# ── is_cup_series ────────────────────────────────────────────────

class TestIsCupSeries:

    @pytest.mark.parametrize("base, expected", [
        ("KXUCL", True),
        ("KXUEL", True),
        ("KXUECL", True),
        ("KXCONMEBOLLIB", True),
        ("KXCONMEBOLSUD", True),
        ("KXFACUP", True),
        ("KXEPL", False),                # league, not cup
        ("KXLALIGA", False),
        ("KXMLS", False),
        ("KXBRASILEIRO", False),
        ("KXNBA", False),                # not soccer
        ("", False),
    ])
    def test_is_cup(self, base, expected):
        assert is_cup_series(base) is expected

    def test_lowercase_input(self):
        """Should normalize case."""
        assert is_cup_series("kxucl") is True


# ── overlay_soccer_aggregate ─────────────────────────────────────

class TestOverlayAggregate:

    def test_does_nothing_when_aggregate_already_present(self):
        g = {"aggregate_home": 1, "aggregate_away": 1, "is_two_leg": True}
        # Lookup must NOT be called
        called = []
        def lookup(t):
            called.append(t)
            return {"aggregate_home": 99}  # would override if called
        result = overlay_soccer_aggregate(
            g, "X", "KXUCL", sofa_lookup=lookup,
        )
        assert called == [], "lookup should not be called when agg already present"
        assert result["aggregate_home"] == 1

    def test_does_nothing_for_non_cup_series(self):
        """League fixtures (KXEPL, KXLALIGA) should never look up aggregate."""
        g = {"aggregate_home": None, "aggregate_away": None}
        called = []
        def lookup(t):
            called.append(t)
            return {"aggregate_home": 99}
        result = overlay_soccer_aggregate(
            g, "X", "KXEPL", sofa_lookup=lookup,
        )
        assert called == []
        assert result["aggregate_home"] is None

    def test_overlays_when_cup_and_missing(self):
        g = {"aggregate_home": None, "aggregate_away": None}
        def lookup(t):
            return {
                "aggregate_home": 4, "aggregate_away": 5,
                "is_two_leg": True, "leg_number": 2,
                "round_name": "Quarter-finals",
            }
        result = overlay_soccer_aggregate(
            g, "Bayern vs PSG", "KXUCL", sofa_lookup=lookup,
        )
        assert result["aggregate_home"] == 4
        assert result["aggregate_away"] == 5
        assert result["is_two_leg"] is True
        assert result["leg_number"] == 2
        assert result["round_name"] == "Quarter-finals"

    def test_overlay_does_not_overwrite_existing_fields(self):
        """If g has SOME aggregate fields and sofa returns more, only
        the missing fields are filled."""
        g = {"aggregate_home": 1, "aggregate_away": None, "is_two_leg": True}
        def lookup(t):
            return {"aggregate_home": 99, "aggregate_away": 5,
                    "is_two_leg": False}
        result = overlay_soccer_aggregate(
            g, "X", "KXUCL", sofa_lookup=lookup,
        )
        assert result["aggregate_home"] == 1   # not overwritten
        assert result["aggregate_away"] == 5   # filled in
        assert result["is_two_leg"] is True    # not overwritten

    def test_lookup_exception_is_swallowed(self):
        """Aggregate lookup failures must not break enrichment."""
        g = {"aggregate_home": None, "aggregate_away": None}
        def lookup(t):
            raise RuntimeError("sofa down")
        result = overlay_soccer_aggregate(
            g, "X", "KXUCL", sofa_lookup=lookup,
        )
        assert result["aggregate_home"] is None
        assert result == g  # unchanged

    def test_lookup_returns_none(self):
        g = {"aggregate_home": None, "aggregate_away": None}
        result = overlay_soccer_aggregate(
            g, "X", "KXUCL", sofa_lookup=lambda t: None,
        )
        assert result["aggregate_home"] is None  # still None

    def test_g_is_none_passthrough(self):
        result = overlay_soccer_aggregate(
            None, "X", "KXUCL", sofa_lookup=lambda t: {"aggregate_home": 1},
        )
        assert result is None


# ── enrich_for_record (drop-in replacement for v1) ───────────────

class TestEnrichForRecord:

    def test_no_source_returns_empty(self):
        sources = {"fl": _fake_source(None)}
        out = enrich_for_record(
            "X", "Tennis", record={}, sources=sources,
        )
        assert out == {}

    def test_basic_basketball_state(self):
        sources = {"espn": _fake_source(_game(
            state="in", display_clock="Q3 5:23", period=3,
            series_summary="OKC leads 2-1", series_home_wins=2,
            series_away_wins=1, series_game_number=4, is_playoff=True,
        ))}
        out = enrich_for_record(
            "OKC at LAL", "Basketball", record={}, sources=sources,
        )
        assert out["state"] == "in"
        assert out["display_clock"] == "Q3 5:23"
        assert out["period"] == 3
        assert out["series_summary"] == "OKC leads 2-1"
        assert out["series_home_wins"] == 2
        assert out["is_playoff"] is True
        assert out["_source"] == "espn"

    def test_soccer_cup_aggregate_overlay(self):
        """Soccer cup-tie record where FL returns no aggregate but
        SofaScore does — overlay should fill it in."""
        sources = {"fl": _fake_source(_game(
            state="in",
            aggregate_home=None,
            aggregate_away=None,
        ))}
        sofa_lookup = lambda t: {
            "aggregate_home": 4, "aggregate_away": 5,
            "is_two_leg": True, "leg_number": 2, "round_name": "QF",
        }
        record = {"series_ticker": "KXUCLGAME"}
        out = enrich_for_record(
            "Bayern vs PSG", "Soccer", record=record,
            sources=sources, sofa_lookup=sofa_lookup,
        )
        assert out["aggregate_home"] == 4
        assert out["aggregate_away"] == 5
        assert out["is_two_leg"] is True
        assert out["leg_number"] == 2
        assert out["round_name"] == "QF"

    def test_soccer_league_no_aggregate_overlay(self):
        """Soccer league fixture (not a cup) — aggregate stays None."""
        sources = {"fl": _fake_source(_game(state="in"))}
        sofa_lookup = lambda t: {"aggregate_home": 99}  # would corrupt
        record = {"series_ticker": "KXEPLGAME"}
        out = enrich_for_record(
            "Arsenal vs Spurs", "Soccer", record=record,
            sources=sources, sofa_lookup=sofa_lookup,
        )
        assert out["aggregate_home"] is None
        assert out["is_two_leg"] is False

    def test_tennis_skip_aggregate_path(self):
        """Non-soccer sports never run aggregate overlay even if cup."""
        sources = {"fl": _fake_source(_game(state="in"))}
        sofa_lookup = lambda t: {"aggregate_home": 99}
        out = enrich_for_record(
            "Sinner vs Alcaraz", "Tennis", record={"series_ticker": "KXUCL"},
            sources=sources, sofa_lookup=sofa_lookup,
        )
        # Aggregate not in tennis output
        assert out.get("aggregate_home") is None

    def test_canonical_schema_keys(self):
        """All documented keys must be present in the output."""
        sources = {"fl": _fake_source(_game())}
        out = enrich_for_record(
            "X", "Soccer", record={}, sources=sources,
        )
        expected_keys = {
            "state", "display_clock", "period", "stage_start_ms",
            "captured_at_ms", "clock_running", "is_two_leg",
            "aggregate_home", "aggregate_away", "leg_number",
            "round_name", "series_home_wins", "series_away_wins",
            "series_summary", "series_game_number", "is_playoff",
            "_source",
        }
        assert set(out.keys()) == expected_keys

    def test_passes_through_when_record_is_none(self):
        """`record=None` should not crash the soccer aggregate path."""
        sources = {"fl": _fake_source(_game())}
        out = enrich_for_record(
            "X", "Soccer", record=None, sources=sources,
        )
        assert out["state"] == "in"
