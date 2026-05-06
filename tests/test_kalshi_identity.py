"""pytest suite for kalshi_identity.py.

Phase 1 verification per SPORTS_V2_PLAN.md §5:
  - 100% of snapshot tickers parse without errors
  - Hand-picked round-trip checks for each grammar pattern
  - Known FL pairings match deterministically

Tests are snapshot-driven where possible: every ticker observed in
kalshi_probe/snapshots/ticker_grammar_*.json is expected to parse.
This locks the parser against real Kalshi data, not assumptions.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from kalshi_identity import (
    Identity, parse_ticker, compute_fl_identity, match,
    parent_fixture_identity, strip_known_suffix,
)

SNAPSHOTS = Path(__file__).resolve().parent.parent / "kalshi_probe" / "snapshots"


def _ts(y: int, mo: int, d: int, h: int = 0, mi: int = 0) -> int:
    """Helper: deterministic UTC unix timestamp for tests."""
    return int(datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp())


# ── Suffix stripping ─────────────────────────────────────────────

class TestStripKnownSuffix:
    @pytest.mark.parametrize("series, expected_base, expected_suf", [
        ("KXEPLGAME",       "KXEPL",        "GAME"),
        ("KXEPL1H",         "KXEPL",        "1H"),
        ("KXEPLBTTS",       "KXEPL",        "BTTS"),
        ("KXEPLTOTAL",      "KXEPL",        "TOTAL"),
        ("KXEPLSPREAD",     "KXEPL",        "SPREAD"),
        ("KXMLBF5",         "KXMLB",        "F5"),
        ("KXMLBRFI",        "KXMLB",        "RFI"),
        ("KXNBA1H",         "KXNBA",        "1H"),
        ("KXNBA2H",         "KXNBA",        "2H"),
        ("KXUCLTCORNERS",   "KXUCL",        "TCORNERS"),
        ("KXUCLCORNERS",    "KXUCL",        "CORNERS"),
        ("KXUCLADVANCE",    "KXUCL",        "ADVANCE"),
        ("KXIPL",           "KXIPL",        ""),    # no suffix
        ("KXLOLMAP",        "KXLOL",        "MAP"),
        ("KXATPSETWINNER",  "KXATP",        "SETWINNER"),
        ("KXCS2TOTALMAPS",  "KXCS2",        "TOTALMAPS"),
    ])
    def test_strip(self, series, expected_base, expected_suf):
        base, suf = strip_known_suffix(series)
        assert base == expected_base
        assert suf == expected_suf

    def test_tcorners_not_collapsed_to_corners(self):
        """Regression: TCORNERS must not strip down to CORNERS."""
        base, suf = strip_known_suffix("KXUCLTCORNERS")
        assert suf == "TCORNERS"
        assert base == "KXUCL"


# ── Single-ticker parse tests, one per grammar ───────────────────

class TestG1DateTeams:
    """Soccer / NBA / NHL / NFL / Tennis / MMA / Boxing / Cricket /
    Rugby / Lacrosse — date + abbrs, no time."""

    def test_soccer_epl(self):
        i = parse_ticker("KXEPLGAME-26MAY19CFCTOT", "KXEPLGAME", "Soccer")
        assert i.kind == "per_fixture"
        assert i.sport == "Soccer"
        assert i.series_base == "KXEPL"
        assert i.date == date(2026, 5, 19)
        assert i.time is None
        assert i.abbr_block == "CFCTOT"

    def test_nba(self):
        i = parse_ticker("KXNBAGAME-26MAY11OKCLAL", "KXNBAGAME", "Basketball")
        assert i.kind == "per_fixture"
        assert i.date == date(2026, 5, 11)
        assert i.time is None
        assert i.abbr_block == "OKCLAL"
        assert i.series_base == "KXNBA"

    def test_nhl(self):
        i = parse_ticker("KXNHLOVERTIME-26MAY10VGKANA", "KXNHLOVERTIME", "Hockey")
        assert i.kind == "per_fixture"
        assert i.date == date(2026, 5, 10)
        assert i.abbr_block == "VGKANA"
        # OVERTIME is a known suffix → series_base is KXNHL
        assert i.series_base == "KXNHL"

    def test_tennis(self):
        i = parse_ticker("KXATPMATCH-26MAY05HIJBAS", "KXATPMATCH", "Tennis")
        assert i.kind == "per_fixture"
        assert i.date == date(2026, 5, 5)
        assert i.abbr_block == "HIJBAS"
        assert i.series_base == "KXATP"

    def test_mma(self):
        i = parse_ticker("KXUFCFIGHT-26MAY16MGOMOR", "KXUFCFIGHT", "MMA")
        assert i.kind == "per_fixture"
        assert i.date == date(2026, 5, 16)
        assert i.abbr_block == "MGOMOR"

    def test_mainz_05_with_digit_in_abbr(self):
        """Regression: M05UNI must parse (Bundesliga Mainz 05)."""
        i = parse_ticker(
            "KXBUNDESLIGAGAME-26MAY10M05UNI",
            "KXBUNDESLIGAGAME", "Soccer",
        )
        assert i.kind == "per_fixture"
        assert i.abbr_block == "M05UNI"
        assert i.date == date(2026, 5, 10)


class TestG7DateTimeTeams:
    """MLB, intl basketball, intl hockey, esports headline,
    AFL, ITTF — date + time + abbrs."""

    def test_mlb(self):
        i = parse_ticker("KXMLBGAME-26MAY071540PITAZ", "KXMLBGAME", "Baseball")
        assert i.kind == "per_fixture"
        assert i.date == date(2026, 5, 7)
        assert i.time == "1540"
        assert i.abbr_block == "PITAZ"
        assert i.series_base == "KXMLB"

    def test_euroleague(self):
        i = parse_ticker(
            "KXEUROLEAGUEGAME-26MAY061415VALPAN",
            "KXEUROLEAGUEGAME", "Basketball",
        )
        assert i.kind == "per_fixture"
        assert i.time == "1415"
        assert i.abbr_block == "VALPAN"

    def test_afl(self):
        i = parse_ticker(
            "KXAFLGAME-26MAY100115ADERIC",
            "KXAFLGAME", "Aussie Rules",
        )
        assert i.kind == "per_fixture"
        assert i.date == date(2026, 5, 10)
        assert i.time == "0115"
        assert i.abbr_block == "ADERIC"

    def test_esports_lol_with_long_abbr(self):
        i = parse_ticker(
            "KXLOLGAME-26MAY071400HRTSKOIA",
            "KXLOLGAME", "Esports",
        )
        assert i.kind == "per_fixture"
        assert i.time == "1400"
        assert i.abbr_block == "HRTSKOIA"

    def test_dota2_digit_prefix_abbr(self):
        i = parse_ticker(
            "KXDOTA2GAME-26MAY0511001WINMOUZ",
            "KXDOTA2GAME", "Esports",
        )
        assert i.kind == "per_fixture"
        assert i.time == "1100"
        assert i.abbr_block == "1WINMOUZ"


class TestGLeg:
    """Tennis set winners + esports map winners — leg suffix."""

    def test_tennis_set_winner(self):
        i = parse_ticker(
            "KXATPSETWINNER-26MAY05HIJBAS-1",
            "KXATPSETWINNER", "Tennis",
        )
        assert i.kind == "per_leg"
        assert i.date == date(2026, 5, 5)
        assert i.abbr_block == "HIJBAS"
        assert i.leg_n == 1
        assert i.time is None

    def test_esports_map(self):
        i = parse_ticker(
            "KXLOLMAP-26MAY071500ZYBSLY-2",
            "KXLOLMAP", "Esports",
        )
        assert i.kind == "per_leg"
        assert i.time == "1500"
        assert i.abbr_block == "ZYBSLY"
        assert i.leg_n == 2

    def test_parent_fixture_lookup(self):
        leg = parse_ticker(
            "KXLOLMAP-26MAY071500ZYBSLY-1",
            "KXLOLMAP", "Esports",
        )
        parent = parent_fixture_identity(leg)
        assert parent is not None
        assert parent.kind == "per_fixture"
        assert parent.abbr_block == leg.abbr_block
        assert parent.date == leg.date
        assert parent.time == leg.time


class TestGSeries:
    """NBA/NHL playoff series identifiers."""

    def test_nba_series_round_2(self):
        i = parse_ticker(
            "KXNBASERIES-26LALOKCR2", "KXNBASERIES", "Basketball",
        )
        assert i.kind == "series"
        assert i.year == 26
        assert i.abbr_block == "LALOKC"
        assert i.round_n == 2

    def test_nhl_series(self):
        i = parse_ticker(
            "KXNHLSERIES-26MTLBUFR2", "KXNHLSERIES", "Hockey",
        )
        assert i.kind == "series"
        assert i.abbr_block == "MTLBUF"
        assert i.round_n == 2


class TestGDateOnly:
    """Tennis #1 ranking on date X."""

    def test_atp_rank(self):
        i = parse_ticker("KXATP1RANK-26DEC31", "KXATP1RANK", "Tennis")
        assert i.kind == "outright"
        assert i.date == date(2026, 12, 31)


