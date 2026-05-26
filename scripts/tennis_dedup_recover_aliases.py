"""Recovery script: re-insert aliases lost during Tennis dedup Phase A.

Root cause: merge_cluster() step 1 used INSERT ... ON CONFLICT
(alias_normalized, source) DO NOTHING. The unique constraint is
GLOBAL (not per-team), so the INSERT silently dropped when the
dupe's own alias row (still alive at step 1) matched the constraint.
Step 5's CASCADE delete then removed the original. Net: alias lost.

This script reads sp.dedup_audit rows from the Phase A apply,
extracts the dupe alias sets from pre_state, and re-inserts them
with team_id = canonical_id.

Usage:
    python scripts/tennis_dedup_recover_aliases.py --dry-run
    python scripts/tennis_dedup_recover_aliases.py --apply
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from db import async_session  # noqa: E402
from observability import get_logger  # noqa: E402


async def recover_aliases(*, dry_run: bool, merge_pr: str = "197") -> int:
    log = get_logger("tennis_dedup_recover")

    if async_session is None:
        print("ERROR: DATABASE_URL not set.", file=sys.stderr)
        return 1

    async with async_session() as session:
        audit_rows = (await session.execute(text(
            "SELECT id, canonical_id, merged_ids, pre_state "
            "FROM sp.dedup_audit "
            "WHERE merge_pr = :pr AND rolled_back_at IS NULL "
            "ORDER BY merged_at"
        ), {"pr": merge_pr})).mappings().all()

    log.info("recover.audit_rows_loaded", count=len(audit_rows))
    print(f"Loaded {len(audit_rows)} audit rows for merge_pr={merge_pr}")

    total_recovered = 0
    total_skipped = 0
    total_already_present = 0

    for audit_row in audit_rows:
        canonical_id = str(audit_row["canonical_id"])
        canonical_uuid = uuid.UUID(canonical_id)
        pre = audit_row["pre_state"]
        if isinstance(pre, str):
            pre = json.loads(pre)

        merged_ids = [str(m) for m in audit_row["merged_ids"]]

        for dupe_id in merged_ids:
            dupe_aliases = pre.get("alias_sets", {}).get(dupe_id, [])
            for alias_data in dupe_aliases:
                anorm = alias_data["alias_normalized"]
                source = alias_data["source"]

                # Check if this alias already exists on the canonical
                async with async_session() as session:
                    exists = (await session.execute(text(
                        "SELECT 1 FROM sp.team_aliases "
                        "WHERE team_id = :tid AND alias_normalized = :anorm"
                    ), {"tid": canonical_uuid, "anorm": anorm})).scalar()

                if exists:
                    total_already_present += 1
                    continue

                # Check if the (alias_normalized, source) slot is taken
                # by another team entirely (shouldn't happen post-merge
                # since the dupe was deleted, but defensive)
                async with async_session() as session:
                    conflict = (await session.execute(text(
                        "SELECT team_id FROM sp.team_aliases "
                        "WHERE alias_normalized = :anorm AND source = :src"
                    ), {"anorm": anorm, "src": source})).scalar()

                if conflict:
                    if str(conflict) == canonical_id:
                        total_already_present += 1
                    else:
                        print(
                            f"  SKIP: alias '{anorm}' source='{source}' "
                            f"owned by team {conflict} (not canonical {canonical_id})"
                        )
                        total_skipped += 1
                    continue

                if dry_run:
                    print(
                        f"  WOULD INSERT: alias='{alias_data['alias']}' "
                        f"norm='{anorm}' source='{source}' "
                        f"→ canonical {canonical_id}"
                    )
                    total_recovered += 1
                else:
                    async with async_session() as session:
                        async with session.begin():
                            await session.execute(text("""
                                INSERT INTO sp.team_aliases
                                  (id, team_id, alias, alias_normalized,
                                   source, confidence, created_at)
                                VALUES (gen_random_uuid(), :tid, :alias,
                                        :anorm, :src, :conf,
                                        CAST(:ca AS timestamptz))
                            """), {
                                "tid": canonical_uuid,
                                "alias": alias_data["alias"],
                                "anorm": anorm,
                                "src": source,
                                "conf": alias_data["confidence"],
                                "ca": alias_data["created_at"],
                            })
                    total_recovered += 1

    action = "WOULD RECOVER" if dry_run else "RECOVERED"
    print(f"\n{action}: {total_recovered} aliases")
    print(f"Already present: {total_already_present}")
    print(f"Skipped (conflict with other team): {total_skipped}")

    log.info(
        "recover.complete",
        dry_run=dry_run,
        recovered=total_recovered,
        already_present=total_already_present,
        skipped=total_skipped,
    )
    return 3 if dry_run else 0


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="Recover aliases lost during Tennis dedup Phase A apply.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--merge-pr", default="197")
    args = parser.parse_args(argv)

    if not args.dry_run and not args.apply:
        print("ERROR: specify --dry-run or --apply.", file=sys.stderr)
        return 2

    return asyncio.run(recover_aliases(
        dry_run=args.dry_run,
        merge_pr=args.merge_pr,
    ))


if __name__ == "__main__":
    sys.exit(main())
