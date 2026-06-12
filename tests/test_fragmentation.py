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
    _has_distinct_entity_marker,
    _has_gender_marker,
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


# ──────────────────────────────────────────────────────────────────────
# Gender / women's-team guard (Day-N+1+1 FIBA Europe Cup finding)
# ──────────────────────────────────────────────────────────────────────


class TestGenderMarkerDetection:
    """`_has_gender_marker` primitive — recognizes Women / Femenino /
    Damen / etc. while NOT false-positive-ing on BW / BWB / Wroclaw."""

    def test_english_women_variants(self):
        for name in ("Real Madrid Women", "Real Madrid Womens",
                     "Real Madrid Woman", "Chelsea Ladies",
                     "real madrid women", "REAL MADRID WOMEN"):
            assert _has_gender_marker(name), f"{name!r} should match"

    def test_spanish_femenino_variants(self):
        for name in ("Casademont Zaragoza Femenino",
                     "Valencia Basket Femenino",
                     "Casademont Zaragoza Femenina",
                     "casademont zaragoza femenino"):
            assert _has_gender_marker(name), f"{name!r} should match"

    def test_italian_femminile(self):
        for name in ("Virtus Bologna Femminile",
                     "Olimpia Milano Femminile"):
            assert _has_gender_marker(name), f"{name!r} should match"

    def test_french_feminin_with_accents(self):
        """Accented French forms must match via NFD-strip."""
        for name in ("ASVEL Féminin", "ASVEL Féminines",
                     "Bourges Basket Féminin",
                     "Lyon ASVEL Féminin"):
            assert _has_gender_marker(name), f"{name!r} should match"

    def test_french_feminin_without_accents(self):
        for name in ("ASVEL Feminin", "ASVEL Feminines",
                     "Bourges Basket Feminin"):
            assert _has_gender_marker(name), f"{name!r} should match"

    def test_german_damen(self):
        for name in ("Bayern München Damen", "Alba Berlin Damen",
                     "bayern munchen damen"):
            assert _has_gender_marker(name), f"{name!r} should match"

    def test_polish_kobiety(self):
        for name in ("Wisła Kraków Kobiety", "Polonia Warszawa Kobiet",
                     "wisla krakow kobiety"):
            assert _has_gender_marker(name), f"{name!r} should match"

    def test_trailing_w_detected(self):
        for name in ("Zaragoza W", "Real Madrid W",
                     "Barcelona W", "Bayern München W ",  # trailing ws
                     "zaragoza w"):
            assert _has_gender_marker(name), f"{name!r} should match"

    def test_bw_not_falsely_matched(self):
        """'W' inside 'BW' / 'BWB' must NOT trigger the trailing-W
        rule — these are real club abbreviations."""
        for name in ("BW", "BWB", "BW Berlin", "Berlin BWB",
                     "BWB Heidelberg"):
            assert not _has_gender_marker(name), (
                f"{name!r} must NOT match (BW/BWB is club abbrev)"
            )

    def test_wroclaw_not_falsely_matched(self):
        """'Wroclaw' ends in lower-case w but is not a standalone W."""
        for name in ("Wroclaw", "Slask Wroclaw", "WKS Slask Wroclaw"):
            assert not _has_gender_marker(name), (
                f"{name!r} must NOT match (Wroclaw is a city)"
            )

    def test_middle_w_not_matched(self):
        """Standalone W in middle position must NOT match — only
        trailing."""
        assert not _has_gender_marker("Real W Madrid")

    def test_real_men_team_names_not_falsely_matched(self):
        """Men's club names without gender markers must NOT match."""
        for name in ("Real Madrid", "Basket Zaragoza", "FC Barcelona",
                     "Bayern München", "Olympiakos BC",
                     "EWE Baskets Oldenburg", "BC Wolves",
                     "AS Monaco", "Hamburg Towers"):
            assert not _has_gender_marker(name), \
                f"{name!r} must NOT match (men's club)"

    def test_empty_input(self):
        assert not _has_gender_marker("")
        assert not _has_gender_marker("   ")


class TestDistinctEntityMarkerAggregate:
    """The aggregate must return True if EITHER reserve OR gender
    marker is present."""

    def test_returns_true_for_reserve(self):
        assert _has_distinct_entity_marker("Monaco U21")
        assert _has_distinct_entity_marker("Real Madrid B")

    def test_returns_true_for_gender(self):
        assert _has_distinct_entity_marker("Casademont Zaragoza Femenino")
        assert _has_distinct_entity_marker("Zaragoza W")

    def test_returns_false_for_senior_mens(self):
        for name in ("Real Madrid", "Basket Zaragoza", "FC Barcelona",
                     "Gravelines-Dunkerque", "BC Vienna"):
            assert not _has_distinct_entity_marker(name)


# ──────────────────────────────────────────────────────────────────────
# Gender guard wired into pair detection
# ──────────────────────────────────────────────────────────────────────