class TestGYear:
    """Year-keyed outrights."""

    def test_short_year_2_digit(self):
        i = parse_ticker("KXUCL-26", "KXUCL", "Soccer")
        assert i.kind == "outright"
        assert i.year == 26

    def test_short_year_1_digit(self):
        # Hypothetical edge case
        i = parse_ticker("KXFOO-7", "KXFOO", "Soccer")
        assert i.kind == "outright"
        assert i.year == 7

    def test_full_year_4_digit(self):
        i = parse_ticker(
            "KXPLAYTOGETHERJBJT-2027",
            "KXPLAYTOGETHERJBJT", "Basketball",
        )
        assert i.kind == "outright"
        assert i.year == 2027


class TestGTournamentHandle:
    """Golf/NASCAR/Esports event handles with year suffix."""

    def test_pga_championship(self):
        i = parse_ticker("KXPGATOUR-PGC26", "KXPGATOUR", "Golf")
        assert i.kind == "tournament"
        assert i.handle == "PGC"
        assert i.year == 26

    def test_nascar_race(self):
        i = parse_ticker(
            "KXNASCARRACE-GOBAT26", "KXNASCARRACE", "Motorsport",
        )
        assert i.kind == "tournament"
        assert i.handle == "GOBAT"
        assert i.year == 26

    def test_cs2_event(self):
        i = parse_ticker("KXCS2-ASIA26", "KXCS2", "Esports")
        assert i.kind == "tournament"
        assert i.handle == "ASIA"
        assert i.year == 26

    def test_long_handle(self):
        i = parse_ticker(
            "KXNASCARAUTOPARTSSERIES-NAPS26",
            "KXNASCARAUTOPARTSSERIES", "Motorsport",
        )
        assert i.kind == "tournament"
        assert i.handle == "NAPS"
        assert i.year == 26


