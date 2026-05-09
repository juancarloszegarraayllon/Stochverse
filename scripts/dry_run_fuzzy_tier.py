"""Phase 2D.2.5 — fuzzy-tier dry-run against production records.

Read-only calibration script. Runs FuzzyTierMatcher against
`deferred_to_2d` records (tennis/individual sports) and team-sport
residuals, reports the predicted bucket distribution AND the
empirical cross-provider corroboration rate.

NO DB WRITES. Reads sp.team_aliases, sp.competitions, sp.teams,
sp.fixtures (for the matcher's corroboration check), sp.kalshi_markets
or sp.fl_events. Resolver crons continue to operate at strict@2a.6
+ alias@2c.0 (the 3-tier orchestration is 2D.3).

Goal: validate the 20-40% post-cron-swap corroboration rate
assumption from PHASE_2D_DESIGN.md rev1 Pushback 5 BEFORE 2D.3
commits to the threshold choices. If actual rate diverges,
day-0 numbers and threshold values may need recalibration.

Two reports per record:

  Pass 1 — raw matcher output (corroboration check fires naturally
    via the matcher's _check_corroboration → find_fixture).

  Pass 2 — counterfactual analysis: for every record that the
    matcher routed to FUZZY (auto-apply) WITH corroboration, would
    it still auto-apply if corroboration were absent? Subtract
    CORROBORATION_SCORE (0.30) from confidence and check against
    AUTO_APPLY_THRESHOLD (0.85). Records that would NOT auto-apply
    without corroboration are "corroboration-driven auto-applies"
    — the headline number for the calibration.

Usage:

    DATABASE_URL=<prod-Neon> python scripts/dry_run_fuzzy_tier.py \\
        --provider kalshi --sport-code tennis --limit 600

    # Show top 5 examples per bucket
    DATABASE_URL=<prod-Neon> python scripts/dry_run_fuzzy_tier.py \\
        --provider kalshi --sport-code tennis --limit 600 \\
        --show-examples 5

Or via Makefile:

    make dry-run-fuzzy-tier ARGS="--sport-code tennis --limit 600"
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

# Make project root importable when invoked as `python scripts/...`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@dataclass
class FixtureResult:
    """Per-record output of the fuzzy dry-run."""
    provider_record_id: str
    home_provider_raw: str
    away_provider_raw: str
    home_canonical: str
    away_canonical: str
    confidence: float
    has_corroboration: bool
    bucket: str                          # auto_apply | review_queue | no_match | anchor_failed | extraction_skipped
    counterfactual_bucket_no_corr: str   # bucket if confidence had been reduced by CORROBORATION_SCORE
    fail_reason: Optional[str]


# ── Bucket routing ─────────────────────────────────────────────


def _bucket_from_match(match_result) -> str:
    """Map the matcher's MatchResult to one of our bucket labels."""
    from resolver import ReasonCode
    rc = match_result.reason_code
    if rc == ReasonCode.FUZZY:
        return "auto_apply"
    if rc == ReasonCode.REVIEW_QUEUE:
        return "review_queue"
    if rc == ReasonCode.NO_MATCH:
        fail = (match_result.reason_detail or {}).get("fail_reason", "")
        if fail == "fuzzy_no_team_resemblance":
            return "anchor_failed"
        return "no_match"
    return "no_match"  # defensive


def _counterfactual_bucket(confidence: float, has_corroboration: bool, bucket: str) -> str:
    """If the matcher routed this to FUZZY (auto-apply) and corroboration
    fired, would it still auto-apply if we subtracted the corroboration
    contribution?

    For non-auto-apply buckets, return the same bucket (no counterfactual
    needed). For auto-apply with corroboration, recompute.
    """
    from resolver.alias_tier import AUTO_APPLY_THRESHOLD, REVIEW_QUEUE_THRESHOLD
    from resolver.fuzzy_tier import CORROBORATION_SCORE

    if not (bucket == "auto_apply" and has_corroboration):
        return bucket

    counterfactual = confidence - CORROBORATION_SCORE
    if counterfactual >= AUTO_APPLY_THRESHOLD:
        return "auto_apply"
    if counterfactual >= REVIEW_QUEUE_THRESHOLD:
        return "review_queue"
    return "no_match"


