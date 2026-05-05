"""Phase B tests — FL → IdentityRegistry seeder.

Exercises the seeder with synthetic FL fixture data (same shape as
the existing test_sports_feed_v2.py mocks). No live network. No
production code paths touched.
"""
from __future__ import annotations
from datetime import date, datetime, timezone

import pytest

from identity_registry import IdentityRegistry
from fl_registry_seed import (
    seed_team_from_fl_event,
    seed_competition_from_fl,
    seed_fixture_from_fl_event,
    seed_from_fl_response,
)


def _ts(y, mo, d, h=0, mi=0):
    return int(datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp())


# ── Fixtures ─────────────────────────────────────────────────────

@pytest.fixture
def fl_response_ucl():
    """Minimal FL response with 1 UCL Play-Offs tournament + 2 fixtures."""
    return {
        "DATA": [
            {
                "TOURNAMENT_STAGE_ID": "tour_ucl_playoffs",
                "NAME":         "Champions League - Play Offs",
                "NAME_PART_1":  "Europe",
                "NAME_PART_2":  "Champions League - Play Offs",
                "COUNTRY_NAME": "Europe",
                "EVENTS": [
                    {
                        "EVENT_ID":       "fl_arsatm",
                        "HOME_NAME":      "Arsenal",
                        "AWAY_NAME":      "Atl. Madrid",
                        "SHORTNAME_HOME": "ARS",
                        "SHORTNAME_AWAY": "ATM",
                        "START_TIME":     _ts(2026, 5, 5, 19, 0),
                        "STAGE_TYPE":     "SCHEDULED",
                    },
                    {
                        "EVENT_ID":       "fl_bmupsg",
                        "HOME_NAME":      "Bayern Munich",
                        "AWAY_NAME":      "PSG",
                        "SHORTNAME_HOME": "BAY",
                        "SHORTNAME_AWAY": "PSG",
                        "START_TIME":     _ts(2026, 5, 13, 19, 0),
                        "STAGE_TYPE":     "SCHEDULED",
                    },
                ],
            },
        ],
    }


@pytest.fixture
def fl_response_nba():
    """NBA Play-Offs response with the team-name divergence case
    (long names like 'Los Angeles Lakers' + abbreviated SHORTNAMEs).
    """
    return {
        "DATA": [
            {
                "TOURNAMENT_STAGE_ID": "tour_nba_playoffs_r2",
                "NAME":         "NBA - Play Offs",
                "NAME_PART_1":  "USA",
                "NAME_PART_2":  "NBA - Play Offs",
                "COUNTRY_NAME": "USA",
                "EVENTS": [
                    {
                        "EVENT_ID":       "fl_okclal",
                        "HOME_NAME":      "Oklahoma City Thunder",
                        "AWAY_NAME":      "Los Angeles Lakers",
                        "SHORTNAME_HOME": "OKC",
                        "SHORTNAME_AWAY": "LAL",
                        "START_TIME":     _ts(2026, 5, 5, 23, 30),
                        "STAGE_TYPE":     "SCHEDULED",
                    },
                ],
            },
        ],
    }


# ── seed_team_from_fl_event ──────────────────────────────────────

class TestSeedTeam:

    def test_home_team(self, fl_response_ucl):
        r = IdentityRegistry()
        ev = fl_response_ucl["DATA"][0]["EVENTS"][0]
        team = seed_team_from_fl_event(r, ev, "Soccer", "home")
        assert team is not None
        assert team.canonical_name == "Arsenal"
        assert team.slug == "arsenal"
        assert team.id == "team:soccer:arsenal"
        assert "ARS" in team.aliases

    def test_away_team(self, fl_response_ucl):
        r = IdentityRegistry()
        ev = fl_response_ucl["DATA"][0]["EVENTS"][0]
        team = seed_team_from_fl_event(r, ev, "Soccer", "away")
        assert team is not None
        assert team.canonical_name == "Atl. Madrid"
        assert team.slug == "atl-madrid"
        assert team.id == "team:soccer:atl-madrid"
        assert "ATM" in team.aliases

    def test_invalid_side_raises(self, fl_response_ucl):
        r = IdentityRegistry()
        ev = fl_response_ucl["DATA"][0]["EVENTS"][0]
        with pytest.raises(ValueError):
            seed_team_from_fl_event(r, ev, "Soccer", "left")

    def test_missing_data_returns_none(self):
        r = IdentityRegistry()
        ev = {}  # no name/shortname at all
        assert seed_team_from_fl_event(r, ev, "Soccer", "home") is None

    def test_only_shortname_works(self):
        r = IdentityRegistry()
        ev = {"SHORTNAME_HOME": "ARS"}
        team = seed_team_from_fl_event(r, ev, "Soccer", "home")
        assert team is not None
        assert team.canonical_name == "ARS"
        assert "ARS" in team.aliases

    def test_idempotent(self, fl_response_ucl):
        """Repeated calls don't create duplicates."""
        r = IdentityRegistry()
        ev = fl_response_ucl["DATA"][0]["EVENTS"][0]
        a = seed_team_from_fl_event(r, ev, "Soccer", "home")
        b = seed_team_from_fl_event(r, ev, "Soccer", "home")
        assert a.id == b.id
        assert r.stats()["teams"] == 1

    def test_long_name_takes_precedence_over_shortname(self):
        """When both are present, slug derives from long name. The
        short form is added as an alias. This protects canonical
        IDs from FL changing its abbreviation convention."""
        r = IdentityRegistry()
        ev = {
            "HOME_NAME":      "Los Angeles Lakers",
            "SHORTNAME_HOME": "LAK",
        }
        team = seed_team_from_fl_event(r, ev, "Basketball", "home")
        assert team.slug == "los-angeles-lakers"
        assert team.id == "team:basketball:los-angeles-lakers"
        assert "LAK" in team.aliases


