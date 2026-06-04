"""Tests for Greek HEBA A1 bootstrap (Phase 2D.5-A workstream #6).

Mirrors tests/test_bootstrap_turkish_bsl.py shape with HEBA-specific
discipline: Greek transliteration pairs (Olympiakos/Olympiacos,
Kolossos Rhodes/Kolossos Rodou), 4 INSERT + 9 BACKFILL composition
with documented Phase 2A.5 UUIDs in notes, dormant phantom risk
documentation, F2 NEW empirical-coverage discipline (bare club aliases
INCLUDED for football-overlap teams).
"""
from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


# ══════════════════════════════════════════════════════════════
# Manifest-shape unit tests (no DB)
# ══════════════════════════════════════════════════════════════


class TestHEBAManifestShape:
    """Pure-data validation of HEBA_TEAMS_SEED + HEBA_ALIAS_SOURCE."""

    def test_manifest_imports_cleanly(self):
        from scripts.heba_seed import HEBA_ALIAS_SOURCE, HEBA_TEAMS_SEED
        assert isinstance(HEBA_TEAMS_SEED, list)
        assert isinstance(HEBA_ALIAS_SOURCE, str)

    def test_manifest_size_is_13(self):
        """2025-26 Greek HEBA A1 has 13 teams (unusual count; typical
        12 or 14). Verified against operator's Day-33 paste from
        Wikipedia 2025-26 Greek Basket League season roster."""
        from scripts.heba_seed import HEBA_TEAMS_SEED
        assert len(HEBA_TEAMS_SEED) == 13, (
            f"Expected 13 HEBA teams; got {len(HEBA_TEAMS_SEED)}. "
            "If roster changed, update this test alongside heba_seed.py."
        )

    def test_all_entries_are_4_tuples(self):
        from scripts.heba_seed import HEBA_TEAMS_SEED
        for entry in HEBA_TEAMS_SEED:
            assert isinstance(entry, tuple)
            assert len(entry) == 4, f"Wrong arity: {entry!r}"
            canonical, country, aliases, _notes = entry
            assert isinstance(canonical, str) and canonical
            assert isinstance(country, str) and country
            assert isinstance(aliases, tuple)

    def test_country_codes_are_all_grc(self):
        """HEBA A1 is a single-country Greek league. All teams GRC."""
        from scripts.heba_seed import HEBA_TEAMS_SEED
        for canonical, country, _aliases, _notes in HEBA_TEAMS_SEED:
            assert country == "GRC", (
                f"Team {canonical!r} has country_code={country!r}; "
                "expected 'GRC'"
            )

    def test_all_canonical_names_normalize_to_nonempty(self):
        from resolver._normalize import normalize_name
        from scripts.heba_seed import HEBA_TEAMS_SEED
        for canonical, _country, _aliases, _notes in HEBA_TEAMS_SEED:
            assert normalize_name(canonical), (
                f"Canonical name {canonical!r} normalizes to empty"
            )

    def test_all_aliases_normalize_to_nonempty(self):
        from resolver._normalize import normalize_name
        from scripts.heba_seed import HEBA_TEAMS_SEED
        for canonical, _country, aliases, _notes in HEBA_TEAMS_SEED:
            for alias in aliases:
                assert normalize_name(alias), (
                    f"Alias {alias!r} (on team {canonical!r}) "
                    "normalizes to empty"
                )

    def test_no_duplicate_canonical_names(self):
        from scripts.heba_seed import HEBA_TEAMS_SEED
        canonicals = [c for c, _, _, _ in HEBA_TEAMS_SEED]
        assert len(canonicals) == len(set(canonicals)), (
            "Duplicate canonical_name in manifest"
        )

    def test_no_within_league_alias_collisions(self):
        """No two DIFFERENT HEBA teams share the same normalized
        alias. Same-team duplicates (e.g., AEK Athens + AEK Athens *
        both normalize to 'aek athens') are not collisions —
        belt-and-suspenders within a single team."""
        from resolver._normalize import normalize_name
        from scripts.heba_seed import HEBA_TEAMS_SEED
        alias_owners: dict[str, set[str]] = {}
        for canonical, _country, aliases, _notes in HEBA_TEAMS_SEED:
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
        from scripts.heba_seed import HEBA_ALIAS_SOURCE
        assert HEBA_ALIAS_SOURCE == "bootstrap_league_coverage"


# ══════════════════════════════════════════════════════════════
# Greek transliteration coverage tests
# ══════════════════════════════════════════════════════════════


