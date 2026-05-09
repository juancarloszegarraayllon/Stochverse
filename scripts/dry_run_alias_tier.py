"""Phase 2C.2.5 — alias-tier dry-run against production records.

Read-only calibration script. Runs the structurally-normalize +
fixture-level-score pipeline (Phase 2C.2 building blocks) against
unresolved provider records and reports the predicted bucket
distribution: auto_apply / review_queue / no_match / anchor_failed
/ extraction_skipped.

NO DB WRITES. The script reads sp.kalshi_markets / sp.fl_events,
sp.teams, and (optionally) sp.fixtures for cross-provider
corroboration. Nothing is persisted; the resolver runs continue
to operate at strict@2a.6.

Goal: stress-test the threshold choice from PR #92 before 2C.3
commits to it. The user's calibration concern (sign-off on PR #90
rev1): tennis matches without corroboration may score 0.50 (no_match)
or 0.70 (review-queue boundary) — most won't auto-apply through
the strict 0.85 threshold. The dry-run quantifies this.

Two passes per record, both reported:
  1. NO corroboration — pure name match. Most pessimistic case.
  2. WITH corroboration — find_fixture lookup against sp.fixtures
     adds +0.20 when the candidate (home_id, away_id) pair has an
     existing fixture at the kickoff window.

The two-pass output answers: how much of alias-tier auto-apply
gain depends on cross-provider corroboration?

Usage:

    DATABASE_URL=<prod-Neon> python scripts/dry_run_alias_tier.py \\
        --provider kalshi --sport-code tennis --limit 600

    # Show top 5 examples per bucket
    DATABASE_URL=<prod-Neon> python scripts/dry_run_alias_tier.py \\
        --provider kalshi --sport-code tennis --limit 600 \\
        --show-examples 5

    # Skip the corroboration pass (faster; one-pass output)
    DATABASE_URL=<prod-Neon> python scripts/dry_run_alias_tier.py \\
        --provider kalshi --sport-code tennis --skip-corroboration

Or via Makefile:

    make dry-run-alias-tier ARGS="--sport-code tennis --limit 600"
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
from typing import Iterable, Optional

# Make project root importable when invoked as `python scripts/...`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Fixture-level scoring (inline; not extending scorer.py) ────
#
# The 2C.2 scorer.score_pair operates on a single side. Fixture-level
# scoring per design doc Q C combines: anchor floor 0.50 (BOTH sides
# anchored) + avg(home_remainder_ratio, away_remainder_ratio) linear
# to +0.30 + 0.20 corroboration. This script computes the fixture
# composition inline — when 2C.3 ships, it'll be promoted to
# scorer.score_fixture as a public API.


@dataclass
class SideResult:
    """Per-side intermediate result during dry-run scoring."""
    team_id: Optional[uuid.UUID]
    anchor_passed: bool
    remainder_ratio: float       # 0.0 if anchor failed or no remainder
    candidate_canonical: str     # for the per-bucket example output


@dataclass
class FixtureResult:
    """Per-record output of the dry-run scorer."""
    provider_record_id: str
    home_provider_raw: str
    away_provider_raw: str
    home: SideResult
    away: SideResult
    confidence_no_corr: float
    confidence_with_corr: float
    has_corroboration: bool
    bucket_no_corr: str          # auto_apply | review_queue | no_match | anchor_failed
    bucket_with_corr: str


@dataclass
class CandidateTeam:
    team_id: uuid.UUID
    canonical_name: str
    structured: "StructuredName"   # forward ref; imported lazily inside main


# ── Bucket routing (same constants as 2C.2 scorer) ─────────────


def _bucket(confidence: float, anchor_passed: bool) -> str:
    if not anchor_passed:
        return "anchor_failed"
    from resolver.alias_tier import AUTO_APPLY_THRESHOLD, REVIEW_QUEUE_THRESHOLD
    if confidence >= AUTO_APPLY_THRESHOLD:
        return "auto_apply"
    if confidence >= REVIEW_QUEUE_THRESHOLD:
        return "review_queue"
    return "no_match"


def _score_fixture(
    home: SideResult,
    away: SideResult,
    *,
    has_corroboration: bool,
    is_personal: bool,
) -> float:
    """Inline fixture-level confidence per design doc Q C."""
    from resolver.alias_tier import (
        ANCHOR_SCORE, CORROBORATION_SCORE,
        PERSONAL_TOKEN_SET_THRESHOLD, TEAM_TOKEN_SET_THRESHOLD,
    )

    if not (home.anchor_passed and away.anchor_passed):
        return 0.0

    confidence = ANCHOR_SCORE  # 0.50

    threshold = PERSONAL_TOKEN_SET_THRESHOLD if is_personal else TEAM_TOKEN_SET_THRESHOLD
    avg_ratio = (home.remainder_ratio + away.remainder_ratio) / 2.0
    if avg_ratio >= threshold:
        # Same linear formula as scorer._linear_contribution.
        span = 1.0 - threshold
        progress = (avg_ratio - threshold) / span
        confidence += 0.20 + progress * 0.10

    if has_corroboration:
        confidence += CORROBORATION_SCORE

    return round(confidence, 4)


# ── Side-level matching ────────────────────────────────────────


def _best_side_match(
    provider_struct: "StructuredName",
    candidates: list[CandidateTeam],
) -> SideResult:
    """Pick the best (team_id, ratio) for one side.

    Personal path: pre-filter by exact surname match, score remainder
    token-set ratio. Take max.

    Team path: score whole-string token-set ratio against every
    candidate. Take max above threshold; below threshold = anchor_failed.
    """
    from rapidfuzz import fuzz
    from resolver.alias_tier import (
        PERSONAL_TOKEN_SET_THRESHOLD, TEAM_TOKEN_SET_THRESHOLD,
    )

    if provider_struct.is_personal:
        # Pre-filter: exact surname match.
        prov_remainder = " ".join(provider_struct.other_tokens)
        best: Optional[SideResult] = None
        for c in candidates:
            if c.structured.surname != provider_struct.surname:
                continue
            cand_remainder = " ".join(c.structured.other_tokens)
            if not prov_remainder or not cand_remainder:
                ratio = 0.0
            else:
                ratio = fuzz.token_set_ratio(prov_remainder, cand_remainder) / 100.0
            if best is None or ratio > best.remainder_ratio:
                best = SideResult(
                    team_id=c.team_id, anchor_passed=True,
                    remainder_ratio=ratio,
                    candidate_canonical=c.canonical_name,
                )
        if best is not None:
            # Below-threshold ratio still anchors (surname matched);
            # the fixture-level scorer decides routing.
            return best
        return SideResult(
            team_id=None, anchor_passed=False,
            remainder_ratio=0.0, candidate_canonical="",
        )

    # Team path: no surname — score every candidate, take max.
    prov = " ".join(provider_struct.other_tokens)
    if not prov:
        return SideResult(
            team_id=None, anchor_passed=False,
            remainder_ratio=0.0, candidate_canonical="",
        )
    best_ratio = 0.0
    best_team_id: Optional[uuid.UUID] = None
    best_canonical = ""
    for c in candidates:
        cand = " ".join(c.structured.other_tokens)
        if not cand:
            continue
        ratio = fuzz.token_set_ratio(prov, cand) / 100.0
        if ratio > best_ratio:
            best_ratio = ratio
            best_team_id = c.team_id
            best_canonical = c.canonical_name
    anchor_passed = best_ratio >= TEAM_TOKEN_SET_THRESHOLD
    return SideResult(
        team_id=best_team_id if anchor_passed else None,
        anchor_passed=anchor_passed,
        remainder_ratio=best_ratio,
        candidate_canonical=best_canonical if anchor_passed else "",
    )


# ── Main ───────────────────────────────────────────────────────


async def main(
    *,
    provider: str,
    sport_code: str,
    limit: Optional[int],
    show_examples: int,
    skip_corroboration: bool,
    session_factory=None,           # injectable for tests
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

    from resolver.alias_tier import (
        INDIVIDUAL_SPORT_CODES,
        StructuredName,
        structurally_normalize,
    )
    from resolver import FLResolverModule, KalshiResolverModule
    from resolver.fixtures import find_fixture
    from observability import get_logger

    sport_code_lower = sport_code.lower()
    is_personal = sport_code_lower in INDIVIDUAL_SPORT_CODES

    log = get_logger("dry_run.alias_tier")
    started = time.monotonic()
    log.info(
        "dry_run.alias_tier.start",
        provider=provider,
        sport_code=sport_code_lower,
        is_personal=is_personal,
        limit=limit,
        skip_corroboration=skip_corroboration,
    )

    extractor = (
        KalshiResolverModule() if provider == "kalshi" else FLResolverModule()
    )

    # ── Step 1: bulk-load candidate sp.teams for this sport ──────
    async with session_factory() as session:
        sport_row = (await session.execute(text(
            "SELECT id, code, name FROM sp.sports WHERE LOWER(code) = :c"
        ).bindparams(c=sport_code_lower))).first()
        if sport_row is None:
            print(f"ERROR: sp.sports has no row for code={sport_code_lower!r}", file=sys.stderr)
            return 3
        sport_id = sport_row.id
        sport_name = sport_row.name

        team_rows = (await session.execute(text(
            "SELECT id, canonical_name FROM sp.teams WHERE sport_id = :s"
        ).bindparams(s=sport_id))).all()

        candidates: list[CandidateTeam] = []
        for row in team_rows:
            structured = structurally_normalize(
                row.canonical_name, sport_code=sport_code_lower,
            )
            if structured is None:
                continue
            candidates.append(CandidateTeam(
                team_id=row.id,
                canonical_name=row.canonical_name,
                structured=structured,
            ))

        # ── Step 2: bulk-load unresolved provider records for this sport ──
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

    print(f"\nCandidate sp.teams loaded: {len(candidates)} (sport={sport_name})")
    print(f"Unresolved {provider} records to score: {len(unresolved_rows)}")
    if not unresolved_rows:
        print("Nothing to score. Exiting.")
        return 0

    # ── Step 3: score each record (no DB writes) ────────────────
    bucket_no_corr: dict[str, int] = defaultdict(int)
    bucket_with_corr: dict[str, int] = defaultdict(int)
    examples_no_corr: dict[str, list[FixtureResult]] = defaultdict(list)
    examples_with_corr: dict[str, list[FixtureResult]] = defaultdict(list)
    extraction_skipped = 0
    crashes = 0

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
                    "dry_run.alias_tier.extract_failed",
                    pk=row.pk, error=str(e)[:200],
                )
                continue

            if signal is None:
                extraction_skipped += 1
                continue

            # Pick the highest-weight TeamCandidate per side, normalize.
            home_struct = _best_normalized_provider_side(
                signal.home_team_candidates, sport_code_lower,
            )
            away_struct = _best_normalized_provider_side(
                signal.away_team_candidates, sport_code_lower,
            )
            if home_struct is None or away_struct is None:
                bucket_no_corr["anchor_failed"] += 1
                bucket_with_corr["anchor_failed"] += 1
                continue

            home_match = _best_side_match(home_struct, candidates)
            away_match = _best_side_match(away_struct, candidates)

            conf_no_corr = _score_fixture(
                home_match, away_match,
                has_corroboration=False, is_personal=is_personal,
            )
            anchor_passed = home_match.anchor_passed and away_match.anchor_passed

            # Optional corroboration pass.
            has_corroboration = False
            if (not skip_corroboration) and anchor_passed and signal.kickoff_at:
                # Try (home, away) and (away, home).
                fid, _ = await find_fixture(
                    session,
                    home_team_id=home_match.team_id,
                    away_team_id=away_match.team_id,
                    kickoff_at=signal.kickoff_at,
                    drift_sec=30 * 60,
                )
                if fid is None:
                    fid, _ = await find_fixture(
                        session,
                        home_team_id=away_match.team_id,
                        away_team_id=home_match.team_id,
                        kickoff_at=signal.kickoff_at,
                        drift_sec=30 * 60,
                    )
                has_corroboration = fid is not None

            conf_with_corr = _score_fixture(
                home_match, away_match,
                has_corroboration=has_corroboration, is_personal=is_personal,
            ) if not skip_corroboration else conf_no_corr

            b_no = _bucket(conf_no_corr, anchor_passed)
            b_with = _bucket(conf_with_corr, anchor_passed)
            bucket_no_corr[b_no] += 1
            bucket_with_corr[b_with] += 1

            result = FixtureResult(
                provider_record_id=str(row.pk),
                home_provider_raw=signal.home_team_candidates[0].raw if signal.home_team_candidates else "",
                away_provider_raw=signal.away_team_candidates[0].raw if signal.away_team_candidates else "",
                home=home_match, away=away_match,
                confidence_no_corr=conf_no_corr,
                confidence_with_corr=conf_with_corr,
                has_corroboration=has_corroboration,
                bucket_no_corr=b_no,
                bucket_with_corr=b_with,
            )
            if len(examples_no_corr[b_no]) < show_examples:
                examples_no_corr[b_no].append(result)
            if len(examples_with_corr[b_with]) < show_examples:
                examples_with_corr[b_with].append(result)

    elapsed = time.monotonic() - started

    # ── Step 4: report ──────────────────────────────────────────
    total_scored = sum(bucket_no_corr.values())
    total_input = len(unresolved_rows)

    print(f"\nDry-run complete in {elapsed:.1f}s.")
    print(f"  records_input:       {total_input:>6}")
    print(f"  records_scored:      {total_scored:>6}")
    print(f"  extraction_skipped:  {extraction_skipped:>6}")
    print(f"  crashed:             {crashes:>6}")
    print()

    def _print_dist(label, dist):
        print(f"{label}:")
        for bucket in ("auto_apply", "review_queue", "no_match", "anchor_failed"):
            n = dist.get(bucket, 0)
            pct = 100.0 * n / total_scored if total_scored else 0
            print(f"    {bucket:<14} {n:>6}  ({pct:>5.1f}%)")

    _print_dist("Without corroboration", bucket_no_corr)
    if not skip_corroboration:
        print()
        _print_dist("With corroboration", bucket_with_corr)
        # Diff: how much corroboration moved
        delta_auto = bucket_with_corr.get("auto_apply", 0) - bucket_no_corr.get("auto_apply", 0)
        delta_review = bucket_with_corr.get("review_queue", 0) - bucket_no_corr.get("review_queue", 0)
        print(f"\n  corroboration delta:")
        print(f"    auto_apply:   {delta_auto:+d}")
        print(f"    review_queue: {delta_review:+d}")

    if show_examples > 0:
        print(f"\nTop {show_examples} examples per bucket (no-corroboration mode):")
        for bucket in ("auto_apply", "review_queue", "no_match", "anchor_failed"):
            ex = examples_no_corr.get(bucket, [])
            if not ex:
                continue
            print(f"  {bucket}:")
            for r in ex:
                home_match_label = (
                    r.home.candidate_canonical or "(none)"
                )
                away_match_label = (
                    r.away.candidate_canonical or "(none)"
                )
                print(
                    f"    [{r.confidence_no_corr:.3f}] "
                    f"{r.home_provider_raw!r} → {home_match_label!r}; "
                    f"{r.away_provider_raw!r} → {away_match_label!r}"
                )

    log.info(
        "dry_run.alias_tier.complete",
        elapsed_sec=round(elapsed, 1),
        records_input=total_input,
        records_scored=total_scored,
        extraction_skipped=extraction_skipped,
        crashes=crashes,
        bucket_no_corr=dict(bucket_no_corr),
        bucket_with_corr=dict(bucket_with_corr) if not skip_corroboration else None,
    )
    return 0


def _best_normalized_provider_side(
    team_candidates,
    sport_code: str,
) -> Optional["StructuredName"]:
    """Pick the highest-weight non-empty TeamCandidate's raw form,
    structurally-normalize it. Returns None if no candidate yields a
    non-None StructuredName."""
    from resolver.alias_tier import structurally_normalize
    sorted_cands = sorted(team_candidates, key=lambda c: c.weight, reverse=True)
    for cand in sorted_cands:
        struct = structurally_normalize(cand.raw, sport_code=sport_code)
        if struct is not None:
            return struct
    return None


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
    parser.add_argument(
        "--skip-corroboration", action="store_true",
        help="Skip the with-corroboration pass (faster; one-pass output).",
    )
    args = parser.parse_args()
    rc = asyncio.run(main(
        provider=args.provider,
        sport_code=args.sport_code,
        limit=args.limit,
        show_examples=args.show_examples,
        skip_corroboration=args.skip_corroboration,
    ))
    sys.exit(rc)