# ── seed_competition_from_fl ─────────────────────────────────────

class TestSeedCompetition:

    def test_basic(self, fl_response_ucl):
        r = IdentityRegistry()
        t = fl_response_ucl["DATA"][0]
        comp = seed_competition_from_fl(r, t, "Soccer")
        assert comp is not None
        assert comp.canonical_name == "Champions League - Play Offs"
        assert comp.slug == "champions-league-play-offs"
        assert "tour_ucl_playoffs" in comp.aliases

    def test_stage_id_registered_as_alias(self, fl_response_ucl):
        """seed_competition_from_fl writes the FL stage_id into the
        alias index so resolve_through_alias('fl', stage_id) returns
        the Competition."""
        r = IdentityRegistry()
        t = fl_response_ucl["DATA"][0]
        comp = seed_competition_from_fl(r, t, "Soccer")
        resolved = r.resolve_through_alias("fl", "tour_ucl_playoffs")
        assert resolved is not None
        assert resolved.id == comp.id

    def test_idempotent(self, fl_response_ucl):
        r = IdentityRegistry()
        t = fl_response_ucl["DATA"][0]
        a = seed_competition_from_fl(r, t, "Soccer")
        b = seed_competition_from_fl(r, t, "Soccer")
        assert a.id == b.id
        assert r.stats()["competitions"] == 1

    def test_falls_back_to_name_part_2(self):
        """If NAME is missing, NAME_PART_2 is used."""
        r = IdentityRegistry()
        t = {
            "TOURNAMENT_STAGE_ID": "tid",
            "NAME": "",
            "NAME_PART_2": "Premier League",
        }
        comp = seed_competition_from_fl(r, t, "Soccer")
        assert comp is not None
        assert comp.canonical_name == "Premier League"
        assert comp.slug == "premier-league"

    def test_returns_none_for_empty_tournament(self):
        r = IdentityRegistry()
        assert seed_competition_from_fl(r, {}, "Soccer") is None


# ── seed_fixture_from_fl_event ───────────────────────────────────

