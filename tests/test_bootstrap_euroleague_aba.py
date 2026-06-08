"""Tests for EuroLeague + ABA combined bootstrap (Phase 2D.5-A
workstreams #8 + #9).

Mirrors tests/test_bootstrap_heba.py shape with combined-workstream
discipline: 24 teams across 12 country codes, diacritic pair
coverage (Budućnost/Buducnost, Žalgiris/Zalgiris), Day-35 discovery
target coverage, 20 BACKFILL UUID documentation, country-code
distribution.
"""
from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


# ══════════════════════════════════════════════════════════════
# Manifest-shape unit tests (no DB)
# ══════════════════════════════════════════════════════════════


class TestEuroABAManifestShape:
    """Pure-data validation of EUROLEAGUE_ABA_TEAMS_SEED."""

    def test_manifest_imports_cleanly(self):
        from scripts.euroleague_aba_seed import (
            EUROLEAGUE_ABA_ALIAS_SOURCE,
            EUROLEAGUE_ABA_TEAMS_SEED,
        )
        assert isinstance(EUROLEAGUE_ABA_TEAMS_SEED, list)
        assert isinstance(EUROLEAGUE_ABA_ALIAS_SOURCE, str)

    def test_manifest_size_is_24(self):
        """Combined: 8 EuroLeague gap-fill + 16 ABA = 24 teams.
        Partizan + Dubai dual-league single team_id each."""
        from scripts.euroleague_aba_seed import EUROLEAGUE_ABA_TEAMS_SEED
        assert len(EUROLEAGUE_ABA_TEAMS_SEED) == 24, (
            f"Expected 24 teams; got {len(EUROLEAGUE_ABA_TEAMS_SEED)}."
        )

    def test_all_entries_are_4_tuples(self):
        from scripts.euroleague_aba_seed import EUROLEAGUE_ABA_TEAMS_SEED
        for entry in EUROLEAGUE_ABA_TEAMS_SEED:
            assert isinstance(entry, tuple)
            assert len(entry) == 4, f"Wrong arity: {entry!r}"
            canonical, country, aliases, _notes = entry
            assert isinstance(canonical, str) and canonical
            assert isinstance(country, str) and country
            assert isinstance(aliases, tuple)

    def test_all_canonical_names_normalize_to_nonempty(self):
        from resolver._normalize import normalize_name
        from scripts.euroleague_aba_seed import EUROLEAGUE_ABA_TEAMS_SEED
        for canonical, _country, _aliases, _notes in EUROLEAGUE_ABA_TEAMS_SEED:
            assert normalize_name(canonical), (
                f"Canonical name {canonical!r} normalizes to empty"
            )

    def test_all_aliases_normalize_to_nonempty(self):
        from resolver._normalize import normalize_name
        from scripts.euroleague_aba_seed import EUROLEAGUE_ABA_TEAMS_SEED
        for canonical, _country, aliases, _notes in EUROLEAGUE_ABA_TEAMS_SEED:
            for alias in aliases:
                assert normalize_name(alias), (
                    f"Alias {alias!r} (on {canonical!r}) "
                    "normalizes to empty"
                )

    def test_no_duplicate_canonical_names(self):
        from scripts.euroleague_aba_seed import EUROLEAGUE_ABA_TEAMS_SEED
        canonicals = [c for c, _, _, _ in EUROLEAGUE_ABA_TEAMS_SEED]
        assert len(canonicals) == len(set(canonicals)), (
            "Duplicate canonical_name in manifest"
        )

    def test_no_within_league_alias_collisions(self):
        from resolver._normalize import normalize_name
        from scripts.euroleague_aba_seed import EUROLEAGUE_ABA_TEAMS_SEED
        alias_owners: dict[str, set[str]] = {}
        for canonical, _country, aliases, _notes in EUROLEAGUE_ABA_TEAMS_SEED:
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
        from scripts.euroleague_aba_seed import EUROLEAGUE_ABA_ALIAS_SOURCE
        assert EUROLEAGUE_ABA_ALIAS_SOURCE == "bootstrap_league_coverage"


# ══════════════════════════════════════════════════════════════
# Diacritic coverage tests (NFD-collapsing pairs)
# ══════════════════════════════════════════════════════════════


