"""Tests for scripts/run_reresolution_pass.py — Phase 2E loop.

Pure-function tests (no DB required):
- F1 Tier-1 SQL string shape: allowlist parameter, asymmetric_excluded
  filter, latest-decision semantics.
- _extract_team_ids_from_reason_detail walks the JSONB defensively
  across known team_id key shapes.
- _filter_tier2 enforces the LOOSE F1a semantics (any team_id in
  reason_detail + alias-add OR fixture-state signal > last decided_at).
- _evaluate_halt_criteria branches (F6 thresholds).
- CLI: default dry-run, --apply requires DATABASE_URL, --candidate-set
  parses.

Real-DB integration tests (SP_INTEGRATION_DB-gated) are stubbed for
the operator to run once dry-run is approved — same convention as
tests/test_resolver_2b.py::TestResolverIntegration.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from scripts.run_reresolution_pass import (
    CANDIDATE_SELECT_LATENCY_CEILING_MS,
    CANDIDATE_SET_MULTIPLIER_CEILING,
    HARD_LIMIT_CANDIDATE_SET,
    LOOP_ELIGIBLE_FAIL_REASONS,
    TIER1_SQL_FL,
    TIER1_SQL_KALSHI,
    _evaluate_halt_criteria,
    _extract_team_ids_from_reason_detail,
    _filter_tier2,
    cli_main,
    parse_candidate_set,
)


# ──────────────────────────────────────────────────────────────────
# F1 allowlist + Tier-1 SQL shape
# ──────────────────────────────────────────────────────────────────


class TestLoopEligibleAllowlist:
    """Day-41 sizing identified exactly 5 fail_reason categories that
    are loop-addressable. The allowlist must match — adding /
    removing a category here is a scope change."""

    def test_exactly_five_categories(self):
        assert len(LOOP_ELIGIBLE_FAIL_REASONS) == 5

    def test_no_duplicates(self):
        assert len(set(LOOP_ELIGIBLE_FAIL_REASONS)) == len(
            LOOP_ELIGIBLE_FAIL_REASONS
        )

    def test_specific_categories_present(self):
        expected = {
            "fuzzy_no_team_resemblance",
            "fuzzy_collision_no_anchor",
            "alias_no_team_resemblance",
            "below_review_threshold",
            "alias_resolution_incomplete",
        }
        assert set(LOOP_ELIGIBLE_FAIL_REASONS) == expected

    def test_excludes_known_non_addressable_categories(self):
        """Day-41 sizing: these reason categories are NOT addressable
        by alias-add (~19,243 records / 54% of the gross no_match
        population). Their absence from the allowlist is the
        structural pre-filter doing its job."""
        for cat in (
            "structural_normalize_failed",  # 8,521 Golf single-player
            "sport_not_classified",         # 3,941 Esports / contaminants
            "deferred_to_2d",               # 2,528 non-terminal artifact
            "kickoff_confidence_below_threshold",  # 53 kickoff-side
        ):
            assert cat not in LOOP_ELIGIBLE_FAIL_REASONS, (
                f"{cat!r} is structurally not loop-addressable per "
                "Day-41 sizing — must not be in the allowlist"
            )


class TestTier1SQLShape:
    """Static-source guards on the Tier-1 SQL. Pure-text assertions —
    no DB connection required. Catches accidental dropping of the
    structural filters that the partial expression index relies on."""

    def test_fl_sql_carries_allowlist_filter(self):
        assert "fail_reason" in TIER1_SQL_FL
        assert ":allowlist" in TIER1_SQL_FL

    def test_fl_sql_carries_asymmetric_excluded_filter(self):
        assert "asymmetric_excluded" in TIER1_SQL_FL
        assert "IS NULL" in TIER1_SQL_FL

    def test_fl_sql_carries_fixture_id_null(self):
        assert "fixture_id IS NULL" in TIER1_SQL_FL

    def test_fl_sql_uses_distinct_on_for_latest_decision(self):
        assert "DISTINCT ON" in TIER1_SQL_FL
        assert "ORDER BY" in TIER1_SQL_FL and "DESC" in TIER1_SQL_FL

    def test_fl_sql_restricts_to_no_match(self):
        assert "reason_code = 'no_match'" in TIER1_SQL_FL

    def test_kalshi_sql_carries_same_filters(self):
        for needle in (
            "fail_reason", ":allowlist", "asymmetric_excluded",
            "fixture_id IS NULL", "DISTINCT ON",
            "reason_code = 'no_match'",
        ):
            assert needle in TIER1_SQL_KALSHI

    def test_fl_sql_joins_sp_fl_events(self):
        assert "sp.fl_events" in TIER1_SQL_FL

    def test_kalshi_sql_joins_sp_kalshi_markets(self):
        assert "sp.kalshi_markets" in TIER1_SQL_KALSHI


# ──────────────────────────────────────────────────────────────────
# _extract_team_ids_from_reason_detail
# ──────────────────────────────────────────────────────────────────


class TestExtractTeamIds:
    """Loose F1a semantics — any team_id key in reason_detail counts.
    The walker must handle every known shape AND degrade gracefully
    on malformed inputs."""

    def test_empty_returns_empty_set(self):
        assert _extract_team_ids_from_reason_detail(None) == set()
        assert _extract_team_ids_from_reason_detail({}) == set()
        assert _extract_team_ids_from_reason_detail("not a dict") == set()

    def test_top_level_team_id_string(self):
        rd = {"home_team_id": "ABC-123", "kickoff": "2026-06-01"}
        assert _extract_team_ids_from_reason_detail(rd) == {"abc-123"}

    def test_colliding_team_ids_list(self):
        rd = {
            "colliding_home_team_ids": ["aaa", "bbb"],
            "colliding_away_team_ids": ["ccc"],
        }
        assert _extract_team_ids_from_reason_detail(rd) == {
            "aaa", "bbb", "ccc",
        }

    def test_asymmetric_failed_side_candidate_team_ids(self):
        """Fuzzy-tier asymmetric routing per
        resolver/fuzzy_tier/matcher.py:521-538."""
        rd = {
            "asymmetric_failed_side_candidate_team_ids": [
                "ddd", "eee", "fff",
            ],
        }
        assert _extract_team_ids_from_reason_detail(rd) == {
            "ddd", "eee", "fff",
        }

    def test_nested_dict_walked(self):
        rd = {"details": {"inner": {"candidate_team_id": "ZZZ"}}}
        assert _extract_team_ids_from_reason_detail(rd) == {"zzz"}

    def test_mixed_shape(self):
        rd = {
            "home_team_id": "AA",
            "colliding_away_team_ids": ["BB", "CC"],
            "asymmetric_failed_side_candidate_team_ids": ["DD"],
            "kickoff": "2026-06-01T15:00:00Z",
            "competition_id": "irrelevant",
        }
        assert _extract_team_ids_from_reason_detail(rd) == {
            "aa", "bb", "cc", "dd",
        }

    def test_lowercases_uuids(self):
        rd = {"home_team_id": "ABC-DEF-123"}
        assert _extract_team_ids_from_reason_detail(rd) == {"abc-def-123"}

    def test_ignores_non_string_in_list(self):
        rd = {"colliding_home_team_ids": ["aaa", 42, None, "bbb"]}
        assert _extract_team_ids_from_reason_detail(rd) == {"aaa", "bbb"}


# ──────────────────────────────────────────────────────────────────
# _filter_tier2 — LOOSE F1a semantics
# ──────────────────────────────────────────────────────────────────


class TestFilterTier2:
    """Tier-2 enforces: at least one team_id in reason_detail has
    either an alias_created_at > row.decided_at (alias-add signal)
    or a fixture_created_at > row.decided_at (fixture-state signal).
    LOOSE per F1a: any team_id in reason_detail qualifies."""

    def _now(self) -> datetime:
        return datetime(2026, 6, 19, 12, 0, 0, tzinfo=timezone.utc)

    def test_no_team_ids_in_reason_detail_dropped(self):
        row = {
            "provider_record_id": "fl:1",
            "reason_detail": {"unrelated_key": "value"},
            "decided_at": self._now() - timedelta(days=1),
        }
        assert _filter_tier2([row], {}, {}) == []

    def test_alias_add_signal_passes(self):
        decided = self._now() - timedelta(days=2)
        row = {
            "provider_record_id": "fl:1",
            "reason_detail": {"home_team_id": "team-a"},
            "decided_at": decided,
        }
        # alias created AFTER the prior decision → signal
        aliases = {"team-a": [self._now() - timedelta(hours=1)]}
        result = _filter_tier2([row], aliases, {})
        assert len(result) == 1
        assert result[0]["provider_record_id"] == "fl:1"

    def test_alias_predates_decision_dropped(self):
        decided = self._now() - timedelta(hours=1)
        row = {
            "provider_record_id": "fl:1",
            "reason_detail": {"home_team_id": "team-a"},
            "decided_at": decided,
        }
        # alias created BEFORE the prior decision → no signal
        aliases = {"team-a": [self._now() - timedelta(days=2)]}
        assert _filter_tier2([row], aliases, {}) == []

    def test_fixture_state_signal_passes(self):
        decided = self._now() - timedelta(days=2)
        row = {
            "provider_record_id": "kalshi:1",
            "reason_detail": {
                "asymmetric_failed_side_candidate_team_ids": ["team-b"],
            },
            "decided_at": decided,
        }
        fixtures = {"team-b": [self._now() - timedelta(hours=2)]}
        result = _filter_tier2([row], {}, fixtures)
        assert len(result) == 1

    def test_either_signal_qualifies(self):
        """Loose: alias-add OR fixture-state, not AND."""
        decided = self._now() - timedelta(days=2)
        row_alias = {
            "provider_record_id": "fl:1",
            "reason_detail": {"home_team_id": "team-a"},
            "decided_at": decided,
        }
        row_fixture = {
            "provider_record_id": "fl:2",
            "reason_detail": {"home_team_id": "team-b"},
            "decided_at": decided,
        }
        aliases = {"team-a": [self._now()]}
        fixtures = {"team-b": [self._now()]}
        result = _filter_tier2([row_alias, row_fixture], aliases, fixtures)
        assert {r["provider_record_id"] for r in result} == {
            "fl:1", "fl:2",
        }

    def test_any_team_id_qualifies(self):
        """Loose: a signal on ANY team_id in reason_detail qualifies,
        even if the prior decision's "primary" team_id had no signal."""
        decided = self._now() - timedelta(days=2)
        row = {
            "provider_record_id": "fl:3",
            "reason_detail": {
                "home_team_id": "team-primary",
                "colliding_home_team_ids": ["team-secondary"],
            },
            "decided_at": decided,
        }
        # Signal only on the secondary team_id.
        aliases = {"team-secondary": [self._now()]}
        result = _filter_tier2([row], aliases, {})
        assert len(result) == 1


