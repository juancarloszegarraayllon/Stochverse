"""Tennis cross-format dedup — merge duplicate player rows in sp.teams.

Per Tennis dedup scope-doc (PR #188):

  Phase A: automated merge of high-confidence duplicate clusters
    where the F8 criterion (5 conditions) is met.
  Phase B: operator-reviewed merge of candidate-verification clusters.

## Usage

    # Phase A dry-run (produces report without applying):
    python scripts/tennis_dedup.py --phase a --dry-run

    # Phase A wet apply:
    python scripts/tennis_dedup.py --phase a --apply

    # Phase B dry-run:
    python scripts/tennis_dedup.py --phase b --dry-run

    # Phase B per-cluster apply:
    python scripts/tennis_dedup.py --phase b --apply --cluster-id <uuid>

    # Rollback a specific merge from sp.dedup_audit:
    python scripts/tennis_dedup.py --rollback --audit-id <uuid>

## Exit codes

  0 — success
  1 — DATABASE_URL not set
  2 — bad CLI args
  3 — dry-run complete (report produced, no writes)

## Architecture

Pure functions (classifiers, union-find, tiebreaker, criterion check)
live at the top of this file. Orchestration (merge_cluster, criterion
query, CLI) lives below. Tests import the pure functions directly via
`from scripts.tennis_dedup import <function>`.

Daily-diff synthetic reason_code note: production cron writes nothing
to sp.resolution_log for records where the resolver returns None at
extract_signal. Daily-diff classifies those as
signal_extraction_skipped via its own synthetic counter. The
colliding_*_team_ids signal used by the F8 criterion lives in
resolution_log rows where the resolver DID produce a result but
couldn't disambiguate — a different population from the
signal_extraction_skipped records (v1.5 amendment #9).
"""
from __future__ import annotations

import re
import sys
import os
from dataclasses import dataclass
from typing import Iterable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Name-format classification ────────────────────────────────────


class NameFormat:
    CLASS_F = "class_f"
    CLASS_S = "class_s"
    UNCLASSIFIED = "unclassified"


# Class F: exactly 2 tokens, first token length > 1 (full given name).
# e.g., "Carlos Alcaraz", "Hyeon Chung"
# 3+ token names route to Population C (Phase B operator review) per
# scope-doc §4.2 adjustment 2.
_CLASS_F_RE = re.compile(r"^[A-Za-zÀ-ɏ]{2,} [A-Za-zÀ-ɏ]{2,}$")

# Class S: surname + single initial + optional country code.
# e.g., "Chung H.", "Alcaraz C. (Esp)", "Chen Y. (Chn)"
_CLASS_S_RE = re.compile(
    r"^[A-Za-zÀ-ɏ]{2,} [A-Za-z]\."
    r"(?: \([A-Za-z]{2,4}\))?$"
)


def classify_name_format(canonical_name: str) -> str:
    """Classify a canonical_name into Class F, Class S, or unclassified.

    Pure function on the string. Does NOT call structurally_normalize
    (that's used separately for surname-anchor extraction). This
    classifier determines the name FORMAT for the pairwise
    format-match check (F8 conditions 3-5).

    Returns one of NameFormat.CLASS_F, CLASS_S, UNCLASSIFIED.
    """
    if not canonical_name or not canonical_name.strip():
        return NameFormat.UNCLASSIFIED
    name = canonical_name.strip()
    if _CLASS_F_RE.match(name):
        return NameFormat.CLASS_F
    if _CLASS_S_RE.match(name):
        return NameFormat.CLASS_S
    return NameFormat.UNCLASSIFIED


# ── Pairwise format-match check ───────────────────────────────────


