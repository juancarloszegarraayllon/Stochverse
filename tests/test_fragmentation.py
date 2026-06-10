"""Unit tests for canonical fragmentation detection + classification.

Covers:
  - Detection: token-subset relationship over distinctive tokens
  - Verdict: all 4 shapes per Day-37 LOCKED rule
      ALIAS-LINK (anchor has fixtures, partner zero)
      ALIAS-LINK (partner has fixtures, anchor zero)
      MERGE-REQUIRED (both have fixtures)
      MERGE-REQUIRED (both zero — degenerate)
  - Regression on the 7 BBL fragmentation pairs the operator
    confirmed Day-37: Oldenburg / EWE Baskets Oldenburg,
    Ludwigsburg / MHP Riesen, Hamburg / Hamburg Towers, etc.
  - Non-fragmentation guard: Real Madrid vs Real Sociedad must NOT
    pair (shared "real" but not subset)
  - Identical-distinctive guard: two teams with the exact same
    distinctive tokens are NOT a fragmentation pair (defer to
    collision audit)
"""
from __future__ import annotations

from resolver.fragmentation import (
    FragmentationPair,
    SPTeamLite,
    classify_fragmentation_pair_pure,
    find_all_fragmentation_pairs_pure,
    find_fragmentation_candidates_pure,
)


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def team(team_id: str, canonical: str, normalized: str,
         country: str = "DEU") -> SPTeamLite:
    return SPTeamLite(
        team_id=team_id,
        canonical_name=canonical,
        normalized_name=normalized,
        country_code=country,
        created_at="2026-05-08T00:00:00Z",
    )


# ──────────────────────────────────────────────────────────────────────
# Detection — positive cases
# ──────────────────────────────────────────────────────────────────────


class TestDetectionPositive:
    """Pairs that SHOULD be detected as fragmentation."""

    def test_city_stub_vs_full_name_oldenburg(self):
        anchor = team("oldenburg-uuid", "Oldenburg", "oldenburg")
        partner = team("ewe-baskets-uuid",
                       "EWE Baskets Oldenburg", "ewe baskets oldenburg")
        pairs = find_fragmentation_candidates_pure(
            anchor=anchor, others=[partner],
        )
        assert len(pairs) == 1
        assert pairs[0].anchor.team_id == "oldenburg-uuid"
        assert pairs[0].partner.team_id == "ewe-baskets-uuid"
        assert pairs[0].broader_team_id == "ewe-baskets-uuid"
        assert pairs[0].narrower_team_id == "oldenburg-uuid"
        assert pairs[0].shared_distinctive_tokens == ("oldenburg",)

    def test_real_madrid_vs_real_madrid_baloncesto(self):
        """Soccer/basketball split — real fragmentation surface."""
        anchor = team("real-madrid-uuid",
                      "Real Madrid", "real madrid", "ESP")
        partner = team("real-madrid-baloncesto-uuid",
                       "Real Madrid Baloncesto",
                       "real madrid baloncesto", "ESP")
        pairs = find_fragmentation_candidates_pure(
            anchor=anchor, others=[partner],
        )
        assert len(pairs) == 1
        assert pairs[0].broader_team_id == "real-madrid-baloncesto-uuid"
        assert pairs[0].shared_distinctive_tokens == ("madrid", "real")

    def test_hamburg_vs_hamburg_towers_bbl_regression(self):
        anchor = team("hamburg-uuid", "Hamburg", "hamburg")
        partner = team("hamburg-towers-uuid",
                       "Hamburg Towers", "hamburg towers")
        pairs = find_fragmentation_candidates_pure(
            anchor=anchor, others=[partner],
        )
        assert len(pairs) == 1
        assert pairs[0].shared_distinctive_tokens == ("hamburg",)

    def test_anchor_can_be_broader_side(self):
        """The anchor side may be the broader name too — orientation
        independent."""
        anchor = team("ewe-baskets-uuid",
                      "EWE Baskets Oldenburg", "ewe baskets oldenburg")
        partner = team("oldenburg-uuid", "Oldenburg", "oldenburg")
        pairs = find_fragmentation_candidates_pure(
            anchor=anchor, others=[partner],
        )
        assert len(pairs) == 1
        assert pairs[0].broader_team_id == "ewe-baskets-uuid"
        assert pairs[0].narrower_team_id == "oldenburg-uuid"


# ──────────────────────────────────────────────────────────────────────
# Detection — negative cases
# ──────────────────────────────────────────────────────────────────────


class TestDetectionNegative:
    """Pairs that must NOT be flagged as fragmentation."""

    def test_real_madrid_vs_real_sociedad_no_subset(self):
        """Shared generic-ish 'real' but distinctive tokens
        {real, madrid} vs {real, sociedad} — neither subset."""
        anchor = team("real-madrid-uuid",
                      "Real Madrid", "real madrid", "ESP")
        partner = team("real-sociedad-uuid",
                       "Real Sociedad", "real sociedad", "ESP")
        pairs = find_fragmentation_candidates_pure(
            anchor=anchor, others=[partner],
        )
        assert pairs == []

    def test_identical_distinctive_tokens_not_pair(self):
        """Two teams with identical distinctive content are duplicates
        of a different shape — defer to collision audit, NOT
        fragmentation."""
        anchor = team("bayern-a-uuid",
                      "Bayern München", "bayern munchen", "DEU")
        partner = team("bayern-b-uuid",
                       "FC Bayern München", "fc bayern munchen", "DEU")
        # After distinctive strip, both → {bayern, munchen}. Equal,
        # not subset.
        pairs = find_fragmentation_candidates_pure(
            anchor=anchor, others=[partner],
        )
        assert pairs == []

    def test_empty_distinctive_anchor_not_pair(self):
        """Anchor with only generic tokens (e.g. 'Basketball')
        cannot anchor a fragmentation pair."""
        anchor = team("generic-uuid", "Basketball", "basketball")
        partner = team("bayern-uuid",
                       "Bayern Basketball", "bayern basketball")
        pairs = find_fragmentation_candidates_pure(
            anchor=anchor, others=[partner],
        )
        assert pairs == []

    def test_no_shared_tokens_not_pair(self):
        anchor = team("bayern-uuid", "Bayern", "bayern")
        partner = team("barcelona-uuid",
                       "FC Barcelona", "fc barcelona")
        pairs = find_fragmentation_candidates_pure(
            anchor=anchor, others=[partner],
        )
        assert pairs == []

    def test_anchor_against_self_skipped(self):
        anchor = team("oldenburg-uuid", "Oldenburg", "oldenburg")
        pairs = find_fragmentation_candidates_pure(
            anchor=anchor, others=[anchor],
        )
        assert pairs == []