# ──────────────────────────────────────────────────────────────────
# F6 halt criteria
# ──────────────────────────────────────────────────────────────────


class TestEvaluateHaltCriteria:
    def test_healthy_pass_no_warnings(self):
        warnings = _evaluate_halt_criteria(
            candidate_set_size=500,
            latency_candidate_select_ms=200,
            trailing_7d_mean_candidate_set=300.0,
        )
        assert warnings == []

    def test_candidate_set_multiplier_warning(self):
        warnings = _evaluate_halt_criteria(
            candidate_set_size=10_000,
            latency_candidate_select_ms=200,
            trailing_7d_mean_candidate_set=500.0,
        )
        assert len(warnings) == 1
        assert "candidate_set_size" in warnings[0]
        assert f"{CANDIDATE_SET_MULTIPLIER_CEILING}×" in warnings[0]

    def test_latency_ceiling_warning(self):
        warnings = _evaluate_halt_criteria(
            candidate_set_size=100,
            latency_candidate_select_ms=10_000,
            trailing_7d_mean_candidate_set=200.0,
        )
        assert len(warnings) == 1
        assert "latency" in warnings[0]
        assert f"{CANDIDATE_SELECT_LATENCY_CEILING_MS}ms" in warnings[0]

    def test_no_trailing_mean_skips_multiplier_check(self):
        """First-pass on a fresh deploy has no trailing 7-day data —
        the multiplier check must short-circuit, NOT warn on every
        first run."""
        warnings = _evaluate_halt_criteria(
            candidate_set_size=100_000,
            latency_candidate_select_ms=200,
            trailing_7d_mean_candidate_set=None,
        )
        assert warnings == []

    def test_zero_trailing_mean_skips_multiplier_check(self):
        """Defensive: trailing mean of 0 (no historic data via
        averaging) also short-circuits — don't divide by zero, don't
        warn on every pass."""
        warnings = _evaluate_halt_criteria(
            candidate_set_size=100_000,
            latency_candidate_select_ms=200,
            trailing_7d_mean_candidate_set=0.0,
        )
        assert warnings == []

    def test_both_thresholds_warned_independently(self):
        warnings = _evaluate_halt_criteria(
            candidate_set_size=10_000,
            latency_candidate_select_ms=10_000,
            trailing_7d_mean_candidate_set=500.0,
        )
        assert len(warnings) == 2

    def test_hard_limit_documented(self):
        """The hard-limit constant is the safety net above the F6
        warn thresholds. It's checked separately in main(); this
        test pins the constant against scope-doc intent (Day-41
        addressable ceiling ~16,588 × 3 = ~50k safety)."""
        assert HARD_LIMIT_CANDIDATE_SET == 50_000


