"""Phase 2D.1 — structural initial expansion + compound surname fallback.

Two pure-Python primitives for the fuzzy tier:

  initials_compatible(provider_tokens, candidate_tokens) -> bool
      Per design Q A. Returns True iff every short token (len 1-2)
      on either side is the prefix of some long token (len > 2) on
      the other side.

      "miomir" / "m"           → True  ("miomir".startswith("m"))
      "daniil" / "d"           → True
      "john" / "m"             → False
      "miomir andrey" / "m a"  → True
      "miomir andrey" / "m b"  → False ("b" doesn't prefix anything)

  candidate_surname_interpretations(tokens) -> tuple[str, ...]
      Per design Q E.3 — multi-interpretation surname index. Returns
      the SAME tokens decomposed into multiple plausible surname
      assignments so a single candidate row in CandidateIndex can be
      reachable under several surname keys.

      ["roberto", "bautista", "agut"]:
        - "agut"            (default — last token)
        - "bautista agut"   (compound — last 2 tokens)
        - "bautista"        (middle-as-surname — Spanish/Portuguese
                             compound name convention)

      Three interpretations max per design A.1 (3-retry ceiling).

Both functions are stateless. No DB. No matcher. The fuzzy-tier
matcher (2D.2) and the multi-interpretation candidate index
(updated in 2C `CandidateIndex` for E.3) consume them.

Lesson from PR #87 carried forward: the matcher in 2D.2 ships
with real call-path tests, not just static-source guards. These
2D.1 primitives are also tested at the function-call level.
"""
from __future__ import annotations


_SHORT_TOKEN_MAX_LEN = 2  # token is a "short" / initial when len ≤ 2
_COMPOUND_RETRY_DEPTH = 3  # design A.1: stop at 3 retries


# ── initials_compatible ────────────────────────────────────────


def initials_compatible(
    provider_tokens: tuple[str, ...] | list[str],
    candidate_tokens: tuple[str, ...] | list[str],
) -> bool:
    """Return True iff every short token (len ≤ 2) on either side is
    the prefix of some long token (len > 2) on the other side.

    Symmetric — works equally well when provider has full names and
    candidate has initials, OR provider has initials and candidate
    has full names. Empty tokens on EITHER or BOTH sides yield
    True (no constraint to violate).

    Per Phase 2D design rev1 §A. The +0.30 confidence contribution
    in the matcher is binary on this function's output.
    """
    p_short, p_long = _split_short_long(provider_tokens)
    c_short, c_long = _split_short_long(candidate_tokens)

    # Each provider-side short token must prefix some candidate-side
    # long token. Symmetric for candidate-side short tokens.
    for s in p_short:
        if not any(_long.startswith(s) for _long in c_long):
            return False
    for s in c_short:
        if not any(_long.startswith(s) for _long in p_long):
            return False
    return True


def _split_short_long(tokens) -> tuple[list[str], list[str]]:
    """Split a token sequence into (short, long) lists by the
    _SHORT_TOKEN_MAX_LEN threshold."""
    short: list[str] = []
    long: list[str] = []
    for t in tokens:
        if not t:
            continue
        if len(t) <= _SHORT_TOKEN_MAX_LEN:
            short.append(t)
        else:
            long.append(t)
    return short, long


# ── candidate_surname_interpretations ──────────────────────────


def candidate_surname_interpretations(tokens: list[str]) -> tuple[str, ...]:
    """Return a tuple of plausible surname assignments for a
    candidate's structurally-normalized name tokens.

    Per Phase 2D design rev1 §E.3 — handles the "Roberto Bautista
    Agut" case where the provider's "Bautista" must reach the
    candidate even though the default last-token interpretation is
    "agut". Three interpretations max per design A.1 (3-retry
    ceiling).

    Returns deduplicated, in priority order:
      1. last token                              (default)
      2. last-two-tokens joined                  (compound)
      3. token[-2] alone                          (middle-as-surname,
                                                   Spanish/Portuguese
                                                   compound convention)

    Edge cases:
      0 tokens → ()
      1 token  → (token,)
      2 tokens → (last, "first last")  (skip middle-as-surname for
                                        2-token; would equal default)

    Used at index-build time (`CandidateIndex.refresh`) so each
    candidate row appears under each plausible surname key in the
    `_by_sport_surname` dict.
    """
    if not tokens:
        return ()
    if len(tokens) == 1:
        return (tokens[0],)
    if len(tokens) == 2:
        # 2-token: default = tokens[-1]; compound = "tokens[0] tokens[1]"
        # No middle-as-surname distinct from default for 2 tokens.
        result = (tokens[-1], " ".join(tokens))
        return _dedupe_preserve_order(result)

    # 3+ tokens — all three interpretations are distinct.
    default = tokens[-1]
    compound = " ".join(tokens[-2:])
    middle = tokens[-2]
    return _dedupe_preserve_order((default, compound, middle))


def _dedupe_preserve_order(items: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return tuple(out)
