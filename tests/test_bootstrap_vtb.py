"""Tests for Russian VTB United League bootstrap (Phase 2D.5-A
workstream #7).

Mirrors tests/test_bootstrap_heba.py shape with VTB-specific
discipline: Russian-to-Latin transliteration spelling variants
(Yekaterinburg/Ekaterinburg, UNICS/Uniks, Khimki/Khimki M.),
F2 NEW empirical-coverage discipline (CSKA Moscow bare INCLUDED;
Zenit bare EXCLUDED), 5 INSERT + 6 BACKFILL composition with
documented Phase 2A.5 UUIDs in notes, dormant phantom risk
documentation, out-of-roster Khimki M. inclusion.
"""
from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


# ══════════════════════════════════════════════════════════════
# Manifest-shape unit tests (no DB)
# ══════════════════════════════════════════════════════════════


class TestVTBManifestShape:
    """Pure-data validation of VTB_TEAMS_SEED + VTB_ALIAS_SOURCE."""

    def test_manifest_imports_cleanly(self):
        from scripts.vtb_seed import VTB_ALIAS_SOURCE, VTB_TEAMS_SEED
        assert isinstance(VTB_TEAMS_SEED, list)
        assert isinstance(VTB_ALIAS_SOURCE, str)

    def test_manifest_size_is_11(self):
        """2025-26 VTB United League has 11 manifest teams (10
        Wikipedia roster + Khimki M. out-of-roster inclusion;
        BC Samara from Wikipedia roster excluded per operator's
        Day-34 spec — §6.5 follow-up)."""
        from scripts.vtb_seed import VTB_TEAMS_SEED
        assert len(VTB_TEAMS_SEED) == 11, (
            f"Expected 11 VTB teams; got {len(VTB_TEAMS_SEED)}. "
            "If roster changed, update this test alongside vtb_seed.py."
        )

    def test_all_entries_are_4_tuples(self):
        from scripts.vtb_seed import VTB_TEAMS_SEED
        for entry in VTB_TEAMS_SEED:
            assert isinstance(entry, tuple)
            assert len(entry) == 4, f"Wrong arity: {entry!r}"
            canonical, country, aliases, _notes = entry
            assert isinstance(canonical, str) and canonical
            assert isinstance(country, str) and country
            assert isinstance(aliases, tuple)

    def test_country_codes_are_all_rus(self):
        from scripts.vtb_seed import VTB_TEAMS_SEED
        for canonical, country, _aliases, _notes in VTB_TEAMS_SEED:
            assert country == "RUS", (
                f"Team {canonical!r} has country_code={country!r}; "
                "expected 'RUS'"
            )

    def test_all_canonical_names_normalize_to_nonempty(self):
        from resolver._normalize import normalize_name
        from scripts.vtb_seed import VTB_TEAMS_SEED
        for canonical, _country, _aliases, _notes in VTB_TEAMS_SEED:
            assert normalize_name(canonical), (
                f"Canonical name {canonical!r} normalizes to empty"
            )

    def test_all_aliases_normalize_to_nonempty(self):
        from resolver._normalize import normalize_name
        from scripts.vtb_seed import VTB_TEAMS_SEED
        for canonical, _country, aliases, _notes in VTB_TEAMS_SEED:
            for alias in aliases:
                assert normalize_name(alias), (
                    f"Alias {alias!r} (on team {canonical!r}) "
                    "normalizes to empty"
                )

    def test_no_duplicate_canonical_names(self):
        from scripts.vtb_seed import VTB_TEAMS_SEED
        canonicals = [c for c, _, _, _ in VTB_TEAMS_SEED]
        assert len(canonicals) == len(set(canonicals)), (
            "Duplicate canonical_name in manifest"
        )

    def test_no_within_league_alias_collisions(self):
        """No two DIFFERENT VTB teams share the same normalized alias."""
        from resolver._normalize import normalize_name
        from scripts.vtb_seed import VTB_TEAMS_SEED
        alias_owners: dict[str, set[str]] = {}
        for canonical, _country, aliases, _notes in VTB_TEAMS_SEED:
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
        from scripts.vtb_seed import VTB_ALIAS_SOURCE
        assert VTB_ALIAS_SOURCE == "bootstrap_league_coverage"


