"""Phase C2c-c part 1 — registry-based pairing tests.

Exercises `pair_via_registry` and `diff_pairings` against synthetic
FL+Kalshi data shaped like the production cache. Validates that
the registry-based approach produces the same pairings as v2 for
the cases v2 already handles correctly.
"""
from __future__ import annotations
from datetime import datetime, timezone

import pytest

from registry_pairing import pair_via_registry, diff_pairings


def _ts(y, mo, d, h=0, mi=0):
    return int(datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp())


# ── pair_via_registry ────────────────────────────────────────────

class TestPairViaRegistry:

    def test_strict_pairing_full_walk(self):
        """UCL Arsenal-Atletico fixture pairs to all 3 Kalshi
        records (Game / Total / Spread) via the strict tier."""
        fl = {
            "DATA": [
                {
                    "TOURNAMENT_STAGE_ID": "tour_ucl",
                    "NAME": "Champions League - Play Offs",
                    "EVENTS": [
                        {
                            "EVENT_ID":       "fl_arsatm",
                            "HOME_NAME":      "Arsenal",
                            "AWAY_NAME":      "Atl. Madrid",
                            "SHORTNAME_HOME": "ARS",
                            "SHORTNAME_AWAY": "ATM",
                            "START_TIME":     _ts(2026, 5, 5, 19, 0),
                        },
                    ],
                },
            ],
        }
        kalshi = [
            {"event_ticker":  "KXUCLGAME-26MAY05ARSATM",
             "series_ticker": "KXUCLGAME"},
            {"event_ticker":  "KXUCLTOTAL-26MAY05ARSATM",
             "series_ticker": "KXUCLTOTAL"},
            {"event_ticker":  "KXUCLSPREAD-26MAY05ARSATM",
             "series_ticker": "KXUCLSPREAD"},
        ]
        pairings = pair_via_registry("Soccer", fl, kalshi)
        assert "fl_arsatm" in pairings
        assert set(pairings["fl_arsatm"]) == {
            "KXUCLGAME-26MAY05ARSATM",
            "KXUCLTOTAL-26MAY05ARSATM",
            "KXUCLSPREAD-26MAY05ARSATM",
        }

    def test_alias_table_pairing(self):
        """LAL@OKC NBA case where FL ships LAK+OKL and Kalshi uses
        LAL+OKC — the alias_table tier catches this."""
        fl = {
            "DATA": [
                {
                    "TOURNAMENT_STAGE_ID": "tour_nba",
                    "NAME": "NBA - Play Offs",
                    "EVENTS": [
                        {
                            "EVENT_ID":       "fl_okllak",
                            "HOME_NAME":      "Oklahoma City Thunder",
                            "AWAY_NAME":      "Los Angeles Lakers",
                            "SHORTNAME_HOME": "OKL",
                            "SHORTNAME_AWAY": "LAK",
                            "START_TIME":     _ts(2026, 5, 5, 23, 30),
                        },
                    ],
                },
            ],
        }
        kalshi = [
            {"event_ticker":  "KXNBAGAME-26MAY05LALOKC",
             "series_ticker": "KXNBAGAME"},
        ]
        pairings = pair_via_registry("Basketball", fl, kalshi)
        assert pairings["fl_okllak"] == ["KXNBAGAME-26MAY05LALOKC"]

    def test_unpaired_fl_event_returns_empty_list(self):
        """FL fixture with no matching Kalshi record gets an empty
        ticker list (not omitted)."""
        fl = {
            "DATA": [
                {
                    "TOURNAMENT_STAGE_ID": "tour",
                    "NAME": "Test League",
                    "EVENTS": [
                        {"EVENT_ID":       "fl_a",
                         "HOME_NAME":      "Foo",
                         "AWAY_NAME":      "Bar",
                         "SHORTNAME_HOME": "FOO",
                         "SHORTNAME_AWAY": "BAR",
                         "START_TIME":     _ts(2026, 5, 5)},
                    ],
                },
            ],
        }
        pairings = pair_via_registry("Soccer", fl, [])
        assert pairings == {"fl_a": []}

    def test_outright_kalshi_records_skipped(self):
        """Outright tickers (KXJOIN*, season-level futures) don't
        pair to any FL fixture — they contribute nothing to the
        result dict."""
        fl = {
            "DATA": [
                {
                    "TOURNAMENT_STAGE_ID": "tour",
                    "NAME": "Test",
                    "EVENTS": [
                        {"EVENT_ID":       "fl_a",
                         "HOME_NAME":      "Foo",
                         "AWAY_NAME":      "Bar",
                         "SHORTNAME_HOME": "FOO",
                         "SHORTNAME_AWAY": "BAR",
                         "START_TIME":     _ts(2026, 5, 5)},
                    ],
                },
            ],
        }
        kalshi = [
            {"event_ticker":  "KXJOINCLUB-26OCT02RODRYGO",
             "series_ticker": "KXJOINCLUB"},
            {"event_ticker":  "KXUCL-26",
             "series_ticker": "KXUCL"},
        ]
        pairings = pair_via_registry("Soccer", fl, kalshi)
        assert pairings == {"fl_a": []}

    def test_per_leg_pairs_to_parent_fixture(self):
        """Tennis set tickers resolve to their parent match
        fixture in the pairings dict."""
        fl = {
            "DATA": [
                {
                    "TOURNAMENT_STAGE_ID": "tour_atp",
                    "NAME": "ATP Test",
                    "EVENTS": [
                        {"EVENT_ID":       "fl_hijbas",
                         "HOME_NAME":      "Hijikata",
                         "AWAY_NAME":      "Basavareddy",
                         "SHORTNAME_HOME": "HIJ",
                         "SHORTNAME_AWAY": "BAS",
                         "START_TIME":     _ts(2026, 5, 5, 18, 0)},
                    ],
                },
            ],
        }
        kalshi = [
            {"event_ticker":  "KXATPSETWINNER-26MAY05HIJBAS-1",
             "series_ticker": "KXATPSETWINNER"},
            {"event_ticker":  "KXATPSETWINNER-26MAY05HIJBAS-2",
             "series_ticker": "KXATPSETWINNER"},
        ]
        pairings = pair_via_registry("Tennis", fl, kalshi)
        # Both set tickers resolve to the parent match fixture
        assert set(pairings["fl_hijbas"]) == {
            "KXATPSETWINNER-26MAY05HIJBAS-1",
            "KXATPSETWINNER-26MAY05HIJBAS-2",
        }

    def test_empty_fl_returns_empty_dict(self):
        assert pair_via_registry("Soccer", {}, []) == {}
        assert pair_via_registry("Soccer", {"DATA": []}, []) == {}

    def test_malformed_fl_data_safe(self):
        # DATA isn't a list → empty pairings, no crash
        assert pair_via_registry("Soccer", {"DATA": "x"}, []) == {}


