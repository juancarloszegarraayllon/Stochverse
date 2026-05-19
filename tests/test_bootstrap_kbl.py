"""Tests for KBL bootstrap (Phase 2C coverage pilot).

Mirrors tests/test_bootstrap_national_teams.py shape:

  - Manifest-shape unit tests (always run; no DB required)
  - Integration tests (SP_INTEGRATION_DB-gated; real Postgres)

Extends PR #156's test surface with alias-path coverage:

  - Alias INSERT idempotency
  - Three-branch classifier on sp.teams (INSERT / BACKFILL / SKIP)
  - Sponsor-prefixed alias distinctiveness verification
  - Hangul preservation through normalize_name
  - bootstrap_league_coverage source-value verification
"""
from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
INTEGRATION_DB = os.environ.get("SP_INTEGRATION_DB", "").strip()


# ══════════════════════════════════════════════════════════════
# Manifest-shape unit tests (no DB)
# ══════════════════════════════════════════════════════════════


class TestManifestShape:
    """Pure-data validation of KBL_TEAMS_SEED + KBL_ALIAS_SOURCE.

    These tests guard the seed manifest against accidental edits
    that would break the bootstrap's invariants. They run always —
    no DB, no network, no integration setup.
    """

    def test_manifest_imports_cleanly(self):
        """Sanity check: the seed module imports without errors and
        exposes both expected constants."""
        from scripts.kbl_seed import KBL_ALIAS_SOURCE, KBL_TEAMS_SEED
        assert isinstance(KBL_TEAMS_SEED, list)
        assert isinstance(KBL_ALIAS_SOURCE, str)

    def test_manifest_size_is_10(self):
        """KBL has exactly 10 teams for the 2025-26 season. If this
        fails, the re-curation runbook in kbl_seed.py was triggered
        without updating this test — verify against an authoritative
        source (asia-basket.com, korealocalpages.com)."""
        from scripts.kbl_seed import KBL_TEAMS_SEED
        assert len(KBL_TEAMS_SEED) == 10, (
            f"Expected 10 KBL teams; got {len(KBL_TEAMS_SEED)}. "
            "If KBL added/removed teams, update this test alongside "
            "kbl_seed.py."
        )

    def test_all_entries_are_4_tuples(self):
        """The bootstrap unpacks (canonical_name, country_code,
        aliases, notes). Wrong tuple arity would crash at runtime."""
        from scripts.kbl_seed import KBL_TEAMS_SEED
        for entry in KBL_TEAMS_SEED:
            assert isinstance(entry, tuple)
            assert len(entry) == 4, (
                f"Manifest entry has wrong arity: {entry!r}"
            )
            canonical, country, aliases, _notes = entry
            assert isinstance(canonical, str) and canonical
            assert isinstance(country, str) and country
            assert isinstance(aliases, tuple)

    def test_all_country_codes_are_kor(self):
        """KBL is a South Korean league; every entry must be KOR."""
        from scripts.kbl_seed import KBL_TEAMS_SEED
        for canonical, country, _aliases, _notes in KBL_TEAMS_SEED:
            assert country == "KOR", (
                f"Team {canonical!r} has country_code={country!r}; "
                "expected 'KOR'"
            )

    def test_all_canonical_names_normalize_to_nonempty(self):
        """The bootstrap's empty_normalized guard exists for defense;
        no production manifest entry should trigger it."""
        from resolver._normalize import normalize_name
        from scripts.kbl_seed import KBL_TEAMS_SEED
        for canonical, _country, _aliases, _notes in KBL_TEAMS_SEED:
            normalized = normalize_name(canonical)
            assert normalized, (
                f"Canonical name {canonical!r} normalizes to empty"
            )

    def test_all_aliases_normalize_to_nonempty(self):
        """Same as canonical-name test but for the alias dimension."""
        from resolver._normalize import normalize_name
        from scripts.kbl_seed import KBL_TEAMS_SEED
        for canonical, _country, aliases, _notes in KBL_TEAMS_SEED:
            for alias in aliases:
                normalized = normalize_name(alias)
                assert normalized, (
                    f"Alias {alias!r} (on team {canonical!r}) "
                    "normalizes to empty"
                )

    def test_no_duplicate_canonical_names(self):
        """Two manifest entries with the same canonical would route
        to the same sp.teams row at bootstrap time and produce
        duplicate alias-insert attempts. The Python dedup catches
        this but cleaner to prevent at the seed level."""
        from scripts.kbl_seed import KBL_TEAMS_SEED
        canonicals = [c for c, _, _, _ in KBL_TEAMS_SEED]
        assert len(canonicals) == len(set(canonicals)), (
            "Duplicate canonical_name in manifest. Duplicates: "
            f"{[c for c in canonicals if canonicals.count(c) > 1]}"
        )

    def test_all_aliases_are_multi_token_or_hangul(self):
        """Alias distinctiveness constraint (see kbl_seed.py docstring):
        bare team-name aliases ("Goyang", "Egis", "Sono") would collide
        with non-KBL Basketball teams in sport_id=3 globally (e.g.,
        Egis Körmend in Hungary; Mexican baseball Sonora teams via
        trigram similarity). Multi-token sponsor-prefixed aliases are
        the load-bearing invariant.

        Hangul-only aliases are exempt — they're inherently distinctive
        from Latin team names. Pure-numeric aliases shouldn't exist for
        KBL teams either, but defensive check.
        """
        from scripts.kbl_seed import KBL_TEAMS_SEED
        for canonical, _country, aliases, _notes in KBL_TEAMS_SEED:
            for alias in aliases:
                # Hangul-containing aliases are inherently distinctive.
                if any('가' <= c <= '힣' for c in alias):
                    continue
                # Latin / mixed: must have at least 2 whitespace-
                # separated tokens to be considered "multi-token."
                tokens = alias.split()
                assert len(tokens) >= 2, (
                    f"Alias {alias!r} on team {canonical!r} is "
                    "single-token Latin. Per kbl_seed.py "
                    "distinctiveness constraint, all Latin aliases "
                    "must be multi-token sponsor-prefixed to prevent "
                    "cross-league alias-tier collisions."
                )

    def test_hangul_coverage_for_three_specific_teams(self):
        """Per F3 decision (2026-05-19), three teams ship with
        confirmed Hangul aliases: Goyang Sono, Anyang JeongKwanJang
        Red Boosters, Changwon LG Sakers. Other 7 ship with romanized
        only. If this changes (Hangul added to other teams in a
        follow-up), update this test alongside the seed."""
        from scripts.kbl_seed import KBL_TEAMS_SEED
        teams_with_hangul: set[str] = set()
        for canonical, _country, aliases, _notes in KBL_TEAMS_SEED:
            for alias in aliases:
                if any('가' <= c <= '힣' for c in alias):
                    teams_with_hangul.add(canonical)
                    break
        expected = {
            "Goyang Sono",
            "Anyang JeongKwanJang Red Boosters",
            "Changwon LG Sakers",
        }
        assert teams_with_hangul == expected, (
            f"Hangul coverage mismatch. Expected {expected!r}; "
            f"got {teams_with_hangul!r}. Per F3 decision the v1 set "
            "is the three teams above. If expanding Hangul coverage, "
            "update this test alongside the seed."
        )

    def test_alias_source_value_is_bootstrap_league_coverage(self):
        """Source-value pin (Q3 decision). Generic value reused across
        the 5-sport cohort (Handball / Snooker / Volleyball / Rugby
        League / Golf / Darts). If a future bootstrap diverges to a
        per-bootstrap source value, this test should NOT be changed
        for KBL — the divergent bootstrap files its own constant."""
        from scripts.kbl_seed import KBL_ALIAS_SOURCE
        assert KBL_ALIAS_SOURCE == "bootstrap_league_coverage"

    def test_aliases_dedup_correctly_after_normalization(self):
        """Within a single team's alias tuple, no two aliases should
        normalize to the same string. The bootstrap dedups in-batch
        but having dup raw forms in the seed wastes review attention
        and creates noise in operator runbook output ('dedup'd within
        batch: N' shouldn't be N>0 in production runs)."""
        from resolver._normalize import normalize_name
        from scripts.kbl_seed import KBL_TEAMS_SEED
        for canonical, _country, aliases, _notes in KBL_TEAMS_SEED:
            normalized_aliases = [normalize_name(a) for a in aliases]
            dups = [
                n for n in normalized_aliases
                if normalized_aliases.count(n) > 1
            ]
            assert not dups, (
                f"Team {canonical!r} has aliases that normalize to "
                f"the same string. Duplicate normalized forms: "
                f"{set(dups)!r}. Verify the raw alias list — the "
                "F2 dedup observation noted Anyang JeongKwanJang "
                "casing variants collapse to the same normalized "
                "form."
            )

    def test_update_branch_teams_match_existing_canonical_shapes(self):
        """F1 decision pin: the two known UPDATE-branch teams
        (Goyang Sono, KCC Egis) must have canonical_name matching
        the existing sp.teams row's stored canonical_name (verified
        2026-05-19 via Query A1/A2). If a future bootstrap edit
        changes these canonicals to current-official forms, the
        normalized lookup in bootstrap_kbl.py won't find the existing
        row and will INSERT a duplicate. This test pins the F1
        contract at the manifest level."""
        from scripts.kbl_seed import KBL_TEAMS_SEED
        canonicals = {c for c, _, _, _ in KBL_TEAMS_SEED}
        assert "Goyang Sono" in canonicals, (
            "Manifest is missing the UPDATE-branch entry for "
            "'Goyang Sono' (existing sp.teams row from 2A.5 legacy "
            "bootstrap). If this team's canonical changed in the "
            "manifest, see F1 decision in kbl_seed.py docstring."
        )
        assert "KCC Egis" in canonicals, (
            "Manifest is missing the UPDATE-branch entry for "
            "'KCC Egis'. Same F1 contract as Goyang Sono."
        )

    def test_current_official_names_appear_as_aliases_for_update_branch(self):
        """F1 corollary: when canonical stays at the legacy form,
        the current official MUST be an alias on the same row.
        Otherwise FL records using the current official name won't
        match anything."""
        from scripts.kbl_seed import KBL_TEAMS_SEED
        for canonical, _country, aliases, _notes in KBL_TEAMS_SEED:
            if canonical == "Goyang Sono":
                assert "Goyang Sono Skygunners" in aliases, (
                    "'Goyang Sono Skygunners' must appear as an alias "
                    "on the 'Goyang Sono' UPDATE-branch entry per F1 "
                    "decision."
                )
            elif canonical == "KCC Egis":
                assert "Busan KCC Egis" in aliases, (
                    "'Busan KCC Egis' must appear as an alias on the "
                    "'KCC Egis' UPDATE-branch entry per F1 decision."
                )


