"""Tests for Turkish BSL bootstrap (Phase 2D.5-A workstream #5).

Mirrors tests/test_bootstrap_israeli_bsl.py shape with Turkish BSL-
specific discipline: empirical-coverage discipline (bare aliases
INCLUDED for football-overlap teams), diacritic empirical verification
(ş/ç/ü/ğ collapse via NFD; `ı` does NOT — both forms functionally
required), canonical-name fragmentation (dormant phantom acceptance
for Karşıyaka + Türk Telekom legacy stubs), sponsor-stripping coverage,
BACKFILL predictions documentation.
"""
from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


# ══════════════════════════════════════════════════════════════
# Manifest-shape unit tests (no DB)
# ══════════════════════════════════════════════════════════════


class TestTurkishBSLManifestShape:
    """Pure-data validation of TURKISH_BSL_TEAMS_SEED +
    TURKISH_BSL_ALIAS_SOURCE."""

    def test_manifest_imports_cleanly(self):
        from scripts.turkish_bsl_seed import (
            TURKISH_BSL_ALIAS_SOURCE,
            TURKISH_BSL_TEAMS_SEED,
        )
        assert isinstance(TURKISH_BSL_TEAMS_SEED, list)
        assert isinstance(TURKISH_BSL_ALIAS_SOURCE, str)

    def test_manifest_size_is_16(self):
        """2025-26 Turkish BSL has 16 teams (structurally; vs Israeli
        BSL's 14, LBA's 16, ACB's 18). Verified against operator's
        Day-31 paste from Wikipedia 2025-26 Basketbol Süper Ligi
        roster."""
        from scripts.turkish_bsl_seed import TURKISH_BSL_TEAMS_SEED
        assert len(TURKISH_BSL_TEAMS_SEED) == 16, (
            f"Expected 16 Turkish BSL teams; got "
            f"{len(TURKISH_BSL_TEAMS_SEED)}. If roster changed, update "
            "this test alongside turkish_bsl_seed.py."
        )

    def test_all_entries_are_4_tuples(self):
        from scripts.turkish_bsl_seed import TURKISH_BSL_TEAMS_SEED
        for entry in TURKISH_BSL_TEAMS_SEED:
            assert isinstance(entry, tuple)
            assert len(entry) == 4, f"Wrong arity: {entry!r}"
            canonical, country, aliases, _notes = entry
            assert isinstance(canonical, str) and canonical
            assert isinstance(country, str) and country
            assert isinstance(aliases, tuple)

    def test_country_codes_are_all_tur(self):
        """Turkish BSL is a single-country league. All teams TUR."""
        from scripts.turkish_bsl_seed import TURKISH_BSL_TEAMS_SEED
        for canonical, country, _aliases, _notes in TURKISH_BSL_TEAMS_SEED:
            assert country == "TUR", (
                f"Team {canonical!r} has country_code={country!r}; "
                "expected 'TUR'"
            )

    def test_all_canonical_names_normalize_to_nonempty(self):
        from resolver._normalize import normalize_name
        from scripts.turkish_bsl_seed import TURKISH_BSL_TEAMS_SEED
        for canonical, _country, _aliases, _notes in TURKISH_BSL_TEAMS_SEED:
            assert normalize_name(canonical), (
                f"Canonical name {canonical!r} normalizes to empty"
            )

    def test_all_aliases_normalize_to_nonempty(self):
        from resolver._normalize import normalize_name
        from scripts.turkish_bsl_seed import TURKISH_BSL_TEAMS_SEED
        for canonical, _country, aliases, _notes in TURKISH_BSL_TEAMS_SEED:
            for alias in aliases:
                assert normalize_name(alias), (
                    f"Alias {alias!r} (on team {canonical!r}) "
                    "normalizes to empty"
                )

    def test_no_duplicate_canonical_names(self):
        from scripts.turkish_bsl_seed import TURKISH_BSL_TEAMS_SEED
        canonicals = [c for c, _, _, _ in TURKISH_BSL_TEAMS_SEED]
        assert len(canonicals) == len(set(canonicals)), (
            "Duplicate canonical_name in manifest"
        )

    def test_no_within_league_alias_collisions(self):
        """No two DIFFERENT BSL teams share the same normalized
        alias. Same-team duplicates (Beşiktaş + Besiktas both normalize
        to 'besiktas') are not collisions — they're documentation
        pairs on the same team."""
        from resolver._normalize import normalize_name
        from scripts.turkish_bsl_seed import TURKISH_BSL_TEAMS_SEED
        alias_owners: dict[str, set[str]] = {}
        for canonical, _country, aliases, _notes in TURKISH_BSL_TEAMS_SEED:
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
        from scripts.turkish_bsl_seed import TURKISH_BSL_ALIAS_SOURCE
        assert TURKISH_BSL_ALIAS_SOURCE == "bootstrap_league_coverage"


