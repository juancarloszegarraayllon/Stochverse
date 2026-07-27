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
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
INTEGRATION_DB = os.environ.get("SP_INTEGRATION_DB", "").strip()


# ══════════════════════════════════════════════════════════════
# Pattern D pre-flight unit tests
# ══════════════════════════════════════════════════════════════


class TestPatternDPreFlight:
    """Verify-endpoint-before-read sub-pattern per PR #167 commit aa95a36.

    Refined 2026-05-21: inet_server_addr() returns Neon's link-local
    proxy (169.254.254.254) — useless as a branch discriminator.
    Replaced with current_database() + DATABASE_URL hostname substring
    match against EXPECTED_PRODUCTION_DB_HOST.

    Test surface is the pure-function _check_pattern_d_endpoint() —
    no DB roundtrip needed.
    """

    PROD_URL = (
        "postgresql://u:p@ep-fragrant-frog-ak3esp11.us-east-2.aws.neon.tech"
        ":5432/neondb"
    )
    DEV_URL = (
        "postgresql://u:p@ep-dev-branch-xyz123.us-east-2.aws.neon.tech"
        ":5432/neondb"
    )

    def test_endpoint_match_passes(self):
        """current_database() = expected AND URL hostname contains
        the expected branch endpoint substring → returns 0."""
        from scripts.daily_diff import _check_pattern_d_endpoint
        rc, msg = _check_pattern_d_endpoint(
            self.PROD_URL,
            "neondb",
            expected_db_name="neondb",
            expected_db_host="ep-fragrant-frog-ak3esp11",
            allow_non_production=False,
        )
        assert rc == 0, f"Expected pass; got {rc} ({msg})"

    def test_endpoint_mismatch_fails_without_override(self):
        """URL hostname does NOT contain the expected branch endpoint
        substring AND allow_non_production=False → returns 3.

        Catches accidental runs against a dev branch of the same Neon
        project."""
        from scripts.daily_diff import _check_pattern_d_endpoint
        rc, msg = _check_pattern_d_endpoint(
            self.DEV_URL,
            "neondb",
            expected_db_name="neondb",
            expected_db_host="ep-fragrant-frog-ak3esp11",
            allow_non_production=False,
        )
        assert rc == 3
        assert "ep-fragrant-frog-ak3esp11" in msg

        # Also fails if current_database() mismatches even when host
        # matches (e.g., dev DB on the production branch).
        rc2, msg2 = _check_pattern_d_endpoint(
            self.PROD_URL,
            "scratch_db",
            expected_db_name="neondb",
            expected_db_host="ep-fragrant-frog-ak3esp11",
            allow_non_production=False,
        )
        assert rc2 == 3
        assert "scratch_db" in msg2

    def test_endpoint_mismatch_passes_with_override(self):
        """DAILY_DIFF_ALLOW_NON_PRODUCTION truthy short-circuits to
        success regardless of endpoint mismatch — for local-dev."""
        from scripts.daily_diff import _check_pattern_d_endpoint
        rc, msg = _check_pattern_d_endpoint(
            self.DEV_URL,
            "totally_wrong_db",
            expected_db_name="neondb",
            expected_db_host="ep-fragrant-frog-ak3esp11",
            allow_non_production=True,
        )
        assert rc == 0

    def test_expected_production_endpoint_unset_fails(self):
        """EXPECTED_PRODUCTION_DB_HOST or EXPECTED_PRODUCTION_DB_NAME
        unset AND allow_non_production=False → returns 3. Forces
        operator to either set both OR explicitly opt out via the
        local-dev flag (fail-closed default)."""
        from scripts.daily_diff import _check_pattern_d_endpoint

        # Host unset
        rc1, msg1 = _check_pattern_d_endpoint(
            self.PROD_URL, "neondb",
            expected_db_name="neondb", expected_db_host=None,
            allow_non_production=False,
        )
        assert rc1 == 3
        assert "EXPECTED_PRODUCTION" in msg1

        # DB name unset
        rc2, _ = _check_pattern_d_endpoint(
            self.PROD_URL, "neondb",
            expected_db_name=None, expected_db_host="ep-fragrant",
            allow_non_production=False,
        )
        assert rc2 == 3

        # Both unset
        rc3, _ = _check_pattern_d_endpoint(
            self.PROD_URL, "neondb",
            expected_db_name=None, expected_db_host=None,
            allow_non_production=False,
        )
        assert rc3 == 3

    def test_database_url_missing_fails(self):
        """DATABASE_URL=None (or empty) with allow_non_production=False
        → returns 3. Defensive check — daily_diff()'s exit-code-1
        path handles a missing engine, but the pre-flight should
        surface DATABASE_URL absence with the same exit-3 semantic
        as other Pattern D failures."""
        from scripts.daily_diff import _check_pattern_d_endpoint
        rc, msg = _check_pattern_d_endpoint(
            None, "neondb",
            expected_db_name="neondb",
            expected_db_host="ep-fragrant-frog-ak3esp11",
            allow_non_production=False,
        )
        assert rc == 3
        assert "DATABASE_URL" in msg


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

    def test_golf_prop_market_filtered_out(self):
        """Golf tournament-prop records filter out via the Day-22
        vocabulary extension.

        Per Day-22 survey: Kalshi Golf is structurally tournament-prop-
        only — no per-fixture H2H ticker exists. These records were
        previously counted in raw.signal_extraction_skipped (Kalshi
        resolver returns None at signal extraction for non-per_fixture
        records). With the v0.3.0 vocabulary extension, suffix-after-
        colon matches against the Golf entries route them to
        raw.prop_market_filtered_out instead.
        """
        from scripts.daily_diff import (
            classify_record, ScopeClassification,
        )
        # Canonical Golf prop suffixes from outcome_shapes.py:200-208
        # + KALSHI_AUDIT.md §4 + Pattern A Q3 vocabulary harvest
        # (Day-22).
        for suffix in [
            "Hole-in-One",
            "Top 5 Finishers",
            "Top 10 Finishers",
            "Top 20 Finishers",
            "Round 1 Top 5 Finishers",
            "Round 1 Top 10 Finishers",
            "Playoff",
            "To Make the Cut",
            "Golf Majors in 2026",
            # Q3 vocabulary harvest additions
            "Cut Line",
            "Eagle in Round 1",
            "Round 1 Scores", "Round 2 Scores",
            "Round 3 Scores", "Round 4 Scores",
            "Round 1 Hole Scores", "Round 2 Hole Scores",
            "Round 3 Hole Scores", "Round 4 Hole Scores",
        ]:
            record = {
                "raw_payload": {
                    "_sport": "Golf",
                    "title": f"PGA Tour Championship: {suffix}",
                },
            }
            assert classify_record("kalshi", record) == ScopeClassification.PROP_MARKET, (
                f"Golf vocabulary entry {suffix!r} should classify as prop_market"
            )

    def test_golf_no_colon_records_stay_signal_extraction_skipped(self):
        """Golf records WITHOUT a colon in the title (e.g., tournament-
        outright-winner shapes like "PGA Tour Championship Winner",
        multi-player matchup shapes like "Smith vs Jones" from
        KXPGAH2H or "Smith/Jones/Brown" from KXPGA3BALL) do NOT match
        the rpartition-after-colon heuristic and flow through to the
        matcher. Kalshi's resolver returns None for non-per_fixture
        records, so they land in raw.signal_extraction_skipped — same
        disposition as before the v0.3.0 vocabulary extension.

        This test pins the scope-filter's narrowness: the vocabulary
        extension covers the colon-suffix shape only. Tournament-
        outright shapes and multi-player matchup shapes need a separate
        intervention (series-ticker-base filter or non-per_fixture-
        aware classification) — out of scope for this PR.

        Pattern A Q1 result (Day-22) shows KXPGA3BALL dominates at 49
        records/24h while KXPGAH2H carries 1 record/24h. The KXPGAH2H
        path is intentionally left open for a future personal-path
        Golf bootstrap; KXPGA3BALL is the bigger out-of-scope-but-
        unfiltered population that may motivate a series-ticker
        filter follow-up.
        """
        from scripts.daily_diff import (
            classify_record, ScopeClassification,
        )
        # Tournament-outright (no colon)
        outright = {
            "raw_payload": {
                "_sport": "Golf",
                "title": "PGA Tour Championship Winner",
            },
        }
        assert classify_record("kalshi", outright) == ScopeClassification.HEAD_TO_HEAD

        # KXPGAH2H per-fixture (no colon) — intentionally unfiltered
        h2h = {
            "raw_payload": {
                "_sport": "Golf",
                "title": "Smith vs Jones",
            },
        }
        assert classify_record("kalshi", h2h) == ScopeClassification.HEAD_TO_HEAD

        # KXPGA3BALL multi-player matchup (no colon)
        triple = {
            "raw_payload": {
                "_sport": "Golf",
                "title": "Smith/Jones/Brown",
            },
        }
        assert classify_record("kalshi", triple) == ScopeClassification.HEAD_TO_HEAD

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
    """Per PR #175 §7 measurement targets — per-sport breakdowns.

    Q-A (approved): pure-function tests on mock dicts shaped like
        {"reason_code": str, "reason_detail": {"sport": str}}
    Q-B (approved): pure-function tests on synthetic
        {"created_at": dt, "sport": str, "status": str}
    dicts for queue metrics; integration test for the SQL→dict
    conversion lives in TestDailyDiffIntegration (Step 6).
    """

    def test_per_sport_auto_apply_rate_calculated(self):
        """Auto-apply count / scope-filtered denominator, partitioned
        by reason_detail->>'sport'."""
        from scripts.daily_diff import aggregate_per_sport_metrics
        rows = [
            # Tennis: 2 auto-apply (strict + fuzzy), 2 non-auto (review + no_match)
            {"reason_code": "strict",       "reason_detail": {"sport": "tennis"}},
            {"reason_code": "fuzzy",        "reason_detail": {"sport": "tennis"}},
            {"reason_code": "review_queue", "reason_detail": {"sport": "tennis"}},
            {"reason_code": "no_match",     "reason_detail": {"sport": "tennis"}},
            # Soccer: 3 auto-apply (alias × 3), 1 review_queue
            {"reason_code": "alias",        "reason_detail": {"sport": "Soccer"}},
            {"reason_code": "alias",        "reason_detail": {"sport": "Soccer"}},
            {"reason_code": "alias",        "reason_detail": {"sport": "Soccer"}},
            {"reason_code": "review_queue", "reason_detail": {"sport": "Soccer"}},
        ]
        result = aggregate_per_sport_metrics(rows)

        # Overall: 5 auto-apply / 8 total = 0.625
        assert result["matcher_capability_rate_overall"] == pytest.approx(0.625)

        # Per-sport: tennis 2/4 = 0.5; Soccer 3/4 = 0.75
        assert result["matcher_capability_rate_per_sport"]["tennis"] == pytest.approx(0.5)
        assert result["matcher_capability_rate_per_sport"]["Soccer"] == pytest.approx(0.75)

    def test_per_tier_resolution_rate_calculated(self):
        """strict / alias / fuzzy / no_match / review_queue / crash
        breakdown per sport."""
        from scripts.daily_diff import (
            aggregate_per_sport_metrics, PER_TIER_BUCKETS,
        )
        rows = [
            {"reason_code": "strict",       "reason_detail": {"sport": "Baseball"}},
            {"reason_code": "alias",        "reason_detail": {"sport": "Baseball"}},
            {"reason_code": "fuzzy",        "reason_detail": {"sport": "Baseball"}},
            {"reason_code": "no_match",     "reason_detail": {"sport": "Baseball"}},
            {"reason_code": "review_queue", "reason_detail": {"sport": "Baseball"}},
            # 'crash' is a synthetic bucket — not a ReasonCode enum
            # member; the measurement script tags raised invocations.
            {"reason_code": "crash",        "reason_detail": {"sport": "Baseball"}},
        ]
        result = aggregate_per_sport_metrics(rows)
        tiers = result["per_tier_rate_per_sport"]["Baseball"]

        # All six bucket keys present, each with count 1.
        assert set(tiers.keys()) == set(PER_TIER_BUCKETS)
        for bucket in PER_TIER_BUCKETS:
            assert tiers[bucket] == 1, (
                f"Bucket {bucket!r} should have 1 row, got {tiers[bucket]}"
            )

        # 'crash' rows must NOT count toward auto-apply.
        # 3 auto-apply (strict + alias + fuzzy) / 6 total = 0.5.
        assert result["matcher_capability_rate_per_sport"]["Baseball"] == pytest.approx(0.5)

    def test_personal_path_vs_team_path_distinction(self):
        """Aggregated by INDIVIDUAL_SPORT_CODES membership.
        Personal-path = tennis/mma/boxing/golf/snooker/darts.
        Team-path = everything else."""
        from scripts.daily_diff import aggregate_per_sport_metrics
        rows = [
            # Personal-path: tennis + mma + golf.
            # Tennis: 1 auto-apply, 1 review_queue
            {"reason_code": "strict",       "reason_detail": {"sport": "tennis"}},
            {"reason_code": "review_queue", "reason_detail": {"sport": "tennis"}},
            # MMA: 1 fuzzy (auto-apply)
            {"reason_code": "fuzzy",        "reason_detail": {"sport": "mma"}},
            # Golf: 1 no_match
            {"reason_code": "no_match",     "reason_detail": {"sport": "golf"}},
            # Team-path: Soccer + Baseball + Hockey.
            # Soccer: 2 alias (auto-apply)
            {"reason_code": "alias",        "reason_detail": {"sport": "Soccer"}},
            {"reason_code": "alias",        "reason_detail": {"sport": "Soccer"}},
            # Baseball: 1 no_match
            {"reason_code": "no_match",     "reason_detail": {"sport": "Baseball"}},
            # Hockey: 1 review_queue
            {"reason_code": "review_queue", "reason_detail": {"sport": "Hockey"}},
        ]
        result = aggregate_per_sport_metrics(rows)

        # Personal: 4 total, 2 auto-apply (tennis strict + mma fuzzy)
        assert result["personal_path_rate"] == pytest.approx(2 / 4)
        # Team: 4 total, 2 auto-apply (2 Soccer alias)
        assert result["team_path_rate"] == pytest.approx(2 / 4)

        # Case-insensitive INDIVIDUAL_SPORT_CODES membership.
        rows_mixed_case = [
            {"reason_code": "strict", "reason_detail": {"sport": "Tennis"}},
            {"reason_code": "strict", "reason_detail": {"sport": "TENNIS"}},
        ]
        result2 = aggregate_per_sport_metrics(rows_mixed_case)
        assert result2["personal_path_rate"] == pytest.approx(1.0)
        assert result2["team_path_rate"] == 0.0  # empty cohort → 0

    def test_queue_depth_per_sport(self):
        """Pending review_queue rows, grouped by sport. Non-pending
        statuses (approved / rejected) don't contribute to depth."""
        from scripts.daily_diff import aggregate_queue_metrics
        now = datetime(2026, 5, 21, 12, 0, 0, tzinfo=timezone.utc)
        rows = [
            {"sport": "tennis",  "status": "pending",  "created_at": now - timedelta(hours=1)},
            {"sport": "tennis",  "status": "pending",  "created_at": now - timedelta(hours=2)},
            {"sport": "tennis",  "status": "approved", "created_at": now - timedelta(hours=3)},
            {"sport": "Soccer",  "status": "pending",  "created_at": now - timedelta(hours=1)},
            {"sport": "Soccer",  "status": "rejected", "created_at": now - timedelta(hours=4)},
        ]
        result = aggregate_queue_metrics(rows, now=now)
        # Only status='pending' counts.
        assert result["depth_per_sport"] == {"tennis": 2, "Soccer": 1}

    def test_time_in_queue_per_sport(self):
        """Median + p95 of (now - created_at) for pending records,
        in seconds, per sport."""
        from scripts.daily_diff import aggregate_queue_metrics
        now = datetime(2026, 5, 21, 12, 0, 0, tzinfo=timezone.utc)
        # Tennis: 5 pending rows aged 1/2/3/4/5 hours
        rows = [
            {"sport": "tennis", "status": "pending",
             "created_at": now - timedelta(hours=h)}
            for h in (1, 2, 3, 4, 5)
        ]
        result = aggregate_queue_metrics(rows, now=now)
        # Median of [1h, 2h, 3h, 4h, 5h] = 3h = 10800s
        assert result["median_time_in_queue_per_sport"]["tennis"] == pytest.approx(3 * 3600)
        # p95 nearest-rank: idx = round(0.95 * 4) = 4 → 5h = 18000s
        assert result["p95_time_in_queue_per_sport"]["tennis"] == pytest.approx(5 * 3600)

    def test_abandonment_rate_per_sport(self):
        """Per-sport fraction of pending records aging beyond N days
        without operator action (default N=14)."""
        from scripts.daily_diff import aggregate_queue_metrics
        now = datetime(2026, 5, 21, 12, 0, 0, tzinfo=timezone.utc)
        rows = [
            # tennis: 2 pending, 1 abandoned (>14d), 1 fresh (1d)
            {"sport": "tennis", "status": "pending",
             "created_at": now - timedelta(days=20)},
            {"sport": "tennis", "status": "pending",
             "created_at": now - timedelta(days=1)},
            # Soccer: 1 pending, 1 fresh — 0 abandonment
            {"sport": "Soccer", "status": "pending",
             "created_at": now - timedelta(days=5)},
            # Approved row past threshold — doesn't count (status filter)
            {"sport": "Soccer", "status": "approved",
             "created_at": now - timedelta(days=30)},
        ]
        result = aggregate_queue_metrics(rows, now=now)
        assert result["abandonment_rate_per_sport"]["tennis"] == pytest.approx(0.5)
        assert result["abandonment_rate_per_sport"]["Soccer"] == 0.0

        # Custom threshold: with abandonment_days=3, the 5d-old Soccer
        # row counts as abandoned.
        result_strict = aggregate_queue_metrics(rows, now=now, abandonment_days=3)
        assert result_strict["abandonment_rate_per_sport"]["Soccer"] == pytest.approx(1.0)

    def test_empty_input_safe(self):
        """Empty iterable inputs return zero-valued metrics rather
        than raising ZeroDivisionError. Per-sport buckets can legit-
        imately be empty (no Tennis records today, etc.)."""
        from scripts.daily_diff import (
            aggregate_per_sport_metrics, aggregate_queue_metrics,
        )
        now = datetime(2026, 5, 21, 12, 0, 0, tzinfo=timezone.utc)

        per_sport = aggregate_per_sport_metrics([])
        assert per_sport["matcher_capability_rate_overall"] == 0.0
        assert per_sport["matcher_capability_rate_per_sport"] == {}
        assert per_sport["per_tier_rate_per_sport"] == {}
        assert per_sport["personal_path_rate"] == 0.0
        assert per_sport["team_path_rate"] == 0.0

        queue = aggregate_queue_metrics([], now=now)
        assert queue["depth_per_sport"] == {}
        assert queue["median_time_in_queue_per_sport"] == {}
        assert queue["p95_time_in_queue_per_sport"] == {}
        assert queue["abandonment_rate_per_sport"] == {}


