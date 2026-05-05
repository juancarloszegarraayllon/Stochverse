"""Phase A tests — canonical entity registry.

Exercises the public API end-to-end with synthetic data. No source
mappers (FL, Kalshi, etc.) involved — those are Phase B and beyond.

Test layout:
    TestSlugify         — slugify() helper edge cases
    TestIdBuilders      — make_*_id deterministic shape
    TestTeamRegistry    — register_team idempotence + alias merging
    TestPlayerRegistry
    TestCompetitionRegistry
    TestFixtureRegistry — version bumps on real change
    TestMarketTypeRegistry — parameterized vs non-parameterized
    TestMarketRegistry  — params required for parameterized types
    TestOutcomeRegistry
    TestAliasIndex      — method precedence, manual override safety
    TestResolveThroughAlias — full external→canonical resolution
    TestGlobalRegistry  — singleton behavior
"""
from __future__ import annotations
from datetime import date

import pytest

from identity_registry import (
    IdentityRegistry,
    slugify,
    make_team_id, make_player_id, make_competition_id,
    make_fixture_id, make_market_type_id, make_market_id,
    make_outcome_id,
    Team, Player, Competition, Fixture, MarketType, Market, Outcome,
    Alias,
    global_registry, reset_global_registry,
)


# ── slugify ──────────────────────────────────────────────────────

class TestSlugify:

    def test_basic_lowercase(self):
        assert slugify("Arsenal") == "arsenal"

    def test_strips_punctuation(self):
        assert slugify("Atl. Madrid") == "atl-madrid"

    def test_collapses_whitespace(self):
        assert slugify("Los  Angeles   Lakers") == "los-angeles-lakers"

    def test_strips_leading_trailing_dashes(self):
        assert slugify("  -  FC Bayern  -  ") == "fc-bayern"

    def test_unicode_handled(self):
        # Non-ASCII chars treated as separators — deterministic
        # downgrade. Mappers can override with ASCII canonical names.
        assert slugify("FC Bayern München") == "fc-bayern-m-nchen"

    def test_empty(self):
        assert slugify("") == ""

    def test_only_punctuation(self):
        assert slugify("---!!!---") == ""


# ── ID builders ──────────────────────────────────────────────────

class TestIdBuilders:

    def test_team_id_shape(self):
        assert (make_team_id("Basketball", "lal")
                == "team:basketball:lal")

    def test_team_id_sport_normalized(self):
        # Sport string is slugified inside the ID builder
        assert (make_team_id("Basketball", "lal")
                == make_team_id("BASKETBALL", "lal"))

    def test_player_id_shape(self):
        assert (make_player_id("Soccer", "rodrygo")
                == "player:soccer:rodrygo")

    def test_competition_id_shape(self):
        assert (make_competition_id("Basketball", "nba-playoffs")
                == "competition:basketball:nba-playoffs")

    def test_fixture_id_shape(self):
        fid = make_fixture_id(
            "Basketball", date(2026, 5, 5), "lakers", "thunder",
        )
        assert fid == "fixture:basketball:2026-05-05:lakers-vs-thunder"

    def test_market_type_id_shape(self):
        assert (make_market_type_id("Basketball", "winner")
                == "market_type:basketball:winner")

    def test_market_id_unparameterized(self):
        fid = make_fixture_id(
            "Basketball", date(2026, 5, 5), "lakers", "thunder",
        )
        mid = make_market_id(fid, "winner")
        assert mid == (
            "market:basketball:2026-05-05:lakers-vs-thunder:winner"
        )

    def test_market_id_parameterized_stable(self):
        """Same params (in any order) → same hash → same ID."""
        fid = make_fixture_id(
            "Soccer", date(2026, 5, 5), "arsenal", "atl-madrid",
        )
        a = make_market_id(fid, "over-under-goals",
                            (("threshold", 2.5), ("line", "main")))
        b = make_market_id(fid, "over-under-goals",
                            (("line", "main"), ("threshold", 2.5)))
        assert a == b
        assert a.startswith(
            "market:soccer:2026-05-05:arsenal-vs-atl-madrid:"
            "over-under-goals:"
        )

    def test_market_id_parameterized_different(self):
        """Different params → different hash → different ID."""
        fid = make_fixture_id(
            "Soccer", date(2026, 5, 5), "arsenal", "atl-madrid",
        )
        a = make_market_id(fid, "over-under-goals",
                            (("threshold", 2.5),))
        b = make_market_id(fid, "over-under-goals",
                            (("threshold", 3.5),))
        assert a != b

    def test_outcome_id_shape(self):
        oid = make_outcome_id("market:foo:winner", "home")
        assert oid == "outcome:market:foo:winner:home"


