"""Tennis dedup: add rolled_back_at column to sp.dedup_audit.

Nullable TIMESTAMPTZ column. Set by the --rollback path when a merge
is reversed. Preserves full audit trail: merged state (merged_at) +
rolled-back state (rolled_back_at) both visible in the same row.

Revision ID: f1b3d5e7a9c2
Revises: e2a7f3c1d4b8
Create Date: 2026-05-26
"""
from alembic import op
import sqlalchemy as sa


revision = "f1b3d5e7a9c2"
down_revision = "e2a7f3c1d4b8"
branch_labels = None
depends_on = None

SCHEMA = "sp"


def upgrade() -> None:
    op.add_column(
        "dedup_audit",
        sa.Column(
            "rolled_back_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Set when --rollback reverses this merge. NULL = merge is active.",
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("dedup_audit", "rolled_back_at", schema=SCHEMA)