# ══════════════════════════════════════════════════════════════
# resolution_log volume tracking (post-Finding X)
# ══════════════════════════════════════════════════════════════


class TestResolutionLogVolume:
    """Per Finding X (2026-05-20): cron re-processes pending records
    daily across all 3 tiers, producing ~7.3M rows/year retry traffic
    at current scale. Track A measures the rate to inform §6.5
    archival sizing (Issue #164)."""

    def test_resolution_log_volume_partitioned_by_reason_code(self):
        """Per-cron-run row counts, partitioned by reason_code, captured
        in metrics.resolution_log_volume_per_cron via
        aggregate_resolution_log_volume()."""
        from scripts.daily_diff import aggregate_resolution_log_volume
        rows = [
            {"reason_code": "strict"},
            {"reason_code": "strict"},
            {"reason_code": "alias"},
            {"reason_code": "no_match"},
            {"reason_code": "no_match"},
            {"reason_code": "no_match"},
            {"reason_code": "review_queue"},
            {"reason_code": None},  # null reason_code → bucketed under ''
        ]
        result = aggregate_resolution_log_volume(rows)
        assert result["total"] == 8
        assert result["by_reason_code"]["strict"] == 2
        assert result["by_reason_code"]["alias"] == 1
        assert result["by_reason_code"]["no_match"] == 3
        assert result["by_reason_code"]["review_queue"] == 1
        assert result["by_reason_code"][""] == 1

        # Empty input → safe
        empty = aggregate_resolution_log_volume([])
        assert empty == {"by_reason_code": {}, "total": 0}


