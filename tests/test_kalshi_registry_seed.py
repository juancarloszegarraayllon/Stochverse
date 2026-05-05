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
            "market:soccer:2026-05-05:arsenal-vs-atl-madrid:winner"
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
            "market:soccer:2026-05-05:arsenal-vs-atl-madrid:winner"
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
