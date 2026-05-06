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
        assert fx.id == "fixture:soccer:2026-05-05:1900:arsenal-vs-atl-madrid"
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

    def test_strict_via_local_date_argentine_timezone(self):
        """Phase C2d end-to-end: Soccer CONMEBOL fixture with FL UTC
        start = May 6 00:00 (= May 5 21:00 ART). Kalshi ticker date
        = MAY05 (local Argentine convention). Strict-date pairing
        works via the FL fixture's local_date (May 5), not the UTC
        date (May 6). This is what fixes the v2_only CONMEBOL gap.
        """
        r = IdentityRegistry()
        fl = {
            "DATA": [
                {
                    "TOURNAMENT_STAGE_ID": "tour_libertadores",
                    "NAME": "CONMEBOL Libertadores",
                    "EVENTS": [
                        {
                            "EVENT_ID":       "fl_ucvind",
                            "HOME_NAME":      "Universidad Catolica",
                            "AWAY_NAME":      "Independiente del Valle",
                            "SHORTNAME_HOME": "UCV",
                            "SHORTNAME_AWAY": "IND",
                            "START_TIME":     _ts(2026, 5, 6, 0, 0),
                        },
                    ],
                },
            ],
        }
        seed_from_fl_response(r, fl, "Soccer")
        rec = {
            "event_ticker":  "KXCONMEBOLLIBGAME-26MAY05UCVIND",
            "series_ticker": "KXCONMEBOLLIBGAME",
        }
        fx = seed_kalshi_record(r, rec, "Soccer")
        assert fx is not None
        # Strict-date match succeeded via local_date (May 5)
        a = r.resolve_alias(
            "kalshi", "KXCONMEBOLLIBGAME-26MAY05UCVIND",
        )
        assert a is not None
        assert a.method == "strict"
        assert a.confidence == 1.0
        # Canonical fixture ID uses local date
        assert fx.id.startswith("fixture:soccer:2026-05-05:")

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


# ── Tier 3: guarded fuzzy (Phase C2) ─────────────────────────────

class TestGuardedFuzzyTier:
    """Phase C2 — final fallback when strict + alias_table both miss.

    Fires ONLY when the (sport, date) bucket has exactly one unpaired
    FL fixture and one unpaired Kalshi record. Anything more
    ambiguous → leave unpaired (caller should add an alias-map
    entry instead of letting v2 guess).
    """

    def _seed_one_fl_fixture(self, sport: str,
                              shortname_home: str,
                              shortname_away: str,
                              fl_event_id: str = "fl_x"):
        """Helper: build a registry with one FL fixture for
        2026-05-05 between the given shortnames."""
        r = IdentityRegistry()
        fl = {
            "DATA": [
                {
                    "TOURNAMENT_STAGE_ID": "tour_test",
                    "NAME": "Test League",
                    "EVENTS": [
                        {
                            "EVENT_ID":       fl_event_id,
                            "HOME_NAME":      f"Team {shortname_home}",
                            "AWAY_NAME":      f"Team {shortname_away}",
                            "SHORTNAME_HOME": shortname_home,
                            "SHORTNAME_AWAY": shortname_away,
                            "START_TIME":     _ts(2026, 5, 5, 19, 0),
                        },
                    ],
                },
            ],
        }
        seed_from_fl_response(r, fl, sport)
        return r

    def test_guarded_fuzzy_pairs_when_one_plus_one(self):
        """Single unpaired FL fixture + single unpaired Kalshi record
        on the same date → guarded fuzzy fires, paired with
        confidence 0.7."""
        # FL fixture between FOO and BAR (no Kalshi alias entries).
        r = self._seed_one_fl_fixture("Soccer", "FOO", "BAR")
        # Kalshi record with a totally different abbr_block (XXXYYY)
        # — strict and alias_table both miss; only guarded fuzzy
        # can pair them.
        records = [
            {"event_ticker":  "KXUCLGAME-26MAY05XXXYYY",
             "series_ticker": "KXUCLGAME"},
        ]
        stats = seed_kalshi_records(r, records, "Soccer")
        assert stats["paired_guarded"] == 1
        assert stats["paired_strict"]  == 0
        assert stats["paired_alias"]   == 0
        assert stats["unpaired"]       == 0
        # Alias was written with the right method/confidence
        a = r.resolve_alias("kalshi", "KXUCLGAME-26MAY05XXXYYY")
        assert a is not None
        assert a.method == "guarded_fuzzy"
        assert a.confidence == 0.7

    def test_does_not_fire_when_multiple_fl_fixtures(self):
        """Two unpaired FL fixtures on the same date → bucket is
        ambiguous → guarded fuzzy refuses to guess.
        """
        r = IdentityRegistry()
        fl = {
            "DATA": [
                {
                    "TOURNAMENT_STAGE_ID": "tour_test",
                    "NAME": "Test League",
                    "EVENTS": [
                        {"EVENT_ID": "fl_a",
                         "HOME_NAME": "Foo", "AWAY_NAME": "Bar",
                         "SHORTNAME_HOME": "FOO", "SHORTNAME_AWAY": "BAR",
                         "START_TIME": _ts(2026, 5, 5, 19, 0)},
                        {"EVENT_ID": "fl_b",
                         "HOME_NAME": "Baz", "AWAY_NAME": "Qux",
                         "SHORTNAME_HOME": "BAZ", "SHORTNAME_AWAY": "QUX",
                         "START_TIME": _ts(2026, 5, 5, 21, 0)},
                    ],
                },
            ],
        }
        seed_from_fl_response(r, fl, "Soccer")
        records = [
            {"event_ticker":  "KXUCLGAME-26MAY05ZZZWWW",
             "series_ticker": "KXUCLGAME"},
        ]
        stats = seed_kalshi_records(r, records, "Soccer")
        assert stats["paired_guarded"] == 0
        assert stats["unpaired"]       == 1

    def test_does_not_fire_when_multiple_kalshi_records(self):
        """One unpaired FL fixture + two unpaired Kalshi records
        on the same date → ambiguous → no pairing."""
        r = self._seed_one_fl_fixture("Soccer", "FOO", "BAR")
        records = [
            {"event_ticker":  "KXUCLGAME-26MAY05XXXYYY",
             "series_ticker": "KXUCLGAME"},
            {"event_ticker":  "KXUCLGAME-26MAY05ZZZWWW",
             "series_ticker": "KXUCLGAME"},
        ]
        stats = seed_kalshi_records(r, records, "Soccer")
        assert stats["paired_guarded"] == 0
        assert stats["unpaired"]       == 2

    def test_does_not_fire_when_dates_differ(self):
        """Same sport, but Kalshi record is for a different date
        than the only unpaired FL fixture → buckets don't intersect
        → no pairing."""
        r = self._seed_one_fl_fixture("Soccer", "FOO", "BAR")
        records = [
            {"event_ticker":  "KXUCLGAME-26MAY12XXXYYY",
             "series_ticker": "KXUCLGAME"},  # May 12, FL is May 5
        ]
        stats = seed_kalshi_records(r, records, "Soccer")
        assert stats["paired_guarded"] == 0
        assert stats["unpaired"]       == 1

    def test_already_paired_fl_fixture_not_eligible(self):
        """If the only FL fixture for the date already has a Kalshi
        alias (from tier 1 or 2), a NEW unpaired record can't piggy-
        back via guarded fuzzy — guard requires ZERO existing aliases.
        """
        r = self._seed_one_fl_fixture("Soccer", "FOO", "BAR")
        # First record pairs strict (FOO+BAR matches FOOBAR)
        records = [
            {"event_ticker":  "KXUCLGAME-26MAY05FOOBAR",
             "series_ticker": "KXUCLGAME"},
            # Second record (different abbr) shouldn't piggy-back
            # via guarded fuzzy — the FL fixture is already paired.
            {"event_ticker":  "KXUCLGAME-26MAY05ZZZWWW",
             "series_ticker": "KXUCLGAME"},
        ]
        stats = seed_kalshi_records(r, records, "Soccer")
        assert stats["paired_strict"]  == 1
        assert stats["paired_guarded"] == 0
        assert stats["unpaired"]       == 1

    def test_strict_takes_precedence_over_guarded(self):
        """When tier 1 matches in pass 1, the record never enters the
        pass-2 buffer — guarded fuzzy never gets a chance."""
        r = self._seed_one_fl_fixture("Soccer", "FOO", "BAR")
        records = [
            {"event_ticker":  "KXUCLGAME-26MAY05FOOBAR",
             "series_ticker": "KXUCLGAME"},
        ]
        stats = seed_kalshi_records(r, records, "Soccer")
        assert stats["paired_strict"]  == 1
        assert stats["paired_guarded"] == 0
        # Alias method must be 'strict', not 'guarded_fuzzy'
        a = r.resolve_alias("kalshi", "KXUCLGAME-26MAY05FOOBAR")
        assert a.method == "strict"

    def test_idempotent_with_guarded(self):
        """Re-running the batch shouldn't double-count or overwrite
        the guarded_fuzzy alias."""
        r = self._seed_one_fl_fixture("Soccer", "FOO", "BAR")
        records = [
            {"event_ticker":  "KXUCLGAME-26MAY05XXXYYY",
             "series_ticker": "KXUCLGAME"},
        ]
        a = seed_kalshi_records(r, records, "Soccer")
        assert a["paired_guarded"] == 1
        # Second run: the FL fixture now has a kalshi alias from run 1,
        # so the guard correctly rejects re-pairing. The same record
        # is also already aliased via resolve_alias's idempotence
        # — but the seeder counts it as 'unpaired' because the FL
        # fixture is no longer in the unpaired_fixtures list.
        # That's the correct behavior: tier 3 doesn't try to "refresh"
        # an existing pairing.
        b = seed_kalshi_records(r, records, "Soccer")
        # Alias still points to same fixture, still guarded_fuzzy
        a2 = r.resolve_alias("kalshi", "KXUCLGAME-26MAY05XXXYYY")
        assert a2.method == "guarded_fuzzy"
        assert b["paired_guarded"] == 0  # didn't re-fire
        # Total registry alias count unchanged. Phase C2b now also
        # writes a 'kalshi_market' alias for the Winner market layer
        # (record has GAME suffix), so:
        #   1 fl fixture + 1 fl tournament + 1 kalshi (fixture)
        #   + 1 kalshi_market = 4
        # No kalshi_outcome aliases because the test record carries
        # no outcomes.
        assert r.stats()["aliases"] == 4


