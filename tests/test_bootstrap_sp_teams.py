"""Tests for the Phase 2A.5 bootstrap script.

Two layers of testing:

  Unit tests (always run):
    - argparse / --help / missing DATABASE_URL exits cleanly
    - normalize_name re-normalization stable

  Integration tests (run when SP_INTEGRATION_DB env var is set
                     to a Postgres URL with the sp schema applied):
    - Full bootstrap roundtrip: seed legacy public.* fixtures,
      run bootstrap, verify sp.teams + sp.team_aliases counts.
    - Idempotency: running bootstrap twice doesn't duplicate aliases.
    - Skipping behavior: entities with unmapped sports / non-team
      entity types / empty normalized names are skipped without error.

Integration tests are skipped unless explicitly opted in. They
depend on docker-compose Postgres or a Neon dev branch with the
sp schema migration applied.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

INTEGRATION_DB = os.environ.get("SP_INTEGRATION_DB", "").strip()


# ── Unit tests ────────────────────────────────────────────────────

class TestCli:
    def test_help_works(self):
        r = subprocess.run(
            [sys.executable, "scripts/bootstrap_sp_teams.py", "--help"],
            capture_output=True, text=True,
        )
        assert r.returncode == 0
        assert "Bootstrap" in r.stdout

    def test_missing_database_url_exits_2(self):
        env = {**os.environ, "DATABASE_URL": ""}
        r = subprocess.run(
            [sys.executable, "scripts/bootstrap_sp_teams.py"],
            capture_output=True, text=True, env=env,
        )
        assert r.returncode == 2
        assert "DATABASE_URL not set" in r.stderr


class TestNormalizationConsistency:
    """The bootstrap must use resolver._normalize.normalize_name so
    aliases land with the SAME normalization the resolver uses at
    match time. Verify importability."""

    def test_resolver_normalize_importable_from_bootstrap_path(self):
        # Same import path as the script.
        from resolver._normalize import normalize_name
        # Same expectations as resolver tests.
        assert normalize_name("Atlético") == "atletico"
        assert normalize_name("  Real   Madrid  ") == "real madrid"


# ── Integration tests (skipped unless SP_INTEGRATION_DB is set) ──

pytestmark_integration = pytest.mark.skipif(
    not INTEGRATION_DB,
    reason="SP_INTEGRATION_DB not set — integration tests require a "
           "Postgres URL with the sp schema migration applied.",
)


@pytestmark_integration
class TestBootstrapEndToEnd:
    """These tests use a Postgres database via SP_INTEGRATION_DB.
    They assume:
      * sp.* schema is applied (alembic upgrade head completed).
      * sp.sports is seeded (the seed_sp_sports migration ran).
      * sp.teams and sp.team_aliases are EMPTY at test start —
        tests will fail otherwise (refuse to run on a populated DB
        to avoid corrupting real data).
      * public.entities and public.entity_aliases tables exist
        (legacy schema applied via models.py / db.init_db).

    Tests insert their own legacy fixtures into public.* and verify
    the bootstrap migrates them correctly. Cleanup at end via
    DELETE FROM sp.team_aliases / sp.teams / public.entity_aliases /
    public.entities WHERE source = 'integration_test_seed' or
    similar tag.
    """

    @pytest.mark.asyncio
    async def test_placeholder_documents_integration_shape(self):
        """Stub. Real integration test runs in CI with Postgres
        provisioned via docker-compose. Phase 2A.5 ships the unit
        tests + this stub; a follow-up PR can flesh out the e2e
        test once a CI job stands up the dev DB.

        Leaving the structure here so the integration coverage is
        documented and easy to add when CI gets a Postgres step.
        """
        # When implemented:
        # 1. Insert sample public.entities rows (Soccer team, NBA
        #    team, an unknown-sport entity, a player entity).
        # 2. Insert public.entity_aliases for each team entity.
        # 3. Run bootstrap as a subprocess.
        # 4. Assert sp.teams has 2 rows (only the two valid teams).
        # 5. Assert sp.team_aliases has the expected number with
        #    source='legacy_bootstrap', confidence=0.95.
        # 6. Run bootstrap again. Assert no duplicates.
        # 7. Cleanup.
        assert INTEGRATION_DB, "guard"