# ══════════════════════════════════════════════════════════════
# Histogram generation (for sample disagreements)
# ══════════════════════════════════════════════════════════════


class TestHistogramGeneration:
    """Confidence-score distribution histogram, stored in report_json.
    Used for threshold-calibration analysis."""

    def test_histogram_buckets_match_threshold_boundaries(self):
        """Histogram buckets align with auto_apply / review_queue /
        no_match thresholds (0.85, 0.70) so the boundary records are
        visible in their own buckets."""
        from scripts.daily_diff import (
            CONFIDENCE_BUCKETS, compute_confidence_histogram,
        )
        labels = [label for label, _, _ in CONFIDENCE_BUCKETS]
        # 0.70-0.85 = review_queue band (between thresholds)
        assert "0.70-0.85" in labels
        # Auto-apply records split into low/high-confidence bands
        # so threshold calibration analysis can see the distribution.
        assert "0.85-0.95" in labels
        assert "0.95-1.00" in labels

        # Empty input → all buckets present, all counts 0.
        empty = compute_confidence_histogram([])
        assert set(empty.keys()) == set(labels)
        assert all(count == 0 for count in empty.values())

    def test_histogram_bin_assignments(self):
        """Scores land in the expected buckets. Lower bound inclusive,
        upper bound exclusive; final bucket closed on both ends so
        score=1.0 has a home."""
        from scripts.daily_diff import compute_confidence_histogram
        scores = [
            0.00, 0.49,            # 0.00-0.50
            0.50, 0.69,            # 0.50-0.70
            0.70, 0.84,            # 0.70-0.85
            0.85, 0.94,            # 0.85-0.95
            0.95, 0.98, 1.00,      # 0.95-1.00 (final bucket closed)
        ]
        result = compute_confidence_histogram(scores)
        assert result["0.00-0.50"] == 2
        assert result["0.50-0.70"] == 2
        assert result["0.70-0.85"] == 2
        assert result["0.85-0.95"] == 2
        assert result["0.95-1.00"] == 3, (
            f"1.00 must land in the final bucket; got {result['0.95-1.00']}."
        )

    def test_histogram_clamps_out_of_range_scores(self):
        """Defensive: scores outside [0, 1] clamp to the boundary."""
        from scripts.daily_diff import compute_confidence_histogram
        result = compute_confidence_histogram([-0.5, 1.5, 2.0])
        # -0.5 → 0.0 → first bucket; 1.5/2.0 → 1.0 → final bucket
        assert result["0.00-0.50"] == 1
        assert result["0.95-1.00"] == 2


