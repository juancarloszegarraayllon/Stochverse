"""Tests for Italian LBA Serie A bootstrap (Phase 2D.5-A workstream #3).

Mirrors tests/test_bootstrap_acb.py shape:

  - Manifest-shape unit tests (always run; no DB required)
  - Alias distinctiveness per sport_id partition
  - Diacritic variant coverage (Cantù)
  - Source-value convention
  - Cross-sport collision discipline (Milano, Bologna, Napoli, Venezia)
  - Day-28/Day-30 discovery target coverage
"""
from __future__ import annotations

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent


# ══════════════════════════════════════════════════════════════
# Manifest-shape unit tests (no DB)
# ══════════════════════════════════════════════════════════════


class TestLBAManifestShape:
    """Pure-data validation of LBA_TEAMS_SEED + LBA_ALIAS_SOURCE."""

    def test_manifest_imports_cleanly(self):
        from scripts.lba_seed import LBA_ALIAS_SOURCE, LBA_TEAMS_SEED
        assert isinstance(LBA_TEAMS_SEED, list)
        assert isinstance(LBA_ALIAS_SOURCE, str)

    def test_manifest_size_is_16(self):
        """2025-26 LBA Serie A has 16 teams. Verified against operator's
        Day-30 paste from Wikipedia 2025-26 LBA season roster."""
        from scripts.lba_seed import LBA_TEAMS_SEED
        assert len(LBA_TEAMS_SEED) == 16, (
            f"Expected 16 LBA teams; got {len(LBA_TEAMS_SEED)}. "
            "If roster changed, update this test alongside lba_seed.py."
        )

    def test_all_entries_are_4_tuples(self):
        from scripts.lba_seed import LBA_TEAMS_SEED
        for entry in LBA_TEAMS_SEED:
            assert isinstance(entry, tuple)
            assert len(entry) == 4, f"Wrong arity: {entry!r}"
            canonical, country, aliases, _notes = entry
            assert isinstance(canonical, str) and canonical
            assert isinstance(country, str) and country
            assert isinstance(aliases, tuple)

    def test_country_codes_are_all_ita(self):
        """LBA is a single-country Italian league. All teams ITA."""
        from scripts.lba_seed import LBA_TEAMS_SEED
        for canonical, country, _aliases, _notes in LBA_TEAMS_SEED:
            assert country == "ITA", (
                f"Team {canonical!r} has country_code={country!r}; "
                "expected 'ITA'"
            )

    def test_all_canonical_names_normalize_to_nonempty(self):
        from resolver._normalize import normalize_name
        from scripts.lba_seed import LBA_TEAMS_SEED
        for canonical, _country, _aliases, _notes in LBA_TEAMS_SEED:
            normalized = normalize_name(canonical)
            assert normalized, (
                f"Canonical name {canonical!r} normalizes to empty"
            )

    def test_all_aliases_normalize_to_nonempty(self):
        from resolver._normalize import normalize_name
        from scripts.lba_seed import LBA_TEAMS_SEED
        for canonical, _country, aliases, _notes in LBA_TEAMS_SEED:
            for alias in aliases:
                normalized = normalize_name(alias)
                assert normalized, (
                    f"Alias {alias!r} (on team {canonical!r}) "
                    "normalizes to empty"
                )

    def test_no_duplicate_canonical_names(self):
        from scripts.lba_seed import LBA_TEAMS_SEED
        canonicals = [c for c, _, _, _ in LBA_TEAMS_SEED]
        assert len(canonicals) == len(set(canonicals)), (
            "Duplicate canonical_name in manifest"
        )

    def test_no_within_league_alias_collisions(self):
        """No two DIFFERENT LBA teams share the same normalized
        alias. Within-league collision would cause ambiguous strict-tier
        lookups (resolver/aliases.py:115-119 returns None on
        ambiguous keys).

        Same-team diacritic pairs (e.g., "Cantù" + "Cantu" both
        normalizing to "cantu") are NOT collisions — they're
        belt-and-suspenders aliases on the same team."""
        from resolver._normalize import normalize_name
        from scripts.lba_seed import LBA_TEAMS_SEED
        alias_owners: dict[str, set[str]] = {}
        for canonical, _country, aliases, _notes in LBA_TEAMS_SEED:
            for alias in aliases:
                normalized = normalize_name(alias)
                if normalized:
                    alias_owners.setdefault(normalized, set()).add(canonical)
        collisions = {
            n: owners for n, owners in alias_owners.items()
            if len(owners) > 1
        }
        assert not collisions, (
            f"Cross-team alias collisions: {collisions}"
        )

    def test_source_value_matches_convention(self):
        """Source value must be 'bootstrap_league_coverage' per Q3
        cohort-wide convention."""
        from scripts.lba_seed import LBA_ALIAS_SOURCE
        assert LBA_ALIAS_SOURCE == "bootstrap_league_coverage"