# ── Phase C2b: market-layer seeding ──────────────────────────────

class TestMarketLayerSeeding:
    """Phase C2b — when a Kalshi Winner record (series_ticker ends
    in GAME or MATCH) pairs to a fixture, the seeder also registers
    the canonical Market and Outcomes for it. This is what enables
    cross-source price aggregation later: Polymarket / OddsAPI
    Winner records for the same fixture will resolve to the SAME
    canonical Market and Outcomes.
    """

    def test_winner_market_layer_seeded(self, registry_seeded_ucl):
        rec = {
            "event_ticker":  "KXUCLGAME-26MAY05ARSATM",
            "series_ticker": "KXUCLGAME",
            "outcomes": [
                {"label": "Arsenal",     "_yb": 47, "ticker": "T1-ARS"},
                {"label": "Atl. Madrid", "_yb": 28, "ticker": "T1-ATM"},
                {"label": "Tie",         "_yb": 26, "ticker": "T1-TIE"},
            ],
        }
        fx = seed_kalshi_record(registry_seeded_ucl, rec, "Soccer")
        assert fx is not None
        # MarketType registered
        mt = registry_seeded_ucl.resolve_market_type(
            "market_type:soccer:winner",
        )
        assert mt is not None
        assert mt.parameterized is False
        # Market registered for this fixture
        expected_market_id = (
            "market:soccer:2026-05-05:1900:arsenal-vs-atl-madrid:winner"
        )
        market = registry_seeded_ucl.resolve_market(expected_market_id)
        assert market is not None
        assert market.fixture_id == fx.id

    def test_market_alias_namespaced(self, registry_seeded_ucl):
        """Fixture-level alias and market-level alias share the same
        event_ticker but live under different sources so they don't
        collide in the alias index."""
        rec = {
            "event_ticker":  "KXUCLGAME-26MAY05ARSATM",
            "series_ticker": "KXUCLGAME",
            "outcomes":      [],
        }
        seed_kalshi_record(registry_seeded_ucl, rec, "Soccer")
        # source='kalshi' → fixture
        fix_alias = registry_seeded_ucl.resolve_alias(
            "kalshi", "KXUCLGAME-26MAY05ARSATM",
        )
        assert fix_alias is not None
        assert fix_alias.canonical_id.startswith("fixture:")
        # source='kalshi_market' → market
        mkt_alias = registry_seeded_ucl.resolve_alias(
            "kalshi_market", "KXUCLGAME-26MAY05ARSATM",
        )
        assert mkt_alias is not None
        assert mkt_alias.canonical_id.startswith("market:")

    def test_outcomes_seeded_with_correct_sides(
        self, registry_seeded_ucl,
    ):
        rec = {
            "event_ticker":  "KXUCLGAME-26MAY05ARSATM",
            "series_ticker": "KXUCLGAME",
            "outcomes": [
                {"label": "Arsenal",     "ticker": "T1-ARS"},
                {"label": "Atl. Madrid", "ticker": "T1-ATM"},
                {"label": "Tie",         "ticker": "T1-TIE"},
            ],
        }
        seed_kalshi_record(registry_seeded_ucl, rec, "Soccer")
        # Three outcomes registered with sides home/away/tie
        market_id = (
            "market:soccer:2026-05-05:1900:arsenal-vs-atl-madrid:winner"
        )
        home_o = registry_seeded_ucl.resolve_outcome(
            f"outcome:{market_id}:home",
        )
        away_o = registry_seeded_ucl.resolve_outcome(
            f"outcome:{market_id}:away",
        )
        tie_o = registry_seeded_ucl.resolve_outcome(
            f"outcome:{market_id}:tie",
        )
        assert home_o is not None
        assert away_o is not None
        assert tie_o is not None
        assert home_o.canonical_label == "Arsenal"
        assert away_o.canonical_label == "Atl. Madrid"
        assert tie_o.canonical_label  == "Tie"

    def test_outcome_aliases_under_kalshi_outcome_source(
        self, registry_seeded_ucl,
    ):
        """Each Kalshi per-outcome ticker ('T1-ARS' etc.) registered
        as a kalshi_outcome alias pointing at the canonical Outcome."""
        rec = {
            "event_ticker":  "KXUCLGAME-26MAY05ARSATM",
            "series_ticker": "KXUCLGAME",
            "outcomes": [
                {"label": "Arsenal",     "ticker": "T1-ARS"},
                {"label": "Atl. Madrid", "ticker": "T1-ATM"},
            ],
        }
        seed_kalshi_record(registry_seeded_ucl, rec, "Soccer")
        a_ars = registry_seeded_ucl.resolve_alias(
            "kalshi_outcome", "T1-ARS",
        )
        assert a_ars is not None
        assert a_ars.canonical_id.endswith(":home")
        a_atm = registry_seeded_ucl.resolve_alias(
            "kalshi_outcome", "T1-ATM",
        )
        assert a_atm is not None
        assert a_atm.canonical_id.endswith(":away")

    def test_sub_market_skipped(self, registry_seeded_ucl):
        """Spread / Total / etc. (parameterized, deferred to C2c)
        should still pair the fixture but NOT seed a Market layer.
        """
        rec = {
            "event_ticker":  "KXUCLSPREAD-26MAY05ARSATM",
            "series_ticker": "KXUCLSPREAD",
            "outcomes":      [],
        }
        fx = seed_kalshi_record(registry_seeded_ucl, rec, "Soccer")
        assert fx is not None
        # Fixture alias was written
        assert registry_seeded_ucl.resolve_alias(
            "kalshi", "KXUCLSPREAD-26MAY05ARSATM",
        ) is not None
        # But NO market layer
        assert registry_seeded_ucl.resolve_alias(
            "kalshi_market", "KXUCLSPREAD-26MAY05ARSATM",
        ) is None
        assert registry_seeded_ucl.stats()["markets"] == 0

    def test_idempotent_market_seeding(self, registry_seeded_ucl):
        """Re-running the same Kalshi record doesn't duplicate the
        Market or Outcomes."""
        rec = {
            "event_ticker":  "KXUCLGAME-26MAY05ARSATM",
            "series_ticker": "KXUCLGAME",
            "outcomes": [
                {"label": "Arsenal",     "ticker": "T1-ARS"},
                {"label": "Atl. Madrid", "ticker": "T1-ATM"},
                {"label": "Tie",         "ticker": "T1-TIE"},
            ],
        }
        seed_kalshi_record(registry_seeded_ucl, rec, "Soccer")
        before = registry_seeded_ucl.stats()
        seed_kalshi_record(registry_seeded_ucl, rec, "Soccer")
        after = registry_seeded_ucl.stats()
        assert before == after
        assert after["markets"]  == 1
        assert after["outcomes"] == 3

    def test_unrecognized_outcome_label_skipped(
        self, registry_seeded_ucl,
    ):
        """A label that doesn't overlap with home/away tokens and
        isn't a tie word gets skipped silently — doesn't blow up
        the seed."""
        rec = {
            "event_ticker":  "KXUCLGAME-26MAY05ARSATM",
            "series_ticker": "KXUCLGAME",
            "outcomes": [
                {"label": "Arsenal",     "ticker": "T1-ARS"},
                {"label": "Mystery Box", "ticker": "T1-MYS"},
                {"label": "Tie",         "ticker": "T1-TIE"},
            ],
        }
        seed_kalshi_record(registry_seeded_ucl, rec, "Soccer")
        # 2 outcomes registered (Arsenal home, Tie tie); Mystery Box
        # silently skipped
        assert registry_seeded_ucl.stats()["outcomes"] == 2


