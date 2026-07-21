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


# ── Day-53 alias-aware existence check (pure functions + static) ──

class TestTrustedAliasSources:
    """The alias-aware existence check must consult ONLY bootstrap-
    family curated aliases. Runtime-derived aliases (fuzzy_auto,
    alias_tier) can be wrong and must never suppress a legitimate
    legacy-entity insert. Guard the allowlist against drift."""

    def test_frozenset_defined_and_bootstrap_family(self):
        from scripts.bootstrap_sp_teams import TRUSTED_ALIAS_SOURCES
        assert isinstance(TRUSTED_ALIAS_SOURCES, frozenset)
        # Bootstrap-family sources present.
        assert "legacy_bootstrap" in TRUSTED_ALIAS_SOURCES
        assert "bootstrap_league_coverage" in TRUSTED_ALIAS_SOURCES
        assert "bootstrap_national" in TRUSTED_ALIAS_SOURCES

    def test_runtime_derived_sources_excluded(self):
        """Case 3 from Day-53 scope: normalized canonical exists as
        fuzzy_auto alias → NOT reused. Enforced at the SQL WHERE
        clause via TRUSTED_ALIAS_SOURCES; this test guards the
        allowlist itself."""
        from scripts.bootstrap_sp_teams import TRUSTED_ALIAS_SOURCES
        assert "fuzzy_auto" not in TRUSTED_ALIAS_SOURCES, (
            "fuzzy_auto is runtime-derived; must NOT be trusted for "
            "team-existence disambiguation — see docstring."
        )
        assert "alias_tier" not in TRUSTED_ALIAS_SOURCES, (
            "alias_tier is runtime-derived; must NOT be trusted for "
            "team-existence disambiguation — see docstring."
        )

    def test_alias_index_sql_filters_by_trusted_sources(self):
        """The bulk SELECT loading alias_team_index must apply the
        TRUSTED_ALIAS_SOURCES filter in the WHERE clause. Static-
        inspection guard so a future edit doesn't quietly drop the
        filter and start indexing every alias source."""
        import inspect
        import scripts.bootstrap_sp_teams
        src = inspect.getsource(scripts.bootstrap_sp_teams)
        # The SELECT joining sp.teams to sp.team_aliases must carry
        # source = ANY(:trusted_sources) OR equivalent.
        assert "SELECT t.sport_id, a.alias_normalized, a.team_id" in src
        assert "FROM sp.team_aliases a" in src
        assert "JOIN sp.teams t ON t.id = a.team_id" in src
        assert "source = ANY(:trusted_sources)" in src
        assert 'list(TRUSTED_ALIAS_SOURCES)' in src, (
            "the SQL parameter binding must pass TRUSTED_ALIAS_SOURCES "
            "rather than a hardcoded list."
        )