# ══════════════════════════════════════════════════════════════
# Diacritic coverage tests
# ══════════════════════════════════════════════════════════════


class TestLBADiacriticCoverage:
    """Every accented canonical must have an ASCII-stripped alias.
    The normalizer strips accents (NFD decomposition), so both forms
    resolve to the same normalized key — but including both in the
    manifest is belt-and-suspenders."""

    ACCENTED_TEAMS = [
        ("Pallacanestro Cantù", "Pallacanestro Cantu"),
        ("Pallacanestro Cantù", "Cantu"),
    ]

    def test_accented_teams_have_ascii_variant(self):
        from scripts.lba_seed import LBA_TEAMS_SEED
        manifest_map = {
            canonical: aliases
            for canonical, _, aliases, _ in LBA_TEAMS_SEED
        }
        for canonical, expected_ascii in self.ACCENTED_TEAMS:
            if canonical not in manifest_map:
                continue
            aliases = manifest_map[canonical]
            assert expected_ascii in aliases, (
                f"Team {canonical!r} missing ASCII variant "
                f"{expected_ascii!r} in aliases {aliases!r}"
            )


# ══════════════════════════════════════════════════════════════
# Cross-sport collision discipline (Day-22 architectural finding)
# ══════════════════════════════════════════════════════════════


class TestLBACrossSportCollisionDiscipline:
    """Italian Serie A football clubs share city names with LBA
    basketball clubs: AC Milan/Inter (Milano), Bologna FC (Bologna),
    SSC Napoli (Napoli), Venezia FC (Venezia). Sport-historical
    canonicals + sport-disambiguated aliases under sport_id partition
    is the discipline (Day-22 finding). Bare city aliases for these
    four cities are INTENTIONALLY EXCLUDED for operator clarity
    (matcher-layer disambiguation already handled by sport_id)."""

    COLLISION_CITIES = ("Milano", "Bologna", "Napoli", "Venezia")

    def test_bare_collision_cities_not_aliased_anywhere(self):
        """No LBA team has bare 'Milano' / 'Bologna' / 'Napoli' /
        'Venezia' as an alias. Each must be qualified."""
        from resolver._normalize import normalize_name
        from scripts.lba_seed import LBA_TEAMS_SEED
        excluded_normalized = {
            normalize_name(c) for c in self.COLLISION_CITIES
        }
        for canonical, _, aliases, _ in LBA_TEAMS_SEED:
            for alias in aliases:
                assert normalize_name(alias) not in excluded_normalized, (
                    f"Found bare collision-city alias {alias!r} on team "
                    f"{canonical!r}. Cities {self.COLLISION_CITIES!r} "
                    "must be sport-disambiguated to avoid Italian "
                    "Serie A football cross-sport collision."
                )

    def test_olimpia_milano_canonical_is_sport_specific(self):
        """Canonical must be 'Olimpia Milano' — sport-disambiguator
        'Olimpia' distinguishes from AC Milan / Inter Milan."""
        from scripts.lba_seed import LBA_TEAMS_SEED
        canonicals = [c for c, _, _, _ in LBA_TEAMS_SEED]
        assert "Olimpia Milano" in canonicals, (
            "Expected 'Olimpia Milano' canonical — 'Olimpia' is the "
            "sport-disambiguator from Milan football clubs"
        )

    def test_virtus_bologna_canonical_is_sport_specific(self):
        """Canonical must be 'Virtus Bologna' — sport-disambiguator
        'Virtus' distinguishes from Bologna FC."""
        from scripts.lba_seed import LBA_TEAMS_SEED
        canonicals = [c for c, _, _, _ in LBA_TEAMS_SEED]
        assert "Virtus Bologna" in canonicals, (
            "Expected 'Virtus Bologna' canonical — 'Virtus' is the "
            "sport-disambiguator from Bologna FC"
        )

    def test_napoli_basket_canonical_is_sport_specific(self):
        """Canonical must be 'Napoli Basket' — sport-disambiguator
        'Basket' distinguishes from SSC Napoli football."""
        from scripts.lba_seed import LBA_TEAMS_SEED
        canonicals = [c for c, _, _, _ in LBA_TEAMS_SEED]
        assert "Napoli Basket" in canonicals, (
            "Expected 'Napoli Basket' canonical — 'Basket' is the "
            "sport-disambiguator from SSC Napoli football"
        )

    def test_reyer_venezia_canonical_is_sport_specific(self):
        """Canonical must be 'Reyer Venezia' — sport-disambiguator
        'Reyer' distinguishes from Venezia FC."""
        from scripts.lba_seed import LBA_TEAMS_SEED
        canonicals = [c for c, _, _, _ in LBA_TEAMS_SEED]
        assert "Reyer Venezia" in canonicals, (
            "Expected 'Reyer Venezia' canonical — 'Reyer' is the "
            "sport-disambiguator from Venezia FC"
        )

    def test_bare_virtus_alias_intentionally_excluded(self):
        """Bare 'Virtus' alias must NOT exist on Virtus Bologna —
        multiple Italian basketball clubs use 'Virtus' across LBA
        and Serie A2/B (Roma 1960, Cassino, etc.). Always qualify."""
        from resolver._normalize import normalize_name
        from scripts.lba_seed import LBA_TEAMS_SEED
        bare_virtus_normalized = normalize_name("Virtus")
        for canonical, _, aliases, _ in LBA_TEAMS_SEED:
            for alias in aliases:
                assert normalize_name(alias) != bare_virtus_normalized, (
                    f"Found bare 'Virtus' alias on {canonical!r}. "
                    "Within-Italy basketball collision risk — always "
                    "qualify with city."
                )