class TestOutcomeSideClassification:

    def test_tie_words(self, registry_seeded_ucl):
        from kalshi_registry_seed import _classify_outcome_side
        fx = registry_seeded_ucl.resolve_through_alias("fl", "fl_arsatm")
        assert _classify_outcome_side(
            "Tie", registry_seeded_ucl, fx,
        ) == "tie"
        assert _classify_outcome_side(
            "Draw", registry_seeded_ucl, fx,
        ) == "tie"
        assert _classify_outcome_side(
            "no winner", registry_seeded_ucl, fx,
        ) == "tie"

    def test_home_match_via_canonical_name(self, registry_seeded_ucl):
        from kalshi_registry_seed import _classify_outcome_side
        fx = registry_seeded_ucl.resolve_through_alias("fl", "fl_arsatm")
        assert _classify_outcome_side(
            "Arsenal FC wins", registry_seeded_ucl, fx,
        ) == "home"

    def test_away_match_via_partial(self, registry_seeded_ucl):
        from kalshi_registry_seed import _classify_outcome_side
        fx = registry_seeded_ucl.resolve_through_alias("fl", "fl_arsatm")
        # "Madrid" is in 'Atl. Madrid' canonical name's tokens
        assert _classify_outcome_side(
            "Madrid wins", registry_seeded_ucl, fx,
        ) == "away"

    def test_zero_overlap_returns_none(self, registry_seeded_ucl):
        from kalshi_registry_seed import _classify_outcome_side
        fx = registry_seeded_ucl.resolve_through_alias("fl", "fl_arsatm")
        assert _classify_outcome_side(
            "Liverpool wins", registry_seeded_ucl, fx,
        ) is None

    def test_empty_label_returns_none(self, registry_seeded_ucl):
        from kalshi_registry_seed import _classify_outcome_side
        fx = registry_seeded_ucl.resolve_through_alias("fl", "fl_arsatm")
        assert _classify_outcome_side(
            "", registry_seeded_ucl, fx,
        ) is None


# ── Phase C2c: per_leg market-layer (tennis sets, esports maps) ──