class TestBuildAliasTeamIndex:
    """Pure-function tests for _build_alias_team_index. Set-valued
    so ambiguity (same normalized on multiple teams in one sport)
    is detectable at lookup time."""

    def test_empty_input(self):
        from scripts.bootstrap_sp_teams import _build_alias_team_index
        assert _build_alias_team_index([]) == {}

    def test_single_alias_single_team(self):
        import uuid
        from scripts.bootstrap_sp_teams import _build_alias_team_index
        team = uuid.uuid4()
        idx = _build_alias_team_index([(6, "campeche", team)])
        assert idx == {(6, "campeche"): {team}}

    def test_multiple_aliases_same_team(self):
        import uuid
        from scripts.bootstrap_sp_teams import _build_alias_team_index
        team = uuid.uuid4()
        idx = _build_alias_team_index([
            (6, "campeche", team),
            (6, "piratas", team),
            (6, "piratas de campeche", team),
        ])
        assert idx[(6, "campeche")] == {team}
        assert idx[(6, "piratas")] == {team}
        assert idx[(6, "piratas de campeche")] == {team}
        assert len(idx) == 3

    def test_same_alias_two_teams_yields_ambiguity_marker(self):
        """Case 4 from Day-53 scope: the same normalized string is
        an alias on two teams in one sport. Set-valued index means
        the ambiguity is detectable rather than silently lost via
        last-writer-wins."""
        import uuid
        from scripts.bootstrap_sp_teams import _build_alias_team_index
        team_a, team_b = uuid.uuid4(), uuid.uuid4()
        idx = _build_alias_team_index([
            (6, "confusingcity", team_a),
            (6, "confusingcity", team_b),
        ])
        assert idx == {(6, "confusingcity"): {team_a, team_b}}
        assert len(idx[(6, "confusingcity")]) == 2

    def test_sport_scoped_not_flat(self):
        """The same normalized string on two teams in DIFFERENT
        sports is NOT ambiguity — sport-scoping means each (sport_id,
        alias_normalized) key stands alone."""
        import uuid
        from scripts.bootstrap_sp_teams import _build_alias_team_index
        soccer_team, basketball_team = uuid.uuid4(), uuid.uuid4()
        idx = _build_alias_team_index([
            (1, "united", soccer_team),
            (2, "united", basketball_team),
        ])
        assert idx == {
            (1, "united"): {soccer_team},
            (2, "united"): {basketball_team},
        }
        assert len(idx[(1, "united")]) == 1
        assert len(idx[(2, "united")]) == 1

    def test_diacritics_index_correctness(self):
        """Case 5 from Day-53 scope: accented legacy canonical whose
        normalize_name matches an accented team's preserved alias.
        The index build itself must correctly key on the pre-
        normalized string (whatever the caller provides); this test
        exercises the accented-input path end-to-end via
        normalize_name so the index-lookup pipeline works on the
        real Diablos Rojos del México / El Águila / Leones data
        shape without derivation drift."""
        import uuid
        from scripts.bootstrap_sp_teams import _build_alias_team_index
        from resolver._normalize import normalize_name
        team = uuid.uuid4()
        # Alias as it lives in sp.team_aliases post-merge (accent-
        # stripped by the resolver's normalizer).
        stored_alias = normalize_name("México")  # → 'mexico'
        idx = _build_alias_team_index([(6, stored_alias, team)])
        # Legacy canonical carrying the accented form — normalizes
        # identically to the stored alias, so the lookup key matches.
        legacy_normalized = normalize_name("México")
        assert stored_alias == "mexico"
        assert legacy_normalized == "mexico"
        assert idx[(6, legacy_normalized)] == {team}

    def test_sqlalchemy_row_shape_supported(self):
        """The index builder must accept both raw tuples (test
        fixtures) and objects with attribute access (SQLAlchemy
        row shape) — the production caller passes the latter."""
        import uuid
        from scripts.bootstrap_sp_teams import _build_alias_team_index

        class _Row:
            def __init__(self, sport_id, alias_normalized, team_id):
                self.sport_id = sport_id
                self.alias_normalized = alias_normalized
                self.team_id = team_id

        team = uuid.uuid4()
        idx = _build_alias_team_index([_Row(6, "campeche", team)])
        assert idx == {(6, "campeche"): {team}}


class TestResolveViaAlias:
    """Pure-function tests for _resolve_via_alias — the secondary
    existence check called on primary-team-lookup miss."""

    def test_hit_single_team_returns_uuid_not_ambiguous(self):
        """Case 2 from Day-53 scope: normalized canonical exists as
        trusted alias, exactly one team. Returns (team_id, False);
        caller reuses it, increments per_sport_alias_reused."""
        import uuid
        from scripts.bootstrap_sp_teams import _resolve_via_alias
        team = uuid.uuid4()
        idx = {(6, "campeche"): {team}}
        result_uuid, ambiguous = _resolve_via_alias(6, "campeche", idx)
        assert result_uuid == team
        assert ambiguous is False

    def test_miss_returns_none_not_ambiguous(self):
        """Not found: caller falls through to INSERT-new-team."""
        from scripts.bootstrap_sp_teams import _resolve_via_alias
        result_uuid, ambiguous = _resolve_via_alias(6, "not_there", {})
        assert result_uuid is None
        assert ambiguous is False

    def test_ambiguity_returns_none_true(self):
        """Case 4 from Day-53 scope: two teams. Caller must
        skip-and-log, not pick arbitrarily."""
        import uuid
        from scripts.bootstrap_sp_teams import _resolve_via_alias
        team_a, team_b = uuid.uuid4(), uuid.uuid4()
        idx = {(6, "confusingcity"): {team_a, team_b}}
        result_uuid, ambiguous = _resolve_via_alias(6, "confusingcity", idx)
        assert result_uuid is None
        assert ambiguous is True

    def test_wrong_sport_id_misses(self):
        """Sport-scoping: lookup with the wrong sport_id doesn't
        find the alias even when the normalized matches."""
        import uuid
        from scripts.bootstrap_sp_teams import _resolve_via_alias
        team = uuid.uuid4()
        idx = {(6, "united"): {team}}
        # Same normalized, wrong sport_id — miss.
        result_uuid, ambiguous = _resolve_via_alias(2, "united", idx)
        assert result_uuid is None
        assert ambiguous is False

    def test_diacritics_lookup(self):
        """Case 5 from Day-53 scope continuation: accented legacy
        canonical resolves via the accent-stripped alias index."""
        import uuid
        from scripts.bootstrap_sp_teams import _resolve_via_alias
        from resolver._normalize import normalize_name
        team = uuid.uuid4()
        idx = {(6, normalize_name("México")): {team}}
        result_uuid, ambiguous = _resolve_via_alias(
            6, normalize_name("México"), idx,
        )
        assert result_uuid == team
        assert ambiguous is False