# ══════════════════════════════════════════════════════════════
# Integration tests (SP_INTEGRATION_DB-gated)
# ══════════════════════════════════════════════════════════════


@pytest.mark.skipif(
    not INTEGRATION_DB,
    reason="SP_INTEGRATION_DB not set — daily-diff integration tests need real Postgres.",
)
class TestDailyDiffIntegration:
    """Real-DB tests against a Postgres with the Phase 2 Track A
    migration (c4d9e2a1b3f7) applied. Run via:

        SP_INTEGRATION_DB=postgresql+asyncpg://... pytest tests/test_daily_diff.py -v
    """

    @pytest.mark.asyncio
    async def test_migration_creates_tables(self):
        """After alembic upgrade head, sp.daily_diff_reports and
        sp.baseline_shifts exist with the documented schemas."""
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine
        engine = create_async_engine(INTEGRATION_DB)
        async with engine.connect() as conn:
            for table_name in ("daily_diff_reports", "baseline_shifts"):
                result = await conn.execute(text(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema='sp' AND table_name=:t)"
                ), {"t": table_name})
                assert result.scalar() is True, (
                    f"sp.{table_name} missing — migration not applied?"
                )

            # daily_diff_reports must carry the documented columns.
            cols = (await conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='sp' AND table_name='daily_diff_reports'"
            ))).scalars().all()
            for expected in (
                "id", "report_date", "window_start", "window_end",
                "total_records_scanned", "metrics", "scope_filter_version",
                "report_json", "legacy_comparison_present", "created_at",
            ):
                assert expected in cols, (
                    f"sp.daily_diff_reports.{expected} missing"
                )
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_idempotency_on_same_day_rerun(self):
        """Inserting two rows with the same report_date raises
        IntegrityError (UNIQUE constraint), which _write_report
        converts to exit code 4."""
        from datetime import date
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine
        from scripts.daily_diff import _write_report

        # Use a sentinel date in the far future to avoid colliding
        # with real cron rows. Tear down afterward.
        sentinel = date(2099, 12, 31)
        engine = create_async_engine(INTEGRATION_DB)
        try:
            # First write — should succeed (0).
            window_start = datetime(2099, 12, 30, tzinfo=timezone.utc)
            window_end = datetime(2099, 12, 31, tzinfo=timezone.utc)
            rc1 = await _write_report(
                window_start=window_start, window_end=window_end,
                metrics={"scope_filtered": {}}, report_json={},
                total_records=1, report_date=sentinel,
            )
            assert rc1 == 0

            # Second write — UNIQUE violation → exit code 4.
            rc2 = await _write_report(
                window_start=window_start, window_end=window_end,
                metrics={"scope_filtered": {}}, report_json={},
                total_records=1, report_date=sentinel,
            )
            assert rc2 == 4
        finally:
            async with engine.connect() as conn:
                await conn.execute(text(
                    "DELETE FROM sp.daily_diff_reports "
                    "WHERE report_date = :d"
                ), {"d": sentinel})
                await conn.commit()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_scope_filter_version_stamped(self):
        """_write_report stamps SCOPE_FILTER_VERSION into the row.
        Version-stamping enables historical re-interpretation when
        the filter rules change."""
        from datetime import date
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine
        from scripts.daily_diff import _write_report, SCOPE_FILTER_VERSION

        sentinel = date(2099, 12, 30)
        engine = create_async_engine(INTEGRATION_DB)
        try:
            await _write_report(
                window_start=datetime(2099, 12, 29, tzinfo=timezone.utc),
                window_end=datetime(2099, 12, 30, tzinfo=timezone.utc),
                metrics={"scope_filtered": {}}, report_json={},
                total_records=1, report_date=sentinel,
            )
            async with engine.connect() as conn:
                stamped = (await conn.execute(text(
                    "SELECT scope_filter_version "
                    "FROM sp.daily_diff_reports WHERE report_date = :d"
                ), {"d": sentinel})).scalar_one()
            assert stamped == SCOPE_FILTER_VERSION
        finally:
            async with engine.connect() as conn:
                await conn.execute(text(
                    "DELETE FROM sp.daily_diff_reports "
                    "WHERE report_date = :d"
                ), {"d": sentinel})
                await conn.commit()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_baseline_shift_annotation_read_through(self):
        """A row in sp.baseline_shifts is visible to render_daily_diff_report's
        _fetch_window logic, which surfaces it in the rendered output."""
        from datetime import date
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine
        from scripts.render_daily_diff_report import (
            _fetch_window, render_markdown,
        )

        engine = create_async_engine(INTEGRATION_DB)
        sentinel_id = None
        try:
            # Insert a sentinel baseline-shift row dated today.
            today = datetime.now(timezone.utc).date()
            async with engine.connect() as conn:
                result = await conn.execute(text(
                    "INSERT INTO sp.baseline_shifts "
                    "  (event_type, event_date, affected_population, notes, "
                    "   created_by) "
                    "VALUES (:t, :d, :ap, :n, :cb) RETURNING id"
                ), {
                    "t": "test_event", "d": today,
                    "ap": "Integration test sentinel",
                    "n": "Inserted by test_baseline_shift_annotation_read_through",
                    "cb": "integration_test",
                })
                sentinel_id = result.scalar_one()
                await conn.commit()

            now = datetime.now(timezone.utc)
            reports, shifts = await _fetch_window(window_days=1, now=now)
            md = render_markdown(reports, shifts, window_days=1, now=now)
            assert "Integration test sentinel" in md
            assert "test_event" in md
        finally:
            if sentinel_id is not None:
                async with engine.connect() as conn:
                    await conn.execute(text(
                        "DELETE FROM sp.baseline_shifts WHERE id = :id"
                    ), {"id": sentinel_id})
                    await conn.commit()
            await engine.dispose()

    @pytest.mark.skip(
        reason="Migration roundtrip skipped — downgrade not implemented for "
               "Track A measurement-infrastructure tables (intentional per "
               "scope doc §14: throw-away-post-Phase-3).",
    )
    def test_migration_roundtrip(self):
        """Skipped: Track A migration intentionally lacks a destructive
        downgrade path. Per scope doc §14 the tables are throw-away
        post-Phase-3 cutover, so the upgrade/downgrade roundtrip
        contract doesn't apply."""
        pass

    @pytest.mark.skip(
        reason="Empty-window test requires controlled window with zero "
               "ingestion records; setup deferred to end-to-end smoke "
               "in operator's manual verification run.",
    )
    def test_empty_window_exits_cleanly(self):
        """Empty-window exit-code-5 behavior: tested via the operator's
        manual verification run after Step 6 lands. Synthetic empty-
        window via SQL DELETE is too invasive for the integration
        suite."""
        pass