# ══════════════════════════════════════════════════════════════
# Diacritic coverage tests (NFD-decomposing characters)
# ══════════════════════════════════════════════════════════════


class TestTurkishBSLDiacriticCoverage:
    """Turkish ş/ç/ü/ğ decompose via NFD + combining-mark strip; both
    diacritic and ASCII-stripped variants normalize to the SAME key.
    Belt-and-suspenders pairs INCLUDED for documentation clarity.

    EXCEPTION: `ı` (U+0131 dotless i) does NOT decompose. It's a
    precomposed base letter distinct from `i`. For ı-containing teams,
    both forms must be present (functional requirement, not
    documentation belt-and-suspenders) — covered in a separate test
    class below."""

    # (alias_with_diacritic, alias_ascii_stripped) — must normalize to same key
    DIACRITIC_PAIRS = [
        ("Beşiktaş", "Besiktas"),
        ("Fenerbahçe", "Fenerbahce"),
        ("Bahçeşehir", "Bahcesehir"),
        ("Tofaş", "Tofas"),
        ("Büyükçekmece", "Buyukcekmece"),
        ("Türk Telekom Ankara", "Turk Telekom Ankara"),
    ]

    def test_diacritic_pairs_normalize_identically(self):
        from resolver._normalize import normalize_name
        for diacritic, ascii_form in self.DIACRITIC_PAIRS:
            n_diacritic = normalize_name(diacritic)
            n_ascii = normalize_name(ascii_form)
            assert n_diacritic == n_ascii, (
                f"NFD diacritic-pair normalization mismatch: "
                f"{diacritic!r}→{n_diacritic!r} vs "
                f"{ascii_form!r}→{n_ascii!r}"
            )

    def test_diacritic_pairs_both_present_in_manifest(self):
        """For documentation clarity, both diacritic and ASCII forms
        should appear in the manifest somewhere (canonical or alias)."""
        from scripts.turkish_bsl_seed import TURKISH_BSL_TEAMS_SEED
        all_strings = set()
        for canonical, _, aliases, _ in TURKISH_BSL_TEAMS_SEED:
            all_strings.add(canonical)
            all_strings.update(aliases)
        for diacritic, ascii_form in self.DIACRITIC_PAIRS:
            # At minimum one of the pair must be a substring of some
            # manifest string. Tighter: both should appear directly.
            d_match = any(diacritic in s for s in all_strings)
            a_match = any(ascii_form in s for s in all_strings)
            assert d_match, f"Diacritic form {diacritic!r} not found in manifest"
            assert a_match, f"ASCII form {ascii_form!r} not found in manifest"


# ══════════════════════════════════════════════════════════════
# Dotless-i coverage tests (Turkish `ı` does NOT decompose under NFD)
# ══════════════════════════════════════════════════════════════


class TestTurkishBSLDotlessICoverage:
    """Turkish `ı` (U+0131 LATIN SMALL LETTER DOTLESS I) does NOT
    decompose via NFD. `Karşıyaka` normalizes to `karsıyaka` (with
    ı); `Karsiyaka` normalizes to `karsiyaka` (with regular i). These
    are DIFFERENT normalized keys. Both forms FUNCTIONALLY REQUIRED
    in manifest for production strings sending either variant."""

    # (alias_with_ı, alias_with_i) — must normalize to DIFFERENT keys
    DOTLESS_I_PAIRS = [
        ("Karşıyaka Basket", "Karsiyaka Basket"),
        ("Karşıyaka Basketbol", "Karsiyaka Basketbol"),
        ("Pınar Karşıyaka", "Pinar Karsiyaka"),
    ]

    def test_dotless_i_pairs_normalize_to_different_keys(self):
        """Confirms our empirical finding: ı vs i normalize differently
        (regression test against normalizer changes that would silently
        collapse them)."""
        from resolver._normalize import normalize_name
        for ı_form, i_form in self.DOTLESS_I_PAIRS:
            n_ı = normalize_name(ı_form)
            n_i = normalize_name(i_form)
            assert n_ı != n_i, (
                f"Unexpected normalization match: {ı_form!r}→{n_ı!r} "
                f"vs {i_form!r}→{n_i!r}. If normalizer was enhanced to "
                "map ı → i, update this test + remove ı/i belt-and-"
                "suspenders pairs from manifest."
            )

    def test_dotless_i_pairs_both_present_as_aliases(self):
        """For ı-containing teams, both ı and i forms must be aliases
        — functional requirement, since they normalize differently."""
        from scripts.turkish_bsl_seed import TURKISH_BSL_TEAMS_SEED
        manifest_map = {
            canonical: tuple(aliases)
            for canonical, _, aliases, _ in TURKISH_BSL_TEAMS_SEED
        }
        # Karşıyaka Basket should have both ı and i forms
        karsiyaka_aliases = manifest_map.get("Karşıyaka Basket", ())
        for ı_form, i_form in self.DOTLESS_I_PAIRS:
            # Find which canonical owns this pair by substring match
            for canonical, aliases in manifest_map.items():
                if ı_form in aliases:
                    assert i_form in aliases, (
                        f"Team {canonical!r} has {ı_form!r} but missing "
                        f"required ASCII-i variant {i_form!r}"
                    )
                    break