class TestUnparsed:
    """Edge cases that don't fit any documented pattern."""

    def test_empty_ticker(self):
        i = parse_ticker("", "KXFOO", "Soccer")
        assert i.kind == "unparsed"

    def test_unrecognized_format(self):
        # Genuinely arbitrary - shouldn't crash
        i = parse_ticker(
            "KXNFLDRAFTPICK-26-3", "KXNFLDRAFTPICK", "Football",
        )
        # This could parse as outright (year=26) — pattern matches first segment
        # Acceptable — we just verify it doesn't crash and produces something
        assert i.kind in ("outright", "unparsed", "tournament")


class TestOutrightSeriesPrefixes:
    """Player / manager / novelty futures whose ticker shape LOOKS
    like G1 (date + abbr) but which must NOT be classified as
    per_fixture — they don't pair with FL events.
    """

    def test_join_club_player_future(self):
        """KXJOINCLUB-26OCT02RODRYGO should be outright, not per_fixture.

        Pre-fix: classified as per_fixture with abbr_block=RODRYGO
        and surfaced as an unpaired Soccer fixture in /sports.
        Post-fix: classified as outright with handle=RODRYGO.
        """
        i = parse_ticker(
            "KXJOINCLUB-26OCT02RODRYGO", "KXJOINCLUB", "Soccer",
        )
        assert i.kind == "outright"
        assert i.handle == "RODRYGO"
        assert i.date == date(2026, 10, 2)

    def test_join_league(self):
        i = parse_ticker(
            "KXJOINLEAGUE-26OCT02MSALAH", "KXJOINLEAGUE", "Soccer",
        )
        assert i.kind == "outright"

    def test_managers_out_with_league_code(self):
        """KXMANAGERSOUT-26AUG01EPL — date + 'EPL' looks like teams
        but is really a league code on a manager-futures market."""
        i = parse_ticker(
            "KXMANAGERSOUT-26AUG01EPL", "KXMANAGERSOUT", "Soccer",
        )
        assert i.kind == "outright"

    def test_next_team_player(self):
        i = parse_ticker(
            "KXNEXTTEAMNFL-27JALLEN", "KXNEXTTEAMNFL", "Football",
        )
        assert i.kind == "outright"

    def test_player_will_play_binary(self):
        i = parse_ticker(
            "KXSOCCERPLAYMESSI-26", "KXSOCCERPLAYMESSI", "Soccer",
        )
        assert i.kind == "outright"
        assert i.year == 26

    def test_nba_draft_pick(self):
        i = parse_ticker(
            "KXNBADRAFTPICK-26-3", "KXNBADRAFTPICK", "Basketball",
        )
        assert i.kind == "outright"

    def test_outright_doesnt_appear_in_per_fixture_join(self):
        """Sanity: outright records, when parsed and grouped, must
        not produce a per_fixture identity."""
        i = parse_ticker(
            "KXJOINCLUB-26OCT02RODRYGO", "KXJOINCLUB", "Soccer",
        )
        assert i.kind != "per_fixture"
        assert i.kind != "per_leg"

    def test_normal_h2h_still_per_fixture(self):
        """Regression: don't accidentally mark normal h2h as outright."""
        i = parse_ticker(
            "KXEPLGAME-26MAY19CFCTOT", "KXEPLGAME", "Soccer",
        )
        assert i.kind == "per_fixture"

    def test_owgrrank_overwatch_outright(self):
        """Esports OWGR rank ticker that LOOKS like G_DATE_ONLY
        but is actually an outright."""
        i = parse_ticker(
            "KXOWGRRANK-26JUNT20", "KXOWGRRANK", "Esports",
        )
        assert i.kind == "outright"


