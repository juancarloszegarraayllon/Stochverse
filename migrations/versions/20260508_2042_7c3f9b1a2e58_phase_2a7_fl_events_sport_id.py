"""Phase 2A.7: sp.fl_events.sport_id (recover sport context per row).

FL ingestion polls per-sport (DEFAULT_FL_SPORT_IDS) but never wrote
the sport context onto sp.fl_events — neither column nor raw_payload
top-level. Result: the resolver runner couldn't pass a `sport=...`
argument to FLResolverModule.extract_signal, so every FL signal hit
the matcher's gate 2 (`sport_not_classified`) and was rejected. First
FL pass against production produced 0/19,753 auto-applies.

This migration adds the missing column + supporting index. The
ingestion update (ingestion/fl.py) populates it on the next
UPSERT — running `make backfill-fl` after merge fills sport_id on
every FL event currently inside the FL ±7 day window.

  ALTER TABLE sp.fl_events
    ADD COLUMN sport_id INTEGER REFERENCES sp.sports(id);

  CREATE INDEX ix_fl_events_sport_unresolved
    ON sp.fl_events (sport_id, last_seen_at DESC)
    WHERE fixture_id IS NULL;

The partial index serves the runner's hot query
  WHERE fixture_id IS NULL AND sport_id IS NOT NULL ORDER BY last_seen_at DESC
without bloating the full-table index footprint.

Revision ID: 7c3f9b1a2e58
Revises: bdf12a30e49b (Phase 2B)
Create Date: 2026-05-08 20:42:00 UTC
"""
from alembic import op
import sqlalchemy as sa


revision = "7c3f9b1a2e58"
down_revision = "bdf12a30e49b"
branch_labels = None
depends_on = None


SCHEMA = "sp"


def upgrade() -> None:
    op.add_column(
        "fl_events",
        sa.Column("sport_id", sa.Integer(), nullable=True),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_fl_events_sport_id_sports",
        source_table="fl_events",
        referent_table="sports",
        local_cols=["sport_id"],
        remote_cols=["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
    )
    # Partial index supporting the runner query
    #   WHERE fl_events.fixture_id IS NULL
    #     AND fl_events.sport_id IS NOT NULL
    #   ORDER BY last_seen_at DESC.
    # Excludes already-resolved rows (huge once parallel-run gets
    # going) and rows where ingestion couldn't classify sport.
    op.create_index(
        "ix_fl_events_sport_unresolved",
        "fl_events",
        ["sport_id", sa.text("last_seen_at DESC")],
        schema=SCHEMA,
        postgresql_where=sa.text("fixture_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_fl_events_sport_unresolved",
        table_name="fl_events",
        schema=SCHEMA,
    )
    op.drop_constraint(
        "fk_fl_events_sport_id_sports",
        "fl_events",
        type_="foreignkey",
        schema=SCHEMA,
    )
    op.drop_column("fl_events", "sport_id", schema=SCHEMA)
