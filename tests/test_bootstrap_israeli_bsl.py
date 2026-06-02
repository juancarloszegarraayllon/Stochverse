"""Tests for Israeli BSL bootstrap (Phase 2D.5-A workstream #4).

Mirrors tests/test_bootstrap_lba.py shape with Israeli BSL-specific
discipline: apostrophe + hyphen handling, 11-city cross-sport
collision discipline (highest of Phase 2D.5-A), Liga Leumit
exclusion check.
"""
from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


# ══════════════════════════════════════════════════════════════
# Manifest-shape unit tests (no DB)
# ══════════════════════════════════════════════════════════════


class TestIsraeliBSLManifestShape:
    """Pure-data validation of ISRAELI_BSL_TEAMS_SEED +
    ISRAELI_BSL_ALIAS_SOURCE."""

    def test_manifest_imports_cleanly(self):
        from scripts.israeli_bsl_seed import (
            ISRAELI_BSL_ALIAS_SOURCE,
            ISRAELI_BSL_TEAMS_SEED,
        )
        assert isinstance(ISRAELI_BSL_TEAMS_SEED, list)
        assert isinstance(ISRAELI_BSL_ALIAS_SOURCE, str)

    def test_manifest_size_is_14(self):
        """2025-26 Israeli BSL has 14 teams (structurally; vs LBA's 16
        or ACB's 18). Verified against operator's Day-31 paste from
        Wikipedia 2025-26 Israeli Basketball Premier League roster."""
        from scripts.israeli_bsl_seed import ISRAELI_BSL_TEAMS_SEED
        assert len(ISRAELI_BSL_TEAMS_SEED) == 14, (
            f"Expected 14 Israeli BSL teams; got "
            f"{len(ISRAELI_BSL_TEAMS_SEED)}. If roster changed, update "
            "this test alongside israeli_bsl_seed.py."
        )

    def test_all_entries_are_4_tuples(self):
        from scripts.israeli_bsl_seed import ISRAELI_BSL_TEAMS_SEED
        for entry in ISRAELI_BSL_TEAMS_SEED:
            assert isinstance(entry, tuple)
            assert len(entry) == 4, f"Wrong arity: {entry!r}"
            canonical, country, aliases, _notes = entry
            assert isinstance(canonical, str) and canonical
            assert isinstance(country, str) and country
            assert isinstance(aliases, tuple)

    def test_country_codes_are_all_isr(self):
        """Israeli BSL is a single-country league. All teams ISR."""
        from scripts.israeli_bsl_seed import ISRAELI_BSL_TEAMS_SEED
        for canonical, country, _aliases, _notes in ISRAELI_BSL_TEAMS_SEED:
            assert country == "ISR", (
                f"Team {canonical!r} has country_code={country!r}; "
                "expected 'ISR'"
            )

    def test_all_canonical_names_normalize_to_nonempty(self):
        from resolver._normalize import normalize_name
        from scripts.israeli_bsl_seed import ISRAELI_BSL_TEAMS_SEED
        for canonical, _country, _aliases, _notes in ISRAELI_BSL_TEAMS_SEED:
            assert normalize_name(canonical), (
                f"Canonical name {canonical!r} normalizes to empty"
            )

    def test_all_aliases_normalize_to_nonempty(self):
        from resolver._normalize import normalize_name
        from scripts.israeli_bsl_seed import ISRAELI_BSL_TEAMS_SEED
        for canonical, _country, aliases, _notes in ISRAELI_BSL_TEAMS_SEED:
            for alias in aliases:
                assert normalize_name(alias), (
                    f"Alias {alias!r} (on team {canonical!r}) "
                    "normalizes to empty"
                )

    def test_no_duplicate_canonical_names(self):
        from scripts.israeli_bsl_seed import ISRAELI_BSL_TEAMS_SEED
        canonicals = [c for c, _, _, _ in ISRAELI_BSL_TEAMS_SEED]
        assert len(canonicals) == len(set(canonicals)), (
            "Duplicate canonical_name in manifest"
        )

    def test_no_within_league_alias_collisions(self):
        """No two DIFFERENT BSL teams share the same normalized
        alias. Same-team duplicates (Tel-Aviv + Tel Aviv both normalize
        to 'tel aviv') are not collisions — they're belt-and-suspenders
        aliases on the same team."""
        from resolver._normalize import normalize_name
        from scripts.israeli_bsl_seed import ISRAELI_BSL_TEAMS_SEED
        alias_owners: dict[str, set[str]] = {}
        for canonical, _country, aliases, _notes in ISRAELI_BSL_TEAMS_SEED:
            for alias in aliases:
                normalized = normalize_name(alias)
                if normalized:
                    alias_owners.setdefault(normalized, set()).add(canonical)
        collisions = {
            n: owners for n, owners in alias_owners.items()
            if len(owners) > 1
        }
        assert not collisions, f"Cross-team alias collisions: {collisions}"

    def test_source_value_matches_convention(self):
        """Source value must be 'bootstrap_league_coverage' per Q3
        cohort-wide convention."""
        from scripts.israeli_bsl_seed import ISRAELI_BSL_ALIAS_SOURCE
        assert ISRAELI_BSL_ALIAS_SOURCE == "bootstrap_league_coverage"