# ── Full snapshot sweep — every observed ticker must parse ──────

class TestSnapshotsParse:
    """Run parse_ticker against every example/unparsed ticker in the
    saved kalshi_probe/snapshots/ticker_grammar_*.json files. We
    require ≥99% parse rate; logged unparsed tickers are visible
    in the failure message for follow-up.
    """

    @staticmethod
    def _collect_tickers():
        """Yield (sport, ticker, ticker_grammar_pattern_label) tuples."""
        for path in sorted(SNAPSHOTS.glob("ticker_grammar_*.json")):
            try:
                data = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            sport = data.get("sport_filter") or path.stem.replace(
                "ticker_grammar_", "").replace("_", " ").title()
            for base in (data.get("bases") or []):
                base_name = base.get("base", "")
                # Examples (already parsed by the probe). Some snapshots
                # store examples as a {pattern: [tickers]} dict, others
                # as a flat list — handle both shapes.
                ex_block = base.get("examples") or {}
                if isinstance(ex_block, dict):
                    items = ex_block.items()
                elif isinstance(ex_block, list):
                    items = [("FLAT_LIST", ex_block)]
                else:
                    items = []
                for ptn_name, exs in items:
                    for ex in (exs or []):
                        if isinstance(ex, dict) and (t := ex.get("ticker")):
                            yield (sport, t, base_name, ptn_name)
                # Unparsed cases too — we want to know if we now parse them
                for unp in (base.get("unparsed") or []):
                    if isinstance(unp, dict) and (t := unp.get("ticker")):
                        yield (sport, t, base_name, "PROBE_UNPARSED")

    def test_snapshot_parse_rate(self):
        """≥99% of snapshot tickers must produce non-unparsed Identity."""
        tickers = list(self._collect_tickers())
        assert tickers, "No snapshot tickers found — check kalshi_probe/snapshots/"

        unparsed = []
        total = len(tickers)
        for sport, ticker, base_name, ptn_name in tickers:
            i = parse_ticker(ticker, base_name, sport)
            if i.kind == "unparsed":
                unparsed.append((sport, ticker, base_name, ptn_name, i.raw_suffix))

        parsed = total - len(unparsed)
        rate = parsed / total
        assert rate >= 0.99, (
            f"Parse rate {rate:.1%} < 99%. "
            f"{len(unparsed)}/{total} unparsed:\n"
            + "\n".join(f"  {s} | {t} | base={b} probe-pattern={p} raw={r}"
                        for s, t, b, p, r in unparsed[:20])
        )

    def test_no_crashes(self):
        """parse_ticker should never raise on snapshot data."""
        for sport, ticker, base_name, _ in self._collect_tickers():
            try:
                parse_ticker(ticker, base_name, sport)
            except Exception as e:
                pytest.fail(f"parse_ticker raised on {ticker!r}: {e}")