# ══════════════════════════════════════════════════════════════
# Render script tests
# ══════════════════════════════════════════════════════════════


class TestRenderScript:
    """scripts/render_daily_diff_report.py output format tests.

    Pure-function tests against render_markdown(). The DB-fetch path
    (_fetch_window) is exercised by TestDailyDiffIntegration when
    SP_INTEGRATION_DB is set.
    """

    @staticmethod
    def _sample_report_row(
        *, report_date, legacy: bool = False,
    ) -> dict:
        return {
            "report_date": report_date,
            "window_start": datetime(2026, 5, 20, tzinfo=timezone.utc),
            "window_end": datetime(2026, 5, 21, tzinfo=timezone.utc),
            "total_records_scanned": 42,
            "scope_filter_version": "v0.1.0",
            "legacy_comparison_present": legacy,
            "created_at": datetime(2026, 5, 21, 2, 30, tzinfo=timezone.utc),
            "metrics": {
                "scope_filtered": {
                    "matcher_capability_rate_overall": 0.75,
                    "matcher_capability_rate_per_sport": {"Tennis": 0.5, "Soccer": 0.9},
                    "per_tier_rate_per_sport": {},
                    "personal_path_rate": 0.5,
                    "team_path_rate": 0.85,
                },
                "raw": {
                    "matcher_capability_rate_overall_unfiltered": 0.42,
                    "signal_extraction_skipped": 3,
                    "non_sport_filtered_out": 12,
                    "prop_market_filtered_out": 5,
                },
                "queue": {
                    "depth_per_sport": {"Tennis": 4},
                    "median_time_in_queue_per_sport": {"Tennis": 3600.0},
                    "p95_time_in_queue_per_sport": {"Tennis": 7200.0},
                    "abandonment_rate_per_sport": {"Tennis": 0.0},
                },
                "resolution_log_volume_per_cron": {
                    "by_reason_code": {"strict": 100, "no_match": 50},
                    "total": 150,
                },
            },
            "report_json": {
                "confidence_histogram": {
                    "0.00-0.50": 5, "0.50-0.70": 3,
                    "0.70-0.85": 7, "0.85-0.95": 20, "0.95-1.00": 10,
                },
                "confidence_scores_count": 45,
            },
        }

    def test_render_outputs_markdown(self):
        """Render produces markdown with the documented section structure."""
        from scripts.render_daily_diff_report import render_markdown
        from datetime import date
        rows = [self._sample_report_row(report_date=date(2026, 5, 21))]
        now = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)
        md = render_markdown(rows, [], window_days=7, now=now)

        # Headline + key sections
        assert "# Daily-diff report (window: 7 days)" in md
        assert "## Window summary" in md
        assert "## Per-sport matcher-capability rates" in md
        assert "## Confidence histogram (latest)" in md
        assert "## Baseline-shift events" in md
        assert "## sp.resolution_log volume" in md

        # Headline metric value rendered.
        assert "75.0%" in md  # scope-filtered matcher-capability rate
        assert "42.0%" in md  # unfiltered

        # Window-summary table includes Team-path + Personal-path columns
        # (headline-promote per 2026-05-21 empirical findings).
        assert "Team-path" in md
        assert "Personal-path" in md
        assert "85.0%" in md  # team_path_rate from fixture
        assert "50.0%" in md  # personal_path_rate from fixture

        # Per-sport rates appear.
        assert "Tennis" in md
        assert "Soccer" in md

        # Histogram buckets appear.
        assert "0.85-0.95" in md
        assert "0.70-0.85" in md

    def test_render_falls_back_to_v0_1_0_keys(self):
        """Historical rows written under scope_filter_version v0.1.0
        used auto_apply_rate_* key names. Render falls back so old
        rows still display rather than rendering 0.0%."""
        from scripts.render_daily_diff_report import render_markdown
        from datetime import date
        legacy_row = {
            "report_date": date(2026, 5, 21),
            "window_start": datetime(2026, 5, 20, tzinfo=timezone.utc),
            "window_end": datetime(2026, 5, 21, tzinfo=timezone.utc),
            "total_records_scanned": 17996,
            "scope_filter_version": "v0.1.0",
            "legacy_comparison_present": False,
            "created_at": datetime(2026, 5, 21, 2, 30, tzinfo=timezone.utc),
            "metrics": {
                "scope_filtered": {
                    "auto_apply_rate_overall": 0.484,
                    "auto_apply_rate_per_sport": {"Soccer": 0.852},
                    "personal_path_rate": 0.123,
                    "team_path_rate": 0.702,
                },
                "raw": {"auto_apply_rate_overall_unfiltered": 0.279},
            },
            "report_json": {},
        }
        now = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)
        md = render_markdown([legacy_row], [], window_days=7, now=now)
        # v0.1.0 rates render via fallback path.
        assert "48.4%" in md
        assert "27.9%" in md
        assert "70.2%" in md
        assert "12.3%" in md
        # Version stamp visible so reader knows which schema.
        assert "v0.1.0" in md

    def test_render_distinguishes_d2_only_vs_d2_plus_d1(self):
        """Reports with legacy_comparison_present=false (D2-only)
        omit the 'Sample disagreements' section. Reports with
        legacy_comparison_present=true (D1+D2) include it."""
        from scripts.render_daily_diff_report import render_markdown
        from datetime import date
        now = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)

        # D2-only — no sample-disagreements section
        d2_only = render_markdown(
            [self._sample_report_row(
                report_date=date(2026, 5, 21), legacy=False,
            )],
            [], window_days=7, now=now,
        )
        assert "Sample disagreements" not in d2_only
        assert "D2-only" in d2_only

        # D1+D2 — section present (placeholder until Deliverable 1)
        d1_d2 = render_markdown(
            [self._sample_report_row(
                report_date=date(2026, 5, 21), legacy=True,
            )],
            [], window_days=7, now=now,
        )
        assert "Sample disagreements" in d1_d2
        assert "D1+D2" in d1_d2

    def test_render_includes_baseline_shifts_in_window(self):
        """sp.baseline_shifts rows appear in the 'Baseline-shift
        events' section. Empty shift list produces the empty marker."""
        from scripts.render_daily_diff_report import render_markdown
        from datetime import date
        now = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)

        shift = {
            "event_type": "dedup",
            "event_date": date(2026, 5, 20),
            "affected_population": "Tennis players (cross-format dupes)",
            "expected_metric_delta": "Tennis auto-apply +5-10%",
            "notes": "Bulk dedup landed 2026-05-20",
            "created_by": "PR #999",
            "created_at": datetime(2026, 5, 20, 14, 0, tzinfo=timezone.utc),
        }
        md = render_markdown(
            [self._sample_report_row(report_date=date(2026, 5, 21))],
            [shift], window_days=7, now=now,
        )
        assert "dedup" in md
        assert "Tennis players (cross-format dupes)" in md
        assert "PR #999" in md

        # Empty shifts → empty marker rendered
        md_empty = render_markdown(
            [self._sample_report_row(report_date=date(2026, 5, 21))],
            [], window_days=7, now=now,
        )
        assert "_No baseline-shift events in window._" in md_empty

    def test_render_window_default_7_days(self):
        """Render script's CLI default is 7 days. Module-level constant
        documents the contract."""
        from scripts.render_daily_diff_report import DEFAULT_WINDOW_DAYS
        assert DEFAULT_WINDOW_DAYS == 7

    def test_render_empty_window_renders_safe_message(self):
        """No reports + no shifts → render produces a markdown document
        with the empty-window markers. (The script's runtime wrapper
        also exits with code 5 in this scenario, exercised by the
        SP_INTEGRATION_DB-gated tests.)"""
        from scripts.render_daily_diff_report import render_markdown
        now = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)
        md = render_markdown([], [], window_days=7, now=now)
        assert "# Daily-diff report (window: 7 days)" in md
        assert "_No reports in window._" in md
        assert "_No baseline-shift events in window._" in md


