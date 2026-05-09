"""Structural name normalization for alias-tier matching.

Phase 2C.2 per PHASE_2C_DESIGN.md, signed-off in PR #90.

Two paths, sport-driven:

  Path 1 — Personal names (sports in INDIVIDUAL_SPORT_CODES):
      surname-anchored decomposition. Strips parentheticals,
      detects "Last F." / "First Last" / multi-token patterns.

  Path 2 — Team names (everything else):
      whole-string token bag. No anchor — the scorer's threshold
      (0.92) carries the safety margin.

Both paths are pure functions over (string, sport_code). No DB, no
matcher state. The scorer (alias_tier.scorer) consumes the result.

Normalization steps shared by both paths:
  1. Strip parenthetical content (country codes, qualifiers like
     "(Q)", "(W)", "(JR)") — we don't want these as tokens.
  2. NFD-decompose, drop combining marks (accent strip).
  3. Lowercase.
  4. Strip punctuation, collapse whitespace.
  5. Tokenize on whitespace.
  6. Drop standalone suffix tokens ("jr", "sr", "ii", "iii", "iv").

Path-specific structural detection follows.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


# Per Phase 2C design D.1: sports where the "team" is one human.
# Scorer chooses Path 1 (surname-anchored) for these; everything
# else goes Path 2 (team-name token bag).
#
# Tests assert each entry exists in sp.sports.code (cheap typo guard).
INDIVIDUAL_SPORT_CODES: frozenset[str] = frozenset({
    "tennis",
    "mma",
    "boxing",
    "golf",
    "snooker",
    "darts",
})


# Suffix tokens stripped from BOTH paths. These appear after the
# normalization pipeline as standalone tokens (the leading punctuation
# was already removed).
_DROP_TOKENS: frozenset[str] = frozenset({
    "jr", "sr", "ii", "iii", "iv",
})


_PARENTHETICAL_RE = re.compile(r"\([^)]*\)")
_PUNCT_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WS_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class StructuredName:
    """Output of structurally_normalize() — the matching key the
    scorer operates on.

    For personal-name paths (is_personal=True):
      - `surname` is the anchor. Must match exactly across both
        sides for a candidate to be considered.
      - `other_tokens` are initials / given names / qualifiers,
        scored via token-set ratio.

    For team-name paths (is_personal=False):
      - `surname` is the empty string. No anchor token.
      - `other_tokens` is the whole-string token bag scored via
        token-set ratio against the candidate team's tokens.

    `detection_path` records which structural rule fired — useful
    for audit and for the dry-run report (2C.2.5).
    """
    raw: str
    detection_path: str           # see detection rules below
    surname: str                  # '' for team paths or empty input
    other_tokens: tuple[str, ...]
    is_personal: bool


def structurally_normalize(s: str | None, *, sport_code: str | None) -> StructuredName | None:
    """Decompose a name into surname-anchored or team-bag structure.

    Returns None when the input is empty / whitespace-only / produces
    no tokens after normalization.

    `sport_code` is the canonical sport identifier (e.g., "tennis",
    "soccer"). Case-insensitive. Determines path selection.
    """
    if not s:
        return None

    # Steps 1-5: shared normalization pipeline.
    no_paren = _PARENTHETICAL_RE.sub(" ", s)
    decomposed = unicodedata.normalize("NFD", no_paren)
    no_accent = "".join(c for c in decomposed if not unicodedata.combining(c))
    lowered = no_accent.lower()
    no_punct = _PUNCT_RE.sub(" ", lowered)
    collapsed = _WS_RE.sub(" ", no_punct).strip()
    if not collapsed:
        return None

    # Step 6: drop suffix tokens.
    tokens = [t for t in collapsed.split(" ") if t and t not in _DROP_TOKENS]
    if not tokens:
        return None

    sport_lower = (sport_code or "").lower()
    is_personal = sport_lower in INDIVIDUAL_SPORT_CODES

    if is_personal:
        return _structurally_normalize_personal(s, tokens)
    return _structurally_normalize_team(s, tokens)


def _structurally_normalize_personal(raw: str, tokens: list[str]) -> StructuredName:
    """Path 1 — surname-anchored decomposition.

    Detection rules (in order):

      personal_initial    — exactly 2 tokens AND second token is
                            1-2 chars. e.g., "kecmanovic m"
                            → surname=kecmanovic, others=("m",)
      personal_two_token  — exactly 2 tokens, both > 2 chars.
                            e.g., "miomir kecmanovic"
                            → surname=kecmanovic (last token),
                              others=("miomir",)
      personal_multi      — 3+ tokens. Per design D.A.2:
                            last token is surname. e.g.,
                            "carlos alcaraz garfia"
                            → surname=garfia, others=("carlos", "alcaraz")
      personal_single     — 1 token. e.g. "djokovic"
                            → surname=djokovic, others=()
                            (Single-token names match anchor-only.)
    """
    if len(tokens) == 1:
        return StructuredName(
            raw=raw, detection_path="personal_single",
            surname=tokens[0], other_tokens=(),
            is_personal=True,
        )
    if len(tokens) == 2:
        # "kecmanovic m" — second token is initial-shaped.
        if len(tokens[1]) <= 2:
            return StructuredName(
                raw=raw, detection_path="personal_initial",
                surname=tokens[0], other_tokens=(tokens[1],),
                is_personal=True,
            )
        # "miomir kecmanovic" — last token is surname.
        return StructuredName(
            raw=raw, detection_path="personal_two_token",
            surname=tokens[-1], other_tokens=(tokens[0],),
            is_personal=True,
        )
    # 3+ tokens — last is surname.
    return StructuredName(
        raw=raw, detection_path="personal_multi",
        surname=tokens[-1], other_tokens=tuple(tokens[:-1]),
        is_personal=True,
    )


def _structurally_normalize_team(raw: str, tokens: list[str]) -> StructuredName:
    """Path 2 — team-name token bag.

    No anchor. The scorer's higher threshold (0.92 vs 0.85 for
    personal) carries the safety margin against cross-team
    false positives.

    Detection rules:

      team_simple    — 1 token. e.g. "psg" → others=("psg",)
      team_qualified — 2+ tokens. e.g. "real madrid"
                       → others=("real", "madrid")
                       Or "sao paulo fc" → others=("sao", "paulo", "fc")
    """
    path = "team_simple" if len(tokens) == 1 else "team_qualified"
    return StructuredName(
        raw=raw, detection_path=path,
        surname="", other_tokens=tuple(tokens),
        is_personal=False,
    )
