"""Phase 2E re-resolution loop: composite partial indexes for the
unresolved + last_seen_at watermark predicate (attempt 4).

Per Day-44 perf finding: attempt-3 (MATERIALIZED CTE driver) killed
the seq scan on fl_events (540ms) but the inner LATERAL index scan
ran 33,882 times — total 13.6s warm. The driver set IS the
bottleneck: O(N_unresolved) per-row LATERAL lookups when N is large
will exceed any reasonable latency ceiling regardless of inner
index quality. No further query-shape tweak fixes this; the driver
set must be bounded.

Operator decision (Day-44): bound the MATERIALIZED CTE by
`last_seen_at > NOW() - INTERVAL '3 days'`. Production counts
informed the window: Kalshi unresolved 48,277 total → 11,299
within 7d → 7,487 within 3d. 3d cuts Kalshi (the binding
constraint) to ~3s warm vs the 5s F6 ceiling. FL lands lower.
2d and 3d are identical on Kalshi (7,487), so 3d costs nothing in
latency over 2d but gives more correctness margin (alias-add
events within the last 72h get caught).

This migration adds the partial expression index that supports the
windowed CTE scan. Without it, the planner has two suboptimal
choices:

  - Use `ix_*_unresolved` (partial WHERE fixture_id IS NULL): scans
    ALL unresolved rows then heap-fetches each for last_seen_at.
    N=33,882 → too slow.
  - Use `ix_*_last_seen` (non-partial): scans recent rows then
    heap-fetches each for fixture_id. The 3-day window includes
    mostly-resolved rows (FL writes ~18k/24h, most resolve quickly),
    so the scan would visit ~50k rows just to filter ~5k unresolved
    survivors.

The new composite partial index `(last_seen_at) WHERE
fixture_id IS NULL` is the deterministic fix:

  - Range scan for last_seen_at > X
  - Partial predicate excludes all resolved rows from the index
    entirely (no heap fetch needed for fixture_id)
  - Returns directly the unresolved+recent set the CTE wants

Both providers get one each:
  - ix_fl_events_unresolved_last_seen
    ON sp.fl_events (last_seen_at) WHERE fixture_id IS NULL
  - ix_kalshi_markets_unresolved_last_seen
    ON sp.kalshi_markets (last_seen_at) WHERE fixture_id IS NULL

──────────────────────────────────────────────────────────────────
PRODUCTION LANDING PATH: console + stamp, NOT alembic upgrade.
──────────────────────────────────────────────────────────────────

Third CONCURRENTLY migration in the chain (after a2c4f6d8e1b3 and
b3d5e7f9a2c4). Same Day-42-established pattern: this repo's async
env.py is incompatible with both alembic CONCURRENTLY escape
hatches (autocommit_block() asserts on self._transaction;
COMMIT + execution_options(AUTOCOMMIT) raises
InvalidRequestError: transaction already initialized).

Operator-runbook:

  # 1. Build both indexes online (non-blocking):
  psql "$DATABASE_URL" -c "
    CREATE INDEX CONCURRENTLY IF NOT EXISTS
      ix_fl_events_unresolved_last_seen
      ON sp.fl_events (last_seen_at)
      WHERE fixture_id IS NULL;
  "
  psql "$DATABASE_URL" -c "
    CREATE INDEX CONCURRENTLY IF NOT EXISTS
      ix_kalshi_markets_unresolved_last_seen
      ON sp.kalshi_markets (last_seen_at)
      WHERE fixture_id IS NULL;
  "

  # 2. Stamp the migration as applied:
  alembic stamp c5e7f9a3b1d4

  # 3. Refresh planner statistics so the new indexes get picked up:
  psql "$DATABASE_URL" -c "ANALYZE sp.fl_events;"
  psql "$DATABASE_URL" -c "ANALYZE sp.kalshi_markets;"

  # 4. EXPLAIN ANALYZE the attempt-4 FL query on warm cache;
  #    confirm:
  #    - CTE Scan using ix_fl_events_unresolved_last_seen
  #      (NOT ix_fl_events_unresolved, NOT ix_fl_events_last_seen)
  #    - NO Seq Scan on sp.fl_events
  #    - Inner LATERAL still uses
  #      ix_resolution_log_provider_record_decided_at
  #    - Total time well under 5s
  psql "$DATABASE_URL" <<'SQL'
  EXPLAIN (ANALYZE, BUFFERS)
  WITH unresolved_fl_events AS MATERIALIZED (
      SELECT fl_event_id FROM sp.fl_events
      WHERE fixture_id IS NULL
        AND last_seen_at > NOW() - INTERVAL '3 days'
  )
  SELECT count(*) FROM unresolved_fl_events;
  SQL

  # 5. Re-run FL + Kalshi dry-runs; confirm candidate-select latency
  #    is under the 5s F6 ceiling on both providers:
  DATABASE_URL="$DATABASE_URL" \\
    python scripts/run_reresolution_pass.py --provider fl
  DATABASE_URL="$DATABASE_URL" \\
    python scripts/run_reresolution_pass.py --provider kalshi

Rollback: DROP INDEX CONCURRENTLY both. Also non-blocking. Same
console + stamp path.

Revision ID: c5e7f9a3b1d4
Revises: b3d5e7f9a2c4
Create Date: 2026-06-20
"""
from alembic import op
from sqlalchemy import text


revision = "c5e7f9a3b1d4"
down_revision = "b3d5e7f9a2c4"
branch_labels = None
depends_on = None

SCHEMA = "sp"


def _switch_to_autocommit():
    """Repo pattern for CONCURRENTLY DDL — see a2c4f6d8e1b3
    docstring for the full why. Production landing path is console
    + stamp per this migration's docstring; this body is here for
    documentation + replay against a fresh database."""
    op.execute("COMMIT")
    return op.get_bind().execution_options(isolation_level="AUTOCOMMIT")


def upgrade() -> None:
    """NOT INVOKED IN PRODUCTION — see migration docstring for the
    console + stamp runbook. Body carries the DDL for replay against
    a fresh database."""
    conn = _switch_to_autocommit()
    conn.execute(text(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
        "ix_fl_events_unresolved_last_seen "
        f"ON {SCHEMA}.fl_events (last_seen_at) "
        "WHERE fixture_id IS NULL"
    ))
    conn.execute(text(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
        "ix_kalshi_markets_unresolved_last_seen "
        f"ON {SCHEMA}.kalshi_markets (last_seen_at) "
        "WHERE fixture_id IS NULL"
    ))


def downgrade() -> None:
    conn = _switch_to_autocommit()
    conn.execute(text(
        "DROP INDEX CONCURRENTLY IF EXISTS "
        f"{SCHEMA}.ix_kalshi_markets_unresolved_last_seen"
    ))
    conn.execute(text(
        "DROP INDEX CONCURRENTLY IF EXISTS "
        f"{SCHEMA}.ix_fl_events_unresolved_last_seen"
    ))