def format_match(class_f_name: str, class_s_name: str) -> bool:
    """Check F8 conditions 4+5: firstname-initial alignment + surname match.

    Args:
        class_f_name: a Class F canonical_name ("Carlos Alcaraz")
        class_s_name: a Class S canonical_name ("Alcaraz C. (Esp)")

    Returns True if:
      - Class F's first token's first character == Class S's initial
      - Class F's last token (lowercased) == Class S's first token (lowercased)

    Pure function. Caller is responsible for ensuring both names are
    already classified as Class F / Class S respectively.
    """
    f_tokens = class_f_name.strip().split()
    s_tokens = class_s_name.strip().split()
    if len(f_tokens) < 2 or len(s_tokens) < 2:
        return False

    f_firstname = f_tokens[0]
    f_surname = f_tokens[-1]

    s_surname = s_tokens[0]
    # Initial is the second token minus the trailing dot
    s_initial_token = s_tokens[1]
    s_initial = s_initial_token.rstrip(".")

    # Condition 4: firstname-initial alignment
    if f_firstname[0].lower() != s_initial[0].lower():
        return False

    # Condition 5: surname-token match
    if f_surname.lower() != s_surname.lower():
        return False

    return True


# ── Union-find cluster assembly ───────────────────────────────────


def build_clusters(
    pairs: Iterable[tuple[str, str]],
) -> list[set[str]]:
    """Given collision pairs (team_a, team_b), produce connected components.

    Uses union-find (disjoint set) for O(n * alpha(n)) performance.
    Returns a list of sets, each set being a cluster of team_ids that
    co-occurred in collision arrays.

    team_a and team_b are string UUIDs (not uuid.UUID) for JSON
    serialization compatibility.
    """
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for a, b in pairs:
        parent.setdefault(a, a)
        parent.setdefault(b, b)
        union(a, b)

    clusters: dict[str, set[str]] = {}
    for node in parent:
        root = find(node)
        clusters.setdefault(root, set()).add(node)

    return list(clusters.values())


# ── F1 tiebreaker logic ──────────────────────────────────────────


@dataclass(frozen=True)
class TeamRow:
    """Minimal sp.teams row for the F1 tiebreaker."""
    team_id: str
    canonical_name: str
    created_at: object  # datetime
    alias_count: int


def pick_canonical(members: list[TeamRow]) -> TeamRow:
    """Select the surviving canonical per F1 decision.

    Tiebreaker chain:
      1. Oldest created_at
      2. More aliases attached (higher alias_count)
      3. Longer canonical_name (more informative for operator display)

    Returns the TeamRow that should survive; all others are dupes.
    """
    if not members:
        raise ValueError("pick_canonical called with empty member list")
    if len(members) == 1:
        return members[0]

    return min(
        members,
        key=lambda t: (
            t.created_at,
            -t.alias_count,
            -len(t.canonical_name),
        ),
    )


# ── Cluster partitioning (Phase A criterion) ─────────────────────


@dataclass(frozen=True)
class MergeGroup:
    """A validated merge-group within a collision cluster."""
    canonical: TeamRow
    dupes: list[TeamRow]
    shared_records: int


def partition_cluster(
    members: list[TeamRow],
    shared_records: int,
    *,
    max_cluster_size: int = 4,
    min_shared_records: int = 5,
) -> MergeGroup | None:
    """Apply the Phase A criterion (F8 conditions 1-5) to a cluster.

    Returns a MergeGroup if the cluster passes all conditions, or None
    if it fails (routes to Phase B or skip).

    Conditions checked:
      1. Matcher-evidence: shared_records >= min_shared_records (caller
         provides this from the criterion query)
      2. Cluster size <= max_cluster_size
      3. Exactly 1 Class F + exactly 1 Class S member
      4. Firstname-initial alignment (via format_match)
      5. Surname-token match (via format_match)

    Members that are UNCLASSIFIED are ignored for conditions 3-5.
    """
    # Condition 1
    if shared_records < min_shared_records:
        return None

    # Condition 2
    if len(members) > max_cluster_size:
        return None

    # Classify each member
    f_members = [m for m in members if classify_name_format(m.canonical_name) == NameFormat.CLASS_F]
    s_members = [m for m in members if classify_name_format(m.canonical_name) == NameFormat.CLASS_S]

    # Condition 3: exactly 1 F + exactly 1 S
    if len(f_members) != 1 or len(s_members) != 1:
        return None

    f_member = f_members[0]
    s_member = s_members[0]

    # Conditions 4+5: format match
    if not format_match(f_member.canonical_name, s_member.canonical_name):
        return None

    # All conditions pass — build the merge group
    canonical = pick_canonical([f_member, s_member])
    dupes = [m for m in [f_member, s_member] if m.team_id != canonical.team_id]

    return MergeGroup(
        canonical=canonical,
        dupes=dupes,
        shared_records=shared_records,
    )