class TestSeedFixture:

    def test_basic(self, fl_response_ucl):
        r = IdentityRegistry()
        ev = fl_response_ucl["DATA"][0]["EVENTS"][0]
        fx = seed_fixture_from_fl_event(r, ev, "Soccer")
        assert fx is not None
        assert fx.id == "fixture:soccer:2026-05-05:1900:arsenal-vs-atl-madrid"
        assert fx.start_time_utc == _ts(2026, 5, 5, 19, 0)
        assert fx.version == 1

    def test_event_id_registered_as_alias(self, fl_response_ucl):
        r = IdentityRegistry()
        ev = fl_response_ucl["DATA"][0]["EVENTS"][0]
        fx = seed_fixture_from_fl_event(r, ev, "Soccer")
        resolved = r.resolve_through_alias("fl", "fl_arsatm")
        assert resolved is not None
        assert resolved.id == fx.id

    def test_links_competition(self, fl_response_ucl):
        r = IdentityRegistry()
        t = fl_response_ucl["DATA"][0]
        ev = t["EVENTS"][0]
        comp = seed_competition_from_fl(r, t, "Soccer")
        fx = seed_fixture_from_fl_event(
            r, ev, "Soccer", competition_id=comp.id,
        )
        assert fx.competition_id == comp.id

    def test_missing_start_time_returns_none(self):
        r = IdentityRegistry()
        ev = {
            "EVENT_ID":       "fl_x",
            "HOME_NAME":      "Arsenal",
            "AWAY_NAME":      "Chelsea",
            "SHORTNAME_HOME": "ARS",
            "SHORTNAME_AWAY": "CHE",
            # No START_TIME
        }
        assert seed_fixture_from_fl_event(r, ev, "Soccer") is None

    def test_missing_team_data_returns_none(self):
        r = IdentityRegistry()
        ev = {
            "EVENT_ID":       "fl_x",
            "START_TIME":     _ts(2026, 5, 5),
            # No team data at all
        }
        assert seed_fixture_from_fl_event(r, ev, "Soccer") is None

    def test_idempotent(self, fl_response_ucl):
        r = IdentityRegistry()
        ev = fl_response_ucl["DATA"][0]["EVENTS"][0]
        a = seed_fixture_from_fl_event(r, ev, "Soccer")
        b = seed_fixture_from_fl_event(r, ev, "Soccer")
        assert a == b
        assert r.stats()["fixtures"] == 1

    def test_reschedule_creates_distinct_fixture(self):
        """Phase C2e — START_TIME is in the canonical ID (HHMM
        component), so a reschedule produces a NEW Fixture rather
        than bumping the existing one's version. Previous bump-
        version behavior would have collided MLB doubleheaders.
        """
        r = IdentityRegistry()
        ev = {
            "EVENT_ID":       "fl_x",
            "HOME_NAME":      "Arsenal",
            "AWAY_NAME":      "Chelsea",
            "SHORTNAME_HOME": "ARS",
            "SHORTNAME_AWAY": "CHE",
            "START_TIME":     _ts(2026, 5, 5, 19, 0),
        }
        a = seed_fixture_from_fl_event(r, ev, "Soccer")
        ev2 = dict(ev)
        ev2["START_TIME"] = _ts(2026, 5, 5, 20, 0)  # postponed +1h
        b = seed_fixture_from_fl_event(r, ev2, "Soccer")
        assert a is not None and b is not None
        assert a.id != b.id  # distinct canonical fixtures
        assert b.start_time_utc == _ts(2026, 5, 5, 20, 0)


# ── seed_from_fl_response (top-level) ────────────────────────────