# ══════════════════════════════════════════════════════════════
# Cross-sport collision policy — INCLUSIVE (F2 NEW)
# ══════════════════════════════════════════════════════════════


class TestTurkishBSLCrossSportCollisionPolicy:
    """Per F2 NEW empirical-coverage discipline, bare-name aliases
    INCLUDED for 5 football-overlap teams (Galatasaray, Fenerbahçe,
    Beşiktaş, Trabzonspor, Bursaspor). This contrasts with Israeli
    BSL workstream #4 which excluded all 11 bare-city forms as
    operator-clarity discipline.

    Rationale: Day-31 discovery shows production provider strings
    ARE the bare forms at material rates. Day-22 sport_id partition
    validates matcher-layer safety (5th empirical validation in this
    workstream — Soccer-side rows for these names already exist)."""

    # (bare_form, owning_canonical) — bare form must be present as
    # alias (or be the canonical itself) on the basketball team
    BARE_INCLUSIONS = [
        ("Galatasaray", "Galatasaray"),  # canonical IS bare; alias explicit too
        ("Fenerbahce", "Fenerbahçe"),
        ("Fenerbahçe", "Fenerbahçe"),
        ("Besiktas", "Beşiktaş"),
        ("Beşiktaş", "Beşiktaş"),
        ("Trabzonspor", "Trabzonspor (Basketbol)"),
        ("Bursaspor", "Bursaspor Basketbol"),
    ]

    def test_bare_forms_present_on_football_overlap_teams(self):
        from scripts.turkish_bsl_seed import TURKISH_BSL_TEAMS_SEED
        manifest_map = {
            canonical: tuple(aliases)
            for canonical, _, aliases, _ in TURKISH_BSL_TEAMS_SEED
        }
        for bare_form, owning_canonical in self.BARE_INCLUSIONS:
            assert owning_canonical in manifest_map, (
                f"Owning canonical {owning_canonical!r} not in manifest"
            )
            aliases = manifest_map[owning_canonical]
            # bare_form present either as canonical or as alias
            present = (
                owning_canonical == bare_form or bare_form in aliases
            )
            assert present, (
                f"Bare form {bare_form!r} not present on "
                f"{owning_canonical!r} aliases — F2 NEW empirical-"
                "coverage discipline requires inclusion"
            )


# ══════════════════════════════════════════════════════════════
# Day-31 discovery target coverage tests
# ══════════════════════════════════════════════════════════════


