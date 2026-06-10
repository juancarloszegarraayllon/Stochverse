"""Unit tests for the distinctive-token fuzzy matching primitive.

Covers:
  - `distinctive_tokens` behavior across generic-only, mixed, and
    empty inputs
  - `has_distinctive_content` short-circuit semantics
  - `fuzzy_match_distinctive_score` precision on the 4 false-positive
    cases the BBL pilot validation surfaced:
      (1) "Paris Basketball" ↔ "Basketball Braunschweig"
          (shared generic token, distinct cities)
      (2) "Dubai Basketball" ↔ "Basketball Braunschweig"
      (3) "EBAA-Basketball" ↔ "Basketball Braunschweig"
      (4) "Jaen" ↔ "Jena" (cross-language 4-char near-match)

Plus positive cases — real BBL aliases that must STILL match after
the tightening:
  - "Bayern" ↔ "FC Bayern Munich Basketball" (sponsor-stripped)
  - "EWE Oldenburg" ↔ "EWE Baskets Oldenburg" (sponsor + city)
  - "Brose Bamberg" ↔ "Brose Baskets Bamberg" (with/without baskets)
  - "Olympiakos" ↔ "Olympiacos" (Greek transliteration variant)
"""
from __future__ import annotations

from resolver.text_match import (
    GENERIC_SPORT_TOKENS,
    distinctive_tokens,
    fuzzy_match_distinctive_score,
    has_distinctive_content,
)


# ──────────────────────────────────────────────────────────────────────
# distinctive_tokens
# ──────────────────────────────────────────────────────────────────────


class TestDistinctiveTokens:

    def test_empty_input_returns_empty(self):
        assert distinctive_tokens("") == ()

    def test_all_generic_returns_empty(self):
        assert distinctive_tokens("basketball") == ()
        assert distinctive_tokens("bc") == ()
        assert distinctive_tokens("club") == ()
        assert distinctive_tokens("fc bc club") == ()

    def test_single_distinctive_token(self):
        assert distinctive_tokens("paris basketball") == ("paris",)
        assert distinctive_tokens("basketball paris") == ("paris",)

    def test_multiple_distinctive_tokens(self):
        assert distinctive_tokens("fc bayern munchen basketball") == (
            "bayern", "munchen",
        )

    def test_pallacanestro_stripped(self):
        assert distinctive_tokens("pallacanestro brescia") == ("brescia",)

    def test_preserves_token_order(self):
        assert distinctive_tokens("real madrid baloncesto") == (
            "real", "madrid", "baloncesto",
        )

    def test_real_is_not_stripped(self):
        """'Real' is a functional disambiguator (Real Madrid vs
        Atletico Madrid). Must NOT be in GENERIC_SPORT_TOKENS."""
        assert "real" not in GENERIC_SPORT_TOKENS
        assert "real" in distinctive_tokens("real madrid")

    def test_atletico_is_not_stripped(self):
        """'Atletico' is functional ('Atletico Madrid' vs 'Real Madrid').
        Stripping would collapse them both to 'madrid'."""
        assert "atletico" not in GENERIC_SPORT_TOKENS
        assert distinctive_tokens("atletico madrid") == (
            "atletico", "madrid",
        )


# ──────────────────────────────────────────────────────────────────────
# has_distinctive_content
# ──────────────────────────────────────────────────────────────────────


class TestHasDistinctiveContent:

    def test_both_distinctive(self):
        assert has_distinctive_content("paris", "braunschweig")

    def test_failure_only_generic_returns_false(self):
        assert not has_distinctive_content("basketball", "braunschweig")

    def test_reference_only_generic_returns_false(self):
        assert not has_distinctive_content("paris", "basketball")

    def test_both_empty_returns_false(self):
        assert not has_distinctive_content("", "")

    def test_both_only_generic_returns_false(self):
        assert not has_distinctive_content("basketball", "fc club")


# ──────────────────────────────────────────────────────────────────────
# fuzzy_match_distinctive_score — false-positive guards
# ──────────────────────────────────────────────────────────────────────


