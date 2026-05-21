"""Phase 2 Track A Deliverable 2: on-demand markdown render of daily-diff data.

SCAFFOLD COMMIT — implementation lands in subsequent commits on this branch.

Per PR #175 §6 (Q1 resolution): hybrid output format. Structured rows
live in sp.daily_diff_reports + sp.baseline_shifts; this script renders
human-readable markdown on demand. NO committed daily reports — operator
runs this when they want to see the report.

## Usage

    # Default — last 7 days, stdout output:
    python scripts/render_daily_diff_report.py

    # Custom window:
    python scripts/render_daily_diff_report.py --days 14

    # Save to file:
    python scripts/render_daily_diff_report.py --days 7 --out report.md

## Pattern D pre-flight

Read-side script — runs the same Pattern D pre-flight as daily_diff.py
(verify-endpoint-before-read sub-pattern). Operator running this against
production needs EXPECTED_PRODUCTION_ENDPOINT set; local dev sets
DAILY_DIFF_ALLOW_NON_PRODUCTION=1.

## Output format

Markdown sections:

  ## Phase 2 Track A — Daily Diff Report
  ### Window: {start_date} → {end_date}
  ### Scope-filter version: {scope_filter_version}

  ## Headline metrics
  - Scope-filtered auto-apply rate (overall): X.XX%
  - Per-sport scope-filtered auto-apply rate: (table)
  - Personal-path vs team-path rate: (table)

  ## Trend (last N days)
  - Auto-apply rate day-over-day: (sparkline-like text)
  - Queue depth day-over-day: (sparkline-like text)

  ## Baseline-shift events
  - (list of sp.baseline_shifts entries in window)

  ## sp.resolution_log volume
  - Per-reason_code breakdown (informs §6.5 archival sizing per #164)

  ## Sample disagreements (post-Deliverable 1)
  - (only present if legacy_comparison_present=true on report rows)

  ## Raw metrics (gross, unfiltered)
  - For reference; scope-filtered is the headline.

SCAFFOLD: implementation lands in subsequent commits.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

# Make project root importable when invoked as `python scripts/...`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import async_session  # noqa: E402
from observability import get_logger  # noqa: E402

# Pattern D pre-flight shared with daily_diff.py — same module to keep
# the verify-endpoint-before-read logic in one place.
from scripts.daily_diff import _pattern_d_pre_flight  # noqa: E402


async def render(days: int = 7, out_path: str | None = None) -> int:
    """Render the daily-diff report for the last N days."""
    log = get_logger("render_daily_diff_report")

    preflight_rc = _pattern_d_pre_flight()
    if preflight_rc != 0:
        return preflight_rc

    if async_session is None:
        print("ERROR: DATABASE_URL not set or engine unavailable.",
              file=sys.stderr)
        return 1

    raise NotImplementedError(
        "render_daily_diff_report main loop — scaffold-first commit; "
        "implementation in subsequent commits on this branch."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="On-demand markdown render of daily-diff data.",
    )
    parser.add_argument(
        "--days", type=int, default=7,
        help="Window length in days (default: 7)",
    )
    parser.add_argument(
        "--out", type=str, default=None,
        help="Output file path (default: stdout)",
    )
    args = parser.parse_args(argv)

    return asyncio.run(render(days=args.days, out_path=args.out))


if __name__ == "__main__":
    sys.exit(main())