# ── Team registry ────────────────────────────────────────────────

class TestTeamRegistry:

    def test_register_returns_team(self):
        r = IdentityRegistry()
        t = r.register_team("Basketball", "Los Angeles Lakers")
        assert isinstance(t, Team)
        assert t.canonical_name == "Los Angeles Lakers"
        assert t.slug == "los-angeles-lakers"
        assert t.id == "team:basketball:los-angeles-lakers"

    def test_register_idempotent(self):
        r = IdentityRegistry()
        a = r.register_team("Basketball", "Los Angeles Lakers")
        b = r.register_team("Basketball", "Los Angeles Lakers")
        assert a.id == b.id
        assert r.stats()["teams"] == 1

    def test_register_explicit_slug(self):
        r = IdentityRegistry()
        t = r.register_team(
            "Basketball", "Los Angeles Lakers", slug="lal",
        )
        assert t.slug == "lal"
        assert t.id == "team:basketball:lal"

    def test_register_merges_aliases(self):
        r = IdentityRegistry()
        a = r.register_team(
            "Basketball", "Los Angeles Lakers",
            slug="lal", aliases={"Lakers", "LAK"},
        )
        b = r.register_team(
            "Basketball", "Los Angeles Lakers",
            slug="lal", aliases={"LAL", "LA Lakers"},
        )
        # Same Team — aliases accumulated
        assert b.id == a.id
        assert b.aliases == {"Lakers", "LAK", "LAL", "LA Lakers"}
        assert r.stats()["teams"] == 1

    def test_register_requires_name_or_slug(self):
        r = IdentityRegistry()
        with pytest.raises(ValueError):
            r.register_team("Basketball", "")

    def test_resolve_team(self):
        r = IdentityRegistry()
        t = r.register_team("Basketball", "Los Angeles Lakers",
                             slug="lal")
        assert r.resolve_team(t.id) == t

    def test_resolve_team_missing(self):
        r = IdentityRegistry()
        assert r.resolve_team("team:basketball:nope") is None

    def test_lookup_team_by_slug(self):
        r = IdentityRegistry()
        t = r.register_team("Basketball", "Los Angeles Lakers",
                             slug="lal")
        assert r.lookup_team("Basketball", "lal") == t


# ── Player registry ──────────────────────────────────────────────

class TestPlayerRegistry:

    def test_register_and_resolve(self):
        r = IdentityRegistry()
        p = r.register_player("Soccer", "Rodrygo Goes",
                                slug="rodrygo")
        assert p.id == "player:soccer:rodrygo"
        assert r.resolve_player(p.id) == p

    def test_idempotent(self):
        r = IdentityRegistry()
        a = r.register_player("Basketball", "LeBron James",
                                slug="lbj")
        b = r.register_player("Basketball", "LeBron James",
                                slug="lbj")
        assert a.id == b.id
        assert r.stats()["players"] == 1


# ── Competition registry ─────────────────────────────────────────

class TestCompetitionRegistry:

    def test_register_and_resolve(self):
        r = IdentityRegistry()
        c = r.register_competition(
            "Basketball", "NBA Playoffs Round 2",
            slug="nba-playoffs-r2",
        )
        assert c.id == "competition:basketball:nba-playoffs-r2"
        assert r.resolve_competition(c.id) == c


