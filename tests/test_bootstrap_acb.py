"""Tests for Liga ACB bootstrap (Phase 2D.5-A data-driven league bootstrap).

Mirrors tests/test_bootstrap_lmb.py shape:

  - Manifest-shape unit tests (always run; no DB required)
  - Alias distinctiveness per sport_id partition
  - Diacritic variant coverage
  - Source-value convention
  - Cross-sport collision notes (Real Madrid, Barcelona)
  - Day-27 target coverage
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent


# ══════════════════════════════════════════════════════════════
# Manifest-shape unit tests (no DB)
# ══════════════════════════════════════════════════════════════


class TestLigaACBManifestShape:
    """Pure-data validation of LIGA_ACB_TEAMS_SEED + LIGA_ACB_ALIAS_SOURCE."""

    def test_manifest_imports_cleanly(self):
        from scripts.liga_acb_seed import (
            LIGA_ACB_ALIAS_SOURCE, LIGA_ACB_TEAMS_SEED,
        )
        assert isinstance(LIGA_ACB_TEAMS_SEED, list)
        assert isinstance(LIGA_ACB_ALIAS_SOURCE, str)

    def test_manifest_size_is_18(self):
        """2025-26 Liga ACB has 18 teams. Verified against operator's
        Wikipedia 2025-26 ACB season roster."""
        from scripts.liga_acb_seed import LIGA_ACB_TEAMS_SEED
        assert len(LIGA_ACB_TEAMS_SEED) == 18, (
            f"Expected 18 Liga ACB teams; got {len(LIGA_ACB_TEAMS_SEED)}. "
            "If roster changed, update this test alongside liga_acb_seed.py."
        )

    def test_all_entries_are_4_tuples(self):
        from scripts.liga_acb_seed import LIGA_ACB_TEAMS_SEED
        for entry in LIGA_ACB_TEAMS_SEED:
            assert isinstance(entry, tuple)
            assert len(entry) == 4, f"Wrong arity: {entry!r}"
            canonical, country, aliases, _notes = entry
            assert isinstance(canonical, str) and canonical
            assert isinstance(country, str) and country
            assert isinstance(aliases, tuple)

    def test_country_codes_are_esp_or_and(self):
        """Liga ACB is a Spanish league; teams are ESP except BC Andorra
        which is AND (Andorran club competing in Spanish league per
        sp.teams geographic-residence convention)."""
        from scripts.liga_acb_seed import LIGA_ACB_TEAMS_SEED
        for canonical, country, _aliases, _notes in LIGA_ACB_TEAMS_SEED:
            assert country in ("ESP", "AND"), (
                f"Team {canonical!r} has country_code={country!r}; "
                "expected 'ESP' or 'AND'"
            )

    def test_andorra_is_and_country_code(self):
        """BC Andorra must be country_code='AND' (not ESP). Geographic
        residence, not competitive league."""
        from scripts.liga_acb_seed import LIGA_ACB_TEAMS_SEED
        andorra_entries = [
            (canonical, country)
            for canonical, country, _, _ in LIGA_ACB_TEAMS_SEED
            if "Andorra" in canonical
        ]
        assert len(andorra_entries) == 1, (
            f"Expected 1 Andorran team; got {len(andorra_entries)}"
        )
        canonical, country = andorra_entries[0]
        assert country == "AND", (
            f"Andorran club {canonical!r} has country={country!r}; "
            "expected 'AND'"
        )

    def test_all_canonical_names_normalize_to_nonempty(self):
        from resolver._normalize import normalize_name
        from scripts.liga_acb_seed import LIGA_ACB_TEAMS_SEED
        for canonical, _country, _aliases, _notes in LIGA_ACB_TEAMS_SEED:
            normalized = normalize_name(canonical)
            assert normalized, (
                f"Canonical name {canonical!r} normalizes to empty"
            )

    def test_all_aliases_normalize_to_nonempty(self):
        from resolver._normalize import normalize_name
        from scripts.liga_acb_seed import LIGA_ACB_TEAMS_SEED
        for canonical, _country, aliases, _notes in LIGA_ACB_TEAMS_SEED:
            for alias in aliases:
                normalized = normalize_name(alias)
                assert normalized, (
                    f"Alias {alias!r} (on team {canonical!r}) "
                    "normalizes to empty"
                )

    def test_no_duplicate_canonical_names(self):
        from scripts.liga_acb_seed import LIGA_ACB_TEAMS_SEED
        canonicals = [c for c, _, _, _ in LIGA_ACB_TEAMS_SEED]
        assert len(canonicals) == len(set(canonicals)), (
            "Duplicate canonical_name in manifest"
        )

    def test_no_within_league_alias_collisions(self):
        """No two DIFFERENT Liga ACB teams share the same normalized
        alias. Within-league collision would cause ambiguous strict-tier
        lookups (resolver/aliases.py:115-119 returns None on
        ambiguous keys).

        Same-team diacritic pairs (e.g., "Málaga" + "Malaga" both
        normalizing to "malaga") are NOT collisions — they're
        belt-and-suspenders aliases on the same team."""
        from resolver._normalize import normalize_name
        from scripts.liga_acb_seed import LIGA_ACB_TEAMS_SEED
        alias_owners: dict[str, set[str]] = {}
        for canonical, _country, aliases, _notes in LIGA_ACB_TEAMS_SEED:
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
        cohort-wide convention (kbl_seed.py docstring)."""
        from scripts.liga_acb_seed import LIGA_ACB_ALIAS_SOURCE
        assert LIGA_ACB_ALIAS_SOURCE == "bootstrap_league_coverage"


