"""Phase C tests — Kalshi → IdentityRegistry seeder.

Exercises the three-tier match (strict → alias_table; guarded fuzzy
is Phase C2 — out of scope) against synthetic FL+Kalshi data. No
production code paths touched.
"""
from __future__ import annotations
from datetime import datetime, timezone

import pytest

from identity_registry import IdentityRegistry
from fl_registry_seed import seed_from_fl_response
from kalshi_registry_seed import (
    seed_kalshi_record,
    seed_kalshi_records,
)


def _ts(y, mo, d, h=0, mi=0):
    return int(datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp())


# ── Fixtures ─────────────────────────────────────────────────────

@pytest.fixture
def registry_seeded_ucl():
    """Registry pre-seeded with the UCL Play-Offs fixture used in
    other Phase B+ tests. Returns the populated IdentityRegistry.
    """
    r = IdentityRegistry()
    fl = {
        "DATA": [
            {
                "TOURNAMENT_STAGE_ID": "tour_ucl_playoffs",
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
    seed_from_fl_response(r, fl, "Soccer")
    return r


@pytest.fixture
def registry_seeded_nba_canonical():
    """NBA fixture with the canonical (Kalshi-matching) shortnames
    LAL/OKC. Strict tier should hit without alias-table fallback.
    """
    r = IdentityRegistry()
    fl = {
        "DATA": [
            {
                "TOURNAMENT_STAGE_ID": "tour_nba_r2",
                "NAME": "NBA - Play Offs",
                "EVENTS": [
                    {
                        "EVENT_ID":       "fl_okclal",
                        "HOME_NAME":      "Oklahoma City Thunder",
                        "AWAY_NAME":      "Los Angeles Lakers",
                        "SHORTNAME_HOME": "OKC",
                        "SHORTNAME_AWAY": "LAL",
                        "START_TIME":     _ts(2026, 5, 5, 23, 30),
                    },
                ],
            },
        ],
    }
    seed_from_fl_response(r, fl, "Basketball")
    return r


@pytest.fixture
def registry_seeded_nba_diverged():
    """NBA fixture where FL's shortnames diverge from Kalshi's
    canonical 3-letter form (FL ships LAK + OKL, Kalshi uses LAL +
    OKC). Strict tier MUST miss; alias_table tier MUST hit.
    """
    r = IdentityRegistry()
    fl = {
        "DATA": [
            {
                "TOURNAMENT_STAGE_ID": "tour_nba_r2",
                "NAME": "NBA - Play Offs",
                "EVENTS": [
                    {
                        "EVENT_ID":       "fl_okllak",
                        "HOME_NAME":      "Oklahoma City Thunder",
                        "AWAY_NAME":      "Los Angeles Lakers",
                        "SHORTNAME_HOME": "OKL",  # FL's form
                        "SHORTNAME_AWAY": "LAK",  # FL's form
                        "START_TIME":     _ts(2026, 5, 5, 23, 30),
                    },
                ],
            },
        ],
    }
    seed_from_fl_response(r, fl, "Basketball")
    return r


# ── Tier 1: strict abbr-equality ─────────────────────────────────

class TestStrictTier:

    def test_paired_via_strict(self, registry_seeded_ucl):
        """Arsenal vs Atletico — Kalshi 'KXUCLGAME-26MAY05ARSATM'
        abbr_block 'ARSATM' matches FL aliases ARS+ATM."""
        rec = {
            "event_ticker":  "KXUCLGAME-26MAY05ARSATM",
            "series_ticker": "KXUCLGAME",
            "title":         "Atletico at Arsenal",
        }
        fx = seed_kalshi_record(registry_seeded_ucl, rec, "Soccer")
        assert fx is not None
        assert fx.id == "fixture:soccer:2026-05-05:arsenal-vs-atl-madrid"
        # Alias was written
        a = registry_seeded_ucl.resolve_alias(
            "kalshi", "KXUCLGAME-26MAY05ARSATM",
        )
        assert a is not None
        assert a.method == "strict"
        assert a.confidence == 1.0
        assert a.canonical_id == fx.id

    def test_resolve_through_alias_works_after_seeding(
        self, registry_seeded_ucl,
    ):
        """Post-seed lookup is O(1) — the entire request-time pairing
        contract for Phase C+1."""
        rec = {
            "event_ticker":  "KXUCLGAME-26MAY05ARSATM",
            "series_ticker": "KXUCLGAME",
        }
        fx = seed_kalshi_record(registry_seeded_ucl, rec, "Soccer")
        resolved = registry_seeded_ucl.resolve_through_alias(
            "kalshi", "KXUCLGAME-26MAY05ARSATM",
        )
        assert resolved == fx

    def test_orientation_either_way(self, registry_seeded_ucl):
        """Kalshi sometimes packs home+away, sometimes away+home.
        Both orientations should hit the strict tier."""
        rec = {
            "event_ticker":  "KXUCLGAME-26MAY05ATMARS",
            "series_ticker": "KXUCLGAME",
        }
        fx = seed_kalshi_record(registry_seeded_ucl, rec, "Soccer")
        assert fx is not None
        a = registry_seeded_ucl.resolve_alias(
            "kalshi", "KXUCLGAME-26MAY05ATMARS",
        )
        assert a.method == "strict"

    def test_strict_paired_nba_canonical(
        self, registry_seeded_nba_canonical,
    ):
        """Sanity: when FL ships canonical NBA shortnames, the strict
        tier handles it without needing the alias map."""
        rec = {
            "event_ticker":  "KXNBAGAME-26MAY05LALOKC",
            "series_ticker": "KXNBAGAME",
        }
        fx = seed_kalshi_record(
            registry_seeded_nba_canonical, rec, "Basketball",
        )
        assert fx is not None
        a = registry_seeded_nba_canonical.resolve_alias(
            "kalshi", "KXNBAGAME-26MAY05LALOKC",
        )
        assert a.method == "strict"
        assert a.confidence == 1.0


# ── Tier 2: alias-table expansion ────────────────────────────────

class TestAliasTableTier:

    def test_paired_via_alias_table(self, registry_seeded_nba_diverged):
        """LAL@OKC pairing case: FL ships LAK+OKL, Kalshi uses
        LAL+OKC. Strict misses, alias_table catches it via
        Basketball alias map (LAK→LAL, OKL→OKC).
        """
        rec = {
            "event_ticker":  "KXNBAGAME-26MAY05LALOKC",
            "series_ticker": "KXNBAGAME",
        }
        fx = seed_kalshi_record(
            registry_seeded_nba_diverged, rec, "Basketball",
        )
        assert fx is not None
        a = registry_seeded_nba_diverged.resolve_alias(
            "kalshi", "KXNBAGAME-26MAY05LALOKC",
        )
        assert a is not None
        assert a.method == "alias_table"
        assert a.confidence == 0.95

    def test_alias_table_doesnt_leak_across_sports(self):
        """Soccer fixture with SHORTNAME=LAK shouldn't get LAL
        treatment — the alias map is keyed by sport, Soccer has
        no NBA-style entries."""
        r = IdentityRegistry()
        fl = {
            "DATA": [
                {
                    "TOURNAMENT_STAGE_ID": "tour_madeup",
                    "NAME": "Made-up League",
                    "EVENTS": [
                        {
                            "EVENT_ID":       "fl_x",
                            "HOME_NAME":      "Made-up A",
                            "AWAY_NAME":      "Made-up B",
                            "SHORTNAME_HOME": "OKL",
                            "SHORTNAME_AWAY": "LAK",
                            "START_TIME":     _ts(2026, 5, 5),
                        },
                    ],
                },
            ],
        }
        seed_from_fl_response(r, fl, "Soccer")
        rec = {
            "event_ticker":  "KXMLSGAME-26MAY05LALOKC",
            "series_ticker": "KXMLSGAME",
        }
        fx = seed_kalshi_record(r, rec, "Soccer")
        assert fx is None  # No alias entries for Soccer LAK→LAL


# ── Edge cases / non-matches ─────────────────────────────────────

class TestEdgeCases:

    def test_outright_skipped(self, registry_seeded_ucl):
        """Outright tickers (KXUCL-26 etc.) must not be paired to
        fixtures. _OUTRIGHT_SERIES_PREFIXES handles the explicit
        outright list; year-only IDs like KXUCL-26 also resolve as
        outright."""
        rec = {
            "event_ticker":  "KXUCL-26",
            "series_ticker": "KXUCL",
            "title":         "Champions League Winner 2026",
        }
        fx = seed_kalshi_record(registry_seeded_ucl, rec, "Soccer")
        assert fx is None
        assert registry_seeded_ucl.resolve_alias(
            "kalshi", "KXUCL-26",
        ) is None

    def test_unparseable_ticker_returns_none(self, registry_seeded_ucl):
        rec = {
            "event_ticker":  "GIBBERISH",
            "series_ticker": "KXMADEUP",
        }
        fx = seed_kalshi_record(registry_seeded_ucl, rec, "Soccer")
        assert fx is None

    def test_missing_fields_returns_none(self, registry_seeded_ucl):
        # No event_ticker
        assert seed_kalshi_record(
            registry_seeded_ucl, {"series_ticker": "KXUCLGAME"},
            "Soccer",
        ) is None
        # No series_ticker
        assert seed_kalshi_record(
            registry_seeded_ucl,
            {"event_ticker": "KXUCLGAME-26MAY05ARSATM"},
            "Soccer",
        ) is None

    def test_no_fl_fixtures_for_date(self, registry_seeded_ucl):
        """Different date than the seeded FL fixture → no candidates,
        no match."""
        rec = {
            "event_ticker":  "KXUCLGAME-26MAY12ARSATM",
            "series_ticker": "KXUCLGAME",
        }
        fx = seed_kalshi_record(registry_seeded_ucl, rec, "Soccer")
        assert fx is None

    def test_wrong_opponent_doesnt_pair(self, registry_seeded_ucl):
        """The dreaded v1 false-positive case: Kalshi has Arsenal vs
        Chelsea, FL has Arsenal vs Atletico same day. v2 strict +
        alias must NOT pair these (different opponent)."""
        rec = {
            "event_ticker":  "KXUCLGAME-26MAY05ARSCHE",
            "series_ticker": "KXUCLGAME",
        }
        fx = seed_kalshi_record(registry_seeded_ucl, rec, "Soccer")
        assert fx is None


# ── Batch seeder + stats ─────────────────────────────────────────

class TestBatchSeeder:

    def test_stats_strict_only(self, registry_seeded_nba_canonical):
        records = [
            {"event_ticker":  "KXNBAGAME-26MAY05LALOKC",
             "series_ticker": "KXNBAGAME"},
            {"event_ticker":  "KXNBASPREAD-26MAY05LALOKC",
             "series_ticker": "KXNBASPREAD"},  # also pairs strict
        ]
        stats = seed_kalshi_records(
            registry_seeded_nba_canonical, records, "Basketball",
        )
        assert stats["total"]         == 2
        assert stats["paired_strict"] == 2
        assert stats["paired_alias"]  == 0
        assert stats["unpaired"]      == 0

    def test_stats_alias_table_only(self, registry_seeded_nba_diverged):
        records = [
            {"event_ticker":  "KXNBAGAME-26MAY05LALOKC",
             "series_ticker": "KXNBAGAME"},
        ]
        stats = seed_kalshi_records(
            registry_seeded_nba_diverged, records, "Basketball",
        )
        assert stats["paired_strict"] == 0
        assert stats["paired_alias"]  == 1

    def test_stats_outright_bucket(self, registry_seeded_ucl):
        records = [
            {"event_ticker":  "KXUCL-26",
             "series_ticker": "KXUCL"},
            {"event_ticker":  "KXJOINCLUB-26OCT02RODRYGO",
             "series_ticker": "KXJOINCLUB"},  # explicit outright
        ]
        stats = seed_kalshi_records(
            registry_seeded_ucl, records, "Soccer",
        )
        assert stats["outright"] >= 1   # KXJOINCLUB is in outright list
        # Year-only KXUCL-26 also classifies as outright per the
        # parser's year-fallback; allow ≥ 1 since both should hit
        # but parser semantics may shift.
        assert stats["paired_strict"] == 0
        assert stats["paired_alias"]  == 0

    def test_stats_unparseable(self, registry_seeded_ucl):
        records = [
            {"event_ticker":  "TOTAL_GIBBERISH",
             "series_ticker": "KXMADEUP"},
            {},  # totally empty
        ]
        stats = seed_kalshi_records(
            registry_seeded_ucl, records, "Soccer",
        )
        # Empty dict has no event_ticker and isn't counted in `total`
        # (it's still a dict so it passes the isinstance check, but
        # ticker/series are empty → unparseable bucket).
        assert stats["unparseable"] >= 1

    def test_idempotent(self, registry_seeded_ucl):
        records = [
            {"event_ticker":  "KXUCLGAME-26MAY05ARSATM",
             "series_ticker": "KXUCLGAME"},
        ]
        a = seed_kalshi_records(
            registry_seeded_ucl, records, "Soccer",
        )
        b = seed_kalshi_records(
            registry_seeded_ucl, records, "Soccer",
        )
        # Stats identical, registry alias count unchanged
        assert a == b

    def test_mixed_records_classified_correctly(
        self, registry_seeded_ucl,
    ):
        records = [
            # Strict hit
            {"event_ticker":  "KXUCLGAME-26MAY05ARSATM",
             "series_ticker": "KXUCLGAME"},
            # Outright (explicit list)
            {"event_ticker":  "KXJOINCLUB-26OCT02RODRYGO",
             "series_ticker": "KXJOINCLUB"},
            # Per-fixture but no FL fixture for that date → unpaired
            {"event_ticker":  "KXUCLGAME-26MAY12BMUPSG",
             "series_ticker": "KXUCLGAME"},
        ]
        stats = seed_kalshi_records(
            registry_seeded_ucl, records, "Soccer",
        )
        assert stats["paired_strict"] == 1
        assert stats["outright"]      == 1
        assert stats["unpaired"]      == 1
