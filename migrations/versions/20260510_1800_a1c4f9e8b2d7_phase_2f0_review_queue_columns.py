"""Phase 2F.0: sp.review_queue columns for the operator review-queue UI.

Per PHASE_2F_DESIGN.md rev1.1 (PR #112, merged at b3226c0). Adds three
columns and one partial index to support the 2F.1 admin UI's hot
read path:

  ALTER TABLE sp.review_queue
    ADD COLUMN reason_detail    JSONB                NULL,
    ADD COLUMN provider_title   TEXT                 NULL,
    ADD COLUMN rejection_count  INTEGER NOT NULL     DEFAULT 0;

  CREATE INDEX ix_review_queue_pending_confidence
    ON sp.review_queue (status, confidence DESC, created_at)
    WHERE status = 'pending';

Column rationale (per the design doc):

- `reason_detail` (JSONB, nullable): snapshot of MatchResult.reason_detail
  at insertion. Includes canonical_home/canonical_away, ratios, fail
  reasons, collision flags. Denormalized so the UI reads a single
  table per page; staleness is acceptable because the matcher
  decision was correct AT INSERT and that's what the operator is
  reviewing. Existing 2,263 pending rows have NULL — UI shows
  "(not snapshotted — review_queue row predates 2F.0)".

- `provider_title` (TEXT, nullable): snapshot of the human-readable
  provider title (Kalshi's raw_payload->>'title', FL's synthesized
  "home vs away"). Saves the per-record JSONB parsing on every page
  load. Existing rows fall back to a UI-time JOIN of provider tables.

- `rejection_count` (INTEGER NOT NULL DEFAULT 0): tracks cumulative
  reject clicks per record. Per Q4 revised: re-queueable rejection
  is correct, but this column is the guardrail against operator
  burnout cycles. 2F.1 surfaces it in the list view; 2F.X adds the
  unreject button + runner-side `rejection_count >= 3 AND
  candidate_fixtures unchanged` skip logic.

Partial index rationale:

The 2F.1 list view's default query is

  SELECT ... FROM sp.review_queue
  WHERE status = 'pending'
  ORDER BY confidence DESC, created_at DESC
  LIMIT 50 OFFSET ?

The partial index covers the WHERE clause AND the ORDER BY without
sorting at query time. `WHERE status = 'pending'` keeps the index
small (decided rows don't bloat it). Latency budget: <500 ms p95
on the list view per the design doc.

Revision ID: a1c4f9e8b2d7
Revises: 7c3f9b1a2e58 (Phase 2A.7)
Create Date: 2026-05-10 18:00:00 UTC
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "a1c4f9e8b2d7"
down_revision = "7c3f9b1a2e58"
branch_labels = None
depends_on = None


SCHEMA = "sp"


def upgrade() -> None:
    op.add_column(
        "review_queue",
        sa.Column("reason_detail", JSONB(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "review_queue",
        sa.Column("provider_title", sa.Text(), nullable=True),
        schema=SCHEMA,
    )
    # rejection_count: NOT NULL with server-side default so the column
    # backfills automatically on the existing 2,263 rows. Populating
    # via a one-shot UPDATE inside the migration would lock the table
    # under concurrent reads; the server_default route is online.
    op.add_column(
        "review_queue",
        sa.Column(
            "rejection_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        schema=SCHEMA,
    )
    # Partial index supporting the 2F.1 list view's hot query:
    #   WHERE status = 'pending' ORDER BY confidence DESC, created_at DESC.
    # The (status, confidence DESC, created_at) ordering matches the
    # query so Postgres can serve LIMIT/OFFSET pagination directly
    # off the index without an extra sort step.
    op.create_index(
        "ix_review_queue_pending_confidence",
        "review_queue",
        ["status", sa.text("confidence DESC"), "created_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_review_queue_pending_confidence",
        table_name="review_queue",
        schema=SCHEMA,
    )
    op.drop_column("review_queue", "rejection_count", schema=SCHEMA)
    op.drop_column("review_queue", "provider_title", schema=SCHEMA)
    op.drop_column("review_queue", "reason_detail", schema=SCHEMA)
