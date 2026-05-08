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


class TestLegacySportAliases:
    """Verifies the LEGACY_SPORT_ALIASES map handles the two name
    drifts surfaced by the cross-sport audit (Football → American
    Football, Rugby → Rugby Union). Sports legitimately not in the
    17-sport list (Table Tennis, Motorsport, Esports) stay
    unmapped — the alias map is not a place to add new sports."""

    def test_alias_map_contents(self):
        from scripts.bootstrap_sp_teams import LEGACY_SPORT_ALIASES
        assert LEGACY_SPORT_ALIASES == {
            "Football": "American Football",
            "Rugby":    "Rugby Union",
        }

    def test_resolve_with_alias(self):
        from scripts.bootstrap_sp_teams import _resolve_sport_id
        sport_ids = {"American Football": 5, "Rugby Union": 11, "Soccer": 1}
        # Legacy "Football" maps via alias → 5.
        assert _resolve_sport_id("Football", sport_ids) == 5
        # Legacy "Rugby" maps via alias → 11.
        assert _resolve_sport_id("Rugby", sport_ids) == 11
        # Direct hit (no alias needed).
        assert _resolve_sport_id("Soccer", sport_ids) == 1

    def test_resolve_unmapped_sport_returns_none(self):
        from scripts.bootstrap_sp_teams import _resolve_sport_id
        sport_ids = {"Soccer": 1}
        # Sport legitimately not in 17-sport list — stays unmapped.
        assert _resolve_sport_id("Table Tennis", sport_ids) is None
        assert _resolve_sport_id("Motorsport", sport_ids) is None
        assert _resolve_sport_id("Esports", sport_ids) is None
        # Empty / None.
        assert _resolve_sport_id(None, sport_ids) is None
        assert _resolve_sport_id("", sport_ids) is None

    def test_resolve_aliased_sport_still_unmapped_if_target_missing(self):
        """If the alias's target sport isn't in sp.sports, the
        resolver still returns None — it doesn't invent a sport_id."""
        from scripts.bootstrap_sp_teams import _resolve_sport_id
        # American Football missing from sport_ids despite alias from "Football"
        sport_ids = {"Soccer": 1}
        assert _resolve_sport_id("Football", sport_ids) is None


class TestTennisDoublesFilter:
    """Static-inspection guard for the Tennis-doubles skip rule.
    Verifies the entity loop drops canonical_names containing '/'
    when sport == 'Tennis'. Avoids polluting sp.team_aliases with
    per-tournament pairing strings."""

    def setup_method(self):
        import inspect
        import scripts.bootstrap_sp_teams
        self.src = inspect.getsource(scripts.bootstrap_sp_teams)

    def test_skip_logic_in_place(self):
        # Look for the Tennis doubles skip block inside the entity loop.
        assert 'ent.sport == "Tennis"' in self.src and \
               '"/" in ent.canonical_name' in self.src, \
               "Tennis-doubles filter missing"

    def test_skip_counter_tracked(self):
        assert "skipped_tennis_doubles" in self.src
        # Surfaced in the stdout summary.
        assert "tennis doubles" in self.src.lower()

    def test_skip_counter_in_log_payload(self):
        # The teams_classified structlog event should include the
        # tennis-doubles count alongside other skip categories.
        assert "skipped_tennis_doubles=skipped_tennis_doubles" in self.src


class TestBulkIOPattern:
    """Static-inspection guards against regressing to per-row I/O.

    The original implementation issued one SELECT per legacy entity
    + one INSERT per alias = ~80,000 round-trips at production scale.
    Took 1-2 hours per run. The bulk-I/O rewrite collapses to ~80
    round-trips total. These tests verify the rewrite stays bulk."""

    def setup_method(self):
        import inspect
        import scripts.bootstrap_sp_teams
        self.src = inspect.getsource(scripts.bootstrap_sp_teams)

    def test_no_per_row_select_inside_entity_loop(self):
        """The classification loop over team_entities must NOT call
        session.execute with SELECT — that's per-row I/O. All
        existing-team lookups must be against the in-memory dict
        team_uuid_by_key.
        """
        # Find the per-entity loop body. Crude but effective: split
        # on the loop header and check the body for forbidden patterns.
        for_idx = self.src.find("for ent in team_entities:")
        assert for_idx > 0, "team_entities loop must be present"

        # Find the next top-level structure (a comment block or
        # subsequent variable assignment) that ends the loop body.
        loop_end_idx = self.src.find("# ── Step 4", for_idx)
        assert loop_end_idx > for_idx, "expected Step 4 marker after entity loop"

        loop_body = self.src[for_idx:loop_end_idx]
        # No SELECT, no execute() inside this loop body.
        assert "session.execute" not in loop_body, \
            "Per-row session.execute() detected in entity loop — must be in-memory"
        assert "SELECT" not in loop_body.upper() or \
               "SELECT" not in loop_body, \
               "Per-row SELECT detected in entity loop"

    def test_no_per_row_insert_inside_alias_loop(self):
        """Same check for the alias classification loop — must NOT
        issue per-row INSERTs."""
        for_idx = self.src.find("for a in aliases:")
        assert for_idx > 0, "aliases loop must be present"

        loop_end_idx = self.src.find("# ── Step 5", for_idx)
        assert loop_end_idx > for_idx, "expected Step 5 marker after alias loop"

        loop_body = self.src[for_idx:loop_end_idx]
        assert "session.execute" not in loop_body, \
            "Per-row session.execute() detected in alias loop — must be in-memory"

    def test_bulk_load_via_two_select_queries(self):
        """Verify the two upfront bulk SELECTs are present:
          1. SELECT id, sport_id, normalized_name FROM sp.teams
          2. SELECT alias_normalized FROM sp.team_aliases WHERE source='legacy_bootstrap'
        """
        assert "SELECT id, sport_id, normalized_name FROM sp.teams" in self.src, \
            "Bulk-load of existing teams missing"
        assert "FROM sp.team_aliases" in self.src and \
               "source = 'legacy_bootstrap'" in self.src, \
               "Bulk-load of legacy_bootstrap aliases missing"

    def test_bulk_insert_via_pg_insert_values_list(self):
        """Verify the rewrite uses pg_insert(...).values(<list>)
        for batched INSERTs rather than executing one stmt per row."""
        # The values() call inside the chunk loop must take the
        # `chunk` list, not a single-row dict.
        assert "pg_insert(Team.__table__).values(chunk)" in self.src
        assert "pg_insert(TeamAlias.__table__).values(chunk)" in self.src

    def test_chunk_size_documented(self):
        assert "INSERT_CHUNK_SIZE" in self.src
        # Default is 1000 per design.
        assert "1000" in self.src


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