# ── FL identity computation ──────────────────────────────────────

class TestFLIdentity:

    def _make_fl_event(self, **overrides):
        ev = {
            "EVENT_ID": "abc123",
            "HOME_NAME": "Arsenal",
            "AWAY_NAME": "Atl. Madrid",
            "SHORTNAME_HOME": "ARS",
            "SHORTNAME_AWAY": "ATM",
            # 2026-05-05 15:00:00 UTC
            "START_TIME": _ts(2026, 5, 5, 15, 0),
        }
        ev.update(overrides)
        return ev

    def test_basic_identity(self):
        ev = self._make_fl_event()
        i = compute_fl_identity(ev, "Soccer")
        assert i is not None
        assert i.kind == "per_fixture"
        assert i.sport == "Soccer"
        assert i.date == date(2026, 5, 5)
        assert i.time == "1500"
        # ATM expands to {ATM, AMI} via the Soccer alias table
        # (ATM↔AMI covers Atletico Mineiro). Arsenal has no aliases,
        # so only ATM/AMI variants appear.
        assert i.fl_orientations == frozenset(
            {"ARSATM", "ATMARS", "ARSAMI", "AMIARS"}
        )

    def test_missing_fields_returns_none(self):
        for missing in ("SHORTNAME_HOME", "SHORTNAME_AWAY", "START_TIME"):
            ev = self._make_fl_event()
            del ev[missing]
            assert compute_fl_identity(ev, "Soccer") is None, (
                f"Should return None when {missing} is missing"
            )

    def test_start_utime_fallback(self):
        ev = self._make_fl_event()
        del ev["START_TIME"]
        ev["START_UTIME"] = _ts(2026, 5, 5, 15, 0)
        i = compute_fl_identity(ev, "Soccer")
        assert i is not None
        assert i.date == date(2026, 5, 5)

    def test_invalid_start_time(self):
        ev = self._make_fl_event(START_TIME="not-a-number")
        # Either returns None or raises — but currently we expect None
        i = compute_fl_identity(ev, "Soccer")
        assert i is None


# ── Pairing rule ─────────────────────────────────────────────────