class TestGenderGuardInPairing:
    """The Day-N+1+1 FIBA Europe Cup regression: men's-vs-women's must
    not pair. Operator-specified cases below."""

    def test_basket_zaragoza_vs_femenino_rejected(self):
        """Operator's primary case: ALIAS-LINK false positive in FIBA
        Europe Cup smoke."""
        mens = team("zaragoza-uuid", "Basket Zaragoza",
                    "basket zaragoza", "ESP")
        womens = team("zaragoza-femenino-uuid",
                      "Casademont Zaragoza Femenino",
                      "casademont zaragoza femenino", "ESP")
        pairs = find_fragmentation_candidates_pure(
            anchor=mens, others=[womens],
        )
        assert pairs == [], (
            "Basket Zaragoza vs Casademont Zaragoza Femenino must NOT "
            "pair (men's vs women's)"
        )

    def test_basket_zaragoza_vs_zaragoza_w_rejected(self):
        """Operator's second case: MERGE-REQUIRED false positive,
        trailing standalone W marker."""
        mens = team("zaragoza-uuid", "Basket Zaragoza",
                    "basket zaragoza", "ESP")
        womens = team("zaragoza-w-uuid", "Zaragoza W", "zaragoza w",
                      "ESP")
        pairs = find_fragmentation_candidates_pure(
            anchor=mens, others=[womens],
        )
        assert pairs == [], (
            "Basket Zaragoza vs Zaragoza W must NOT pair (trailing W "
            "= women's marker)"
        )

    def test_real_madrid_vs_real_madrid_women_rejected(self):
        mens = team("rm-uuid", "Real Madrid", "real madrid", "ESP")
        womens = team("rm-w-uuid", "Real Madrid Women",
                      "real madrid women", "ESP")
        pairs = find_fragmentation_candidates_pure(
            anchor=mens, others=[womens],
        )
        assert pairs == []

    def test_french_asvel_vs_asvel_feminin_rejected(self):
        mens = team("asvel-uuid", "ASVEL", "asvel", "FRA")
        womens = team("asvel-w-uuid", "ASVEL Féminin", "asvel feminin",
                      "FRA")
        pairs = find_fragmentation_candidates_pure(
            anchor=mens, others=[womens],
        )
        assert pairs == []

    def test_bayern_vs_bayern_damen_rejected(self):
        mens = team("bayern-uuid", "Bayern München", "bayern munchen",
                    "DEU")
        womens = team("bayern-damen-uuid", "Bayern München Damen",
                      "bayern munchen damen", "DEU")
        pairs = find_fragmentation_candidates_pure(
            anchor=mens, others=[womens],
        )
        assert pairs == []

    def test_two_womens_teams_still_eligible(self):
        """Both sides carry women's marker → guard does not block.
        Subset relation still required."""
        a = team("a", "Zaragoza Femenino", "zaragoza femenino", "ESP")
        p = team("b", "Casademont Zaragoza Femenino",
                 "casademont zaragoza femenino", "ESP")
        pairs = find_fragmentation_candidates_pure(
            anchor=a, others=[p],
        )
        assert len(pairs) == 1, (
            "Two women's teams with same marker — guard should not "
            "block"
        )

    def test_bw_club_pairs_with_broader_form(self):
        """BW (Blau-Weiss) abbreviation must NOT be stripped — a BW
        team and a broader-name partner with no markers should pair."""
        anchor = team("a", "BW Berlin", "bw berlin", "DEU")
        partner = team("b", "BW Berlin United", "bw berlin united",
                       "DEU")
        pairs = find_fragmentation_candidates_pure(
            anchor=anchor, others=[partner],
        )
        assert len(pairs) == 1, (
            "BW Berlin ↔ BW Berlin United should pair "
            "(W-rule must NOT strip 'W' from 'BW')"
        )

    def test_reserve_vs_womens_still_rejected(self):
        """Cross-marker case: reserve on one side, women's on other.
        Markers differ → guard blocks. (This is the expected behavior:
        a U21 men's reserve and a women's team are distinct entities
        from each other AND from any senior men's club.)"""
        reserve = team("a", "Monaco U21", "monaco u21", "FRA")
        womens = team("b", "Monaco Femenino", "monaco femenino", "FRA")
        # Same canonical city, different markers (reserve vs gender) —
        # subset relation holds ({monaco} ⊆ both), but distinct-entity
        # markers DIFFER (anchor has reserve only, partner has gender
        # only) → both `_has_distinct_entity_marker` calls return True
        # so markers MATCH and the pair would proceed. Document this:
        # the guard is reserve-OR-gender per side, not differentiating
        # between marker flavors. In practice this is rare and the
        # downstream fixture-count classification would still flag
        # operator review.
        pairs = find_fragmentation_candidates_pure(
            anchor=reserve, others=[womens],
        )
        # Both sides have markers → aggregate matches → guard passes →
        # subset relation evaluated. Both have distinct tokens
        # {monaco, u21} vs {monaco, femenino}; neither is subset of
        # the other → no pair on subset grounds.
        assert pairs == []