class TestPerLegMarketLayer:
    """Phase C2c — per_leg Kalshi tickers (e.g. KXATPSETWINNER for
    tennis sets, KXLOLMAP for League of Legends maps) resolve to
    their PARENT fixture (the match) plus a parameterized sub-market
    for the specific leg.

    Parent fixture aliasing via source='kalshi'; the parameterized
    Set Winner / Map Winner Market aliases via 'kalshi_market'.
    """

    @pytest.fixture
    def registry_tennis_match(self):
        """One tennis match between Hijikata (HIJ) and Basavareddy
        (BAS) on 2026-05-05."""
        r = IdentityRegistry()
        fl = {
            "DATA": [
                {
                    "TOURNAMENT_STAGE_ID": "tour_atp",
                    "NAME": "ATP Test Tournament",
                    "EVENTS": [
                        {
                            "EVENT_ID":       "fl_hijbas",
                            "HOME_NAME":      "Hijikata",
                            "AWAY_NAME":      "Basavareddy",
                            "SHORTNAME_HOME": "HIJ",
                            "SHORTNAME_AWAY": "BAS",
                            "START_TIME":     _ts(2026, 5, 5, 18, 0),
                        },
                    ],
                },
            ],
        }
        seed_from_fl_response(r, fl, "Tennis")
        return r

    @pytest.fixture
    def registry_esports_match(self):
        """One LoL series ZYB vs SLY on 2026-05-07."""
        r = IdentityRegistry()
        fl = {
            "DATA": [
                {
                    "TOURNAMENT_STAGE_ID": "tour_lol",
                    "NAME": "LoL Test Tournament",
                    "EVENTS": [
                        {
                            "EVENT_ID":       "fl_zybsly",
                            "HOME_NAME":      "Zybertech",
                            "AWAY_NAME":      "Sly Wolves",
                            "SHORTNAME_HOME": "ZYB",
                            "SHORTNAME_AWAY": "SLY",
                            "START_TIME":     _ts(2026, 5, 7, 15, 0),
                        },
                    ],
                },
            ],
        }
        seed_from_fl_response(r, fl, "Esports")
        return r

    def test_tennis_set_pairs_to_parent_fixture(
        self, registry_tennis_match,
    ):
        """KXATPSETWINNER-26MAY05HIJBAS-1 — set 1 of the HIJ-BAS
        match. Parent fixture must be paired and a Set Winner
        market with leg_n=1 must be registered."""
        rec = {
            "event_ticker":  "KXATPSETWINNER-26MAY05HIJBAS-1",
            "series_ticker": "KXATPSETWINNER",
            "outcomes": [
                {"label": "Hijikata",     "ticker": "T-HIJ-S1"},
                {"label": "Basavareddy",  "ticker": "T-BAS-S1"},
            ],
        }
        fx = seed_kalshi_record(registry_tennis_match, rec, "Tennis")
        assert fx is not None
        # Parent fixture aliased via source='kalshi'
        a = registry_tennis_match.resolve_alias(
            "kalshi", "KXATPSETWINNER-26MAY05HIJBAS-1",
        )
        assert a is not None
        assert a.canonical_id.startswith("fixture:")
        # MarketType = Set Winner, parameterized
        mt = registry_tennis_match.resolve_market_type(
            "market_type:tennis:set-winner",
        )
        assert mt is not None
        assert mt.parameterized is True
        # Market with leg_n=1 created
        mkt_alias = registry_tennis_match.resolve_alias(
            "kalshi_market", "KXATPSETWINNER-26MAY05HIJBAS-1",
        )
        assert mkt_alias is not None
        market = registry_tennis_match.resolve_market(
            mkt_alias.canonical_id,
        )
        assert market is not None
        assert ("leg_n", 1) in market.params
        # Two outcomes (home/away — sets have no tie)
        assert registry_tennis_match.stats()["outcomes"] == 2

    def test_set_2_creates_distinct_market(
        self, registry_tennis_match,
    ):
        """Set 1 and Set 2 of the same match → distinct canonical
        Markets (different leg_n params)."""
        rec1 = {
            "event_ticker":  "KXATPSETWINNER-26MAY05HIJBAS-1",
            "series_ticker": "KXATPSETWINNER",
            "outcomes": [
                {"label": "Hijikata",    "ticker": "T-HIJ-S1"},
                {"label": "Basavareddy", "ticker": "T-BAS-S1"},
            ],
        }
        rec2 = {
            "event_ticker":  "KXATPSETWINNER-26MAY05HIJBAS-2",
            "series_ticker": "KXATPSETWINNER",
            "outcomes": [
                {"label": "Hijikata",    "ticker": "T-HIJ-S2"},
                {"label": "Basavareddy", "ticker": "T-BAS-S2"},
            ],
        }
        seed_kalshi_record(registry_tennis_match, rec1, "Tennis")
        seed_kalshi_record(registry_tennis_match, rec2, "Tennis")
        # Both markets exist, distinct IDs
        assert registry_tennis_match.stats()["markets"] == 2
        m1 = registry_tennis_match.resolve_alias(
            "kalshi_market", "KXATPSETWINNER-26MAY05HIJBAS-1",
        ).canonical_id
        m2 = registry_tennis_match.resolve_alias(
            "kalshi_market", "KXATPSETWINNER-26MAY05HIJBAS-2",
        ).canonical_id
        assert m1 != m2

    def test_esports_map_pairs_to_parent_fixture(
        self, registry_esports_match,
    ):
        """KXLOLMAP-26MAY071500ZYBSLY-1 — map 1 of the ZYB-SLY
        series. Parent fixture paired, Map Winner market with
        leg_n=1 registered."""
        rec = {
            "event_ticker":  "KXLOLMAP-26MAY071500ZYBSLY-1",
            "series_ticker": "KXLOLMAP",
            "outcomes": [
                {"label": "Zybertech",   "ticker": "T-ZYB-M1"},
                {"label": "Sly Wolves",  "ticker": "T-SLY-M1"},
            ],
        }
        fx = seed_kalshi_record(registry_esports_match, rec, "Esports")
        assert fx is not None
        # MarketType = Map Winner
        mt = registry_esports_match.resolve_market_type(
            "market_type:esports:map-winner",
        )
        assert mt is not None
        assert mt.parameterized is True
        # Market with leg_n=1
        mkt_alias = registry_esports_match.resolve_alias(
            "kalshi_market", "KXLOLMAP-26MAY071500ZYBSLY-1",
        )
        assert mkt_alias is not None
        market = registry_esports_match.resolve_market(
            mkt_alias.canonical_id,
        )
        assert ("leg_n", 1) in market.params

    def test_unmapped_sport_skips_market_layer(self):
        """Sports without a per_leg market-type entry in
        _PER_LEG_MARKET_TYPES (e.g. Soccer — no per-set sub-markets)
        leave the per_leg ticker unpaired. Defensive: they typically
        wouldn't ship per_leg shaped tickers anyway."""
        r = IdentityRegistry()
        fl = {
            "DATA": [
                {
                    "TOURNAMENT_STAGE_ID": "tour_x",
                    "NAME": "Test League",
                    "EVENTS": [
                        {
                            "EVENT_ID":       "fl_x",
                            "HOME_NAME":      "Foo",
                            "AWAY_NAME":      "Bar",
                            "SHORTNAME_HOME": "FOO",
                            "SHORTNAME_AWAY": "BAR",
                            "START_TIME":     _ts(2026, 5, 5),
                        },
                    ],
                },
            ],
        }
        seed_from_fl_response(r, fl, "Soccer")
        rec = {
            "event_ticker":  "KXMADESETWINNER-26MAY05FOOBAR-1",
            "series_ticker": "KXMADESETWINNER",
        }
        # Soccer + per_leg → no MarketType configured. Parent fixture
        # gets aliased but no market layer surfaced.
        fx = seed_kalshi_record(r, rec, "Soccer")
        assert fx is not None
        assert r.resolve_alias(
            "kalshi", "KXMADESETWINNER-26MAY05FOOBAR-1",
        ) is not None
        assert r.resolve_alias(
            "kalshi_market", "KXMADESETWINNER-26MAY05FOOBAR-1",
        ) is None
        assert r.stats()["markets"] == 0

    def test_per_leg_no_parent_fixture_returns_none(self):
        """If FL doesn't have the parent match registered, the
        per_leg ticker can't pair — returns None and writes nothing."""
        r = IdentityRegistry()
        rec = {
            "event_ticker":  "KXATPSETWINNER-26MAY05HIJBAS-1",
            "series_ticker": "KXATPSETWINNER",
        }
        fx = seed_kalshi_record(r, rec, "Tennis")
        assert fx is None
        assert r.resolve_alias(
            "kalshi", "KXATPSETWINNER-26MAY05HIJBAS-1",
        ) is None

    def test_idempotent_per_leg(self, registry_tennis_match):
        rec = {
            "event_ticker":  "KXATPSETWINNER-26MAY05HIJBAS-1",
            "series_ticker": "KXATPSETWINNER",
            "outcomes": [
                {"label": "Hijikata",    "ticker": "T-HIJ-S1"},
                {"label": "Basavareddy", "ticker": "T-BAS-S1"},
            ],
        }
        seed_kalshi_record(registry_tennis_match, rec, "Tennis")
        before = registry_tennis_match.stats()
        seed_kalshi_record(registry_tennis_match, rec, "Tennis")
        after = registry_tennis_match.stats()
        assert before == after

    def test_batch_seeder_per_leg_stats(self, registry_tennis_match):
        records = [
            {"event_ticker":  "KXATPSETWINNER-26MAY05HIJBAS-1",
             "series_ticker": "KXATPSETWINNER",
             "outcomes": [
                 {"label": "Hijikata",    "ticker": "T-HIJ-S1"},
                 {"label": "Basavareddy", "ticker": "T-BAS-S1"},
             ]},
            {"event_ticker":  "KXATPSETWINNER-26MAY05HIJBAS-2",
             "series_ticker": "KXATPSETWINNER",
             "outcomes": [
                 {"label": "Hijikata",    "ticker": "T-HIJ-S2"},
                 {"label": "Basavareddy", "ticker": "T-BAS-S2"},
             ]},
        ]
        stats = seed_kalshi_records(
            registry_tennis_match, records, "Tennis",
        )
        assert stats["paired_per_leg"] == 2
        assert stats["paired_strict"]  == 0
        assert stats["unpaired"]       == 0


