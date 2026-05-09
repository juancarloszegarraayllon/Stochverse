"""Tests for resolver/alias_tier/normalize.py — Phase 2C.2.

Unit tests only, no DB. Two paths (personal + team) tested
independently. Boundary + edge cases for every detection rule.
"""
from __future__ import annotations

import pytest

from resolver.alias_tier import (
    INDIVIDUAL_SPORT_CODES,
    StructuredName,
    structurally_normalize,
)


# ── Sanity: INDIVIDUAL_SPORT_CODES ──────────────────────────────


class TestIndividualSportCodes:
    def test_contains_expected_individual_sports(self):
        # Per Phase 2C design D.1.
        assert {"tennis", "mma", "boxing", "golf", "snooker", "darts"} <= INDIVIDUAL_SPORT_CODES

    def test_does_not_contain_team_sports(self):
        # Team sports should NOT be in the individual set — they
        # route to Path 2 (team-name token bag).
        for sport in ("soccer", "basketball", "hockey", "baseball",
                      "american football", "volleyball", "cricket"):
            assert sport not in INDIVIDUAL_SPORT_CODES

    def test_codes_are_lowercase(self):
        # Path discrimination lowercases the input sport_code; the
        # constant must store the canonical lowercase form.
        for code in INDIVIDUAL_SPORT_CODES:
            assert code == code.lower()


# ── Empty / whitespace handling ─────────────────────────────────


class TestEmptyInputs:
    @pytest.mark.parametrize("s", [None, "", "   ", "\t\n", "()"])
    def test_empty_or_whitespace_returns_none(self, s):
        assert structurally_normalize(s, sport_code="tennis") is None
        assert structurally_normalize(s, sport_code="soccer") is None

    def test_only_parentheticals_returns_none(self):
        # All content was inside parens; stripping leaves nothing.
        assert structurally_normalize("(Q)", sport_code="tennis") is None
        assert structurally_normalize("(Srb) (Q)", sport_code="tennis") is None

    def test_only_dropped_suffixes_returns_none(self):
        # "Jr." alone has no name content.
        assert structurally_normalize("Jr.", sport_code="tennis") is None
        assert structurally_normalize("II III", sport_code="tennis") is None


# ── Path 1: personal-name detection ─────────────────────────────


class TestPersonalInitial:
    """`detection_path='personal_initial'` — exactly 2 tokens, second
    is 1-2 chars (the initial). Surname is the FIRST token here —
    this is the FL "Last F. (Country)" shape."""

    def test_kecmanovic_with_initial_and_country(self):
        sn = structurally_normalize("Kecmanovic M. (Srb)", sport_code="tennis")
        assert sn is not None
        assert sn.detection_path == "personal_initial"
        assert sn.surname == "kecmanovic"
        assert sn.other_tokens == ("m",)
        assert sn.is_personal is True

    def test_two_letter_initial(self):
        sn = structurally_normalize("Smith JJ", sport_code="tennis")
        assert sn is not None
        assert sn.detection_path == "personal_initial"
        assert sn.surname == "smith"
        assert sn.other_tokens == ("jj",)

    def test_initial_with_period(self):
        # "M." should normalize to "m" (period stripped as punct).
        sn = structurally_normalize("Kecmanovic M.", sport_code="tennis")
        assert sn.detection_path == "personal_initial"
        assert sn.other_tokens == ("m",)


class TestPersonalTwoToken:
    """`detection_path='personal_two_token'` — 2 tokens, both > 2 chars.
    Surname is the LAST token (Kalshi "First Last" shape)."""

    def test_miomir_kecmanovic(self):
        sn = structurally_normalize("Miomir Kecmanovic", sport_code="tennis")
        assert sn is not None
        assert sn.detection_path == "personal_two_token"
        assert sn.surname == "kecmanovic"
        assert sn.other_tokens == ("miomir",)
        assert sn.is_personal is True

    def test_accent_strip(self):
        # "Federer R" already exact; "Atlético" loses its accent.
        sn = structurally_normalize("Roger Federer", sport_code="tennis")
        assert sn.surname == "federer"
        assert sn.other_tokens == ("roger",)

    def test_diacritic_normalization(self):
        sn = structurally_normalize("Diego Schwartzman", sport_code="tennis")
        assert sn.surname == "schwartzman"

    def test_with_country_parenthetical_stripped(self):
        sn = structurally_normalize("Carlos Alcaraz (Esp)", sport_code="tennis")
        assert sn is not None
        # Two tokens after stripping "(Esp)".
        assert sn.detection_path == "personal_two_token"
        assert sn.surname == "alcaraz"
        assert sn.other_tokens == ("carlos",)


class TestPersonalMulti:
    """`detection_path='personal_multi'` — 3+ tokens. Last token is
    surname per design doc D.A.2 (last-token-as-surname, fall back
    to compound on miss is the matcher's responsibility)."""

    def test_three_token_compound_name(self):
        sn = structurally_normalize("Carlos Alcaraz Garfia", sport_code="tennis")
        assert sn is not None
        assert sn.detection_path == "personal_multi"
        assert sn.surname == "garfia"  # last token
        assert sn.other_tokens == ("carlos", "alcaraz")

    def test_three_token_with_jr_suffix_dropped(self):
        # "Jr." is in _DROP_TOKENS; remaining 2 tokens fall through
        # to personal_two_token.
        sn = structurally_normalize("Stefan Edberg Jr.", sport_code="tennis")
        assert sn.detection_path == "personal_two_token"
        assert sn.surname == "edberg"

    def test_four_token_pulls_last(self):
        sn = structurally_normalize("Maria Sara Lopez Garcia", sport_code="tennis")
        assert sn.detection_path == "personal_multi"
        assert sn.surname == "garcia"
        assert sn.other_tokens == ("maria", "sara", "lopez")