# ══════════════════════════════════════════════════════════════
# Spelling-variant coverage tests (transliteration handling)
# ══════════════════════════════════════════════════════════════


class TestVTBSpellingVariants:
    """Russian-to-Latin transliteration produces multiple valid forms
    that do NOT collapse under NFD (cross-script, not Latin-diacritic).
    Manifest must enumerate both forms per F3. Also Khimki M. (legacy
    canonical with trailing period) normalizes differently from bare
    'Khimki' (period stripped → 'khimki m' vs 'khimki')."""

    # (form_a, form_b) — both must be present in manifest on the
    # SAME team; both normalize to DIFFERENT keys
    SPELLING_VARIANT_PAIRS = [
        ("Yekaterinburg", "Ekaterinburg"),  # both on BC Uralmash Yekaterinburg
        ("UNICS", "Uniks"),  # both on UNICS Kazan
        ("Khimki M.", "Khimki"),  # both on Khimki M.
    ]

    def test_spelling_variants_normalize_to_different_keys(self):
        """Regression test: verify these variant pairs do NOT
        collapse under the current normalizer. If normalizer is
        enhanced to handle Russian transliteration consistently,
        update this test + remove belt-and-suspenders pairs."""
        from resolver._normalize import normalize_name
        for form_a, form_b in self.SPELLING_VARIANT_PAIRS:
            n_a = normalize_name(form_a)
            n_b = normalize_name(form_b)
            assert n_a != n_b, (
                f"Unexpected normalization match: {form_a!r}→{n_a!r} "
                f"vs {form_b!r}→{n_b!r}. If normalizer was enhanced, "
                "update this test + remove pairs from manifest."
            )

    def test_spelling_pairs_both_present_on_same_team(self):
        from scripts.vtb_seed import VTB_TEAMS_SEED
        manifest_map = {
            canonical: tuple(aliases)
            for canonical, _, aliases, _ in VTB_TEAMS_SEED
        }
        for form_a, form_b in self.SPELLING_VARIANT_PAIRS:
            owner = None
            for canonical, aliases in manifest_map.items():
                if form_a in aliases or canonical == form_a:
                    owner = canonical
                    break
                if form_b in aliases or canonical == form_b:
                    owner = canonical
                    break
            assert owner is not None, (
                f"Spelling variant {form_a!r} / {form_b!r} not found "
                "in any manifest team"
            )
            aliases = manifest_map[owner]
            assert (form_a in aliases or owner == form_a), (
                f"Team {owner!r} missing form {form_a!r}"
            )
            assert (form_b in aliases or owner == form_b), (
                f"Team {owner!r} missing form {form_b!r}"
            )


# ══════════════════════════════════════════════════════════════
# Day-34 discovery target coverage tests
# ══════════════════════════════════════════════════════════════


