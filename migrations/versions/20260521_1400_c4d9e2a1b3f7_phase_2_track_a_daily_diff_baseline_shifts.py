"""Phase 2 Track A: measurement-infrastructure tables (daily_diff_reports + baseline_shifts).

Two new tables landing as part of Phase 2 Track A Deliverable 2 per
PR #175's scope doc:

  - sp.daily_diff_reports: one row per daily-diff cron pass. Carries
    scope-filtered metrics, per-sport / per-tier breakdowns, sample
    disagreements + histogram in a JSONB column for flexibility.
    Queryable for trend analysis; renders to operator-facing
    markdown on-demand via scripts/render_daily_diff_report.py.

  - sp.baseline_shifts: one row per population-level event that
    causes a discontinuous metric shift. Dedup events, scope-filter
    changes, alias bootstraps, threshold changes. Operator inserts
    a row when an event happens; daily-diff render reads this table
    + sp.daily_diff_reports correlating on date.

Both tables are scoped to Track A's ~6-10 week lifespan per
architecture doc §11.5 throw-away-infrastructure pattern. Drop
post-Phase-3 cutover via the deprecation runbook in PR #175 §14.

## Schema decisions

### sp.daily_diff_reports

- `report_date` (DATE): the day the report covers (window_end's date).
  Indexed for trend queries.
- `window_start` / `window_end` (TIMESTAMPTZ): the exact 24h slice
  the cron pass measured against. Audit-trail for "which records
  contributed to this row's metrics".
- `metrics` (JSONB): flexible per-sport / per-tier metric storage.
  JSONB chosen over per-column because the metric vocabulary may
  evolve (new sports, new tiers, new scope-filter rules) without
  schema migrations.
- `scope_filter_version` (TEXT): version stamp for the filter rules
  applied. When NON_SPORT filter (#174) lands or vocabulary changes
  (#160), the version bumps so historical reports can be re-interpreted.
- `report_json` (JSONB): sample disagreements (post-Deliverable 1)
  + histogram data. Operator-facing details that don't warrant
  promotion to first-class columns.
- `legacy_comparison_present` (BOOLEAN): false during Deliverable 2,
  true once Deliverable 1 lands and legacy-vs-new comparison is
  integrated. Lets the render script distinguish D2-only reports
  from D2+D1 reports.

No FK constraints to `sp.resolution_log` or `sp.review_queue` —
report rows reference their measurement window via timestamps,
not row-level joins. Decoupled lifecycle simplifies the eventual
deprecation.

### sp.baseline_shifts

Schema per PR #175 §8. `event_type` left as TEXT (not enum) so
new event types can be added without schema migration during the
throw-away infrastructure's lifetime.

`affected_population` is human-readable text describing what
population the event affected (e.g., "Tennis players (cross-format
dupes)", "NON_SPORT records"). Render script displays this verbatim
in the operator markdown.

`expected_metric_delta` is optional human-readable text describing
the anticipated direction + magnitude of the metric shift (e.g.,
"Tennis auto-apply rate +5-10% post-dedup"). Sets operator
expectation before the event lands; can be compared against actual
metric movement in subsequent days' reports.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "c4d9e2a1b3f7"
down_revision = "b8e1f4c2a7d3"
branch_labels = None
depends_on = None


SCHEMA = "sp"


def upgrade() -> None:
    # ── sp.daily_diff_reports ────────────────────────────────────
    op.create_table(
        "daily_diff_reports",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column(
            "window_start",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "window_end",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("total_records_scanned", sa.Integer(), nullable=False),
        sa.Column(
            "metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment=(
                "Per-sport and per-tier scope-filtered metrics. "
                "Schema documented in scripts/daily_diff.py. "
                "JSONB to allow evolution without schema migration."
            ),
        ),
        sa.Column(
            "scope_filter_version",
            sa.Text(),
            nullable=False,
            comment=(
                "Version stamp for the filter rules applied "
                "(NON_SPORT, prop-market vocabulary, etc.). When "
                "the rules change, version bumps so historical "
                "reports can be re-interpreted."
            ),
        ),
        sa.Column(
            "report_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment=(
                "Sample disagreements + histogram data + "
                "Deliverable-1 legacy-vs-new comparison details "
                "(when present)."
            ),
        ),
        sa.Column(
            "legacy_comparison_present",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment=(
                "False during Deliverable 2; true after Deliverable 1 "
                "integrates the legacy comparison dimension."
            ),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_daily_diff_reports_report_date",
        "daily_diff_reports",
        ["report_date"],
        schema=SCHEMA,
    )
    op.create_unique_constraint(
        "uq_daily_diff_reports_report_date",
        "daily_diff_reports",
        ["report_date"],
        schema=SCHEMA,
    )

    # ── sp.baseline_shifts ────────────────────────────────────────
    op.create_table(
        "baseline_shifts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "event_type",
            sa.Text(),
            nullable=False,
            comment=(
                "Population-level event type. Suggested values: "
                "'dedup', 'scope_filter', 'alias_bootstrap', "
                "'threshold_change', 'other'. TEXT (not enum) "
                "to allow new types without schema migration."
            ),
        ),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column(
            "affected_population",
            sa.Text(),
            nullable=False,
            comment=(
                "Human-readable text describing the affected "
                "population (e.g., 'Tennis players (cross-format "
                "dupes)', 'NON_SPORT records')."
            ),
        ),
        sa.Column(
            "expected_metric_delta",
            sa.Text(),
            nullable=True,
            comment=(
                "Optional anticipated direction + magnitude "
                "(e.g., 'Tennis auto-apply rate +5-10% post-dedup'). "
                "Sets operator expectation before the event lands."
            ),
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "created_by",
            sa.Text(),
            nullable=True,
            comment=(
                "Operator name, 'script', or PR number — provenance "
                "for the annotation."
            ),
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_baseline_shifts_event_date",
        "baseline_shifts",
        ["event_date"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_baseline_shifts_event_type",
        "baseline_shifts",
        ["event_type"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    # Drop in reverse-create order. Indexes drop automatically with
    # the tables; explicit drops included for symmetry + clarity.
    op.drop_index(
        "ix_baseline_shifts_event_type",
        table_name="baseline_shifts",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_baseline_shifts_event_date",
        table_name="baseline_shifts",
        schema=SCHEMA,
    )
    op.drop_table("baseline_shifts", schema=SCHEMA)
    op.drop_constraint(
        "uq_daily_diff_reports_report_date",
        "daily_diff_reports",
        schema=SCHEMA,
        type_="unique",
    )
    op.drop_index(
        "ix_daily_diff_reports_report_date",
        table_name="daily_diff_reports",
        schema=SCHEMA,
    )
    op.drop_table("daily_diff_reports", schema=SCHEMA)