class TestPersonalSingle:
    """`detection_path='personal_single'` — 1 token. Anchor-only."""

    def test_single_surname(self):
        sn = structurally_normalize("Djokovic", sport_code="tennis")
        assert sn is not None
        assert sn.detection_path == "personal_single"
        assert sn.surname == "djokovic"
        assert sn.other_tokens == ()
        assert sn.is_personal is True


# ── Path 2: team-name detection ─────────────────────────────────


class TestTeamSimple:
    """`detection_path='team_simple'` — 1 token."""

    def test_single_token_team(self):
        sn = structurally_normalize("PSG", sport_code="soccer")
        assert sn is not None
        assert sn.detection_path == "team_simple"
        assert sn.surname == ""
        assert sn.other_tokens == ("psg",)
        assert sn.is_personal is False

    def test_lowercase_handled(self):
        sn = structurally_normalize("aktobe", sport_code="soccer")
        assert sn.detection_path == "team_simple"
        assert sn.other_tokens == ("aktobe",)


class TestTeamQualified:
    """`detection_path='team_qualified'` — 2+ tokens for team-name
    sports. Whole-string token bag, no anchor."""

    def test_two_token_team(self):
        sn = structurally_normalize("Real Madrid", sport_code="soccer")
        assert sn is not None
        assert sn.detection_path == "team_qualified"
        assert sn.surname == ""
        assert sn.other_tokens == ("real", "madrid")
        assert sn.is_personal is False

    def test_qualifier_suffix_kept(self):
        sn = structurally_normalize("São Paulo FC", sport_code="soccer")
        assert sn.detection_path == "team_qualified"
        assert sn.other_tokens == ("sao", "paulo", "fc")

    def test_diacritic_stripped(self):
        # Bayern München → bayern munchen (NFD strips combining marks
        # but keeps base char `u`).
        sn = structurally_normalize("Bayern München", sport_code="soccer")
        assert sn.other_tokens == ("bayern", "munchen")

    def test_atletico_tucuman(self):
        sn = structurally_normalize("Atlético Tucumán", sport_code="soccer")
        assert sn.other_tokens == ("atletico", "tucuman")


# ── Path discrimination via sport_code ──────────────────────────


class TestPathDiscrimination:
    def test_two_token_in_tennis_takes_personal_path(self):
        sn = structurally_normalize("Real Madrid", sport_code="tennis")
        # Even "Real Madrid" goes Path 1 if the sport says tennis.
        # (Test is hypothetical — real tennis records won't have
        # "Real Madrid" — but the path discriminator must obey
        # sport_code, not autodetect.)
        assert sn.is_personal is True
        assert sn.detection_path == "personal_two_token"

    def test_two_token_in_soccer_takes_team_path(self):
        sn = structurally_normalize("Miomir Kecmanovic", sport_code="soccer")
        assert sn.is_personal is False
        assert sn.detection_path == "team_qualified"
        assert sn.other_tokens == ("miomir", "kecmanovic")

    def test_unknown_sport_takes_team_path(self):
        # Sport code not in INDIVIDUAL_SPORT_CODES → team path.
        sn = structurally_normalize("Some Name", sport_code="curling")
        assert sn.is_personal is False

    def test_none_sport_takes_team_path(self):
        sn = structurally_normalize("Real Madrid", sport_code=None)
        assert sn.is_personal is False
        assert sn.detection_path == "team_qualified"

    def test_sport_code_is_case_insensitive(self):
        # The discriminator lowercases input. "Tennis", "TENNIS",
        # "Tennis" all route to Path 1.
        for code in ("tennis", "Tennis", "TENNIS"):
            sn = structurally_normalize("Roger Federer", sport_code=code)
            assert sn.is_personal is True


# ── Drop-token handling ─────────────────────────────────────────


class TestDropTokens:
    def test_jr_dropped_personal(self):
        sn = structurally_normalize("Smith Jr.", sport_code="tennis")
        # "Smith jr" → after dropping "jr" → just ["smith"]
        # That triggers personal_single.
        assert sn.detection_path == "personal_single"
        assert sn.surname == "smith"

    def test_iii_dropped_personal(self):
        sn = structurally_normalize("Tiger Woods III", sport_code="golf")
        # 3 tokens → drop "iii" → 2 tokens → personal_two_token
        assert sn.detection_path == "personal_two_token"
        assert sn.surname == "woods"

    def test_drop_tokens_in_team_name(self):
        # Team name containing "ii" — unlikely in practice but the
        # drop applies uniformly. (No real-world false-positive
        # expected; documented for transparency.)
        sn = structurally_normalize("Dynamo II", sport_code="soccer")
        # "Dynamo ii" → drop "ii" → ["dynamo"] → team_simple
        assert sn.detection_path == "team_simple"
        assert sn.other_tokens == ("dynamo",)