# ══════════════════════════════════════════════════════════════
# Apostrophe coverage tests
# ══════════════════════════════════════════════════════════════


class TestIsraeliBSLApostropheCoverage:
    """Apostrophe characters are stripped to space by the normalizer
    (`resolver/_normalize.py` _PUNCT_RE). So 'Be'er Sheva' and
    'Beer Sheva' produce DIFFERENT normalized keys; both must be
    present in the manifest."""

    def test_beer_sheva_apostrophe_and_no_apostrophe_both_present(self):
        from resolver._normalize import normalize_name
        from scripts.israeli_bsl_seed import ISRAELI_BSL_TEAMS_SEED
        manifest_map = {
            canonical: aliases
            for canonical, _, aliases, _ in ISRAELI_BSL_TEAMS_SEED
        }
        canonical = "Hapoel Be'er Sheva/Dimona"
        assert canonical in manifest_map, (
            f"{canonical!r} missing from manifest"
        )
        aliases_norm = {normalize_name(a) for a in manifest_map[canonical]}
        # apostrophe variant → "be er" segment
        assert normalize_name("Hapoel Be'er Sheva") in aliases_norm, (
            "Apostrophe variant 'Hapoel Be'er Sheva' "
            "(normalized 'hapoel be er sheva') missing"
        )
        # no-apostrophe variant → "beer" segment
        assert normalize_name("Hapoel Beer Sheva") in aliases_norm, (
            "ASCII variant 'Hapoel Beer Sheva' "
            "(normalized 'hapoel beer sheva') missing"
        )

    def test_raanana_apostrophe_and_no_apostrophe_both_present(self):
        from resolver._normalize import normalize_name
        from scripts.israeli_bsl_seed import ISRAELI_BSL_TEAMS_SEED
        manifest_map = {
            canonical: aliases
            for canonical, _, aliases, _ in ISRAELI_BSL_TEAMS_SEED
        }
        canonical = "Maccabi Ironi Ra'anana"
        assert canonical in manifest_map, (
            f"{canonical!r} missing from manifest"
        )
        aliases_norm = {normalize_name(a) for a in manifest_map[canonical]}
        assert normalize_name("Maccabi Ironi Ra'anana") in aliases_norm
        assert normalize_name("Maccabi Ironi Raanana") in aliases_norm


# ══════════════════════════════════════════════════════════════
# Hyphen coverage tests
# ══════════════════════════════════════════════════════════════


class TestIsraeliBSLHyphenCoverage:
    """Hyphenated 'Tel-Aviv' production forms normalize identically
    to 'Tel Aviv' (hyphen → space; whitespace collapses). Including
    both is belt-and-suspenders documentation."""

    def test_maccabi_tel_aviv_hyphenated_alias_present(self):
        from scripts.israeli_bsl_seed import ISRAELI_BSL_TEAMS_SEED
        for canonical, _, aliases, _ in ISRAELI_BSL_TEAMS_SEED:
            if canonical == "Maccabi Tel Aviv":
                assert "Maccabi Tel-Aviv" in aliases, (
                    "Maccabi Tel Aviv missing hyphenated 'Tel-Aviv' alias"
                )
                return

    def test_hapoel_tel_aviv_hyphenated_alias_present(self):
        from scripts.israeli_bsl_seed import ISRAELI_BSL_TEAMS_SEED
        for canonical, _, aliases, _ in ISRAELI_BSL_TEAMS_SEED:
            if canonical == "Hapoel Tel Aviv":
                assert "Hapoel Tel-Aviv" in aliases, (
                    "Hapoel Tel Aviv missing hyphenated 'Tel-Aviv' alias"
                )
                return


