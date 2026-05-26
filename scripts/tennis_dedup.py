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
