"""BBL Component 4 — operator-identified 4 MERGE-required FK-cascades.

Phase 2D.5-A workstream #10, Component 4 of 4. The first MERGE in
program history.

Wraps the tested `scripts.tennis_dedup.merge_cluster()` cascade
primitive. Skips Tennis cluster-detection entirely — the four BBL
merge pairs are operator-identified (Amendment #25: both sides carry
live fixtures; winner = the side with more fixtures, per
fixture-history-wins). One MergeGroup per pair → one per-pair
transaction → independently atomic + independently rollback-able via
sp.dedup_audit.

## Why this exists (the one thing different from Tennis)

Production inspection (Day-N+1 BBL) found 4-6 pending
`sp.review_queue` rows whose `candidate_fixtures` JSONB array
contains BOTH the winner AND the loser of the same merge pair
(e.g. `[anchored_id, rasta_vechta_id, vechta_id]`). The Tennis
cascade's Step 3 naive element-wise swap (`tennis_dedup.py:671-681`)
turns that into `[anchored_id, vechta_id, vechta_id]` — same team
offered twice as distinct candidates. Tennis never produces this
shape because Tennis player merges don't tend to surface review_queue
rows containing both forms; the BBL city-stub-vs-full-name
fragmentation pattern does.

The fix is the `post_review_queue_swap_hook` parameter added to
`merge_cluster()` in this PR. The hook fires INSIDE merge_cluster's
transaction, AFTER Step 3's swap, BEFORE Step 4's audit-row INSERT.
This wrapper passes an order-preserving dedupe UPDATE that keeps
first-occurrence order — `candidate_fixtures[0]` is the anchored
side and `[1:]` are the failed side's trigram candidates (DESC by
similarity), both load-bearing per the matcher / admin / template
invariant chain (see `resolver/fuzzy_tier/matcher.py:521-538`).

Plain `DISTINCT` would scramble that order and silently corrupt the
admin UI. The hook uses `WITH ORDINALITY + MIN(ord)` to dedupe
positionally.

## The 4 BBL merge pairs

Operator-confirmed. Winner is the DEU canonical with more live
fixtures (Amendment #25 fixture-history-wins). Loser will be
DELETE'd after FK cascade.

  Vechta     winner 87d4c8c9-...  loser 74e4e1e2-... (Rasta Vechta)
  Rostock    winner 1b81310d-...  loser 3aa87552-... (Rostock Seawolves)
  Hamburg    winner 09624eed-...  loser 76f717ca-... (Hamburg Towers)
  Heidelberg winner 36cf720f-...  loser 29b00c01-... (MLP Academics)

## Usage

    # Dry-run (DEFAULT — no writes, four per-pair reports):
    DATABASE_URL=<url> python scripts/merge_bbl.py
    DATABASE_URL=<url> python scripts/merge_bbl.py --dry-run

    # Wet apply (requires explicit --apply):
    DATABASE_URL=<url> python scripts/merge_bbl.py --apply \\
        --merge-pr <BBL-Component-4-PR>

    # Rollback a specific merge from sp.dedup_audit (uses Tennis path):
    DATABASE_URL=<url> python scripts/tennis_dedup.py --rollback \\
        --audit-id <uuid>

## Exit codes

  0 — success (writes happened OR dry-run report produced)
  1 — DATABASE_URL not set or engine unavailable
  2 — bad CLI args
  3 — Pattern D pre-flight failed (endpoint mismatch)
  4 — pre-merge sanity check failed (a team_id missing, FOR UPDATE
      lock conflict, etc.)
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from db import async_session  # noqa: E402
from observability import get_logger  # noqa: E402
from scripts.daily_diff import _check_pattern_d_endpoint  # noqa: E402
from scripts.tennis_dedup import (  # noqa: E402
    MergeGroup,
    TeamRow,
    load_team_rows,
    merge_cluster,
)


# ──────────────────────────────────────────────────────────────────────
# The 4 BBL merge pairs — operator-confirmed
# ──────────────────────────────────────────────────────────────────────

# Each tuple: (display_label, winner_team_id, loser_team_id).
# Winner = the side with more live fixtures (Amendment #25).
BBL_MERGE_PAIRS: list[tuple[str, str, str]] = [
    (
        "Vechta / Rasta Vechta",
        "87d4c8c9-b17f-4428-b4d3-29666f4326e7",
        "74e4e1e2-24e9-4766-840b-c3271897b903",
    ),
    (
        "Rostock / Rostock Seawolves",
        "1b81310d-6e53-4a90-8604-7e49718d311c",
        "3aa87552-e24c-42b6-ac66-de437b9463a7",
    ),
    (
        "Hamburg / Hamburg Towers",
        "09624eed-4b9b-47f1-ab7f-87bc1a7416b5",
        "76f717ca-f68d-45b9-bf28-7e28d9dec64e",
    ),
    (
        "Heidelberg / MLP Academics Heidelberg",
        "36cf720f-beae-48ae-9941-4a4d4e959aec",
        "29b00c01-4556-4583-aa4d-307b38396a48",
    ),
]


# ──────────────────────────────────────────────────────────────────────
# Order-preserving dedupe — pure Python mirror (for tests)
# ──────────────────────────────────────────────────────────────────────


def dedupe_array_preserve_order(
    arr: list[str],
    dupe_id: str,
    canonical_id: str,
) -> list[str]:
    """Pure-Python mirror of the SQL hook's behavior.

    Models what `candidate_fixtures` looks like AFTER Step 3's swap
    (`dupe_id` → `canonical_id`) and BEFORE the dedupe runs: the swap
    has already happened; this function dedupes preserving first-
    occurrence order. Used by unit tests so we can assert the shape
    without a real Postgres.

    The SQL hook (`_dedupe_candidate_fixtures_hook`) achieves the
    same result via `WITH ORDINALITY + MIN(ord)`, applied in-DB after
    the swap. Both paths preserve:
      - position [0] (the anchored side from the matcher emission)
      - relative order of [1:] (trigram candidates, DESC by similarity)

    Note: the `dupe_id` parameter is accepted for signature symmetry
    with the SQL hook but is not used here — by the time this function
    runs, Step 3 has already overwritten every occurrence of `dupe_id`
    with `canonical_id`. The input `arr` is already post-swap. The
    parameter is kept so the unit tests can document the full hook
    contract (and so future maintenance keeps both paths aligned).
    """
    del dupe_id  # documented but unused; see docstring
    del canonical_id  # ditto
    seen: set[str] = set()
    out: list[str] = []
    for elem in arr:
        if elem in seen:
            continue
        seen.add(elem)
        out.append(elem)
    return out


# ──────────────────────────────────────────────────────────────────────
# The async hook — the one thing this wrapper adds over the Tennis path
# ──────────────────────────────────────────────────────────────────────


async def _dedupe_candidate_fixtures_hook(
    session, dupe_id: str, canonical_id: str,
) -> None:
    """Order-preserving post-swap dedupe of
    `sp.review_queue.candidate_fixtures`.

    Runs INSIDE merge_cluster()'s transaction immediately after
    Step 3 swaps `dupe_id` → `canonical_id` and BEFORE Step 4's
    audit-row INSERT. Uses the passed-in `session` so it
    participates in the per-pair transaction — a hook failure rolls
    back the whole merge.

    Targets only rows that actually have a duplicate post-swap
    (length > distinct count). Rows with no duplication are not
    rewritten, so the dedupe is a no-op for them.

    Preserves first-occurrence order via `WITH ORDINALITY + MIN(ord)`:
      - candidate_fixtures[0] remains the anchored side
      - candidate_fixtures[1:] remain trigram-ordered (DESC by
        similarity)
    Plain DISTINCT would scramble both. See
    `resolver/fuzzy_tier/matcher.py:521-538` for the load-bearing
    invariant.
    """
    del dupe_id  # the post-swap state already replaced dupe_id with canonical_id
    await session.execute(text("""
        UPDATE sp.review_queue
        SET candidate_fixtures = (
            SELECT jsonb_agg(elem ORDER BY first_ord)
            FROM (
                SELECT elem, MIN(ord) AS first_ord
                FROM jsonb_array_elements(candidate_fixtures)
                     WITH ORDINALITY AS t(elem, ord)
                GROUP BY elem
            ) d
        )
        WHERE candidate_fixtures::text LIKE '%%' || :canonical_id || '%%'
          AND (
              SELECT count(*)
              FROM jsonb_array_elements(candidate_fixtures)
          ) > (
              SELECT count(DISTINCT elem #>> '{}')
              FROM jsonb_array_elements(candidate_fixtures) elem
          )
    """), {"canonical_id": canonical_id})


# ──────────────────────────────────────────────────────────────────────
# BBL-specific dry-run enrichments
# ──────────────────────────────────────────────────────────────────────


async def _collision_row_count(
    session, winner_id: str, loser_id: str,
) -> int:
    """Count pending sp.review_queue rows whose candidate_fixtures
    contains BOTH the winner AND the loser. These are the rows the
    dedupe hook will rewrite (the naive Tennis swap would duplicate
    the winner here)."""
    row = (await session.execute(text("""
        SELECT count(*) AS n
        FROM sp.review_queue
        WHERE candidate_fixtures::text LIKE '%%' || :winner_id || '%%'
          AND candidate_fixtures::text LIKE '%%' || :loser_id || '%%'
    """), {"winner_id": winner_id, "loser_id": loser_id})).first()
    return int(row.n) if row else 0


async def _fixture_breakdown(
    session, loser_id: str,
) -> tuple[int, int]:
    """Count sp.fixtures rows referencing the loser as home vs as away.
    Sum is the total Step 2 home/away UPDATE count."""
    home_row = (await session.execute(text(
        "SELECT count(*) AS n FROM sp.fixtures "
        "WHERE home_team_id = :loser_id"
    ), {"loser_id": loser_id})).first()
    away_row = (await session.execute(text(
        "SELECT count(*) AS n FROM sp.fixtures "
        "WHERE away_team_id = :loser_id"
    ), {"loser_id": loser_id})).first()
    return (
        int(home_row.n) if home_row else 0,
        int(away_row.n) if away_row else 0,
    )


# ──────────────────────────────────────────────────────────────────────
# Per-pair runner
# ──────────────────────────────────────────────────────────────────────


async def _run_one_pair(
    label: str,
    winner_id: str,
    loser_id: str,
    *,
    merge_pr: str | None,
    dry_run: bool,
    log,
) -> dict:
    """Load TeamRows for the pair, construct a MergeGroup, fetch
    BBL-specific dry-run enrichments, then call merge_cluster() with
    the dedupe hook.

    Returns a report dict for printing.
    """
    team_rows = await load_team_rows([winner_id, loser_id])
    if winner_id not in team_rows:
        raise RuntimeError(
            f"BBL pair {label!r}: winner team_id {winner_id} not "
            "found in sp.teams. Operator-supplied UUIDs may be "
            "stale — re-verify before any --apply."
        )
    if loser_id not in team_rows:
        raise RuntimeError(
            f"BBL pair {label!r}: loser team_id {loser_id} not found "
            "in sp.teams. Operator-supplied UUIDs may be stale — "
            "re-verify before any --apply."
        )
    canonical = team_rows[winner_id]
    dupe = team_rows[loser_id]

    # BBL-specific dry-run enrichments — collision row count + fixture
    # home/away breakdown. Run in a short read-only session before
    # merge_cluster takes over.
    async with async_session() as readonly_session:
        collision_rows = await _collision_row_count(
            readonly_session, winner_id, loser_id,
        )
        home_fixtures, away_fixtures = await _fixture_breakdown(
            readonly_session, loser_id,
        )

    mg = MergeGroup(
        canonical=canonical,
        dupes=[dupe],
        # shared_records is meaningful only for Tennis's clustered
        # detection (count of resolution_log evidence rows). Not
        # applicable to BBL's operator-supplied pairs.
        shared_records=0,
    )

    log.info(
        "merge_bbl.pair.start",
        label=label,
        winner=canonical.canonical_name,
        loser=dupe.canonical_name,
        dry_run=dry_run,
    )

    cascade_report = await merge_cluster(
        mg,
        merge_phase="phase_b",
        merge_pr=merge_pr,
        dry_run=dry_run,
        post_review_queue_swap_hook=_dedupe_candidate_fixtures_hook,
    )

    enriched = {
        "label": label,
        "winner_id": winner_id,
        "winner_name": canonical.canonical_name,
        "loser_id": loser_id,
        "loser_name": dupe.canonical_name,
        "aliases_transferring": cascade_report["aliases_transferring"],
        "affected_fixtures_total": cascade_report["affected_fixtures"],
        "fixtures_home": home_fixtures,
        "fixtures_away": away_fixtures,
        "review_queue_rows_affected": cascade_report["affected_review_queue"],
        "review_queue_collision_rows": collision_rows,
        "dry_run": dry_run,
    }

    log.info("merge_bbl.pair.complete", **{
        k: v for k, v in enriched.items() if k != "label"
    })
    return enriched


# ──────────────────────────────────────────────────────────────────────
# Orchestration
# ──────────────────────────────────────────────────────────────────────


async def run_merges(*, dry_run: bool, merge_pr: str | None) -> int:
    log = get_logger("merge_bbl")
    started = time.monotonic()
    log.info("merge_bbl.start", dry_run=dry_run,
             pair_count=len(BBL_MERGE_PAIRS),
             merge_pr=merge_pr)

    if async_session is None:
        print("ERROR: DATABASE_URL not set or engine unavailable.",
              file=sys.stderr)
        return 1

    # Pattern D pre-flight (write-path discipline; also enforced for
    # dry-run so the operator can't accidentally point at the wrong
    # endpoint).
    allow_non_prod = (
        os.environ.get("DAILY_DIFF_ALLOW_NON_PRODUCTION", "").strip() == "1"
    )
    if not allow_non_prod:
        expected_db_name = (
            os.environ.get("EXPECTED_PRODUCTION_DB_NAME", "").strip()
            or "neondb"
        )
        expected_db_host = (
            os.environ.get("EXPECTED_PRODUCTION_DB_HOST", "").strip() or None
        )
        async with async_session() as preflight_session:
            result = await preflight_session.execute(
                text("SELECT current_database();")
            )
            current_db = result.scalar_one()
        rc, msg = _check_pattern_d_endpoint(
            os.environ.get("DATABASE_URL"),
            current_db,
            expected_db_name=expected_db_name,
            expected_db_host=expected_db_host,
            allow_non_production=False,
        )
        if rc != 0:
            print(f"ERROR: {msg}", file=sys.stderr)
            return 3
        log.info("merge_bbl.pattern_d.ok",
                 current_database=current_db,
                 expected_db_name=expected_db_name,
                 expected_db_host=expected_db_host)
    else:
        log.info("merge_bbl.pattern_d.bypass",
                 reason="DAILY_DIFF_ALLOW_NON_PRODUCTION=1")

    reports: list[dict] = []
    for label, winner_id, loser_id in BBL_MERGE_PAIRS:
        try:
            r = await _run_one_pair(
                label=label,
                winner_id=winner_id,
                loser_id=loser_id,
                merge_pr=merge_pr,
                dry_run=dry_run,
                log=log,
            )
            reports.append(r)
        except Exception as exc:
            log.error("merge_bbl.pair.failed",
                      label=label, error=str(exc))
            print(f"\nERROR on pair {label!r}: {exc}", file=sys.stderr)
            print("Aborting remaining pairs. Each pair is independently "
                  "atomic via merge_cluster's transaction; any pair that "
                  "completed before this one is committed (if --apply) "
                  "or already rolled back (if --dry-run).",
                  file=sys.stderr)
            return 4

    elapsed = time.monotonic() - started
    verb = "Would" if dry_run else ""
    print(f"\nBBL Component 4 {'dry-run' if dry_run else 'apply'} "
          f"complete in {elapsed:.1f}s — {len(reports)} pairs:")
    for r in reports:
        print(f"\n  {r['label']}")
        print(f"    Winner: {r['winner_name']} ({r['winner_id']})")
        print(f"    Loser:  {r['loser_name']} ({r['loser_id']})")
        print(f"    Aliases {verb} reparent:                 "
              f"{r['aliases_transferring']:>4}")
        print(f"    Fixtures {verb} re-point (home):         "
              f"{r['fixtures_home']:>4}")
        print(f"    Fixtures {verb} re-point (away):         "
              f"{r['fixtures_away']:>4}")
        print(f"    Fixtures {verb} re-point (total):        "
              f"{r['affected_fixtures_total']:>4}")
        print(f"    Review_queue rows {verb} rewrite:        "
              f"{r['review_queue_rows_affected']:>4}")
        print(f"    Review_queue COLLISION rows (winner+loser in same "
              f"array — dedupe hook will fire here): "
              f"{r['review_queue_collision_rows']:>4}")
        print(f"    Loser team_id {verb} DELETE: "
              f"{r['loser_id']}")
    print()
    if dry_run:
        print(
            "Dry-run complete. Each pair's transaction rolled back; no "
            "writes. Operator review the per-pair counts above before "
            "running with --apply --merge-pr <PR>."
        )
    else:
        print(
            "Apply complete. Each pair committed independently via "
            "merge_cluster's per-pair transaction. Rollback any "
            "specific pair via:\n"
            "    python scripts/tennis_dedup.py --rollback "
            "--audit-id <sp.dedup_audit.id>"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "BBL Component 4 — 4 MERGE-required FK-cascades. Wraps "
            "the tested scripts.tennis_dedup.merge_cluster() primitive "
            "with an order-preserving dedupe hook on "
            "sp.review_queue.candidate_fixtures."
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", action="store_true",
        help=(
            "Classify + report per-pair counts without writing. "
            "DEFAULT — explicit --apply required to write."
        ),
    )
    mode.add_argument(
        "--apply", action="store_true",
        help=(
            "Run wet — commits the FK cascade and sp.dedup_audit "
            "row per pair. Each pair is independently atomic; pairs "
            "that complete before any failure stay committed. "
            "Require --merge-pr."
        ),
    )
    parser.add_argument(
        "--merge-pr", default=None,
        help=(
            "PR number string for sp.dedup_audit.merge_pr provenance. "
            "Required with --apply; optional with --dry-run."
        ),
    )
    args = parser.parse_args(argv)

    if not args.apply and not args.dry_run:
        # No mode flag → DEFAULT dry-run (safety).
        args.dry_run = True

    if args.apply and not args.merge_pr:
        print(
            "ERROR: --apply requires --merge-pr <PR string> for "
            "sp.dedup_audit provenance.",
            file=sys.stderr,
        )
        return 2

    return asyncio.run(run_merges(
        dry_run=not args.apply,
        merge_pr=args.merge_pr,
    ))


if __name__ == "__main__":
    sys.exit(main())
