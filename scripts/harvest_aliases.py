"""Production-failure alias harvester (Phase 2D.5-A engine, Component 2).

Mines REAL provider strings from `sp.resolution_log` no-match /
asymmetric-anchor-failure records, fuzzy-matches them against a
known team roster, and emits candidate aliases ranked by occurrence
count. Pipes the candidate set through the amendment #22 collision
audit (`resolver.collision_audit`) so collisions are dropped pre-emit
rather than caught post-apply.

## Why this exists

Phase 2D.5-A workstreams #1-9 manually curated aliases from Wikipedia
sponsor lists, FL discovery query notes, and operator memory. That
produced ~90 raw aliases per league at the cost of ~30 min operator
time and several post-apply collision-remediation rounds (Day-33 HEBA,
Day-34 VTB, Day-35 EuroLeague+ABA).

Real provider strings from production are higher-quality input:
  - They're what FL / Kalshi actually send (not what we guessed they'd
    send)
  - The occurrence_count tells us which aliases matter by volume
  - Variants we'd never invent (asterisk suffixes, missing diacritics,
    sponsor abbreviations, transliterations) surface organically
  - Amendment #21 (Pattern A.2 sequencing: production discovery before
    authoritative-source) generalizes to alias variants the same way
    it does to roster discovery

## Pipeline

  1. Caller supplies a target roster: list of (team_id, canonical_name)
     plus optional FL-canonical short forms for stronger fuzzy match.
  2. Query `sp.resolution_log` for no_match / asymmetric records in
     a configurable window (default 7d) for the given sport_id.
  3. Extract unresolved provider name strings (HOME/AWAY).
  4. Normalize via `resolver._normalize.normalize_name`.
  5. Fuzzy-match each unresolved normalized string against the
     roster's reference set using rapidfuzz. Configurable threshold
     (default 0.75). Higher → fewer candidates, higher precision.
  6. Aggregate candidates by (alias_normalized, target_team_id) with
     occurrence_count_7d.
  7. Pipe through `audit_alias_collisions` — clean candidates emit,
     colliders flagged.
  8. Emit a Markdown report ranked by occurrence_count + a JSON file
     for tooling.

## Usage

    DATABASE_URL=<url> python scripts/harvest_aliases.py \\
        --sport-id 3 \\
        --roster-json ./bbl_roster.json \\
        --window-days 7 \\
        --fuzzy-threshold 0.75 \\
        --out-dir ./harvest_output/

Where `--roster-json` is a list of dicts:
    [
      {"team_id": "<uuid>", "canonical_name": "Bayern München",
       "reference_forms": ["Bayern", "FC Bayern Munich Basketball"]},
      ...
    ]

`reference_forms` (optional) extends the fuzzy-match reference set
beyond canonical_name — useful when FL's short-form provider string
("Bonn", "Bayern") differs from the sp.teams canonical.

## Exit codes

  0 — success (outputs written; check report for clean/colliding counts)
  1 — DATABASE_URL missing / engine unavailable
  2 — bad CLI args
  3 — roster file invalid (not a list of dicts with team_id +
      canonical_name)
  4 — no failure records found in window (window may be too short or
      sport_id wrong)

## Pilot scope

Read-only audit + emit. No DB writes. Operator reviews `harvest_*.md`
and selects which candidates to layer into the manifest before apply.

Out of scope for this build:
  - Batch multi-league orchestration (Component 3, next)
  - Auto-apply
  - Scheduled/cron execution
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402
from rapidfuzz import fuzz  # noqa: E402

from db import async_session  # noqa: E402
from observability import get_logger  # noqa: E402
from resolver._normalize import normalize_name  # noqa: E402
from resolver.collision_audit import (  # noqa: E402
    ProposedAlias,
    audit_alias_collisions,
    propose_alias,
)


# ──────────────────────────────────────────────────────────────────────
# Data shapes
# ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RosterEntry:
    """One target team for the harvest."""
    team_id: str
    canonical_name: str
    reference_forms: tuple[str, ...] = field(default_factory=tuple)


@dataclass
class Candidate:
    """A proposed alias derived from production-failure mining."""
    alias_normalized: str
    raw_example: str  # one representative raw form
    raw_examples: list[str] = field(default_factory=list)  # all forms
    target_team_id: str = ""
    target_canonical: str = ""
    occurrence_count: int = 0
    fuzzy_confidence: float = 0.0
    matched_reference: str = ""  # the reference string that matched


# ──────────────────────────────────────────────────────────────────────
# Roster loading
# ──────────────────────────────────────────────────────────────────────


def load_roster(path: Path) -> list[RosterEntry]:
    raw = json.loads(path.read_text())
    if not isinstance(raw, list):
        raise ValueError("roster-json must be a JSON list")
    out: list[RosterEntry] = []
    for idx, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ValueError(f"roster entry {idx} is not a dict")
        team_id = entry.get("team_id")
        canonical = entry.get("canonical_name")
        if not team_id or not canonical:
            raise ValueError(
                f"roster entry {idx} missing team_id or canonical_name"
            )
        refs = entry.get("reference_forms") or []
        if not isinstance(refs, list):
            raise ValueError(
                f"roster entry {idx} reference_forms must be a list"
            )
        out.append(RosterEntry(
            team_id=str(team_id),
            canonical_name=str(canonical),
            reference_forms=tuple(str(r) for r in refs),
        ))
    return out


def build_reference_index(
    roster: list[RosterEntry],
) -> list[tuple[str, str, str]]:
    """Flatten the roster into (normalized_reference, raw_reference,
    team_id) triples for fuzzy matching."""
    out: list[tuple[str, str, str]] = []
    for entry in roster:
        for ref in (entry.canonical_name, *entry.reference_forms):
            normed = normalize_name(ref)
            if normed:
                out.append((normed, ref, entry.team_id))
    return out


# ──────────────────────────────────────────────────────────────────────
# Failure mining
# ──────────────────────────────────────────────────────────────────────


async def mine_failure_strings(
    session,
    sport_id: int,
    window_days: int,
    log,
) -> list[tuple[str, str]]:
    """Return list of (raw_provider_string, normalized) extracted
    from no_match / asymmetric records in the past `window_days`.

    Walks `sp.resolution_log.reason_detail` JSON for the home/away
    provider-name fields the resolver records on failure. Defensive
    over multiple possible field names since the resolver has carried
    different shapes across phases.
    """
    sport_name_row = (await session.execute(
        text("SELECT name FROM sp.sports WHERE id = :sid"),
        {"sid": sport_id},
    )).first()
    sport_name = sport_name_row.name if sport_name_row else None
    if not sport_name:
        log.error("harvest.sport_not_found", sport_id=sport_id)
        return []

    # Query failures. We accept multiple reason_codes per the Phase
    # 2D.5-A discovery patterns.
    rows = (await session.execute(
        text(
            "SELECT reason_detail "
            "FROM sp.resolution_log "
            "WHERE reason_detail->>'sport' = :sport_name "
            "  AND reason_code IN ('no_match','review_queue') "
            "  AND decided_at >= NOW() - (:days || ' days')::interval "
            "ORDER BY decided_at DESC "
            "LIMIT 50000"
        ).bindparams(sport_name=sport_name, days=str(window_days)),
    )).all()

    # Defensive field extraction — different phases recorded provider
    # names under different keys.
    PROVIDER_NAME_KEYS = (
        "home_provider_normalized", "away_provider_normalized",
        "home_provider_raw", "away_provider_raw",
        "home_canonical", "away_canonical",
        "home_name", "away_name",
        "home", "away",
    )

    out: list[tuple[str, str]] = []
    for row in rows:
        rd = row.reason_detail
        if not isinstance(rd, dict):
            continue
        for key in PROVIDER_NAME_KEYS:
            v = rd.get(key)
            if isinstance(v, str) and v.strip():
                normed = normalize_name(v)
                if normed:
                    out.append((v.strip(), normed))
    log.info(
        "harvest.failure_strings_mined",
        sport_id=sport_id,
        sport_name=sport_name,
        window_days=window_days,
        row_count=len(rows),
        extracted_strings=len(out),
    )
    return out


# ──────────────────────────────────────────────────────────────────────
# Fuzzy matching
# ──────────────────────────────────────────────────────────────────────


def fuzzy_match_failures(
    failures: list[tuple[str, str]],
    reference_index: list[tuple[str, str, str]],
    threshold: float,
) -> dict[tuple[str, str], Candidate]:
    """For each failure string, find the best matching reference.

    Returns dict keyed on (alias_normalized, target_team_id) →
    aggregated Candidate. Same normalized form targeting the SAME
    team_id aggregates into one candidate with summed occurrences.

    Threshold is rapidfuzz `token_set_ratio / 100` (range 0.0 → 1.0).
    """
    bucket: dict[tuple[str, str], Candidate] = {}

    for raw_failure, norm_failure in failures:
        best_score = 0.0
        best_target_id = ""
        best_reference = ""
        for ref_normed, ref_raw, team_id in reference_index:
            # token_set_ratio handles word-order changes + partial
            # overlap gracefully. Range 0-100.
            score = fuzz.token_set_ratio(norm_failure, ref_normed) / 100.0
            if score > best_score:
                best_score = score
                best_target_id = team_id
                best_reference = ref_raw
        if best_score < threshold:
            continue

        key = (norm_failure, best_target_id)
        c = bucket.get(key)
        if c is None:
            c = Candidate(
                alias_normalized=norm_failure,
                raw_example=raw_failure,
                target_team_id=best_target_id,
                fuzzy_confidence=best_score,
                matched_reference=best_reference,
            )
            bucket[key] = c
        c.occurrence_count += 1
        if raw_failure not in c.raw_examples:
            c.raw_examples.append(raw_failure)
        # Keep the highest confidence seen for this candidate.
        if best_score > c.fuzzy_confidence:
            c.fuzzy_confidence = best_score
            c.matched_reference = best_reference

    return bucket


# ──────────────────────────────────────────────────────────────────────
# Output writers
# ──────────────────────────────────────────────────────────────────────


def write_harvest_report(
    out_dir: Path,
    candidates_clean: list[Candidate],
    candidates_dropped: list[tuple[Candidate, list[dict]]],
    candidates_same_team: list[Candidate],
    sport_id: int,
    window_days: int,
    threshold: float,
    failures_mined: int,
    roster_size: int,
    elapsed_sec: float,
) -> Path:
    path = out_dir / "harvest_report.md"
    lines: list[str] = []
    lines.append("# Production-failure alias harvest report")
    lines.append("")
    lines.append(f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
    lines.append("")
    lines.append("## Parameters")
    lines.append("")
    lines.append(f"- sport_id: {sport_id}")
    lines.append(f"- window: {window_days}d")
    lines.append(f"- fuzzy threshold: {threshold:.2f} (token_set_ratio / 100)")
    lines.append(f"- roster size: {roster_size} teams")
    lines.append(f"- failure strings mined: {failures_mined}")
    lines.append(f"- elapsed: {elapsed_sec:.2f}s")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Clean candidates (safe to emit): {len(candidates_clean)}")
    lines.append(f"- Dropped (collision): {len(candidates_dropped)}")
    lines.append(f"- Same team already present (idempotent): "
                 f"{len(candidates_same_team)}")
    lines.append("")
    lines.append("## Clean candidates (ranked by occurrence count)")
    lines.append("")
    lines.append("| occ | alias_normalized | raw_example | target_team_id | "
                 "matched_reference | conf |")
    lines.append("|---:|---|---|---|---|---:|")
    for c in sorted(candidates_clean,
                    key=lambda x: x.occurrence_count, reverse=True):
        lines.append(
            f"| {c.occurrence_count} | `{c.alias_normalized}` | "
            f"{c.raw_example} | `{c.target_team_id}` | "
            f"{c.matched_reference} | {c.fuzzy_confidence:.2f} |"
        )
    lines.append("")
    if candidates_dropped:
        lines.append("## Dropped (would have collided)")
        lines.append("")
        lines.append("| occ | alias_normalized | raw_example | "
                     "would-target | conflict team_id | conflict canonical "
                     "| conflict source |")
        lines.append("|---:|---|---|---|---|---|---|")
        for c, conflicts in sorted(
            candidates_dropped,
            key=lambda x: x[0].occurrence_count, reverse=True,
        ):
            for conf in conflicts:
                lines.append(
                    f"| {c.occurrence_count} | `{c.alias_normalized}` | "
                    f"{c.raw_example} | `{c.target_team_id}` | "
                    f"`{conf['team_id']}` | {conf['canonical_name']} | "
                    f"{conf['source']} |"
                )
        lines.append("")
    if candidates_same_team:
        lines.append("## Same team already present (bootstrap NOT-EXISTS will dedup)")
        lines.append("")
        lines.append("| occ | alias_normalized | raw_example | target_team_id |")
        lines.append("|---:|---|---|---|")
        for c in sorted(candidates_same_team,
                        key=lambda x: x.occurrence_count, reverse=True):
            lines.append(
                f"| {c.occurrence_count} | `{c.alias_normalized}` | "
                f"{c.raw_example} | `{c.target_team_id}` |"
            )
        lines.append("")
    lines.append("## Operator review checklist")
    lines.append("")
    lines.append("1. Spot-check the top-occurrence candidates — do raw "
                 "examples really refer to the assigned target team?")
    lines.append("2. Confidence < 0.85 candidates may need closer review "
                 "(threshold-edge false positives).")
    lines.append("3. Dropped collisions: decide whether to (a) accept the "
                 "drop (existing legacy stub continues routing), (b) DELETE "
                 "the conflicting row first then re-emit, or (c) override "
                 "the target.")
    lines.append("4. Approved candidates layer into the manifest's "
                 "aliases tuple before running the bootstrap apply.")
    lines.append("")
    path.write_text("\n".join(lines))
    return path


def write_harvest_json(
    out_dir: Path,
    candidates_clean: list[Candidate],
    candidates_dropped: list[tuple[Candidate, list[dict]]],
    candidates_same_team: list[Candidate],
    metadata: dict,
) -> Path:
    path = out_dir / "harvest_candidates.json"
    payload = {
        "metadata": metadata,
        "clean": [
            {
                "alias_normalized": c.alias_normalized,
                "raw_example": c.raw_example,
                "raw_examples": c.raw_examples,
                "target_team_id": c.target_team_id,
                "occurrence_count": c.occurrence_count,
                "fuzzy_confidence": c.fuzzy_confidence,
                "matched_reference": c.matched_reference,
            }
            for c in sorted(candidates_clean,
                            key=lambda x: x.occurrence_count, reverse=True)
        ],
        "dropped": [
            {
                "candidate": {
                    "alias_normalized": c.alias_normalized,
                    "raw_example": c.raw_example,
                    "raw_examples": c.raw_examples,
                    "target_team_id": c.target_team_id,
                    "occurrence_count": c.occurrence_count,
                    "fuzzy_confidence": c.fuzzy_confidence,
                    "matched_reference": c.matched_reference,
                },
                "conflicts": conflicts,
            }
            for c, conflicts in candidates_dropped
        ],
        "same_team_already_present": [
            {
                "alias_normalized": c.alias_normalized,
                "raw_example": c.raw_example,
                "target_team_id": c.target_team_id,
                "occurrence_count": c.occurrence_count,
            }
            for c in candidates_same_team
        ],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    return path


# ──────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────


async def run(args, log) -> int:
    if not os.environ.get("DATABASE_URL", "").strip():
        print("ERROR: DATABASE_URL not set in environment",
              file=sys.stderr)
        return 1

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    started = time.monotonic()

    # ── Load roster ─────────────────────────────────────────────
    try:
        roster = load_roster(Path(args.roster_json))
    except (ValueError, json.JSONDecodeError, FileNotFoundError) as exc:
        print(f"ERROR: roster file invalid: {exc}", file=sys.stderr)
        return 3
    log.info("harvest.roster_loaded", count=len(roster),
             path=args.roster_json)
    reference_index = build_reference_index(roster)
    log.info("harvest.reference_index_built",
             reference_count=len(reference_index))

    if async_session is None:
        print("ERROR: DATABASE_URL did not produce a session.",
              file=sys.stderr)
        return 1

    # ── Mine failures ───────────────────────────────────────────
    async with async_session() as session:
        failures = await mine_failure_strings(
            session=session,
            sport_id=args.sport_id,
            window_days=args.window_days,
            log=log,
        )
    if not failures:
        print(
            f"ERROR: No failure strings mined for sport_id="
            f"{args.sport_id} in window={args.window_days}d. "
            "Try a longer window or verify sport_id.",
            file=sys.stderr,
        )
        return 4

    # ── Fuzzy match ─────────────────────────────────────────────
    bucket = fuzzy_match_failures(
        failures=failures,
        reference_index=reference_index,
        threshold=args.fuzzy_threshold,
    )
    log.info("harvest.fuzzy_match.complete",
             candidate_count=len(bucket),
             failures_in=len(failures),
             threshold=args.fuzzy_threshold)

    if not bucket:
        print(
            f"WARN: 0 candidates above fuzzy threshold "
            f"{args.fuzzy_threshold:.2f}. Lower the threshold (try 0.60) "
            "or check that the roster reference forms overlap with FL's "
            "actual provider strings.",
            file=sys.stderr,
        )
        # Still write empty outputs for tooling consistency.

    # Roster lookup for canonical_name attribution.
    roster_by_id = {r.team_id: r for r in roster}
    for c in bucket.values():
        target = roster_by_id.get(c.target_team_id)
        if target:
            c.target_canonical = target.canonical_name

    # ── Collision audit (Component 1) ───────────────────────────
    proposals: list[ProposedAlias] = [
        propose_alias(c.alias_normalized, c.raw_example, c.target_team_id)
        for c in bucket.values()
    ]
    async with async_session() as session:
        report = await audit_alias_collisions(
            session=session,
            proposed_aliases=proposals,
            sport_id=args.sport_id,
        )
    log.info("harvest.collision_audit.complete", summary=report.summarize())

    # Bucket candidates by audit verdict.
    clean_keys = {
        (p.alias_normalized, p.target_team_id) for p in report.clean
    }
    same_team_keys = {
        (p.alias_normalized, p.target_team_id)
        for p in report.same_team_already_present
    }
    candidates_clean = [
        c for (norm, tid), c in bucket.items()
        if (norm, tid) in clean_keys
    ]
    candidates_same_team = [
        c for (norm, tid), c in bucket.items()
        if (norm, tid) in same_team_keys
    ]
    candidates_dropped: list[tuple[Candidate, list[dict]]] = []
    for coll in report.colliding:
        key = (coll.proposed.alias_normalized,
               coll.proposed.target_team_id)
        c = bucket.get(key)
        if c is None:
            continue
        conflicts = [
            {
                "team_id": m.team_id,
                "canonical_name": m.canonical_name,
                "source": m.source,
            }
            for m in coll.conflicting_mappings
        ]
        candidates_dropped.append((c, conflicts))

    # ── Outputs ─────────────────────────────────────────────────
    elapsed = time.monotonic() - started
    md_path = write_harvest_report(
        out_dir=out_dir,
        candidates_clean=candidates_clean,
        candidates_dropped=candidates_dropped,
        candidates_same_team=candidates_same_team,
        sport_id=args.sport_id,
        window_days=args.window_days,
        threshold=args.fuzzy_threshold,
        failures_mined=len(failures),
        roster_size=len(roster),
        elapsed_sec=elapsed,
    )
    json_path = write_harvest_json(
        out_dir=out_dir,
        candidates_clean=candidates_clean,
        candidates_dropped=candidates_dropped,
        candidates_same_team=candidates_same_team,
        metadata={
            "sport_id": args.sport_id,
            "window_days": args.window_days,
            "fuzzy_threshold": args.fuzzy_threshold,
            "roster_size": len(roster),
            "failures_mined": len(failures),
            "candidates_total": len(bucket),
            "elapsed_sec": elapsed,
        },
    )

    print(f"\nAlias harvest complete in {elapsed:.1f}s.")
    print(f"  Failures mined:    {len(failures)}")
    print(f"  Candidates total:  {len(bucket)}")
    print(f"  Clean (emit):      {len(candidates_clean)}")
    print(f"  Dropped (collide): {len(candidates_dropped)}")
    print(f"  Same team present: {len(candidates_same_team)}")
    print(f"\nOutputs:")
    print(f"  - {md_path}")
    print(f"  - {json_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Production-failure alias harvester "
                    "(Phase 2D.5-A engine, Component 2).",
    )
    parser.add_argument("--sport-id", type=int, required=True,
                        help="sp.sports.id to harvest within (e.g. 3 = "
                             "Basketball).")
    parser.add_argument("--roster-json", required=True,
                        help="Path to roster JSON: list of "
                             "{team_id, canonical_name, "
                             "reference_forms?}.")
    parser.add_argument("--window-days", type=int, default=7,
                        help="sp.resolution_log lookback window. "
                             "Default 7d.")
    parser.add_argument("--fuzzy-threshold", type=float, default=0.75,
                        help="rapidfuzz token_set_ratio threshold "
                             "(0.0-1.0). Default 0.75. Lower → more "
                             "candidates, more false positives.")
    parser.add_argument("--out-dir", default="./harvest_output",
                        help="Output directory for harvest_report.md "
                             "and harvest_candidates.json.")
    args = parser.parse_args(argv)
    log = get_logger("harvest_aliases")
    return asyncio.run(run(args, log))


if __name__ == "__main__":
    sys.exit(main())
