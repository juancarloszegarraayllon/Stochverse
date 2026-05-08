"""Phase 2B: sp.resolver_runs + sp.fixtures.competition_id nullable.

Two coordinated schema changes for the strict-tier resolver:

  1. sp.resolver_runs — new table. Per-run audit row written by
     scripts/run_resolver_pass.py and (later) Phase 2E's live
     runner. Provides queryable metrics for parallel-run reports
     without log-grepping. Schema columns covered in the Phase 2B
     design doc:
       provider TEXT  ('fl' | 'kalshi')
       run_mode TEXT  ('standalone' | 'cron' | 'live')
       run_id, started_at, finished_at, resolver_version,
       records_scanned, auto_applies, no_match, crashes,
       legacy_diff_count (NULL for FL), legacy_diff_details,
       latency_p95_ms, extra (JSONB)

  2. sp.fixtures.competition_id — alter from NOT NULL to NULL.
     Required for the strict-tier sport-only fallback: when a
     provider record's competition_hint can't be resolved to an
     existing sp.competitions row (sp.competitions is empty
     until Phase 2C seeds it), the matcher creates a fixture
     with competition_id=NULL. Sport stays implicit via the
     home/away teams' sport_id. Identity rule from architecture
     §5.4 is unchanged — kickoff_at + team pair define identity;
     competition is mutable metadata.

Revision ID: bdf12a30e49b
Revises: d8e717ed79dd (seed_sp_sports)
Create Date: 2026-05-08 15:37:00 UTC
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "bdf12a30e49b"
down_revision = "d8e717ed79dd"
branch_labels = None
depends_on = None


SCHEMA = "sp"


def upgrade() -> None:
    # ── 1. sp.resolver_runs ────────────────────────────────────
    op.create_table(
        "resolver_runs",
        sa.Column("id",                  sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("run_id",              UUID(as_uuid=True), nullable=False),
        sa.Column("resolver_version",    sa.Text(), nullable=False),
        sa.Column("provider",            sa.Text(), nullable=False),
        sa.Column("run_mode",            sa.Text(), nullable=False),
        sa.Column("started_at",          sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at",         sa.DateTime(timezone=True)),
        sa.Column("records_scanned",     sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("auto_applies",        sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("no_match",            sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("crashes",             sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("legacy_diff_count",   sa.Integer()),                       # Kalshi only; NULL for FL
        sa.Column("legacy_diff_details", JSONB()),                            # provider records that differed
        sa.Column("latency_p95_ms",      sa.Integer()),
        sa.Column("extra",               JSONB(), server_default=sa.text("'{}'::jsonb")),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_resolver_runs_provider_started",
        "resolver_runs", ["provider", "started_at"], schema=SCHEMA,
        postgresql_using="btree",
    )
    op.create_index(
        "ix_resolver_runs_run_mode_started",
        "resolver_runs", ["run_mode", "started_at"], schema=SCHEMA,
        postgresql_using="btree",
    )

    # ── 2. sp.fixtures.competition_id → NULLABLE ──────────────
    op.alter_column(
        "fixtures",
        "competition_id",
        existing_type=UUID(as_uuid=True),
        nullable=True,
        schema=SCHEMA,
    )


def downgrade() -> None:
    # Reverse in opposite order. Note: making competition_id NOT NULL
    # again will fail if any rows have NULL competition_id by the
    # time downgrade runs. Operator must clean those up first
    # (sp.fixtures.competition_id IS NULL → backfill or DELETE).
    op.alter_column(
        "fixtures",
        "competition_id",
        existing_type=UUID(as_uuid=True),
        nullable=False,
        schema=SCHEMA,
    )

    op.drop_index("ix_resolver_runs_run_mode_started", "resolver_runs", schema=SCHEMA)
    op.drop_index("ix_resolver_runs_provider_started", "resolver_runs", schema=SCHEMA)
    op.drop_table("resolver_runs", schema=SCHEMA)
