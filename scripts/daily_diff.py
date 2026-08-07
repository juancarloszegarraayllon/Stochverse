"""Phase 2 Track A Deliverable 2: daily-diff measurement script.

Per PR #175's scope doc, this script:

  1. Verifies connection endpoint matches production (Pattern D
     pre-flight, per the read-path sub-pattern added 2026-05-20).
  2. Pulls last 24h of records from sp.kalshi_markets + sp.fl_events
     (fresh reads, not joins against existing sp.resolution_log per
     scope doc §4).
  3. Classifies each record via scope-filter rules (NON_SPORT,
     prop-market vocabulary per Issue #160, head-to-head).
  4. Runs the resolver's TieredMatcher against in-scope records.
  5. Aggregates outcomes per scope doc §7 measurement targets.
  6. Writes one row to sp.daily_diff_reports.
  7. (Deliverable 1, future): also runs the legacy Tier 1-4 resolver
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

## Scope-filter classification (per PR #175 §7, Issues #160 + #174)

Records are classified pre-parser into four buckets:

  - HEAD_TO_HEAD — in scope; counted in scope_filtered denominator
    + further processed by TieredMatcher
  - NON_SPORT — empty _sport field on Kalshi (Issue #174); filtered
    out of scope_filtered denominator
  - PROP_MARKET — Kalshi market title carries a prop-market segment
    per the KALSHI_PROP_MARKET_SEGMENTS vocabulary (Issue #160);
    filtered out
  - SIGNAL_EXTRACTION_SKIPPED — record passed scope filter, but
    parser failed to produce a FixtureSignal. Counted separately
    in raw.signal_extraction_skipped (NOT in scope_filtered
    denominator).

Scope-filter logic is pure-function on the raw record. Determined
at pre-parser stage. SIGNAL_EXTRACTION_SKIPPED is layered on later
during parser-run phase.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Iterable
from urllib.parse import urlparse

# Make project root importable when invoked as `python scripts/...`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from db import async_session  # noqa: E402
from observability import get_logger  # noqa: E402
from resolver.alias_tier import INDIVIDUAL_SPORT_CODES  # noqa: E402
from resolver.fuzzy_tier.matcher import KALSHI_PROP_MARKET_SEGMENTS  # noqa: E402
from resolver.types import ReasonCode  # noqa: E402


# ── Scope-filter version stamp ─────────────────────────────────


# Bumped when scope-filter rules change (NON_SPORT filter rule per
# Issue #174, prop-market vocabulary additions per Issue #160, etc.)
# AND when the metrics-JSONB schema changes shape (key renames, new
# top-level fields, etc.). Stamped into sp.daily_diff_reports.scope_filter_version
# so historical readers can interpret old rows against the schema that
# wrote them.
#
# Version log:
#   v0.1.0 — initial schema (2026-05-21, commit 775a55a)
#   v0.2.0 — rename auto_apply_rate_* → matcher_capability_rate_*
#            (2026-05-21 post-empirical-validation). The metric measures
#            what the matcher COULD auto-apply given today's records,
#            distinct from the production cron's INCREMENTAL apply rate
#            (newly-resolved records only). Per-record reason_codes
#            (strict/alias/fuzzy as "auto-apply tiers") are unchanged —
#            only the aggregate metric names move. v0.1.0 rows retain
#            their original key names; readers
#            dispatch on the version stamp.
#   v0.3.0 — Golf prop-market vocabulary added to
#            KALSHI_PROP_MARKET_SEGMENTS per Day-22 survey finding
#            (resolver/fuzzy_tier/matcher.py). Records previously
#            counted in raw.signal_extraction_skipped (~1,371/day per
#            Day-21 measurement) shift to raw.prop_market_filtered_out;
#            the scope-filtered denominator tightens, headline
#            matcher-capability rate rises a few percentage points.
#            Baseline-shift event annotated in sp.baseline_shifts at
#            ship time so the rate jump is operator-attributable.
SCOPE_FILTER_VERSION = "v0.3.0"


# ── Scope-filter classification constants ──────────────────────


class ScopeClassification:
    """Classification labels for the scope-filter pre-parser stage.

    Module-level constants pinned by the test suite. Don't rename
    without updating the metrics shape in sp.daily_diff_reports.metrics
    and the render script's section labels.
    """

    HEAD_TO_HEAD = "head_to_head"
    NON_SPORT = "non_sport_filtered_out"
    PROP_MARKET = "prop_market_filtered_out"
    # Layered on later; not returned by pre-parser classify_record().
    # Listed here as the canonical constant for downstream aggregation
    # to reference.
    SIGNAL_EXTRACTION_SKIPPED = "signal_extraction_skipped"


def _looks_like_prop_market_title(title: str) -> bool:
    """Detect Kalshi prop-market titles via the rpartition-after-colon
    heuristic against KALSHI_PROP_MARKET_SEGMENTS.

    Distinct from resolver/fuzzy_tier/matcher.py:_looks_like_kalshi_prop_market,
    which operates on individual parsed names AFTER the title is split
    into home/away. This operates on the raw market title pre-parser.

    Examples:
      "Colorado Rockies vs Arizona Diamondbacks: Hits"
          rpartition(':')[2].strip() = "Hits" → in vocab → True

      "Anaheim vs Game 3: Vegas"  (NHL playoff-series record)
          rpartition(':')[2].strip() = "Vegas" → not in vocab → False

      "Manchester United vs Chelsea"  (no colon)
          → False

    Fail-open: titles whose suffix-after-colon isn't in the vocabulary
    flow through as head-to-head. Per Issue #160, new prop types reach
    operators rather than getting silently filtered.
    """
    if not title or ':' not in title:
        return False
    _, _, suffix = title.rpartition(':')
    return suffix.strip() in KALSHI_PROP_MARKET_SEGMENTS


def classify_kalshi_record(record: dict) -> str:
    """Classify a Kalshi record for scope-filter purposes.

    Inspects the raw_payload's _sport field + market title. Returns
    one of ScopeClassification.{NON_SPORT, PROP_MARKET, HEAD_TO_HEAD}.

    Pure function — no DB calls, no parser invocation. Deterministic
    on the input record.

    Args:
        record: dict-like with `raw_payload` key (JSONB content as
            dict). Minimum shape:
              {"raw_payload": {"_sport": str, "title": str, ...}}

    Returns:
        Classification label string. Caller stores this in the
        metrics aggregation per scope_filter_version.

    Order of checks:
      1. NON_SPORT (empty _sport) — fastest discriminator
      2. PROP_MARKET (vocabulary match on title) — string check
      3. HEAD_TO_HEAD (default, in-scope)
    """
    raw = record.get("raw_payload") or {}
    sport = (raw.get("_sport") or "").strip()
    if not sport:
        return ScopeClassification.NON_SPORT
    title = raw.get("title") or ""
    if _looks_like_prop_market_title(title):
        return ScopeClassification.PROP_MARKET
    return ScopeClassification.HEAD_TO_HEAD


def classify_fl_record(record: dict) -> str:
    """Classify a FL record for scope-filter purposes.

    FL ingestion (per ingestion/fl.py) only writes sport events to
    sp.fl_events; NON_SPORT filtering isn't needed. FL doesn't carry
    Kalshi-style prop markets, so PROP_MARKET filtering doesn't apply.

    All FL records pass scope filter as HEAD_TO_HEAD. Future FL out-of-
    scope categories (e.g., bench-clearing-brawl prop markets if FL
    ever ingests those) would extend this function.
    """
    return ScopeClassification.HEAD_TO_HEAD


def classify_record(provider: str, record: dict) -> str:
    """Dispatch to provider-specific scope-filter classifier.

    Single entry point for the measurement pass's classification
    step. Each record from sp.kalshi_markets / sp.fl_events runs
    through this function before reaching the parser stage.

    SIGNAL_EXTRACTION_SKIPPED is NOT returned by this function —
    that classification is determined later, when the parser runs
    against records that passed scope filter as HEAD_TO_HEAD.
    """
    if provider == "kalshi":
        return classify_kalshi_record(record)
    if provider == "fl":
        return classify_fl_record(record)
    raise ValueError(
        f"Unknown provider {provider!r}; expected 'kalshi' or 'fl'."
    )


# ── Per-sport metric aggregation (pure functions) ──────────────


# Reason codes that count as auto-apply (confidence >= threshold).
# Mirrors the runner's auto_apply branch — kept here as a frozenset for
# fast membership tests in the aggregation hot loop. CORROBORATION is
# included for completeness even though Phase 2 doesn't emit it yet;
# Deliverable 1's cross-provider pass will.
_AUTO_APPLY_REASON_CODES: frozenset[str] = frozenset({
    ReasonCode.STRICT.value,
    ReasonCode.ALIAS.value,
    ReasonCode.FUZZY.value,
    ReasonCode.CORROBORATION.value,
})


# Per-tier buckets stamped into metrics.scope_filtered.per_tier_rate_per_sport.
# 'crash' is NOT a ReasonCode enum member — the measurement script tags
# rows whose matcher invocation raised with this synthetic value, so the
# crash-rate-per-sport target from PR #175 §7 is observable.
PER_TIER_BUCKETS: tuple[str, ...] = (
    "strict", "alias", "fuzzy",
    "no_match", "review_queue", "crash",
)


# Default abandonment threshold per scope doc §7. Configurable via
# aggregate_queue_metrics(abandonment_days=...).
DEFAULT_ABANDONMENT_DAYS: int = 14


# Confidence-score histogram bucket boundaries.
#
# Aligned to the resolver's auto_apply (0.85) and review_queue (0.70)
# thresholds so the boundary records are visible in their own buckets:
#   [0.00, 0.50)  — clear miss territory
#   [0.50, 0.70)  — near-miss but below review_queue floor
#   [0.70, 0.85)  — currently routed to review_queue
#   [0.85, 0.95)  — auto-applied, low-confidence end
#   [0.95, 1.00]  — auto-applied, high-confidence (strict + corroboration)
#
# Bucket labels match the lower bound. The render script + threshold-
# calibration analysis read these labels directly; renaming requires a
# scope_filter_version bump.
CONFIDENCE_BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("0.00-0.50", 0.00, 0.50),
    ("0.50-0.70", 0.50, 0.70),
    ("0.70-0.85", 0.70, 0.85),  # review_queue band
    ("0.85-0.95", 0.85, 0.95),  # auto-apply low-confidence band
    ("0.95-1.00", 0.95, 1.00),  # auto-apply high-confidence band
)


def compute_confidence_histogram(scores: Iterable[float]) -> dict[str, int]:
    """Bin a sequence of confidence scores into the CONFIDENCE_BUCKETS.

    Pure function. Returns a dict of {bucket_label: count}. All buckets
    appear in the result even when their count is zero — the render
    script renders a complete profile rather than a sparse one.

    Scores outside [0, 1] are silently clamped to the bucket boundary
    (defensive against floating-point drift); a score exactly 1.0 lands
    in the final bucket via the half-open lower-bound semantic.
    """
    counts: dict[str, int] = {label: 0 for label, _, _ in CONFIDENCE_BUCKETS}
    for raw_score in scores:
        score = max(0.0, min(1.0, float(raw_score)))
        for label, lo, hi in CONFIDENCE_BUCKETS:
            # Final bucket is closed on the right; others half-open.
            if label == CONFIDENCE_BUCKETS[-1][0]:
                if lo <= score <= hi:
                    counts[label] += 1
                    break
            elif lo <= score < hi:
                counts[label] += 1
                break
    return counts


def _safe_rate(numerator: int, denominator: int) -> float:
    """Division that returns 0.0 when the denominator is zero.

    Per-sport buckets can legitimately be empty (no Tennis records in
    today's window, etc.) — that should report 0.0, not ZeroDivisionError.
    """
    return numerator / denominator if denominator else 0.0


def _percentile(values: list[float], p: float) -> float:
    """Nearest-rank percentile for small samples.

    Python's statistics.quantiles assumes ≥2 samples; queue sizes per
    sport are often 1-5 in dev, so we use nearest-rank to avoid raising
    on edge cases. Returns 0.0 for empty input.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, int(round(p * (len(ordered) - 1)))))
    return ordered[idx]