# ── Deliverable 1: v3-vs-v4 legacy comparison ─────────────────────
#
# Per docs/measurement/deliverable-1-scope-2026-07-25.md §4.6.
# All 12 tests specified by the amended scope doc. Category split:
#   1-7: core _diff_pairings classification (pure-function, no DB)
#   8:   refactor safety (_build_kalshi_index_for_sport records param)
#   9-11: namespace / window / fl_event_id key-format discipline
#   12-13: summed-across-sports aggregation shape (2 tests)


class _FakeExtractorAlwaysReturnsSignal:
    """Extractor stub that never refuses — every record produces a
    (truthy) signal, so v4_extraction_excluded stays empty."""

    def extract_signal(self, record):
        return "signal"  # any truthy value


class _FakeExtractorRefusesTicker:
    """Extractor stub that refuses specific event_tickers. Lets tests
    exercise the v4_extraction_excluded bucket precisely."""

    def __init__(self, refused_event_tickers):
        self._refused = set(refused_event_tickers)

    def extract_signal(self, record):
        return None if record.get("event_ticker") in self._refused else "signal"


class TestDiffPairingsClassification:
    """§4.6 tests 1-7: pure-function _diff_pairings bucket classification.
    No DB required; all inputs are hand-built dicts of sets."""

    def test_diff_pairings_bucket_invariant(self):
        """Bucket counts sum to total_evaluated. The pipeline-halt
        guard that catches classification bugs before they persist."""
        from scripts.daily_diff import _diff_pairings
        legacy = {"fl_a": {"E1"}, "fl_b": {"E2"}, "fl_c": {"E3"}}
        v4 = {"fl_a": {"E1"}, "fl_b": {"E2"}, "fl_d": {"E4"}}
        out = _diff_pairings(legacy, v4, _FakeExtractorAlwaysReturnsSignal(), [])
        bucket_sum = (
            out["agree_same_fixture"] + out["agree_partial_coverage"]
            + out["v4_only"] + out["legacy_only"]
            + out["both_pair_different"] + out["v4_extraction_excluded"]
        )
        assert bucket_sum == out["total_evaluated"]
        assert out["total_evaluated"] == 4  # fl_a, fl_b, fl_c, fl_d

    def test_diff_pairings_invariant_asserts_on_orphan_fl_id_with_empty_sets(self):
        """Deliberately-corrupted fixture: an fl_id keyed to empty sets
        in both maps produces no bucket increment. Invariant assert must
        fire — pipeline-halt guard proves it guards."""
        import pytest
        from scripts.daily_diff import _diff_pairings
        # fl_orphan is in all_fl_ids (via legacy_map key) but its sets
        # are empty on both sides → nothing increments → invariant fires.
        legacy = {"fl_orphan": set()}
        v4 = {"fl_orphan": set()}
        with pytest.raises(AssertionError, match="invariant violated"):
            _diff_pairings(legacy, v4, _FakeExtractorAlwaysReturnsSignal(), [])

    def test_diff_pairings_extraction_exclusion_isolates(self):
        """Legacy pairing whose only Kalshi tickers are v4-refused
        goes to v4_extraction_excluded, NOT legacy_only. Correct-
        behavior classes (KXMLBMENTION etc.) never read as regression."""
        from scripts.daily_diff import _diff_pairings
        legacy = {"fl_a": {"KXMLBMENTION-123"}}
        v4: dict = {}
        extractor = _FakeExtractorRefusesTicker({"KXMLBMENTION-123"})
        records = [{"event_ticker": "KXMLBMENTION-123"}]
        out = _diff_pairings(legacy, v4, extractor, records)
        assert out["v4_extraction_excluded"] == 1
        assert out["legacy_only"] == 0

    def test_diff_pairings_full_agreement(self):
        """Identical maps → all agree_same_fixture; other buckets zero."""
        from scripts.daily_diff import _diff_pairings
        legacy = {"fl_a": {"E1"}, "fl_b": {"E2", "E3"}}
        v4 = {"fl_a": {"E1"}, "fl_b": {"E2", "E3"}}
        out = _diff_pairings(legacy, v4, _FakeExtractorAlwaysReturnsSignal(), [])
        assert out["agree_same_fixture"] == 2
        assert out["agree_partial_coverage"] == 0
        assert out["both_pair_different"] == 0
        assert out["legacy_only"] == 0
        assert out["v4_only"] == 0

    def test_diff_pairings_partial_coverage_not_dangerous(self):
        """Overlapping-unequal sets: {A,B} vs {A,C} → agree_partial_
        coverage, NOT both_pair_different. Guards against operator-
        caught regression that would set Item 7 threshold against
        benign coverage noise."""
        from scripts.daily_diff import _diff_pairings
        legacy = {"fl_a": {"E1", "E2"}}
        v4 = {"fl_a": {"E1", "E3"}}
        out = _diff_pairings(legacy, v4, _FakeExtractorAlwaysReturnsSignal(), [])
        assert out["agree_partial_coverage"] == 1
        assert out["both_pair_different"] == 0
        assert out["agree_same_fixture"] == 0
        # Sample carries the overlap for eyeball inspection.
        sample = out["sample_disagreements"]["agree_partial_coverage"][0]
        assert sample["overlap"] == ["E1"]

    def test_diff_pairings_dangerous_class_is_disjoint_only(self):
        """Disjoint sets: {A,B} vs {C,D} → both_pair_different. Also
        {A} vs {B} single-element disjoint. The dangerous bucket
        must ONLY carry disjoint disagreements after PR #256's
        equal/overlap/disjoint partition."""
        from scripts.daily_diff import _diff_pairings
        legacy = {"fl_a": {"E1", "E2"}, "fl_b": {"E5"}}
        v4 = {"fl_a": {"E3", "E4"}, "fl_b": {"E6"}}
        out = _diff_pairings(legacy, v4, _FakeExtractorAlwaysReturnsSignal(), [])
        assert out["both_pair_different"] == 2
        assert out["agree_partial_coverage"] == 0
        assert out["agree_same_fixture"] == 0

    def test_diff_pairings_v4_only_and_legacy_only(self):
        """v4-only and legacy-only paths increment their own buckets."""
        from scripts.daily_diff import _diff_pairings
        legacy = {"fl_l": {"E1"}}
        v4 = {"fl_v": {"E2"}}
        out = _diff_pairings(legacy, v4, _FakeExtractorAlwaysReturnsSignal(), [])
        assert out["legacy_only"] == 1
        assert out["v4_only"] == 1


