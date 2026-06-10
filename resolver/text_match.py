"""Distinctive-token fuzzy matching for alias harvesting.

Day-N+1 (BBL pilot validation) finding: bare `rapidfuzz.token_set_ratio`
over-matches on generic sport tokens. "Paris Basketball" vs
"Basketball Braunschweig" scored 0.75+ purely on the shared "basketball"
token. The collision audit (Component 1) caught the bogus matches only
because each happened to have its own legacy stub — junk aliases that
DIDN'T have an existing stub would have passed clean.

This module precision-tightens the matching primitive:

  1. Strip a curated set of generic sport tokens from both sides before
     computing the fuzzy score (`GENERIC_SPORT_TOKENS`).
  2. Require non-empty distinctive content on BOTH sides — a string
     whose only tokens are generics returns 0.0 confidence.
  3. Compute `token_set_ratio` over the distinctive-only substring.

The result: matches must succeed on REAL content (city, sponsor,
heritage moniker), not on shared sport descriptors.

## Generic token policy

Conservative: only tokens that are functionally MEANINGLESS as
distinguishers are stripped. Words like "Real" / "Atletico" / "Inter" /
"Athletic" / "Olympique" / "United" / "Sporting" are NOT stripped —
they ARE functional disambiguators ("Real Madrid" vs "Atletico Madrid"
vs "Real Sociedad"). Stripping them would over-collapse distinct teams.

The current set is biased toward Phase 2D.5-A's Basketball workstream
vocabulary. Other sports may need additions (operator extends per
finding).
"""
from __future__ import annotations

from rapidfuzz import fuzz


# Curated generic-token set. Conservative — see module docstring.
GENERIC_SPORT_TOKENS: frozenset[str] = frozenset({
    # Sport names (multiple languages)
    "basketball", "basket", "baskets",
    "football", "soccer", "baseball", "hockey",
    "futbol",  # Spanish "fútbol" → "futbol" post-NFD
    # Pure club-type prefixes (typically meaningless as distinguisher)
    "bc", "kk", "hkk", "sc", "fc",
    "asd",   # Italian "Associazione Sportiva Dilettantistica"
    # Generic descriptors
    "club", "team", "sports", "spor", "sport",
    "the",
    # Long-form sport prefixes in original language
    "pallacanestro", "basketbol",
    # NOTE: "as", "ac", "fc" sometimes part of distinctive identity
    # (AS Monaco, AC Milan) — included above because the city/name
    # carries the distinctive load. If a roster's teams disambiguate
    # via prefix alone, operator should remove the prefix from this
    # set and re-run.
})


def distinctive_tokens(normalized: str) -> tuple[str, ...]:
    """Return the tokens of `normalized` excluding `GENERIC_SPORT_TOKENS`.

    Input is expected to be already normalized via
    `resolver._normalize.normalize_name` (lowercase, accent-stripped,
    punctuation→space, whitespace-collapsed).

    Returns empty tuple if `normalized` is empty or contains only
    generic tokens.

    >>> distinctive_tokens("paris basketball")
    ('paris',)
    >>> distinctive_tokens("fc bayern munchen basketball")
    ('bayern', 'munchen')
    >>> distinctive_tokens("basketball")
    ()
    >>> distinctive_tokens("")
    ()
    """
    if not normalized:
        return tuple()
    return tuple(
        t for t in normalized.split()
        if t and t not in GENERIC_SPORT_TOKENS
    )


def has_distinctive_content(
    failure_normalized: str,
    reference_normalized: str,
) -> bool:
    """True iff both strings have at least one non-generic token.

    Matching with no distinctive content on either side would compare
    nothing meaningful — return False to short-circuit.
    """
    return (
        bool(distinctive_tokens(failure_normalized))
        and bool(distinctive_tokens(reference_normalized))
    )


def fuzzy_match_distinctive_score(
    failure_normalized: str,
    reference_normalized: str,
) -> float:
    """Compute rapidfuzz `token_set_ratio` over the distinctive-only
    portion of both strings.

    Returns 0.0 if either side has no distinctive content (matching on
    generics would be a false positive). Otherwise returns the
    rapidfuzz score scaled to 0.0-1.0.

    Day-N+1 BBL validation finding: this guards against the
    "Paris Basketball" ↔ "Basketball Braunschweig" false-positive
    pattern. Bare-token fuzzy match scored 0.75+ on those purely from
    the shared "basketball" token; with distinctive-only, the score
    drops to ~0 because "paris" ≠ "braunschweig".
    """
    failure_dist = distinctive_tokens(failure_normalized)
    reference_dist = distinctive_tokens(reference_normalized)
    if not failure_dist or not reference_dist:
        return 0.0
    failure_str = " ".join(failure_dist)
    reference_str = " ".join(reference_dist)
    return fuzz.token_set_ratio(failure_str, reference_str) / 100.0
