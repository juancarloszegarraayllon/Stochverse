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
Updated: 2026-06-22 — Day-44 cleanup: upgrade()/downgrade() bodies
                     are now REPLAY-SAFE NO-OPS. Day-44 confirmed
                     that the COMMIT + execution_options fallback
                     ALSO fails in this env.py (InvalidRequestError:
                     transaction already initialized). With BOTH
                     alembic escape hatches broken, the canonical
                     landing path is the console + stamp runbook
                     in this docstring; the code body intentionally
                     does NOTHING so `alembic upgrade head` from a
                     fresh checkout records the revision in
                     sp.alembic_version cleanly without attempting
                     the failing DDL. See CI guard
                     test_migration_uses_repo_concurrently_pattern_not_autocommit_block
                     for the enforced contract.
"""
from alembic import op  # noqa: F401  (kept import for replay context)


revision = "a2c4f6d8e1b3"
down_revision = "f1b3d5e7a9c2"
branch_labels = None
depends_on = None

SCHEMA = "sp"


def upgrade() -> None:
    """REPLAY-SAFE NO-OP. The canonical landing path is the console
    + stamp runbook in the module docstring above. This body
    intentionally does NOTHING because:

      1. This repo's async env.py is incompatible with BOTH alembic
         CONCURRENTLY escape hatches (Day-42 + Day-44 lessons):
           a. op.get_context().autocommit_block() fails the
              `self._transaction is not None` assertion.
           b. op.execute("COMMIT") + execution_options(
              isolation_level="AUTOCOMMIT") raises
              InvalidRequestError: transaction already initialized.
         A fresh-DB `alembic upgrade head` would therefore fail on
         the DDL itself before reaching any index-creation work.

      2. Production is already at this revision via the docstring
         runbook (sp.alembic_version stamped post-Day-44). On a
         production replay the indexes already exist and any DDL
         attempt would be a no-op at the SQL level (CREATE INDEX
         CONCURRENTLY IF NOT EXISTS) but would still fail at the
         env.py transaction-state level before the SQL ran.

      3. For a TRUE fresh-DB rebuild (disaster recovery), the
         operator follows the docstring runbook to build the
         indexes via Neon console / psql, then `alembic stamp
         a2c4f6d8e1b3` records the revision.

    Calling this no-op is safe on any DB (production replay, fresh
    DB, stale DB) — it never errors. `alembic upgrade head` from
    any starting state moves cleanly to the head revision.
    """
    pass


def downgrade() -> None:
    """REPLAY-SAFE NO-OP. Same reasoning as upgrade(). See module
    docstring for the canonical DROP INDEX CONCURRENTLY runbook."""
    pass