class TestEuroABADiacriticCoverage:
    """Diacritic pairs that NFD-collapse to same normalized key.
    Belt-and-suspenders documentation pairs INCLUDED in manifest."""

    DIACRITIC_COLLAPSE_PAIRS = [
        ("Budućnost", "Buducnost"),  # ć → c
        ("Žalgiris", "Zalgiris"),  # ž → z
    ]

    def test_diacritic_pairs_normalize_identically(self):
        from resolver._normalize import normalize_name
        for diacritic, ascii_form in self.DIACRITIC_COLLAPSE_PAIRS:
            n_diacritic = normalize_name(diacritic)
            n_ascii = normalize_name(ascii_form)
            assert n_diacritic == n_ascii, (
                f"NFD diacritic-pair normalization mismatch: "
                f"{diacritic!r}→{n_diacritic!r} vs "
                f"{ascii_form!r}→{n_ascii!r}"
            )

    def test_diacritic_pairs_both_present_in_manifest(self):
        from scripts.euroleague_aba_seed import EUROLEAGUE_ABA_TEAMS_SEED
        all_strings = set()
        for canonical, _, aliases, _ in EUROLEAGUE_ABA_TEAMS_SEED:
            all_strings.add(canonical)
            all_strings.update(aliases)
        for diacritic, ascii_form in self.DIACRITIC_COLLAPSE_PAIRS:
            d_match = any(diacritic in s for s in all_strings)
            a_match = any(ascii_form in s for s in all_strings)
            assert d_match, f"Diacritic form {diacritic!r} missing"
            assert a_match, f"ASCII form {ascii_form!r} missing"


# ══════════════════════════════════════════════════════════════
# Day-35 discovery target coverage tests
# ══════════════════════════════════════════════════════════════


class TestEuroABADay35Targets:
    """Verify Day-35 production discovery provider forms covered."""

    DAY_35_TARGETS_PRESENT = [
        "KK Partizan Belgrade",
        "KK Crvena zvezda Belgrade",
        "KK Buducnost Voli",
        "KK Bosna Royal Sarajevo",
        "Monaco",
        "BC Rytas Vilnius",
        "FC Universitatea Cluj",
    ]

    def test_day35_targets_are_covered(self):
        from resolver._normalize import normalize_name
        from scripts.euroleague_aba_seed import EUROLEAGUE_ABA_TEAMS_SEED
        covered_normalized = set()
        for canonical, _, aliases, _ in EUROLEAGUE_ABA_TEAMS_SEED:
            covered_normalized.add(normalize_name(canonical))
            for alias in aliases:
                n = normalize_name(alias)
                if n:
                    covered_normalized.add(n)
        for target in self.DAY_35_TARGETS_PRESENT:
            normalized = normalize_name(target)
            assert normalized in covered_normalized, (
                f"Day-35 target {target!r} (normalized {normalized!r}) "
                "not covered by manifest"
            )


# ══════════════════════════════════════════════════════════════
# BACKFILL UUID documentation tests
# ══════════════════════════════════════════════════════════════


class TestEuroABABackfillUUIDs:
    """20 manifest canonicals expected to BACKFILL onto Phase 2A.5
    legacy stubs. UUIDs documented in canonical notes per
    amendment #22 pre-apply audit preparation."""

    BACKFILL_CANONICALS_WITH_UUIDS = {
        # EuroLeague gap-fill (7)
        "Monaco": "092518ec-4e4e-4523-9235-8a938de1d2e7",
        "Bayern München": "bdb22a1c-f2f6-4804-9a9d-cb8871c00170",
        "Lyon-Villeurbanne": "5481c8e7-cff6-4b9f-b2fc-d22e953296f7",
        "Paris Basketball": "e4e0e605-dfe7-42c0-bbfa-7ae895feaede",
        "Partizan Mozzart Bet": "575ec0fc-e1e9-4420-96aa-31376443a664",
        "Zalgiris Kaunas": "a845d73b-d8ec-4f96-8695-1c9f6dc9de13",
        "Rytas": "834075ed-c190-4c46-be1d-7fcd263ee9b3",
        # ABA League (13)
        "Crvena Zvezda Meridianbet": "a3d095e9-32c5-4491-acf2-30866bb1350a",
        "Buducnost": "063a1204-fdd6-4930-a23f-d4df5975902e",
        "KK Bosna": "99368c5b-9da7-4c56-827c-a981788875a9",
        "Cedevita Olimpija": "e7cce709-8d55-4955-a9be-49d8afdf0d0f",
        "Mega Basket": "5ef0b126-635e-464a-9e1b-ea44c2c40e1e",
        "Igokea": "ea0cd454-6efc-4cc2-a3a8-f357ada59e55",
        "KK Zadar": "bb0da184-2077-48ff-b80e-9e377567961a",
        "FMP Beograd": "1337e0d0-31f5-4021-9574-e3c8683aed0e",
        "Borac Mozzart": "949c6254-c8b2-4500-a0ee-edd03c47a206",
        "BC Vienna": "3c7275fc-407b-45c7-bd74-a95874f82ec3",
        "KK Split": "d7a6e58e-1dba-48ac-bcf0-9d16b7baca88",
        "KK Krka Novo Mesto": "0674ed89-7b97-4cc1-808b-54fe385820c3",
        "Spartak Subotica": "3c6aa492-5fa0-4ba6-a12a-9476601e96b3",
    }

    INSERT_CANONICALS = {
        "Dubai Basketball",
        "SC Derby",
        "Ilirija",
        "U-BT Cluj-Napoca",
    }

    def test_backfill_canonicals_all_present(self):
        from scripts.euroleague_aba_seed import EUROLEAGUE_ABA_TEAMS_SEED
        canonicals = {c for c, _, _, _ in EUROLEAGUE_ABA_TEAMS_SEED}
        missing = set(self.BACKFILL_CANONICALS_WITH_UUIDS) - canonicals
        assert not missing, f"BACKFILL canonicals missing: {missing}"

    def test_insert_canonicals_all_present(self):
        from scripts.euroleague_aba_seed import EUROLEAGUE_ABA_TEAMS_SEED
        canonicals = {c for c, _, _, _ in EUROLEAGUE_ABA_TEAMS_SEED}
        missing = self.INSERT_CANONICALS - canonicals
        assert not missing, f"INSERT canonicals missing: {missing}"

    def test_backfill_uuids_documented_in_notes(self):
        from scripts.euroleague_aba_seed import EUROLEAGUE_ABA_TEAMS_SEED
        notes_by_canonical = {
            c: notes for c, _, _, notes in EUROLEAGUE_ABA_TEAMS_SEED
        }
        for canonical, uuid_str in self.BACKFILL_CANONICALS_WITH_UUIDS.items():
            notes = notes_by_canonical.get(canonical, "")
            assert uuid_str in notes, (
                f"BACKFILL canonical {canonical!r} missing UUID "
                f"{uuid_str!r} in notes field"
            )

    def test_composition_is_4_insert_plus_20_backfill(self):
        """Total = 24 = 4 INSERT + 20 BACKFILL."""
        assert len(self.INSERT_CANONICALS) == 4
        assert len(self.BACKFILL_CANONICALS_WITH_UUIDS) == 20
        assert (
            len(self.INSERT_CANONICALS)
            + len(self.BACKFILL_CANONICALS_WITH_UUIDS) == 24
        )