# ══════════════════════════════════════════════════════════════
# Diacritic coverage tests
# ══════════════════════════════════════════════════════════════


class TestLigaACBDiacriticCoverage:
    """Every accented canonical must have an ASCII-stripped alias.
    The normalizer strips accents (NFD decomposition), so both forms
    resolve to the same normalized key — but including both in the
    manifest is belt-and-suspenders."""

    ACCENTED_TEAMS = [
        ("Bàsquet Girona", "Basquet Girona"),
        ("Bàsquet Manresa", "Basquet Manresa"),
        ("FC Barcelona Bàsquet", "FC Barcelona Basquet"),
        ("Força Lleida CE", "Forca Lleida CE"),
        ("Fundación CB Granada", "Fundacion CB Granada"),
        ("Río Breogán", "Rio Breogan"),
        ("Unicaja Málaga", "Unicaja Malaga"),
    ]

    def test_accented_teams_have_ascii_variant(self):
        from scripts.liga_acb_seed import LIGA_ACB_TEAMS_SEED
        manifest_map = {
            canonical: aliases
            for canonical, _, aliases, _ in LIGA_ACB_TEAMS_SEED
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


class TestLigaACBCrossSportCollision:
    """Real Madrid Baloncesto + FC Barcelona Bàsquet have cross-sport
    name overlap with soccer canonicals likely already in sp.teams.
    Sport-historical canonicals + bare aliases under sport_id partition
    is the discipline (Day-22 finding)."""

    def test_real_madrid_canonical_is_basketball_specific(self):
        """Canonical must be 'Real Madrid Baloncesto' (not 'Real Madrid')
        to distinguish from the soccer canonical."""
        from scripts.liga_acb_seed import LIGA_ACB_TEAMS_SEED
        canonicals = [c for c, _, _, _ in LIGA_ACB_TEAMS_SEED]
        assert "Real Madrid Baloncesto" in canonicals, (
            "Expected 'Real Madrid Baloncesto' canonical for basketball "
            "team — soccer canonical 'Real Madrid' likely exists"
        )
        assert "Real Madrid" not in canonicals, (
            "Found 'Real Madrid' as canonical — should be alias only, "
            "with 'Real Madrid Baloncesto' as canonical"
        )

    def test_fc_barcelona_canonical_is_basketball_specific(self):
        """Canonical must be 'FC Barcelona Bàsquet' (not 'FC Barcelona'
        or 'Barcelona') to distinguish from the soccer canonical."""
        from scripts.liga_acb_seed import LIGA_ACB_TEAMS_SEED
        canonicals = [c for c, _, _, _ in LIGA_ACB_TEAMS_SEED]
        assert "FC Barcelona Bàsquet" in canonicals, (
            "Expected 'FC Barcelona Bàsquet' canonical for basketball team"
        )
        assert "FC Barcelona" not in canonicals
        assert "Barcelona" not in canonicals

    def test_real_madrid_bare_alias_present(self):
        """'Real Madrid' must be an alias on Real Madrid Baloncesto
        (sport_id partition makes bare alias safe under basketball)."""
        from scripts.liga_acb_seed import LIGA_ACB_TEAMS_SEED
        for canonical, _, aliases, _ in LIGA_ACB_TEAMS_SEED:
            if canonical == "Real Madrid Baloncesto":
                assert "Real Madrid" in aliases, (
                    f"Real Madrid Baloncesto missing 'Real Madrid' bare alias"
                )
                return
        pytest.fail("Real Madrid Baloncesto not found in manifest")

    def test_madrid_bare_alias_intentionally_excluded(self):
        """'Madrid' bare alias must NOT exist on Real Madrid Baloncesto.
        Too generic — risks collision with future Madrid-area basketball
        clubs (Estudiantes if they return to ACB)."""
        from scripts.liga_acb_seed import LIGA_ACB_TEAMS_SEED
        for canonical, _, aliases, _ in LIGA_ACB_TEAMS_SEED:
            if canonical == "Real Madrid Baloncesto":
                assert "Madrid" not in aliases, (
                    f"Found bare 'Madrid' alias on Real Madrid Baloncesto. "
                    f"Intentionally excluded per F2 — too generic."
                )
                return

    def test_barcelona_bare_alias_present(self):
        """'Barcelona' must be an alias on FC Barcelona Bàsquet
        (sport_id partition makes bare alias safe under basketball)."""
        from scripts.liga_acb_seed import LIGA_ACB_TEAMS_SEED
        for canonical, _, aliases, _ in LIGA_ACB_TEAMS_SEED:
            if canonical == "FC Barcelona Bàsquet":
                assert "Barcelona" in aliases, (
                    f"FC Barcelona Bàsquet missing 'Barcelona' bare alias"
                )
                return
        pytest.fail("FC Barcelona Bàsquet not found in manifest")


# ══════════════════════════════════════════════════════════════
# Day-27 target coverage tests
# ══════════════════════════════════════════════════════════════


class TestLigaACBDay27Targets:
    """Verify that production-form strings from Day-27's
    asymmetric_anchor_failure query are covered as aliases."""

    DAY_27_TARGETS = [
        ("Real Madrid", 35),
        ("Barcelona", None),  # opponent of Real Madrid
        ("Baskonia", None),
        ("Joventut Badalona", None),
        ("CB Gran Canaria", None),
        ("CB 1939 Canarias", None),
        ("CB San Pablo Burgos", None),
        ("Basket Zaragoza", None),
        ("Basket Zaragoza 2002", None),  # production-only form
        ("Caprabo Lleida", None),  # old sponsor in production data
        ("Granada", None),
    ]

    def test_day27_target_strings_are_aliases(self):
        from resolver._normalize import normalize_name
        from scripts.liga_acb_seed import LIGA_ACB_TEAMS_SEED
        all_normalized_aliases = set()
        for _, _, aliases, _ in LIGA_ACB_TEAMS_SEED:
            for alias in aliases:
                normalized = normalize_name(alias)
                if normalized:
                    all_normalized_aliases.add(normalized)
        for target_string, records_per_week in self.DAY_27_TARGETS:
            normalized = normalize_name(target_string)
            ctx = (
                f" ({records_per_week}/week)"
                if records_per_week else ""
            )
            assert normalized in all_normalized_aliases, (
                f"Day-27 target {target_string!r}{ctx} "
                f"not found in manifest aliases"
            )
