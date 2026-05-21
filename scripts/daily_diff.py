"""Phase 2 Track A Deliverable 2: daily-diff measurement script.

SCAFFOLD COMMIT — implementation lands in subsequent commits on this branch.

Per PR #175's scope doc, this script:

  1. Verifies connection endpoint matches production (Pattern D
     pre-flight, per the read-path sub-pattern added 2026-05-20).
  2. Pulls last 24h of records from sp.kalshi_markets + sp.fl_events
     (fresh reads, not joins against existing sp.resolution_log per
     scope doc §4).
  3. Runs the resolver's TieredMatcher against each record.
  4. Classifies outcomes per scope doc §7 measurement targets.
  5. Writes one row to sp.daily_diff_reports.
  6. (Deliverable 1, future): also runs the legacy Tier 1-4 resolver
     for AGREE/disagree comparison.

Per scope doc §9, scheduled at 02:30 UTC via Railway cron — 15-min
buffer after the existing Kalshi cron at 02:15 UTC.

Per scope doc §14, ~6-10 week useful life, deprecated post-Phase-3
cutover. Throw-away infrastructure per architecture doc §11.5.

## Usage

    # Production cron invocation (Railway):
    python scripts/daily_diff.py

    # Local dev invocation against a non-production endpoint:
    DAILY_DIFF_ALLOW_NON_PRODUCTION=1 python scripts/daily_diff.py

    # Local dev with explicit window override:
    python scripts/daily_diff.py --window-start "2026-05-20 00:00:00+00" \\
                                  --window-end   "2026-05-21 00:00:00+00"

## Exit codes

  0 — success (report written)
  1 — DATABASE_URL not set or engine unavailable
  2 — bad CLI args
  3 — Pattern D pre-flight failed (connection endpoint mismatch)
  4 — already-ran today (idempotency: unique constraint on report_date)
  5 — no records in window (cron fired but ingestion didn't supply data)

## Pattern D pre-flight (sub-pattern: verify-endpoint-before-READ)

The script reads production data for measurement purposes. Per Pattern
D's read-path sub-pattern (docs/bootstraps/kbl-2025-26.md), the pre-
flight check at script start:

  - SELECT current_database(), current_schema(), inet_server_addr();
  - Compares inet_server_addr() against EXPECTED_PRODUCTION_ENDPOINT
    env var.
  - Exits cleanly (exit code 3) with error message if mismatch.
  - DAILY_DIFF_ALLOW_NON_PRODUCTION=1 env override allows local dev
    runs against dev branches (e.g., for testing). Production cron
    has this UNSET; local testers set it explicitly.

Rationale: measurement against the wrong DB produces wrong baselines
that drift undetected. Same cost-asymmetry as Pattern D's write-path
version: 5-second pre-flight prevents hours-to-days of misdirection.

See PR #175 §10 for the full Pattern D framing.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from datetime import datetime, timedelta, timezone

# Make project root importable when invoked as `python scripts/...`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from db import async_session  # noqa: E402
from observability import get_logger  # noqa: E402


# ── Scope-filter version stamp ─────────────────────────────────


# Bumped when scope-filter rules change (NON_SPORT filter rule per
# Issue #174, prop-market vocabulary additions per Issue #160, etc.).
# Stamped into sp.daily_diff_reports.scope_filter_version so historical
# reports can be re-interpreted post-rule-change.
SCOPE_FILTER_VERSION = "v0.1.0"


# ── Pattern D pre-flight ───────────────────────────────────────


def _pattern_d_pre_flight() -> int:
    """Verify connection endpoint matches expected production endpoint.

    Returns 0 on success, 3 on mismatch. Operator runs production cron
    with EXPECTED_PRODUCTION_ENDPOINT set; local dev uses
    DAILY_DIFF_ALLOW_NON_PRODUCTION=1 to bypass.

    SCAFFOLD: implementation lands in subsequent commit.
    """
    raise NotImplementedError("Pattern D pre-flight — pending implementation")


# ── Measurement targets (per PR #175 §7) ───────────────────────


async def _measure(window_start: datetime, window_end: datetime) -> dict:
    """Run the measurement pass against the 24h window.

    Returns a dict matching the sp.daily_diff_reports.metrics JSONB
    column shape. Schema:

    {
      "scope_filtered": {
        "auto_apply_rate_overall": float,
        "auto_apply_rate_per_sport": {sport: float, ...},
        "per_tier_rate_per_sport": {
          sport: {
            "strict": int, "alias": int, "fuzzy": int,
            "no_match": int, "review_queue": int, "crash": int,
          },
          ...
        },
        "personal_path_rate": float,
        "team_path_rate": float,
      },
      "raw": {
        "auto_apply_rate_overall_unfiltered": float,
        "signal_extraction_skipped": int,
        "non_sport_filtered_out": int,
        "prop_market_filtered_out": int,
      },
      "queue": {
        "depth_per_sport": {sport: int, ...},
        "median_time_in_queue_per_sport": {sport: float, ...},
        "p95_time_in_queue_per_sport": {sport: float, ...},
        "abandonment_rate_per_sport": {sport: float, ...},
      },
      "resolution_log_volume_per_cron": {
        "by_reason_code": {reason_code: int, ...},
        "total": int,
      },
    }

    The eight measurement targets from PR #175 §7 + the
    resolution_log row-volume target added post-Finding X.

    SCAFFOLD: implementation lands in subsequent commit.
    """
    raise NotImplementedError("Measurement pass — pending implementation")


async def _write_report(
    window_start: datetime,
    window_end: datetime,
    metrics: dict,
    total_records: int,
) -> None:
    """Write one row to sp.daily_diff_reports.

    Unique constraint on report_date enforces idempotency — re-running
    on the same day fails fast with exit code 4 rather than producing
    duplicate rows.

    SCAFFOLD: implementation lands in subsequent commit.
    """
    raise NotImplementedError("Report write — pending implementation")


# ── Entry point ────────────────────────────────────────────────


async def daily_diff(
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> int:
    """Run the daily-diff measurement pass. Returns process exit code."""
    log = get_logger("daily_diff")
    started = time.monotonic()

    # Pattern D pre-flight first — any failure here exits before
    # touching production data.
    preflight_rc = _pattern_d_pre_flight()
    if preflight_rc != 0:
        return preflight_rc

    if async_session is None:
        print("ERROR: DATABASE_URL not set or engine unavailable.",
              file=sys.stderr)
        return 1

    # Default window: last 24 hours ending at script start.
    if window_end is None:
        window_end = datetime.now(timezone.utc)
    if window_start is None:
        window_start = window_end - timedelta(hours=24)

    log.info(
        "daily_diff.start",
        window_start=window_start.isoformat(),
        window_end=window_end.isoformat(),
        scope_filter_version=SCOPE_FILTER_VERSION,
    )

    # Measurement + write — implementation in subsequent commits.
    raise NotImplementedError(
        "daily_diff main loop — scaffold-first commit; "
        "implementation in subsequent commits on this branch."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Phase 2 Track A daily-diff measurement script.",
    )
    parser.add_argument(
        "--window-start", type=str, default=None,
        help="ISO 8601 window start (default: 24h before --window-end)",
    )
    parser.add_argument(
        "--window-end", type=str, default=None,
        help="ISO 8601 window end (default: now, UTC)",
    )
    args = parser.parse_args(argv)

    window_start = (
        datetime.fromisoformat(args.window_start)
        if args.window_start else None
    )
    window_end = (
        datetime.fromisoformat(args.window_end)
        if args.window_end else None
    )

    return asyncio.run(daily_diff(
        window_start=window_start,
        window_end=window_end,
    ))


if __name__ == "__main__":
    sys.exit(main())
