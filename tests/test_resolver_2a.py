"""Tests for the Phase 2A resolver scaffolding.

Covers types, normalization, FL extraction, Kalshi extraction.
Pure unit tests — no DB, no network. The Phase 2B PR will add
integration tests against Postgres for the matching pipeline.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from resolver import (
    FLResolverModule,
    FixtureSignal,
    KalshiResolverModule,
    MatchResult,
    ReasonCode,
    ResolverModule,
    TeamCandidate,
)
from resolver._normalize import normalize_name


# ── Normalization ───────────────────────────────────────────────

class TestNormalize:
    def test_empty_input(self):
        assert normalize_name("") == ""
        assert normalize_name(None) == ""

    def test_lowercase(self):
        assert normalize_name("Bayern Munich") == "bayern munich"

    def test_strips_accents(self):
        assert normalize_name("Atlético Madrid") == "atletico madrid"
        # NFD-only normalization: ø (U+00F8, "Latin small letter o with
        # stroke") is a standalone base letter, NOT a decomposable
        # accented form. So it stays as-is. Mapping ø→o, æ→ae, ß→ss
        # belongs in the alias table, not the normalizer (per
        # architecture §9.2 — keep normalization strict).
        assert normalize_name("FC København") == "fc københavn"
        # Real test: things that DO decompose strip cleanly.
        assert normalize_name("Crvena Zvezda") == "crvena zvezda"
        assert normalize_name("Schalke 04 — Köln") == "schalke 04 koln"

    def test_collapses_whitespace(self):
        assert normalize_name("  Real   Madrid  ") == "real madrid"
        assert normalize_name("AC\tMilan\nFC") == "ac milan fc"

    def test_strips_punctuation(self):
        assert normalize_name("Real Madrid C.F.") == "real madrid c f"
        assert normalize_name("AC/Milan-FC") == "ac milan fc"

    def test_idempotent(self):
        for s in ["", "Bayern Munich", "Atlético", "FC København"]:
            assert normalize_name(normalize_name(s)) == normalize_name(s)


# ── Types: model validation ─────────────────────────────────────

class TestTeamCandidate:
    def test_valid(self):
        tc = TeamCandidate(
            raw="Bayern", normalized="bayern", kind="name", weight=0.9,
        )
        assert tc.weight == 0.9

    def test_weight_bounds(self):
        with pytest.raises(ValidationError):
            TeamCandidate(raw="x", normalized="x", kind="name", weight=1.5)
        with pytest.raises(ValidationError):
            TeamCandidate(raw="x", normalized="x", kind="name", weight=-0.1)

    def test_default_weight(self):
        tc = TeamCandidate(raw="x", normalized="x", kind="name")
        assert tc.weight == 1.0

    def test_immutable(self):
        tc = TeamCandidate(raw="x", normalized="x", kind="name")
        with pytest.raises(ValidationError):
            tc.weight = 0.5


class TestFixtureSignal:
    def _candidates(self):
        return [TeamCandidate(raw="x", normalized="x", kind="name")]

    def test_minimal_construction(self):
        sig = FixtureSignal(
            provider="fl",
            provider_record_id="abc",
            sport="soccer",
            home_team_candidates=self._candidates(),
            away_team_candidates=self._candidates(),
            kickoff_at=datetime(2026, 5, 7, 22, tzinfo=timezone.utc),
        )
        assert sig.kickoff_confidence == 1.0
        assert sig.raw_signals == {}

    def test_kickoff_can_be_none(self):
        # Some provider records don't have a kickoff yet (futures, etc.)
        sig = FixtureSignal(
            provider="kalshi",
            provider_record_id="x",
            sport="",
            home_team_candidates=self._candidates(),
            away_team_candidates=self._candidates(),
            kickoff_at=None,
            kickoff_confidence=0.0,
        )
        assert sig.kickoff_at is None


class TestMatchResult:
    def test_unmatched(self):
        r = MatchResult(
            fixture_id=None,
            confidence=0.0,
            reason_code=ReasonCode.NO_MATCH,
            resolver_version="test@0.0",
        )
        assert r.fixture_id is None
        assert r.candidate_fixtures == []

    def test_reason_codes_are_strings(self):
        # Enum is StrEnum-like via inheritance from str
        assert ReasonCode.STRICT == "strict"
        assert ReasonCode.REVIEW_QUEUE == "review_queue"


# ── Protocol conformance ─────────────────────────────────────────

class TestProtocolConformance:
    def test_fl_module_conforms(self):
        m = FLResolverModule()
        assert isinstance(m, ResolverModule)
        assert m.provider == "fl"

    def test_kalshi_module_conforms(self):
        m = KalshiResolverModule()
        assert isinstance(m, ResolverModule)
        assert m.provider == "kalshi"


# ── FL extraction ───────────────────────────────────────────────

class TestFLExtractSignal:
    def setup_method(self):
        self.m = FLResolverModule()

    def test_full_record(self):
        raw = {
            "EVENT_ID":                 "fl_abc",
            "HOME_NAME":                "Bayern Munich",
            "AWAY_NAME":                "PSG",
            "SHORTNAME_HOME":           "BAY",
            "SHORTNAME_AWAY":           "PSG",
            "HOME_PARTICIPANT_TEAM_ID": ["fl-team-bayern"],
            "AWAY_PARTICIPANT_TEAM_ID": ["fl-team-psg"],
            "START_TIME":               1778191200,  # 2026-05-07 18:00 UTC
            "STAGE_TYPE":               "SCHEDULED",
        }
        tournament = {
            "TOURNAMENT_STAGE_ID": "stg_ucl",
            "NAME":                "Europe: Champions League",
            "NAME_PART_1":         "Europe",
            "NAME_PART_2":         "Champions League",
        }
        sig = self.m.extract_signal(raw, tournament_context=tournament, sport="soccer")
        assert sig is not None
        assert sig.provider == "fl"
        assert sig.provider_record_id == "fl_abc"
        assert sig.sport == "soccer"
        assert sig.kickoff_at == datetime(2026, 5, 7, 22, tzinfo=timezone.utc)
        assert sig.kickoff_confidence == 1.0
        assert sig.competition_hint == "stg_ucl"

        # Home: 3 candidates — fl_team_id, name, shortname
        kinds = [c.kind for c in sig.home_team_candidates]
        assert kinds == ["fl_team_id", "name", "shortname"]
        assert sig.home_team_candidates[1].normalized == "bayern munich"
        assert sig.home_team_candidates[2].normalized == "BAY"

    def test_missing_event_id_returns_none(self):
        sig = self.m.extract_signal({"HOME_NAME": "X"})
        assert sig is None

    def test_missing_optional_fields(self):
        # Only EVENT_ID present — extraction succeeds with empty candidates.
        sig = self.m.extract_signal({"EVENT_ID": "x"})
        assert sig is not None
        assert sig.home_team_candidates == []
        assert sig.away_team_candidates == []
        assert sig.kickoff_at is None
        assert sig.kickoff_confidence == 0.0

    def test_no_tournament_context(self):
        sig = self.m.extract_signal(
            {"EVENT_ID": "x", "HOME_NAME": "A", "AWAY_NAME": "B"},
            tournament_context=None,
        )
        assert sig is not None
        assert sig.competition_hint is None

    def test_start_utime_fallback(self):
        # Some FL responses use START_UTIME instead of START_TIME.
        sig = self.m.extract_signal({
            "EVENT_ID":   "x",
            "START_UTIME": 1778191200,
        })
        assert sig.kickoff_at == datetime(2026, 5, 7, 22, tzinfo=timezone.utc)
        assert sig.kickoff_confidence == 1.0

    def test_invalid_start_time_yields_none_kickoff(self):
        sig = self.m.extract_signal({
            "EVENT_ID":   "x",
            "START_TIME": "not-a-number",
        })
        assert sig.kickoff_at is None
        assert sig.kickoff_confidence == 0.0


# ── Kalshi extraction ───────────────────────────────────────────

class TestKalshiExtractSignal:
    def setup_method(self):
        self.m = KalshiResolverModule()

    def test_per_fixture_with_title(self):
        raw = {
            "event_ticker":  "KXUCLGAME-26MAY07BAYPSG",
            "series_ticker": "KXUCLGAME",
            "title":         "Bayern Munich vs PSG",
            "_sport":        "Soccer",
            "_kickoff_dt":   "2026-05-07T19:00:00+00:00",
        }
        sig = self.m.extract_signal(raw)
        assert sig is not None
        assert sig.provider == "kalshi"
        assert sig.provider_record_id == "KXUCLGAME-26MAY07BAYPSG"
        # title parsed: "Bayern Munich" home, "PSG" away
        home_names = [c.normalized for c in sig.home_team_candidates if c.kind == "name"]
        away_names = [c.normalized for c in sig.away_team_candidates if c.kind == "name"]
        assert "bayern munich" in home_names
        assert "psg" in away_names
        # abbr_block 'BAYPSG' added direction-blind to both sides
        home_abbrs = [c.normalized for c in sig.home_team_candidates if c.kind == "kalshi_abbr"]
        away_abbrs = [c.normalized for c in sig.away_team_candidates if c.kind == "kalshi_abbr"]
        assert "BAYPSG" in home_abbrs
        assert "BAYPSG" in away_abbrs
        # Explicit kickoff with confidence 1.0
        assert sig.kickoff_at == datetime(2026, 5, 7, 19, tzinfo=timezone.utc)
        assert sig.kickoff_confidence == 1.0

    def test_at_separator_inverts_orientation(self):
        # 'X at Y' = X away, Y home (legacy convention)
        raw = {
            "event_ticker":  "KXNBAGAME-26MAY07LALOKC",
            "series_ticker": "KXNBAGAME",
            "title":         "LAL at OKC",
            "_sport":        "Basketball",
        }
        sig = self.m.extract_signal(raw)
        home_names = [c.normalized for c in sig.home_team_candidates if c.kind == "name"]
        away_names = [c.normalized for c in sig.away_team_candidates if c.kind == "name"]
        assert "okc" in home_names
        assert "lal" in away_names

    def test_outright_returns_none(self):
        # Outrights aren't per_fixture; matcher skips them.
        raw = {
            "event_ticker":  "KXBALLONDOR-26MESSI",
            "series_ticker": "KXBALLONDOR",
            "title":         "Will Messi win the 2026 Ballon d'Or?",
            "_sport":        "Soccer",
        }
        sig = self.m.extract_signal(raw)
        assert sig is None

    # ── Phase 2C.2.6 — prop-bet title-suffix detection ─────────
    #
    # parse_ticker classifies these tickers as per_fixture (the
    # KNOWN_SUFFIXES match the sub-market suffix on the series
    # ticker), but the title's trailing ": <PropType>" segment
    # reveals they're prop bets, not game markets. extract_signal
    # must return None so the alias tier never sees them.

    def test_prop_suffix_totals_returns_none(self):
        raw = {
            "event_ticker":  "KXEPLTOTAL-26MAY09BHA",
            "series_ticker": "KXEPLTOTAL",
            "title":         "Brighton: Totals",
            "_sport":        "Soccer",
            "_kickoff_dt":   "2026-05-09T14:00:00+00:00",
        }
        assert self.m.extract_signal(raw) is None

    def test_prop_suffix_spreads_returns_none(self):
        raw = {
            "event_ticker":  "KXEPLSPREAD-26MAY09WOL",
            "series_ticker": "KXEPLSPREAD",
            "title":         "Wolfsburg: Spreads",
            "_sport":        "Soccer",
            "_kickoff_dt":   "2026-05-09T14:00:00+00:00",
        }
        assert self.m.extract_signal(raw) is None

    def test_prop_suffix_first_goalscorer_returns_none(self):
        raw = {
            "event_ticker":  "KXEPLGSCORE-26MAY09BHA",
            "series_ticker": "KXEPLGSCORE",
            "title":         "Brighton: First Goalscorer",
            "_sport":        "Soccer",
            "_kickoff_dt":   "2026-05-09T14:00:00+00:00",
        }
        assert self.m.extract_signal(raw) is None

    def test_prop_suffix_both_teams_to_score_returns_none(self):
        raw = {
            "event_ticker":  "KXEPLBTTS-26MAY09ELC",
            "series_ticker": "KXEPLBTTS",
            "title":         "Elche: Both Teams to Score",
            "_sport":        "Soccer",
            "_kickoff_dt":   "2026-05-09T14:00:00+00:00",
        }
        assert self.m.extract_signal(raw) is None

    def test_prop_suffix_game_spread_returns_none(self):
        raw = {
            "event_ticker":  "KXEPLGSPREAD-26MAY09BHA",
            "series_ticker": "KXEPLGSPREAD",
            "title":         "Brighton: Game Spread",
            "_sport":        "Soccer",
            "_kickoff_dt":   "2026-05-09T14:00:00+00:00",
        }
        assert self.m.extract_signal(raw) is None

    def test_prop_suffix_exact_match_score_returns_none(self):
        raw = {
            "event_ticker":  "KXEPLEMS-26MAY09BHA",
            "series_ticker": "KXEPLEMS",
            "title":         "Brighton: Exact Match Score",
            "_sport":        "Soccer",
            "_kickoff_dt":   "2026-05-09T14:00:00+00:00",
        }
        assert self.m.extract_signal(raw) is None

    def test_prop_suffix_singular_total_also_filtered(self):
        # Singular "Total" / "Spread" variants — listed in the
        # initial set because both are observed in production.
        raw = {
            "event_ticker":  "KXMLBTOTAL-26MAY09NYY",
            "series_ticker": "KXMLBTOTAL",
            "title":         "New York Yankees: Total",
            "_sport":        "Baseball",
            "_kickoff_dt":   "2026-05-09T23:00:00+00:00",
        }
        assert self.m.extract_signal(raw) is None

    def test_prop_suffix_case_insensitive(self):
        # "spreads" lowercase, "SPREADS" uppercase, "Spreads"
        # title-case — all the same prop-bet identifier.
        for variant in ("spreads", "SPREADS", "Spreads", "SpReAdS"):
            raw = {
                "event_ticker":  "KXEPLSPREAD-26MAY09BHA",
                "series_ticker": "KXEPLSPREAD",
                "title":         f"Brighton: {variant}",
                "_sport":        "Soccer",
                "_kickoff_dt":   "2026-05-09T14:00:00+00:00",
            }
            assert self.m.extract_signal(raw) is None, (
                f"variant {variant!r} not filtered"
            )

    def test_prop_suffix_with_multiple_colons_uses_last_segment(self):
        # "Group A: Round 1: Brighton vs Bournemouth" — last segment
        # is the game, NOT a prop type. Must NOT be filtered.
        raw = {
            "event_ticker":  "KXEPLGAME-26MAY09BHABOU",
            "series_ticker": "KXEPLGAME",
            "title":         "Group A: Round 1: Brighton vs Bournemouth",
            "_sport":        "Soccer",
            "_kickoff_dt":   "2026-05-09T14:00:00+00:00",
        }
        sig = self.m.extract_signal(raw)
        assert sig is not None  # real game; should produce signal

    def test_prop_suffix_game_level_prop_filtered(self):
        # "Bayern Munich vs PSG: Spreads" — prop bet on a specific
        # game. Last segment is "spreads" → filter. The game-level
        # market would have its own GAME-suffix ticker; this is the
        # spread-specific sub-market and shouldn't reach the matcher.
        raw = {
            "event_ticker":  "KXUCLSPREAD-26MAY07BAYPSG",
            "series_ticker": "KXUCLSPREAD",
            "title":         "Bayern Munich vs PSG: Spreads",
            "_sport":        "Soccer",
            "_kickoff_dt":   "2026-05-07T19:00:00+00:00",
        }
        assert self.m.extract_signal(raw) is None

    # ── Phase 2D.2.6 — tennis-specific suffixes ────────────────
    #
    # Added after the 2D.2.5 dry-run showed records like "Alexander
    # Bublik: Total Games" reaching anchor_failed instead of
    # extraction_skipped. Same upstream-filter pattern as 2C.2.6.

    def test_prop_suffix_total_games_returns_none(self):
        # The named example from the 2D.2.5 dry-run output.
        raw = {
            "event_ticker":  "KXATPTOTALGAMES-26MAY09BUBLIK",
            "series_ticker": "KXATPTOTALGAMES",
            "title":         "Alexander Bublik: Total Games",
            "_sport":        "Tennis",
            "_kickoff_dt":   "2026-05-09T13:00:00+00:00",
        }
        assert self.m.extract_signal(raw) is None

    def test_prop_suffix_set_winner_returns_none(self):
        # Tennis sub-market identifying which player wins a specific set.
        raw = {
            "event_ticker":  "KXATPSETWIN-26MAY09KECMRUBL",
            "series_ticker": "KXATPSETWIN",
            "title":         "Andrey Rublev: Set Winner",
            "_sport":        "Tennis",
            "_kickoff_dt":   "2026-05-09T14:00:00+00:00",
        }
        assert self.m.extract_signal(raw) is None

    def test_prop_suffix_match_winner_returns_none(self):
        raw = {
            "event_ticker":  "KXATPMATCHWIN-26MAY09KECMRUBL",
            "series_ticker": "KXATPMATCHWIN",
            "title":         "Miomir Kecmanovic: Match Winner",
            "_sport":        "Tennis",
            "_kickoff_dt":   "2026-05-09T14:00:00+00:00",
        }
        assert self.m.extract_signal(raw) is None

    def test_prop_suffix_tiebreak_returns_none(self):
        # "Tiebreak" markets bet on whether a specific set goes
        # to a tiebreak. Per-player prop, not a game-level market.
        raw = {
            "event_ticker":  "KXATPTIEBREAK-26MAY09KECMRUBL",
            "series_ticker": "KXATPTIEBREAK",
            "title":         "Miomir Kecmanovic: Tiebreak",
            "_sport":        "Tennis",
            "_kickoff_dt":   "2026-05-09T14:00:00+00:00",
        }
        assert self.m.extract_signal(raw) is None

    def test_tennis_match_real_game_still_produces_signal(self):
        # Regression: a real tennis game ticker (no prop suffix in
        # title) must still produce a signal — the alias tier (and
        # post-2D.3, the fuzzy tier) processes it normally.
        raw = {
            "event_ticker":  "KXATPMATCH-26MAY09KECMRUBL",
            "series_ticker": "KXATPMATCH",
            "title":         "Miomir Kecmanovic vs Andrey Rublev",
            "_sport":        "Tennis",
            "_kickoff_dt":   "2026-05-09T14:00:00+00:00",
        }
        sig = self.m.extract_signal(raw)
        assert sig is not None
        # Title parsed: "Miomir Kecmanovic" home, "Andrey Rublev" away
        home_names = [c.normalized for c in sig.home_team_candidates if c.kind == "name"]
        assert "miomir kecmanovic" in home_names

    # ── Regression: real game titles still produce signals ─────

    def test_no_colon_title_still_produces_signal(self):
        # The 2C.2.6 filter must not reject titles without ": ".
        raw = {
            "event_ticker":  "KXEPLGAME-26MAY09BHABOU",
            "series_ticker": "KXEPLGAME",
            "title":         "Brighton vs Bournemouth",
            "_sport":        "Soccer",
            "_kickoff_dt":   "2026-05-09T14:00:00+00:00",
        }
        sig = self.m.extract_signal(raw)
        assert sig is not None

    def test_non_prop_colon_title_still_produces_signal(self):
        # ": <something>" where <something> isn't a prop suffix.
        # e.g. "Champions League: Bayern vs PSG" — last segment is
        # the game, not a prop type. Real game; signal expected.
        raw = {
            "event_ticker":  "KXUCLGAME-26MAY07BAYPSG",
            "series_ticker": "KXUCLGAME",
            "title":         "Champions League: Bayern Munich vs PSG",
            "_sport":        "Soccer",
            "_kickoff_dt":   "2026-05-07T19:00:00+00:00",
        }
        sig = self.m.extract_signal(raw)
        assert sig is not None

    def test_missing_event_ticker_returns_none(self):
        sig = self.m.extract_signal({"title": "x", "_sport": "Soccer"})
        assert sig is None

    def test_kickoff_from_ticker_when_no_explicit_dt(self):
        # No _kickoff_dt — fall back to identity.date with date-only confidence.
        raw = {
            "event_ticker":  "KXEPLGAME-26MAY07ARSCHE",
            "series_ticker": "KXEPLGAME",
            "title":         "Arsenal vs Chelsea",
            "_sport":        "Soccer",
            # no _kickoff_dt
        }
        sig = self.m.extract_signal(raw)
        assert sig is not None
        assert sig.kickoff_at is not None
        assert sig.kickoff_at.date().isoformat() == "2026-05-07"
        # Date-only fallback yields confidence 0.6 + 18:00 UTC default
        assert sig.kickoff_confidence == 0.6
        assert sig.kickoff_at.hour == 18

    def test_kickoff_from_ticker_with_time_component(self):
        # G7 pattern: KXMLBGAME-26MAY071540PITAZ — date + HHMM + abbr
        raw = {
            "event_ticker":  "KXMLBGAME-26MAY071540PITAZ",
            "series_ticker": "KXMLBGAME",
            "title":         "Pittsburgh at Arizona",
            "_sport":        "Baseball",
        }
        sig = self.m.extract_signal(raw)
        assert sig is not None
        # Date+time fallback yields confidence 0.85
        assert sig.kickoff_confidence == 0.85
        assert sig.kickoff_at.hour == 15
        assert sig.kickoff_at.minute == 40

    def test_competition_hint_uses_series_ticker(self):
        # Phase 2A.6: series_ticker is the canonical hint (matches the
        # bootstrap_sp_competitions seed key). _soccer_comp is human
        # display text and is preserved on raw_signals only.
        raw = {
            "event_ticker":  "KXUCLGAME-26MAY07BAYPSG",
            "series_ticker": "KXUCLGAME",
            "title":         "Bayern vs PSG",
            "_sport":        "Soccer",
            "_soccer_comp":  "Champions League",
        }
        sig = self.m.extract_signal(raw)
        assert sig.competition_hint == "KXUCLGAME"
        assert sig.raw_signals["soccer_comp"] == "Champions League"

    def test_competition_hint_falls_back_to_series(self):
        raw = {
            "event_ticker":  "KXNBAGAME-26MAY07LALOKC",
            "series_ticker": "KXNBAGAME",
            "title":         "LAL at OKC",
            "_sport":        "Basketball",
        }
        sig = self.m.extract_signal(raw)
        assert sig.competition_hint == "KXNBAGAME"

    def test_sport_override(self):
        raw = {
            "event_ticker":  "KXNBAGAME-26MAY07LALOKC",
            "series_ticker": "KXNBAGAME",
            "title":         "LAL at OKC",
            "_sport":        "",  # not classified
        }
        sig = self.m.extract_signal(raw, sport_override="basketball")
        assert sig.sport == "basketball"
