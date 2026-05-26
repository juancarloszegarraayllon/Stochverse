"""Tennis dedup: sp.dedup_audit table for merge rollback.

Per Tennis dedup scope-doc (PR #188) §4.1 + F7 decision: one row per
cluster merge capturing full pre-merge state. Rollback reads the audit
row and reverses the merge at per-merge-group granularity.

Throw-away per architecture doc §14 — deprecated post-Phase-3 cutover
alongside the daily-diff measurement-infrastructure tables.

Revision ID: e2a7f3c1d4b8
Revises: c4d9e2a1b3f7
Create Date: 2026-05-26
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY


revision = "e2a7f3c1d4b8"
down_revision = "c4d9e2a1b3f7"
branch_labels = None
depends_on = None

SCHEMA = "sp"


def upgrade() -> None:
    op.create_table(
        "dedup_audit",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("canonical_id", UUID(as_uuid=True), nullable=False,
                  comment="Surviving team_id after merge."),
        sa.Column("merged_ids", ARRAY(UUID(as_uuid=True)), nullable=False,
                  comment="Deleted team_id(s) — 1 or more per cluster merge."),
        sa.Column("pre_state", JSONB, nullable=False,
                  comment=(
                      "Full pre-merge state for rollback: both team rows + alias sets "
                      "+ affected fixtures {fixture_id, original_home_team_id, original_away_team_id} "
                      "+ affected review_queue rows {review_queue_id, original_candidate_fixtures_jsonb}."
                  )),
        sa.Column("merged_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("merge_phase", sa.Text, nullable=False,
                  comment="'phase_a' (automated, F8 criterion met) or 'phase_b' (operator-reviewed)."),
        sa.Column("merge_pr", sa.Text, nullable=True,
                  comment="PR number for provenance."),
        sa.PrimaryKeyConstraint("id", name="pk_dedup_audit"),
        schema=SCHEMA,
    )
    op.create_index("ix_dedup_audit_canonical", "dedup_audit", ["canonical_id"], schema=SCHEMA)
    op.create_index("ix_dedup_audit_merged_at", "dedup_audit", ["merged_at"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index("ix_dedup_audit_merged_at", table_name="dedup_audit", schema=SCHEMA)
    op.drop_index("ix_dedup_audit_canonical", table_name="dedup_audit", schema=SCHEMA)
    op.drop_table("dedup_audit", schema=SCHEMA)