# ── Criterion query (F8 collision-cluster extraction) ─────────


import argparse
import asyncio
import json
import uuid
from datetime import timedelta

from sqlalchemy import text  # noqa: E402

from db import async_session  # noqa: E402
from observability import get_logger  # noqa: E402


_COLLISION_PAIRS_SQL = """
WITH tennis_collisions AS (
  SELECT
    provider_record_id,
    jsonb_array_elements_text(reason_detail->'colliding_home_team_ids')::uuid AS team_id
  FROM sp.resolution_log
  WHERE reason_detail->>'sport' = 'Tennis'
    AND reason_detail->'colliding_home_team_ids' IS NOT NULL
    AND jsonb_array_length(reason_detail->'colliding_home_team_ids') >= 2
    AND decided_at >= NOW() - :window
  UNION ALL
  SELECT
    provider_record_id,
    jsonb_array_elements_text(reason_detail->'colliding_away_team_ids')::uuid AS team_id
  FROM sp.resolution_log
  WHERE reason_detail->>'sport' = 'Tennis'
    AND reason_detail->'colliding_away_team_ids' IS NOT NULL
    AND jsonb_array_length(reason_detail->'colliding_away_team_ids') >= 2
    AND decided_at >= NOW() - :window
),
collision_pairs AS (
  SELECT
    LEAST(a.team_id, b.team_id) AS team_a,
    GREATEST(a.team_id, b.team_id) AS team_b,
    a.provider_record_id
  FROM tennis_collisions a
  JOIN tennis_collisions b
    ON a.provider_record_id = b.provider_record_id
    AND a.team_id < b.team_id
)
SELECT team_a, team_b,
       count(DISTINCT provider_record_id) AS shared_records
FROM collision_pairs
GROUP BY team_a, team_b
HAVING count(DISTINCT provider_record_id) >= :min_shared
ORDER BY shared_records DESC
"""

_TEAM_ROWS_SQL = """
SELECT t.id AS team_id,
       t.canonical_name,
       t.created_at,
       (SELECT count(*) FROM sp.team_aliases a WHERE a.team_id = t.id) AS alias_count
FROM sp.teams t
JOIN sp.sports s ON s.id = t.sport_id
WHERE s.code = 'tennis'
  AND t.id = ANY(:team_ids)
"""


async def extract_collision_pairs(
    *, window_days: int = 30, min_shared: int = 5,
) -> list[tuple[str, str, int]]:
    """Run the F8 criterion query against production.

    Returns list of (team_a, team_b, shared_records) tuples.
    """
    async with async_session() as session:
        rows = (await session.execute(
            text(_COLLISION_PAIRS_SQL),
            {"window": timedelta(days=window_days), "min_shared": min_shared},
        )).all()
    return [(str(r.team_a), str(r.team_b), r.shared_records) for r in rows]


async def load_team_rows(team_ids: list[str]) -> dict[str, TeamRow]:
    """Load sp.teams rows for a set of team_ids.

    Returns {team_id_str: TeamRow}.
    """
    if not team_ids:
        return {}
    uuids = [uuid.UUID(tid) for tid in team_ids]
    async with async_session() as session:
        rows = (await session.execute(
            text(_TEAM_ROWS_SQL),
            {"team_ids": uuids},
        )).all()
    return {
        str(r.team_id): TeamRow(
            team_id=str(r.team_id),
            canonical_name=r.canonical_name,
            created_at=r.created_at,
            alias_count=r.alias_count,
        )
        for r in rows
    }