# ── Fixture registry ─────────────────────────────────────────────

class TestFixtureRegistry:

    def _setup(self, r: IdentityRegistry):
        home = r.register_team("Basketball", "Los Angeles Lakers",
                                slug="lal")
        away = r.register_team("Basketball", "Oklahoma City Thunder",
                                slug="okc")
        comp = r.register_competition(
            "Basketball", "NBA Playoffs Round 2",
            slug="nba-playoffs-r2",
        )
        return home, away, comp

    def test_register_requires_both_teams(self):
        r = IdentityRegistry()
        # Neither registered
        with pytest.raises(ValueError):
            r.register_fixture(
                "Basketball", date(2026, 5, 5),
                home_team_id="team:basketball:lal",
                away_team_id="team:basketball:okc",
                start_time_utc=1746475800,
            )

    def test_register_basic(self):
        r = IdentityRegistry()
        home, away, comp = self._setup(r)
        f = r.register_fixture(
            "Basketball", date(2026, 5, 5),
            home_team_id=home.id, away_team_id=away.id,
            start_time_utc=1746475800,
            competition_id=comp.id,
        )
        assert isinstance(f, Fixture)
        assert f.id == ("fixture:basketball:2026-05-05:"
                        "lal-vs-okc")
        assert f.version == 1
        assert f.start_time_utc == 1746475800
        assert f.competition_id == comp.id

    def test_register_idempotent(self):
        r = IdentityRegistry()
        home, away, _ = self._setup(r)
        a = r.register_fixture(
            "Basketball", date(2026, 5, 5),
            home_team_id=home.id, away_team_id=away.id,
            start_time_utc=1746475800,
        )
        b = r.register_fixture(
            "Basketball", date(2026, 5, 5),
            home_team_id=home.id, away_team_id=away.id,
            start_time_utc=1746475800,
        )
        assert a == b
        assert b.version == 1
        assert r.stats()["fixtures"] == 1

    def test_register_bumps_version_on_time_change(self):
        r = IdentityRegistry()
        home, away, _ = self._setup(r)
        a = r.register_fixture(
            "Basketball", date(2026, 5, 5),
            home_team_id=home.id, away_team_id=away.id,
            start_time_utc=1746475800,
        )
        b = r.register_fixture(
            "Basketball", date(2026, 5, 5),
            home_team_id=home.id, away_team_id=away.id,
            start_time_utc=1746479400,  # rescheduled +1h
        )
        assert b.id == a.id
        assert b.version == 2
        assert b.start_time_utc == 1746479400
        assert b.updated_at_utc >= a.updated_at_utc

    def test_register_bumps_version_on_competition_change(self):
        r = IdentityRegistry()
        home, away, comp = self._setup(r)
        a = r.register_fixture(
            "Basketball", date(2026, 5, 5),
            home_team_id=home.id, away_team_id=away.id,
            start_time_utc=1746475800,
        )
        b = r.register_fixture(
            "Basketball", date(2026, 5, 5),
            home_team_id=home.id, away_team_id=away.id,
            start_time_utc=1746475800,
            competition_id=comp.id,
        )
        assert b.version == 2
        assert b.competition_id == comp.id


# ── MarketType registry ──────────────────────────────────────────

class TestMarketTypeRegistry:

    def test_register_unparameterized(self):
        r = IdentityRegistry()
        mt = r.register_market_type("Basketball", "Winner")
        assert mt.parameterized is False
        assert mt.slug == "winner"
        assert mt.id == "market_type:basketball:winner"

    def test_register_parameterized(self):
        r = IdentityRegistry()
        mt = r.register_market_type(
            "Soccer", "Over/Under Goals",
            slug="over-under-goals", parameterized=True,
        )
        assert mt.parameterized is True

    def test_parameterized_flip_rejected(self):
        r = IdentityRegistry()
        r.register_market_type("Basketball", "Winner",
                                 parameterized=False)
        with pytest.raises(ValueError):
            r.register_market_type("Basketball", "Winner",
                                     parameterized=True)