class TestBuildKalshiIndexRecordsParam:
    """§4.6 test 8: refactor safety. Default preserves existing
    cache-read behavior; explicit records bypasses cache."""

    def test_build_kalshi_index_for_sport_records_param(self, monkeypatch):
        """Passing an explicit records list must not touch _cache or
        get_data(). Guards against a future refactor accidentally
        re-adding a `get_data()` call inside the records-supplied branch."""
        import main
        get_data_calls: list[int] = []

        def _boom_if_called():
            get_data_calls.append(1)

        monkeypatch.setattr(main, "get_data", _boom_if_called)
        monkeypatch.setattr(main, "_cache", {})  # empty cache
        # match_game will return None for garbage records; that's fine
        # — the point of this test is the records-branch wiring, not
        # a positive pairing outcome.
        main._build_kalshi_index_for_sport(
            "Basketball",
            records=[{"_sport": "Basketball", "title": "does not match anything"}],
        )
        assert get_data_calls == [], (
            "records-supplied call path must not invoke get_data() — "
            "if it does, same-window discipline breaks on the daily-diff "
            "cron path."
        )


class TestV4QueryDiscipline:
    """§4.6 tests 9-11: namespace-normalization + same-window +
    fl_event_id key-format discipline. Pipeline-shaped; the SQL runs
    against a stub that captures parameters for assertion (no live DB
    required in the general case; the integration variant runs against
    SP_INTEGRATION_DB when set)."""

    def test_v4_query_uses_event_ticker_namespace_directly(self):
        """The v4 SQL aggregates `km.ticker` directly — the DB column
        NAME is "ticker" but its content is the Kalshi EVENT_TICKER
        (populated at ingestion.kalshi:179). No JOIN through
        public.markets/events is needed. Post-manual-run correction
        2026-07-28: the prior version's JOIN chain silently dropped
        ~95% of km rows via the pm.ticker!=km.ticker mismatch —
        this test locks in the corrected direct-aggregation."""
        from scripts.daily_diff import _V4_PAIRINGS_SQL
        sql = _V4_PAIRINGS_SQL
        # Direct aggregation of km.ticker (== event_ticker semantically).
        assert "array_agg(DISTINCT km.ticker)" in sql
        # Must NOT re-introduce the phantom-namespace JOIN chain.
        assert "public.markets" not in sql
        assert "public.events" not in sql
        assert "pe.event_ticker" not in sql

    def test_v4_query_scopes_by_pk_sets(self):
        """The v4 SQL scopes by ANY(:kalshi_pks) + ANY(:fl_pks) — D2's
        actual loaded PK sets — not by re-evaluating last_seen_at at a
        different moment. Post-manual-run tightening 2026-07-28:
        insulates from PR #260's active-row last_seen_at bumps that
        would exclude still-active fixture-linked rows on any
        historical cron run. Guards against a future edit that
        re-introduces a window predicate."""
        from scripts.daily_diff import _V4_PAIRINGS_SQL
        sql = _V4_PAIRINGS_SQL
        assert "fle.fl_event_id = ANY(:fl_pks)" in sql
        assert "km.ticker      = ANY(:kalshi_pks)" in sql
        # No window predicate — same-population is by PK-set scope.
        assert "last_seen_at" not in sql
        assert "window_start" not in sql
        assert "window_end" not in sql

    def test_fl_event_id_key_format_consistency(self):
        """v1, v2, and v4 all emit fl_event_id keys as str. Cross-
        format keys silently produce empty intersections that read
        as both_pair_different, so we assert str-ness at each source's
        boundary. If a future refactor changes any source's key type
        (e.g. UUID), this test fires."""
        from scripts.daily_diff import _run_legacy_pairings
        # v1/v2: exercise via a trivial record + fl_event. Neither
        # pairing algorithm will match, but the empty return still
        # carries the type contract (str keys).
        v1_map, v2_map = _run_legacy_pairings("Basketball", [], [])
        assert all(isinstance(k, str) for k in v1_map.keys())
        assert all(isinstance(k, str) for k in v2_map.keys())
        # v4: the query function coerces to str via `str(row.fl_event_id)`
        # (see _query_v4_pairings). Assert the code path exists via
        # source inspection.
        import inspect
        from scripts.daily_diff import _query_v4_pairings
        src = inspect.getsource(_query_v4_pairings)
        assert "str(row.fl_event_id)" in src, (
            "v4 pairings query must coerce fl_event_id to str to match "
            "v1/v2 key format; drift causes silent disjoint intersection."
        )