class TestSeedFromFLResponse:

    def test_full_walk(self, fl_response_ucl):
        r = IdentityRegistry()
        stats = seed_from_fl_response(r, fl_response_ucl, "Soccer")
        assert stats["tournaments_seeded"] == 1
        assert stats["fixtures_seeded"]    == 2
        assert stats["fixtures_skipped"]   == 0
        # Both fixtures created — both teams + both teams = 4 teams
        assert stats["teams_seeded"]       == 4

        # Check the registry state matches the stats
        regs = r.stats()
        assert regs["competitions"] == 1
        assert regs["fixtures"]     == 2
        assert regs["teams"]        == 4
        # 1 competition + 2 fixtures = 3 'fl' aliases registered
        assert regs["aliases"]      == 3

    def test_idempotent_full_walk(self, fl_response_ucl):
        r = IdentityRegistry()
        seed_from_fl_response(r, fl_response_ucl, "Soccer")
        before = r.stats()
        # Run twice — registry unchanged
        seed_from_fl_response(r, fl_response_ucl, "Soccer")
        after = r.stats()
        assert before == after

    def test_competition_links_propagate_to_fixtures(self, fl_response_ucl):
        r = IdentityRegistry()
        seed_from_fl_response(r, fl_response_ucl, "Soccer")
        # Look up the comp by FL stage_id alias
        comp = r.resolve_through_alias("fl", "tour_ucl_playoffs")
        assert comp is not None
        # And the fixtures should reference it
        fx = r.resolve_through_alias("fl", "fl_arsatm")
        assert fx is not None
        assert fx.competition_id == comp.id

    def test_handles_nba_long_names(self, fl_response_nba):
        """NBA fixture with full long names + 3-letter SHORTNAMEs.
        Slug derives from long name; SHORTNAME stored as alias."""
        r = IdentityRegistry()
        seed_from_fl_response(r, fl_response_nba, "Basketball")
        thunder = r.lookup_team("Basketball", "oklahoma-city-thunder")
        assert thunder is not None
        assert "OKC" in thunder.aliases
        lakers = r.lookup_team("Basketball", "los-angeles-lakers")
        assert lakers is not None
        assert "LAL" in lakers.aliases
        fx = r.resolve_through_alias("fl", "fl_okclal")
        assert fx is not None
        assert (fx.id ==
                "fixture:basketball:2026-05-05:2330:"
                "oklahoma-city-thunder-vs-los-angeles-lakers")

    def test_skips_malformed_events(self):
        """Events missing required data don't crash the walk —
        they're counted as skipped."""
        r = IdentityRegistry()
        resp = {
            "DATA": [
                {
                    "TOURNAMENT_STAGE_ID": "tid",
                    "NAME": "Test League",
                    "EVENTS": [
                        {
                            # Valid
                            "EVENT_ID":       "fl_a",
                            "HOME_NAME":      "Arsenal",
                            "AWAY_NAME":      "Chelsea",
                            "SHORTNAME_HOME": "ARS",
                            "SHORTNAME_AWAY": "CHE",
                            "START_TIME":     _ts(2026, 5, 5),
                        },
                        # Missing START_TIME — skipped
                        {
                            "EVENT_ID":       "fl_b",
                            "HOME_NAME":      "Liverpool",
                            "AWAY_NAME":      "Man City",
                            "SHORTNAME_HOME": "LIV",
                            "SHORTNAME_AWAY": "MCI",
                        },
                        # Empty — skipped
                        {},
                    ],
                },
            ],
        }
        stats = seed_from_fl_response(r, resp, "Soccer")
        assert stats["fixtures_seeded"]  == 1
        assert stats["fixtures_skipped"] == 2

    def test_empty_response(self):
        r = IdentityRegistry()
        stats = seed_from_fl_response(r, {}, "Soccer")
        assert stats == {
            "tournaments_seeded": 0,
            "fixtures_seeded":    0,
            "teams_seeded":       0,
            "fixtures_skipped":   0,
        }

    def test_malformed_data_field(self):
        """DATA isn't a list → safely return zeros."""
        r = IdentityRegistry()
        stats = seed_from_fl_response(r, {"DATA": "not a list"}, "Soccer")
        assert stats["tournaments_seeded"] == 0
        assert stats["fixtures_seeded"]    == 0

    def test_local_date_used_for_conmebol_evening_game(self):
        """Phase C2d — Soccer CONMEBOL evening game (21:00 ART) with
        FL UTC start crossing midnight should produce a Fixture
        whose canonical date is the LOCAL Argentine date, not the
        UTC date. This is what makes Kalshi's MAY05 ticker (local-
        Argentine convention) match the FL fixture (UTC May 6).
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
                            # FL ships UTC: May 6 00:00 = May 5
                            # 21:00 Buenos Aires (UTC-3)
                            "START_TIME":     _ts(2026, 5, 6, 0, 0),
                        },
                    ],
                },
            ],
        }
        seed_from_fl_response(r, fl, "Soccer")
        fx = r.resolve_through_alias("fl", "fl_ucvind")
        assert fx is not None
        # Canonical ID uses Argentine local date (May 5), NOT UTC
        # May 6. Time component (HHMM) stays UTC: 00:00.
        assert fx.id == (
            "fixture:soccer:2026-05-05:0000:"
            "universidad-catolica-vs-independiente-del-valle"
        )
        assert fx.local_date == date(2026, 5, 5)

    def test_local_date_falls_back_to_utc_when_no_tournament_context(self):
        """seed_fixture_from_fl_event called without fl_tournament
        falls back to UTC date for backward compatibility."""
        from fl_registry_seed import seed_fixture_from_fl_event
        r = IdentityRegistry()
        ev = {
            "EVENT_ID":       "fl_x",
            "HOME_NAME":      "Foo",
            "AWAY_NAME":      "Bar",
            "SHORTNAME_HOME": "FOO",
            "SHORTNAME_AWAY": "BAR",
            # May 6 00:00 UTC
            "START_TIME":     _ts(2026, 5, 6, 0, 0),
        }
        # No fl_tournament passed → tz defaults to UTC → local_date
        # equals UTC date (May 6)
        fx = seed_fixture_from_fl_event(r, ev, "Soccer")
        assert fx is not None
        assert fx.local_date == date(2026, 5, 6)

    def test_local_date_unchanged_for_kbo_afternoon_game(self):
        """KBO afternoon games (14:00 KST = 05:00 UTC) don't cross
        midnight — local_date and UTC date agree. Sanity check the
        timezone work doesn't move dates that shouldn't move."""
        r = IdentityRegistry()
        fl = {
            "DATA": [
                {
                    "TOURNAMENT_STAGE_ID": "tour_kbo",
                    "NAME": "KBO Regular Season",
                    "EVENTS": [
                        {
                            "EVENT_ID":       "fl_samkiw",
                            "HOME_NAME":      "Samsung Lions",
                            "AWAY_NAME":      "Kiwoom Heroes",
                            "SHORTNAME_HOME": "SAM",
                            "SHORTNAME_AWAY": "KIW",
                            # May 5 05:00 UTC = May 5 14:00 KST
                            "START_TIME":     _ts(2026, 5, 5, 5, 0),
                        },
                    ],
                },
            ],
        }
        seed_from_fl_response(r, fl, "Baseball")
        fx = r.resolve_through_alias("fl", "fl_samkiw")
        assert fx is not None
        # KST local date and UTC date both = May 5
        assert fx.local_date == date(2026, 5, 5)
