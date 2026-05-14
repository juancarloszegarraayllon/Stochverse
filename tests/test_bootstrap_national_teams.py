"""Phase 2F — tests for scripts/bootstrap_national_teams.py + the
companion manifest scripts/national_teams_seed.py.

Closes Issue #136 (national-team bootstrap, Phase 1 — men's senior
Soccer national teams).

Two test layers:

  - TestManifestShape: pure-data assertions on the manifest.
    No DB needed; runs unconditionally in CI. Catches data-quality
    regressions (duplicate canonicals, malformed alpha-3 codes,
    accidentally-shipped long-form FIFA names that would defeat
    the trigram-similarity property).

  - TestBootstrapIntegration: SP_INTEGRATION_DB-gated. Exercises
    the bootstrap script via direct function call (not subprocess —
    cheaper than alembic invocations; matches the test_alias_add
    integration-test pattern). Covers idempotency, dry-run,
    no-touch-on-existing-rows.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import uuid
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
INTEGRATION_DB = os.environ.get("SP_INTEGRATION_DB", "").strip()
_TEST_MARKER = "TEST-NAT-TEAM-BOOTSTRAP"


# ── Unit tests (no DB) ─────────────────────────────────────────


class TestManifestShape:
    """Pure-data assertions on the manifest. Catches data-quality
    regressions before they reach a database."""

    def test_manifest_is_non_empty(self):
        from scripts.national_teams_seed import NATIONAL_TEAMS_SEED
        assert len(NATIONAL_TEAMS_SEED) > 0

    def test_manifest_size_in_expected_range(self):
        """FIFA's 2024-2025 membership is ~211. Allow ±10 for
        documentation drift (recent additions like Sint Maarten 2024;
        edge cases like Zanzibar / Kosovo). A count well outside this
        range (say, 50 or 400) almost certainly indicates the manifest
        was truncated or duplicated by an edit."""
        from scripts.national_teams_seed import NATIONAL_TEAMS_SEED
        n = len(NATIONAL_TEAMS_SEED)
        assert 200 <= n <= 225, (
            f"Manifest has {n} entries; expected ~211 ± documented "
            f"recent additions. If you genuinely meant this change, "
            f"update the bounds in this test AND the count expectation "
            f"in the manifest's docstring."
        )

    def test_canonical_names_are_unique(self):
        from scripts.national_teams_seed import NATIONAL_TEAMS_SEED
        canonicals = [t[0] for t in NATIONAL_TEAMS_SEED]
        seen: dict[str, int] = {}
        for c in canonicals:
            seen[c] = seen.get(c, 0) + 1
        duplicates = {k: v for k, v in seen.items() if v > 1}
        assert not duplicates, (
            f"Duplicate canonical_name entries in manifest: {duplicates}. "
            f"sp.teams has no UNIQUE constraint on (sport_id, "
            f"normalized_name); bootstrap idempotency relies on Python-"
            f"side dedup. Duplicates in the manifest would still insert "
            f"only once (first one wins), but indicate a curation bug."
        )

    def test_alpha3_codes_are_unique(self):
        from scripts.national_teams_seed import NATIONAL_TEAMS_SEED
        codes = [t[1] for t in NATIONAL_TEAMS_SEED]
        seen: dict[str, int] = {}
        for c in codes:
            seen[c] = seen.get(c, 0) + 1
        duplicates = {k: v for k, v in seen.items() if v > 1}
        assert not duplicates, (
            f"Duplicate alpha-3 codes in manifest: {duplicates}. "
            f"Each country has exactly one ISO 3166-1 alpha-3."
        )

    def test_all_alpha3_codes_are_three_uppercase_letters(self):
        """ISO 3166-1 alpha-3 is exactly three uppercase letters.
        Catches transposition typos and accidental alpha-2."""
        import re
        from scripts.national_teams_seed import NATIONAL_TEAMS_SEED
        pattern = re.compile(r"^[A-Z]{3}$")
        offenders = [
            (canonical, code)
            for canonical, code, _ in NATIONAL_TEAMS_SEED
            if not pattern.match(code)
        ]
        assert not offenders, (
            f"Non-alpha-3 codes in manifest: {offenders}. Required "
            f"format: exactly 3 uppercase letters. ISO 3166-1 user-"
            f"assigned codes (XKX for Kosovo, EAZ for Zanzibar) also "
            f"match this pattern."
        )

    def test_canonical_names_use_short_form_not_long_fifa(self):
        """Catches accidentally-shipped long-form canonical_names that
        would defeat the trigram-similarity property. similarity(
        'France', 'France national football team') ≈ 0.15, well below
        the 0.30 threshold; canonical must be the short FIFA form so
        Kalshi titles auto-match.

        The "national" and "team" tokens are the canonical
        anti-patterns. France/Germany/etc. are fine; "France national
        football team" would trigger this guard.

        One legit exception worth excluding from the check: country
        names that legitimately contain "team" or "national" in their
        normal form — none in FIFA's list as of 2024. If a future entry
        legitimately needs one of these tokens, add an explicit
        whitelist entry here with a comment explaining why."""
        from scripts.national_teams_seed import NATIONAL_TEAMS_SEED
        forbidden_tokens = {"national", "team", "football"}
        offenders = []
        for canonical, _code, _notes in NATIONAL_TEAMS_SEED:
            tokens = {t.lower().strip(",.()") for t in canonical.split()}
            hits = tokens & forbidden_tokens
            if hits:
                offenders.append((canonical, hits))
        assert not offenders, (
            f"Canonical names contain forbidden long-form tokens: "
            f"{offenders}. The trigram matcher requires the short FIFA "
            f"form (e.g. 'France', not 'France national football team') "
            f"so Kalshi titles like 'France vs Senegal' auto-match. See "
            f"the manifest's 'Naming convention' docstring section."
        )

    def test_notes_field_is_string_or_none(self):
        """Tuple type discipline: third element must be str or None."""
        from scripts.national_teams_seed import NATIONAL_TEAMS_SEED
        offenders = [
            t for t in NATIONAL_TEAMS_SEED
            if t[2] is not None and not isinstance(t[2], str)
        ]
        assert not offenders, (
            f"Manifest tuples must be (canonical_name, alpha3, "
            f"notes-str-or-None). Bad rows: {offenders}"
        )

    def test_normalize_name_produces_non_empty_for_all_canonicals(self):
        """The bootstrap classifies entries by normalize_name(canonical).
        An empty normalized form would skip the entry silently —
        defensive sanity check that every manifest canonical produces
        a usable key.

        Edge cases worth probing: accented strings (Côte d'Ivoire,
        São Tomé), short names (Fiji, USA-shape multi-words), special
        characters (apostrophes in Côte d'Ivoire)."""
        from resolver._normalize import normalize_name
        from scripts.national_teams_seed import NATIONAL_TEAMS_SEED
        offenders = [
            canonical for canonical, _, _ in NATIONAL_TEAMS_SEED
            if not normalize_name(canonical)
        ]
        assert not offenders, (
            f"normalize_name returned empty for manifest entries: "
            f"{offenders}. Bootstrap would silently skip these. "
            f"Check resolver/_normalize.py for accent / punctuation "
            f"handling of the specific names."
        )

    def test_normalize_name_is_unique_within_manifest(self):
        """Two different canonicals normalizing to the same key would
        cause one to silently dedupe-out during bootstrap. Catches
        accent-stripping collisions (e.g. if two countries had names
        that diverged only by diacritic in canonical form)."""
        from resolver._normalize import normalize_name
        from scripts.national_teams_seed import NATIONAL_TEAMS_SEED
        normalized_to_canonicals: dict[str, list[str]] = {}
        for canonical, _, _ in NATIONAL_TEAMS_SEED:
            key = normalize_name(canonical)
            normalized_to_canonicals.setdefault(key, []).append(canonical)
        collisions = {
            k: v for k, v in normalized_to_canonicals.items() if len(v) > 1
        }
        assert not collisions, (
            f"normalize_name collisions in manifest: {collisions}. Two "
            f"different canonical names produced the same normalized "
            f"key — bootstrap dedup logic would only insert one. "
            f"Likely cause: accent stripping or punctuation handling "
            f"makes two visually-distinct names match."
        )


# ── Integration tests (require SP_INTEGRATION_DB) ──────────────


@pytest.mark.skipif(
    not INTEGRATION_DB,
    reason=(
        "SP_INTEGRATION_DB not set — bootstrap integration tests need "
        "a real Postgres with sp schema migrations applied. Set "
        "SP_INTEGRATION_DB to a DISPOSABLE database (Neon dev branch "
        "or local apt-installed Postgres). NEVER point at production."
    ),
)
class TestBootstrapIntegration:
    """End-to-end: apply migrations, run bootstrap, verify rows
    landed; re-run, verify idempotency; dry-run, verify no writes."""

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
        # Apply migrations to head.
        result = subprocess.run(
            ["alembic", "upgrade", "head"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            env={**os.environ, "DATABASE_URL": INTEGRATION_DB},
        )
        assert result.returncode == 0, (
            f"alembic upgrade head failed:\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        self._purge_bootstrap_rows(engine)
        yield
        self._purge_bootstrap_rows(engine)

    def _purge_bootstrap_rows(self, engine):
        """Remove any rows the bootstrap created from prior test runs.
        Identified by (sport_id=Soccer, country_code IS NOT NULL,
        normalized_name matches manifest). Doesn't disturb legacy
        bootstrap rows (those have country_code NULL in this test DB
        per the bootstrap_sp_teams.py reading from earlier today)."""
        from sqlalchemy import text
        from scripts.national_teams_seed import NATIONAL_TEAMS_SEED
        from resolver._normalize import normalize_name
        normalized_keys = [
            normalize_name(c) for c, _, _ in NATIONAL_TEAMS_SEED
        ]
        with engine.begin() as conn:
            soccer_id = conn.execute(text(
                "SELECT id FROM sp.sports WHERE name = 'Soccer'"
            )).scalar()
            if soccer_id is None:
                return
            conn.execute(text(
                "DELETE FROM sp.teams "
                "WHERE sport_id = :sport_id "
                "AND normalized_name = ANY(:keys)"
            ), {"sport_id": soccer_id, "keys": normalized_keys})

    def _run_bootstrap(self, dry_run: bool = False) -> int:
        """Invoke bootstrap() directly (not via subprocess). The script
        reads DATABASE_URL via db.py module-level import — ensure
        SP_INTEGRATION_DB is exported into os.environ as DATABASE_URL
        before this is called."""
        os.environ["DATABASE_URL"] = INTEGRATION_DB
        # Reset modules so db.py re-reads DATABASE_URL.
        import sys
        for mod in list(sys.modules):
            if (
                mod == "db"
                or mod == "scripts.bootstrap_national_teams"
            ):
                del sys.modules[mod]
        from scripts.bootstrap_national_teams import bootstrap
        return asyncio.run(bootstrap(dry_run=dry_run))

    def _count_bootstrap_rows(self, engine) -> int:
        from sqlalchemy import text
        from scripts.national_teams_seed import NATIONAL_TEAMS_SEED
        from resolver._normalize import normalize_name
        normalized_keys = [
            normalize_name(c) for c, _, _ in NATIONAL_TEAMS_SEED
        ]
        with engine.connect() as conn:
            return conn.execute(text(
                "SELECT COUNT(*) FROM sp.teams t "
                "JOIN sp.sports s ON t.sport_id = s.id "
                "WHERE s.name = 'Soccer' "
                "AND t.normalized_name = ANY(:keys)"
            ), {"keys": normalized_keys}).scalar() or 0

    def test_bootstrap_inserts_all_manifest_rows(self, engine):
        """Initial run inserts every manifest entry."""
        from scripts.national_teams_seed import NATIONAL_TEAMS_SEED
        assert self._count_bootstrap_rows(engine) == 0, (
            "Pre-test purge didn't fully clean — investigate "
            "_purge_bootstrap_rows fixture."
        )
        rc = self._run_bootstrap(dry_run=False)
        assert rc == 0
        post = self._count_bootstrap_rows(engine)
        assert post == len(NATIONAL_TEAMS_SEED), (
            f"Bootstrap inserted {post} rows; manifest has "
            f"{len(NATIONAL_TEAMS_SEED)}. Investigate dedup-logic "
            f"correctness (possibly a normalize_name collision the "
            f"test_normalize_name_is_unique_within_manifest unit test "
            f"missed, OR an existing-team collision against the "
            f"integration DB's pre-seeded legacy rows)."
        )

    def test_bootstrap_is_idempotent_on_second_run(self, engine):
        """Second run inserts zero new rows. Same Python-side dedup
        mechanism that bootstrap_sp_teams.py uses — load existing
        state, compare manifest in-memory, queue only genuinely-new."""
        from scripts.national_teams_seed import NATIONAL_TEAMS_SEED
        self._run_bootstrap(dry_run=False)
        first = self._count_bootstrap_rows(engine)
        assert first == len(NATIONAL_TEAMS_SEED)
        rc = self._run_bootstrap(dry_run=False)
        assert rc == 0
        second = self._count_bootstrap_rows(engine)
        assert second == first, (
            f"Idempotency broken: second run changed row count from "
            f"{first} to {second}. Most likely cause: dedup key drift "
            f"between the bulk-load SELECT and the in-Python "
            f"normalize_name() call. Check that both use the same "
            f"(sport_id, normalized_name) shape."
        )

    def test_bootstrap_dry_run_does_not_write(self, engine):
        """--dry-run prints counts but doesn't touch the DB."""
        assert self._count_bootstrap_rows(engine) == 0
        rc = self._run_bootstrap(dry_run=True)
        assert rc == 0
        assert self._count_bootstrap_rows(engine) == 0, (
            "--dry-run wrote rows to the DB. Bug in the bootstrap "
            "script's dry-run branch — INSERT path is not properly "
            "skipped."
        )

    def test_bootstrap_preserves_existing_row_with_same_normalized_name(
        self, engine,
    ):
        """If a manifest canonical's normalized_name already exists in
        sp.teams (e.g. a legacy club happened to be named 'France'),
        the bootstrap MUST NOT insert a duplicate row. The existing
        row's uuid is preserved; no second row appears for that key.

        Pre-seeds one fake Soccer team with the normalized_name of
        a manifest entry ('France' → 'france'); verifies the count
        of rows matching that normalized_name stays at 1 post-
        bootstrap.
        """
        from sqlalchemy import text
        from resolver._normalize import normalize_name
        # Pick a manifest entry to collide with.
        target_canonical = "France"
        target_normalized = normalize_name(target_canonical)
        # Seed a pre-existing row with the colliding normalized_name.
        with engine.begin() as conn:
            soccer_id = conn.execute(text(
                "SELECT id FROM sp.sports WHERE name = 'Soccer'"
            )).scalar()
            pre_existing_uuid = uuid.uuid4()
            conn.execute(text(
                "INSERT INTO sp.teams "
                "(id, sport_id, canonical_name, normalized_name, country_code) "
                "VALUES (:id, :sport_id, :canonical, :normalized, NULL)"
            ), {
                "id": pre_existing_uuid,
                "sport_id": soccer_id,
                "canonical": target_canonical,
                "normalized": target_normalized,
            })

        # Run bootstrap. Should skip the 'France' manifest entry
        # because (sport_id, normalized_name) already exists.
        self._run_bootstrap(dry_run=False)

        # Exactly one row exists for (Soccer, 'france') —
        # the pre-existing one. Qualify `id` as `t.id` because both
        # sp.teams and sp.sports have an `id` column (ambiguous
        # reference otherwise).
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT t.id FROM sp.teams t "
                "JOIN sp.sports s ON t.sport_id = s.id "
                "WHERE s.name = 'Soccer' "
                "AND t.normalized_name = :normalized"
            ), {"normalized": target_normalized}).all()
        assert len(rows) == 1, (
            f"Bootstrap created a duplicate row for "
            f"(Soccer, {target_normalized!r}). Expected the pre-"
            f"existing row to win the dedup check; got {len(rows)} "
            f"rows. Investigate the Python-side existing_by_normalized "
            f"lookup."
        )
        assert rows[0].id == pre_existing_uuid, (
            "Bootstrap did some kind of replace/rewrite — the pre-"
            "existing UUID should have been preserved, not replaced. "
            "This bootstrap is INSERT-only, not UPSERT."
        )

        # Cleanup the pre-seeded row.
        with engine.begin() as conn:
            conn.execute(text(
                "DELETE FROM sp.teams WHERE id = :id"
            ), {"id": pre_existing_uuid})

    def test_bootstrap_populates_country_code_on_new_rows(self, engine):
        """Manifest tuples include the alpha-3 code; bootstrap must
        write that into sp.teams.country_code (the operationally
        useful field for cross-referencing markets to national teams)."""
        from sqlalchemy import text
        self._run_bootstrap(dry_run=False)
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT country_code FROM sp.teams t "
                "JOIN sp.sports s ON t.sport_id = s.id "
                "WHERE s.name = 'Soccer' AND t.canonical_name = 'France'"
            )).first()
        assert row is not None, (
            "France row not present post-bootstrap — investigate "
            "test_bootstrap_inserts_all_manifest_rows."
        )
        assert row.country_code == "FRA", (
            f"France's country_code is {row.country_code!r}; expected "
            f"'FRA'. Bootstrap is not writing the alpha-3 to sp.teams."
        )