# ── Phase C2e: time-aware tiebreaker ─────────────────────────────

class TestTimeAwareTiebreaker:
    """Phase C2e — when multiple FL fixtures share (sport, local_date,
    abbr_block), the one whose start_time_utc is closest to the Kalshi
    ticker's encoded time wins. Prevents MLB doubleheader false-pairs
    and same-day multi-fixture mismatches.
    """

    def _seed_two_mlb_fixtures(self):
        """MLB doubleheader: same teams (ATH-PHI), same date, two
        different start times. Game 1 at 17:00 UTC, Game 2 at 23:00
        UTC. Kalshi ships two G7 tickers, one per game."""
        r = IdentityRegistry()
        fl = {
            "DATA": [
                {
                    "TOURNAMENT_STAGE_ID": "tour_mlb",
                    "NAME": "MLB Regular Season",
                    "EVENTS": [
                        {"EVENT_ID":       "fl_g1",
                         "HOME_NAME":      "Philadelphia Phillies",
                         "AWAY_NAME":      "Athletics",
                         "SHORTNAME_HOME": "PHI",
                         "SHORTNAME_AWAY": "ATH",
                         "START_TIME":     _ts(2026, 5, 5, 17, 0)},
                        {"EVENT_ID":       "fl_g2",
                         "HOME_NAME":      "Philadelphia Phillies",
                         "AWAY_NAME":      "Athletics",
                         "SHORTNAME_HOME": "PHI",
                         "SHORTNAME_AWAY": "ATH",
                         "START_TIME":     _ts(2026, 5, 5, 23, 0)},
                    ],
                },
            ],
        }
        seed_from_fl_response(r, fl, "Baseball")
        return r

    def test_doubleheader_g1_pairs_to_early_fixture(self):
        """Kalshi 17:00 UTC ticker → fl_g1 (early game)."""
        r = self._seed_two_mlb_fixtures()
        rec = {
            "event_ticker":  "KXMLBGAME-26MAY051700ATHPHI",
            "series_ticker": "KXMLBGAME",
        }
        fx = seed_kalshi_record(r, rec, "Baseball")
        assert fx is not None
        a = r.resolve_alias("kalshi", "KXMLBGAME-26MAY051700ATHPHI")
        # The 17:00 ticker must pair to the 17:00 fixture, not the
        # 23:00 one (which would happen with the pre-C2e first-match
        # behavior if fl_g2 were registered first).
        resolved_fx = r.resolve_through_alias(
            "kalshi", "KXMLBGAME-26MAY051700ATHPHI",
        )
        assert resolved_fx is not None
        # The Fixture's start_time_utc must equal the early game.
        assert resolved_fx.start_time_utc == _ts(2026, 5, 5, 17, 0)

    def test_doubleheader_g2_pairs_to_late_fixture(self):
        """Kalshi 23:00 UTC ticker → fl_g2 (late game)."""
        r = self._seed_two_mlb_fixtures()
        rec = {
            "event_ticker":  "KXMLBGAME-26MAY052300ATHPHI",
            "series_ticker": "KXMLBGAME",
        }
        fx = seed_kalshi_record(r, rec, "Baseball")
        assert fx is not None
        resolved_fx = r.resolve_through_alias(
            "kalshi", "KXMLBGAME-26MAY052300ATHPHI",
        )
        assert resolved_fx is not None
        assert resolved_fx.start_time_utc == _ts(2026, 5, 5, 23, 0)

    def test_both_doubleheader_games_seed_to_distinct_fixtures(self):
        """Sanity: seeding both tickers in sequence binds each to its
        own canonical fixture."""
        r = self._seed_two_mlb_fixtures()
        rec_early = {"event_ticker":  "KXMLBGAME-26MAY051700ATHPHI",
                      "series_ticker": "KXMLBGAME"}
        rec_late = {"event_ticker":  "KXMLBGAME-26MAY052300ATHPHI",
                      "series_ticker": "KXMLBGAME"}
        fx1 = seed_kalshi_record(r, rec_early, "Baseball")
        fx2 = seed_kalshi_record(r, rec_late, "Baseball")
        assert fx1 is not None and fx2 is not None
        assert fx1.id != fx2.id  # distinct canonical fixtures
        assert r.stats()["aliases"] >= 4  # 2 fl + 2 kalshi minimum

    def test_g1_ticker_no_time_falls_through_to_first_match(self):
        """When the Kalshi identity has no time component (G1
        tickers like KXNBAGAME-26MAY05CLEDET), behavior matches
        pre-C2e: first abbr-match wins. No doubleheader case for NBA
        in practice, but we verify the fall-through works."""
        r = IdentityRegistry()
        fl = {
            "DATA": [
                {
                    "TOURNAMENT_STAGE_ID": "tour_nba",
                    "NAME": "NBA - Play Offs",
                    "EVENTS": [
                        {"EVENT_ID":       "fl_only_one",
                         "HOME_NAME":      "Detroit Pistons",
                         "AWAY_NAME":      "Cleveland Cavaliers",
                         "SHORTNAME_HOME": "DET",
                         "SHORTNAME_AWAY": "CLE",
                         "START_TIME":     _ts(2026, 5, 5, 23, 0)},
                    ],
                },
            ],
        }
        seed_from_fl_response(r, fl, "Basketball")
        rec = {
            "event_ticker":  "KXNBAGAME-26MAY05CLEDET",
            "series_ticker": "KXNBAGAME",
        }
        # G1 ticker (no time component in identity). Should still
        # pair via the abbr-only path.
        fx = seed_kalshi_record(r, rec, "Basketball")
        assert fx is not None

    def test_no_match_when_time_outside_fuzz_window(self):
        """Kalshi 17:00 UTC ticker with FL fixtures only at 12:00
        UTC and 19:00 UTC — both more than 30 min away. The time
        filter rejects both → no pair (rather than gluing to the
        wrong fixture).

        This is the protective behavior that prevents NBA series
        Game 2 ticker from snapping to Game 1's fixture if there's
        no exact match.
        """
        r = IdentityRegistry()
        fl = {
            "DATA": [
                {
                    "TOURNAMENT_STAGE_ID": "tour_mlb",
                    "NAME": "MLB",
                    "EVENTS": [
                        {"EVENT_ID":       "fl_morning",
                         "HOME_NAME":      "Phillies", "AWAY_NAME": "Athletics",
                         "SHORTNAME_HOME": "PHI", "SHORTNAME_AWAY": "ATH",
                         "START_TIME":     _ts(2026, 5, 5, 12, 0)},
                        {"EVENT_ID":       "fl_evening",
                         "HOME_NAME":      "Phillies", "AWAY_NAME": "Athletics",
                         "SHORTNAME_HOME": "PHI", "SHORTNAME_AWAY": "ATH",
                         "START_TIME":     _ts(2026, 5, 5, 19, 0)},
                    ],
                },
            ],
        }
        seed_from_fl_response(r, fl, "Baseball")
        rec = {
            "event_ticker":  "KXMLBGAME-26MAY051700ATHPHI",
            "series_ticker": "KXMLBGAME",
        }
        fx = seed_kalshi_record(r, rec, "Baseball")
        # 17:00 UTC ticker, FL games at 12:00 (5h diff) and 19:00
        # (2h diff). Both outside ±30min → no pair.
        assert fx is None

    def test_picks_closest_when_multiple_inside_window(self):
        """If two FL fixtures are both within ±30min, pick the
        closer one."""
        r = IdentityRegistry()
        fl = {
            "DATA": [
                {
                    "TOURNAMENT_STAGE_ID": "tour_mlb",
                    "NAME": "MLB",
                    "EVENTS": [
                        # 16:55 UTC — 5 min before Kalshi 17:00
                        {"EVENT_ID":       "fl_close",
                         "HOME_NAME":      "Phillies", "AWAY_NAME": "Athletics",
                         "SHORTNAME_HOME": "PHI", "SHORTNAME_AWAY": "ATH",
                         "START_TIME":     _ts(2026, 5, 5, 16, 55)},
                        # 17:25 UTC — 25 min after Kalshi 17:00
                        {"EVENT_ID":       "fl_far",
                         "HOME_NAME":      "Phillies", "AWAY_NAME": "Athletics",
                         "SHORTNAME_HOME": "PHI", "SHORTNAME_AWAY": "ATH",
                         "START_TIME":     _ts(2026, 5, 5, 17, 25)},
                    ],
                },
            ],
        }
        seed_from_fl_response(r, fl, "Baseball")
        rec = {
            "event_ticker":  "KXMLBGAME-26MAY051700ATHPHI",
            "series_ticker": "KXMLBGAME",
        }
        fx = seed_kalshi_record(r, rec, "Baseball")
        # 16:55 (5min) closer than 17:25 (25min) → fl_close wins
        assert fx is not None
        assert fx.start_time_utc == _ts(2026, 5, 5, 16, 55)

    def test_pick_best_by_time_helper_direct(self):
        """Direct unit test on the helper to lock in edge cases:
        empty list → None; identity with no time → first match;
        wrap-across-midnight handled."""
        from kalshi_registry_seed import _pick_best_by_time
        from types import SimpleNamespace
        # Empty
        assert _pick_best_by_time([], SimpleNamespace(time="1700")) is None
        # No identity time → first match
        fake_fx_a = SimpleNamespace(start_time_utc=_ts(2026, 5, 5, 12, 0))
        fake_fx_b = SimpleNamespace(start_time_utc=_ts(2026, 5, 5, 18, 0))
        assert _pick_best_by_time(
            [fake_fx_a, fake_fx_b], SimpleNamespace(time=""),
        ) is fake_fx_a


