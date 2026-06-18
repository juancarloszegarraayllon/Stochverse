"""Phase 2E re-resolution loop: two indexes on sp.resolution_log.

Per docs/reresolution/scope-2026-06-17.md F2 — DECIDED Day-42 two-tier
index strategy:

  Tier 1 — partial expression btree on (reason_detail->>'fail_reason')
           WHERE reason_code='no_match'. Supports the F1 structural
           pre-filter (allowlist + reason_code gate). Day-41 sizing
           established this filter does ~54% of candidate narrowing,
           so the partial index is evidence-driven, not speculative.

  Tier 2 — GIN with jsonb_path_ops on reason_detail. Supports F1's
           Tier-2 alias-add containment over the ~16,588 survivors
           that pass Tier 1.

Both indexes are CREATE INDEX CONCURRENTLY — non-blocking online
build on the production hot-write sp.resolution_log table
(130k+ rows). Plain CREATE INDEX would lock the table for the
duration of the build and is NOT acceptable.

──────────────────────────────────────────────────────────────────
First CONCURRENTLY migration in this repo's chain — establishes
the repo pattern. Future CONCURRENTLY migrations: mirror this.
──────────────────────────────────────────────────────────────────

Why this looks unusual:

This repo's `migrations/env.py` runs alembic under an async
(asyncpg) engine with `transaction_per_migration=True` (env.py
line 111). The standard alembic escape hatch
`op.get_context().autocommit_block()` fails the
`assert self._transaction is not None` assertion at
`alembic/runtime/migration.py:329` in this specific configuration —
same class as the Phase 1A async-alembic commit gotcha (alembic's
per-migration transaction tracker doesn't reliably reflect the
underlying DB transaction state through the sync-bridge that
`AsyncConnection.run_sync()` provides).

Day-42 production attempt with `autocommit_block()` failed cleanly
before any write (sp.alembic_version stayed at f1b3d5e7a9c2, no
indexes created — verified post-failure). Bug report logged.

Working pattern (used here):

  1. `op.execute("COMMIT")` — explicitly close alembic's
     per-migration transaction so the connection's transaction
     state machine is clean for the autocommit switch. With
     asyncpg + SQLAlchemy 2.0+, raw "COMMIT" SQL is interpreted
     by the connection's transaction state machine and committed.

  2. `op.get_bind().execution_options(isolation_level="AUTOCOMMIT")`
     — switch the connection to AUTOCOMMIT isolation for the
     duration of this migration. Without this, the next statement
     would implicit-BEGIN a new transaction and Postgres would
     reject CREATE INDEX CONCURRENTLY with
     "CREATE INDEX CONCURRENTLY cannot run inside a transaction
     block."

  3. Execute the CONCURRENTLY DDL on the autocommit-bound
     connection. Each statement is its own implicit-autocommit
     transaction.

On exit from upgrade(), alembic auto-begins a new transaction for
its sp.alembic_version row UPDATE + commit.

Operator-runbook fallback (in case the alembic-side pattern fails
on a future asyncpg/SQLAlchemy version drift):

  # Run the two CONCURRENTLY indexes via psql directly:
  psql "$DATABASE_URL" -c "
    CREATE INDEX CONCURRENTLY IF NOT EXISTS
      ix_resolution_log_fail_reason_no_match
      ON sp.resolution_log ((reason_detail->>'fail_reason'))
      WHERE reason_code = 'no_match';
  "
  psql "$DATABASE_URL" -c "
    CREATE INDEX CONCURRENTLY IF NOT EXISTS
      ix_resolution_log_reason_detail_gin
      ON sp.resolution_log USING gin (reason_detail jsonb_path_ops);
  "
  # Then mark this migration as applied without running upgrade():
  alembic stamp a2c4f6d8e1b3

Rollback: DROP INDEX CONCURRENTLY for both. Also non-blocking. Same
COMMIT + AUTOCOMMIT pattern.

Revision ID: a2c4f6d8e1b3
Revises: f1b3d5e7a9c2
Create Date: 2026-06-19
Updated: 2026-06-20 — autocommit_block() workaround per Day-42
                     bug report (production upgrade failure).
"""
from alembic import op
from sqlalchemy import text


revision = "a2c4f6d8e1b3"
down_revision = "f1b3d5e7a9c2"
branch_labels = None
depends_on = None

SCHEMA = "sp"


def _switch_to_autocommit():
    """Close alembic's per-migration transaction and return a
    connection bound in AUTOCOMMIT isolation. The repo pattern for
    CONCURRENTLY DDL — see module docstring for the why."""
    # Step 1: commit alembic's per-migration transaction.
    op.execute("COMMIT")
    # Step 2: switch isolation level so subsequent statements don't
    # implicit-BEGIN a new transaction. CREATE INDEX CONCURRENTLY
    # would otherwise fail with "cannot run inside a transaction
    # block."
    return op.get_bind().execution_options(isolation_level="AUTOCOMMIT")


def upgrade() -> None:
    conn = _switch_to_autocommit()

    # Tier 1: partial expression btree for the structural
    # pre-filter. The WHERE clause restricts the index to no_match
    # rows so the footprint is bounded by the unresolved-population
    # size rather than the full sp.resolution_log accretion.
    conn.execute(text(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
        "ix_resolution_log_fail_reason_no_match "
        f"ON {SCHEMA}.resolution_log "
        "((reason_detail->>'fail_reason')) "
        "WHERE reason_code = 'no_match'"
    ))

    # Tier 2: GIN with jsonb_path_ops for containment queries.
    # Smaller and faster than the default jsonb_ops operator class;
    # supports only @> (containment) which is exactly what the
    # alias-add signal needs ("does this reason_detail mention this
    # team_id?").
    conn.execute(text(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
        "ix_resolution_log_reason_detail_gin "
        f"ON {SCHEMA}.resolution_log "
        "USING gin (reason_detail jsonb_path_ops)"
    ))


def downgrade() -> None:
    conn = _switch_to_autocommit()
    conn.execute(text(
        "DROP INDEX CONCURRENTLY IF EXISTS "
        f"{SCHEMA}.ix_resolution_log_reason_detail_gin"
    ))
    conn.execute(text(
        "DROP INDEX CONCURRENTLY IF EXISTS "
        f"{SCHEMA}.ix_resolution_log_fail_reason_no_match"
    ))
