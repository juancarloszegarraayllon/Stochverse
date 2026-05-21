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
# Issue #174, prop-market vocabulary additions per Issue #160, etc.).
# Stamped into sp.daily_diff_reports.scope_filter_version so historical
# reports can be re-interpreted post-rule-change.
SCOPE_FILTER_VERSION = "v0.1.0"


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
        "auto_apply_rate_overall": _safe_rate(auto_apply_total, total),
        "auto_apply_rate_per_sport": {
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


async def _measure(
    window_start: datetime, window_end: datetime,
) -> tuple[dict, int]:
    """Run the measurement pass against the window.

    Returns (metrics_dict, total_records_scanned). The metrics dict
    matches the sp.daily_diff_reports.metrics JSONB column shape:

    {
      "scope_filtered": {
        "auto_apply_rate_overall": float,
        "auto_apply_rate_per_sport": {sport: float, ...},
        "per_tier_rate_per_sport": {sport: {bucket: int, ...}, ...},
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
            # Scope_filtered.auto_apply_rate_overall excludes those.
            "auto_apply_rate_overall_unfiltered": _safe_rate(
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
    # histogram (for threshold-calibration analysis), placeholder for
    # Deliverable-1's sample disagreements + legacy comparison.
    report_json = {
        "confidence_histogram": compute_confidence_histogram(confidence_scores),
        "confidence_scores_count": len(confidence_scores),
    }
    return metrics, report_json, overall_records


async def _write_report(
    window_start: datetime,
    window_end: datetime,
    metrics: dict,
    report_json: dict,
    total_records: int,
    report_date,
) -> int:
    """Write one row to sp.daily_diff_reports.

    Unique constraint on report_date enforces idempotency — re-running
    on the same day raises IntegrityError, which the caller converts
    to exit code 4.
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
                    "legacy_present": False,
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
    metrics, report_json, total_records = await _measure(window_start, window_end)

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
        auto_apply_rate=metrics["scope_filtered"]["auto_apply_rate_overall"],
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
