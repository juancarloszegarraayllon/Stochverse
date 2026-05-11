"""Tests for resolver/fuzzy_tier/initial_expansion.py — Phase 2D.1.

Pure-function tests, no DB. Per design rev1 sign-off:

- Question A: initials_compatible (binary signal). Symmetric
  prefix-match rule for short tokens.
- Question A.1: candidate_surname_interpretations — 3-retry
  ceiling (default, compound, middle-as-surname).
- Question A.3: multi-initial cases ("J.J. Watt") stay
  no_match. The function returns False on those — the matcher
  routes accordingly.
- Question E.3: candidate index integration (tested via
  TestCandidateIndexMultiInterpretation in this file — exercises
  the actual CandidateIndex.refresh code path).

Lesson from PR #87: real call-path tests as the primary surface.
The CandidateIndex tests use a mocked AsyncSession that returns
controlled rows, then assert the post-refresh by_sport_surname
dict is populated under all expected interpretation keys.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from resolver.fuzzy_tier import (
    candidate_surname_interpretations,
    initials_compatible,
)


# ── initials_compatible (Question A) ───────────────────────────


class TestInitialsCompatibleBasicCases:
    """The four named tennis cases from the design doc."""

    def test_miomir_vs_m(self):
        # Provider: "Miomir Kecmanovic" → remainder ("miomir",)
        # Candidate: "Kecmanovic M. (Srb)" → remainder ("m",)
        assert initials_compatible(("miomir",), ("m",)) is True

    def test_daniil_vs_d(self):
        assert initials_compatible(("daniil",), ("d",)) is True

    def test_john_vs_m_rejected(self):
        # "M" is not the prefix of "john" — incompatible.
        assert initials_compatible(("john",), ("m",)) is False

    def test_two_full_vs_two_initials(self):
        # "miomir andrey" vs "m a" — both prefix-checks pass
        assert initials_compatible(("miomir", "andrey"), ("m", "a")) is True

    def test_two_full_vs_wrong_initials(self):
        # "miomir andrey" vs "m b" — "b" doesn't prefix "miomir" or "andrey"
        assert initials_compatible(("miomir", "andrey"), ("m", "b")) is False


class TestInitialsCompatibleSymmetry:
    """The rule is symmetric — works in both directions."""

    def test_symmetric_provider_initials(self):
        # Provider has initials, candidate has full names
        assert initials_compatible(("m",), ("miomir",)) is True

    def test_symmetric_provider_two_initials(self):
        assert initials_compatible(("m", "a"), ("miomir", "andrey")) is True

    def test_symmetric_with_wrong_initial(self):
        assert initials_compatible(("m",), ("john",)) is False


class TestInitialsCompatibleEmptyInputs:
    """Empty token sequences yield True (no constraint to violate)."""

    def test_both_empty(self):
        assert initials_compatible((), ()) is True

    def test_only_provider_empty(self):
        # Candidate has tokens but no short ones — vacuously compatible
        assert initials_compatible((), ("miomir",)) is True

    def test_only_candidate_empty(self):
        assert initials_compatible(("miomir",), ()) is True

    def test_provider_short_no_candidate_long(self):
        # Provider has an initial but candidate has no long token to
        # prefix-match against — incompatible
        assert initials_compatible(("m",), ()) is False

    def test_candidate_short_no_provider_long(self):
        # Symmetric: candidate has initial, provider has nothing long
        assert initials_compatible((), ("m",)) is False


class TestInitialsCompatibleMultiInitial:
    """A.3 — multi-initial cases like 'J.J. Watt'.

    "j.j. watt" after normalize = ["j", "j", "watt"]. P_short=["j", "j"].
    If candidate is the same shape (also ["j", "j", "watt"]), that's
    trivially compatible. If candidate is ["jj", "watt"] (single
    bigram), then C_long=["jj", "watt"] and "j" prefix-matches "jj"
    — compatible.

    But if candidate is ["watt"] only (surname extracted off, no
    initial info), C_long=["watt"]. "j".startswith("w") → False;
    "watt".startswith("j") → False. Incompatible — design A.3
    documented limitation.
    """

    def test_multi_initial_compatible_when_both_sides_have_them(self):
        assert initials_compatible(("j", "j"), ("james", "john")) is True

    def test_multi_initial_with_no_long_token_incompatible(self):
        # "j j" provider vs "watt" candidate — provider has 2 short
        # tokens, but candidate's only long token "watt" doesn't
        # start with "j". False.
        assert initials_compatible(("j", "j"), ("watt",)) is False


class TestInitialsCompatibleEdgeCases:
    def test_empty_string_token_skipped(self):
        # An empty-string token in the input list is filtered before
        # length classification.
        assert initials_compatible(("miomir", ""), ("m",)) is True

    def test_two_letter_initial_handled(self):
        # "JJ" is length 2 — counts as short (≤ 2). Its prefix
        # check looks for any candidate long token that starts with "jj".
        assert initials_compatible(("jj",), ("james jordan",)) is False  # "james jordan" is one token here
        assert initials_compatible(("jj",), ("jjames",)) is True

    def test_lengths_at_threshold(self):
        # Length 2 = short, length 3 = long.
        assert initials_compatible(("ab",), ("abc",)) is True
        assert initials_compatible(("abc",), ("ab",)) is True


# ── candidate_surname_interpretations (Question A.1 + E.3) ────


class TestSurnameInterpretationsLengths:
    def test_empty_returns_empty(self):
        assert candidate_surname_interpretations([]) == ()

    def test_single_token_returns_single_interpretation(self):
        assert candidate_surname_interpretations(["djokovic"]) == ("djokovic",)

    def test_two_tokens_returns_default_and_compound(self):
        # "Miomir Kecmanovic" → default="kecmanovic" (last),
        # compound="miomir kecmanovic"
        result = candidate_surname_interpretations(["miomir", "kecmanovic"])
        assert result == ("kecmanovic", "miomir kecmanovic")

    def test_three_tokens_returns_three_interpretations(self):
        # "Roberto Bautista Agut" — the named E.3 case
        # default="agut", compound="bautista agut", middle="bautista"
        result = candidate_surname_interpretations(["roberto", "bautista", "agut"])
        assert result == ("agut", "bautista agut", "bautista")

    def test_four_tokens_caps_at_three_interpretations(self):
        # "Maria Sara Lopez Garcia" — default="garcia",
        # compound="lopez garcia", middle="lopez"
        # Per design A.1 ceiling of 3 retries.
        result = candidate_surname_interpretations(
            ["maria", "sara", "lopez", "garcia"]
        )
        assert result == ("garcia", "lopez garcia", "lopez")
        assert len(result) <= 3


class TestSurnameInterpretationsDeduplication:
    def test_two_identical_tokens_dedupes(self):
        # ["a", "a"]: default="a", compound="a a" → ("a", "a a")
        result = candidate_surname_interpretations(["a", "a"])
        assert result == ("a", "a a")

    def test_three_repeating_tokens_dedupes(self):
        # ["a", "a", "a"]: default="a", compound="a a", middle="a"
        # After dedupe: ("a", "a a")
        result = candidate_surname_interpretations(["a", "a", "a"])
        assert result == ("a", "a a")


class TestSurnameInterpretationsBautistaCase:
    """The named E.3 case from the design doc — verify the
    interpretation reaches "bautista" so provider input "Bautista"
    can match."""

    def test_bautista_reachable_via_middle_as_surname(self):
        result = candidate_surname_interpretations(
            ["roberto", "bautista", "agut"]
        )
        assert "bautista" in result

    def test_alcaraz_reachable_via_middle_as_surname(self):
        result = candidate_surname_interpretations(
            ["carlos", "alcaraz", "garfia"]
        )
        # "alcaraz" is the middle-as-surname interpretation
        assert "alcaraz" in result
        # "garfia" is the default (last-token)
        assert "garfia" in result
        # "alcaraz garfia" is the compound
        assert "alcaraz garfia" in result


# ── CandidateIndex multi-interpretation integration (E.3) ─────


class TestCandidateIndexMultiInterpretation:
    """Real call-path test of the CandidateIndex.refresh path post-
    2D.1 update: a personal-sport candidate must be reachable under
    EVERY interpretation its surname tokens generate.

    Mocked AsyncSession returns hand-built rows; assertion is
    against the populated _by_sport_surname dict."""

    @pytest.mark.asyncio
    async def test_three_token_personal_name_indexed_under_all_three(self):
        from resolver.alias_tier.candidates import CandidateIndex

        class _Row:
            def __init__(self, **kwargs):
                for k, v in kwargs.items():
                    setattr(self, k, v)

        rows = [
            _Row(
                team_id=uuid.uuid4(),
                sport_id=2,
                canonical_name="Roberto Bautista Agut",
                sport_code="tennis",
            ),
        ]
        result_obj = MagicMock()
        result_obj.all = MagicMock(return_value=rows)
        session = AsyncMock()
        session.execute = AsyncMock(return_value=result_obj)

        ci = await CandidateIndex.load_all(session)

        # The same candidate must appear under all three surname
        # interpretations. Lookup via candidates_for_surname:
        for surname_key in ("agut", "bautista agut", "bautista"):
            cands = ci.candidates_for_surname(2, surname_key)
            assert len(cands) == 1, (
                f"surname={surname_key!r}: expected 1 candidate, got {len(cands)}"
            )
            assert cands[0].canonical_name == "Roberto Bautista Agut"

    @pytest.mark.asyncio
    async def test_two_token_personal_name_indexed_under_default_and_compound(self):
        from resolver.alias_tier.candidates import CandidateIndex

        class _Row:
            def __init__(self, **kwargs):
                for k, v in kwargs.items():
                    setattr(self, k, v)

        rows = [
            _Row(
                team_id=uuid.uuid4(),
                sport_id=2,
                canonical_name="Miomir Kecmanovic",
                sport_code="tennis",
            ),
        ]
        result_obj = MagicMock()
        result_obj.all = MagicMock(return_value=rows)
        session = AsyncMock()
        session.execute = AsyncMock(return_value=result_obj)

        ci = await CandidateIndex.load_all(session)

        # 2-token: default = "kecmanovic"; compound = "miomir kecmanovic"
        for surname_key in ("kecmanovic", "miomir kecmanovic"):
            cands = ci.candidates_for_surname(2, surname_key)
            assert len(cands) == 1, (
                f"surname={surname_key!r}: expected 1 candidate, got {len(cands)}"
            )

        # Middle-as-surname doesn't apply to 2-token (would equal default).
        # Verify a wrong-key lookup returns empty.
        assert ci.candidates_for_surname(2, "miomir") == []

    @pytest.mark.asyncio
    async def test_personal_initial_path_keeps_only_default_surname(self):
        """Personal_initial detection ("Kecmanovic M. (Srb)") keeps
        just the default surname — multi-interpretation expansion
        doesn't apply because the structural detector's surname is
        already the FIRST token (Kalshi/FL convention), not derived
        from a long-token list."""
        from resolver.alias_tier.candidates import CandidateIndex

        class _Row:
            def __init__(self, **kwargs):
                for k, v in kwargs.items():
                    setattr(self, k, v)

        rows = [
            _Row(
                team_id=uuid.uuid4(),
                sport_id=2,
                canonical_name="Kecmanovic M. (Srb)",
                sport_code="tennis",
            ),
        ]
        result_obj = MagicMock()
        result_obj.all = MagicMock(return_value=rows)
        session = AsyncMock()
        session.execute = AsyncMock(return_value=result_obj)

        ci = await CandidateIndex.load_all(session)

        # Indexed under the surname only.
        cands = ci.candidates_for_surname(2, "kecmanovic")
        assert len(cands) == 1

        # No expansion to "kecmanovic m" or other compound forms —
        # personal_initial detection treats the second token as an
        # initial, not part of the surname.
        for unused_key in ("kecmanovic m", "m kecmanovic", "m"):
            assert ci.candidates_for_surname(2, unused_key) == [], (
                f"surname={unused_key!r}: should not have indexed this candidate"
            )

    @pytest.mark.asyncio
    async def test_team_sport_unaffected_by_multi_interpretation(self):
        """Multi-interpretation expansion is for personal sports only
        (per design — team-sport surnames are empty string in the
        StructuredName, so no by_sport_surname entries get
        created)."""
        from resolver.alias_tier.candidates import CandidateIndex

        class _Row:
            def __init__(self, **kwargs):
                for k, v in kwargs.items():
                    setattr(self, k, v)

        rows = [
            _Row(
                team_id=uuid.uuid4(),
                sport_id=1,
                canonical_name="Real Madrid",
                sport_code="soccer",
            ),
        ]
        result_obj = MagicMock()
        result_obj.all = MagicMock(return_value=rows)
        session = AsyncMock()
        session.execute = AsyncMock(return_value=result_obj)

        ci = await CandidateIndex.load_all(session)

        # Indexed for sport but NOT under any surname key.
        assert len(ci.candidates_for_sport(1)) == 1
        # Team has empty surname → no surname index entry
        assert ci.candidates_for_surname(1, "real madrid") == []
        assert ci.candidates_for_surname(1, "madrid") == []


# ── Static guards (backstop only) ──────────────────────────────


class TestStaticGuards:
    def setup_method(self):
        import inspect
        import resolver.fuzzy_tier.initial_expansion
        self.src = inspect.getsource(resolver.fuzzy_tier.initial_expansion)

    def test_compound_retry_depth_is_three(self):
        """Per design A.1 — stop at 3 retries. The constant exists
        as documentation; the function caps interpretations at 3 by
        construction, not by reading this constant. Static check
        ensures the constant is present + correct."""
        assert "_COMPOUND_RETRY_DEPTH = 3" in self.src

    def test_short_token_threshold_is_two(self):
        """Per design A — 'short' means len ≤ 2 (initial or
        bigram). Constant documents the threshold."""
        assert "_SHORT_TOKEN_MAX_LEN = 2" in self.src
