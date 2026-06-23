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
    RERESOLUTION_LASTSEEN_WINDOW_DAYS,
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
    no DB connection required.

    Day-43 walked through three perf iterations to land on the
    current shape (MATERIALIZED CTE + LATERAL):

      Attempt 1: CTE + DISTINCT ON + JOIN. 6.3s warm. Planner chose
        Parallel Seq Scan + Sort on the whole resolution_log because
        reason_detail JSONB had to be pulled per-row.
      Attempt 2: FROM provider_table JOIN LATERAL ... WHERE
        fixture_id IS NULL. 2.7s warm. Outer applied fixture_id
        IS NULL as a Seq Scan filter on fl_events, so the LATERAL
        ran per-row across ALL fl_events.
      Attempt 3 (current): WITH unresolved AS MATERIALIZED
        (SELECT ... WHERE fixture_id IS NULL). MATERIALIZED is
        non-negotiable: PG 12+ inlines single-reference CTEs by
        default, which would put us back at attempt 2's seq-scan
        choice. MATERIALIZED forces the planner to compute the CTE
        separately, exercising the pre-existing partial index
        ix_*_unresolved before any LATERAL work.

    These tests pin all three properties of the current shape so a
    future maintainer doesn't silently regress to attempt 1 or 2.
    """

    def test_fl_sql_carries_allowlist_filter(self):
        assert "fail_reason" in TIER1_SQL_FL
        assert ":allowlist" in TIER1_SQL_FL

    def test_fl_sql_carries_asymmetric_excluded_filter(self):
        assert "asymmetric_excluded" in TIER1_SQL_FL
        assert "IS NULL" in TIER1_SQL_FL

    def test_fl_sql_filters_fixture_id_null_inside_materialized_cte(self):
        """Day-43 attempt-3 fix: fixture_id IS NULL MUST appear in
        the MATERIALIZED CTE block, not as an outer WHERE clause —
        otherwise the planner applies it as a Seq Scan filter and
        ix_fl_events_unresolved is ignored.

        Catches the attempt-2 regression: a maintainer "simplifying"
        by moving the predicate into the outer WHERE would silently
        cost ~1.5s per pass."""
        # Find the MATERIALIZED CTE's body and assert fixture_id IS
        # NULL appears inside it.
        import re
        cte_match = re.search(
            r"WITH\s+\w+\s+AS\s+MATERIALIZED\s*\((.*?)\)",
            TIER1_SQL_FL, flags=re.DOTALL | re.IGNORECASE,
        )
        assert cte_match is not None, (
            "TIER1_SQL_FL must use a MATERIALIZED CTE for the "
            "unresolved-driver set — see attempt-3 rationale."
        )
        cte_body = cte_match.group(1)
        assert "fixture_id IS NULL" in cte_body, (
            "fixture_id IS NULL must be filtered INSIDE the "
            "MATERIALIZED CTE so the partial index "
            "ix_fl_events_unresolved drives the outer scan. "
            "Moving it to the outer WHERE silently regresses to "
            "Day-43 attempt 2's Seq Scan."
        )

    def test_fl_sql_uses_materialized_cte_for_unresolved_driver(self):
        """MATERIALIZED is non-negotiable — without it, PG 12+
        inlines the CTE and we lose the partial-index drive.
        Attempt-3 regression guard."""
        assert "AS MATERIALIZED" in TIER1_SQL_FL.upper().replace(
            "AS  MATERIALIZED", "AS MATERIALIZED"
        ) or "AS MATERIALIZED" in TIER1_SQL_FL, (
            "TIER1_SQL_FL must use `WITH ... AS MATERIALIZED (...)`"
            " — without MATERIALIZED, PG 12+ inlines single-"
            "reference CTEs and the partial-index drive is lost."
        )

    def test_fl_sql_uses_lateral_for_latest_decision(self):
        """The LATERAL's ORDER BY decided_at DESC LIMIT 1 lets
        ix_resolution_log_provider_record_decided_at serve the
        latest-decision lookup directly — no full scan, no sort."""
        assert "JOIN LATERAL" in TIER1_SQL_FL
        assert "ORDER BY" in TIER1_SQL_FL and "DESC" in TIER1_SQL_FL
        assert "LIMIT 1" in TIER1_SQL_FL

    def test_fl_sql_does_not_use_distinct_on(self):
        """Attempt-1 regression guard."""
        assert "DISTINCT ON" not in TIER1_SQL_FL

    def test_fl_sql_restricts_to_no_match(self):
        assert "reason_code = 'no_match'" in TIER1_SQL_FL

    def test_kalshi_sql_carries_same_filters(self):
        """Same shape on the Kalshi side."""
        for needle in (
            "fail_reason", ":allowlist", "asymmetric_excluded",
            "fixture_id IS NULL", "JOIN LATERAL",
            "reason_code = 'no_match'", "LIMIT 1",
            "AS MATERIALIZED",
        ):
            assert needle in TIER1_SQL_KALSHI, (
                f"{needle!r} missing from TIER1_SQL_KALSHI"
            )

    def test_kalshi_sql_does_not_use_distinct_on(self):
        assert "DISTINCT ON" not in TIER1_SQL_KALSHI

    def test_kalshi_sql_filters_fixture_id_null_inside_materialized_cte(self):
        """Same attempt-2 regression guard as FL: fixture_id IS NULL
        MUST live inside the MATERIALIZED CTE so the partial index
        ix_kalshi_markets_unresolved drives the outer scan."""
        import re
        cte_match = re.search(
            r"WITH\s+\w+\s+AS\s+MATERIALIZED\s*\((.*?)\)",
            TIER1_SQL_KALSHI, flags=re.DOTALL | re.IGNORECASE,
        )
        assert cte_match is not None
        cte_body = cte_match.group(1)
        assert "fixture_id IS NULL" in cte_body

    def test_fl_sql_drives_from_sp_fl_events(self):
        """Outer driver = MATERIALIZED unresolved set of fl_events.
        The partial index ix_fl_events_unresolved on (fixture_id)
        WHERE fixture_id IS NULL serves the CTE's scan."""
        assert "FROM sp.fl_events" in TIER1_SQL_FL

    def test_kalshi_sql_drives_from_sp_kalshi_markets(self):
        """Outer driver = MATERIALIZED unresolved set of kalshi_markets."""
        assert "FROM sp.kalshi_markets" in TIER1_SQL_KALSHI

    def test_fl_lateral_keys_on_fl_event_id(self):
        """sp.fl_events PK is `fl_event_id` (TEXT), not `ticker`. The
        LATERAL must key on the CTE's projected `fl_event_id` column.
        Catches a copy-paste regression from the Kalshi shape."""
        assert "rl.provider_record_id = u.fl_event_id" in TIER1_SQL_FL

    def test_kalshi_lateral_keys_on_ticker(self):
        """sp.kalshi_markets PK is `ticker` (TEXT), not `fl_event_id`.
        The LATERAL must key on the CTE's projected `ticker` column.
        Catches a copy-paste regression from the FL shape."""
        assert "rl.provider_record_id = u.ticker" in TIER1_SQL_KALSHI

    def test_fl_watermark_predicate_inside_materialized_cte(self):
        """Attempt-4 guard (Day-44). The last_seen_at watermark MUST
        live inside the MATERIALIZED CTE body, BEFORE the LATERAL —
        putting it in the outer WHERE silently regresses to
        attempt-3's O(N_unresolved) cost on the inner LATERAL
        (33,882 → 13.6s warm).

        The composite partial index
        ix_fl_events_unresolved_last_seen ON (last_seen_at) WHERE
        fixture_id IS NULL (migration c5e7f9a3b1d4) serves the CTE
        scan only if the watermark is in the CTE body.

        Position check (rather than CTE-body regex) because the CTE
        body contains nested parens (NOW()) that a non-greedy regex
        can't span. The LATERAL is the unambiguous boundary between
        CTE body and outer query."""
        lateral_idx = TIER1_SQL_FL.find("JOIN LATERAL")
        assert lateral_idx > 0, "JOIN LATERAL must be present"

        cte_body = TIER1_SQL_FL[:lateral_idx]
        assert "last_seen_at" in cte_body, (
            "Day-44 attempt-4 regression guard: the last_seen_at "
            "watermark predicate MUST live inside the MATERIALIZED "
            "CTE body (i.e. before the LATERAL). Hoisting it to "
            "the outer WHERE silently regresses to attempt-3's "
            "O(N_unresolved) cost."
        )
        assert "INTERVAL" in cte_body, (
            "Watermark must use INTERVAL (e.g. NOW() - INTERVAL "
            "'3 days'). Catches a maintainer dropping the "
            "time-bound by accident."
        )

    def test_kalshi_watermark_predicate_inside_materialized_cte(self):
        """Same attempt-4 guard for the Kalshi side. Kalshi is the
        binding-constraint provider (48,277 unresolved → 7,487 at
        3d) so this regression matters more here than FL."""
        lateral_idx = TIER1_SQL_KALSHI.find("JOIN LATERAL")
        assert lateral_idx > 0
        cte_body = TIER1_SQL_KALSHI[:lateral_idx]
        assert "last_seen_at" in cte_body
        assert "INTERVAL" in cte_body