# ── diff_pairings ────────────────────────────────────────────────

class TestDiffPairings:

    def test_identical_pairings(self):
        v2 = {"fl_a": ["T1", "T2"], "fl_b": ["T3"]}
        rg = {"fl_a": ["T1", "T2"], "fl_b": ["T3"]}
        d = diff_pairings(v2, rg)
        assert d["identical_count"]  == 2
        assert d["v2_only_pairings"]       == []
        assert d["registry_only_pairings"] == []
        assert d["mixed_pairings"]         == []

    def test_order_doesnt_matter(self):
        """Same set of tickers, different order → identical."""
        v2 = {"fl_a": ["T1", "T2"]}
        rg = {"fl_a": ["T2", "T1"]}
        d = diff_pairings(v2, rg)
        assert d["identical_count"] == 1

    def test_v2_only_pairing(self):
        """v2 paired tickers but registry didn't pair anything for
        this fixture (regression class)."""
        v2 = {"fl_a": ["T1"]}
        rg = {"fl_a": []}
        d = diff_pairings(v2, rg)
        assert d["identical_count"]    == 0
        assert d["v2_only_pairings"]   == [
            {"fl_event_id": "fl_a", "v2_only_tickers": ["T1"]},
        ]

    def test_registry_only_pairing(self):
        """Registry paired tickers but v2 didn't (improvement class —
        registry caught a pairing v2 missed, e.g. via guarded fuzzy)."""
        v2 = {"fl_a": []}
        rg = {"fl_a": ["T1"]}
        d = diff_pairings(v2, rg)
        assert d["registry_only_pairings"] == [
            {"fl_event_id": "fl_a", "registry_only_tickers": ["T1"]},
        ]

    def test_mixed_pairing(self):
        """Both sides paired SOME tickers but the sets differ —
        most interesting case for debugging."""
        v2 = {"fl_a": ["T1", "T2"]}
        rg = {"fl_a": ["T2", "T3"]}
        d = diff_pairings(v2, rg)
        assert d["identical_count"]   == 0
        assert d["mixed_pairings"]    == [
            {
                "fl_event_id":   "fl_a",
                "shared":        ["T2"],
                "v2_only":       ["T1"],
                "registry_only": ["T3"],
            },
        ]

    def test_disjoint_event_ids(self):
        """v2 has fl_a, registry has fl_b — both surface as
        side-only entries."""
        v2 = {"fl_a": ["T1"]}
        rg = {"fl_b": ["T2"]}
        d = diff_pairings(v2, rg)
        assert d["v2_only_pairings"] == [
            {"fl_event_id": "fl_a", "v2_only_tickers": ["T1"]},
        ]
        assert d["registry_only_pairings"] == [
            {"fl_event_id": "fl_b", "registry_only_tickers": ["T2"]},
        ]

    def test_empty_inputs(self):
        d = diff_pairings({}, {})
        assert d["identical_count"]         == 0
        assert d["v2_only_pairings"]        == []
        assert d["registry_only_pairings"]  == []
        assert d["mixed_pairings"]          == []
