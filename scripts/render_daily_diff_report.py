"""Phase 2 Track A Deliverable 2: on-demand markdown render of daily-diff data.

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
production needs EXPECTED_PRODUCTION_DB_NAME + EXPECTED_PRODUCTION_DB_HOST
set; local dev sets DAILY_DIFF_ALLOW_NON_PRODUCTION=1.

## Exit codes

  0 — success
  1 — DATABASE_URL not set
  2 — bad CLI args
  3 — Pattern D pre-flight failed
  5 — no rows in window
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

# Make project root importable when invoked as `python scripts/...`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from db import async_session  # noqa: E402
from observability import get_logger  # noqa: E402

# Pattern D pre-flight shared with daily_diff.py — same module to keep
# the verify-endpoint-before-read logic in one place.
from scripts.daily_diff import _pattern_d_pre_flight  # noqa: E402


DEFAULT_WINDOW_DAYS = 7


def render_markdown(
    report_rows: list[dict],
    shift_rows: list[dict],
    *,
    window_days: int,
    now: datetime,
) -> str:
    """Pure function: format rows as markdown.

    report_rows: dicts shaped per sp.daily_diff_reports columns.
    shift_rows:  dicts shaped per sp.baseline_shifts columns.

    Both ordered newest-first. Caller writes the returned string to
    stdout / file.

    D2-only vs D1+D2 distinction:
      - legacy_comparison_present=false → omit "Sample disagreements"
      - legacy_comparison_present=true  → include it (placeholder until
        Deliverable 1 lands)
    """
    out: list[str] = []
    out.append(f"# Daily-diff report (window: {window_days} days)")
    out.append("")
    out.append(f"Generated: {now.isoformat()}")
    out.append("")

    # ── Window summary ──
    out.append("## Window summary")
    out.append("")
    if not report_rows:
        out.append("_No reports in window._")
        out.append("")
    else:
        out.append(
            "| Report date | Records | Matcher capability "
            "(scope-filtered) | Matcher capability (unfiltered) "
            "| Team-path | Personal-path | Scope filter version | Mode |"
        )
        out.append("|---|---:|---:|---:|---:|---:|---|---|")
        for r in report_rows:
            metrics = r.get("metrics") or {}
            scope = metrics.get("scope_filtered", {})
            raw = metrics.get("raw", {})
            mode = "D1+D2" if r.get("legacy_comparison_present") else "D2-only"
            # v0.2.0 keys with v0.1.0 fallback so historical rows still
            # render. SCOPE_FILTER_VERSION version log documents the
            # rename. Pre-v0.2.0 rows used auto_apply_rate_* names.
            scoped_rate = scope.get(
                "matcher_capability_rate_overall",
                scope.get("auto_apply_rate_overall", 0.0),
            )
            unfiltered_rate = raw.get(
                "matcher_capability_rate_overall_unfiltered",
                raw.get("auto_apply_rate_overall_unfiltered", 0.0),
            )
            team_rate = scope.get("team_path_rate", 0.0)
            personal_rate = scope.get("personal_path_rate", 0.0)
            out.append(
                f"| {r['report_date']} "
                f"| {r['total_records_scanned']} "
                f"| {scoped_rate:.1%} "
                f"| {unfiltered_rate:.1%} "
                f"| {team_rate:.1%} "
                f"| {personal_rate:.1%} "
                f"| {r.get('scope_filter_version', '?')} "
                f"| {mode} |"
            )
        out.append("")

    # ── Per-sport matcher-capability rates (latest only) ──
    out.append("## Per-sport matcher-capability rates (latest)")
    out.append("")
    if not report_rows:
        out.append("_No data._")
        out.append("")
    else:
        latest = report_rows[0]
        scope_latest = (latest.get("metrics") or {}).get("scope_filtered", {})
        # v0.2.0 key with v0.1.0 fallback for historical rows.
        per_sport = scope_latest.get(
            "matcher_capability_rate_per_sport",
            scope_latest.get("auto_apply_rate_per_sport", {}),
        )
        if not per_sport:
            out.append("_No per-sport data in latest report._")
            out.append("")
        else:
            out.append("| Sport | Matcher capability |")
            out.append("|---|---:|")
            for sport, rate in sorted(per_sport.items()):
                label = sport or "(empty sport tag)"
                out.append(f"| {label} | {rate:.1%} |")
            out.append("")

    # ── Personal vs team path (latest) ──
    if report_rows:
        latest_scope = (report_rows[0].get("metrics") or {}).get(
            "scope_filtered", {}
        )
        personal = latest_scope.get("personal_path_rate")
        team = latest_scope.get("team_path_rate")
        if personal is not None or team is not None:
            out.append("## Personal vs team path (latest)")
            out.append("")
            out.append("| Path | Matcher capability |")
            out.append("|---|---:|")
            out.append(f"| Personal (tennis/mma/boxing/golf/snooker/darts) "
                       f"| {personal or 0.0:.1%} |")
            out.append(f"| Team | {team or 0.0:.1%} |")
            out.append("")

    # ── Confidence histogram (latest) ──
    out.append("## Confidence histogram (latest)")
    out.append("")
    if report_rows:
        latest = report_rows[0]
        rj = latest.get("report_json") or {}
        histogram = rj.get("confidence_histogram") or {}
        if histogram:
            out.append("| Bucket | Count |")
            out.append("|---|---:|")
            for bucket, count in histogram.items():
                out.append(f"| {bucket} | {count} |")
            out.append("")
        else:
            out.append("_No histogram data in latest report._")
            out.append("")
    else:
        out.append("_No data._")
        out.append("")

    # ── resolution_log volume (latest) ──
    if report_rows:
        latest_metrics = report_rows[0].get("metrics") or {}
        log_vol = latest_metrics.get("resolution_log_volume_per_cron") or {}
        if log_vol:
            out.append("## sp.resolution_log volume (latest cron)")
            out.append("")
            out.append(f"Total rows written: **{log_vol.get('total', 0)}**")
            out.append("")
            by_rc = log_vol.get("by_reason_code") or {}
            if by_rc:
                out.append("| Reason code | Count |")
                out.append("|---|---:|")
                for rc, count in sorted(by_rc.items()):
                    label = rc or "(empty)"
                    out.append(f"| {label} | {count} |")
                out.append("")

    # ── Baseline-shift events ──
    out.append("## Baseline-shift events")
    out.append("")
    if not shift_rows:
        out.append("_No baseline-shift events in window._")
        out.append("")
    else:
        out.append(
            "| Date | Event type | Affected population "
            "| Expected delta | Created by | Notes |"
        )
        out.append("|---|---|---|---|---|---|")
        for s in shift_rows:
            out.append(
                f"| {s['event_date']} "
                f"| {s['event_type']} "
                f"| {s['affected_population']} "
                f"| {s.get('expected_metric_delta') or '—'} "
                f"| {s.get('created_by') or '—'} "
                f"| {s.get('notes') or '—'} |"
            )
        out.append("")

    # ── Sample disagreements (D1+D2 only) ──
    has_legacy = any(r.get("legacy_comparison_present") for r in report_rows)
    if has_legacy:
        out.append("## Sample disagreements (D1+D2)")
        out.append("")
        out.append(
            "_Deliverable 1 legacy-vs-new comparison data wired through. "
            "Sample disagreements stored in report_json.sample_disagreements; "
            "render TBD._"
        )
        out.append("")
    # D2-only reports intentionally omit this section.

    return "\n".join(out)


async def _fetch_window(
    *, window_days: int, now: datetime,
) -> tuple[list[dict], list[dict]]:
    """Fetch sp.daily_diff_reports + sp.baseline_shifts for the window.

    Returns (report_rows, shift_rows), both newest-first.
    """
    if async_session is None:
        raise RuntimeError("async_session unavailable; DATABASE_URL not set.")

    cutoff = (now - timedelta(days=window_days)).date()

    async with async_session() as session:
        reports = (await session.execute(text(
            "SELECT report_date, window_start, window_end, "
            "       total_records_scanned, metrics, scope_filter_version, "
            "       report_json, legacy_comparison_present, created_at "
            "FROM sp.daily_diff_reports "
            "WHERE report_date >= :cutoff "
            "ORDER BY report_date DESC"
        ), {"cutoff": cutoff})).mappings().all()

        shifts = (await session.execute(text(
            "SELECT event_type, event_date, affected_population, "
            "       expected_metric_delta, notes, created_by, created_at "
            "FROM sp.baseline_shifts "
            "WHERE event_date >= :cutoff "
            "ORDER BY event_date DESC"
        ), {"cutoff": cutoff})).mappings().all()

    return [dict(r) for r in reports], [dict(s) for s in shifts]


async def render(days: int = DEFAULT_WINDOW_DAYS, out_path: str | None = None) -> int:
    """Render the daily-diff report for the last N days."""
    log = get_logger("render_daily_diff_report")

    preflight_rc = await _pattern_d_pre_flight()
    if preflight_rc != 0:
        return preflight_rc

    if async_session is None:
        print("ERROR: DATABASE_URL not set or engine unavailable.",
              file=sys.stderr)
        return 1

    now = datetime.now(timezone.utc)
    reports, shifts = await _fetch_window(window_days=days, now=now)

    if not reports and not shifts:
        print(
            f"WARN: No reports or baseline shifts in last {days} days.",
            file=sys.stderr,
        )
        log.warning("render_daily_diff_report.empty_window", days=days)
        return 5

    output = render_markdown(reports, shifts, window_days=days, now=now)
    if out_path:
        with open(out_path, "w") as f:
            f.write(output)
            f.write("\n")
        log.info(
            "render_daily_diff_report.written",
            path=out_path, days=days, reports=len(reports), shifts=len(shifts),
        )
    else:
        print(output)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="On-demand markdown render of daily-diff data.",
    )
    parser.add_argument(
        "--days", type=int, default=DEFAULT_WINDOW_DAYS,
        help=f"Window length in days (default: {DEFAULT_WINDOW_DAYS})",
    )
    parser.add_argument(
        "--out", type=str, default=None,
        help="Output file path (default: stdout)",
    )
    args = parser.parse_args(argv)

    if args.days < 1:
        print("ERROR: --days must be >= 1.", file=sys.stderr)
        return 2

    return asyncio.run(render(days=args.days, out_path=args.out))


if __name__ == "__main__":
    sys.exit(main())