class TestHEBADiacriticCoverage:
    """Greek-to-Latin transliteration produces multiple valid forms
    that do NOT collapse under NFD (cross-script). Manifest must
    enumerate both forms per F3."""

    # (form_a, form_b) — both must be present in manifest (canonical
    # or alias) on the SAME team
    GREEK_TRANSLITERATION_PAIRS = [
        ("Olympiakos", "Olympiacos"),  # both on Olympiakos BC
        ("Kolossos Rhodes", "Kolossos Rodou"),  # both on Kolossos Rhodes
    ]

    def test_transliteration_pairs_both_present_on_same_team(self):
        from scripts.heba_seed import HEBA_TEAMS_SEED
        manifest_map = {
            canonical: tuple(aliases)
            for canonical, _, aliases, _ in HEBA_TEAMS_SEED
        }
        for form_a, form_b in self.GREEK_TRANSLITERATION_PAIRS:
            # Find which canonical owns this pair
            owner = None
            for canonical, aliases in manifest_map.items():
                if form_a in aliases or canonical == form_a:
                    owner = canonical
                    break
            assert owner is not None, (
                f"Transliteration form {form_a!r} not found in any "
                "manifest team's canonical or aliases"
            )
            aliases = manifest_map[owner]
            assert (
                form_b in aliases or owner == form_b
            ), (
                f"Team {owner!r} has {form_a!r} but missing "
                f"transliteration pair {form_b!r}"
            )


# ══════════════════════════════════════════════════════════════
# Day-32 discovery target coverage tests
# ══════════════════════════════════════════════════════════════


class TestHEBADay32Targets:
    """Verify that Day-32 afternoon discovery provider forms are
    covered as canonicals or aliases on the manifest."""

    DAY_32_TARGETS_PRESENT = [
        "AEK Athens",
        "AEK",
        "BC AEK Athens",
        "AEK Athens *",
        "Olympiacos",
        "Olympiakos",
        "BC Olympiakos Piraeus",
        "Olympiacos *",
        "Aris",
        "BC Aris Thessaloniki",
        "Kolossos Rhodes",
        "BC Kolossos Rhodes",
        "Panathinaikos",
        "Panathinaikos BC",
        "PAOK",
    ]

    BARE_CITY_FORMS_EXCLUDED = [
        "Athens",
        "Thessaloniki",
        "Piraeus",
        "Patras",
        "Marousi",
        "Rhodes",
        "BC",
    ]

    def test_day32_targets_are_covered(self):
        from resolver._normalize import normalize_name
        from scripts.heba_seed import HEBA_TEAMS_SEED
        covered_normalized = set()
        for canonical, _, aliases, _ in HEBA_TEAMS_SEED:
            covered_normalized.add(normalize_name(canonical))
            for alias in aliases:
                n = normalize_name(alias)
                if n:
                    covered_normalized.add(n)
        for target in self.DAY_32_TARGETS_PRESENT:
            normalized = normalize_name(target)
            assert normalized in covered_normalized, (
                f"Day-32 discovery target {target!r} (normalized "
                f"{normalized!r}) not covered by manifest"
            )

    def test_bare_city_forms_absent_as_aliases(self):
        """Bare city forms must NOT appear as standalone aliases —
        too generic, cross-sport collision risk."""
        from resolver._normalize import normalize_name
        from scripts.heba_seed import HEBA_TEAMS_SEED
        excluded_normalized = {
            normalize_name(c) for c in self.BARE_CITY_FORMS_EXCLUDED
        }
        for canonical, _, aliases, _ in HEBA_TEAMS_SEED:
            for alias in aliases:
                assert normalize_name(alias) not in excluded_normalized, (
                    f"Found bare city alias {alias!r} on team "
                    f"{canonical!r}. Bare city forms "
                    f"{self.BARE_CITY_FORMS_EXCLUDED!r} are excluded "
                    "per F2 discipline (cross-sport collision risk)."
                )


# ══════════════════════════════════════════════════════════════
# BACKFILL UUID documentation tests
# ══════════════════════════════════════════════════════════════