# ──────────────────────────────────────────────────────────────────
# parse_candidate_set (F8 LISTEN/NOTIFY seam)
# ──────────────────────────────────────────────────────────────────


class TestParseCandidateSet:
    def test_single_fl_entry(self):
        result = parse_candidate_set("fl:ABC123")
        assert result == [("fl", "ABC123")]

    def test_multi_entry_mixed_providers(self):
        result = parse_candidate_set("fl:ABC,kalshi:KX-1,fl:DEF")
        assert result == [
            ("fl", "ABC"),
            ("kalshi", "KX-1"),
            ("fl", "DEF"),
        ]

    def test_empty_string_returns_empty_list(self):
        assert parse_candidate_set("") == []

    def test_whitespace_tolerated(self):
        result = parse_candidate_set("  fl : ABC , kalshi : KX-1  ")
        assert result == [("fl", "ABC"), ("kalshi", "KX-1")]

    def test_missing_provider_prefix_raises(self):
        with pytest.raises(ValueError, match="provider"):
            parse_candidate_set("ABC123")

    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="provider"):
            parse_candidate_set("polymarket:ABC")

    def test_empty_record_id_raises(self):
        with pytest.raises(ValueError, match="empty record_id"):
            parse_candidate_set("fl:")


# ──────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────


class TestCLI:
    def test_no_provider_errors(self, capsys):
        with pytest.raises(SystemExit) as exc:
            cli_main(["--dry-run"])
        assert exc.value.code == 2

    def test_dry_run_and_apply_mutually_exclusive(self, capsys):
        with pytest.raises(SystemExit) as exc:
            cli_main(["--provider", "fl", "--dry-run", "--apply"])
        assert exc.value.code == 2

    def test_bad_candidate_set_returns_2(self, capsys):
        rc = cli_main([
            "--provider", "fl",
            "--candidate-set", "bad-shape-no-colon",
        ])
        assert rc == 2
        captured = capsys.readouterr()
        assert "candidate-set" in captured.err.lower()

    def test_no_database_url_returns_1(self, monkeypatch, capsys):
        """Without DATABASE_URL the script exits 1 cleanly before
        any DB call — matches the run_resolver_pass.py pattern."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        rc = cli_main(["--provider", "fl", "--dry-run"])
        assert rc == 1
        captured = capsys.readouterr()
        assert "DATABASE_URL" in captured.err


# ──────────────────────────────────────────────────────────────────
# Integration test stubs (SP_INTEGRATION_DB-gated)
# ──────────────────────────────────────────────────────────────────


INTEGRATION_DB = os.environ.get("SP_INTEGRATION_DB", "").strip()


@pytest.mark.skipif(
    not INTEGRATION_DB,
    reason="SP_INTEGRATION_DB not set — re-resolution integration "
           "tests need real Postgres.",
)
class TestReresolutionIntegration:
    """Real-DB tests; stubbed pending operator dry-run approval —
    same convention as tests/test_resolver_2b.py and
    tests/test_merge_bbl.py."""

    @pytest.mark.skip(reason="Integration test — pending operator dry-run approval")
    def test_alias_add_lifts_a_record_through_one_pass(self):
        """End-to-end: stage a no_match record + add an alias whose
        team_id appears in reason_detail + run --apply. Assert the
        record now has a fresh resolution_log row with reason_code
        IN ('strict', 'alias', 'fuzzy', 'review_queue'). The
        dispositive F7 Part A check."""
        pass

    @pytest.mark.skip(reason="Integration test — pending operator dry-run approval")
    def test_partial_index_used_by_candidate_query(self):
        """EXPLAIN ANALYZE the Tier-1 SQL against a fixture corpus
        and assert the plan uses
        ix_resolution_log_fail_reason_no_match (not Seq Scan)."""
        pass

    @pytest.mark.skip(reason="Integration test — pending operator dry-run approval")
    def test_gin_index_used_by_tier2_containment(self):
        """EXPLAIN ANALYZE the Tier-2 alias-add JSONB containment
        and assert the plan uses
        ix_resolution_log_reason_detail_gin (not Seq Scan)."""
        pass

    @pytest.mark.skip(reason="Integration test — pending operator dry-run approval")
    def test_daily_cron_and_loop_no_lock_contention(self):
        """Run a daily cron pass + a loop pass concurrently and
        assert both complete without lock-wait warnings. F7 Part C
        negative-check shape."""
        pass

    @pytest.mark.skip(reason="Integration test — pending operator dry-run approval")
    def test_run_mode_live_row_written(self):
        """After --apply, assert sp.resolver_runs has a fresh row
        with run_mode='live' and extra->>'candidate_set_size'
        populated."""
        pass

    @pytest.mark.skip(reason="Integration test — pending operator dry-run approval")
    def test_dry_run_writes_nothing(self):
        """Snapshot sp.resolution_log + sp.review_queue +
        sp.resolver_runs counts pre + post a dry-run pass; assert
        zero new rows."""
        pass

    @pytest.mark.skip(reason="Integration test — pending operator dry-run approval")
    def test_candidate_set_override_bypasses_selection(self):
        """Invoke with --candidate-set fl:X,fl:Y and assert ONLY
        those two records were processed (no others), and the
        candidate-selection SQL was not executed."""
        pass


# ──────────────────────────────────────────────────────────────────
# Static invariants — sanity guards against silent regressions
# ──────────────────────────────────────────────────────────────────


class TestStaticInvariants:
    def test_module_docstring_references_scope_doc(self):
        import scripts.run_reresolution_pass as mod
        assert "docs/reresolution/scope-2026-06-17.md" in (
            mod.__doc__ or ""
        )

    def test_module_carries_phase_2e_marker(self):
        import scripts.run_reresolution_pass as mod
        assert "Phase 2E" in (mod.__doc__ or "")

    def test_migration_uses_repo_concurrently_pattern_not_autocommit_block(self):
        """Every CONCURRENTLY migration in the chain MUST use the
        COMMIT + execution_options(AUTOCOMMIT) pattern documented in
        `a2c4f6d8e1b3` (the first CONCURRENTLY migration) and reused
        by every subsequent one.

        Day-42 production confirmed the failure modes:
          1. `op.get_context().autocommit_block()` fails the
             `assert self._transaction is not None` (alembic
             migration.py:329) — async-asyncpg env.py with
             transaction_per_migration + run_sync sync-bridge.
          2. `op.execute("COMMIT")` + `execution_options(
             isolation_level="AUTOCOMMIT")` ALSO fails:
             `InvalidRequestError: transaction already initialized`.

        The repo pattern (established by `a2c4f6d8e1b3`, extended by
        every subsequent CONCURRENTLY migration) is: skip the
        alembic-upgrade path entirely for CONCURRENTLY DDL — go via
        Neon console + `alembic stamp <rev>`. The migration's
        `upgrade()` body still carries the DDL for documentation +
        replay against a fresh database (e.g., disaster recovery)
        and uses the COMMIT + execution_options pattern there
        (rather than autocommit_block) so the replay path is at
        least closer to working.

        This test glob-scans every migration that uses
        `CREATE INDEX CONCURRENTLY` and pins all three properties:
          - No `autocommit_block(` call in the code (docstring
            mentions are allowed — they explain why we avoid it).
          - COMMIT + AUTOCOMMIT isolation pattern present.
          - CONCURRENTLY preserved (no fallback to plain
            CREATE INDEX, which would lock the 130k+ row hot-write
            sp.resolution_log table).
          - Console + stamp runbook documented in the migration
            (a future maintainer needs the runbook on disk; an
            empty docstring would be a regression).

        Guards against a future maintainer "tidying up" any
        CONCURRENTLY migration with the standard alembic recipe and
        silently breaking deploys."""
        import glob
        import re

        repo_root = os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))
        )
        mig_dir = os.path.join(repo_root, "migrations", "versions")
        # Find every migration file. Filter to the ones that contain
        # `CREATE INDEX CONCURRENTLY` — those are the ones that need
        # the repo pattern.
        concurrent_migrations: list[str] = []
        for path in sorted(glob.glob(os.path.join(mig_dir, "*.py"))):
            with open(path, encoding="utf-8") as f:
                blob = f.read()
            if "CREATE INDEX CONCURRENTLY" in blob:
                concurrent_migrations.append(path)

        # The repo pattern was established by a2c4f6d8e1b3. If this
        # list is empty, something deleted the migration chain —
        # fail loudly so the test isn't silently a no-op.
        assert len(concurrent_migrations) >= 1, (
            "Expected at least one CONCURRENTLY migration in the "
            "chain (the pattern was established by "
            "a2c4f6d8e1b3 Day-42). None found — did someone delete "
            "the migration?"
        )

        for path in concurrent_migrations:
            with open(path, encoding="utf-8") as f:
                blob = f.read()
            # Strip the module docstring (first triple-quoted
            # string). The docstring explains why we avoid
            # autocommit_block — those mentions are legitimate.
            # We're guarding against actual code calls.
            code_only = re.sub(
                r'^"""[\s\S]*?"""', "", blob, count=1,
            )
            base = os.path.basename(path)
            assert "autocommit_block(" not in code_only, (
                f"{base}: autocommit_block() fails in this repo's "
                "env.py config — see a2c4f6d8e1b3 docstring for the "
                "full explanation. Use COMMIT + "
                "execution_options(isolation_level='AUTOCOMMIT') in "
                "the upgrade() body, and document the console + "
                "stamp landing path in the docstring."
            )
            assert 'isolation_level="AUTOCOMMIT"' in blob, (
                f"{base}: must switch to AUTOCOMMIT isolation so "
                "the next statement does not implicit-BEGIN a new "
                "transaction. Mirror a2c4f6d8e1b3's "
                "_switch_to_autocommit() helper."
            )
            assert "CREATE INDEX CONCURRENTLY" in blob, (
                f"{base}: claimed to be a CONCURRENTLY migration "
                "but doesn't actually carry CREATE INDEX "
                "CONCURRENTLY. sp.resolution_log is 130k+ rows and "
                "a production hot-write table — plain CREATE INDEX "
                "would lock it."
            )
            # The console + stamp runbook must be documented in the
            # docstring so a future operator (or a fresh-session
            # Claude) can find it without rediscovering it.
            assert "alembic stamp" in blob, (
                f"{base}: missing the `alembic stamp` runbook in "
                "its docstring. The Day-42 lesson is that the "
                "production landing path is console + stamp, NOT "
                "alembic upgrade. Future operators need that "
                "runbook on disk."
            )

    def test_railway_toml_crons_are_commented_off(self):
        """Belt-and-suspenders: the three Phase 2E cron service
        entries MUST be commented in railway.toml — they go live
        only after operator dry-run review removes the leading
        '# '."""
        repo_root = os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))
        )
        toml_path = os.path.join(repo_root, "railway.toml")
        with open(toml_path, encoding="utf-8") as f:
            text_blob = f.read()
        # Each service entry header is `name = "<service>"`. The
        # Phase 2E services must all be commented (line starts with
        # `# name`).
        for svc in (
            "resolver-reresolution-fl",
            "resolver-reresolution-kalshi",
            "daily-diff",
        ):
            assert f'# name = "{svc}"' in text_blob, (
                f"Phase 2E service {svc!r} must be COMMENTED in "
                "railway.toml — flagged off pending operator dry-run "
                "review"
            )
            # And the corresponding [[services]] block header should
            # also be commented (or absent).
            assert f'[[services]]\nname = "{svc}"' not in text_blob, (
                f"Phase 2E service {svc!r} has UNCOMMENTED "
                "[[services]] header — must be commented until "
                "operator un-flags"
            )
