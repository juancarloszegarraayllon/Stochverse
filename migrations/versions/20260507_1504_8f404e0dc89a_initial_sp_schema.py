"""Initial SP Architecture schema — canonical entities + provider tables.

Phase 1A deliverable per SP Architecture v1.2 §11.2. Creates the
`sp` schema and 12 tables that house the canonical entity model
(sports / competitions / teams / team_aliases / fixtures), the
provider-record tables (fl_events / kalshi_markets /
polymarket_markets / oddsapi_events), the resolution support
(resolution_log / review_queue), and the operations table
(provider_api_calls).

The schema is intentionally namespaced — none of these tables
collide with the legacy `public.*` tables managed manually via
models.py (entities, events, markets, prices, etc.). The
`include_object` filter in env.py enforces this isolation:
autogenerate against this revision will never propose changes to
the legacy schema.

Revision ID: 8f404e0dc89a
Revises:
Create Date: 2026-05-07 15:04:00 UTC
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


# revision identifiers, used by Alembic.
revision = "8f404e0dc89a"
down_revision = None
branch_labels = None
depends_on = None


SCHEMA = "sp"


def upgrade() -> None:
    # Schema first — alembic version table will live inside it
    # (configured via version_table_schema in env.py).
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")

    # ── 1. Sports ──────────────────────────────────────────────
    op.create_table(
        "sports",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.Text(), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "auto_link_drift_minutes",
            sa.Integer(),
            nullable=False,
            server_default=sa.text(str(24 * 60)),
            comment="Maximum kickoff drift (minutes) for resolver to auto-link a provider record to an existing fixture; beyond this routes to review_queue (architecture §5.4).",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        schema=SCHEMA,
    )

    # ── 2. Competitions ───────────────────────────────────────
    op.create_table(
        "competitions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("sport_id", sa.Integer(), sa.ForeignKey(f"{SCHEMA}.sports.id"), nullable=False),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("normalized_name", sa.Text(), nullable=False),
        sa.Column("country_code", sa.String(3)),
        sa.Column("season", sa.Text()),
        sa.Column("competition_type", sa.Text()),
        sa.Column("kalshi_series_bases", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("fl_tournament_stage_ids", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("polymarket_slugs", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("oddsapi_keys", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        schema=SCHEMA,
    )
    op.create_index("ix_competitions_sport_normalized", "competitions", ["sport_id", "normalized_name"], schema=SCHEMA)

    # ── 3. Teams ──────────────────────────────────────────────
    op.create_table(
        "teams",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("sport_id", sa.Integer(), sa.ForeignKey(f"{SCHEMA}.sports.id"), nullable=False),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("normalized_name", sa.Text(), nullable=False),
        sa.Column("country_code", sa.String(3)),
        sa.Column("logo_url", sa.Text()),
        sa.Column("logo_source", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        schema=SCHEMA,
    )
    op.create_index("ix_teams_sport_normalized", "teams", ["sport_id", "normalized_name"], schema=SCHEMA)

    # ── 4. Team aliases ───────────────────────────────────────
    op.create_table(
        "team_aliases",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("team_id", UUID(as_uuid=True), sa.ForeignKey(f"{SCHEMA}.teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("alias", sa.Text(), nullable=False),
        sa.Column("alias_normalized", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default=sa.text("1.0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("alias_normalized", "source", name="uq_team_aliases_alias_normalized_source"),
        schema=SCHEMA,
    )
    op.create_index("ix_team_aliases_alias_normalized", "team_aliases", ["alias_normalized"], schema=SCHEMA)

    # ── 5. Fixtures ───────────────────────────────────────────
    op.create_table(
        "fixtures",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("home_team_id", UUID(as_uuid=True), sa.ForeignKey(f"{SCHEMA}.teams.id"), nullable=False),
        sa.Column("away_team_id", UUID(as_uuid=True), sa.ForeignKey(f"{SCHEMA}.teams.id"), nullable=False),
        sa.Column("competition_id", UUID(as_uuid=True), sa.ForeignKey(f"{SCHEMA}.competitions.id"), nullable=False),
        sa.Column("kickoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stage", sa.Text()),
        sa.Column("tie_id", UUID(as_uuid=True)),
        sa.Column("state", sa.Text(), nullable=False, server_default=sa.text("'scheduled'")),
        sa.Column("score_home", sa.Integer()),
        sa.Column("score_away", sa.Integer()),
        sa.Column("score_source", sa.Text()),
        sa.Column("score_as_of", sa.DateTime(timezone=True)),
        sa.Column("venue", sa.Text()),
        sa.Column("neutral_ground", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("behind_closed_doors", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        schema=SCHEMA,
    )
    op.create_index("ix_fixtures_home_away_kickoff", "fixtures", ["home_team_id", "away_team_id", "kickoff_at"], schema=SCHEMA)
    op.create_index("ix_fixtures_kickoff", "fixtures", ["kickoff_at"], schema=SCHEMA)
    op.create_index("ix_fixtures_competition_kickoff", "fixtures", ["competition_id", "kickoff_at"], schema=SCHEMA)

    # ── 6. Provider tables ────────────────────────────────────
    op.create_table(
        "fl_events",
        sa.Column("fl_event_id", sa.Text(), primary_key=True),
        sa.Column("fixture_id", UUID(as_uuid=True), sa.ForeignKey(f"{SCHEMA}.fixtures.id")),
        sa.Column("raw_payload", JSONB(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("last_changed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        schema=SCHEMA,
    )
    op.create_index("ix_fl_events_fixture_id", "fl_events", ["fixture_id"], schema=SCHEMA)
    op.execute(f"CREATE INDEX ix_fl_events_unresolved ON {SCHEMA}.fl_events (fixture_id) WHERE fixture_id IS NULL")
    op.create_index("ix_fl_events_last_seen", "fl_events", ["last_seen_at"], schema=SCHEMA)

    op.create_table(
        "kalshi_markets",
        sa.Column("ticker", sa.Text(), primary_key=True),
        sa.Column("fixture_id", UUID(as_uuid=True), sa.ForeignKey(f"{SCHEMA}.fixtures.id")),
        sa.Column("market_type", sa.Text(), nullable=False),
        sa.Column("series_ticker", sa.Text()),
        sa.Column("abbr_block", sa.Text()),
        sa.Column("parsed_home_abbr", sa.Text()),
        sa.Column("parsed_away_abbr", sa.Text()),
        sa.Column("raw_payload", JSONB(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("last_changed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        schema=SCHEMA,
    )
    op.create_index("ix_kalshi_markets_fixture_id", "kalshi_markets", ["fixture_id"], schema=SCHEMA)
    op.execute(f"CREATE INDEX ix_kalshi_markets_unresolved ON {SCHEMA}.kalshi_markets (fixture_id) WHERE fixture_id IS NULL")
    op.create_index("ix_kalshi_markets_series", "kalshi_markets", ["series_ticker"], schema=SCHEMA)
    op.create_index("ix_kalshi_markets_last_seen", "kalshi_markets", ["last_seen_at"], schema=SCHEMA)

    op.create_table(
        "polymarket_markets",
        sa.Column("condition_id", sa.Text(), primary_key=True),
        sa.Column("fixture_id", UUID(as_uuid=True), sa.ForeignKey(f"{SCHEMA}.fixtures.id")),
        sa.Column("market_slug", sa.Text()),
        sa.Column("outcomes", JSONB()),
        sa.Column("raw_payload", JSONB(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("last_changed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        schema=SCHEMA,
    )
    op.create_index("ix_polymarket_markets_fixture_id", "polymarket_markets", ["fixture_id"], schema=SCHEMA)
    op.execute(f"CREATE INDEX ix_polymarket_markets_unresolved ON {SCHEMA}.polymarket_markets (fixture_id) WHERE fixture_id IS NULL")

    op.create_table(
        "oddsapi_events",
        sa.Column("oddsapi_id", sa.Text(), primary_key=True),
        sa.Column("fixture_id", UUID(as_uuid=True), sa.ForeignKey(f"{SCHEMA}.fixtures.id")),
        sa.Column("home_team", sa.Text()),
        sa.Column("away_team", sa.Text()),
        sa.Column("commence_time", sa.DateTime(timezone=True)),
        sa.Column("sport_key", sa.Text()),
        sa.Column("raw_payload", JSONB(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("last_changed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        schema=SCHEMA,
    )
    op.create_index("ix_oddsapi_events_fixture_id", "oddsapi_events", ["fixture_id"], schema=SCHEMA)
    op.execute(f"CREATE INDEX ix_oddsapi_events_unresolved ON {SCHEMA}.oddsapi_events (fixture_id) WHERE fixture_id IS NULL")
    op.create_index("ix_oddsapi_events_commence", "oddsapi_events", ["commence_time"], schema=SCHEMA)

    # ── 7. Resolution support ─────────────────────────────────
    op.create_table(
        "resolution_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("run_id", UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("provider_record_id", sa.Text(), nullable=False),
        sa.Column("fixture_id", UUID(as_uuid=True), sa.ForeignKey(f"{SCHEMA}.fixtures.id")),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("reason_code", sa.Text(), nullable=False),
        sa.Column("reason_detail", JSONB(), server_default=sa.text("'{}'::jsonb")),
        sa.Column("resolver_version", sa.Text(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        schema=SCHEMA,
    )
    op.create_index("ix_resolution_log_run", "resolution_log", ["run_id"], schema=SCHEMA)
    op.create_index("ix_resolution_log_provider_record", "resolution_log", ["provider", "provider_record_id"], schema=SCHEMA)
    op.create_index("ix_resolution_log_fixture", "resolution_log", ["fixture_id"], schema=SCHEMA)
    op.create_index("ix_resolution_log_decided_at", "resolution_log", ["decided_at"], schema=SCHEMA)

    op.create_table(
        "review_queue",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("provider_record_id", sa.Text(), nullable=False),
        sa.Column("candidate_fixtures", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("reviewed_by", sa.Text()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("provider", "provider_record_id", name="uq_review_queue_provider_record"),
        schema=SCHEMA,
    )
    op.create_index("ix_review_queue_status_created", "review_queue", ["status", "created_at"], schema=SCHEMA)

    # ── 8. Operations ─────────────────────────────────────────
    op.create_table(
        "provider_api_calls",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("called_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("status", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("response_bytes", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("error", sa.Text()),
        sa.Column("extra", JSONB(), server_default=sa.text("'{}'::jsonb")),
        schema=SCHEMA,
    )
    op.create_index("ix_provider_api_calls_provider_called", "provider_api_calls", ["provider", "called_at"], schema=SCHEMA)
    op.create_index("ix_provider_api_calls_status", "provider_api_calls", ["status"], schema=SCHEMA)


def downgrade() -> None:
    # Drop in reverse dependency order. The schema itself stays
    # because dropping it would also remove the alembic version
    # table — operators who want a full reset can DROP SCHEMA sp
    # CASCADE manually after this downgrade.
    op.drop_table("provider_api_calls", schema=SCHEMA)
    op.drop_table("review_queue", schema=SCHEMA)
    op.drop_table("resolution_log", schema=SCHEMA)
    op.drop_table("oddsapi_events", schema=SCHEMA)
    op.drop_table("polymarket_markets", schema=SCHEMA)
    op.drop_table("kalshi_markets", schema=SCHEMA)
    op.drop_table("fl_events", schema=SCHEMA)
    op.drop_table("fixtures", schema=SCHEMA)
    op.drop_table("team_aliases", schema=SCHEMA)
    op.drop_table("teams", schema=SCHEMA)
    op.drop_table("competitions", schema=SCHEMA)
    op.drop_table("sports", schema=SCHEMA)