# ══════════════════════════════════════════════════════════════
# Cross-sport collision discipline (highest of Phase 2D.5-A)
# ══════════════════════════════════════════════════════════════


class TestIsraeliBSLCrossSportCollisionDiscipline:
    """11 of 14 BSL teams have Israeli football counterparts. Bare-city
    aliases EXCLUDED for these 11 cities. Bare prefixes EXCLUDED for
    within-BSL collision discipline (Maccabi, Hapoel, Ironi, Bnei,
    Elitzur)."""

    EXCLUDED_BARE_CITIES = (
        "Tel Aviv", "Jerusalem", "Be'er Sheva", "Beer Sheva", "Holon",
        "Ra'anana", "Raanana", "Ness Ziona", "Ramat Gan", "Herzliya",
        "Rishon LeZion", "Rishon", "Netanya",
    )

    EXCLUDED_BARE_PREFIXES = (
        "Maccabi", "Hapoel", "Ironi", "Bnei", "Elitzur",
    )

    def test_bare_collision_cities_not_aliased_anywhere(self):
        """No BSL team has bare 'Tel Aviv' / 'Jerusalem' / etc. as
        a standalone alias. Each must be sport-disambiguated."""
        from resolver._normalize import normalize_name
        from scripts.israeli_bsl_seed import ISRAELI_BSL_TEAMS_SEED
        excluded_normalized = {
            normalize_name(c) for c in self.EXCLUDED_BARE_CITIES
        }
        for canonical, _, aliases, _ in ISRAELI_BSL_TEAMS_SEED:
            for alias in aliases:
                assert normalize_name(alias) not in excluded_normalized, (
                    f"Found bare collision-city alias {alias!r} on "
                    f"team {canonical!r}. Cities "
                    f"{self.EXCLUDED_BARE_CITIES!r} must be sport-"
                    "disambiguated to avoid Israeli football "
                    "cross-sport collision."
                )

    def test_bare_prefixes_not_aliased_anywhere(self):
        """No BSL team has bare 'Maccabi' / 'Hapoel' / etc. as a
        standalone alias. Prefixes are shared across multiple BSL
        teams + future-promotion risk."""
        from resolver._normalize import normalize_name
        from scripts.israeli_bsl_seed import ISRAELI_BSL_TEAMS_SEED
        excluded_normalized = {
            normalize_name(p) for p in self.EXCLUDED_BARE_PREFIXES
        }
        for canonical, _, aliases, _ in ISRAELI_BSL_TEAMS_SEED:
            for alias in aliases:
                assert normalize_name(alias) not in excluded_normalized, (
                    f"Found bare prefix alias {alias!r} on team "
                    f"{canonical!r}. Prefixes "
                    f"{self.EXCLUDED_BARE_PREFIXES!r} must always be "
                    "qualified (within-BSL + future-promotion risk)."
                )

    def test_maccabi_tel_aviv_canonical_is_prefix_disambiguated(self):
        """Canonical must be 'Maccabi Tel Aviv' — 'Maccabi' prefix
        distinguishes from Hapoel Tel Aviv (BSL) and the football clubs."""
        from scripts.israeli_bsl_seed import ISRAELI_BSL_TEAMS_SEED
        canonicals = [c for c, _, _, _ in ISRAELI_BSL_TEAMS_SEED]
        assert "Maccabi Tel Aviv" in canonicals

    def test_hapoel_jerusalem_canonical_is_prefix_disambiguated(self):
        """Canonical must be 'Hapoel Jerusalem' — 'Hapoel' prefix
        distinguishes from Beitar Jerusalem FC + Hapoel Jerusalem FC."""
        from scripts.israeli_bsl_seed import ISRAELI_BSL_TEAMS_SEED
        canonicals = [c for c, _, _, _ in ISRAELI_BSL_TEAMS_SEED]
        assert "Hapoel Jerusalem" in canonicals


# ══════════════════════════════════════════════════════════════
# Day-31 discovery target coverage tests
# ══════════════════════════════════════════════════════════════