# ── Market registry ──────────────────────────────────────────────

class TestMarketRegistry:

    def _setup(self):
        r = IdentityRegistry()
        home = r.register_team("Basketball", "Lakers", slug="lal")
        away = r.register_team("Basketball", "Thunder", slug="okc")
        f = r.register_fixture(
            "Basketball", date(2026, 5, 5),
            home_team_id=home.id, away_team_id=away.id,
            start_time_utc=1746475800,
        )
        winner = r.register_market_type("Basketball", "Winner")
        ou = r.register_market_type(
            "Basketball", "Over/Under Points",
            slug="over-under-points", parameterized=True,
        )
        return r, f, winner, ou

    def test_register_unparameterized_market(self):
        r, f, winner, _ = self._setup()
        m = r.register_market(f.id, winner.id)
        assert m.fixture_id == f.id
        assert m.market_type_id == winner.id
        assert m.params == ()

    def test_register_parameterized_market_requires_params(self):
        r, f, _, ou = self._setup()
        with pytest.raises(ValueError):
            r.register_market(f.id, ou.id)

    def test_register_unparameterized_rejects_params(self):
        r, f, winner, _ = self._setup()
        with pytest.raises(ValueError):
            r.register_market(f.id, winner.id,
                                params=(("threshold", 2.5),))

    def test_idempotent(self):
        r, f, winner, _ = self._setup()
        a = r.register_market(f.id, winner.id)
        b = r.register_market(f.id, winner.id)
        assert a == b
        assert r.stats()["markets"] == 1

    def test_parameterized_different_params_distinct(self):
        r, f, _, ou = self._setup()
        m25 = r.register_market(f.id, ou.id,
                                  params=(("threshold", 2.5),))
        m35 = r.register_market(f.id, ou.id,
                                  params=(("threshold", 3.5),))
        assert m25.id != m35.id
        assert r.stats()["markets"] == 2


# ── Outcome registry ─────────────────────────────────────────────

class TestOutcomeRegistry:

    def _setup(self):
        r = IdentityRegistry()
        home = r.register_team("Basketball", "Lakers", slug="lal")
        away = r.register_team("Basketball", "Thunder", slug="okc")
        f = r.register_fixture(
            "Basketball", date(2026, 5, 5),
            home_team_id=home.id, away_team_id=away.id,
            start_time_utc=1746475800,
        )
        winner = r.register_market_type("Basketball", "Winner")
        m = r.register_market(f.id, winner.id)
        return r, m

    def test_register_and_resolve(self):
        r, m = self._setup()
        o = r.register_outcome(m.id, "home", "Los Angeles Lakers")
        assert o.market_id == m.id
        assert o.side == "home"
        assert o.canonical_label == "Los Angeles Lakers"
        assert r.resolve_outcome(o.id) == o

    def test_idempotent(self):
        r, m = self._setup()
        a = r.register_outcome(m.id, "home", "Lakers")
        b = r.register_outcome(m.id, "home", "Lakers")
        assert a == b
        assert r.stats()["outcomes"] == 1

    def test_unknown_market_rejected(self):
        r, _ = self._setup()
        with pytest.raises(ValueError):
            r.register_outcome("market:nope", "home", "X")


# ── Alias index ──────────────────────────────────────────────────