class TestGamesHydrationForCron:
    """Symptom 1 fix (2026-07-28): match_game accepts an explicit
    games dict, and _run_legacy_pairings builds one from fl_events
    so the standalone daily-diff cron doesn't produce empty v1 maps
    from an unpopulated module-level GAMES."""

    def test_match_game_accepts_explicit_games_dict(self):
        """match_game(title, sport, games=explicit_dict) works even
        when the module-level GAMES is empty. Default (games=None)
        preserves the pre-existing behavior for every legacy caller."""
        import flashlive_feed
        from flashlive_feed import match_game
        # Force module-level GAMES empty so we know the resolution
        # comes from the explicit dict.
        original = flashlive_feed.GAMES.copy()
        try:
            flashlive_feed.GAMES.clear()
            # Construct an explicit game dict with the fields
            # match_game reads (home_phrases, away_phrases, sport).
            games = {
                "key1": {
                    "sport": "Basketball",
                    "home_name": "Cleveland Cavaliers",
                    "away_name": "Toronto Raptors",
                    "home_phrases": ["cleveland", "cavaliers", "cavs"],
                    "away_phrases": ["toronto", "raptors"],
                    "event_id": "fl_test_evt",
                },
            }
            # With module GAMES empty, no-games call returns None.
            assert match_game("Cleveland Cavaliers vs Toronto Raptors", "Basketball") is None
            # With explicit games, matches.
            hit = match_game("Cleveland Cavaliers vs Toronto Raptors",
                             "Basketball", games=games)
            assert hit is not None
            assert hit["event_id"] == "fl_test_evt"
        finally:
            flashlive_feed.GAMES.update(original)

    def test_run_legacy_pairings_hydrates_games_from_fl_events(self):
        """_build_games_from_fl_events converts raw fl_event dicts
        into the shape match_game expects (via _parse_event). Empty
        input returns empty dict; well-formed input produces a game
        entry with home_phrases/away_phrases/sport populated."""
        from scripts.daily_diff import _build_games_from_fl_events
        # Empty input → empty dict.
        assert _build_games_from_fl_events([]) == {}

        # Well-formed FL raw event → one game entry with the fields
        # match_game reads.
        fl_event = {
            "EVENT_ID":       "fl_evt_1",
            "HOME_NAME":      "Cleveland Cavaliers",
            "AWAY_NAME":      "Toronto Raptors",
            "SHORTNAME_HOME": "CLE",
            "SHORTNAME_AWAY": "TOR",
            "SPORT_ID":       "2",   # SPORT_MAP entry — Basketball or similar
            "START_UTIME":    1780000000,
        }
        games = _build_games_from_fl_events([fl_event])
        assert games, "well-formed fl_event should produce at least one game"
        # Exactly one entry — one fl_event in, one game out.
        g = next(iter(games.values()))
        assert g.get("home_name") == "Cleveland Cavaliers"
        assert g.get("away_name") == "Toronto Raptors"
        # match_game reads home_phrases/away_phrases; assert populated.
        assert g.get("home_phrases"), "home_phrases must be populated"
        assert g.get("away_phrases"), "away_phrases must be populated"


class TestSumDiffDictsAggregation:
    """§4.6 tests 12-13: summed-across-sports aggregation shape."""

    def test_sum_diff_dicts_preserves_bucket_invariant(self):
        """Summed bucket counts equal sum of per-sport bucket counts AND
        equal summed total_evaluated. The invariant that holds per-sport
        must also hold in the summed shape shipping to report_json."""
        from scripts.daily_diff import _sum_diff_dicts
        per_sport = [
            {
                "agree_same_fixture": 10, "agree_partial_coverage": 2,
                "v4_only": 3, "legacy_only": 4, "both_pair_different": 1,
                "v4_extraction_excluded": 5, "total_evaluated": 25,
                "sample_disagreements": {},
            },
            {
                "agree_same_fixture": 7, "agree_partial_coverage": 0,
                "v4_only": 1, "legacy_only": 2, "both_pair_different": 0,
                "v4_extraction_excluded": 3, "total_evaluated": 13,
                "sample_disagreements": {},
            },
        ]
        summed = _sum_diff_dicts(per_sport)
        assert summed["total_evaluated"] == 38
        bucket_sum = (
            summed["agree_same_fixture"] + summed["agree_partial_coverage"]
            + summed["v4_only"] + summed["legacy_only"]
            + summed["both_pair_different"] + summed["v4_extraction_excluded"]
        )
        assert bucket_sum == summed["total_evaluated"]

    def test_sum_diff_dicts_truncates_samples(self):
        """Sum three per-sport dicts each carrying 20 samples in one
        bucket; summed dict has ≤ SAMPLE_N samples in that bucket.
        Bounds report_json size regardless of sport count."""
        from scripts.daily_diff import _sum_diff_dicts, SAMPLE_N
        per_sport = [
            {
                "agree_same_fixture": 0, "agree_partial_coverage": 0,
                "v4_only": 0, "legacy_only": 20, "both_pair_different": 0,
                "v4_extraction_excluded": 0, "total_evaluated": 20,
                "sample_disagreements": {
                    "legacy_only": [
                        {"fl_event_id": f"s{s}_r{i}", "legacy_tickers": ["X"]}
                        for i in range(20)
                    ],
                },
            }
            for s in range(3)
        ]
        summed = _sum_diff_dicts(per_sport)
        assert len(summed["sample_disagreements"]["legacy_only"]) <= SAMPLE_N