# ── Main ───────────────────────────────────────────────────────


async def main(
    *,
    provider: str,
    sport_code: str,
    limit: Optional[int],
    show_examples: int,
    session_factory=None,
) -> int:
    from sqlalchemy import text

    if session_factory is None:
        from db import async_session, DATABASE_URL
        if not DATABASE_URL or async_session is None:
            print("ERROR: DATABASE_URL not set; dry-run requires Postgres.", file=sys.stderr)
            return 2
        session_factory = async_session

    if provider not in ("fl", "kalshi"):
        print(f"ERROR: --provider must be 'fl' or 'kalshi', got {provider!r}", file=sys.stderr)
        return 2

    from resolver import (
        FuzzyTierMatcher, FLResolverModule, KalshiResolverModule,
        CandidateIndex,
    )
    from observability import get_logger

    sport_code_lower = sport_code.lower()

    log = get_logger("dry_run.fuzzy_tier")
    started = time.monotonic()
    log.info(
        "dry_run.fuzzy_tier.start",
        provider=provider,
        sport_code=sport_code_lower,
        limit=limit,
    )

    extractor = (
        KalshiResolverModule() if provider == "kalshi" else FLResolverModule()
    )

    # ── Step 1: build matcher (bulk-load CandidateIndex + sport map) ──
    async with session_factory() as session:
        sport_row = (await session.execute(text(
            "SELECT id, code, name FROM sp.sports WHERE LOWER(code) = :c"
        ).bindparams(c=sport_code_lower))).first()
        if sport_row is None:
            print(f"ERROR: sp.sports has no row for code={sport_code_lower!r}", file=sys.stderr)
            return 3
        sport_id = sport_row.id
        sport_name = sport_row.name

        # Sport map (lowercase code + canonical name → sp.sports.id)
        all_sports = (await session.execute(
            text("SELECT id, code, name FROM sp.sports")
        )).all()
        sport_id_by_code_or_name: dict[str, int] = {}
        for row in all_sports:
            sport_id_by_code_or_name[row.code] = row.id
            sport_id_by_code_or_name[row.name] = row.id

        # CandidateIndex (with multi-interpretation surname index from 2D.1)
        candidate_index = await CandidateIndex.load_all(session)

    matcher = FuzzyTierMatcher(
        candidates=candidate_index,
        sport_id_by_code_or_name=sport_id_by_code_or_name,
    )

    print(f"\nCandidate index loaded: {candidate_index.stats()}")

    # ── Step 2: fetch unresolved records for the sport ────────────
    async with session_factory() as session:
        if provider == "kalshi":
            sql = (
                "SELECT ticker AS pk, raw_payload "
                "FROM sp.kalshi_markets "
                "WHERE fixture_id IS NULL "
                "  AND ( "
                "    (raw_payload->>'_is_sport')::boolean = true "
                "    OR raw_payload->>'category' = 'Sports' "
                "  ) "
                "  AND raw_payload->>'_sport' = :sport_name "
                "ORDER BY last_seen_at DESC"
            )
            params = {"sport_name": sport_name}
        else:
            sql = (
                "SELECT fl_event_id AS pk, raw_payload "
                "FROM sp.fl_events "
                "WHERE fixture_id IS NULL "
                "  AND sport_id = :sport_id "
                "ORDER BY last_seen_at DESC"
            )
            params = {"sport_id": sport_id}
        if limit:
            sql += " LIMIT :limit"
            params["limit"] = int(limit)

        unresolved_rows = (await session.execute(text(sql).bindparams(**params))).all()

    print(f"Unresolved {provider} {sport_name} records to score: {len(unresolved_rows)}")
    if not unresolved_rows:
        print("Nothing to score. Exiting.")
        return 0

    # ── Step 3: run the matcher per record ────────────────────────
    bucket_counts: dict[str, int] = defaultdict(int)
    counterfactual_counts: dict[str, int] = defaultdict(int)
    examples: dict[str, list[FixtureResult]] = defaultdict(list)
    extraction_skipped = 0
    crashes = 0
    corroboration_anchored = 0
    no_corroboration_anchored = 0

    async with session_factory() as session:
        for row in unresolved_rows:
            try:
                if provider == "fl":
                    signal = extractor.extract_signal(
                        row.raw_payload, sport=sport_name,
                    )
                else:
                    signal = extractor.extract_signal(row.raw_payload)
            except Exception as e:
                crashes += 1
                log.warning(
                    "dry_run.fuzzy_tier.extract_failed",
                    pk=row.pk, error=str(e)[:200],
                )
                continue

            if signal is None:
                extraction_skipped += 1
                continue

            try:
                match_result = await matcher.match(session, signal)
            except Exception as e:
                crashes += 1
                log.warning(
                    "dry_run.fuzzy_tier.match_failed",
                    pk=row.pk, error=str(e)[:200],
                )
                continue

            bucket = _bucket_from_match(match_result)
            has_corroboration = bool(
                (match_result.reason_detail or {}).get(
                    "has_cross_provider_corroboration"
                )
            )
            counterfactual = _counterfactual_bucket(
                match_result.confidence, has_corroboration, bucket,
            )

            bucket_counts[bucket] += 1
            counterfactual_counts[counterfactual] += 1

            # Track corroboration of anchored records (excludes anchor_failed
            # and extraction_skipped which can't have corroboration)
            if bucket in ("auto_apply", "review_queue", "no_match"):
                # Note: bucket=='no_match' includes both 'below_review_threshold'
                # and 'fuzzy_no_existing_fixture'. Both can have or not have
                # corroboration depending on the record.
                if has_corroboration:
                    corroboration_anchored += 1
                else:
                    no_corroboration_anchored += 1

            result = FixtureResult(
                provider_record_id=str(row.pk),
                home_provider_raw=signal.home_team_candidates[0].raw if signal.home_team_candidates else "",
                away_provider_raw=signal.away_team_candidates[0].raw if signal.away_team_candidates else "",
                home_canonical=(match_result.reason_detail or {}).get("home_canonical", ""),
                away_canonical=(match_result.reason_detail or {}).get("away_canonical", ""),
                confidence=match_result.confidence,
                has_corroboration=has_corroboration,
                bucket=bucket,
                counterfactual_bucket_no_corr=counterfactual,
                fail_reason=(match_result.reason_detail or {}).get("fail_reason"),
            )
            if len(examples[bucket]) < show_examples:
                examples[bucket].append(result)

    elapsed = time.monotonic() - started
    total_input = len(unresolved_rows)
    total_anchored = corroboration_anchored + no_corroboration_anchored

    # ── Step 4: report ────────────────────────────────────────────
    print(f"\nFuzzy-tier dry-run complete in {elapsed:.1f}s.")
    print(f"  records_input:       {total_input:>6}")
    print(f"  extraction_skipped:  {extraction_skipped:>6}  ({_pct(extraction_skipped, total_input)})")
    print(f"  crashed:             {crashes:>6}")
    print()

    print("Bucket distribution (matcher actual output, with corroboration):")
    for bucket in ("auto_apply", "review_queue", "no_match", "anchor_failed"):
        n = bucket_counts.get(bucket, 0)
        print(f"    {bucket:<14} {n:>6}  ({_pct(n, total_input)})")
    print()

    if total_anchored > 0:
        print(f"Corroboration analysis (of {total_anchored} anchored records):")
        print(f"    with corroboration:    {corroboration_anchored:>6}  ({_pct(corroboration_anchored, total_anchored)})")
        print(f"    without corroboration: {no_corroboration_anchored:>6}  ({_pct(no_corroboration_anchored, total_anchored)})")
        print()

    # Counterfactual: how many auto_apply rows depend on corroboration?
    actual_auto = bucket_counts.get("auto_apply", 0)
    counterfactual_auto = counterfactual_counts.get("auto_apply", 0)
    corr_dependent = actual_auto - counterfactual_auto
    if actual_auto > 0:
        print("Counterfactual auto-apply analysis:")
        print(f"    actual auto_applies (with corroboration): {actual_auto:>6}")
        print(f"    would auto-apply WITHOUT corroboration:    {counterfactual_auto:>6}  ({_pct(counterfactual_auto, actual_auto)} of auto-applies)")
        print(f"    corroboration-dependent auto-applies:      {corr_dependent:>6}  ({_pct(corr_dependent, actual_auto)} of auto-applies)")
        print()
        print("Counterfactual bucket distribution (if corroboration absent):")
        for bucket in ("auto_apply", "review_queue", "no_match", "anchor_failed"):
            n = counterfactual_counts.get(bucket, 0)
            print(f"    {bucket:<14} {n:>6}  ({_pct(n, total_input)})")
        print()

    # Calibration interpretation
    if total_anchored > 0:
        empirical_corroboration_rate = 100.0 * corroboration_anchored / total_anchored
        print(f"Empirical corroboration rate: {empirical_corroboration_rate:.1f}%")
        design_low, design_high = 20.0, 40.0
        if empirical_corroboration_rate < design_low:
            print(
                f"  WARNING: BELOW design rev1 range ({design_low:.0f}-{design_high:.0f}%). "
                "Day-0 prediction was OPTIMISTIC. Before 2D.3, consider "
                "either: (a) accepting smaller auto-apply gain, "
                "(b) bumping CORROBORATION_SCORE to e.g. +0.40, or "
                "(c) lowering AUTO_APPLY_THRESHOLD."
            )
        elif empirical_corroboration_rate > design_high:
            print(
                f"  WARNING: ABOVE design rev1 range ({design_low:.0f}-{design_high:.0f}%). "
                "Day-0 prediction was CONSERVATIVE. Auto-apply gain is "
                "larger than predicted; threshold choices remain valid."
            )
        else:
            print(
                f"  Within design rev1 range ({design_low:.0f}-{design_high:.0f}%). "
                "Threshold choices validated."
            )
        print()

    if show_examples > 0:
        print(f"Top {show_examples} examples per bucket:")
        for bucket in ("auto_apply", "review_queue", "no_match", "anchor_failed"):
            ex = examples.get(bucket, [])
            if not ex:
                continue
            print(f"  {bucket}:")
            for r in ex:
                corr = "+corr" if r.has_corroboration else "no-corr"
                home_label = r.home_canonical or "(no candidate)"
                away_label = r.away_canonical or "(no candidate)"
                fail = f" [fail={r.fail_reason}]" if r.fail_reason else ""
                print(
                    f"    [{r.confidence:.3f} {corr}] "
                    f"{r.home_provider_raw!r} → {home_label!r}; "
                    f"{r.away_provider_raw!r} → {away_label!r}{fail}"
                )

    log.info(
        "dry_run.fuzzy_tier.complete",
        elapsed_sec=round(elapsed, 1),
        records_input=total_input,
        extraction_skipped=extraction_skipped,
        crashed=crashes,
        bucket_counts=dict(bucket_counts),
        corroboration_anchored=corroboration_anchored,
        no_corroboration_anchored=no_corroboration_anchored,
        actual_auto_applies=actual_auto,
        counterfactual_auto_applies=counterfactual_auto,
        corroboration_dependent_auto_applies=corr_dependent,
    )
    return 0


def _pct(n: int, total: int) -> str:
    if total == 0:
        return "0.0%"
    return f"{100.0 * n / total:.1f}%"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--provider", required=True, choices=["fl", "kalshi"],
        help="Provider whose unresolved records to score.",
    )
    parser.add_argument(
        "--sport-code", required=True,
        help="Canonical sp.sports.code (lowercase). e.g., 'tennis', 'soccer'.",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Cap on records to score. Use to keep dry-runs fast.",
    )
    parser.add_argument(
        "--show-examples", type=int, default=3,
        help="Number of example records to print per bucket. 0 = none.",
    )
    args = parser.parse_args()
    rc = asyncio.run(main(
        provider=args.provider,
        sport_code=args.sport_code,
        limit=args.limit,
        show_examples=args.show_examples,
    ))
    sys.exit(rc)