def aggregate_per_sport_metrics(rows: Iterable[dict]) -> dict:
    """Aggregate resolution_log-like rows into per-sport metrics.

    Pure function: takes an iterable of dicts shaped
        {"reason_code": str, "reason_detail": {"sport": str, ...}}
    and returns the scope_filtered sub-section of metrics.

    Crashes: rows tagged with reason_code='crash' contribute to the
    'crash' bucket in per_tier_rate_per_sport but NOT to auto-apply
    (a crash isn't an applied resolution).

    Empty / missing sport in reason_detail bucketed under the literal
    '' key — surfacing this is intentional. Pre-Phase-3 some legacy
    rows have no sport tag; the render script highlights '' so the
    population stays visible until it's eliminated upstream.
    """
    per_sport_tiers: dict[str, dict[str, int]] = defaultdict(
        lambda: {b: 0 for b in PER_TIER_BUCKETS}
    )
    per_sport_total: dict[str, int] = defaultdict(int)
    per_sport_auto_apply: dict[str, int] = defaultdict(int)

    total = 0
    auto_apply_total = 0
    personal_total = 0
    personal_auto_apply = 0
    team_total = 0
    team_auto_apply = 0

    for row in rows:
        rc = (row.get("reason_code") or "").strip()
        detail = row.get("reason_detail") or {}
        sport = (detail.get("sport") or "").strip()

        total += 1
        per_sport_total[sport] += 1
        if rc in PER_TIER_BUCKETS:
            per_sport_tiers[sport][rc] += 1

        is_auto_apply = rc in _AUTO_APPLY_REASON_CODES
        if is_auto_apply:
            auto_apply_total += 1
            per_sport_auto_apply[sport] += 1

        # Personal-path = INDIVIDUAL_SPORT_CODES membership
        # (tennis/mma/boxing/golf/snooker/darts). Empty sport falls
        # into team-path by default.
        is_personal = sport.lower() in INDIVIDUAL_SPORT_CODES
        if is_personal:
            personal_total += 1
            if is_auto_apply:
                personal_auto_apply += 1
        else:
            team_total += 1
            if is_auto_apply:
                team_auto_apply += 1

    return {
        # Renamed from auto_apply_rate_* in v0.2.0 — see SCOPE_FILTER_VERSION
        # version log. The METRIC name now disambiguates from the
        # production cron's incremental_apply_rate (newly-resolved records
        # per pass). The underlying reason_codes (strict/alias/fuzzy as
        # auto-apply tiers) are unchanged.
        "matcher_capability_rate_overall": _safe_rate(auto_apply_total, total),
        "matcher_capability_rate_per_sport": {
            sport: _safe_rate(
                per_sport_auto_apply[sport], per_sport_total[sport]
            )
            for sport in per_sport_total
        },
        "per_tier_rate_per_sport": {
            sport: dict(per_sport_tiers[sport]) for sport in per_sport_total
        },
        "personal_path_rate": _safe_rate(personal_auto_apply, personal_total),
        "team_path_rate": _safe_rate(team_auto_apply, team_total),
    }


def aggregate_queue_metrics(
    rows: Iterable[dict],
    *,
    now: datetime,
    abandonment_days: int = DEFAULT_ABANDONMENT_DAYS,
) -> dict:
    """Aggregate review_queue-like rows into per-sport queue metrics.

    Input rows: dicts shaped
        {"sport": str, "created_at": datetime, "status": str}
    Only rows with status='pending' contribute. Other statuses
    ('approved', 'rejected') represent terminal queue exits and are
    not queue depth.

    All time values are in seconds (float). Median + p95 computed via
    nearest-rank to avoid raising on single-element sport buckets.

    abandonment_days: pending rows aging beyond this threshold count
    toward abandonment_rate_per_sport. Default 14 per scope doc §7.
    """
    per_sport_ages: dict[str, list[float]] = defaultdict(list)
    per_sport_depth: dict[str, int] = defaultdict(int)
    per_sport_abandoned: dict[str, int] = defaultdict(int)

    abandon_threshold = timedelta(days=abandonment_days)

    for row in rows:
        if row.get("status") != "pending":
            continue
        sport = (row.get("sport") or "").strip()
        created_at = row["created_at"]
        age = now - created_at
        per_sport_depth[sport] += 1
        per_sport_ages[sport].append(age.total_seconds())
        if age >= abandon_threshold:
            per_sport_abandoned[sport] += 1

    return {
        "depth_per_sport": dict(per_sport_depth),
        "median_time_in_queue_per_sport": {
            sport: float(median(ages)) if ages else 0.0
            for sport, ages in per_sport_ages.items()
        },
        "p95_time_in_queue_per_sport": {
            sport: _percentile(ages, 0.95)
            for sport, ages in per_sport_ages.items()
        },
        "abandonment_rate_per_sport": {
            sport: _safe_rate(
                per_sport_abandoned[sport], per_sport_depth[sport]
            )
            for sport in per_sport_depth
        },
    }


def aggregate_resolution_log_volume(rows: Iterable[dict]) -> dict:
    """Count resolution_log rows per reason_code + overall total.

    Per Finding X (2026-05-20): cron re-processes pending records daily
    across all 3 tiers, producing high retry traffic. This aggregation
    surfaces the per-reason-code mix so §6.5 archival sizing (Issue #164)
    has empirical inputs.

    Input rows: {"reason_code": str}. Empty / missing reason_code
    bucketed under '' (parallel to per-sport handling — surface the
    null population).
    """
    by_reason_code: dict[str, int] = defaultdict(int)
    total = 0
    for row in rows:
        rc = (row.get("reason_code") or "").strip()
        by_reason_code[rc] += 1
        total += 1
    return {
        "by_reason_code": dict(by_reason_code),
        "total": total,
    }