# ══════════════════════════════════════════════════════════════
# Integration tests (SP_INTEGRATION_DB-gated)
# ══════════════════════════════════════════════════════════════


_TEST_MARKER = "TEST-KBL-BOOTSTRAP"


@pytest.mark.skipif(
    not INTEGRATION_DB,
    reason="SP_INTEGRATION_DB not set — KBL bootstrap integration tests "
           "need real Postgres.",
)
class TestKblBootstrapIntegration:
    """Real-DB tests for the bootstrap script's three-branch
    classifier + alias write path."""

    @pytest.fixture
    def engine(self):
        from sqlalchemy import create_engine
        url = INTEGRATION_DB
        if "+asyncpg" in url:
            url = url.replace("+asyncpg", "")
        eng = create_engine(url)
        yield eng
        eng.dispose()

    @pytest.fixture(autouse=True)
    def setup_schema(self, engine):
        result = subprocess.run(
            ["alembic", "upgrade", "head"],
            cwd=REPO_ROOT, capture_output=True, text=True,
            env={**os.environ, "DATABASE_URL": INTEGRATION_DB},
        )
        assert result.returncode == 0, (
            f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}"
        )
        self._purge_test_data(engine)
        yield
        self._purge_test_data(engine)

    def _purge_test_data(self, engine):
        """Clean ALL KBL-related rows so tests start from a known
        state regardless of prior bootstrap-against-real-data history.

        Scope: delete every Basketball team with country_code='KOR'
        OR canonical_name matching the manifest's known KBL names.
        Cascades to sp.team_aliases via FK ON DELETE CASCADE.
        """
        from sqlalchemy import text
        from scripts.kbl_seed import KBL_TEAMS_SEED
        kbl_canonicals = [c for c, _, _, _ in KBL_TEAMS_SEED]
        with engine.begin() as conn:
            # Also delete any aliases written under the
            # bootstrap_league_coverage source value (defensive — FK
            # CASCADE should handle this via team deletion, but
            # explicit cleanup for cases where the team row predates
            # the KBL bootstrap).
            conn.execute(text(
                "DELETE FROM sp.team_aliases WHERE source = 'bootstrap_league_coverage'"
            ))
            # Delete KBL teams. Match on canonical_name (catches
            # newly-inserted rows) OR (country_code='KOR' AND
            # sport_id=Basketball — catches legacy KBL rows with
            # backfilled country_codes).
            conn.execute(text(
                "DELETE FROM sp.teams "
                "WHERE canonical_name = ANY(CAST(:names AS text[])) "
                "OR (country_code = 'KOR' "
                "    AND sport_id = (SELECT id FROM sp.sports "
                "                    WHERE name = 'Basketball'))"
            ), {"names": kbl_canonicals})

    def _run_bootstrap(self, *args: str):
        """Subprocess the bootstrap with INTEGRATION_DB as
        DATABASE_URL. Returns CompletedProcess for assertion on
        stdout/stderr/returncode."""
        return subprocess.run(
            ["python", "scripts/bootstrap_kbl.py", *args],
            cwd=REPO_ROOT, capture_output=True, text=True,
            env={**os.environ, "DATABASE_URL": INTEGRATION_DB},
        )

    def _count_kbl_teams(self, engine) -> int:
        from sqlalchemy import text
        with engine.begin() as conn:
            row = conn.execute(text(
                "SELECT COUNT(*) AS n FROM sp.teams "
                "WHERE country_code = 'KOR' "
                "AND sport_id = (SELECT id FROM sp.sports "
                "                WHERE name = 'Basketball')"
            )).first()
        return row.n

    def _count_kbl_aliases(self, engine) -> int:
        from sqlalchemy import text
        with engine.begin() as conn:
            row = conn.execute(text(
                "SELECT COUNT(*) AS n FROM sp.team_aliases "
                "WHERE source = 'bootstrap_league_coverage'"
            )).first()
        return row.n

    def test_full_insert_branch_happy_path(self, engine):
        """Empty sp.teams (no KBL rows) → bootstrap inserts all 10
        teams + all aliases. Counts match manifest expectations."""
        from scripts.kbl_seed import KBL_TEAMS_SEED

        assert self._count_kbl_teams(engine) == 0
        result = self._run_bootstrap()
        assert result.returncode == 0, (
            f"Bootstrap failed:\nstdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )
        assert self._count_kbl_teams(engine) == 10

        # Aliases: sum of all aliases tuples in manifest.
        expected_alias_count = sum(len(a) for _, _, a, _ in KBL_TEAMS_SEED)
        assert self._count_kbl_aliases(engine) == expected_alias_count

    def test_update_branch_backfill_for_goyang_sono(self, engine):
        """Pre-seed 'Goyang Sono' with country_code=NULL (mimics 2A.5
        legacy state). Run bootstrap. Verify the row's country_code
        is backfilled to 'KOR' AND the 'Goyang Sono Skygunners'
        alias gets inserted on the SAME row (not a new row)."""
        from sqlalchemy import text

        # Pre-seed the legacy state.
        legacy_id = uuid.uuid4()
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO sp.teams "
                "(id, sport_id, canonical_name, normalized_name, country_code) "
                "SELECT :id, s.id, 'Goyang Sono', 'goyang sono', NULL "
                "FROM sp.sports s WHERE s.name = 'Basketball'"
            ), {"id": legacy_id})

        # Run bootstrap.
        result = self._run_bootstrap()
        assert result.returncode == 0

        # Verify country_code backfilled on the SAME row id.
        with engine.begin() as conn:
            row = conn.execute(text(
                "SELECT id, country_code FROM sp.teams "
                "WHERE canonical_name = 'Goyang Sono'"
            )).first()
        assert row is not None
        assert row.id == legacy_id, (
            "Bootstrap inserted a new 'Goyang Sono' row instead of "
            "backfilling country_code on the existing legacy row. "
            "F1 contract broken."
        )
        assert row.country_code == "KOR"

        # Verify alias inserted on the SAME team_id.
        with engine.begin() as conn:
            alias_rows = conn.execute(text(
                "SELECT alias, source FROM sp.team_aliases "
                "WHERE team_id = :tid AND source = 'bootstrap_league_coverage'"
            ), {"tid": legacy_id}).all()
        alias_strings = {r.alias for r in alias_rows}
        assert "Goyang Sono Skygunners" in alias_strings, (
            "'Goyang Sono Skygunners' alias not attached to the "
            "backfilled team_id. UPDATE-branch alias-write broken."
        )

    def test_idempotency_second_run_is_noop(self, engine):
        """Run bootstrap twice. Second run should change nothing —
        same team count, same alias count, exit code 0."""
        result1 = self._run_bootstrap()
        assert result1.returncode == 0
        teams_after_first = self._count_kbl_teams(engine)
        aliases_after_first = self._count_kbl_aliases(engine)

        result2 = self._run_bootstrap()
        assert result2.returncode == 0
        assert self._count_kbl_teams(engine) == teams_after_first
        assert self._count_kbl_aliases(engine) == aliases_after_first

    def test_dry_run_writes_nothing(self, engine):
        """--dry-run flag: classifier runs, summary prints, but no
        writes happen."""
        assert self._count_kbl_teams(engine) == 0
        result = self._run_bootstrap("--dry-run")
        assert result.returncode == 0
        assert "Would insert" in result.stdout
        assert self._count_kbl_teams(engine) == 0
        assert self._count_kbl_aliases(engine) == 0

    def test_alias_source_is_bootstrap_league_coverage_on_inserted_rows(self, engine):
        """Every alias the bootstrap writes carries
        source='bootstrap_league_coverage'. Critical for analytics: distinguishes
        bootstrap-seeded aliases from operator-added ones."""
        from sqlalchemy import text
        result = self._run_bootstrap()
        assert result.returncode == 0
        with engine.begin() as conn:
            rows = conn.execute(text(
                "SELECT DISTINCT source FROM sp.team_aliases ta "
                "JOIN sp.teams t ON t.id = ta.team_id "
                "WHERE t.country_code = 'KOR' "
                "AND t.sport_id = (SELECT id FROM sp.sports "
                "                  WHERE name = 'Basketball')"
            )).all()
        sources = {r.source for r in rows}
        assert "bootstrap_league_coverage" in sources
        # No other sources should appear (test setup purges everything).
        assert sources == {"bootstrap_league_coverage"}, (
            f"Unexpected alias sources on KBL teams: {sources!r}"
        )
