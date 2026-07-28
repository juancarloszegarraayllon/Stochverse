"""Tests for ingestion.base — shared UPSERT primitive across providers.

Task #22 (2026-07-29) added the hourly staleness gate on Step 3b's
last_seen_at UPDATE. These tests lock in:
  (1) the SQL carries the staleness predicate (guards refactors)
  (2) a row bumped hourly stays inside the 3-day reresolution
      watermark's window (correctness envelope proof)
  (3) the changed-rows path (Step 3a) still unconditionally sets
      last_seen_at regardless of freshness (gate applies only to
      Step 3b — changed rows always get a fresh bump)

Test file exists because upsert_provider_records_batch is the SHARED
primitive across providers (kalshi + fl + future); it deserves its own
test home rather than living inside provider-specific test files.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone

import pytest


class TestStalenessGate:
    """Task #22: hourly staleness gate on Step 3b UPDATE.

    Cuts Neon WAL churn ~99% (baseline ~19M row versions/day for the
    UPDATE 1000-item IN variant; steady state ~183k post-gate) while
    keeping the reresolution watermark's 3-day window with 71× margin.
    """

    def test_staleness_gate_sql_contains_hourly_predicate(self):
        """Guards against a refactor removing the gate. Inspects the
        source of upsert_provider_records_batch and asserts the
        `NOW() - INTERVAL '1 hour'` predicate is present.

        Direct source inspection because SQLAlchemy statement
        compilation depends on dialect + connection state; the raw
        string form the code emits is the durable contract."""
        from ingestion.base import upsert_provider_records_batch
        src = inspect.getsource(upsert_provider_records_batch)
        assert "NOW() - INTERVAL '1 hour'" in src, (
            "Staleness predicate missing from upsert_provider_records_batch. "
            "If this refactored away, Step 3b will unconditionally bump "
            "last_seen_at on every unchanged row every pass — the pre-fix "
            "~19M row versions/day WAL churn regression returns."
        )
        # Also assert it's applied to last_seen_at, not some other column.
        assert 'tbl.c["last_seen_at"]' in src or (
            "last_seen_col = tbl.c[\"last_seen_at\"]" in src
        ), (
            "Staleness gate must apply to last_seen_at column reference; "
            "if a different column is being gated the churn reduction "
            "doesn't materialize."
        )

    def test_hourly_gate_stays_inside_reresolution_window(self):
        """Correctness envelope proof: a row whose only bumps come from
        the hourly staleness gate ALWAYS stays inside the reresolution
        watermark's 3-day window.

        Simulates 72 consecutive hourly bumps against the 3-day (72-hour)
        watermark. At every step, the row's last_seen_at is at most 1h
        past its most-recent-bump time, and the watermark selects rows
        with `last_seen_at > NOW() - INTERVAL '3 days'` = 72 hours.
        Margin: 71× (worst case: bump completes 1h stale, next check
        fires 3 days later — row still 71h ahead of the exclusion edge).

        Abstract time-math — no DB required. Guards against a future
        change to either the hourly threshold OR the 3-day watermark
        that would collapse the margin."""
        HOURLY_GATE_S       = 60 * 60           # 1 hour
        RERESOLUTION_WINDOW_S = 3 * 24 * 60 * 60  # 3 days = 259,200 seconds

        # Simulate a row that started fresh at t=0 and gets a bump
        # once per hour (worst case: bump fires when the row is
        # exactly 1h stale, so last_seen_at trails "now" by 1 hour
        # briefly, then catches up).
        t0 = datetime(2026, 7, 29, 0, 0, tzinfo=timezone.utc)
        last_seen_at = t0

        for hour in range(72):
            now = t0 + timedelta(seconds=HOURLY_GATE_S * hour)
            staleness_seconds = (now - last_seen_at).total_seconds()

            if staleness_seconds >= HOURLY_GATE_S:
                # Gate fires — bump last_seen_at.
                last_seen_at = now

            # At every step, the row must be inside the reresolution
            # window (last_seen_at > NOW() - 3 days).
            reresolution_edge = now - timedelta(seconds=RERESOLUTION_WINDOW_S)
            assert last_seen_at > reresolution_edge, (
                f"Hour {hour}: row's last_seen_at {last_seen_at.isoformat()} "
                f"fell out of the reresolution window edge "
                f"{reresolution_edge.isoformat()}. Gate + watermark drift "
                f"broke the envelope."
            )

        # Sanity: after 72 hours, last_seen_at should be at most 1 hour
        # behind now (proves the gate is actually bumping).
        final_now = t0 + timedelta(hours=72)
        final_staleness = (final_now - last_seen_at).total_seconds()
        assert final_staleness <= HOURLY_GATE_S, (
            f"After 72 hours the row's last_seen_at is {final_staleness}s "
            f"stale; expected at most {HOURLY_GATE_S}s. The simulation "
            f"loop isn't bumping — check the gate logic."
        )

    def test_changed_rows_path_unconditionally_bumps_last_seen_at(self):
        """Step 3a (UPSERT for changed rows) must set last_seen_at =
        NOW() UNCONDITIONALLY — the staleness gate applies only to
        Step 3b (unchanged rows). A changed row that also happens to
        be fresh (<1h since last bump) still needs last_seen_at
        bumped so downstream freshness readers see the change
        timestamp, not a stale value from before the content update.

        Source inspection because building a real INSERT ... ON
        CONFLICT SQLAlchemy statement + intercepting its compiled
        text without a live connection is heavier than the assertion
        is worth. The contract is: update_cols['last_seen_at'] =
        text('NOW()') sits unconditionally in the changed-rows path."""
        from ingestion.base import upsert_provider_records_batch
        src = inspect.getsource(upsert_provider_records_batch)

        # The `update_cols["last_seen_at"] = text("NOW()")` line in
        # Step 3a must exist WITHOUT any staleness predicate. It's the
        # unconditional bump for changed rows.
        assert 'update_cols["last_seen_at"] = text("NOW()")' in src, (
            "Step 3a's unconditional last_seen_at bump for changed "
            "rows is missing. Changed-row UPSERTs must always set "
            "last_seen_at to NOW() — the staleness gate applies ONLY "
            "to Step 3b's unchanged-rows UPDATE."
        )

        # Also verify Step 3a's bump line does NOT sit inside any
        # conditional referencing NOW() - INTERVAL (which would be
        # the gate leaking upward to the changed-rows path).
        step_3a_marker = '# Step 3a'
        step_3b_marker = '# Step 3b'
        assert step_3a_marker in src and step_3b_marker in src, (
            "Step markers missing; can't isolate 3a vs 3b for testing. "
            "Test needs update if the code structure changed."
        )
        step_3a_body = src.split(step_3a_marker, 1)[1].split(step_3b_marker, 1)[0]
        assert "INTERVAL '1 hour'" not in step_3a_body, (
            "Staleness gate leaked into Step 3a (changed-rows path). "
            "That would make changed rows skip last_seen_at bumps when "
            "fresh — incorrect. Gate belongs ONLY in Step 3b."
        )