async def build_phase_a_population(
    *, window_days: int = 30, min_shared: int = 5,
    max_cluster_size: int = 4,
) -> tuple[list[MergeGroup], list[set[str]]]:
    """Full Phase A pipeline: criterion query → clusters → partition.

    Returns (merge_groups, skipped_clusters).
    merge_groups: clusters that passed all F8 conditions.
    skipped_clusters: clusters that failed (Phase B candidates or skip).
    """
    log = get_logger("tennis_dedup")

    pairs_raw = await extract_collision_pairs(
        window_days=window_days, min_shared=min_shared,
    )
    log.info("tennis_dedup.pairs_extracted", count=len(pairs_raw))

    pair_evidence: dict[tuple[str, str], int] = {}
    dupes_seen = 0
    for a, b, shared in pairs_raw:
        key = (min(a, b), max(a, b))
        if key in pair_evidence:
            dupes_seen += 1
        pair_evidence[key] = max(pair_evidence.get(key, 0), shared)
    if dupes_seen:
        log.warning(
            "tennis_dedup.duplicate_pairs",
            count=dupes_seen,
            note="criterion query returned duplicate pairs; max() dedup applied",
        )

    clusters = build_clusters([(a, b) for a, b, _ in pairs_raw])
    log.info("tennis_dedup.clusters_built", count=len(clusters))

    all_team_ids = sorted({tid for c in clusters for tid in c})
    team_rows = await load_team_rows(all_team_ids)
    log.info("tennis_dedup.team_rows_loaded", count=len(team_rows))

    merge_groups: list[MergeGroup] = []
    skipped: list[set[str]] = []

    for cluster in clusters:
        members = [team_rows[tid] for tid in cluster if tid in team_rows]
        if len(members) < 2:
            skipped.append(cluster)
            continue

        # Use max shared_records across all pairs in the cluster as the
        # evidence strength. For fully-connected clusters from single
        # co-occurrence events (the common case: all N team_ids appeared
        # in the same colliding_*_team_ids array), all pairs share the
        # same shared_records count, so max == any. For clusters
        # assembled from pairs with different evidence strengths (rare:
        # would require team_ids to co-occur in different collision
        # events at different frequencies), max overstates the weakest
        # pair's evidence. Acceptable for Phase A's ≥5 threshold —
        # the partition_cluster check re-validates the threshold.
        cluster_evidence = max(
            pair_evidence.get((min(a, b), max(a, b)), 0)
            for a in cluster for b in cluster if a < b
        )

        mg = partition_cluster(
            members, cluster_evidence,
            max_cluster_size=max_cluster_size,
            min_shared_records=min_shared,
        )
        if mg is not None:
            merge_groups.append(mg)
        else:
            skipped.append(cluster)

    log.info(
        "tennis_dedup.population_built",
        phase_a=len(merge_groups),
        skipped=len(skipped),
    )
    return merge_groups, skipped


# ── Pre-state capture (F7) ────────────────────────────────────