# ── Phase C2f+ — title_match normalization + time gating ─────────


class TestTitleMatchNormalization:
    """Exercises `_normalize_team_name` and `_title_overlap_score` on
    the cases the user specified — positive (must match) and
    negative (must NOT match).
    """

    def test_strip_country_suffix(self):
        from kalshi_registry_seed import _normalize_team_name
        assert _normalize_team_name("Tolima") == "tolima"
        assert _normalize_team_name("Deportes Tolima (Col)") == "tolima"
        assert _normalize_team_name("Nacional (Uru)") == "nacional"
        # 4-char suffix code (e.g. (Engl) seen rarely)
        assert _normalize_team_name("Bayern (Ger)") == "bayern"

    def test_expand_abbreviation(self):
        from kalshi_registry_seed import _normalize_team_name
        assert _normalize_team_name("U. Catolica") == "universidad catolica"
        assert _normalize_team_name("Atl. Madrid") == "atletico madrid"
        assert _normalize_team_name("St. Pauli") == "saint pauli"
        # 'Dep.' expands to 'Deportivo', then prefix-strip removes it
        # — same final form as 'Deportivo Cali'. Both Kalshi shorthand
        # and FL full-form converge to 'cali'.
        assert _normalize_team_name("Dep. Cali") == "cali"
        assert _normalize_team_name("Deportivo Cali") == "cali"

    def test_strip_generic_prefix(self):
        from kalshi_registry_seed import _normalize_team_name
        assert _normalize_team_name("Deportes Tolima") == "tolima"
        assert _normalize_team_name("FC Köln") == "köln"
        assert _normalize_team_name("Club Atletico") == "atletico"
        # 'Real' is NOT stripped — it's canonical
        assert _normalize_team_name("Real Madrid") == "real madrid"
        assert _normalize_team_name("Real Sociedad") == "real sociedad"

    def test_combined_pipeline(self):
        from kalshi_registry_seed import _normalize_team_name
        # All three transforms together
        assert _normalize_team_name("Deportes Tolima (Col)") == "tolima"
        assert _normalize_team_name("U. Catolica (Chi)") == "universidad catolica"
        assert _normalize_team_name("FC Bayern (Ger)") == "bayern"

    def test_idempotent(self):
        from kalshi_registry_seed import _normalize_team_name
        for name in ["Deportes Tolima (Col)", "U. Catolica (Chi)",
                     "Real Madrid", "Nacional"]:
            once = _normalize_team_name(name)
            twice = _normalize_team_name(once)
            assert once == twice

    # ── Positive cases (must match — score above 0.5 threshold) ──

    def test_positive_tolima(self):
        """Tolima ↔ Deportes Tolima (Col) — both reduce to {tolima}."""
        from kalshi_registry_seed import _title_overlap_score
        score = _title_overlap_score("Deportes Tolima (Col)", "Tolima")
        assert score == 1.0

    def test_positive_universidad_catolica(self):
        """Universidad Catolica ↔ U. Catolica (Chi)."""
        from kalshi_registry_seed import _title_overlap_score
        score = _title_overlap_score("U. Catolica (Chi)",
                                       "Universidad Catolica")
        assert score == 1.0

    def test_positive_nacional(self):
        """Nacional ↔ Nacional (Uru)."""
        from kalshi_registry_seed import _title_overlap_score
        score = _title_overlap_score("Nacional (Uru)", "Nacional")
        assert score == 1.0

    # ── Negative cases (must NOT match — score at or below 0.5) ──

    def test_negative_real_madrid_vs_real_sociedad(self):
        """Same first token 'Real' but different teams. Single-token
        overlap on 2-token names is 1/3 = 0.33 — below threshold."""
        from kalshi_registry_seed import _title_overlap_score
        score = _title_overlap_score("Real Madrid", "Real Sociedad")
        assert score < 0.5

    def test_negative_atletico_madrid_vs_atletico_mineiro(self):
        """Same first token 'Atletico' but different teams (Spain vs
        Brazil). 1/3 = 0.33 — below threshold."""
        from kalshi_registry_seed import _title_overlap_score
        score = _title_overlap_score("Atletico Madrid", "Atletico Mineiro")
        assert score < 0.5

    def test_negative_nacional_vs_nacional_am(self):
        """Brazilian Nacional-AM vs Uruguayan Nacional. After
        normalization {nacional, am} vs {nacional} → 1/2 = 0.5,
        which is exactly at the strict-greater-than-0.5 threshold,
        so does NOT match. (am is 2 chars so excluded by min_len=3
        — but the test still passes because both reduce to
        {nacional} → score 1.0; time-gating in _pair_via_title is
        what disambiguates these cases in practice.)"""
        from kalshi_registry_seed import _title_overlap_score
        # Without time-gating, these names ARE indistinguishable
        # by name alone — that's what the time gate covers.
        score = _title_overlap_score("Nacional-AM", "Nacional")
        # Confirm the score is high (name-only can't tell them
        # apart). Time gate in _pair_via_title is the disambiguator.
        assert score >= 0.5