# ── Pattern D pre-flight ───────────────────────────────────────


# Pattern D pre-flight design (refined 2026-05-21):
#
# Original scope doc proposal used inet_server_addr() as the endpoint
# signal. Empirically (operator pre-flight against production)
# inet_server_addr() returns 169.254.254.254 on Neon — the link-local
# proxy address, not a meaningful branch discriminator. The real
# discriminator is the DATABASE_URL hostname (e.g.,
# ep-fragrant-frog-ak3esp11.us-east-2.aws.neon.tech).
#
# Refined check:
#   1. SELECT current_database() — must equal EXPECTED_PRODUCTION_DB_NAME
#      (default 'neondb'). Catches accidentally running against a
#      non-Neon DB.
#   2. DATABASE_URL hostname must contain EXPECTED_PRODUCTION_DB_HOST
#      substring (e.g., the branch endpoint ID). Catches accidentally
#      running against a dev branch of the same Neon project.
#
# Both env vars must be set for the check to run. If either is unset,
# pre-flight fails closed (operator must opt out via
# DAILY_DIFF_ALLOW_NON_PRODUCTION=1).
PATTERN_D_DEFAULT_DB_NAME = "neondb"


def _check_pattern_d_endpoint(
    database_url: str | None,
    current_database_value: str,
    *,
    expected_db_name: str | None,
    expected_db_host: str | None,
    allow_non_production: bool,
) -> tuple[int, str]:
    """Pure-function core of Pattern D pre-flight.

    Returns (exit_code, message) — 0 + "ok" on pass, 3 + reason on
    fail. Factored out so tests don't need a live DB connection.

    Args:
        database_url: the actual DATABASE_URL env var value (or None
            if unset; that's an exit-3 condition on its own unless
            the override is set).
        current_database_value: result of `SELECT current_database();`
            against the connected DB.
        expected_db_name: EXPECTED_PRODUCTION_DB_NAME env var value.
        expected_db_host: EXPECTED_PRODUCTION_DB_HOST env var value
            (hostname substring, e.g. 'ep-fragrant-frog-ak3esp11').
        allow_non_production: DAILY_DIFF_ALLOW_NON_PRODUCTION truthy.

    Local-dev opt-out (allow_non_production=True) short-circuits to
    success regardless of the other inputs. Production cron sets
    EXPECTED_PRODUCTION_DB_NAME + EXPECTED_PRODUCTION_DB_HOST and
    leaves DAILY_DIFF_ALLOW_NON_PRODUCTION unset.
    """
    if allow_non_production:
        return 0, (
            "Pattern D pre-flight bypassed via "
            "DAILY_DIFF_ALLOW_NON_PRODUCTION=1 (local-dev mode)."
        )

    if not expected_db_name or not expected_db_host:
        return 3, (
            "Pattern D pre-flight: EXPECTED_PRODUCTION_DB_NAME and/or "
            "EXPECTED_PRODUCTION_DB_HOST not set. Production cron must "
            "configure both; set DAILY_DIFF_ALLOW_NON_PRODUCTION=1 for "
            "local-dev runs."
        )

    if not database_url:
        return 3, "Pattern D pre-flight: DATABASE_URL not set."

    if current_database_value != expected_db_name:
        return 3, (
            f"Pattern D pre-flight: current_database()="
            f"{current_database_value!r} does not match expected "
            f"{expected_db_name!r}. Refusing to run against a non-"
            f"production database."
        )

    # URL hostname substring match. Use urlparse to extract hostname
    # so e.g. user:pass@host:port DATABASE_URLs don't false-match
    # against credential substrings.
    try:
        parsed = urlparse(database_url)
        hostname = (parsed.hostname or "")
    except (ValueError, AttributeError):
        return 3, (
            f"Pattern D pre-flight: DATABASE_URL is not a parseable URL."
        )

    if expected_db_host not in hostname:
        return 3, (
            f"Pattern D pre-flight: DATABASE_URL hostname "
            f"{hostname!r} does not contain expected "
            f"{expected_db_host!r}. Refusing to run against a non-"
            f"production branch endpoint."
        )

    return 0, "ok"


async def _pattern_d_pre_flight() -> int:
    """Verify connection endpoint matches expected production endpoint.

    Returns 0 on success, 3 on mismatch. See _check_pattern_d_endpoint()
    for the pure-function check logic.

    Production cron sets EXPECTED_PRODUCTION_DB_NAME +
    EXPECTED_PRODUCTION_DB_HOST. Local dev sets
    DAILY_DIFF_ALLOW_NON_PRODUCTION=1.

    Per scope doc §10 + KBL methodology doc Pattern D (read-path
    sub-pattern): runs BEFORE any production data read. Cost-asymmetry:
    5-second check prevents hours-to-days of misdirected measurement.
    """
    log = get_logger("daily_diff")

    allow_non_production = (
        os.environ.get("DAILY_DIFF_ALLOW_NON_PRODUCTION", "").strip() == "1"
    )

    # Bypass entire SQL roundtrip when opt-out is set.
    if allow_non_production:
        rc, msg = _check_pattern_d_endpoint(
            os.environ.get("DATABASE_URL"), "",
            expected_db_name=None, expected_db_host=None,
            allow_non_production=True,
        )
        log.info("daily_diff.pattern_d.bypass", message=msg)
        return rc

    expected_db_name = (
        os.environ.get("EXPECTED_PRODUCTION_DB_NAME", "").strip()
        or PATTERN_D_DEFAULT_DB_NAME
    )
    expected_db_host = (
        os.environ.get("EXPECTED_PRODUCTION_DB_HOST", "").strip() or None
    )
    database_url = os.environ.get("DATABASE_URL")

    if async_session is None:
        print(
            "ERROR: Pattern D pre-flight: async_session unavailable "
            "(DATABASE_URL not set or engine init failed).",
            file=sys.stderr,
        )
        return 3

    async with async_session() as session:
        result = await session.execute(text("SELECT current_database();"))
        current_database_value = result.scalar_one()

    rc, msg = _check_pattern_d_endpoint(
        database_url, current_database_value,
        expected_db_name=expected_db_name,
        expected_db_host=expected_db_host,
        allow_non_production=False,
    )
    if rc == 0:
        log.info(
            "daily_diff.pattern_d.ok",
            current_database=current_database_value,
            expected_db_name=expected_db_name,
            expected_db_host=expected_db_host,
        )
    else:
        log.error("daily_diff.pattern_d.fail", message=msg)
        print(f"ERROR: {msg}", file=sys.stderr)
    return rc


# ── Measurement targets (per PR #175 §7) ───────────────────────


async def _build_matcher(session):
    """Construct a TieredMatcher with strict + alias + fuzzy tiers.

    Mirrors scripts/run_resolver_pass.py:main bootstrap steps. Extracted
    here so the measurement loop stands up an identical resolver stack
    without duplicating the load_all calls inline.
    """
    from resolver import (
        AliasResolver, AliasTierMatcher, CandidateIndex,
        CompetitionResolver, FuzzyTierMatcher, StrictMatcher,
        TieredMatcher,
    )

    aliases = await AliasResolver.load_all(session)
    sport_rows = (await session.execute(
        text("SELECT id, code, name FROM sp.sports")
    )).all()
    sport_id_by_code_or_name: dict[str, int] = {}
    for row in sport_rows:
        sport_id_by_code_or_name[row.code] = row.id
        sport_id_by_code_or_name[row.name] = row.id

    competitions = await CompetitionResolver.load_all(session)
    candidate_index = await CandidateIndex.load_all(session)

    strict = StrictMatcher(
        aliases=aliases,
        sport_id_by_code_or_name=sport_id_by_code_or_name,
        competitions=competitions,
    )
    alias = AliasTierMatcher(
        candidates=candidate_index,
        sport_id_by_code_or_name=sport_id_by_code_or_name,
    )
    fuzzy = FuzzyTierMatcher(
        candidates=candidate_index,
        sport_id_by_code_or_name=sport_id_by_code_or_name,
    )
    return TieredMatcher(strict=strict, alias=alias, fuzzy=fuzzy)


