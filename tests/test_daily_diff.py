"""Phase 2 Track A Deliverable 2 — daily-diff measurement infrastructure tests.

SCAFFOLD COMMIT — test stubs for the scope. Implementation lands in
subsequent commits on this branch.

Test categories per PR #175's scope doc:

  - Manifest / migration shape tests (always run; no DB required)
  - Pattern D pre-flight unit tests (always run; mocks the SQL call)
  - Classification logic tests (always run; pure-function inputs)
  - Histogram generation tests (always run)
  - Integration tests (SP_INTEGRATION_DB-gated; real Postgres)
    - Idempotency on re-run (unique constraint on report_date)
    - Empty-window handling (exit code 5)
    - Migration up/down roundtrip
    - sp.daily_diff_reports + sp.baseline_shifts write-through
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
INTEGRATION_DB = os.environ.get("SP_INTEGRATION_DB", "").strip()


# ══════════════════════════════════════════════════════════════
# Pattern D pre-flight unit tests
# ══════════════════════════════════════════════════════════════


class TestPatternDPreFlight:
    """Verify-endpoint-before-read sub-pattern per PR #167 commit aa95a36.

    Three test cases:
      - Endpoint matches expected → returns 0
      - Endpoint mismatch + DAILY_DIFF_ALLOW_NON_PRODUCTION unset → returns 3
      - Endpoint mismatch + DAILY_DIFF_ALLOW_NON_PRODUCTION=1 → returns 0
    """

    @pytest.mark.skip(reason="SCAFFOLD — implementation pending")
    def test_endpoint_match_passes(self):
        """When inet_server_addr() matches EXPECTED_PRODUCTION_ENDPOINT,
        pre-flight returns 0."""
        # from scripts.daily_diff import _pattern_d_pre_flight
        # ...
        pass

    @pytest.mark.skip(reason="SCAFFOLD — implementation pending")
    def test_endpoint_mismatch_fails_without_override(self):
        """When inet_server_addr() doesn't match expected AND
        DAILY_DIFF_ALLOW_NON_PRODUCTION is unset, pre-flight returns 3."""
        pass

    @pytest.mark.skip(reason="SCAFFOLD — implementation pending")
    def test_endpoint_mismatch_passes_with_override(self):
        """When DAILY_DIFF_ALLOW_NON_PRODUCTION=1, pre-flight returns 0
        regardless of endpoint mismatch."""
        pass

    @pytest.mark.skip(reason="SCAFFOLD — implementation pending")
    def test_expected_production_endpoint_unset_fails(self):
        """When EXPECTED_PRODUCTION_ENDPOINT is unset AND
        DAILY_DIFF_ALLOW_NON_PRODUCTION is unset, pre-flight returns 3.
        Forces operator to either set the expected endpoint OR
        explicitly opt out via the local-dev flag."""
        pass


# ══════════════════════════════════════════════════════════════
# Scope-filter classification logic
# ══════════════════════════════════════════════════════════════


class TestScopeFilterClassification:
    """Per PR #175 §7, scope-filter rules determine which records
    contribute to scope-filtered metrics vs are filtered out as
    structurally out-of-scope.

    Filter dimensions:
      - NON_SPORT records (empty _sport field per Issue #174) → filtered
      - Kalshi prop-market records per KALSHI_PROP_MARKET_SEGMENTS
        vocabulary (Issue #160) → filtered
      - Tennis prop markets (SET_WINNER, EXACT_SCORE, etc.) → filtered
      - All others → counted in scope-filtered metrics
    """

    def test_non_sport_record_filtered_out(self):
        """Record with empty _sport field → filter classification
        returns 'non_sport_filtered_out'."""
        from scripts.daily_diff import (
            classify_record, ScopeClassification,
        )
        # Empty _sport: oil prices, crypto, politics, weather records
        # (Issue #174's NON_SPORT population, ~56% of unresolved Kalshi).
        record = {
            "raw_payload": {
                "_sport": "",
                "title": "Brent crude oil > $80 by end of Q2",
            },
        }
        assert classify_record("kalshi", record) == ScopeClassification.NON_SPORT
        assert ScopeClassification.NON_SPORT == "non_sport_filtered_out"

        # Missing _sport entirely (defensive — older records may not
        # have the field at all).
        record_missing = {"raw_payload": {"title": "..."}}
        assert classify_record("kalshi", record_missing) == ScopeClassification.NON_SPORT

        # Whitespace-only _sport — still non-sport.
        record_whitespace = {"raw_payload": {"_sport": "   ", "title": "..."}}
        assert classify_record("kalshi", record_whitespace) == ScopeClassification.NON_SPORT

    def test_kalshi_prop_market_filtered_out(self):
        """Record with prop-market vocabulary match → filter
        classification returns 'prop_market_filtered_out'."""
        from scripts.daily_diff import (
            classify_record, ScopeClassification,
        )
        # "Colorado Rockies vs Arizona Diamondbacks: Hits" — suffix
        # "Hits" is in KALSHI_PROP_MARKET_SEGMENTS.
        record = {
            "raw_payload": {
                "_sport": "Baseball",
                "title": "Colorado Rockies vs Arizona Diamondbacks: Hits",
            },
        }
        assert classify_record("kalshi", record) == ScopeClassification.PROP_MARKET

        # Other prop-market shapes from the vocabulary.
        for suffix in [
            "First Inning Run", "Strikeouts", "Total Runs",
            "Method of Victory", "Triple Doubles", "BTTS",
            "Overtime", "First Goal", "Total Maps", "4th TD",
        ]:
            record = {
                "raw_payload": {
                    "_sport": "Baseball",
                    "title": f"Team A vs Team B: {suffix}",
                },
            }
            assert classify_record("kalshi", record) == ScopeClassification.PROP_MARKET, (
                f"Vocabulary entry {suffix!r} should classify as prop_market"
            )

    def test_head_to_head_record_counted(self):
        """Standard head-to-head record → counted in scope-filtered
        metrics."""
        from scripts.daily_diff import (
            classify_record, ScopeClassification,
        )
        # Standard team-vs-team title, no prop suffix.
        record = {
            "raw_payload": {
                "_sport": "Soccer",
                "title": "Manchester United vs Chelsea",
            },
        }
        assert classify_record("kalshi", record) == ScopeClassification.HEAD_TO_HEAD

        # NHL playoff-series shape — colon present but "Game 3" not
        # in vocabulary, so passes scope filter as head-to-head.
        # (Issue #160 precision-precedent: "Game N: TeamName" must
        # NOT get filtered.)
        record_playoff = {
            "raw_payload": {
                "_sport": "Hockey",
                "title": "Anaheim Ducks vs Game 3: Vegas",
            },
        }
        assert classify_record("kalshi", record_playoff) == ScopeClassification.HEAD_TO_HEAD

        # FL records — all classified as head-to-head per
        # classify_fl_record's design.
        fl_record = {"raw_payload": {"some_fl_field": "value"}}
        assert classify_record("fl", fl_record) == ScopeClassification.HEAD_TO_HEAD

    def test_signal_extraction_skipped_counted_separately(self):
        """Record where ingestion failed to extract a FixtureSignal
        → counted in raw.signal_extraction_skipped, NOT in
        scope_filtered denominator.

        SIGNAL_EXTRACTION_SKIPPED is layered on AFTER the pre-parser
        classification (during the parser-run phase). The constant
        is exposed for downstream aggregation but classify_record()
        does NOT return it — that's by design per the scope-filter-
        is-pure-pre-parser-function semantic.

        This test pins the constant value + the contract that
        classify_record() returns one of HEAD_TO_HEAD / NON_SPORT /
        PROP_MARKET (never SIGNAL_EXTRACTION_SKIPPED).
        """
        from scripts.daily_diff import (
            classify_record, ScopeClassification,
        )
        assert ScopeClassification.SIGNAL_EXTRACTION_SKIPPED == "signal_extraction_skipped"

        # classify_record() never returns SIGNAL_EXTRACTION_SKIPPED.
        # Sample a representative set of records that pass / fail
        # scope filter; none should yield SIGNAL_EXTRACTION_SKIPPED.
        for record in [
            {"raw_payload": {"_sport": "", "title": "..."}},  # NON_SPORT
            {"raw_payload": {"_sport": "Baseball", "title": "T1 vs T2: Hits"}},  # PROP_MARKET
            {"raw_payload": {"_sport": "Soccer", "title": "T1 vs T2"}},  # HEAD_TO_HEAD
        ]:
            result = classify_record("kalshi", record)
            assert result != ScopeClassification.SIGNAL_EXTRACTION_SKIPPED, (
                f"classify_record() must not return SIGNAL_EXTRACTION_SKIPPED; "
                f"that label is for the parser-run-phase aggregation layer only. "
                f"Got {result!r} for record {record!r}."
            )

    def test_unknown_provider_raises(self):
        """Defensive: passing an unknown provider raises ValueError.
        Catches typos / future provider additions that haven't been
        wired into classify_record's dispatch."""
        from scripts.daily_diff import classify_record
        with pytest.raises(ValueError, match="Unknown provider"):
            classify_record("polymarket", {"raw_payload": {}})
        with pytest.raises(ValueError, match="Unknown provider"):
            classify_record("oddsapi", {"raw_payload": {}})


# ══════════════════════════════════════════════════════════════
# Per-sport / per-tier metric breakdown
# ══════════════════════════════════════════════════════════════


class TestPerSportMetrics:
    """Per PR #175 §7 measurement targets — per-sport breakdowns."""

    @pytest.mark.skip(reason="SCAFFOLD — implementation pending")
    def test_per_sport_auto_apply_rate_calculated(self):
        """Auto-apply count / scope-filtered denominator, partitioned
        by reason_detail->>'sport'."""
        pass

    @pytest.mark.skip(reason="SCAFFOLD — implementation pending")
    def test_per_tier_resolution_rate_calculated(self):
        """strict / alias / fuzzy / no_match / review_queue / crash
        breakdown per sport."""
        pass

    @pytest.mark.skip(reason="SCAFFOLD — implementation pending")
    def test_personal_path_vs_team_path_distinction(self):
        """Aggregated by INDIVIDUAL_SPORT_CODES membership.
        Personal-path = Tennis + MMA + Boxing.
        Team-path = everything else."""
        pass

    @pytest.mark.skip(reason="SCAFFOLD — implementation pending")
    def test_queue_depth_per_sport(self):
        """Pending review_queue rows, grouped by sport."""
        pass

    @pytest.mark.skip(reason="SCAFFOLD — implementation pending")
    def test_time_in_queue_per_sport(self):
        """Median + p95 of (NOW() - created_at) for pending records,
        per sport."""
        pass

    @pytest.mark.skip(reason="SCAFFOLD — implementation pending")
    def test_abandonment_rate_per_sport(self):
        """Per-sport fraction of pending records aging >N days
        without operator action (default N=14)."""
        pass


# ══════════════════════════════════════════════════════════════
# resolution_log volume tracking (post-Finding X)
# ══════════════════════════════════════════════════════════════


class TestResolutionLogVolume:
    """Per Finding X (2026-05-20): cron re-processes pending records
    daily across all 3 tiers, producing ~7.3M rows/year retry traffic
    at current scale. Track A measures the rate to inform §6.5
    archival sizing (Issue #164)."""

    @pytest.mark.skip(reason="SCAFFOLD — implementation pending")
    def test_resolution_log_volume_partitioned_by_reason_code(self):
        """Per-cron-run row counts in sp.resolution_log, partitioned
        by reason_code, captured in metrics.resolution_log_volume_per_cron."""
        pass


# ══════════════════════════════════════════════════════════════
# Histogram generation (for sample disagreements)
# ══════════════════════════════════════════════════════════════


class TestHistogramGeneration:
    """Confidence-score distribution histogram, stored in report_json.
    Used for threshold-calibration analysis."""

    @pytest.mark.skip(reason="SCAFFOLD — implementation pending")
    def test_histogram_buckets_match_threshold_boundaries(self):
        """Histogram buckets align with auto_apply / review_queue /
        no_match thresholds (0.85, 0.70) so the boundary records
        are visible."""
        pass


# ══════════════════════════════════════════════════════════════
# Integration tests (SP_INTEGRATION_DB-gated)
# ══════════════════════════════════════════════════════════════


@pytest.mark.skipif(
    not INTEGRATION_DB,
    reason="SP_INTEGRATION_DB not set — daily-diff integration tests need real Postgres.",
)
class TestDailyDiffIntegration:
    """Real-DB tests against a Postgres with the Phase 2 Track A
    migration applied."""

    @pytest.mark.skip(reason="SCAFFOLD — implementation pending")
    def test_migration_creates_tables(self):
        """After alembic upgrade head, sp.daily_diff_reports and
        sp.baseline_shifts exist with the documented schemas."""
        pass

    @pytest.mark.skip(reason="SCAFFOLD — implementation pending")
    def test_migration_roundtrip(self):
        """upgrade head → downgrade -1 → upgrade head succeeds
        cleanly. Schema returns to current state. (Per-migration
        guard against destructive downgrade behavior.)"""
        pass

    @pytest.mark.skip(reason="SCAFFOLD — implementation pending")
    def test_idempotency_on_same_day_rerun(self):
        """Running daily_diff.py twice on the same date fails the
        second invocation with exit code 4 (unique constraint on
        report_date). No duplicate rows in sp.daily_diff_reports."""
        pass

    @pytest.mark.skip(reason="SCAFFOLD — implementation pending")
    def test_empty_window_exits_cleanly(self):
        """When the 24h window contains zero records (no ingestion
        during the period), script exits with code 5. No row written
        to sp.daily_diff_reports."""
        pass

    @pytest.mark.skip(reason="SCAFFOLD — implementation pending")
    def test_baseline_shift_annotation_read_through(self):
        """A row in sp.baseline_shifts is visible to the render
        script's correlation logic. Baseline-shift events on the
        same date as a report row are surfaced in the render output."""
        pass

    @pytest.mark.skip(reason="SCAFFOLD — implementation pending")
    def test_scope_filter_version_stamped(self):
        """Every written row has scope_filter_version set to the
        SCOPE_FILTER_VERSION constant from daily_diff.py.
        Version-stamping enables historical re-interpretation when
        the filter rules change (NON_SPORT, prop vocabulary, etc.)."""
        pass


# ══════════════════════════════════════════════════════════════
# Render script tests
# ══════════════════════════════════════════════════════════════


class TestRenderScript:
    """scripts/render_daily_diff_report.py output format tests."""

    @pytest.mark.skip(reason="SCAFFOLD — implementation pending")
    def test_render_outputs_markdown(self):
        """Render produces markdown with the documented section structure."""
        pass

    @pytest.mark.skip(reason="SCAFFOLD — implementation pending")
    def test_render_window_default_7_days(self):
        """Default window is 7 days. Render queries
        sp.daily_diff_reports.report_date >= NOW() - INTERVAL '7 days'."""
        pass

    @pytest.mark.skip(reason="SCAFFOLD — implementation pending")
    def test_render_includes_baseline_shifts_in_window(self):
        """sp.baseline_shifts rows with event_date in the window
        appear in the rendered output's 'Baseline-shift events'
        section."""
        pass

    @pytest.mark.skip(reason="SCAFFOLD — implementation pending")
    def test_render_distinguishes_d2_only_vs_d2_plus_d1(self):
        """Reports with legacy_comparison_present=false (D2-only)
        omit the 'Sample disagreements' section. Reports with
        legacy_comparison_present=true (D2+D1) include it."""
        pass