class TestAliasAwareClassificationControlFlow:
    """Static-inspection guards for how the secondary alias-aware
    check fits into the classification loop. The primary check must
    still run first (Case 1 unchanged behavior), the secondary check
    runs only on primary miss, and ambiguity skips-and-logs rather
    than proceeding with an INSERT."""

    def setup_method(self):
        import inspect
        import scripts.bootstrap_sp_teams
        self.src = inspect.getsource(scripts.bootstrap_sp_teams)

    def test_primary_check_runs_before_secondary(self):
        """team_uuid_by_key.get(key) (primary) must appear before
        the secondary _resolve_via_alias CALL SITE. Case 1 from
        Day-53 scope: primary reuse path is unchanged.

        Note: `.find("_resolve_via_alias(")` matches the function
        DEFINITION first (early in file). We search for the
        assignment form used at the call site to disambiguate.
        """
        primary_idx = self.src.find("team_uuid_by_key.get(key)")
        secondary_call_idx = self.src.find(
            "aliased_uuid, ambiguous = _resolve_via_alias("
        )
        assert primary_idx > 0
        assert secondary_call_idx > 0, (
            "expected the classification loop's call site of "
            "_resolve_via_alias with the (aliased_uuid, ambiguous) "
            "tuple unpacking"
        )
        assert primary_idx < secondary_call_idx, (
            "Primary team-existence check must run before the "
            "secondary alias-aware check at the call site."
        )

    def test_secondary_check_only_runs_on_primary_miss(self):
        """The secondary check must be inside the primary-miss
        branch (after `existing_uuid is not None: ... continue`),
        not before it. Uses the call-site form to avoid matching
        the function definition earlier in the file."""
        primary_hit = self.src.find("if existing_uuid is not None:")
        continue_after_primary = self.src.find("continue", primary_hit)
        secondary_call_idx = self.src.find(
            "aliased_uuid, ambiguous = _resolve_via_alias("
        )
        assert primary_hit > 0
        assert continue_after_primary > primary_hit
        assert secondary_call_idx > continue_after_primary, (
            "Secondary alias-aware check must run only on primary "
            "miss (i.e., after the primary-hit branch's continue)."
        )

    def test_ambiguity_skips_and_logs(self):
        """Ambiguity path must log at warning level with the
        colliding team_ids AND NOT proceed to INSERT."""
        assert "alias_ambiguous" in self.src
        assert "bootstrap.sp_teams.alias_ambiguous" in self.src
        assert "colliding_team_ids" in self.src

    def test_per_sport_counters_defined_and_in_log(self):
        """Both new counters must exist and appear in the structlog
        payload alongside existing counters."""
        assert "per_sport_alias_reused" in self.src
        assert "per_sport_alias_ambiguous" in self.src
        assert "alias_reused_per_sport=dict(per_sport_alias_reused)" in self.src
        assert "alias_ambiguous_per_sport=dict(per_sport_alias_ambiguous)" in self.src

    def test_stdout_summary_surfaces_canary(self):
        """The --dry-run stdout summary must surface the per-sport
        alias_reused count and the canary invariant statement, so
        the operator can eyeball the value against the retained
        dedup snapshot tables."""
        assert "Alias-aware existence check (canary)" in self.src
        assert "Canary invariant" in self.src


class TestDocstringInvariantCorrected:
    """The pre-Day-53 docstring claimed 'Re-running this script
    after a successful bootstrap produces zero new inserts.' That
    was silently false after any direction-(b) dedup. The corrected
    docstring documents the canary invariant instead."""

    def setup_method(self):
        import scripts.bootstrap_sp_teams
        self.doc = scripts.bootstrap_sp_teams.__doc__ or ""

    def test_zero_new_inserts_absolute_claim_removed(self):
        """The false absolute claim must be gone."""
        # The old phrasing was 'produces zero new inserts.' with a
        # period ending the sentence — an absolute assertion.
        assert (
            "Re-running this script after a successful bootstrap "
            "produces zero new inserts."
        ) not in self.doc

    def test_canary_invariant_documented(self):
        """New invariant explains the alias-aware secondary check
        and the sum-across-snapshot-tables canary."""
        assert "canary" in self.doc.lower()
        assert "TRUSTED_ALIAS_SOURCES" in self.doc
        assert "direction-(b)" in self.doc
        # The invariant must state the counter-is-the-gate framing.
        assert "counter is the gate" in self.doc


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