async def _resolve_record(
    *, matcher, extractor, raw_payload: dict,
    sport_name: str | None, provider: str, session,
) -> dict:
    """Run extract_signal + matcher.match on one record.

    Returns a row-shaped dict for aggregate_per_sport_metrics():
        {"reason_code": str, "reason_detail": {"sport": str, ...}}

    Synthetic reason_codes (not in ReasonCode enum):
      - 'signal_extraction_skipped' — extract_signal returned None
      - 'crash' — extract_signal or matcher raised
    """
    try:
        if provider == "fl":
            signal = extractor.extract_signal(raw_payload, sport=sport_name)
        else:
            signal = extractor.extract_signal(raw_payload)
    except Exception:
        return {
            "reason_code": "crash",
            "reason_detail": {"sport": sport_name or ""},
        }

    if signal is None:
        return {
            "reason_code": "signal_extraction_skipped",
            "reason_detail": {"sport": sport_name or ""},
        }

    try:
        tier_results = await matcher.match(session, signal)
    except Exception:
        return {
            "reason_code": "crash",
            "reason_detail": {"sport": signal.sport or sport_name or ""},
        }

    if not tier_results:
        return {
            "reason_code": "no_match",
            "reason_detail": {"sport": signal.sport or sport_name or ""},
        }

    final = tier_results[-1]
    detail = dict(final.reason_detail) if final.reason_detail else {}
    # The matcher's reason_detail doesn't reliably carry the sport
    # tag; thread the signal's sport through so per-sport aggregation
    # works regardless of which tier produced the final result.
    detail.setdefault("sport", signal.sport or sport_name or "")
    return {
        "reason_code": final.reason_code.value,
        "reason_detail": detail,
        "confidence": float(final.confidence),
    }


# Window SQL — fresh reads against the provider tables. No filter on
# fixture_id: we measure the resolver's output against EVERY record
# ingested in the window, regardless of whether the production cron
# already resolved it. The matcher is deterministic given the data
# (architecture P5); running it twice yields the same result. This
# gives a baseline measurement that doesn't depend on cron timing.
_KALSHI_WINDOW_SQL = (
    "SELECT km.ticker AS pk, km.raw_payload "
    "FROM sp.kalshi_markets km "
    "WHERE km.last_seen_at >= :window_start "
    "  AND km.last_seen_at < :window_end "
    "ORDER BY km.last_seen_at DESC"
)

_FL_WINDOW_SQL = (
    "SELECT fle.fl_event_id AS pk, fle.raw_payload, s.name AS sport_name "
    "FROM sp.fl_events fle "
    "LEFT JOIN sp.sports s ON s.id = fle.sport_id "
    "WHERE fle.last_seen_at >= :window_start "
    "  AND fle.last_seen_at < :window_end "
    "ORDER BY fle.last_seen_at DESC"
)

_REVIEW_QUEUE_SQL = (
    "SELECT rq.status, rq.created_at, "
    "       COALESCE(rq.reason_detail->>'sport', '') AS sport "
    "FROM sp.review_queue rq "
    "WHERE rq.status = 'pending'"
)

_RESOLUTION_LOG_WINDOW_SQL = (
    "SELECT reason_code FROM sp.resolution_log "
    "WHERE decided_at >= :window_start "
    "  AND decided_at < :window_end"
)


# ── Deliverable 1 (v3-vs-v4 legacy comparison) ─────────────────
#
# Per docs/measurement/deliverable-1-scope-2026-07-25.md. Adds the
# missing comparison dimension that closes Gate #2's second blank
# (§11.3 Item 7, "diff until acceptable"). Deliverable 2 measures the
# NEW resolver standalone; Deliverable 1 compares its output against
# the two legacy pairing algorithms (v1 title-parse via
# `main.py::_build_kalshi_index_for_sport`, v2 identity-parse via
# `kalshi_join.build_kalshi_index + join_with_fl`).
#
# Namespace realization (post-manual-run correction 2026-07-28): the
# original scope doc §3.1/§4.3 premise about a namespace mismatch was
# WRONG. `sp.kalshi_markets.ticker` is the DB column name, but its
# semantic content is the Kalshi EVENT_TICKER — populated at
# ingestion.kalshi:179 via `record.get("event_ticker")`. So v1/v2
# legacy maps AND `km.ticker` are already in the same event_ticker
# namespace. No JOIN through `public.markets` / `public.events` is
# needed to normalize; we `array_agg(DISTINCT km.ticker)` directly.
# The prior version's JOIN chain silently dropped ~95% of km rows via
# the pm.ticker!=km.ticker mismatch, producing the manual-run's
# near-empty v4 map (5 fl_ids across 9 sports).
#
# Same-window discipline (post-manual-run tightening): scope the v4
# query by D2's LOADED PK SETS (`kalshi_pks` + `fl_pks`) rather than
# re-evaluating a `last_seen_at` predicate at a different moment.
# Insulates from PR #260's active-row `last_seen_at` bumps that would
# otherwise exclude still-active fixture-linked rows on any historical
# cron run (window ended before run). Rock-solid same-population by
# construction: v4's ticker sets come from EXACTLY the km rows D2's
# `_KALSHI_WINDOW_SQL` loaded, not a re-evaluated superset/subset.

SAMPLE_N = 30

# 2026-08-08 (#47 cross-process cohort attribution): daily_diff runs
# as a standalone cron process (railway.toml). cutover.is_v4_cohort
# has three RAM lookups (_CURRENT_CONFIG, _SERIES_SPORT_DYNAMIC,
# _V4_LINKAGE_MAP) that are ALL populated only in the FastAPI web
# process — the cron sees them empty and every is_v4_cohort call
# returns False, mis-attributing every row to *_out_of_cohort and
# blinding the Item 7 both_pair_different_cohort gate at line 1134.
#
# Fix: the web process persists its cohort snapshot to
# cache_blobs['v4_cohort_snapshot'] on every _v4_linkage_refresh_loop
# tick (main.py). We load that blob at pass start and shim the
# is_v4_cohort classifier to check set-membership against it.
#
# ACCEPTED LIMITATION (documented, not solved): the snapshot reflects
# cohort membership at snapshot time. If cohort membership drifts
# WITHIN a 24h report window (linked_universe growth reorders
# lowest-N-by-hash), attribution uses the value at snapshot load
# time — a small class of under-attribution possible but bounded to
# the linkage refresh cadence (default 60s per _V4_LINKAGE_REFRESH_
# INTERVAL_S). Soak reset-conditions already watch cohort drift as
# an operator concern; provenance fields (loaded_at_ts,
# config_loaded_at_ts) in report_json's cohort_snapshot let future
# attribution disputes self-adjudicate.
_COHORT_SNAPSHOT_SET: set = set()
_COHORT_SNAPSHOT_META: dict = {}


async def _load_cohort_snapshot_from_cache_blob() -> None:
    """Populate _COHORT_SNAPSHOT_SET + _COHORT_SNAPSHOT_META from the
    web-side persisted cache_blob. Called ONCE at daily_diff() entry;
    the snapshot is then used throughout the pass.

    On any failure (blob missing, DB unavailable, unexpected shape)
    the sets stay empty — falling through to every-row-out-of-cohort
    behavior, which is the pre-fix diff behavior. That preserves
    forward-compat: an old cron running against a new web deploy
    that hasn't yet written the blob still produces a report,
    just with the old attribution accuracy.
    """
    global _COHORT_SNAPSHOT_SET, _COHORT_SNAPSHOT_META
    _log = get_logger("daily_diff.cohort_snapshot")
    try:
        from db import load_cache_blob
        blob = await load_cache_blob("v4_cohort_snapshot")
    except Exception as e:
        _log.warning("v4_cohort_snapshot load failed: %s", e)
        return
    if not isinstance(blob, dict):
        return
    series_list = blob.get("cohort_series") or []
    _COHORT_SNAPSHOT_SET = {str(s).upper() for s in series_list if s}
    _COHORT_SNAPSHOT_META = {
        k: v for k, v in blob.items() if k != "cohort_series"
    }
    _log.info(
        "v4_cohort_snapshot.loaded",
        size=len(_COHORT_SNAPSHOT_SET),
        loaded_at_ts=_COHORT_SNAPSHOT_META.get("loaded_at_ts"),
        config_source=_COHORT_SNAPSHOT_META.get("config_source"),
    )

# v4 pairing reconstruction — all sports at once, scoped by D2's PKs.
#   - fle.fixture_id NOT NULL       → v4 has actually resolved this fl_event
#   - fle.fl_event_id = ANY(:fl_pks) → strict same-population with D2's fl_rows
#   - km.ticker      = ANY(:kalshi_pks) → strict same-population with D2's kalshi_rows
# `km.ticker` semantically holds event_ticker (see ingestion.kalshi:179),
# so array_agg(DISTINCT km.ticker) yields event_ticker sets directly.
# One query instead of one-per-sport; per-sport partitioning happens
# in Python via the fl_id → sport_name map built from fl_rows.
_V4_PAIRINGS_SQL = (
    "SELECT fle.fl_event_id, "
    "       array_agg(DISTINCT km.ticker) AS event_tickers "
    "FROM sp.fl_events fle "
    "JOIN sp.kalshi_markets km ON km.fixture_id = fle.fixture_id "
    "WHERE fle.fixture_id IS NOT NULL "
    "  AND fle.fl_event_id = ANY(:fl_pks) "
    "  AND km.ticker      = ANY(:kalshi_pks) "
    "GROUP BY fle.fl_event_id"
)


