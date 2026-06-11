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
    _has_reserve_marker,
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


# ──────────────────────────────────────────────────────────────────────
# Reserve-team guard (Day-N+1 France LNB finding)
# ──────────────────────────────────────────────────────────────────────


class TestReserveMarkerDetection:
    """`_has_reserve_marker` primitive — recognizes U21/Espoirs/B/II
    etc. while NOT false-positive-ing on BC, III, embedded substrings."""

    def test_u21_variants_detected(self):
        for name in ("Monaco U21", "Monaco U-21", "Monaco U 21",
                     "monaco u21", "MONACO U21"):
            assert _has_reserve_marker(name), f"{name!r} should match"

    def test_age_group_range(self):
        # All U15..U24 should match
        for age in (15, 16, 17, 18, 19, 20, 21, 22, 23, 24):
            assert _has_reserve_marker(f"Team U{age}"), \
                f"U{age} should match"
        # Outside range should NOT match
        for age in (10, 14, 25, 30):
            assert not _has_reserve_marker(f"Team U{age}"), \
                f"U{age} should NOT match"

    def test_espoirs_detected(self):
        for name in ("Limoges CSP Espoirs", "Monaco Espoirs U21",
                     "Espoirs Monaco", "Espoir Monaco"):
            assert _has_reserve_marker(name), f"{name!r} should match"

    def test_reserve_juniors_jr_detected(self):
        for name in ("Real Madrid Reserve", "Real Madrid Reserves",
                     "Junior NBA", "FC Barcelona Junior",
                     "FC Barcelona Juniors", "Real Madrid Jr",
                     "Real Madrid Jr."):
            assert _has_reserve_marker(name), f"{name!r} should match"

    def test_trailing_b_detected(self):
        for name in ("Real Madrid B", "FC Barcelona B",
                     "AC Milan B", "Bayern Munich B "):  # trailing ws
            assert _has_reserve_marker(name), f"{name!r} should match"

    def test_trailing_ii_detected(self):
        for name in ("Real Madrid II", "Barcelona II",
                     "Bayern Munich II"):
            assert _has_reserve_marker(name), f"{name!r} should match"

    def test_bc_not_falsely_matched(self):
        """'B' inside 'BC' must NOT trigger the trailing-B rule —
        BC is a generic club prefix in many leagues."""
        for name in ("BC Vienna", "BC Andorra", "BC Zenit",
                     "BC Wolves", "Vienna BC"):
            assert not _has_reserve_marker(name), (
                f"{name!r} must NOT match (BC is generic prefix)"
            )

    def test_iii_not_falsely_matched(self):
        """'II' embedded in 'III' must NOT trigger."""
        assert not _has_reserve_marker("Real Madrid III")

    def test_middle_b_or_ii_not_matched(self):
        """Standalone B/II only counts as trailing — middle position
        must NOT match."""
        assert not _has_reserve_marker("Real B Madrid")
        assert not _has_reserve_marker("Real II Madrid")

    def test_real_team_names_not_falsely_matched(self):
        """Senior club names without reserve markers must NOT match."""
        for name in ("Real Madrid", "FC Barcelona", "Bayern München",
                     "Olympiakos BC", "Maccabi Tel Aviv",
                     "Gravelines-Dunkerque",
                     "BCM Gravelines-Dunkerque",
                     "EWE Baskets Oldenburg"):
            assert not _has_reserve_marker(name), \
                f"{name!r} must NOT match (senior club)"

    def test_empty_input(self):
        assert not _has_reserve_marker("")
        assert not _has_reserve_marker("   ")


# ──────────────────────────────────────────────────────────────────────
# Reserve-team guard wired into pair detection
# ──────────────────────────────────────────────────────────────────────


