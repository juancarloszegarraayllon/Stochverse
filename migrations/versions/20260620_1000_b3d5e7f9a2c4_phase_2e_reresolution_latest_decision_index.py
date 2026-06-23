"""Phase 2E re-resolution loop: covering index for latest-decision DISTINCT ON.

Per the Day-43 perf finding (FL dry-run): the candidate-selection
query's DISTINCT ON `(provider, provider_record_id)` ORDER BY
`decided_at DESC` was the dominant cost — 6,103ms warm of the 6.3s
total (4,520ms Index Scan + 1,583ms Incremental Sort), exceeding the
5s F6 halt ceiling.

EXPLAIN ANALYZE attribution: the existing
`ix_resolution_log_provider_record` (provider, provider_record_id)
satisfies the DISTINCT ON's first two key columns, but `decided_at`
is not in the index — Postgres has to fetch every matching heap
tuple for `decided_at`, then resort within each provider_record_id
group via Incremental Sort.

This migration adds a third index that matches the
`DISTINCT ON ... ORDER BY` exactly:

  CREATE INDEX CONCURRENTLY ix_resolution_log_provider_record_decided_at
    ON sp.resolution_log (provider, provider_record_id, decided_at DESC);

Expected effect: the Index Scan + Incremental Sort collapses into
a single Index Scan (or Index-Only Scan if heap-only-tuples
fraction is high) producing rows already in the correct order.
Unique (DISTINCT ON) becomes "take first row per group" — effectively
free. Total warm latency expected to drop from 6.3s to well under 1s.

The previous two indexes (`ix_resolution_log_fail_reason_no_match`
partial btree and `ix_resolution_log_reason_detail_gin` GIN) are
NOT replaced — they serve filters DOWNSTREAM of the DISTINCT ON
(the 5-category allowlist on the materialized latest set + the
Tier-2 alias-add containment respectively) and become active once
the DISTINCT ON is fast.

The existing `ix_resolution_log_provider_record` is also NOT
dropped — it's used by `admin/queries.py:246, 1281, 1365`,
`scripts/tennis_dedup.py:309, 318`, and
`scripts/harvest_aliases.py:299`. Postgres can still satisfy
queries filtering on `(provider, provider_record_id)` alone from
either index; this migration is ADDITIVE per operator brief.

──────────────────────────────────────────────────────────────────
PRODUCTION LANDING PATH: console + stamp, NOT alembic upgrade.
──────────────────────────────────────────────────────────────────

This is the SECOND CONCURRENTLY migration in the chain. The first
(`a2c4f6d8e1b3`) established that this repo's async env.py path is
incompatible with both standard alembic CONCURRENTLY escape hatches:
the `autocommit_block()` and the COMMIT + execution_options
fallback both fail. The repo pattern is: build the index directly
via Neon console / psql, then `alembic stamp` to record the
revision without running `upgrade()`.

The `upgrade()` / `downgrade()` bodies below carry the DDL for
documentation + future replay (e.g., disaster recovery into a
fresh database), but DO NOT attempt `alembic upgrade head`
against production — it will fail the same way `a2c4f6d8e1b3`
did before the runbook was followed.

Operator-runbook (verbatim):

  # 1. Build the index online (non-blocking on the 130k+ row
  #    sp.resolution_log hot-write table):
  psql "$DATABASE_URL" -c "
    CREATE INDEX CONCURRENTLY IF NOT EXISTS
      ix_resolution_log_provider_record_decided_at
      ON sp.resolution_log
         (provider, provider_record_id, decided_at DESC);
  "

  # 2. Stamp the migration as applied (no upgrade() execution):
  alembic stamp b3d5e7f9a2c4

  # 3. Refresh planner statistics so the new index is picked up:
  psql "$DATABASE_URL" -c "ANALYZE sp.resolution_log;"

  # 4. Verify the planner now uses the new index + no Incremental
  #    Sort, on warm cache:
  psql "$DATABASE_URL" <<'SQL'
  EXPLAIN (ANALYZE, BUFFERS)
  WITH latest AS (
      SELECT DISTINCT ON (rl.provider, rl.provider_record_id)
          rl.provider, rl.provider_record_id, rl.reason_code,
          rl.reason_detail, rl.decided_at
      FROM sp.resolution_log rl
      WHERE rl.provider = 'fl'
      ORDER BY rl.provider, rl.provider_record_id,
               rl.decided_at DESC
  )
  SELECT count(*) FROM latest WHERE reason_code = 'no_match';
  SQL

  # Expected plan:
  #   - Index Scan using
  #     ix_resolution_log_provider_record_decided_at
  #     (NOT ix_resolution_log_provider_record)
  #   - NO Incremental Sort node
  #   - Total time well under 1s

  # 5. Re-run the FL dry-run to confirm candidate-select latency
  #    is under the 5s F6 ceiling:
  DATABASE_URL="$DATABASE_URL" \\
    python scripts/run_reresolution_pass.py --provider fl

Rollback: `DROP INDEX CONCURRENTLY` — also non-blocking. Same
console path.

Revision ID: b3d5e7f9a2c4
Revises: a2c4f6d8e1b3
Create Date: 2026-06-20
Updated: 2026-06-22 — Day-44 cleanup: upgrade()/downgrade() bodies
                     are now REPLAY-SAFE NO-OPS. See a2c4f6d8e1b3
                     for the full rationale (BOTH alembic
                     CONCURRENTLY escape hatches fail in this env.py;
                     console + stamp is canonical; the body is no-op
                     so any future alembic upgrade head records the
                     revision cleanly without attempting the failing
                     DDL).
"""
from alembic import op  # noqa: F401  (kept import for replay context)


revision = "b3d5e7f9a2c4"
down_revision = "a2c4f6d8e1b3"
branch_labels = None
depends_on = None

SCHEMA = "sp"


def upgrade() -> None:
    """REPLAY-SAFE NO-OP. See module docstring for the canonical
    console + stamp runbook and a2c4f6d8e1b3 docstring for the full
    explanation of why both alembic CONCURRENTLY escape hatches
    fail in this repo's env.py."""
    pass


def downgrade() -> None:
    """REPLAY-SAFE NO-OP. See module docstring for the canonical
    DROP INDEX CONCURRENTLY runbook."""
    pass