class TestTurkishBSLDiscoveryTargets:
    """Verify that in-scope Day-31 discovery provider forms are
    covered as canonicals or aliases. EXCEPTION: 'Turk Telekom'
    routes to dormant phantom by design and is NOT covered by the
    new manifest team Türk Telekom Ankara."""

    DAY_31_TARGETS_IN_MANIFEST = [
        "Galatasaray",
        "Galatasaray SK",
        "Besiktas",
        "Besiktas JK",
        "Besiktas *",
        "Fenerbahce",
        "Fenerbahce Istanbul",
        "Fenerbahce *",
        "Esenler Erokspor",
        "Bursaspor",
        "Merkezefendi",
        "Merkezefendi Belediyesi Denizli Basket",
        "Trabzonspor",
        "Manisa",
        "Mersin SK",
        "Bahcesehir Kol.",
        "Bahcesehir Kol. *",
    ]

    DAY_31_TARGETS_ROUTING_TO_DORMANT_PHANTOM = [
        # These provider forms intentionally NOT covered by manifest;
        # they continue resolving via legacy Phase 2A.5 canonical_name
        # lookup on the dormant phantom stubs.
        "Karsiyaka",  # legacy Karşıyaka phantom (id ff68785a)
        "Turk Telekom",  # legacy Turk Telekom phantom (id d436ec55)
    ]

    def test_discovery_target_strings_are_covered(self):
        from resolver._normalize import normalize_name
        from scripts.turkish_bsl_seed import TURKISH_BSL_TEAMS_SEED
        covered_normalized = set()
        for canonical, _, aliases, _ in TURKISH_BSL_TEAMS_SEED:
            covered_normalized.add(normalize_name(canonical))
            for alias in aliases:
                n = normalize_name(alias)
                if n:
                    covered_normalized.add(n)
        for target in self.DAY_31_TARGETS_IN_MANIFEST:
            normalized = normalize_name(target)
            assert normalized in covered_normalized, (
                f"Day-31 in-scope target {target!r} (normalized "
                f"{normalized!r}) not covered by manifest"
            )

    def test_dormant_phantom_routes_not_in_manifest(self):
        """Confirms dormant phantom discipline: 'Turk Telekom' and
        'Karsiyaka' provider strings are NOT covered by new manifest
        teams. They continue resolving to legacy stubs."""
        from resolver._normalize import normalize_name
        from scripts.turkish_bsl_seed import TURKISH_BSL_TEAMS_SEED
        covered_normalized = set()
        for canonical, _, aliases, _ in TURKISH_BSL_TEAMS_SEED:
            covered_normalized.add(normalize_name(canonical))
            for alias in aliases:
                n = normalize_name(alias)
                if n:
                    covered_normalized.add(n)
        for target in self.DAY_31_TARGETS_ROUTING_TO_DORMANT_PHANTOM:
            normalized = normalize_name(target)
            assert normalized not in covered_normalized, (
                f"Dormant phantom target {target!r} (normalized "
                f"{normalized!r}) UNEXPECTEDLY covered by manifest — "
                "this would create within-sport ambiguous lookup "
                "with legacy phantom (resolver returns None)"
            )


# ══════════════════════════════════════════════════════════════
# BACKFILL prediction documentation
# ══════════════════════════════════════════════════════════════


class TestTurkishBSLBackfillPredictions:
    """3 manifest canonicals are expected to BACKFILL onto Phase 2A.5
    legacy Basketball stubs at apply time. Documented as predictions,
    not enforced (apply-time empirical verification is authoritative
    per the operator's Day-31 afternoon finding that Phase 2A.5
    legacy coverage is non-prominence-correlated)."""

    BACKFILL_PREDICTIONS = {
        # canonical → (legacy_uuid, normalized_match)
        "Anadolu Efes": (
            "ca2f4866-c4ac-4a26-976f-d54401ce8c1d", "anadolu efes",
        ),
        "Bursaspor Basketbol": (
            "85c6d6bf-8ffb-4309-b0aa-9ba3d146ad4c", "bursaspor basketbol",
        ),
        "Tofaş": (
            "7f3d7ec1-c48f-48cf-8b8f-089faec3fc53", "tofas",
        ),
    }

    def test_backfill_canonicals_present_in_manifest(self):
        from scripts.turkish_bsl_seed import TURKISH_BSL_TEAMS_SEED
        canonicals = {c for c, _, _, _ in TURKISH_BSL_TEAMS_SEED}
        for canonical in self.BACKFILL_PREDICTIONS:
            assert canonical in canonicals, (
                f"BACKFILL prediction canonical {canonical!r} not "
                "found in manifest"
            )

    def test_backfill_canonicals_normalize_to_predicted_keys(self):
        from resolver._normalize import normalize_name
        for canonical, (_uuid, expected_norm) in (
            self.BACKFILL_PREDICTIONS.items()
        ):
            actual_norm = normalize_name(canonical)
            assert actual_norm == expected_norm, (
                f"BACKFILL prediction {canonical!r} normalizes to "
                f"{actual_norm!r}, expected {expected_norm!r} (match "
                "with legacy stub). If normalizer changed, update "
                "predictions."
            )


# ══════════════════════════════════════════════════════════════
# Dormant phantom canonical-name fragmentation documentation
# ══════════════════════════════════════════════════════════════