class TestReserveGuardInPairing:
    """The Day-N+1 France LNB regression: senior vs reserve must not
    pair. Operator-specified cases below."""

    def test_monaco_vs_monaco_u21_rejected(self):
        senior = team("monaco-uuid", "Monaco", "monaco")
        reserve = team("monaco-u21-uuid", "Monaco U21", "monaco u21")
        pairs = find_fragmentation_candidates_pure(
            anchor=senior, others=[reserve],
        )
        assert pairs == [], (
            "Monaco vs Monaco U21 must NOT pair (senior vs reserve)"
        )

    def test_monaco_vs_monaco_espoirs_u21_rejected(self):
        senior = team("monaco-uuid", "Monaco", "monaco")
        reserve = team("monaco-espoirs-uuid",
                       "Monaco Espoirs U21", "monaco espoirs u21")
        pairs = find_fragmentation_candidates_pure(
            anchor=senior, others=[reserve],
        )
        assert pairs == []

    def test_real_madrid_vs_real_madrid_b_rejected(self):
        """Trailing standalone B distinguishes reserve squad."""
        senior = team("rm-uuid", "Real Madrid", "real madrid", "ESP")
        reserve = team("rm-b-uuid", "Real Madrid B", "real madrid b",
                       "ESP")
        pairs = find_fragmentation_candidates_pure(
            anchor=senior, others=[reserve],
        )
        assert pairs == []

    def test_real_madrid_vs_real_madrid_ii_rejected(self):
        senior = team("rm-uuid", "Real Madrid", "real madrid", "ESP")
        reserve = team("rm-ii-uuid", "Real Madrid II", "real madrid ii",
                       "ESP")
        pairs = find_fragmentation_candidates_pure(
            anchor=senior, others=[reserve],
        )
        assert pairs == []

    def test_nanterre_vs_nanterre_92_espoirs_rejected(self):
        """France LNB operator-cited example."""
        senior = team("nanterre-uuid", "Nanterre", "nanterre")
        reserve = team("nanterre-92-espoirs-uuid",
                       "Nanterre 92 Espoirs", "nanterre 92 espoirs")
        pairs = find_fragmentation_candidates_pure(
            anchor=senior, others=[reserve],
        )
        assert pairs == []

    def test_gravelines_dunkerque_true_positive_survives(self):
        """The ONE true positive in France LNB: no reserve marker
        either side; this is a real sponsor-name fragment that MUST
        survive the fix."""
        anchor = team("grav-uuid", "Gravelines-Dunkerque",
                      "gravelines dunkerque")
        partner = team("bcm-grav-uuid", "BCM Gravelines-Dunkerque",
                       "bcm gravelines dunkerque")
        pairs = find_fragmentation_candidates_pure(
            anchor=anchor, others=[partner],
        )
        assert len(pairs) == 1, (
            "Gravelines-Dunkerque true-positive fragment must survive "
            "the reserve-team guard"
        )
        assert pairs[0].broader_team_id == "bcm-grav-uuid"

    def test_bbl_regressions_still_pair(self):
        """BBL pairs that passed Day-37 gate must still pair after
        the Day-N+1 guard (no reserve markers on either side)."""
        cases = [
            ("Oldenburg", "EWE Baskets Oldenburg",
             "oldenburg", "ewe baskets oldenburg"),
            ("Hamburg", "Hamburg Towers",
             "hamburg", "hamburg towers"),
            ("Bayern", "Bayern München",
             "bayern", "bayern munchen"),
        ]
        for anchor_c, partner_c, anchor_n, partner_n in cases:
            a = team("a", anchor_c, anchor_n)
            p = team("b", partner_c, partner_n)
            pairs = find_fragmentation_candidates_pure(
                anchor=a, others=[p],
            )
            assert len(pairs) == 1, (
                f"BBL regression: {anchor_c!r} ↔ {partner_c!r} "
                "must still pair"
            )

    def test_two_reserves_same_marker_still_eligible(self):
        """Two reserve squads of related clubs both carry markers →
        marker presence MATCHES → guard does not block. Subset
        relation still required."""
        # Both have U21 marker → guard passes; subset {monaco u21}
        # ⊆ {as, monaco, u21} → still a candidate.
        a = team("a", "Monaco U21", "monaco u21")
        p = team("b", "AS Monaco U21", "as monaco u21")
        pairs = find_fragmentation_candidates_pure(
            anchor=a, others=[p],
        )
        assert len(pairs) == 1, (
            "Two reserves with same marker — guard should not block"
        )

    def test_bc_vienna_not_treated_as_reserve(self):
        """The B-rule must not strip 'B' from 'BC'. BC Vienna and a
        broader-name partner should still pair if subset relation
        holds and neither has a reserve marker."""
        anchor = team("a", "BC Vienna", "bc vienna")
        partner = team("b", "BC Vienna United",
                       "bc vienna united")
        pairs = find_fragmentation_candidates_pure(
            anchor=anchor, others=[partner],
        )
        # Neither has a reserve marker; subset holds. Should pair.
        assert len(pairs) == 1, (
            "BC Vienna ↔ BC Vienna United should pair "
            "(B-rule must NOT strip 'B' from 'BC')"
        )