# ──────────────────────────────────────────────────────────────────────
# find_all_fragmentation_pairs_pure — batch + dedup
# ──────────────────────────────────────────────────────────────────────


class TestFindAllPairs:
    """Batch scan over the whole team list."""

    def test_dedup_pairs_returned_once_per_id_pair(self):
        teams = [
            team("oldenburg-uuid", "Oldenburg", "oldenburg"),
            team("ewe-baskets-uuid",
                 "EWE Baskets Oldenburg", "ewe baskets oldenburg"),
            team("hamburg-uuid", "Hamburg", "hamburg"),
            team("hamburg-towers-uuid",
                 "Hamburg Towers", "hamburg towers"),
        ]
        pairs = find_all_fragmentation_pairs_pure(teams)
        assert len(pairs) == 2
        # Each (a, b) returned once
        keys = {
            frozenset({p.anchor.team_id, p.partner.team_id})
            for p in pairs
        }
        assert len(keys) == 2

    def test_three_way_chain_returns_two_pairs(self):
        """A {bayern} ⊆ {fc, bayern} ⊆ {fc, bayern, munchen} — three
        teams form 3 distinct pairs but operator only needs the
        useful subsets. Algorithm returns all 3 (operator can
        post-filter)."""
        teams = [
            team("a", "Bayern", "bayern"),
            team("b", "FC Bayern", "fc bayern"),  # but fc is generic
            team("c", "Bayern Munchen", "bayern munchen"),
        ]
        # Distinctive: a={bayern}, b={bayern} (fc stripped),
        # c={bayern, munchen}
        # a==b (identical distinctive) → not pair
        # a⊆c, b⊆c → 2 pairs (a-c, b-c)
        pairs = find_all_fragmentation_pairs_pure(teams)
        # a==b filtered, so 2 pairs
        assert len(pairs) == 2

    def test_no_pairs_in_clean_roster(self):
        """Distinct teams with no fragmentation."""
        teams = [
            team("a", "Bayern Munich", "bayern munich"),
            team("b", "FC Barcelona", "fc barcelona"),
            team("c", "Real Madrid", "real madrid"),
        ]
        pairs = find_all_fragmentation_pairs_pure(teams)
        assert pairs == []


# ──────────────────────────────────────────────────────────────────────
# Verdict — all 4 shapes per Day-37 LOCKED rule
# ──────────────────────────────────────────────────────────────────────


class TestVerdictShapes:

    @staticmethod
    def _bbl_pair():
        anchor = team("oldenburg-uuid", "Oldenburg", "oldenburg")
        partner = team("ewe-baskets-uuid",
                       "EWE Baskets Oldenburg", "ewe baskets oldenburg")
        return find_fragmentation_candidates_pure(
            anchor=anchor, others=[partner],
        )[0]

    def test_alias_link_anchor_has_fixtures_partner_zero(self):
        pair = self._bbl_pair()
        v = classify_fragmentation_pair_pure(
            pair=pair,
            anchor_fixture_count=5,
            partner_fixture_count=0,
        )
        assert v.classification == "ALIAS-LINK"
        assert v.canonical_winner_team_id == "oldenburg-uuid"
        assert v.dormant_phantom_team_id == "ewe-baskets-uuid"
        assert v.proposed_alias_form == "EWE Baskets Oldenburg"

    def test_alias_link_partner_has_fixtures_anchor_zero(self):
        pair = self._bbl_pair()
        v = classify_fragmentation_pair_pure(
            pair=pair,
            anchor_fixture_count=0,
            partner_fixture_count=12,
        )
        assert v.classification == "ALIAS-LINK"
        assert v.canonical_winner_team_id == "ewe-baskets-uuid"
        assert v.dormant_phantom_team_id == "oldenburg-uuid"
        assert v.proposed_alias_form == "Oldenburg"

    def test_merge_required_both_have_fixtures(self):
        pair = self._bbl_pair()
        v = classify_fragmentation_pair_pure(
            pair=pair,
            anchor_fixture_count=3,
            partner_fixture_count=8,
        )
        assert v.classification == "MERGE-REQUIRED"
        assert v.canonical_winner_team_id is None
        assert v.dormant_phantom_team_id is None
        assert v.proposed_alias_form is None
        assert "Tennis-dedup" in v.notes

    def test_merge_required_both_zero_fixtures(self):
        """Degenerate case — both dormant. Conservative MERGE-REQUIRED
        with retention-strategy note."""
        pair = self._bbl_pair()
        v = classify_fragmentation_pair_pure(
            pair=pair,
            anchor_fixture_count=0,
            partner_fixture_count=0,
        )
        assert v.classification == "MERGE-REQUIRED"
        assert v.canonical_winner_team_id is None
        assert "BOTH SIDES have zero fixtures" in v.notes