class TestHEBABackfillUUIDs:
    """9 manifest canonicals are expected to BACKFILL onto Phase 2A.5
    legacy Basketball stubs at apply time. UUIDs documented in
    canonical notes per scope-doc §6 and amendment #22 pre-apply
    audit preparation."""

    BACKFILL_CANONICALS_WITH_UUIDS = {
        "Iraklis BC": "c17fa0b9-bad0-4027-9a96-8f50584873fb",
        "Kolossos Rhodes": "ca5f6d4a-f75d-45ea-8a26-e610b40dbf31",
        "Maroussi BC": "d8e37aa5-bfd3-4555-b5a1-f6173b034d12",
        "Mykonos": "2f32272a-a077-43db-a024-75326f688acd",
        "PAOK BC": "59eb93a6-fa3c-44f1-80c0-a67c5783352a",
        "Panathinaikos BC": "6e1268f8-46dc-431d-a38c-9f0924c6922b",
        "Panionios": "380f47bc-1057-4030-9064-f8896dc6e779",
        "Peristeri BC": "6a00a818-b27a-4cbe-b1b5-dfd2a7364a9c",
        "Promitheas Patras BC Vikos Cola":
            "eb0e7a18-7498-46aa-bf13-06b38c190795",
    }

    INSERT_CANONICALS = {
        "AEK Athens",
        "Aris Thessaloniki",
        "Olympiakos BC",
        "GS Karditsa",
    }

    def test_backfill_canonicals_all_present(self):
        from scripts.heba_seed import HEBA_TEAMS_SEED
        canonicals = {c for c, _, _, _ in HEBA_TEAMS_SEED}
        missing = set(self.BACKFILL_CANONICALS_WITH_UUIDS) - canonicals
        assert not missing, (
            f"BACKFILL canonicals missing from manifest: {missing}"
        )

    def test_insert_canonicals_all_present(self):
        from scripts.heba_seed import HEBA_TEAMS_SEED
        canonicals = {c for c, _, _, _ in HEBA_TEAMS_SEED}
        missing = self.INSERT_CANONICALS - canonicals
        assert not missing, (
            f"INSERT canonicals missing from manifest: {missing}"
        )

    def test_backfill_uuids_documented_in_notes(self):
        """Each BACKFILL canonical's notes field documents its Phase
        2A.5 legacy UUID for amendment #22 pre-apply audit."""
        from scripts.heba_seed import HEBA_TEAMS_SEED
        notes_by_canonical = {
            c: notes for c, _, _, notes in HEBA_TEAMS_SEED
        }
        for canonical, uuid_str in self.BACKFILL_CANONICALS_WITH_UUIDS.items():
            notes = notes_by_canonical.get(canonical, "")
            assert uuid_str in notes, (
                f"BACKFILL canonical {canonical!r} missing UUID "
                f"{uuid_str!r} documentation in notes field"
            )

    def test_composition_is_4_insert_plus_9_backfill(self):
        """Total = 13 teams = 4 INSERT + 9 BACKFILL."""
        assert len(self.INSERT_CANONICALS) == 4
        assert len(self.BACKFILL_CANONICALS_WITH_UUIDS) == 9
        assert (
            len(self.INSERT_CANONICALS)
            + len(self.BACKFILL_CANONICALS_WITH_UUIDS) == 13
        )


# ══════════════════════════════════════════════════════════════
# Cross-sport collision empirical-coverage discipline (F2 NEW)
# ══════════════════════════════════════════════════════════════


class TestHEBACrossSportCollisionPolicy:
    """Per F2 NEW empirical-coverage discipline (Turkish BSL #5),
    bare club names INCLUDED for football-overlap teams when
    Day-32 discovery shows production strings ARE the bare forms.
    Day-22 sport_id partition validates matcher-layer safety."""

    BARE_INCLUSIONS = [
        ("Olympiakos", "Olympiakos BC"),
        ("Olympiacos", "Olympiakos BC"),
        ("Panathinaikos", "Panathinaikos BC"),
        ("AEK", "AEK Athens"),
        ("AEK Athens", "AEK Athens"),
        ("PAOK", "PAOK BC"),
        ("Aris", "Aris Thessaloniki"),
    ]

    def test_bare_club_aliases_present_on_football_overlap_teams(self):
        from scripts.heba_seed import HEBA_TEAMS_SEED
        manifest_map = {
            canonical: tuple(aliases)
            for canonical, _, aliases, _ in HEBA_TEAMS_SEED
        }
        for bare_form, owning_canonical in self.BARE_INCLUSIONS:
            assert owning_canonical in manifest_map, (
                f"Owning canonical {owning_canonical!r} not in manifest"
            )
            aliases = manifest_map[owning_canonical]
            present = (
                owning_canonical == bare_form or bare_form in aliases
            )
            assert present, (
                f"Bare form {bare_form!r} not present on "
                f"{owning_canonical!r} aliases — F2 NEW empirical-"
                "coverage discipline requires inclusion"
            )


# ══════════════════════════════════════════════════════════════
# Roster-membership tests (Wikipedia 2025-26 HEBA A1 operator paste)
# ══════════════════════════════════════════════════════════════


class TestHEBARosterMembership:
    """All 13 operator-pasted Wikipedia 2025-26 Greek Basket League
    season roster teams present as canonicals."""

    EXPECTED_CANONICALS = {
        "AEK Athens",
        "Aris Thessaloniki",
        "Olympiakos BC",
        "GS Karditsa",
        "Iraklis BC",
        "Kolossos Rhodes",
        "Maroussi BC",
        "Mykonos",
        "PAOK BC",
        "Panathinaikos BC",
        "Panionios",
        "Peristeri BC",
        "Promitheas Patras BC Vikos Cola",
    }

    def test_expected_canonicals_all_present(self):
        from scripts.heba_seed import HEBA_TEAMS_SEED
        canonicals = {c for c, _, _, _ in HEBA_TEAMS_SEED}
        missing = self.EXPECTED_CANONICALS - canonicals
        assert not missing, (
            f"Operator-pasted roster teams missing from manifest: {missing}"
        )
