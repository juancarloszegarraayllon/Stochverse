"""Phase 3: cutover_config singleton table for feature-flag control.

Single-row table (CHECK id=1) holding the traffic percentage,
enabled sports whitelist, and minimum cohort floor. Read by a
background loop (main._cutover_config_refresh_loop) and cached in
memory; NEVER read on the request path. Operator flips via UPDATE.

Seeded with pct=0, enabled_sports={} → serves v3 for all traffic
until operator explicitly widens.

Chain: c5e7f9a3b1d4 → d7f9a2c4e6b8 (this).
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "d7f9a2c4e6b8"
down_revision = "c5e7f9a3b1d4"
branch_labels = None
depends_on = None


SCHEMA = "sp"


def upgrade() -> None:
    op.create_table(
        "cutover_config",
        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
            server_default=sa.text("1"),
            comment=(
                "Singleton row constraint: CHECK id=1 below ensures "
                "only one row exists. INSERT ... ON CONFLICT is safe."
            ),
        ),
        sa.Column(
            "traffic_pct",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment=(
                "Operator-controlled v4 traffic percentage (0-100). "
                "Actual cohort size derives from this + min_cohort_series "
                "via the cutover.compute_effective_cohort_size() helper. "
                "0 = serve v3 for everything; 100 = serve v4 for all "
                "enabled_sports (subject to per-request fallback on error)."
            ),
        ),
        sa.Column(
            "enabled_sports",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
            comment=(
                "Whitelist of sport names (case-sensitive, matches "
                "_resolve_series_sport_dynamic output) to enable v4 "
                "cohort membership for. Empty array = v4 disabled "
                "regardless of traffic_pct. Soccer-first per Phase 3 "
                "opener rollout plan; expand one sport at a time."
            ),
        ),
        sa.Column(
            "min_cohort_series",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("2"),
            comment=(
                "MIN-COHORT FLOOR (Day-64 spec): cohort_size = "
                "max(round(universe * traffic_pct / 100), min_cohort_series). "
                "Prevents lumpy 5%-of-30-series stages from producing "
                "0-4 series with heavy variance. Cohort is the N lowest-"
                "hash series in enabled_sports (deterministic, no per-"
                "worker divergence given identical universe)."
            ),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_by",
            sa.Text(),
            nullable=True,
            comment=(
                "Operator name or PR/session marker for the last flip. "
                "Provenance trail for cutover audit."
            ),
        ),
        sa.CheckConstraint("id = 1", name="cutover_config_singleton_id"),
        sa.CheckConstraint(
            "traffic_pct >= 0 AND traffic_pct <= 100",
            name="cutover_config_pct_range",
        ),
        sa.CheckConstraint(
            "min_cohort_series >= 0",
            name="cutover_config_min_cohort_nonneg",
        ),
        schema=SCHEMA,
    )
    # Seed the singleton row with safe defaults (pct=0, no sports).
    # Serving path serves v3 for all traffic on first request post-
    # deploy — operator must UPDATE to widen.
    op.execute(
        f"""
        INSERT INTO {SCHEMA}.cutover_config
            (id, traffic_pct, enabled_sports, min_cohort_series, updated_by)
        VALUES (1, 0, '{{}}', 2, 'migration:d7f9a2c4e6b8')
        ON CONFLICT (id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_table("cutover_config", schema=SCHEMA)