async def capture_pre_state(
    session, canonical_id: str, dupe_ids: list[str],
) -> dict:
    """Capture full pre-merge state for rollback per F7 decision.

    Returns a dict suitable for JSONB serialization into
    sp.dedup_audit.pre_state.
    """
    all_ids = [uuid.UUID(canonical_id)] + [uuid.UUID(d) for d in dupe_ids]

    # Team rows
    team_rows = (await session.execute(text(
        "SELECT id, canonical_name, normalized_name, sport_id, "
        "       country_code, created_at "
        "FROM sp.teams WHERE id = ANY(:ids)"
    ), {"ids": all_ids})).mappings().all()

    # Alias sets per team
    alias_rows = (await session.execute(text(
        "SELECT team_id, id AS alias_id, alias, alias_normalized, "
        "       source, confidence, created_at "
        "FROM sp.team_aliases WHERE team_id = ANY(:ids)"
    ), {"ids": all_ids})).mappings().all()

    alias_sets: dict[str, list[dict]] = {}
    for a in alias_rows:
        tid = str(a["team_id"])
        alias_sets.setdefault(tid, []).append({
            "alias_id": str(a["alias_id"]),
            "alias": a["alias"],
            "alias_normalized": a["alias_normalized"],
            "source": a["source"],
            "confidence": float(a["confidence"]) if a["confidence"] is not None else None,
            "created_at": a["created_at"].isoformat() if a["created_at"] else None,
        })

    # Affected fixtures
    dupe_uuids = [uuid.UUID(d) for d in dupe_ids]
    fixture_rows = (await session.execute(text(
        "SELECT id, home_team_id, away_team_id "
        "FROM sp.fixtures "
        "WHERE home_team_id = ANY(:dids) OR away_team_id = ANY(:dids)"
    ), {"dids": dupe_uuids})).mappings().all()

    # Affected review_queue rows — full candidate_fixtures JSONB per F7 adjustment
    rq_rows = (await session.execute(text(
        "SELECT id, candidate_fixtures "
        "FROM sp.review_queue "
        "WHERE candidate_fixtures::text LIKE ANY(:patterns)"
    ), {
        "patterns": [f"%{d}%" for d in dupe_ids],
    })).mappings().all()

    return {
        "team_rows": [
            {
                "id": str(t["id"]),
                "canonical_name": t["canonical_name"],
                "normalized_name": t["normalized_name"],
                "sport_id": t["sport_id"],
                "country_code": t["country_code"],
                "created_at": t["created_at"].isoformat() if t["created_at"] else None,
            }
            for t in team_rows
        ],
        "alias_sets": alias_sets,
        "affected_fixtures": [
            {
                "fixture_id": str(f["id"]),
                "original_home_team_id": str(f["home_team_id"]),
                "original_away_team_id": str(f["away_team_id"]),
            }
            for f in fixture_rows
        ],
        "affected_review_queue": [
            {
                "review_queue_id": str(r["id"]),
                "original_candidate_fixtures": r["candidate_fixtures"],
            }
            for r in rq_rows
        ],
    }


# ── merge_cluster() async primitive ───────────────────────────