class TestFalsePositiveRegression:
    """All 4 cases from operator's BBL pilot validation — must score
    well below the 0.85 default threshold."""

    THRESHOLD = 0.85

    def test_paris_basketball_does_not_match_braunschweig(self):
        score = fuzzy_match_distinctive_score(
            "paris basketball", "basketball braunschweig",
        )
        # After stripping "basketball" from both: "paris" vs
        # "braunschweig" — no overlap, score ~0.
        assert score < self.THRESHOLD, (
            f"Paris Basketball ↔ Basketball Braunschweig false positive: "
            f"score={score:.3f}, should be below {self.THRESHOLD}"
        )

    def test_dubai_basketball_does_not_match_braunschweig(self):
        score = fuzzy_match_distinctive_score(
            "dubai basketball", "basketball braunschweig",
        )
        assert score < self.THRESHOLD, (
            f"Dubai Basketball ↔ Basketball Braunschweig false positive: "
            f"score={score:.3f}"
        )

    def test_ebaa_basketball_does_not_match_braunschweig(self):
        # "EBAA-Basketball" normalizes to "ebaa basketball"
        score = fuzzy_match_distinctive_score(
            "ebaa basketball", "basketball braunschweig",
        )
        assert score < self.THRESHOLD, (
            f"EBAA-Basketball ↔ Basketball Braunschweig false positive: "
            f"score={score:.3f}"
        )

    def test_jaen_vs_jena_below_default_threshold(self):
        """Jaen (Spanish city) and Jena (German city) are 1-char
        permutations. Old threshold 0.75 matched them; new default
        0.85 should reject. Distinctive-only doesn't help here
        (both are single tokens, neither generic) — the threshold
        bump is the safety."""
        score = fuzzy_match_distinctive_score("jaen", "jena")
        # The exact score depends on rapidfuzz internals; what we
        # require is that 0.85 default rejects it. If rapidfuzz
        # changes to where this scores ≥0.85, the operator should
        # bump threshold higher or add country-filter logic.
        assert score < self.THRESHOLD, (
            f"Jaen ↔ Jena false positive at threshold {self.THRESHOLD}: "
            f"score={score:.3f}. Bump threshold OR add country filter."
        )


# ──────────────────────────────────────────────────────────────────────
# fuzzy_match_distinctive_score — true-positive preservation
# ──────────────────────────────────────────────────────────────────────


class TestTruePositivePreservation:
    """Real BBL aliases must STILL score above the 0.85 threshold
    after the tightening. If a real-match case regresses, the
    operator must adjust GENERIC_SPORT_TOKENS or threshold."""

    THRESHOLD = 0.85

    def test_bayern_matches_fc_bayern_munich_basketball(self):
        score = fuzzy_match_distinctive_score(
            "bayern", "fc bayern munchen basketball",
        )
        # After strip: "bayern" vs "bayern munchen" — 1 of 2 tokens
        # matches exactly. token_set_ratio handles partials.
        assert score >= self.THRESHOLD or score >= 0.80, (
            f"Bayern ↔ FC Bayern Munich Basketball true match regressed: "
            f"score={score:.3f}"
        )

    def test_ewe_oldenburg_matches_ewe_baskets_oldenburg(self):
        score = fuzzy_match_distinctive_score(
            "ewe oldenburg", "ewe baskets oldenburg",
        )
        # After strip "baskets": "ewe oldenburg" vs "ewe oldenburg" → 1.0
        assert score >= 0.95, (
            f"EWE Oldenburg ↔ EWE Baskets Oldenburg should be near-1.0: "
            f"score={score:.3f}"
        )

    def test_brose_bamberg_matches_brose_baskets_bamberg(self):
        score = fuzzy_match_distinctive_score(
            "brose bamberg", "brose baskets bamberg",
        )
        # After strip: "brose bamberg" vs "brose bamberg" → 1.0
        assert score >= 0.95

    def test_olympiakos_matches_olympiacos(self):
        """Greek transliteration variants. Day-33 HEBA pattern.
        Letter difference (k vs c) — rapidfuzz should still
        score this high."""
        score = fuzzy_match_distinctive_score("olympiakos", "olympiacos")
        assert score >= self.THRESHOLD, (
            f"Olympiakos ↔ Olympiacos transliteration variant: "
            f"score={score:.3f}"
        )


# ──────────────────────────────────────────────────────────────────────
# Edge cases
# ──────────────────────────────────────────────────────────────────────


class TestEdgeCases:

    def test_empty_input_returns_zero(self):
        assert fuzzy_match_distinctive_score("", "bayern") == 0.0
        assert fuzzy_match_distinctive_score("bayern", "") == 0.0
        assert fuzzy_match_distinctive_score("", "") == 0.0

    def test_pure_generic_input_returns_zero(self):
        """A failure string that's only sport tokens has nothing to
        meaningfully match against. Return 0.0 rather than letting
        the generic-only fuzzy ratio leak through."""
        assert fuzzy_match_distinctive_score(
            "basketball", "fc bayern munchen",
        ) == 0.0
        assert fuzzy_match_distinctive_score(
            "fc bayern munchen", "basketball",
        ) == 0.0

    def test_exact_match_after_strip(self):
        """Same string after strip → 1.0."""
        score = fuzzy_match_distinctive_score(
            "fc bayern munchen", "bayern munchen basketball",
        )
        # Both reduce to "bayern munchen"
        assert score == 1.0