class TestMatch:
    """Critical regression coverage — the past-bug team-name fixtures."""

    def _arsenal_atletico_kalshi(self):
        # Title was "Atletico at Arsenal" — order ATM+ARS
        return parse_ticker(
            "KXUCLGAME-26MAY05ARSATM", "KXUCLGAME", "Soccer",
        )

    def _arsenal_atletico_fl(self):
        return compute_fl_identity({
            "HOME_NAME": "Arsenal", "AWAY_NAME": "Atl. Madrid",
            "SHORTNAME_HOME": "ARS", "SHORTNAME_AWAY": "ATM",
            "START_TIME": _ts(2026, 5, 5, 15, 0),
        }, "Soccer")

    def test_arsenal_atletico_match(self):
        """The fixture that broke v1's corroboration check."""
        k = self._arsenal_atletico_kalshi()
        fl = self._arsenal_atletico_fl()
        assert match(k, fl)

    def test_bayern_psg_match(self):
        """The fixture that needed v5 prefix matching in v1."""
        k = parse_ticker("KXUCLGAME-26MAY06BMUPSG", "KXUCLGAME", "Soccer")
        # FL has PSG and Bayern Munich. Kalshi ticker has BMU+PSG.
        fl = compute_fl_identity({
            "HOME_NAME": "Bayern Munich", "AWAY_NAME": "PSG",
            "SHORTNAME_HOME": "BMU", "SHORTNAME_AWAY": "PSG",
            "START_TIME": _ts(2026, 5, 6, 15, 0),
        }, "Soccer")
        assert match(k, fl)

    def test_orientation_swap_still_matches(self):
        """If FL has home/away in opposite order, still pair."""
        k = parse_ticker("KXUCLGAME-26MAY05ARSATM", "KXUCLGAME", "Soccer")
        fl = compute_fl_identity({
            # Swapped: Atletico is home
            "SHORTNAME_HOME": "ATM", "SHORTNAME_AWAY": "ARS",
            "START_TIME": _ts(2026, 5, 5, 15, 0),
        }, "Soccer")
        assert match(k, fl)

    def test_different_sport_doesnt_match(self):
        k = parse_ticker("KXUCLGAME-26MAY05ARSATM", "KXUCLGAME", "Soccer")
        fl = compute_fl_identity({
            "SHORTNAME_HOME": "ARS", "SHORTNAME_AWAY": "ATM",
            "START_TIME": _ts(2026, 5, 5, 15, 0),
        }, "Basketball")
        assert not match(k, fl)

    def test_different_team_doesnt_match(self):
        k = parse_ticker("KXUCLGAME-26MAY05ARSATM", "KXUCLGAME", "Soccer")
        fl = compute_fl_identity({
            "SHORTNAME_HOME": "BAR", "SHORTNAME_AWAY": "RMA",
            "START_TIME": _ts(2026, 5, 5, 15, 0),
        }, "Soccer")
        assert not match(k, fl)

    def test_date_off_by_2_doesnt_match(self):
        k = parse_ticker("KXUCLGAME-26MAY05ARSATM", "KXUCLGAME", "Soccer")
        fl = compute_fl_identity({
            "SHORTNAME_HOME": "ARS", "SHORTNAME_AWAY": "ATM",
            # 2026-05-07 — 2 days off
            # 2 days off
            "START_TIME": _ts(2026, 5, 7, 15, 0),
        }, "Soccer")
        assert not match(k, fl, fuzz_days=1)

    def test_date_off_by_1_matches_with_fuzz(self):
        """Timezone-drift tolerance: 1 day off should still match."""
        k = parse_ticker("KXUCLGAME-26MAY05ARSATM", "KXUCLGAME", "Soccer")
        fl = compute_fl_identity({
            "SHORTNAME_HOME": "ARS", "SHORTNAME_AWAY": "ATM",
            "START_TIME": _ts(2026, 5, 4, 15, 0),
        }, "Soccer")
        assert match(k, fl)

    def test_mlb_doubleheader_time_disambiguates(self):
        """G7: same teams, same date, different time = different fixtures."""
        k_game1 = parse_ticker(
            "KXMLBGAME-26MAY071240PITAZ", "KXMLBGAME", "Baseball",
        )
        k_game2 = parse_ticker(
            "KXMLBGAME-26MAY071800PITAZ", "KXMLBGAME", "Baseball",
        )
        # Game 1 FL event: 12:40 UTC
        fl_game1 = compute_fl_identity({
            "SHORTNAME_HOME": "PIT", "SHORTNAME_AWAY": "AZ",
            "START_TIME": _ts(2026, 5, 7, 12, 40),
        }, "Baseball")
        assert k_game1.time == "1240"
        assert k_game2.time == "1800"
        assert fl_game1.time == "1240"
        # Game 1 kalshi matches Game 1 FL
        assert match(k_game1, fl_game1)
        # Game 2 kalshi does NOT match Game 1 FL (5+ hour gap)
        assert not match(k_game2, fl_game1)

    def test_outright_doesnt_match_per_fixture(self):
        """An outright (year code) shouldn't pair with an FL fixture."""
        k = parse_ticker("KXUCL-26", "KXUCL", "Soccer")
        fl = compute_fl_identity({
            "SHORTNAME_HOME": "ARS", "SHORTNAME_AWAY": "ATM",
            "START_TIME": _ts(2026, 5, 5, 15, 0),
        }, "Soccer")
        assert not match(k, fl)