class TestTurkishBSLDormantPhantomDocumentation:
    """2 manifest canonicals deliberately diverge from legacy Phase
    2A.5 stubs. Legacy stubs become DORMANT PHANTOMS — kept in
    sp.teams, not BACKFILLed, no new bootstrap aliases. Manifest uses
    Wikipedia-canonical / location-disambiguated forms per F1
    amendment #12 authoritative-source primacy."""

    DORMANT_PHANTOM_PAIRS = [
        # (manifest_canonical, legacy_canonical, legacy_uuid)
        ("Karşıyaka Basket", "Karşıyaka", "ff68785a-0698-4934-b594-c68ccfdb1711"),
        ("Türk Telekom Ankara", "Turk Telekom",
         "d436ec55-a303-49a5-84af-0e3f0e90156b"),
    ]

    def test_manifest_uses_full_form_not_legacy_bare(self):
        from scripts.turkish_bsl_seed import TURKISH_BSL_TEAMS_SEED
        canonicals = {c for c, _, _, _ in TURKISH_BSL_TEAMS_SEED}
        for manifest_canon, legacy_canon, _uuid in self.DORMANT_PHANTOM_PAIRS:
            assert manifest_canon in canonicals, (
                f"Manifest canonical {manifest_canon!r} missing"
            )
            assert legacy_canon not in canonicals, (
                f"Legacy bare canonical {legacy_canon!r} unexpectedly "
                "in manifest — would conflict with dormant phantom"
            )

    def test_manifest_and_legacy_normalize_to_different_keys(self):
        """The key property: manifest and legacy normalize to different
        keys, so they can coexist as separate sp.teams rows."""
        from resolver._normalize import normalize_name
        for manifest_canon, legacy_canon, _uuid in self.DORMANT_PHANTOM_PAIRS:
            n_manifest = normalize_name(manifest_canon)
            n_legacy = normalize_name(legacy_canon)
            assert n_manifest != n_legacy, (
                f"Canonical-name fragmentation property violated: "
                f"{manifest_canon!r}→{n_manifest!r} same as "
                f"{legacy_canon!r}→{n_legacy!r}. Dormant phantom "
                "discipline requires distinct normalized keys."
            )


# ══════════════════════════════════════════════════════════════
# Sponsor stripping tests
# ══════════════════════════════════════════════════════════════


class TestTurkishBSLSponsorStripping:
    """Sponsor-form variants present as aliases on the heritage
    canonical (F1 amendment #12 authoritative-source primacy:
    canonical = heritage, aliases = sponsor variants)."""

    SPONSOR_FORM_TO_CANONICAL = [
        ("Beşiktaş Gain", "Beşiktaş"),
        ("Fenerbahçe Beko", "Fenerbahçe"),
        ("Galatasaray MCT Technic", "Galatasaray"),
        ("Glint Manisa Basket", "Manisa Basket"),
        ("ONVO Büyükçekmece", "Büyükçekmece Basketbol"),
        ("Yukatel Merkezefendi Basket", "Merkezefendi Basket"),
    ]

    def test_sponsor_forms_present_as_aliases(self):
        from scripts.turkish_bsl_seed import TURKISH_BSL_TEAMS_SEED
        manifest_map = {
            canonical: tuple(aliases)
            for canonical, _, aliases, _ in TURKISH_BSL_TEAMS_SEED
        }
        for sponsor_form, owning_canonical in self.SPONSOR_FORM_TO_CANONICAL:
            assert owning_canonical in manifest_map, (
                f"Owning canonical {owning_canonical!r} not in manifest"
            )
            aliases = manifest_map[owning_canonical]
            assert sponsor_form in aliases, (
                f"Sponsor form {sponsor_form!r} not present as alias "
                f"on {owning_canonical!r}"
            )


# ══════════════════════════════════════════════════════════════
# Roster-membership tests (Wikipedia 2025-26 BSL operator paste)
# ══════════════════════════════════════════════════════════════


class TestTurkishBSLRosterMembership:
    """All 16 operator-pasted Wikipedia 2025-26 Türkiye Basketbol
    Süper Ligi roster teams present as canonicals."""

    EXPECTED_CANONICALS = {
        "Anadolu Efes",
        "Bahçeşehir Koleji",
        "Beşiktaş",
        "Bursaspor Basketbol",
        "Büyükçekmece Basketbol",
        "Esenler Erokspor",
        "Fenerbahçe",
        "Galatasaray",
        "Karşıyaka Basket",
        "Manisa Basket",
        "Merkezefendi Basket",
        "Mersin MSK",
        "Petkim Spor",
        "Tofaş",
        "Trabzonspor (Basketbol)",
        "Türk Telekom Ankara",
    }

    def test_expected_canonicals_all_present(self):
        from scripts.turkish_bsl_seed import TURKISH_BSL_TEAMS_SEED
        canonicals = {c for c, _, _, _ in TURKISH_BSL_TEAMS_SEED}
        missing = self.EXPECTED_CANONICALS - canonicals
        assert not missing, (
            f"Operator-pasted roster teams missing from manifest: {missing}"
        )