class TestWindowConstant:
    """Day-44 attempt 4 — the watermark window is a named module-
    level constant, NOT a magic literal scattered through SQL strings.
    Tunable via single source of truth."""

    def test_constant_exists_with_documented_value(self):
        """RERESOLUTION_LASTSEEN_WINDOW_DAYS = 3 was the Day-44
        operator decision against production counts (Kalshi 7,487
        at 3d → ~3s warm vs 5s F6 ceiling; 2d and 3d identical on
        Kalshi so 3d costs nothing in latency over 2d but gives
        +24h correctness margin)."""
        assert isinstance(RERESOLUTION_LASTSEEN_WINDOW_DAYS, int)
        assert RERESOLUTION_LASTSEEN_WINDOW_DAYS == 3, (
            "Day-44 production-evidence-decided value is 3 days. "
            "Change requires a sized re-measurement per the "
            "attempt-4 perf-arc methodology — don't tweak without "
            "EXPLAIN ANALYZE evidence."
        )

    def test_constant_baked_into_fl_sql(self):
        """The constant is interpolated into TIER1_SQL_FL at module
        load via f-string. Verify the literal flows through, so a
        future maintainer can't silently drift the constant from the
        SQL by editing one but not the other."""
        expected = (
            f"INTERVAL '{RERESOLUTION_LASTSEEN_WINDOW_DAYS} days'"
        )
        assert expected in TIER1_SQL_FL, (
            f"Expected {expected!r} in TIER1_SQL_FL — the constant "
            "RERESOLUTION_LASTSEEN_WINDOW_DAYS must be the single "
            "source of truth, baked into the SQL via f-string."
        )

    def test_constant_baked_into_kalshi_sql(self):
        expected = (
            f"INTERVAL '{RERESOLUTION_LASTSEEN_WINDOW_DAYS} days'"
        )
        assert expected in TIER1_SQL_KALSHI

    def test_constant_is_positive_integer(self):
        """Defensive: a non-positive value would either drop all
        records (≤0) or be parsed differently by Postgres."""
        assert RERESOLUTION_LASTSEEN_WINDOW_DAYS > 0


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
        """Every CONCURRENTLY migration in the chain MUST follow the
        Day-44 cleanup pattern: console + stamp is the canonical
        landing path; the upgrade()/downgrade() code bodies are
        REPLAY-SAFE NO-OPS; the DDL lives in the module docstring as
        the operator runbook.

        Two failure modes confirmed in production:
          1. Day-42 — op.get_context().autocommit_block() fails the
             `self._transaction is not None` assertion
             (alembic/runtime/migration.py:329) — async-asyncpg
             env.py with transaction_per_migration + run_sync
             sync-bridge.
          2. Day-44 — op.execute("COMMIT") + execution_options(
             isolation_level="AUTOCOMMIT") ALSO fails:
             InvalidRequestError: transaction already initialized.

        With BOTH alembic CONCURRENTLY escape hatches broken, the
        only safe upgrade()/downgrade() body is a NO-OP. A fresh-DB
        `alembic upgrade head` then cleanly records the revision in
        sp.alembic_version without attempting the failing DDL.
        Production indexes are built via the docstring runbook
        (psql + `alembic stamp <rev>`).

        This test glob-scans every migration whose blob contains
        `CREATE INDEX CONCURRENTLY` (typically in the module
        docstring) and enforces four properties:

          A. autocommit_block( is NOT called in code
             (Day-42 reintroduction guard).
          B. CREATE INDEX CONCURRENTLY is NOT in the code body
             (Day-44 cleanup guard — DDL lives only in the
             docstring as the operator runbook; any code-side
             execution would fail in this env.py).
          C. CREATE INDEX CONCURRENTLY IS present somewhere
             (the runbook itself must not have been deleted).
          D. `alembic stamp` runbook is documented somewhere
             (future operator / fresh-session Claude need it
             on disk).

        Guards against a future maintainer:
          - "Tidying up" by reintroducing autocommit_block() (A).
          - "Finishing the migration" by moving the CONCURRENTLY
            DDL into the upgrade() body (B).
          - Dropping CONCURRENTLY semantics by replacing with plain
            CREATE INDEX (would lock the 130k+ row hot-write
            sp.resolution_log) (C).
          - Deleting the docstring runbook (D).
        """
        import glob
        import re

        repo_root = os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))
        )
        mig_dir = os.path.join(repo_root, "migrations", "versions")
        concurrent_migrations: list[str] = []
        for path in sorted(glob.glob(os.path.join(mig_dir, "*.py"))):
            with open(path, encoding="utf-8") as f:
                blob = f.read()
            if "CREATE INDEX CONCURRENTLY" in blob:
                concurrent_migrations.append(path)

        assert len(concurrent_migrations) >= 1, (
            "Expected at least one CONCURRENTLY migration in the "
            "chain (the pattern was established by "
            "a2c4f6d8e1b3 Day-42). None found — did someone delete "
            "the migration?"
        )

        for path in concurrent_migrations:
            with open(path, encoding="utf-8") as f:
                blob = f.read()
            # Strip ALL triple-quoted strings (module docstring +
            # every function/class docstring). Docstrings are where
            # the operator runbook + design rationale live — their
            # mentions of autocommit_block / CREATE INDEX CONCURRENTLY
            # are documentation, not execution. The non-docstring
            # remainder is what we guard against.
            code_only = re.sub(
                r'"""[\s\S]*?"""', "", blob,
            )
            base = os.path.basename(path)

            # A. Day-42 guard.
            assert "autocommit_block(" not in code_only, (
                f"{base}: autocommit_block() fails in this repo's "
                "env.py config (Day-42 lesson). See a2c4f6d8e1b3 "
                "docstring for the full explanation. The Day-44 "
                "cleanup pattern keeps upgrade()/downgrade() as "
                "NO-OPS and runs the runbook from the docstring."
            )

            # B. Day-44 cleanup guard — CONCURRENTLY DDL must live
            # ONLY in the module docstring, not in the code body.
            assert "CREATE INDEX CONCURRENTLY" not in code_only, (
                f"{base}: CREATE INDEX CONCURRENTLY appears in the "
                "code body, but the Day-44 cleanup requires it to "
                "live ONLY in the module docstring (as the operator "
                "runbook). BOTH alembic CONCURRENTLY escape "
                "hatches fail in this repo's env.py — any "
                "code-side execution would crash a future "
                "`alembic upgrade head`. Move the DDL to the "
                "docstring runbook and make upgrade()/downgrade() "
                "REPLAY-SAFE NO-OPS. See a2c4f6d8e1b3 for the "
                "established shape."
            )

            # C. CONCURRENTLY semantics preserved (in the runbook).
            assert "CREATE INDEX CONCURRENTLY" in blob, (
                f"{base}: claimed to be a CONCURRENTLY migration "
                "but doesn't actually carry CREATE INDEX "
                "CONCURRENTLY anywhere. sp.resolution_log is 130k+ "
                "rows and a production hot-write table — plain "
                "CREATE INDEX would lock it. Restore the runbook "
                "with CONCURRENTLY semantics in the module "
                "docstring."
            )

            # D. Runbook documented.
            assert "alembic stamp" in blob, (
                f"{base}: missing the `alembic stamp` runbook in "
                "its docstring. The production landing path is "
                "console + stamp, NOT alembic upgrade. Future "
                "operators need that runbook on disk."
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