class TestIsraeliBSLDiscoveryTargets:
    """Verify that in-scope production-form strings from Day-31's
    asymmetric_anchor_failure discovery query are covered as aliases."""

    DAY_31_TARGETS = [
        "Maccabi Tel Aviv",
        "Maccabi Tel-Aviv",
        "Hapoel Tel Aviv",
        "Hapoel Tel-Aviv",
        "Hapoel Jerusalem",
        "Bnei Herzliya",
        "Bnei Herzliya Basket",
        "Hapoel HaEmek",
        "Hapoel Haemek",
        "Maccabi Rishon LeZion",
        "Maccabi Rishon",
        "Hapoel Beer Sheva",
        "Ironi Kiryat Ata",
        "Hapoel Galil Elyon",
        "Galil Elyon",
        "Elitzur Maccabi Netanya",
    ]

    def test_discovery_target_strings_are_aliases(self):
        from resolver._normalize import normalize_name
        from scripts.israeli_bsl_seed import ISRAELI_BSL_TEAMS_SEED
        all_normalized_aliases = set()
        for _, _, aliases, _ in ISRAELI_BSL_TEAMS_SEED:
            for alias in aliases:
                normalized = normalize_name(alias)
                if normalized:
                    all_normalized_aliases.add(normalized)
        for target_string in self.DAY_31_TARGETS:
            normalized = normalize_name(target_string)
            assert normalized in all_normalized_aliases, (
                f"Day-31 discovery target {target_string!r} "
                f"(normalized {normalized!r}) not found in manifest aliases"
            )


# ══════════════════════════════════════════════════════════════
# Liga Leumit out-of-scope exclusion tests
# ══════════════════════════════════════════════════════════════


class TestIsraeliBSLLigaLeumitExclusion:
    """Day-31 discovery query surfaced 6 Liga Leumit (Israeli National
    League / second division) team names as out-of-scope FL leakage.
    These must NOT be present as canonicals or aliases in the v1
    Premier League manifest."""

    LIGA_LEUMIT_OUT_OF_SCOPE = (
        "Maccabi Haifa",
        "Maccabi Petah Tikva",
        "Maccabi Kiryat Gat",
        "Maccabi Maale Adumim",
        "Migdal Haemek",
        "Elitzur Yavne",
    )

    def test_liga_leumit_teams_absent_from_canonicals(self):
        from scripts.israeli_bsl_seed import ISRAELI_BSL_TEAMS_SEED
        canonicals = {c for c, _, _, _ in ISRAELI_BSL_TEAMS_SEED}
        present_oos = set(self.LIGA_LEUMIT_OUT_OF_SCOPE) & canonicals
        assert not present_oos, (
            f"Liga Leumit teams found in BSL Premier manifest "
            f"(scope violation): {present_oos}"
        )

    def test_liga_leumit_teams_absent_from_aliases(self):
        from resolver._normalize import normalize_name
        from scripts.israeli_bsl_seed import ISRAELI_BSL_TEAMS_SEED
        oos_normalized = {
            normalize_name(t) for t in self.LIGA_LEUMIT_OUT_OF_SCOPE
        }
        for canonical, _, aliases, _ in ISRAELI_BSL_TEAMS_SEED:
            for alias in aliases:
                assert normalize_name(alias) not in oos_normalized, (
                    f"Liga Leumit team name {alias!r} found as alias "
                    f"on BSL Premier team {canonical!r} (scope violation)"
                )


# ══════════════════════════════════════════════════════════════
# Roster-membership tests (Wikipedia 2025-26 BSL Premier paste)
# ══════════════════════════════════════════════════════════════


class TestIsraeliBSLRosterMembership:
    """All 14 operator-pasted Wikipedia 2025-26 Israeli Basketball
    Premier League roster teams present as canonicals."""

    EXPECTED_CANONICALS = {
        "Bnei Herzliya",
        "Elitzur Netanya",
        "Hapoel Be'er Sheva/Dimona",
        "Hapoel Galil Elyon",
        "Hapoel HaEmek",
        "Hapoel Holon",
        "Hapoel Jerusalem",
        "Hapoel Tel Aviv",
        "Ironi Kiryat Ata",
        "Ironi Ness Ziona",
        "Maccabi Ironi Ra'anana",
        "Maccabi Ironi Ramat Gan",
        "Maccabi Rishon LeZion",
        "Maccabi Tel Aviv",
    }

    def test_expected_canonicals_all_present(self):
        from scripts.israeli_bsl_seed import ISRAELI_BSL_TEAMS_SEED
        canonicals = {c for c, _, _, _ in ISRAELI_BSL_TEAMS_SEED}
        missing = self.EXPECTED_CANONICALS - canonicals
        assert not missing, (
            f"Operator-pasted roster teams missing from manifest: {missing}"
        )