class TestAliasIndex:

    def test_register_basic(self):
        r = IdentityRegistry()
        a = r.register_alias(
            source="kalshi",
            external_id="KXNBAGAME-26MAY05LALOKC",
            canonical_id="market:basketball:2026-05-05:lal-vs-okc:winner",
            method="strict", confidence=1.0,
        )
        assert isinstance(a, Alias)
        assert r.stats()["aliases"] == 1

    def test_resolve_alias(self):
        r = IdentityRegistry()
        r.register_alias(
            "kalshi", "KXNBAGAME-26MAY05LALOKC",
            "market:basketball:2026-05-05:lal-vs-okc:winner",
            "strict",
        )
        a = r.resolve_alias("kalshi", "KXNBAGAME-26MAY05LALOKC")
        assert a is not None
        assert a.method == "strict"

    def test_method_precedence_higher_wins(self):
        """Manual operator override must replace an auto guess."""
        r = IdentityRegistry()
        r.register_alias(
            "kalshi", "K-1",
            "team:basketball:lal", "guarded_fuzzy",
            confidence=0.7,
        )
        r.register_alias(
            "kalshi", "K-1",
            "team:basketball:okc", "manual",
            confidence=1.0,
        )
        final = r.resolve_alias("kalshi", "K-1")
        assert final.method == "manual"
        assert final.canonical_id == "team:basketball:okc"

    def test_method_precedence_lower_does_not_overwrite(self):
        """A weak fuzzy hit must not silently overwrite a strict
        match. The original wins."""
        r = IdentityRegistry()
        r.register_alias(
            "kalshi", "K-1",
            "team:basketball:lal", "strict",
            confidence=1.0,
        )
        r.register_alias(
            "kalshi", "K-1",
            "team:basketball:okc", "guarded_fuzzy",
            confidence=0.7,
        )
        final = r.resolve_alias("kalshi", "K-1")
        assert final.method == "strict"
        assert final.canonical_id == "team:basketball:lal"

    def test_confidence_must_be_in_range(self):
        r = IdentityRegistry()
        with pytest.raises(ValueError):
            r.register_alias(
                "kalshi", "K-1", "team:basketball:lal",
                "strict", confidence=1.5,
            )

    def test_required_fields(self):
        r = IdentityRegistry()
        with pytest.raises(ValueError):
            r.register_alias(
                "", "K-1", "team:basketball:lal", "strict",
            )


# ── Resolve through alias ────────────────────────────────────────

class TestResolveThroughAlias:
    """End-to-end: external (source, external_id) → canonical entity.

    This is the path request-time pairing will take post-Phase C.
    O(1) dict lookup, no fuzzy logic at request time.
    """

    def test_resolves_team_alias_to_team(self):
        r = IdentityRegistry()
        team = r.register_team("Basketball", "Lakers", slug="lal")
        r.register_alias("kalshi", "LAL", team.id, "strict")
        assert r.resolve_through_alias("kalshi", "LAL") == team

    def test_resolves_fixture_alias_to_fixture(self):
        r = IdentityRegistry()
        home = r.register_team("Basketball", "Lakers", slug="lal")
        away = r.register_team("Basketball", "Thunder", slug="okc")
        f = r.register_fixture(
            "Basketball", date(2026, 5, 5),
            home_team_id=home.id, away_team_id=away.id,
            start_time_utc=1746475800,
        )
        r.register_alias(
            "kalshi", "KXNBAGAME-26MAY05LALOKC",
            f.id, "strict",
        )
        result = r.resolve_through_alias(
            "kalshi", "KXNBAGAME-26MAY05LALOKC",
        )
        assert result == f

    def test_resolves_unknown_to_none(self):
        r = IdentityRegistry()
        assert r.resolve_through_alias("kalshi", "nope") is None

    def test_unknown_canonical_kind_returns_none(self):
        """Defensive: alias points to a canonical_id with unknown
        prefix → None, not a crash."""
        r = IdentityRegistry()
        r.register_alias("kalshi", "K-1", "weird:thing", "strict")
        assert r.resolve_through_alias("kalshi", "K-1") is None


# ── Global registry ──────────────────────────────────────────────

class TestGlobalRegistry:

    def test_singleton(self):
        reset_global_registry()
        a = global_registry()
        b = global_registry()
        assert a is b

    def test_reset_clears(self):
        reset_global_registry()
        r = global_registry()
        r.register_team("Basketball", "Lakers", slug="lal")
        assert r.stats()["teams"] == 1
        reset_global_registry()
        r2 = global_registry()
        assert r2 is not r
        assert r2.stats()["teams"] == 0