def _build_games_from_fl_events(fl_events: list[dict]) -> dict:
    """Construct the `games` dict `match_game(...)` expects, from the
    same fl_events D2 loaded. Same-window discipline: v1's title-based
    pairing pairs against exactly the fl_events D2 sampled, not against
    the module-level `flashlive_feed.GAMES` (empty in the standalone
    daily-diff cron because the FL polling loop only runs in the web
    process — see manual-run Symptom 1 finding).

    Keying mirrors the polling loop's `{sport}:{home_norm}:{away_norm}`
    format so nothing about match_game's iteration semantics changes.
    Falls back to `event_id` if the sport/home/away triple can't be
    constructed (parse failed for that fl_event) — match_game iterates
    values, so key format only matters for uniqueness.
    """
    from flashlive_feed import _parse_event, _normalize
    games: dict = {}
    for ev in fl_events:
        if not isinstance(ev, dict):
            continue
        try:
            g = _parse_event(ev)
        except Exception:
            continue
        if not (g and g.get("home_name") and g.get("away_name")):
            continue
        sport = g.get("sport") or ""
        home_norm = _normalize(g.get("home_name") or "")
        away_norm = _normalize(g.get("away_name") or "")
        key = f"{sport}:{home_norm}:{away_norm}"
        if not (home_norm and away_norm):
            key = g.get("event_id") or f"unkeyed:{id(g)}"
        games[key] = g
    return games


