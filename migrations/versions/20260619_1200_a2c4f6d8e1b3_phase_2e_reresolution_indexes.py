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
(130k+ rows). CREATE INDEX CONCURRENTLY cannot run inside a
transaction; alembic's `op.get_context().autocommit_block()` is the
documented escape hatch (the connection switches to AUTOCOMMIT for
the duration of the block, then back to the normal
transaction-per-migration shape).

Rollback: DROP INDEX CONCURRENTLY for both, also non-blocking.

Revision ID: a2c4f6d8e1b3
Revises: f1b3d5e7a9c2
Create Date: 2026-06-19
"""
from alembic import op


revision = "a2c4f6d8e1b3"
down_revision = "f1b3d5e7a9c2"
branch_labels = None
depends_on = None

SCHEMA = "sp"


def upgrade() -> None:
    # CREATE INDEX CONCURRENTLY cannot run inside a transaction.
    # autocommit_block() switches the connection out of alembic's
    # transaction-per-migration scope for the duration of the block.
    with op.get_context().autocommit_block():
        # Tier 1: partial expression btree for the structural
        # pre-filter. The WHERE clause matches F1 condition (3) —
        # reason_code = 'no_match' — so the index footprint is bounded
        # by the unresolved-population size rather than the full
        # sp.resolution_log accretion.
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_resolution_log_fail_reason_no_match "
            f"ON {SCHEMA}.resolution_log "
            "((reason_detail->>'fail_reason')) "
            "WHERE reason_code = 'no_match'"
        )

        # Tier 2: GIN with jsonb_path_ops for containment queries.
        # Smaller and faster than the default jsonb_ops operator
        # class; supports only @> (containment) which is exactly
        # what the alias-add signal needs ("does this reason_detail
        # mention this team_id?").
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_resolution_log_reason_detail_gin "
            f"ON {SCHEMA}.resolution_log "
            "USING gin (reason_detail jsonb_path_ops)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS "
            f"{SCHEMA}.ix_resolution_log_reason_detail_gin"
        )
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS "
            f"{SCHEMA}.ix_resolution_log_fail_reason_no_match"
        )