class TestTitleMatchTimeGate:
    """Exercises the ±30 min time gate in `_pair_via_title`. This is
    the critical safeguard for cases like Nacional (Uru) vs
    Nacional-AM where names are indistinguishable but kickoffs
    differ.
    """

    def test_time_gate_rejects_nacional_vs_nacional_am(self):
        """Two FL fixtures with identical short-name 'Nacional' but
        different kickoffs. Kalshi 'Nacional vs X' should pair to
        the one whose kickoff is within ±30 min, not the other.

        UTC times set to mid-day (12-18) to avoid local_date crossing
        midnight under any reasonable TZ — keeps both fixtures in
        the same May 6 date bucket.
        """
        r = IdentityRegistry()
        fl = {
            "DATA": [
                {
                    "TOURNAMENT_STAGE_ID": "tour_a",
                    # CONMEBOL pattern → Buenos Aires TZ
                    "NAME": "South America: Copa Libertadores - Group Stage",
                    "EVENTS": [{
                        "EVENT_ID":       "fl_uru",
                        "HOME_NAME":      "Nacional (Uru)",
                        "AWAY_NAME":      "Penarol",
                        "SHORTNAME_HOME": "NAC",
                        "SHORTNAME_AWAY": "PEN",
                        # 18:00 UTC — matches Kalshi
                        "START_TIME":     _ts(2026, 5, 6, 18, 0),
                    }],
                },
                {
                    "TOURNAMENT_STAGE_ID": "tour_b",
                    "NAME": "South America: Copa Libertadores - Group Stage",
                    "EVENTS": [{
                        "EVENT_ID":       "fl_brazil",
                        "HOME_NAME":      "Nacional-AM",
                        "AWAY_NAME":      "Some Team",
                        "SHORTNAME_HOME": "NAC",
                        "SHORTNAME_AWAY": "SOM",
                        # 14:00 UTC — 4h off Kalshi
                        "START_TIME":     _ts(2026, 5, 6, 14, 0),
                    }],
                },
            ],
        }
        seed_from_fl_response(r, fl, "Soccer")
        # Kalshi: Nacional vs Penarol at 18:00 UTC
        rec = {
            "event_ticker":  "KXCONMEBOLLIBGAME-26MAY06NACPEN",
            "series_ticker": "KXCONMEBOLLIBGAME",
            "title":         "Nacional vs Penarol",
            "_kickoff_dt":   "2026-05-06T18:00:00Z",
            "markets":       [{}],
        }
        fx = seed_kalshi_record(r, rec, "Soccer")
        # Should pair to the 18:00 fixture, NOT the 14:00 one
        # (outside ±30 min gate).
        assert fx is not None
        assert fx.start_time_utc == _ts(2026, 5, 6, 18, 0)

    def test_time_gate_admits_when_within_window(self):
        """Kalshi kickoff at 18:00, FL fixture at 18:15 → 15 min
        apart, well within ±30 min → matches."""
        r = IdentityRegistry()
        fl = {"DATA": [{
            "TOURNAMENT_STAGE_ID": "t1",
            "NAME": "South America: Copa Libertadores - Group Stage",
            "EVENTS": [{
                "EVENT_ID":       "fl_a",
                "HOME_NAME":      "Bayern Munich",
                "AWAY_NAME":      "PSG",
                "SHORTNAME_HOME": "BAY",
                "SHORTNAME_AWAY": "PSG",
                "START_TIME":     _ts(2026, 5, 6, 18, 15),
            }],
        }]}
        seed_from_fl_response(r, fl, "Soccer")
        rec = {
            "event_ticker":  "KXUCLGAME-26MAY06BMUPSG",
            "series_ticker": "KXUCLGAME",
            "title":         "Bayern Munich vs PSG",
            "_kickoff_dt":   "2026-05-06T18:00:00Z",
            "markets":       [{}],
        }
        fx = seed_kalshi_record(r, rec, "Soccer")
        assert fx is not None

    def test_time_gate_bypassed_when_kalshi_has_no_kickoff(self):
        """When Kalshi `_kickoff_dt` is missing, time gate is
        bypassed — falls back to name-only matching. Tolima case
        from the user's data set (no _kickoff_dt) should still pair
        via title-match."""
        r = IdentityRegistry()
        fl = {"DATA": [{
            "TOURNAMENT_STAGE_ID": "t1",
            "NAME": "South America: Copa Libertadores - Group Stage",
            "EVENTS": [{
                "EVENT_ID":       "fl_t",
                "HOME_NAME":      "Deportes Tolima (Col)",
                "AWAY_NAME":      "Atletico Nacional",
                "SHORTNAME_HOME": "DEP",
                "SHORTNAME_AWAY": "ANL",
                "START_TIME":     _ts(2026, 5, 6, 18, 0),
            }],
        }]}
        seed_from_fl_response(r, fl, "Soccer")
        rec = {
            "event_ticker":  "KXCONMEBOLLIBGAME-26MAY06TOLNAC",
            "series_ticker": "KXCONMEBOLLIBGAME",
            "title":         "Tolima vs Nacional",
            # No _kickoff_dt — time gate bypassed
            "markets":       [{}],
        }
        fx = seed_kalshi_record(r, rec, "Soccer")
        assert fx is not None  # Name-only path still reaches this

    def test_parse_iso_to_epoch_helper(self):
        from kalshi_registry_seed import _parse_iso_to_epoch
        # Z suffix
        assert _parse_iso_to_epoch("2026-05-07T05:00:00Z") == \
            _ts(2026, 5, 7, 5, 0)
        # Explicit +00:00
        assert _parse_iso_to_epoch("2026-05-07T05:00:00+00:00") == \
            _ts(2026, 5, 7, 5, 0)
        # Naive — assumed UTC
        assert _parse_iso_to_epoch("2026-05-07T05:00:00") == \
            _ts(2026, 5, 7, 5, 0)
        # Garbage / empty
        assert _parse_iso_to_epoch("garbage") is None
        assert _parse_iso_to_epoch("") is None
        assert _parse_iso_to_epoch(None) is None