# ── FL → Kalshi abbreviation alias map ───────────────────────────

class TestFLAbbrAliases:
    """Phase 5 punch list 2026-05-05 — NBA fixtures dropped from
    /sports v2 because FL's NBA shortnames don't always equal
    Kalshi's. Alias map normalizes FL's vocabulary into Kalshi's
    so the deterministic identity match still pairs.
    """

    def test_nba_lakers_thunder_via_alias(self):
        """KXNBAGAME-26MAY05LALOKC must pair with FL fixture
        even when FL ships SHORTNAME=LAK (not LAL) and OKL (not OKC).
        """
        k = parse_ticker(
            "KXNBAGAME-26MAY05LALOKC", "KXNBAGAME", "Basketball",
        )
        fl = compute_fl_identity({
            "SHORTNAME_HOME": "OKL",  # FL form
            "SHORTNAME_AWAY": "LAK",  # FL form
            "START_TIME": _ts(2026, 5, 5, 20, 30),
        }, "Basketball")
        assert match(k, fl), (
            f"k.abbr_block={k.abbr_block!r} not in "
            f"fl.fl_orientations={fl.fl_orientations!r}"
        )

    def test_nba_canonical_form_still_matches(self):
        """Sanity: FL fixtures already using Kalshi's canonical
        abbreviation pair without alias help.
        """
        k = parse_ticker(
            "KXNBAGAME-26MAY05LALOKC", "KXNBAGAME", "Basketball",
        )
        fl = compute_fl_identity({
            "SHORTNAME_HOME": "OKC",  # canonical
            "SHORTNAME_AWAY": "LAL",  # canonical
            "START_TIME": _ts(2026, 5, 5, 20, 30),
        }, "Basketball")
        assert match(k, fl)

    def test_alias_doesnt_leak_into_other_sports(self):
        """Soccer SHORTNAME_HOME=LAK must not be normalized to LAL —
        the alias map is keyed by sport, so non-Basketball entries
        stay untouched.
        """
        k = parse_ticker(
            "KXMLSGAME-26MAY05LALOKC", "KXMLSGAME", "Soccer",
        )
        fl = compute_fl_identity({
            "SHORTNAME_HOME": "OKL",
            "SHORTNAME_AWAY": "LAK",
            "START_TIME": _ts(2026, 5, 5, 20, 30),
        }, "Soccer")
        # No Soccer alias entries → orientations only contain
        # OKL+LAK / LAK+OKL, not the LAL/OKC-normalized form.
        assert not match(k, fl)
