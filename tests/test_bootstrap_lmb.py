"""Tests for LMB bootstrap (Phase 2D.5-A data-driven league bootstrap).

Mirrors tests/test_bootstrap_kbl.py shape:

  - Manifest-shape unit tests (always run; no DB required)
  - Alias distinctiveness per sport_id partition
  - Diacritic variant coverage
  - Source-value convention
"""
from __future__ import annotations

import os
from collections import Counter
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent


# ══════════════════════════════════════════════════════════════
# Manifest-shape unit tests (no DB)
# ══════════════════════════════════════════════════════════════


class TestLMBManifestShape:
    """Pure-data validation of LMB_TEAMS_SEED + LMB_ALIAS_SOURCE."""

    def test_manifest_imports_cleanly(self):
        from scripts.lmb_seed import LMB_ALIAS_SOURCE, LMB_TEAMS_SEED
        assert isinstance(LMB_TEAMS_SEED, list)
        assert isinstance(LMB_ALIAS_SOURCE, str)

    def test_manifest_size_is_20(self):
        """2026 LMB has 20 teams (10 Norte + 10 Sur). Verified against
        Posta Deportes April 2026 authoritative source."""
        from scripts.lmb_seed import LMB_TEAMS_SEED
        assert len(LMB_TEAMS_SEED) == 20, (
            f"Expected 20 LMB teams; got {len(LMB_TEAMS_SEED)}. "
            "If LMB roster changed, update this test alongside lmb_seed.py."
        )

    def test_all_entries_are_4_tuples(self):
        from scripts.lmb_seed import LMB_TEAMS_SEED
        for entry in LMB_TEAMS_SEED:
            assert isinstance(entry, tuple)
            assert len(entry) == 4, f"Wrong arity: {entry!r}"
            canonical, country, aliases, _notes = entry
            assert isinstance(canonical, str) and canonical
            assert isinstance(country, str) and country
            assert isinstance(aliases, tuple)

    def test_all_country_codes_are_mex(self):
        """LMB is a Mexican league; every entry must be MEX."""
        from scripts.lmb_seed import LMB_TEAMS_SEED
        for canonical, country, _aliases, _notes in LMB_TEAMS_SEED:
            assert country == "MEX", (
                f"Team {canonical!r} has country_code={country!r}; "
                "expected 'MEX'"
            )

    def test_all_canonical_names_normalize_to_nonempty(self):
        from resolver._normalize import normalize_name
        from scripts.lmb_seed import LMB_TEAMS_SEED
        for canonical, _country, _aliases, _notes in LMB_TEAMS_SEED:
            normalized = normalize_name(canonical)
            assert normalized, (
                f"Canonical name {canonical!r} normalizes to empty"
            )

    def test_all_aliases_normalize_to_nonempty(self):
        from resolver._normalize import normalize_name
        from scripts.lmb_seed import LMB_TEAMS_SEED
        for canonical, _country, aliases, _notes in LMB_TEAMS_SEED:
            for alias in aliases:
                normalized = normalize_name(alias)
                assert normalized, (
                    f"Alias {alias!r} (on team {canonical!r}) "
                    "normalizes to empty"
                )

    def test_no_duplicate_canonical_names(self):
        from scripts.lmb_seed import LMB_TEAMS_SEED
        canonicals = [c for c, _, _, _ in LMB_TEAMS_SEED]
        assert len(canonicals) == len(set(canonicals)), (
            "Duplicate canonical_name in manifest"
        )

    def test_no_within_league_alias_collisions(self):
        """No two DIFFERENT LMB teams share the same normalized alias.
        Within-league collision would cause ambiguous strict-tier
        lookups (resolver/aliases.py:115-119 returns None on
        ambiguous keys).

        Same-team diacritic pairs (e.g., "Unión Laguna" + "Union Laguna"
        both normalizing to "union laguna") are NOT collisions — they're
        belt-and-suspenders aliases on the same team."""
        from resolver._normalize import normalize_name
        from scripts.lmb_seed import LMB_TEAMS_SEED
        # Map each normalized alias → set of canonical_names that own it
        alias_owners: dict[str, set[str]] = {}
        for canonical, _country, aliases, _notes in LMB_TEAMS_SEED:
            for alias in aliases:
                normalized = normalize_name(alias)
                if normalized:
                    alias_owners.setdefault(normalized, set()).add(canonical)
        # Collision = same normalized alias owned by 2+ different teams
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
        from scripts.lmb_seed import LMB_ALIAS_SOURCE
        assert LMB_ALIAS_SOURCE == "bootstrap_league_coverage"


# ══════════════════════════════════════════════════════════════
# Diacritic coverage tests
# ══════════════════════════════════════════════════════════════


class TestLMBDiacriticCoverage:
    """Every accented canonical must have an ASCII-stripped alias.
    The normalizer strips accents (NFD decomposition), so both forms
    resolve to the same normalized key — but including both in the
    manifest is belt-and-suspenders."""

    ACCENTED_TEAMS = [
        ("Algodoneros de Unión Laguna", "Union Laguna"),
        ("Bravos de León", "Leon"),
        ("Leones de Yucatán", "Yucatan"),
        ("Diablos Rojos del México", "Mexico"),
        ("Águilas de Mexicali", None),  # removed from 2026; skip
        ("El Águila de Veracruz", "Aguila"),
        ("Conspiradores de Querétaro", "Queretaro"),
    ]

    def test_accented_teams_have_ascii_variant(self):
        from scripts.lmb_seed import LMB_TEAMS_SEED
        manifest_map = {
            canonical: aliases
            for canonical, _, aliases, _ in LMB_TEAMS_SEED
        }
        for canonical, expected_ascii in self.ACCENTED_TEAMS:
            if expected_ascii is None:
                continue
            if canonical not in manifest_map:
                continue
            aliases = manifest_map[canonical]
            assert expected_ascii in aliases, (
                f"Team {canonical!r} missing ASCII variant "
                f"{expected_ascii!r} in aliases {aliases!r}"
            )


# ══════════════════════════════════════════════════════════════
# Day-27 target coverage tests
# ══════════════════════════════════════════════════════════════


class TestLMBDay27Targets:
    """Verify that the top unresolved strings from Day-27's
    asymmetric_anchor_failure query are covered as aliases."""

    DAY_27_TARGETS = [
        ("Monterrey", 182),
        ("Puebla", 161),
        ("Queretaro", 161),
        ("Tabasco", 76),
    ]

    def test_day27_target_strings_are_aliases(self):
        from resolver._normalize import normalize_name
        from scripts.lmb_seed import LMB_TEAMS_SEED
        all_normalized_aliases = set()
        for _, _, aliases, _ in LMB_TEAMS_SEED:
            for alias in aliases:
                normalized = normalize_name(alias)
                if normalized:
                    all_normalized_aliases.add(normalized)
        for target_string, records_per_week in self.DAY_27_TARGETS:
            normalized = normalize_name(target_string)
            assert normalized in all_normalized_aliases, (
                f"Day-27 target {target_string!r} ({records_per_week}/week) "
                f"not found in manifest aliases"
            )

    def test_tigres_bare_excluded(self):
        """'Tigres' as a bare alias is explicitly excluded per F2
        distinctiveness — collides with Tigres de Quintana Roo within
        the same Baseball sport_id."""
        from scripts.lmb_seed import LMB_TEAMS_SEED
        for _, _, aliases, _ in LMB_TEAMS_SEED:
            for alias in aliases:
                assert alias.strip().lower() != "tigres", (
                    f"Bare 'Tigres' alias found — excluded per F2 "
                    f"within-league collision rule"
                )