class TestVTBDay34Targets:
    """Verify that Day-34 discovery provider forms are covered as
    canonicals or aliases on the manifest."""

    DAY_34_TARGETS_PRESENT = [
        "BC Lokomotiv Kuban",
        "Lokomotiv Kuban",
        "CSKA Moscow",
        "CSKA Moscow *",
        "PBC CSKA Moscow",
        "BC Uniks Kazan",
        "Unics Kazan",
        "UNICS Kazan",
        "Enisey",
        "BC Enisey",
        "Khimki M.",
        "Khimki",
    ]

    # Bare forms or excluded discovery surface
    EXCLUDED_FORMS = [
        "Zenit",  # cross-sport collision (Zenit Saint Petersburg FC)
        "Moscow",  # within-VTB collision (CSKA + MBA both Moscow)
        "Kazan",  # too generic
        "Perm",  # too generic
        "Lokomotiv",  # too generic (Lokomotiv Moscow FC etc.)
        "Chelyabinsk",  # out-of-scope, non-VTB-roster
    ]

    def test_day34_targets_are_covered(self):
        from resolver._normalize import normalize_name
        from scripts.vtb_seed import VTB_TEAMS_SEED
        covered_normalized = set()
        for canonical, _, aliases, _ in VTB_TEAMS_SEED:
            covered_normalized.add(normalize_name(canonical))
            for alias in aliases:
                n = normalize_name(alias)
                if n:
                    covered_normalized.add(n)
        for target in self.DAY_34_TARGETS_PRESENT:
            normalized = normalize_name(target)
            assert normalized in covered_normalized, (
                f"Day-34 target {target!r} (normalized {normalized!r}) "
                "not covered by manifest"
            )

    def test_excluded_forms_absent_as_standalone_aliases(self):
        """Bare 'Zenit', 'Moscow', 'Kazan', 'Perm', 'Lokomotiv',
        'Chelyabinsk' must NOT appear as standalone aliases (too
        generic or cross-sport collision risk)."""
        from resolver._normalize import normalize_name
        from scripts.vtb_seed import VTB_TEAMS_SEED
        excluded_normalized = {
            normalize_name(c) for c in self.EXCLUDED_FORMS
        }
        for canonical, _, aliases, _ in VTB_TEAMS_SEED:
            for alias in aliases:
                assert normalize_name(alias) not in excluded_normalized, (
                    f"Found excluded alias {alias!r} on team "
                    f"{canonical!r}. Excluded forms "
                    f"{self.EXCLUDED_FORMS!r} must not appear as "
                    "standalone aliases."
                )


# ══════════════════════════════════════════════════════════════
# Cross-sport collision policy — F2 NEW empirical-coverage
# ══════════════════════════════════════════════════════════════


