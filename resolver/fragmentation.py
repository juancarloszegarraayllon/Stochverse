"""Canonical fragmentation detection (Day-37 BBL pilot finding).

Phase 2A.5's legacy `public.entities` accumulator was populated from
live-score feeds (ESPN / SofaScore / SportsDB) as games appeared. For
many real clubs it captured BOTH a bare city-stub ("Oldenburg") AND
the full-name club ("EWE Baskets Oldenburg") as TWO SEPARATE sp.teams
rows / team_ids. The BBL pilot Day-37 production analysis surfaced 7
such pairs in BBL alone; the pattern repeats across leagues because
the accumulator's behavior was source-agnostic.

This module detects those fragmentation pairs and classifies them per
the Day-37 LOCKED resolution rule:

  - **ALIAS-LINK** (auto-proposable):
    One side has ZERO fixtures (dormant phantom). Canonical winner is
    the side WITH fixture history (Option A — fixture-history wins,
    F1 production-anchor discipline). The other side's canonical form
    becomes an alias on the live team_id; the dormant duplicate is
    FLAGGED for operator review but NOT deleted in automation.

  - **MERGE-REQUIRED** (NEVER auto-applied):
    Both sides have fixtures. Cannot decide canonical winner without
    FK-cascade merge machinery (Tennis-dedup precedent: sp.team_aliases
    copy, sp.fixtures home/away UPDATE, sp.review_queue JSONB rewrite,
    sp.dedup_audit rollback). Automation flags only. Operator runs the
    merge as a separate task.

## Token-subset detection rule

Pair candidate iff:
  - Both sides have non-empty distinctive tokens (per
    `resolver.text_match.distinctive_tokens`)
  - The shorter side's distinctive tokens are a SUBSET of the longer
    side's (strict subset, after generic-token strip)
  - The shared tokens carry real content (non-generic)
  - **Distinct-entity guard** (Day-N+1 / Day-N+1+1 findings): NEITHER
    side carries a distinct-entity marker that the other lacks. Two
    flavors of marker, same mechanism:
      * Reserve/junior (Day-N+1 France LNB): U21..U24, Espoirs,
        Reserve(s), Junior(s)/Jr, trailing standalone II/B.
      * Women's-team (Day-N+1+1 FIBA Europe Cup): Women(s)/Woman/
        Ladies, Femenino/Femenina, Femminile, Féminin(es), Damen,
        Kobiet/Kobiety, trailing standalone W.
    Senior-vs-reserve and men's-vs-women's are distinct entities with
    their own fixtures and competitions; pairing them would corrupt
    both teams' history. See `_has_distinct_entity_marker`.

Examples that match:
  - "Oldenburg" {oldenburg} ⊆ "EWE Baskets Oldenburg" {ewe, oldenburg}
  - "Real Madrid" {real, madrid} ⊆ "Real Madrid Baloncesto"
    {real, madrid, baloncesto}
  - "Hamburg" {hamburg} ⊆ "Hamburg Towers" {hamburg, towers}
  - "Gravelines-Dunkerque" vs "BCM Gravelines-Dunkerque" (real LNB
    fragment, no distinct-entity markers either side)

Examples that do NOT match (reserve-team guard):
  - "Monaco" vs "Monaco U21" (senior vs reserve squad)
  - "Monaco" vs "Monaco Espoirs U21" (senior vs reserve)
  - "Real Madrid" vs "Real Madrid B" (senior vs reserve)
  - "Nanterre" vs "Nanterre 92 Espoirs" (senior vs reserve)

Examples that do NOT match (women's-team guard):
  - "Basket Zaragoza" vs "Casademont Zaragoza Femenino"
    (men's vs women's squad)
  - "Basket Zaragoza" vs "Zaragoza W" (trailing standalone W)
  - "Bayern München" vs "Bayern München Damen" (German women's)

Examples that do NOT match (other rules):
  - "Real Madrid" {real, madrid} vs "Real Sociedad" {real, sociedad}
    — neither is subset of the other
  - "Bayern" vs "FC Barcelona" — no shared distinctive token

The rule is conservative — it catches strict subset relationships
(true fragmentation shape) without overmatching distinct clubs that
share only generic disambiguators or senior-vs-reserve splits.

## Architecture

Pure / impure split (same pattern as `resolver.collision_audit`):

  - `find_fragmentation_candidates_pure(anchor, others) -> list[Pair]`
    — pure data function, fully unit-testable
  - `classify_fragmentation_pair(pair, fixture_counts) -> Verdict`
    — pure: takes pair + already-fetched fixture counts, returns the
    ALIAS-LINK / MERGE-REQUIRED verdict + canonical winner

Caller (the batch orchestrator) handles DB I/O — bulk-loads sp.teams,
batch-queries fixture counts.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Sequence

from resolver.text_match import distinctive_tokens


# ──────────────────────────────────────────────────────────────────────
# Distinct-entity marker detection
# ──────────────────────────────────────────────────────────────────────
#
# Two finding-driven additions:
#   - Day-N+1 (France LNB): reserve / junior squads (U21, Espoirs,
#     trailing B / II, Junior / Jr) are distinct entities from the
#     senior club — must NOT pair with the senior squad.
#   - Day-N+1+1 (FIBA Europe Cup, Basket Zaragoza ↔ Casademont
#     Zaragoza Femenino): women's teams are distinct entities from
#     the men's club — same guard.
#
# Both are handled by the same pairing logic: if exactly one side
# carries a distinct-entity marker, they're not a fragmentation
# pair. _has_distinct_entity_marker is the aggregate the pairing
# logic uses; _has_reserve_marker and _has_gender_marker remain
# separately testable for clarity.


# --- Reserve / junior markers (Day-N+1 France LNB) -----------------

# Age-group markers: U15..U24 with optional separator (hyphen or
# whitespace). Captures "U21", "U-21", "U 21" (which is what the
# normalizer produces from "U-21" via the punct → space rule).
_AGE_GROUP_RE = re.compile(r"(?i)\bU[-\s]?(?:1[5-9]|2[0-4])\b")

# Named reserve / junior markers. Word-bounded so embedded substrings
# don't false-positive (e.g. "Espoirs" hits, "Réespoir" does not).
_NAMED_MARKER_RE = re.compile(
    r"(?i)\b(?:espoir|espoirs|reserve|reserves|junior|juniors|jr\.?)\b"
)

# Trailing standalone "II" or "B". The trailing-only rule is critical:
# we must NOT strip "B" from "BC" (a generic club prefix) or "II" from
# "III" / other sequences. Anchored to end of string with optional
# trailing whitespace; word-boundary before guards against matching
# inside other words.
#
#   "Real Madrid B"   → matches (trailing standalone B)
#   "Barcelona II"    → matches (trailing standalone II)
#   "BC Vienna"       → no match (B is part of "BC", not trailing)
#   "Real B Madrid"   → no match (B is standalone but not trailing)
_TRAILING_B_OR_II_RE = re.compile(r"(?i)\b(?:II|B)\s*$")


# --- Gender / women's markers (Day-N+1+1 FIBA Europe Cup) ---------

# Named women's-team markers. We apply these to an accent-stripped
# lowercase form of canonical_name so French "féminin", Italian
# "femminile", etc. all match regardless of source-language accents.
_GENDER_NAMED_RE = re.compile(
    r"\b(?:"
    r"women|womens|woman|ladies|"
    r"femenino|femenina|"        # Spanish
    r"femminile|"                # Italian
    r"feminin|feminins|feminines|"  # French (post NFD)
    r"damen|"                    # German
    r"kobiet|kobiety"            # Polish
    r")\b"
)

# Trailing standalone "W" — analogous to B/II trailing rule. Must NOT
# strip "W" from inside "BW" / "BWB" / other words.
#
#   "Zaragoza W"  → matches (trailing standalone W)
#   "BW"          → no match (W not preceded by word boundary)
#   "BWB"         → no match (W not trailing)
#   "Wroclaw"     → no match (ends in w but no boundary before)
_TRAILING_W_RE = re.compile(r"(?i)\bW\s*$")


def _strip_accents_lower(s: str) -> str:
    """NFD-strip + lowercase. Used by `_has_gender_marker` so source-
    language accents (féminin, féminines) match against ASCII-form
    regex patterns."""
    decomposed = unicodedata.normalize("NFD", s)
    no_combining = "".join(
        c for c in decomposed if not unicodedata.combining(c)
    )
    return no_combining.lower()


def _has_reserve_marker(canonical_name: str) -> bool:
    """True if `canonical_name` contains any reserve / junior /
    secondary-squad marker per the Day-N+1 France LNB finding.

    Markers (case-insensitive, word-boundary matched):
      - U15..U24 age groups, with optional hyphen or space
        ("U21", "U-21", "U 21")
      - Espoir / Espoirs (French reserve-squad label)
      - Reserve / Reserves
      - Junior / Juniors / Jr / Jr.
      - Trailing standalone "II" or "B" (caution: NOT embedded "B"
        inside "BC" / other words)

    Returns False on empty input.
    """
    if not canonical_name:
        return False
    s = canonical_name.strip()
    if not s:
        return False
    if _AGE_GROUP_RE.search(s):
        return True
    if _NAMED_MARKER_RE.search(s):
        return True
    if _TRAILING_B_OR_II_RE.search(s):
        return True
    return False


def _has_gender_marker(canonical_name: str) -> bool:
    """True if `canonical_name` carries a women's-team / gender
    marker per the Day-N+1+1 FIBA Europe Cup finding.

    Markers (case-insensitive, accent-insensitive, word-boundary):
      - Women / Womens / Woman / Ladies (English)
      - Femenino / Femenina (Spanish)
      - Femminile (Italian)
      - Féminin / Féminins / Féminines (French; accent stripped)
      - Damen (German)
      - Kobiet / Kobiety (Polish)
      - Trailing standalone "W" (caution: NOT embedded "W" inside
        "BW" / "BWB" / other words)

    Returns False on empty input.
    """
    if not canonical_name:
        return False
    s = canonical_name.strip()
    if not s:
        return False
    # Trailing W check on the original case — case-insensitive regex
    # handles upper/lower internally.
    if _TRAILING_W_RE.search(s):
        return True
    # Accent-strip + lowercase for the named-marker check so French
    # "Féminin", Italian "Femminile" etc. all match.
    s_stripped = _strip_accents_lower(s)
    if _GENDER_NAMED_RE.search(s_stripped):
        return True
    return False


def _has_distinct_entity_marker(canonical_name: str) -> bool:
    """Aggregate of `_has_reserve_marker` + `_has_gender_marker`.

    Used by the fragmentation pairing logic: if EITHER side has a
    distinct-entity marker the other lacks, they're not a
    fragmentation pair — they're senior-vs-reserve OR men's-vs-
    women's, both being distinct entities with their own fixture
    histories.
    """
    return (
        _has_reserve_marker(canonical_name)
        or _has_gender_marker(canonical_name)
    )


# ──────────────────────────────────────────────────────────────────────
# Data shapes
# ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SPTeamLite:
    """Minimal sp.teams fields needed for fragmentation detection.

    `team_id` as string UUID for portability. `normalized_name` is the
    output of `resolver._normalize.normalize_name` applied to
    `canonical_name`; caller computes once before passing in.
    """
    team_id: str
    canonical_name: str
    normalized_name: str
    country_code: str | None
    created_at: str  # ISO 8601 — for operator spot-check display


@dataclass(frozen=True)
class FragmentationPair:
    """A pair of sp.teams rows that look like fragmentation."""
    anchor: SPTeamLite
    partner: SPTeamLite
    # Which side has the broader name (more distinctive tokens). The
    # narrower side is the subset.
    broader_team_id: str
    narrower_team_id: str
    shared_distinctive_tokens: tuple[str, ...]


@dataclass(frozen=True)
class FragmentationVerdict:
    """Classification of a fragmentation pair per the Day-37 rule."""
    pair: FragmentationPair
    anchor_fixture_count: int
    partner_fixture_count: int
    classification: str  # "ALIAS-LINK" | "MERGE-REQUIRED"
    canonical_winner_team_id: str | None  # None for MERGE-REQUIRED
    dormant_phantom_team_id: str | None   # None for MERGE-REQUIRED
    proposed_alias_form: str | None       # the canonical_name of the
                                           # dormant phantom — becomes
                                           # an alias on the live stub
    notes: str = ""


# ──────────────────────────────────────────────────────────────────────
# Pure detection
# ──────────────────────────────────────────────────────────────────────


def find_fragmentation_candidates_pure(
    anchor: SPTeamLite,
    others: Sequence[SPTeamLite],
) -> list[FragmentationPair]:
    """For a single `anchor` team, scan `others` for partners whose
    distinctive tokens form a strict subset/superset relationship.

    Returns at most one pair per `other` team_id; an `other` whose
    distinctive tokens are identical to anchor's is NOT a fragmentation
    pair (it's a duplicate by another shape — surface via the existing
    collision audit instead).

    Empty distinctive-tokens on either side → not a candidate (no
    real content to fragment on).

    Distinct-entity guard (Day-N+1 / Day-N+1+1): if exactly one side
    carries a distinct-entity marker (reserve: U21/Espoirs/B/II/...;
    women's: W/Femenino/Damen/...), they're a senior-vs-reserve OR
    men's-vs-women's split — distinct entities, NOT a fragmentation
    pair. Pairs where BOTH sides have markers OR NEITHER does still
    proceed (two reserves, two women's teams, or two senior variants
    can legitimately be fragments of each other).
    """
    anchor_tokens = set(distinctive_tokens(anchor.normalized_name))
    if not anchor_tokens:
        return []
    anchor_has_marker = _has_distinct_entity_marker(anchor.canonical_name)

    pairs: list[FragmentationPair] = []
    seen_partner_ids: set[str] = set()

    for other in others:
        if other.team_id == anchor.team_id:
            continue
        if other.team_id in seen_partner_ids:
            continue
        other_tokens = set(distinctive_tokens(other.normalized_name))
        if not other_tokens:
            continue
        # Strict subset: one is a proper subset of the other.
        if anchor_tokens == other_tokens:
            # Same distinctive content — not fragmentation in the
            # token-subset shape; defer to collision audit.
            continue
        if anchor_tokens.issubset(other_tokens):
            broader_id = other.team_id
            narrower_id = anchor.team_id
            shared = tuple(sorted(anchor_tokens))
        elif other_tokens.issubset(anchor_tokens):
            broader_id = anchor.team_id
            narrower_id = other.team_id
            shared = tuple(sorted(other_tokens))
        else:
            continue
        # Distinct-entity guard (Day-N+1 France LNB reserve teams +
        # Day-N+1+1 FIBA Europe Cup women's teams). Same mechanism.
        other_has_marker = _has_distinct_entity_marker(
            other.canonical_name
        )
        if anchor_has_marker != other_has_marker:
            # Exactly one side carries a distinct-entity marker
            # (reserve/junior or women's). Despite the token-subset
            # match they're distinct clubs/squads. Skip pair.
            continue
        pairs.append(FragmentationPair(
            anchor=anchor, partner=other,
            broader_team_id=broader_id,
            narrower_team_id=narrower_id,
            shared_distinctive_tokens=shared,
        ))
        seen_partner_ids.add(other.team_id)
    return pairs


def find_all_fragmentation_pairs_pure(
    teams: Sequence[SPTeamLite],
) -> list[FragmentationPair]:
    """Scan an entire team list for fragmentation pairs.

    De-duplicates: each (team_id_a, team_id_b) pair returned once
    regardless of which side is anchor.
    """
    out: list[FragmentationPair] = []
    seen_pairs: set[frozenset] = set()
    for i, anchor in enumerate(teams):
        for pair in find_fragmentation_candidates_pure(
            anchor=anchor, others=teams[i + 1:],
        ):
            key = frozenset({pair.anchor.team_id, pair.partner.team_id})
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            out.append(pair)
    return out


# ──────────────────────────────────────────────────────────────────────
# Pure classification (verdict given fixture counts)
# ──────────────────────────────────────────────────────────────────────


def classify_fragmentation_pair_pure(
    pair: FragmentationPair,
    anchor_fixture_count: int,
    partner_fixture_count: int,
) -> FragmentationVerdict:
    """Classify the pair per the Day-37 LOCKED rule.

    Inputs:
      - `pair`: detected fragmentation pair
      - `anchor_fixture_count`, `partner_fixture_count`: integer fixture
        counts. Caller fetches via SQL batch before invoking.

    Returns a `FragmentationVerdict`. Caller emits to report — does NOT
    apply.

    Verdict rules:
      - One side has zero fixtures, the other > 0 → ALIAS-LINK.
        Canonical winner = the side with fixtures (Option A:
        fixture-history wins). Dormant phantom = the zero-fixture side.
        Proposed alias = dormant phantom's canonical_name.
      - Both have > 0 fixtures → MERGE-REQUIRED. No canonical winner
        proposed; operator runs FK-cascade merge as separate task.
      - Both have zero fixtures → MERGE-REQUIRED (degenerate; both
        dormant — operator decides which to keep). Conservative
        default: don't auto-propose either as canonical.
    """
    anchor_id = pair.anchor.team_id
    partner_id = pair.partner.team_id

    if anchor_fixture_count > 0 and partner_fixture_count == 0:
        return FragmentationVerdict(
            pair=pair,
            anchor_fixture_count=anchor_fixture_count,
            partner_fixture_count=partner_fixture_count,
            classification="ALIAS-LINK",
            canonical_winner_team_id=anchor_id,
            dormant_phantom_team_id=partner_id,
            proposed_alias_form=pair.partner.canonical_name,
            notes=(
                f"Anchor has {anchor_fixture_count} fixtures, partner "
                "has 0 — partner is dormant phantom. Propose partner's "
                "canonical as alias on anchor."
            ),
        )
    if partner_fixture_count > 0 and anchor_fixture_count == 0:
        return FragmentationVerdict(
            pair=pair,
            anchor_fixture_count=anchor_fixture_count,
            partner_fixture_count=partner_fixture_count,
            classification="ALIAS-LINK",
            canonical_winner_team_id=partner_id,
            dormant_phantom_team_id=anchor_id,
            proposed_alias_form=pair.anchor.canonical_name,
            notes=(
                f"Partner has {partner_fixture_count} fixtures, anchor "
                "has 0 — anchor is dormant phantom. Propose anchor's "
                "canonical as alias on partner."
            ),
        )
    if anchor_fixture_count == 0 and partner_fixture_count == 0:
        return FragmentationVerdict(
            pair=pair,
            anchor_fixture_count=0,
            partner_fixture_count=0,
            classification="MERGE-REQUIRED",
            canonical_winner_team_id=None,
            dormant_phantom_team_id=None,
            proposed_alias_form=None,
            notes=(
                "BOTH SIDES have zero fixtures — degenerate dormant "
                "pair. Operator decides retention strategy."
            ),
        )
    # Both > 0
    return FragmentationVerdict(
        pair=pair,
        anchor_fixture_count=anchor_fixture_count,
        partner_fixture_count=partner_fixture_count,
        classification="MERGE-REQUIRED",
        canonical_winner_team_id=None,
        dormant_phantom_team_id=None,
        proposed_alias_form=None,
        notes=(
            f"Both sides have fixtures (anchor {anchor_fixture_count}, "
            f"partner {partner_fixture_count}) — Tennis-dedup-shape "
            "FK-cascade merge required. Operator-run, not automation."
        ),
    )
