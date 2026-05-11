"""Seed sp.sports with the finite sport list.

Phase 2A.5 deliverable per SP Architecture v1.4 §5.4 + Phase 2B
design doc. Populates sp.sports with the 17-sport list our ingestion
covers, each with the per-sport auto_link_drift_minutes value the
resolver will use when matching against existing fixtures.

The `name` column is set to match legacy public.entities.sport
exactly (case-sensitive) so the bootstrap script can lookup
sp_sports_id by joining on legacy entity rows without case folding.

Drift thresholds (from architecture v1.4 §5.4 + design doc):
  Soccer:           24 hours = 1440 min  (cup competitions get 720
                                          via competition-level
                                          override later; sport-level
                                          default is 24h)
  Tennis:            6 hours =  360 min  (matches reschedule within
                                          tournament day)
  Basketball:       12 hours =  720 min
  Hockey:           24 hours = 1440 min
  American Football: 24 hours = 1440 min
  Baseball:          6 hours =  360 min  (doubleheaders / weather)
  All others:       24 hours = 1440 min  (default)

Idempotent: re-running this migration is a no-op via ON CONFLICT
(code) DO NOTHING.

Revision ID: d8e717ed79dd
Revises: 8f404e0dc89a
Create Date: 2026-05-08 12:58:00 UTC
"""
from alembic import op
import sqlalchemy as sa


revision = "d8e717ed79dd"
down_revision = "8f404e0dc89a"
branch_labels = None
depends_on = None


SCHEMA = "sp"


# (code, name, drift_minutes)
# `name` matches legacy public.entities.sport exactly (case-sensitive)
# so scripts/bootstrap_sp_teams.py can look up sport_id by name.
SPORTS: list[tuple[str, str, int]] = [
    ("soccer",     "Soccer",            1440),
    ("tennis",     "Tennis",             360),
    ("basketball", "Basketball",         720),
    ("hockey",     "Hockey",            1440),
    ("football",   "American Football", 1440),
    ("baseball",   "Baseball",           360),
    ("handball",   "Handball",          1440),
    ("cricket",    "Cricket",           1440),
    ("volleyball", "Volleyball",        1440),
    ("rugby_u",    "Rugby Union",       1440),
    ("aussie_rules", "Aussie Rules",    1440),
    ("rugby_l",    "Rugby League",      1440),
    ("mma",        "MMA",               1440),
    ("boxing",     "Boxing",            1440),
    ("golf",       "Golf",              1440),
    ("snooker",    "Snooker",           1440),
    ("darts",      "Darts",             1440),
]


def upgrade() -> None:
    # Use ON CONFLICT on the unique `code` column so the migration is
    # idempotent even on re-runs (e.g., re-application after a
    # downgrade or against a partially-seeded environment).
    for code, name, drift in SPORTS:
        op.execute(sa.text(
            f"""
            INSERT INTO {SCHEMA}.sports (code, name, auto_link_drift_minutes)
            VALUES (:code, :name, :drift)
            ON CONFLICT (code) DO NOTHING
            """
        ).bindparams(code=code, name=name, drift=drift))


def downgrade() -> None:
    # Remove only the rows this migration inserted. Do NOT TRUNCATE
    # — operators may have added custom sports manually.
    codes = tuple(c for c, _, _ in SPORTS)
    op.execute(
        sa.text(f"DELETE FROM {SCHEMA}.sports WHERE code = ANY(:codes)")
        .bindparams(codes=list(codes))
    )