def _run_legacy_pairings(
    sport_name: str,
    kalshi_records: list[dict],
    fl_events: list[dict],
    games: dict | None = None,
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Run v1 (title-parse) and v2 (identity-parse) pairings against
    the same 24h population.

    Returns (v1_map, v2_map). Both maps carry `{fl_event_id: {event_ticker, ...}}`
    — the event_ticker namespace, same as `_query_v4_pairings`.

    `games` parameter (added post-manual-run for Symptom 1 fix):
    passed through to v1's `_build_kalshi_index_for_sport(...games=...)`
    → `match_game(...games=...)`. In the daily-diff cron, callers build
    `games` from the same fl_events via `_build_games_from_fl_events`;
    in tests, callers can pass any pre-built dict. Default `None`
    preserves the pre-existing behavior of reading module-level
    `flashlive_feed.GAMES` (which is what legacy request-path callers
    of `_build_kalshi_index_for_sport` still expect).
    """
    from main import _build_kalshi_index_for_sport
    from kalshi_join import build_kalshi_index, join_with_fl

    # v1: title-parse via _build_kalshi_index_for_sport. Passes the
    # explicit records list AND games dict so the daily-diff cron
    # gets deterministic same-window behavior — no dependency on
    # `_cache` or module-level `GAMES`, both of which are empty in
    # the standalone cron container.
    v1_index = _build_kalshi_index_for_sport(
        sport_name, records=kalshi_records, games=games,
    )
    v1_map: dict[str, set[str]] = {
        str(fl_id): {r.get("event_ticker") or "" for r in recs}
        for fl_id, recs in v1_index.items()
    }
    # Drop empty-string tickers that leaked in from records missing an
    # event_ticker field; the intersection tests treat "" as a real
    # element otherwise.
    for k in list(v1_map):
        v1_map[k].discard("")
        if not v1_map[k]:
            v1_map.pop(k)

    # v2: identity-parse via kalshi_join. Doesn't depend on GAMES —
    # parses tickers directly, no title matching.
    v2_index = build_kalshi_index(kalshi_records, sport_name)
    v2_pairings, _v2_unpaired = join_with_fl(fl_events, v2_index, sport_name)
    v2_map: dict[str, set[str]] = {}
    for p in v2_pairings:
        fl_id = p.fl_event.get("EVENT_ID") or ""
        if not fl_id:
            continue
        tickers = {r.get("event_ticker") or "" for r in p.kalshi_records}
        tickers.discard("")
        if tickers:
            v2_map[str(fl_id)] = tickers

    return v1_map, v2_map


async def _query_v4_pairings(
    session,
    kalshi_pks: list[str],
    fl_pks: list[str],
) -> dict[str, set[str]]:
    """Reconstruct v4's pairing state scoped to D2's loaded populations.

    Returns `{fl_event_id: {event_ticker, ...}}` — same shape and
    namespace as `_run_legacy_pairings` output. fl_events with
    `fixture_id IS NULL` are excluded (v4 hasn't paired them); if
    v1/v2 paired one of these it becomes `legacy_only` in the diff.

    Strict same-population semantics: v4's ticker sets come from the
    exact km rows D2 loaded, not from a re-evaluated `last_seen_at ∈
    window` predicate at a slightly-later moment. Insulates from
    PR #260's active-row `last_seen_at` bumps that would exclude
    still-active fixture-linked rows on any historical cron run.
    """
    if not (kalshi_pks and fl_pks):
        return {}
    result = await session.execute(
        text(_V4_PAIRINGS_SQL),
        {
            "kalshi_pks": kalshi_pks,
            "fl_pks":     fl_pks,
        },
    )
    v4_map: dict[str, set[str]] = {}
    for row in result.all():
        fl_id = str(row.fl_event_id)
        tickers = {t for t in (row.event_tickers or []) if t}
        if tickers:
            v4_map[fl_id] = tickers
    return v4_map


def _diff_pairings(
    legacy_map: dict[str, set[str]],
    v4_map: dict[str, set[str]],
    extractor,
    kalshi_records: list[dict],
) -> dict:
    """Bucket every fl_event_id present in either map into one of six
    dispositions per scope doc §3.5 and §3.4.

    Both maps MUST be in the same namespace (event_ticker). If callers
    pass mixed namespaces the intersection tests silently collapse to
    disjoint-everywhere and every dual-pairing lands in
    `both_pair_different` — the exact regression `test_v4_query_returns_
    event_ticker_namespace` guards against.

    Returns the report_json sub-dict per §3.3 schema. Asserts pre-return
    that the bucket-sum invariant holds; raises AssertionError on
    violation rather than writing a corrupt report.
    """
    # Build set of event_tickers v4's extractor deliberately refused.
    # Namespace: event_ticker, matching what legacy_map and v4_map carry.
    v4_excluded_tickers: set[str] = set()
    for r in kalshi_records:
        try:
            sig = extractor.extract_signal(r)
        except Exception:
            sig = None
        if sig is None:
            et = r.get("event_ticker") or ""
            if et:
                v4_excluded_tickers.add(et)

    all_fl_ids = set(legacy_map.keys()) | set(v4_map.keys())

    # Phase 3 (2026-08-05): TWELVE-bucket schema — each of the six
    # semantic buckets is split into `_cohort` (records whose series
    # is in the v4 cohort at report-generation time) and `_out_of_cohort`
    # (everything else). Item 7 gate reads on `..._cohort` counts
    # only — those are the records USERS SEE served as v4. Out-of-
    # cohort disagreements are measurement continuity (still tracking
    # v4 accuracy on the un-served slice); useful for expanding the
    # gate later but not part of stage-freeze triggers today.
    _bucket_keys = (
        "agree_same_fixture", "agree_partial_coverage",
        "v4_only", "legacy_only", "both_pair_different",
        "v4_extraction_excluded",
    )
    buckets = {}
    for bk in _bucket_keys:
        buckets[f"{bk}_cohort"] = 0
        buckets[f"{bk}_out_of_cohort"] = 0
    # 2026-08-08 (#47 fix): cross-process cohort attribution.
    # daily_diff runs as a standalone cron process where
    # cutover.is_v4_cohort's three RAM lookups (_CURRENT_CONFIG,
    # _SERIES_SPORT_DYNAMIC, _V4_LINKAGE_MAP) are ALL empty and
    # every call returns False → all *_cohort buckets read zero →
    # Item 7 gate below blind to real cohort danger. The shim below
    # uses the cohort snapshot the SERVING web process persists to
    # cache_blobs['v4_cohort_snapshot'] (see
    # main._v4_linkage_refresh_loop) as the truth source.
    #
    # _COHORT_SNAPSHOT_SET is populated at daily_diff() entry via
    # _load_cohort_snapshot_from_cache_blob(); if the load failed or
    # the blob is missing, the set is empty and the shim returns
    # False for every series — same behavior as the pre-fix bug.
    # That preserves forward-compat: old cron running against new
    # web deploy that hasn't yet written the blob still produces a
    # report, just with the pre-fix attribution accuracy.
    def _is_v4_cohort(series_ticker, cfg=None):  # noqa: ARG001
        if not series_ticker:
            return False
        return str(series_ticker).upper() in _COHORT_SNAPSHOT_SET
    # Sample every non-agree bucket AND agree_partial_coverage.
    # Sample keys use the RAW bucket name (no cohort suffix) — the
    # sample list gets a `cohort` bool field so operators can filter
    # a single sample stream by cohort membership at read time.
    samples: dict[str, list[dict]] = {
        bk: [] for bk in _bucket_keys if bk != "agree_same_fixture"
    }

    def _series_of(tickers):
        # Extract representative series_ticker for cohort membership.
        # All tickers for one fl_event_id typically share a series
        # (same market family per fixture); pick the first.
        for t in tickers:
            if isinstance(t, str) and "-" in t:
                return t.split("-", 1)[0]
        return ""

    def _cohort_bucket(base: str, series: str) -> str:
        return f"{base}_{'cohort' if _is_v4_cohort(series) else 'out_of_cohort'}"

    for fl_id in all_fl_ids:
        legacy_tickers = legacy_map.get(fl_id, set())
        v4_tickers = v4_map.get(fl_id, set())
        # Representative series for cohort attribution — pick from
        # any available ticker (v4 preferred, falls back to legacy).
        _rep_series = _series_of(v4_tickers) or _series_of(legacy_tickers)
        _cohort = _is_v4_cohort(_rep_series)

        # Extraction-exclusion first — a legacy pairing whose Kalshi
        # side is entirely v4-excluded tickers is NOT a regression.
        legacy_non_excluded = legacy_tickers - v4_excluded_tickers
        if legacy_tickers and not legacy_non_excluded and not v4_tickers:
            buckets[_cohort_bucket("v4_extraction_excluded", _rep_series)] += 1
            if len(samples["v4_extraction_excluded"]) < SAMPLE_N:
                samples["v4_extraction_excluded"].append({
                    "fl_event_id":      fl_id,
                    "excluded_tickers": sorted(legacy_tickers),
                    "cohort":           _cohort,
                })
            continue

        legacy_effective = legacy_non_excluded  # tickers v4 could evaluate

        # Five-bucket classification per §3.5 equal / overlap / disjoint.
        if legacy_effective and v4_tickers:
            if legacy_effective == v4_tickers:
                buckets[_cohort_bucket("agree_same_fixture", _rep_series)] += 1
            elif legacy_effective & v4_tickers:
                # Non-empty intersection, unequal sets — coverage
                # difference under a shared fixture. BENIGN.
                buckets[_cohort_bucket("agree_partial_coverage", _rep_series)] += 1
                if len(samples["agree_partial_coverage"]) < SAMPLE_N:
                    samples["agree_partial_coverage"].append({
                        "fl_event_id":    fl_id,
                        "legacy_tickers": sorted(legacy_effective),
                        "v4_tickers":     sorted(v4_tickers),
                        "overlap":        sorted(legacy_effective & v4_tickers),
                        "cohort":         _cohort,
                    })
            else:
                # Disjoint sets — different fixtures under same
                # fl_event_id. DANGEROUS: silent wrong-linking.
                # Item 7 gate reads on both_pair_different_cohort only.
                buckets[_cohort_bucket("both_pair_different", _rep_series)] += 1
                if len(samples["both_pair_different"]) < SAMPLE_N:
                    samples["both_pair_different"].append({
                        "fl_event_id":    fl_id,
                        "legacy_tickers": sorted(legacy_effective),
                        "v4_tickers":     sorted(v4_tickers),
                        "cohort":         _cohort,
                    })
        elif v4_tickers:
            buckets[_cohort_bucket("v4_only", _rep_series)] += 1
            if len(samples["v4_only"]) < SAMPLE_N:
                samples["v4_only"].append({
                    "fl_event_id": fl_id,
                    "v4_tickers":  sorted(v4_tickers),
                    "cohort":      _cohort,
                })
        elif legacy_effective:
            buckets[_cohort_bucket("legacy_only", _rep_series)] += 1
            if len(samples["legacy_only"]) < SAMPLE_N:
                samples["legacy_only"].append({
                    "fl_event_id":    fl_id,
                    "legacy_tickers": sorted(legacy_effective),
                    "cohort":         _cohort,
                })
        # No else branch — if we fall through here (both sets empty
        # after extraction filtering, and the earlier v4_extraction_
        # excluded gate didn't fire), no bucket increments. The
        # invariant assert below catches it — deliberate loudness on
        # any classification bug.

    total = sum(buckets.values())
    assert total == len(all_fl_ids), (
        f"invariant violated: bucket sum {total} != all_fl_ids "
        f"count {len(all_fl_ids)} — orphaned fl_ids in the diff would "
        f"produce a corrupt report; raising rather than persisting."
    )

    return {
        **buckets,
        "total_evaluated":       total,
        "sample_disagreements":  samples,
    }


def _sum_diff_dicts(diffs: list[dict]) -> dict:
    """Sum per-sport bucket counts into the summed-across-sports shape
    that ships in `report_json` per §3.3 aggregation shape.

    Per-sport samples are concatenated then truncated to SAMPLE_N per
    bucket so `report_json` stays bounded regardless of sport count.
    """
    # Phase 3 (2026-08-05): twelve-bucket schema. Each semantic
    # bucket has _cohort / _out_of_cohort suffix. Also emit
    # backward-compatible aggregate counts (sum of both suffixes)
    # so existing render_daily_diff_report + monitoring code that
    # reads the six unsuffixed keys keeps working.
    _semantic_keys = [
        "agree_same_fixture",
        "agree_partial_coverage",
        "v4_only",
        "legacy_only",
        "both_pair_different",
        "v4_extraction_excluded",
    ]
    bucket_keys = []
    for bk in _semantic_keys:
        bucket_keys.append(f"{bk}_cohort")
        bucket_keys.append(f"{bk}_out_of_cohort")
    if not diffs:
        empty = {k: 0 for k in bucket_keys}
        empty.update({k: 0 for k in _semantic_keys})  # BC aggregates
        return {
            **empty,
            "total_evaluated":      0,
            "sample_disagreements": {},
        }
    summed = {k: sum(d.get(k, 0) for d in diffs) for k in bucket_keys}
    # Backward-compat aggregates: sum both cohort halves so pre-
    # Phase-3 readers (render script, dashboards) still see the
    # six unsuffixed counts. Item 7 gate reads on `_cohort` only.
    for bk in _semantic_keys:
        summed[bk] = summed.get(f"{bk}_cohort", 0) + summed.get(f"{bk}_out_of_cohort", 0)
    summed["total_evaluated"] = sum(d.get("total_evaluated", 0) for d in diffs)
    # Concatenate sample lists across sports; truncate to SAMPLE_N per
    # bucket. Union of keys handles the case where some sports had
    # empty sample buckets omitted.
    all_sample_keys: set[str] = set()
    for d in diffs:
        all_sample_keys.update(d.get("sample_disagreements", {}).keys())
    summed["sample_disagreements"] = {
        k: [s for d in diffs for s in d.get("sample_disagreements", {}).get(k, [])][:SAMPLE_N]
        for k in all_sample_keys
    }
    return summed


async def _measure(
    window_start: datetime, window_end: datetime,
) -> tuple[dict, int]:
    """Run the measurement pass against the window.

    Returns (metrics_dict, total_records_scanned). The metrics dict
    matches the sp.daily_diff_reports.metrics JSONB column shape
    (v0.2.0 stamped via SCOPE_FILTER_VERSION):

    {
      "scope_filtered": {
        "matcher_capability_rate_overall": float,
        "matcher_capability_rate_per_sport": {sport: float, ...},
        "per_tier_rate_per_sport": {sport: {bucket: int, ...}, ...},
        "personal_path_rate": float,
        "team_path_rate": float,
      },
      "raw": {
        "matcher_capability_rate_overall_unfiltered": float,
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

    Eight measurement targets from PR #175 §7 + resolution_log
    row-volume from Finding X (2026-05-20).
    """
    from resolver import KalshiResolverModule, FLResolverModule

    if async_session is None:
        raise RuntimeError("async_session unavailable; cannot measure.")

    log = get_logger("daily_diff")
    bind = {"window_start": window_start, "window_end": window_end}

    # ── Step 1: bootstrap matcher + fetch window records ──
    async with async_session() as session:
        matcher = await _build_matcher(session)
        kalshi_rows = (await session.execute(
            text(_KALSHI_WINDOW_SQL), bind,
        )).all()
        fl_rows = (await session.execute(
            text(_FL_WINDOW_SQL), bind,
        )).all()
        queue_rows = (await session.execute(text(_REVIEW_QUEUE_SQL))).all()
        log_rows = (await session.execute(
            text(_RESOLUTION_LOG_WINDOW_SQL), bind,
        )).all()

    log.info(
        "daily_diff.window_loaded",
        kalshi=len(kalshi_rows),
        fl=len(fl_rows),
        queue=len(queue_rows),
        resolution_log=len(log_rows),
    )

    kalshi_extractor = KalshiResolverModule()
    fl_extractor = FLResolverModule()

    # ── Step 2: classify + resolve per record ──
    # Each record runs in its own session so a single matcher crash
    # doesn't poison subsequent reads (mirrors run_resolver_pass.py's
    # 2D.3.1 per-record transaction discipline).
    scope_aggregator_rows: list[dict] = []
    confidence_scores: list[float] = []
    non_sport = 0
    prop_market = 0
    signal_skipped = 0
    overall_records = 0
    overall_auto_apply = 0

    async with async_session() as session:
        for row in kalshi_rows:
            overall_records += 1
            raw = row.raw_payload or {}
            record = {"raw_payload": raw}
            classification = classify_record("kalshi", record)
            if classification == ScopeClassification.NON_SPORT:
                non_sport += 1
                continue
            if classification == ScopeClassification.PROP_MARKET:
                prop_market += 1
                continue
            resolved = await _resolve_record(
                matcher=matcher, extractor=kalshi_extractor,
                raw_payload=raw, sport_name=None,
                provider="kalshi", session=session,
            )
            rc = resolved["reason_code"]
            if rc == "signal_extraction_skipped":
                signal_skipped += 1
                continue
            if rc in _AUTO_APPLY_REASON_CODES:
                overall_auto_apply += 1
            scope_aggregator_rows.append(resolved)
            if "confidence" in resolved:
                confidence_scores.append(resolved["confidence"])

        for row in fl_rows:
            overall_records += 1
            raw = row.raw_payload or {}
            classification = classify_record("fl", {"raw_payload": raw})
            # FL only emits HEAD_TO_HEAD today (per classify_fl_record);
            # the branch stays for future cohorts that add FL filters.
            if classification == ScopeClassification.NON_SPORT:
                non_sport += 1
                continue
            if classification == ScopeClassification.PROP_MARKET:
                prop_market += 1
                continue
            resolved = await _resolve_record(
                matcher=matcher, extractor=fl_extractor,
                raw_payload=raw, sport_name=row.sport_name,
                provider="fl", session=session,
            )
            rc = resolved["reason_code"]
            if rc == "signal_extraction_skipped":
                signal_skipped += 1
                continue
            if rc in _AUTO_APPLY_REASON_CODES:
                overall_auto_apply += 1
            scope_aggregator_rows.append(resolved)
            if "confidence" in resolved:
                confidence_scores.append(resolved["confidence"])

    # ── Step 3: aggregate via pure functions ──
    scope_filtered = aggregate_per_sport_metrics(scope_aggregator_rows)

    queue_dicts = [
        {"sport": r.sport, "status": r.status, "created_at": r.created_at}
        for r in queue_rows
    ]
    queue_metrics = aggregate_queue_metrics(
        queue_dicts, now=window_end,
    )

    log_dicts = [{"reason_code": r.reason_code} for r in log_rows]
    log_volume = aggregate_resolution_log_volume(log_dicts)

    metrics = {
        "scope_filtered": scope_filtered,
        "raw": {
            # Unfiltered = overall_auto_apply / all records (including
            # NON_SPORT + PROP_MARKET + signal_extraction_skipped).
            # scope_filtered.matcher_capability_rate_overall excludes those.
            "matcher_capability_rate_overall_unfiltered": _safe_rate(
                overall_auto_apply, overall_records,
            ),
            "signal_extraction_skipped": signal_skipped,
            "non_sport_filtered_out": non_sport,
            "prop_market_filtered_out": prop_market,
        },
        "queue": queue_metrics,
        "resolution_log_volume_per_cron": log_volume,
    }

    # report_json carries non-metric supplementary data: confidence
    # histogram (for threshold-calibration analysis) + Deliverable 1's
    # legacy-vs-new pairing comparison per §3.3 schema.
    report_json = {
        "confidence_histogram": compute_confidence_histogram(confidence_scores),
        "confidence_scores_count": len(confidence_scores),
    }

    # ── Step 4: Deliverable 1 legacy comparison (v3 vs v4) ──
    #
    # Per-sport fan-out. kalshi raw_payload IS the enriched cache
    # record (has `_sport` field) because ingestion.kalshi writes
    # main._cache["data_all"] rows verbatim; FL sport comes from the
    # sp.sports JOIN already surfaced in fl_rows[i].sport_name.
    kalshi_records_for_diff: list[dict] = [
        dict(row.raw_payload) for row in kalshi_rows
        if row.raw_payload and isinstance(row.raw_payload, dict)
    ]
    fl_events_by_sport: dict[str, list[dict]] = defaultdict(list)
    for row in fl_rows:
        if row.raw_payload and isinstance(row.raw_payload, dict) and row.sport_name:
            # Inject `_sport` from the sp.sports JOIN's sport_name.
            # sp.fl_events.raw_payload does NOT carry SPORT_ID at the
            # event level — FL's /v1/events/list nests SPORT_ID in the
            # tournament wrapper, not per-event. `_parse_event` at
            # flashlive_feed.py:510 falls back to
            # `SPORT_MAP.get(str(ev.get("SPORT_ID", "")), "")` = `""`
            # when SPORT_ID is absent → every game gets `sport=""` →
            # `match_game`'s sport-filter drops every candidate → v1
            # map is empty. Diagnostic Run 3 (2026-07-28) confirmed
            # this via scripts/diag_s1.py: stage [10] showed 0/313
            # games matched Basketball. `_parse_event` prefers
            # `_sport` when present, so injecting the JOIN-derived
            # display-case sport here gives every game the right
            # sport label without changing FL API parsing.
            enriched = dict(row.raw_payload)
            enriched["_sport"] = row.sport_name
            fl_events_by_sport[row.sport_name].append(enriched)
    kalshi_records_by_sport: dict[str, list[dict]] = defaultdict(list)
    for r in kalshi_records_for_diff:
        sport = r.get("_sport")
        if sport:
            kalshi_records_by_sport[sport].append(r)

    # Extract D2's loaded PK sets — used to scope the v4 query to
    # exactly the same population D2 sampled. Strict same-window by
    # construction (see _V4_PAIRINGS_SQL header comment).
    kalshi_pks = [row.pk for row in kalshi_rows]  # sp.kalshi_markets.ticker
    fl_pks     = [row.pk for row in fl_rows]      # sp.fl_events.fl_event_id

    # Build fl_event_id → sport lookup from fl_rows (has sport_name via
    # LEFT JOIN sp.sports in _FL_WINDOW_SQL). Used to partition v4's
    # single all-sports return by sport in Python.
    fl_id_to_sport: dict[str, str] = {}
    for row in fl_rows:
        if row.raw_payload and isinstance(row.raw_payload, dict) and row.sport_name:
            evt_id = str(row.raw_payload.get("EVENT_ID") or "")
            if evt_id:
                fl_id_to_sport[evt_id] = row.sport_name

    sports_to_diff = sorted(
        set(kalshi_records_by_sport) & set(fl_events_by_sport)
    )
    per_sport_v1_diffs: list[dict] = []
    per_sport_v2_diffs: list[dict] = []
    legacy_diff_errors = 0
    all_v4_map: dict[str, set[str]] = {}

    async with async_session() as session:
        try:
            # ONE all-sports v4 query, PK-scoped. Partition per-sport
            # in Python via fl_id_to_sport lookup.
            all_v4_map = await _query_v4_pairings(session, kalshi_pks, fl_pks)
        except Exception as exc:
            legacy_diff_errors += 1
            log.warning(
                "daily_diff.v4_pairing_query_failed",
                error_class=type(exc).__name__,
                error_msg=str(exc)[:300],
            )
            all_v4_map = {}

    v4_map_by_sport: dict[str, dict[str, set[str]]] = defaultdict(dict)
    for fl_id, tickers in all_v4_map.items():
        sport = fl_id_to_sport.get(fl_id)
        if sport:
            v4_map_by_sport[sport][fl_id] = tickers

    for sport in sports_to_diff:
        # Build FL games dict from THIS sport's fl_events. Symptom 1
        # fix: the standalone daily-diff cron has an empty module-
        # level `flashlive_feed.GAMES` (no polling loop), so v1's
        # match_game would otherwise return None for every title.
        games_for_sport = _build_games_from_fl_events(fl_events_by_sport[sport])
        try:
            v1_map, v2_map = _run_legacy_pairings(
                sport,
                kalshi_records_by_sport[sport],
                fl_events_by_sport[sport],
                games=games_for_sport,
            )
        except Exception as exc:
            legacy_diff_errors += 1
            log.warning(
                "daily_diff.legacy_pairing_failed",
                sport=sport,
                error_class=type(exc).__name__,
                error_msg=str(exc)[:300],
            )
            continue
        v4_map_for_sport = v4_map_by_sport.get(sport, {})
        try:
            v1_diff = _diff_pairings(
                v1_map, v4_map_for_sport, kalshi_extractor,
                kalshi_records_by_sport[sport],
            )
            v2_diff = _diff_pairings(
                v2_map, v4_map_for_sport, kalshi_extractor,
                kalshi_records_by_sport[sport],
            )
        except AssertionError:
            # Invariant violation — raise rather than write corrupt.
            raise
        except Exception as exc:
            legacy_diff_errors += 1
            log.warning(
                "daily_diff.diff_pairings_failed",
                sport=sport,
                error_class=type(exc).__name__,
                error_msg=str(exc)[:300],
            )
            continue
        per_sport_v1_diffs.append(v1_diff)
        per_sport_v2_diffs.append(v2_diff)

    legacy_present = bool(per_sport_v1_diffs and per_sport_v2_diffs)
    if legacy_present:
        report_json["legacy_v1_diff"] = _sum_diff_dicts(per_sport_v1_diffs)
        report_json["legacy_v2_diff"] = _sum_diff_dicts(per_sport_v2_diffs)
        report_json["legacy_diff_meta"] = {
            "sports_covered":    sports_to_diff,
            "per_sport_errors":  legacy_diff_errors,
            # Replaces the removed v4_join_unresolved_tickers counter
            # (was a check on public.markets JOIN drift, no longer
            # needed since we don't JOIN through public.markets).
            # Contextualizes total_evaluated: how many fl_events in
            # D2's loaded fl_rows had fixture_id set AND at least one
            # in-window fixture-linked km peer. Feeds the S2b backfill
            # decision — if this number is tiny relative to
            # len(fl_pks), the fixture-link population is genuinely
            # narrow and backfill may be warranted before Item 7
            # threshold-setting can trust the diff.
            "fixture_linked_in_window_count": len(all_v4_map),
        }
        # 2026-08-08 (#47 fix): cohort_snapshot now sourced from
        # the loaded cache_blob (populated by the SERVING web
        # process at every _v4_linkage_refresh_loop tick), NOT
        # from a live cohort_series_list() call. Cron process has
        # empty RAM state so live-call always returned [] — the
        # pre-fix bug this PR closes. Reports historical reports
        # remain interpretable via cohort membership as of the
        # snapshot's loaded_at_ts.
        #
        # Provenance fields (loaded_at_ts, config_source,
        # config_loaded_at_ts) let future attribution disputes
        # self-adjudicate: an operator seeing an unexpected cohort
        # attribution can trace back through the snapshot's own
        # timestamp + config source to the exact serving-side
        # state at snapshot capture.
        #
        # Empty-snapshot fallback: if the cache_blob load failed
        # or is missing (fresh deploy, DB unreachable, etc.), the
        # loaded meta dict is empty; report shows size=0 and
        # missing=True so the operator can distinguish "cohort is
        # empty" from "snapshot never loaded".
        _COHORT_SNAPSHOT_MAX = 500
        if _COHORT_SNAPSHOT_META or _COHORT_SNAPSHOT_SET:
            _series_sorted = sorted(_COHORT_SNAPSHOT_SET)
            report_json["cohort_snapshot"] = {
                "series":             _series_sorted[:_COHORT_SNAPSHOT_MAX],
                "size":               len(_series_sorted),
                "truncated":          len(_series_sorted) > _COHORT_SNAPSHOT_MAX,
                # Provenance from the snapshot the web process wrote.
                "loaded_at_ts":       _COHORT_SNAPSHOT_META.get("loaded_at_ts"),
                "config_source":      _COHORT_SNAPSHOT_META.get("config_source"),
                "config_loaded_at_ts": _COHORT_SNAPSHOT_META.get("config_loaded_at_ts"),
                "traffic_pct":        _COHORT_SNAPSHOT_META.get("traffic_pct"),
                "enabled_sports":     _COHORT_SNAPSHOT_META.get("enabled_sports"),
                "min_cohort_series":  _COHORT_SNAPSHOT_META.get("min_cohort_series"),
                "universe_size":      _COHORT_SNAPSHOT_META.get("universe_size"),
                "effective_pct":      _COHORT_SNAPSHOT_META.get("effective_pct"),
                "snapshot_version":   _COHORT_SNAPSHOT_META.get("snapshot_version"),
                # When the cron process read the blob (vs when the
                # web process WROTE it — both surfaced for clock-
                # skew diagnosis).
                "cron_read_at_ts":    int(datetime.now(timezone.utc).timestamp()),
            }
        else:
            report_json["cohort_snapshot"] = {
                "series":             [],
                "size":               0,
                "truncated":          False,
                "missing":            True,
                "cron_read_at_ts":    int(datetime.now(timezone.utc).timestamp()),
                "note":               "v4_cohort_snapshot cache_blob not found or empty — cohort attribution unavailable this pass",
            }
        log.info(
            "daily_diff.legacy_comparison_computed",
            sports_covered=len(sports_to_diff),
            per_sport_errors=legacy_diff_errors,
            fixture_linked_in_window=len(all_v4_map),
            v1_both_pair_different=report_json["legacy_v1_diff"].get(
                "both_pair_different", 0,
            ),
            v2_both_pair_different=report_json["legacy_v2_diff"].get(
                "both_pair_different", 0,
            ),
            v1_both_pair_different_cohort=report_json["legacy_v1_diff"].get(
                "both_pair_different_cohort", 0,
            ),
            v2_both_pair_different_cohort=report_json["legacy_v2_diff"].get(
                "both_pair_different_cohort", 0,
            ),
            cohort_snapshot_size=report_json.get("cohort_snapshot", {}).get("size"),
        )
    else:
        # No sport produced a clean diff — surface, but still write the
        # Deliverable 2 report. legacy_comparison_present stays False on
        # the row so downstream readers know the diff isn't there.
        log.warning(
            "daily_diff.legacy_comparison_skipped",
            sports_attempted=len(sports_to_diff),
            per_sport_errors=legacy_diff_errors,
        )

    return metrics, report_json, overall_records, legacy_present


async def _write_report(
    window_start: datetime,
    window_end: datetime,
    metrics: dict,
    report_json: dict,
    total_records: int,
    report_date,
    legacy_present: bool = False,
) -> int:
    """Write one row to sp.daily_diff_reports.

    Unique constraint on report_date enforces idempotency — re-running
    on the same day raises IntegrityError, which the caller converts
    to exit code 4.

    `legacy_present` becomes True once Deliverable 1's diff has run
    cleanly for at least one sport (v1 and v2 both produced a summed
    diff). Set to False when the diff didn't run — the row still gets
    written for Deliverable 2's telemetry, but downstream readers
    know the legacy comparison dimension isn't there for that row.
    """
    import json
    from sqlalchemy.exc import IntegrityError

    if async_session is None:
        raise RuntimeError("async_session unavailable; cannot write report.")

    sql = text(
        "INSERT INTO sp.daily_diff_reports "
        "  (report_date, window_start, window_end, total_records_scanned, "
        "   metrics, scope_filter_version, report_json, "
        "   legacy_comparison_present) "
        "VALUES "
        "  (:report_date, :window_start, :window_end, :total, "
        "   CAST(:metrics AS jsonb), :scope_filter_version, "
        "   CAST(:report_json AS jsonb), :legacy_present)"
    )

    async with async_session() as session:
        try:
            async with session.begin():
                await session.execute(sql, {
                    "report_date": report_date,
                    "window_start": window_start,
                    "window_end": window_end,
                    "total": total_records,
                    "metrics": json.dumps(metrics),
                    "scope_filter_version": SCOPE_FILTER_VERSION,
                    "report_json": json.dumps(report_json),
                    "legacy_present": legacy_present,
                })
        except IntegrityError:
            return 4
    return 0


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
    preflight_rc = await _pattern_d_pre_flight()
    if preflight_rc != 0:
        return preflight_rc

    if async_session is None:
        print("ERROR: DATABASE_URL not set or engine unavailable.",
              file=sys.stderr)
        return 1

    # 2026-08-08 (#47): load the cohort snapshot the serving web
    # process persisted to cache_blobs['v4_cohort_snapshot'].
    # Populates _COHORT_SNAPSHOT_SET (used by the _is_v4_cohort
    # shim in _diff_pairings) + _COHORT_SNAPSHOT_META (surfaced in
    # report_json for provenance). Called ONCE per pass, before
    # any classifier work.
    await _load_cohort_snapshot_from_cache_blob()

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

    # Measurement pass.
    metrics, report_json, total_records, legacy_present = await _measure(
        window_start, window_end,
    )

    # Empty window — surface as exit code 5 per the docstring contract.
    # Caller is the Railway cron; an empty window means upstream
    # ingestion failed to supply data and the operator should
    # investigate rather than silently write a zero-row report.
    if total_records == 0:
        print(
            "WARN: daily_diff window contained zero records. "
            "Skipping write to sp.daily_diff_reports.",
            file=sys.stderr,
        )
        log.warning(
            "daily_diff.empty_window",
            window_start=window_start.isoformat(),
            window_end=window_end.isoformat(),
        )
        return 5

    # report_date = window_end's UTC date.
    write_rc = await _write_report(
        window_start=window_start,
        window_end=window_end,
        metrics=metrics,
        report_json=report_json,
        total_records=total_records,
        report_date=window_end.date(),
        legacy_present=legacy_present,
    )
    if write_rc != 0:
        log.warning(
            "daily_diff.write_failed",
            exit_code=write_rc,
            report_date=window_end.date().isoformat(),
        )
        return write_rc

    log.info(
        "daily_diff.done",
        total_records=total_records,
        elapsed_seconds=round(time.monotonic() - started, 2),
        matcher_capability_rate=(
            metrics["scope_filtered"]["matcher_capability_rate_overall"]
        ),
    )
    return 0


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