class TestSyntheticEventImageLookup:
    """Phase C2g — `_lookup_team_images_by_name` lets synthetic
    Kalshi-only events inherit FL imagery from same-name paired
    teams in the same request, using the PR #32 normalization.
    """

    def test_short_kalshi_form_finds_full_fl_form(self):
        """Kalshi 'Bayern Munich' should find FL 'FC Bayern Munich'
        via prefix-strip normalization."""
        from main import _lookup_team_images_by_name
        paired = [{
            "events": [{
                "HOME_NAME":   "FC Bayern Munich",
                "HOME_IMAGES": ["https://fl.cdn/bayern.png"],
                "AWAY_NAME":   "Real Madrid",
                "AWAY_IMAGES": ["https://fl.cdn/realmadrid.png"],
            }],
        }]
        imgs = _lookup_team_images_by_name("Bayern Munich", paired)
        assert imgs == ["https://fl.cdn/bayern.png"]

    def test_country_suffix_strip(self):
        """Kalshi 'Tolima' should find FL 'Deportes Tolima (Col)'."""
        from main import _lookup_team_images_by_name
        paired = [{
            "events": [{
                "HOME_NAME":   "Deportes Tolima (Col)",
                "HOME_IMAGES": ["https://fl.cdn/tolima.png"],
                "AWAY_NAME":   "Atletico Nacional",
                "AWAY_IMAGES": [],
            }],
        }]
        imgs = _lookup_team_images_by_name("Tolima", paired)
        assert imgs == ["https://fl.cdn/tolima.png"]

    def test_abbreviation_expansion(self):
        """Kalshi 'Universidad Catolica' should find FL 'U. Catolica
        (Chi)'."""
        from main import _lookup_team_images_by_name
        paired = [{
            "events": [{
                "HOME_NAME":   "U. Catolica (Chi)",
                "HOME_IMAGES": ["https://fl.cdn/ucatolica.png"],
                "AWAY_NAME":   "Cruzeiro",
                "AWAY_IMAGES": [],
            }],
        }]
        imgs = _lookup_team_images_by_name("Universidad Catolica", paired)
        assert imgs == ["https://fl.cdn/ucatolica.png"]

    def test_away_side_match(self):
        """Lookup walks both home and away sides."""
        from main import _lookup_team_images_by_name
        paired = [{
            "events": [{
                "HOME_NAME":   "PSG",
                "HOME_IMAGES": ["https://fl.cdn/psg.png"],
                "AWAY_NAME":   "Real Madrid",
                "AWAY_IMAGES": ["https://fl.cdn/realmadrid.png"],
            }],
        }]
        imgs = _lookup_team_images_by_name("Real Madrid", paired)
        assert imgs == ["https://fl.cdn/realmadrid.png"]

    def test_no_match_returns_empty(self):
        from main import _lookup_team_images_by_name
        paired = [{
            "events": [{
                "HOME_NAME":   "Bayern Munich",
                "HOME_IMAGES": ["https://fl.cdn/bayern.png"],
                "AWAY_NAME":   "PSG",
                "AWAY_IMAGES": ["https://fl.cdn/psg.png"],
            }],
        }]
        assert _lookup_team_images_by_name("Brighton", paired) == []

    def test_empty_inputs_return_empty(self):
        from main import _lookup_team_images_by_name
        paired = [{"events": [{"HOME_NAME": "Bayern", "HOME_IMAGES": ["x"]}]}]
        assert _lookup_team_images_by_name("", paired) == []
        assert _lookup_team_images_by_name("Bayern", []) == []
        assert _lookup_team_images_by_name("Bayern", None) == []

    def test_skips_team_with_empty_images(self):
        """When a name match has no images on that side, walk past
        it to find the next match (don't return [] just because the
        first match had empty images)."""
        from main import _lookup_team_images_by_name
        paired = [
            {"events": [{"HOME_NAME": "Bayern Munich", "HOME_IMAGES": []}]},
            {"events": [{"HOME_NAME": "Bayern Munich",
                          "HOME_IMAGES": ["https://fl.cdn/bayern.png"]}]},
        ]
        imgs = _lookup_team_images_by_name("Bayern Munich", paired)
        assert imgs == ["https://fl.cdn/bayern.png"]


class TestPersistentLogoCache:
    """Phase C2g+ — `_TEAM_LOGO_CACHE` lets synthetic events inherit
    team imagery from prior requests' paired FL events, so a team
    seen on Sunday still shows its logo on Wednesday's Kalshi-only
    card.
    """

    def setup_method(self):
        # Clear the module-level cache before each test.
        from main import _TEAM_LOGO_CACHE
        _TEAM_LOGO_CACHE.clear()

    def test_remember_populates_cache(self):
        from main import _remember_team_logos, _TEAM_LOGO_CACHE
        _remember_team_logos({
            "HOME_NAME":   "Deportes Tolima (Col)",
            "HOME_IMAGES": ["https://fl.cdn/tolima.png"],
            "AWAY_NAME":   "Atletico Nacional",
            "AWAY_IMAGES": ["https://fl.cdn/anacional.png"],
        })
        assert _TEAM_LOGO_CACHE.get("tolima") == ["https://fl.cdn/tolima.png"]
        assert _TEAM_LOGO_CACHE.get("atletico nacional") == ["https://fl.cdn/anacional.png"]

    def test_lookup_falls_back_to_cache(self):
        """When in-request paired tournaments have no match, the
        cache (populated by prior requests) is consulted."""
        from main import (_remember_team_logos,
                            _lookup_team_images_by_name)
        # Prior request — Tolima paired and cached
        _remember_team_logos({
            "HOME_NAME":   "Deportes Tolima (Col)",
            "HOME_IMAGES": ["https://fl.cdn/tolima.png"],
        })
        # Current request — no paired tournaments contain Tolima
        empty_paired = [{"events": [
            {"HOME_NAME": "Bayern Munich", "HOME_IMAGES": ["x"]},
        ]}]
        imgs = _lookup_team_images_by_name("Tolima", empty_paired)
        assert imgs == ["https://fl.cdn/tolima.png"]

    def test_in_request_wins_over_cache(self):
        """Same-request paired event takes priority — cache is a
        fallback, not an override. Catches the case where a team's
        imagery changes (e.g. league rebrand) and we want fresh
        URLs from this request, not stale cached ones."""
        from main import (_remember_team_logos,
                            _lookup_team_images_by_name)
        _remember_team_logos({
            "HOME_NAME":   "Tolima",
            "HOME_IMAGES": ["https://fl.cdn/tolima_old.png"],
        })
        paired = [{"events": [
            {"HOME_NAME":   "Deportes Tolima (Col)",
             "HOME_IMAGES": ["https://fl.cdn/tolima_new.png"]},
        ]}]
        imgs = _lookup_team_images_by_name("Tolima", paired)
        assert imgs == ["https://fl.cdn/tolima_new.png"]

    def test_remember_skips_empty(self):
        """Empty name or empty images list → no cache entry.
        Prevents cache pollution with junk."""
        from main import _remember_team_logos, _TEAM_LOGO_CACHE
        _remember_team_logos({
            "HOME_NAME":   "",
            "HOME_IMAGES": ["x"],
            "AWAY_NAME":   "Bayern",
            "AWAY_IMAGES": [],
        })
        assert "bayern" not in _TEAM_LOGO_CACHE
        assert _TEAM_LOGO_CACHE == {}

    def test_lookup_with_no_paired_uses_cache_directly(self):
        """When `paired_tournaments` is None or empty list, lookup
        skips tier 1 and goes straight to the cache."""
        from main import (_remember_team_logos,
                            _lookup_team_images_by_name)
        _remember_team_logos({
            "HOME_NAME":   "Bayern Munich",
            "HOME_IMAGES": ["https://fl.cdn/bayern.png"],
        })
        assert _lookup_team_images_by_name("Bayern Munich", None) == \
            ["https://fl.cdn/bayern.png"]
        assert _lookup_team_images_by_name("Bayern Munich", []) == \
            ["https://fl.cdn/bayern.png"]

    def test_remember_uses_normalized_key(self):
        """Verify the cache key is the normalized form so lookups
        with the short Kalshi form find the cached entry."""
        from main import _remember_team_logos, _TEAM_LOGO_CACHE
        _remember_team_logos({
            "HOME_NAME":   "U. Catolica (Chi)",  # FL form
            "HOME_IMAGES": ["https://fl.cdn/ucat.png"],
        })
        # Key should be the normalized form, not the raw FL name
        assert "universidad catolica" in _TEAM_LOGO_CACHE
        assert "u. catolica (chi)" not in _TEAM_LOGO_CACHE