class TestVTBCrossSportCollisionPolicy:
    """Per F2 NEW empirical-coverage discipline (Turkish BSL #5,
    Greek HEBA #6), bare club names INCLUDED where FL sends bare
    forms at material rates AND sport_id partition validates safety.
    Bare generic forms EXCLUDED where football/hockey collision
    creates ambiguity at operator level."""

    BARE_INCLUSIONS = [
        # (bare_form, owning_canonical) — included per F2 NEW
        ("CSKA Moscow", "CSKA Moscow"),  # canonical IS bare
        ("CSKA", "CSKA Moscow"),
        ("Lokomotiv Kuban", "Lokomotiv Kuban"),  # canonical IS bare
        ("Avtodor", "BC Avtodor"),
        ("Uralmash", "BC Uralmash Yekaterinburg"),
        ("Enisey", "Enisey"),  # canonical IS bare
        ("UNICS", "UNICS Kazan"),
    ]

    BARE_EXCLUSIONS_FOOTBALL = [
        "Zenit",  # Zenit Saint Petersburg FC top-5
    ]

    def test_bare_forms_present_on_football_overlap_teams(self):
        from scripts.vtb_seed import VTB_TEAMS_SEED
        manifest_map = {
            canonical: tuple(aliases)
            for canonical, _, aliases, _ in VTB_TEAMS_SEED
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

    def test_zenit_bare_excluded(self):
        """Bare 'Zenit' must NOT appear as alias — Zenit Saint
        Petersburg FC (top-5 Russian football recognition) creates
        operator-clarity collision risk. City-qualified forms
        included instead."""
        from resolver._normalize import normalize_name
        from scripts.vtb_seed import VTB_TEAMS_SEED
        zenit_normalized = normalize_name("Zenit")
        for canonical, _, aliases, _ in VTB_TEAMS_SEED:
            for alias in aliases:
                assert normalize_name(alias) != zenit_normalized, (
                    f"Bare 'Zenit' alias found on {canonical!r}; "
                    "must be excluded per F2 (football collision)"
                )


# ══════════════════════════════════════════════════════════════
# BACKFILL UUID documentation tests
# ══════════════════════════════════════════════════════════════


class TestVTBBackfillUUIDs:
    """6 manifest canonicals are expected to BACKFILL onto Phase 2A.5
    legacy Basketball stubs at apply time. UUIDs documented in
    canonical notes per amendment #22 pre-apply audit preparation."""

    BACKFILL_CANONICALS_WITH_UUIDS = {
        "Lokomotiv Kuban": "1dae39ae-fbb5-4727-ba12-080d383a3cd3",
        "UNICS Kazan": "b1d198b0-e06d-48da-8c0b-fd6c7c146ea5",
        "Enisey": "eef30d44-b25d-4673-9cb9-a586a7212263",
        "Zenit Petersburg": "d639c09a-517e-4950-b291-5cddc493c1b7",
        "Parma Perm": "a1973c38-48f2-4a53-bba7-859f67d9e1e3",
        "Khimki M.": "b2fbeb14-c4f3-4f59-a9d0-ae3e4489f127",
    }

    INSERT_CANONICALS = {
        "CSKA Moscow",
        "BC Uralmash Yekaterinburg",
        "BC Nizhny Novgorod",
        "BC Avtodor",
        "MBA Moscow",
    }

    def test_backfill_canonicals_all_present(self):
        from scripts.vtb_seed import VTB_TEAMS_SEED
        canonicals = {c for c, _, _, _ in VTB_TEAMS_SEED}
        missing = set(self.BACKFILL_CANONICALS_WITH_UUIDS) - canonicals
        assert not missing, (
            f"BACKFILL canonicals missing from manifest: {missing}"
        )

    def test_insert_canonicals_all_present(self):
        from scripts.vtb_seed import VTB_TEAMS_SEED
        canonicals = {c for c, _, _, _ in VTB_TEAMS_SEED}
        missing = self.INSERT_CANONICALS - canonicals
        assert not missing, (
            f"INSERT canonicals missing from manifest: {missing}"
        )

    def test_backfill_uuids_documented_in_notes(self):
        from scripts.vtb_seed import VTB_TEAMS_SEED
        notes_by_canonical = {
            c: notes for c, _, _, notes in VTB_TEAMS_SEED
        }
        for canonical, uuid_str in self.BACKFILL_CANONICALS_WITH_UUIDS.items():
            notes = notes_by_canonical.get(canonical, "")
            assert uuid_str in notes, (
                f"BACKFILL canonical {canonical!r} missing UUID "
                f"{uuid_str!r} documentation in notes field"
            )

    def test_composition_is_5_insert_plus_6_backfill(self):
        """Total = 11 teams = 5 INSERT + 6 BACKFILL."""
        assert len(self.INSERT_CANONICALS) == 5
        assert len(self.BACKFILL_CANONICALS_WITH_UUIDS) == 6
        assert (
            len(self.INSERT_CANONICALS)
            + len(self.BACKFILL_CANONICALS_WITH_UUIDS) == 11
        )


# ══════════════════════════════════════════════════════════════
# Roster-membership tests
# ══════════════════════════════════════════════════════════════


class TestVTBRosterMembership:
    """11 manifest teams present as canonicals (per operator's
    Day-34 spec; BC Samara from Wikipedia roster intentionally
    excluded per §6.5)."""

    EXPECTED_CANONICALS = {
        "CSKA Moscow",
        "BC Uralmash Yekaterinburg",
        "BC Nizhny Novgorod",
        "BC Avtodor",
        "MBA Moscow",
        "Lokomotiv Kuban",
        "UNICS Kazan",
        "Enisey",
        "Zenit Petersburg",
        "Parma Perm",
        "Khimki M.",
    }

    def test_expected_canonicals_all_present(self):
        from scripts.vtb_seed import VTB_TEAMS_SEED
        canonicals = {c for c, _, _, _ in VTB_TEAMS_SEED}
        missing = self.EXPECTED_CANONICALS - canonicals
        assert not missing, (
            f"Operator-spec roster teams missing from manifest: {missing}"
        )