# ══════════════════════════════════════════════════════════════
# Country code distribution tests (multi-country)
# ══════════════════════════════════════════════════════════════


class TestEuroABACountryCodes:
    """Multi-country workstream. Per-team country_code distribution
    must match the expected 12-country breakdown."""

    EXPECTED_DISTRIBUTION = {
        "SRB": 6,  # Partizan, Crvena Zvezda, Mega, FMP, Borac, Spartak
        "MNE": 2,  # Buducnost, SC Derby
        "BIH": 2,  # KK Bosna, Igokea
        "SVN": 3,  # Cedevita Olimpija, KK Krka, Ilirija
        "CRO": 2,  # KK Zadar, KK Split
        "AUT": 1,  # BC Vienna
        "ROU": 1,  # U-BT Cluj-Napoca
        "UAE": 1,  # Dubai Basketball
        "MCO": 1,  # Monaco
        "DEU": 1,  # Bayern München
        "FRA": 2,  # Lyon-Villeurbanne, Paris Basketball
        "LTU": 2,  # Zalgiris Kaunas, Rytas
    }

    def test_country_code_distribution(self):
        from scripts.euroleague_aba_seed import EUROLEAGUE_ABA_TEAMS_SEED
        actual: dict[str, int] = {}
        for _canonical, country, _aliases, _notes in EUROLEAGUE_ABA_TEAMS_SEED:
            actual[country] = actual.get(country, 0) + 1
        assert actual == self.EXPECTED_DISTRIBUTION, (
            f"Country code distribution mismatch.\n"
            f"Expected: {self.EXPECTED_DISTRIBUTION}\n"
            f"Actual:   {actual}"
        )

    def test_sc_derby_is_mne_not_srb(self):
        """SC Derby is Montenegrin (Podgorica-based), not Serbian.
        Operator-corrected Day-35 spec."""
        from scripts.euroleague_aba_seed import EUROLEAGUE_ABA_TEAMS_SEED
        for canonical, country, _, _ in EUROLEAGUE_ABA_TEAMS_SEED:
            if canonical == "SC Derby":
                assert country == "MNE", (
                    f"SC Derby has country={country!r}; expected 'MNE'"
                )
                return
        raise AssertionError("SC Derby not found in manifest")

    def test_total_count_matches_manifest_size(self):
        from scripts.euroleague_aba_seed import EUROLEAGUE_ABA_TEAMS_SEED
        assert sum(self.EXPECTED_DISTRIBUTION.values()) == len(
            EUROLEAGUE_ABA_TEAMS_SEED
        )