async def merge_cluster(
    mg: MergeGroup,
    *,
    merge_phase: str,
    merge_pr: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Merge a validated MergeGroup. Single transaction per F4/Phase 2D.3.1.

    Returns a report dict (usable in both dry-run and wet modes).
    In dry_run mode: captures what WOULD happen without writing.
    In wet mode: applies the merge and writes the audit row.
    """
    canonical_id = mg.canonical.team_id
    dupe_ids = [d.team_id for d in mg.dupes]
    log = get_logger("tennis_dedup")

    async with async_session() as session:
        async with session.begin():
            # Step 0: SELECT FOR UPDATE — fail-fast if any row already gone
            all_ids = [uuid.UUID(canonical_id)] + [uuid.UUID(d) for d in dupe_ids]
            locked = (await session.execute(text(
                "SELECT id FROM sp.teams WHERE id = ANY(:ids) FOR UPDATE"
            ), {"ids": all_ids})).scalars().all()

            if len(locked) != len(all_ids):
                raise ValueError(
                    f"Expected {len(all_ids)} rows, locked {len(locked)}. "
                    "Concurrent modification or prior merge."
                )

            # Capture pre-state before any mutations
            pre_state = await capture_pre_state(session, canonical_id, dupe_ids)

            report = {
                "canonical_id": canonical_id,
                "canonical_name": mg.canonical.canonical_name,
                "dupe_ids": dupe_ids,
                "dupe_names": [d.canonical_name for d in mg.dupes],
                "shared_records": mg.shared_records,
                "affected_fixtures": len(pre_state["affected_fixtures"]),
                "affected_review_queue": len(pre_state["affected_review_queue"]),
                "aliases_transferring": sum(
                    len(pre_state["alias_sets"].get(d, []))
                    for d in dupe_ids
                ),
                # TODO: populate with UNCLASSIFIED cluster members that
                # didn't pair into a merge-group. Enhances the dry-run
                # report per review observation #3 ("Cluster X has 4
                # members; merging F/S pair (2 rows); 2 UNCLASSIFIED
                # members remain standalone").
                "unclassified_standalone": [],
            }

            if dry_run:
                # Transaction rolls back on context exit (no commit)
                return report

            # Step 1-5: apply the merge
            for dupe_id in dupe_ids:
                dupe_uuid = uuid.UUID(dupe_id)
                canonical_uuid = uuid.UUID(canonical_id)

                # Step 1: Copy aliases
                await session.execute(text("""
                    INSERT INTO sp.team_aliases
                      (id, team_id, alias, alias_normalized, source,
                       confidence, created_at)
                    SELECT gen_random_uuid(), :canonical_id, a.alias,
                           a.alias_normalized, a.source, a.confidence,
                           a.created_at
                    FROM sp.team_aliases a WHERE a.team_id = :dupe_id
                    ON CONFLICT (alias_normalized, source) DO NOTHING
                """), {"canonical_id": canonical_uuid, "dupe_id": dupe_uuid})

                # Step 2: Rewrite fixtures
                await session.execute(text(
                    "UPDATE sp.fixtures SET home_team_id = :canonical_id "
                    "WHERE home_team_id = :dupe_id"
                ), {"canonical_id": canonical_uuid, "dupe_id": dupe_uuid})
                await session.execute(text(
                    "UPDATE sp.fixtures SET away_team_id = :canonical_id "
                    "WHERE away_team_id = :dupe_id"
                ), {"canonical_id": canonical_uuid, "dupe_id": dupe_uuid})

                # Step 3: Rewrite review_queue candidate_fixtures JSONB
                dupe_text = str(dupe_id)
                canonical_text = str(canonical_id)
                await session.execute(text("""
                    UPDATE sp.review_queue
                    SET candidate_fixtures = (
                      SELECT jsonb_agg(
                        CASE WHEN elem #>> '{}' = :dupe_text
                             THEN to_jsonb(:canonical_text)
                             ELSE elem END
                      ) FROM jsonb_array_elements(candidate_fixtures) elem
                    )
                    WHERE candidate_fixtures::text LIKE '%%' || :dupe_text || '%%'
                """), {"dupe_text": dupe_text, "canonical_text": canonical_text})

            # Step 4: Audit row
            await session.execute(text("""
                INSERT INTO sp.dedup_audit
                  (canonical_id, merged_ids, pre_state, merge_phase, merge_pr)
                VALUES (:canonical_id, :merged_ids, :pre_state, :phase, :pr)
            """), {
                "canonical_id": uuid.UUID(canonical_id),
                "merged_ids": [uuid.UUID(d) for d in dupe_ids],
                "pre_state": json.dumps(pre_state, default=str),
                "phase": merge_phase,
                "pr": merge_pr,
            })

            # Step 5: Delete dupe rows (CASCADE purges alias originals)
            for dupe_id in dupe_ids:
                await session.execute(text(
                    "DELETE FROM sp.teams WHERE id = :dupe_id"
                ), {"dupe_id": uuid.UUID(dupe_id)})

            log.info(
                "tennis_dedup.merged",
                canonical_id=canonical_id,
                dupe_ids=dupe_ids,
                fixtures=report["affected_fixtures"],
                review_queue=report["affected_review_queue"],
            )

    return report


# ── Dry-run report formatting ─────────────────────────────────


def format_dry_run_report(
    merge_groups: list[MergeGroup],
    skipped_clusters: list[set[str]],
    team_rows: dict[str, TeamRow],
    reports: list[dict],
) -> str:
    """Format the --dry-run output for operator review."""
    out: list[str] = []
    out.append(f"# Tennis Dedup Phase A — Dry-Run Report")
    out.append("")
    out.append(f"Merge-groups: {len(merge_groups)}")
    out.append(f"Skipped clusters (Phase B or skip): {len(skipped_clusters)}")
    out.append("")

    for i, (mg, report) in enumerate(zip(merge_groups, reports), 1):
        out.append(f"## Merge-group {i}")
        out.append(f"- Canonical: `{mg.canonical.canonical_name}` ({mg.canonical.team_id})")
        for d in mg.dupes:
            out.append(f"- Dupe: `{d.canonical_name}` ({d.team_id})")
        out.append(f"- Shared records: {mg.shared_records}")
        out.append(f"- Aliases transferring: {report['aliases_transferring']}")
        out.append(f"- Affected fixtures: {report['affected_fixtures']}")
        out.append(f"- Affected review_queue rows: {report['affected_review_queue']}")
        out.append("")

    if skipped_clusters:
        out.append(f"## Skipped clusters ({len(skipped_clusters)})")
        out.append("")
        for cluster in skipped_clusters[:20]:
            names = [
                team_rows[tid].canonical_name if tid in team_rows else tid
                for tid in sorted(cluster)
            ]
            out.append(f"- {len(cluster)} members: {', '.join(names)}")
        if len(skipped_clusters) > 20:
            out.append(f"- ... and {len(skipped_clusters) - 20} more")
        out.append("")

    return "\n".join(out)


# ── CLI entry point ───────────────────────────────────────────


async def run_phase_a(*, dry_run: bool, merge_pr: str | None) -> int:
    """Execute Phase A: criterion query → partition → merge (or dry-run)."""
    log = get_logger("tennis_dedup")

    merge_groups, skipped = await build_phase_a_population()
    if not merge_groups:
        print("No merge-groups found meeting Phase A criterion.")
        return 0

    all_team_ids = sorted({
        tid
        for mg in merge_groups
        for tid in [mg.canonical.team_id] + [d.team_id for d in mg.dupes]
    } | {tid for c in skipped for tid in c})
    team_rows_map = await load_team_rows(all_team_ids)

    reports: list[dict] = []
    for mg in merge_groups:
        report = await merge_cluster(
            mg, merge_phase="phase_a", merge_pr=merge_pr, dry_run=dry_run,
        )
        reports.append(report)

    output = format_dry_run_report(merge_groups, skipped, team_rows_map, reports)
    print(output)

    if dry_run:
        log.info("tennis_dedup.dry_run_complete", groups=len(merge_groups))
        return 3

    log.info(
        "tennis_dedup.phase_a_complete",
        merged=len(merge_groups),
        skipped=len(skipped),
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Tennis cross-format dedup (Phase A/B).",
    )
    parser.add_argument(
        "--phase", choices=["a", "b"], default="a",
        help="Phase A (automated) or Phase B (operator-reviewed).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Produce report without applying merges.",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Apply merges (wet run).",
    )
    parser.add_argument(
        "--merge-pr", type=str, default=None,
        help="PR number for audit trail provenance.",
    )
    parser.add_argument(
        "--rollback", action="store_true",
        help="Rollback a specific merge from sp.dedup_audit.",
    )
    parser.add_argument(
        "--audit-id", type=str, default=None,
        help="Audit row UUID for --rollback.",
    )

    args = parser.parse_args(argv)

    # Argument validation first (before DATABASE_URL check) so CLI
    # errors surface without requiring a live DB connection.
    if args.rollback:
        if not args.audit_id:
            print("ERROR: --rollback requires --audit-id.", file=sys.stderr)
            return 2
        if not async_session:
            print("ERROR: DATABASE_URL not set.", file=sys.stderr)
            return 1
        print("Rollback not yet implemented.", file=sys.stderr)
        return 2

    if args.phase == "b":
        print("Phase B not yet implemented.", file=sys.stderr)
        return 2

    if not args.dry_run and not args.apply:
        print("ERROR: specify --dry-run or --apply.", file=sys.stderr)
        return 2

    if args.apply:
        # GUARD: wet apply requires rollback to be implemented first.
        # An erroneous merge without a recovery path risks destroying
        # real player data. This guard is removed when rollback ships
        # in the Phase A apply PR.
        print(
            "ERROR: --apply is blocked until rollback is implemented. "
            "Use --dry-run to preview merge-groups. "
            "Rollback implementation ships in the Phase A apply PR.",
            file=sys.stderr,
        )
        return 2

    if not async_session:
        print("ERROR: DATABASE_URL not set.", file=sys.stderr)
        return 1

    return asyncio.run(run_phase_a(
        dry_run=args.dry_run,
        merge_pr=args.merge_pr,
    ))


if __name__ == "__main__":
    sys.exit(main())