# ══════════════════════════════════════════════════════════════
# Day-28/Day-30 discovery target coverage tests
# ══════════════════════════════════════════════════════════════


class TestLBADiscoveryTargets:
    """Verify that in-scope production-form strings from Day-28/Day-30's
    asymmetric_anchor_failure discovery query are covered as aliases.

    Out-of-scope provider forms (Fortitudo Bologna, Verona, Virtus Gvm
    Roma 1960, Rucker San Vendemiano) are intentionally absent — they
    play Serie A2/B, not LBA Serie A 2025-26."""

    DAY_28_30_TARGETS = [
        ("Brescia", 28),
        ("Brescia *", 14),
        ("Trieste", 28),
        ("Olimpia Milano", 28),
        ("Reggiana", 28),
        ("Tortona", 14),
        ("Treviso", 14),
        ("Cantu", 14),
        ("Basket Napoli", 14),
        ("Sassari", 14),
    ]

    def test_discovery_target_strings_are_aliases(self):
        from resolver._normalize import normalize_name
        from scripts.lba_seed import LBA_TEAMS_SEED
        all_normalized_aliases = set()
        for _, _, aliases, _ in LBA_TEAMS_SEED:
            for alias in aliases:
                normalized = normalize_name(alias)
                if normalized:
                    all_normalized_aliases.add(normalized)
        for target_string, records_per_week in self.DAY_28_30_TARGETS:
            normalized = normalize_name(target_string)
            ctx = (
                f" ({records_per_week}/week)"
                if records_per_week else ""
            )
            assert normalized in all_normalized_aliases, (
                f"Day-28/30 target {target_string!r}{ctx} "
                f"not found in manifest aliases"
            )


# ══════════════════════════════════════════════════════════════
# Roster-membership tests (Wikipedia 2025-26 LBA Serie A operator paste)
# ══════════════════════════════════════════════════════════════


class TestLBARosterMembership:
    """All 16 operator-pasted Wikipedia roster teams present as
    canonicals. Out-of-scope teams (Fortitudo Bologna, Verona /
    Scaligera, Virtus Roma 1960, Rucker San Vendemiano) absent."""

    EXPECTED_CANONICALS = {
        "Aquila Basket Trento",
        "APU Udine",
        "Derthona Basket",
        "Dinamo Sassari",
        "Napoli Basket",
        "Olimpia Milano",
        "Pallacanestro Brescia",
        "Pallacanestro Cantù",
        "Pallacanestro Reggiana",
        "Pallacanestro Trieste 2004",
        "Pallacanestro Varese",
        "Reyer Venezia",
        "Trapani Shark",
        "Universo Treviso Basket",
        "Vanoli Cremona",
        "Virtus Bologna",
    }

    OUT_OF_SCOPE_CANONICALS = {
        "Fortitudo Bologna",
        "Tezenis Verona",
        "Scaligera Verona",
        "Scaligera Basket Verona",
        "Virtus Roma 1960",
        "Virtus Gvm Roma 1960",
        "Rucker San Vendemiano",
    }

    def test_expected_canonicals_all_present(self):
        from scripts.lba_seed import LBA_TEAMS_SEED
        canonicals = {c for c, _, _, _ in LBA_TEAMS_SEED}
        missing = self.EXPECTED_CANONICALS - canonicals
        assert not missing, (
            f"Operator-pasted roster teams missing from manifest: {missing}"
        )

    def test_out_of_scope_canonicals_absent(self):
        from scripts.lba_seed import LBA_TEAMS_SEED
        canonicals = {c for c, _, _, _ in LBA_TEAMS_SEED}
        present_oos = self.OUT_OF_SCOPE_CANONICALS & canonicals
        assert not present_oos, (
            f"Serie A2/B teams found in LBA Serie A manifest "
            f"(scope violation): {present_oos}"
        )
