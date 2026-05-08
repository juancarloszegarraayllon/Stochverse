"""SQLAlchemy models for the SP Architecture canonical entity layer.

Phase 1 deliverable per SP Architecture v1.2 §5 and §11.2.

These models live alongside the legacy `models.py` (entities, events,
markets, prices, etc.) without disturbing it. Tables are placed in a
dedicated Postgres schema `sp.*` so the boundary between the new
canonical entity layer and the legacy data layer is unambiguous.

Tables:

  Canonical entities:
    sp.sports          — top-level taxonomy
    sp.competitions    — leagues, tournaments
    sp.teams           — clubs, organizational units
    sp.team_aliases    — many-to-one provider-string → team mapping
    sp.fixtures        — match between two teams at a specific time

  Provider records (raw payloads + nullable FK to fixtures):
    sp.fl_events
    sp.kalshi_markets
    sp.polymarket_markets
    sp.oddsapi_events

  Resolution support:
    sp.resolution_log  — append-only audit of every resolution decision
    sp.review_queue    — low-confidence resolutions awaiting human approval

  Operations:
    sp.provider_api_calls — Phase 0 emits these as JSON logs; this
                            table is the persistent destination

Identity rule (architecture doc §5.4): kickoff_at and the team pair
together define a fixture's identity. Auto-link drift threshold is
configured per sport in `sports.auto_link_drift_minutes`.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, relationship


SCHEMA = "sp"


class SPBase(DeclarativeBase):
    """Declarative base for the SP Architecture entity layer.

    All tables defined under this base live in the `sp` schema. The
    naming convention for constraints/indexes is set so Alembic
    autogenerate produces stable, deterministic migration names.
    """

    metadata = MetaData(
        schema=SCHEMA,
        naming_convention={
            "ix":  "ix_%(column_0_label)s",
            "uq":  "uq_%(table_name)s_%(column_0_name)s",
            "ck":  "ck_%(table_name)s_%(constraint_name)s",
            "fk":  "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk":  "pk_%(table_name)s",
        },
    )


# Helper: timezone-aware UTC default ─ never use naive datetimes.
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── 1. Canonical entities ────────────────────────────────────────

class Sport(SPBase):
    """Top-level taxonomy. Soccer, NFL, NBA, MLB, etc.

    Small finite set, seeded statically. The `auto_link_drift_minutes`
    column carries the per-sport drift threshold from architecture
    doc §5.4 — lets the resolver auto-link a provider record to an
    existing fixture by team pair when kickoff differs by less than
    this. Beyond it, the resolver routes to review_queue.
    """
    __tablename__ = "sports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(Text, nullable=False, unique=True)            # 'soccer', 'nba', 'mlb', ...
    name = Column(Text, nullable=False)                         # 'Soccer', 'NBA', 'MLB', ...

    # Per-sport defaults for the resolver. Overridable per fixture
    # if a competition has tighter requirements.
    auto_link_drift_minutes = Column(
        Integer, nullable=False, default=24 * 60,
        comment="Maximum kickoff drift (minutes) for resolver to auto-link a provider record to an existing fixture; beyond this routes to review_queue (architecture §5.4).",
    )

    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)


class Competition(SPBase):
    """A league, tournament, or organized series of matches.

    Provider records carry hints toward a competition; the resolver
    matches hints to canonical competitions via the
    `kalshi_series_bases` and `fl_tournament_stage_ids` JSONB arrays
    or via name fuzz.
    """
    __tablename__ = "competitions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sport_id = Column(Integer, ForeignKey(f"{SCHEMA}.sports.id"), nullable=False)

    canonical_name = Column(Text, nullable=False)
    normalized_name = Column(Text, nullable=False)
    country_code = Column(String(3))                            # ISO 3166-1 alpha-2 (or 'INT')
    season = Column(Text)                                       # e.g. '2025-26'
    competition_type = Column(Text)                             # league / cup / international / friendly

    kalshi_series_bases = Column(JSONB, nullable=False, default=list)
    fl_tournament_stage_ids = Column(JSONB, nullable=False, default=list)
    polymarket_slugs = Column(JSONB, nullable=False, default=list)
    oddsapi_keys = Column(JSONB, nullable=False, default=list)

    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)

    sport = relationship("Sport")

    __table_args__ = (
        Index("ix_competitions_sport_normalized", "sport_id", "normalized_name"),
    )


class Team(SPBase):
    """A football club, basketball team, or equivalent organizational
    unit competing in matches.

    `canonical_name` preserves the original encoding (accents,
    special characters); `normalized_name` is lowercased and
    accent-stripped for matching only.
    """
    __tablename__ = "teams"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sport_id = Column(Integer, ForeignKey(f"{SCHEMA}.sports.id"), nullable=False)

    canonical_name = Column(Text, nullable=False)
    normalized_name = Column(Text, nullable=False)
    country_code = Column(String(3))

    logo_url = Column(Text)
    logo_source = Column(Text)                                  # provider that supplied the logo

    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)

    aliases = relationship("TeamAlias", back_populates="team", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_teams_sport_normalized", "sport_id", "normalized_name"),
    )


class TeamAlias(SPBase):
    """Many-to-one mapping from provider-supplied team strings to a
    canonical team. Replaces the hardcoded alias dictionaries in the
    legacy code (kalshi_identity._FL_ABBR_ALIASES, etc.).
    """
    __tablename__ = "team_aliases"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id = Column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.teams.id", ondelete="CASCADE"), nullable=False)

    alias = Column(Text, nullable=False)                        # original encoding
    alias_normalized = Column(Text, nullable=False)
    source = Column(Text, nullable=False)                       # 'kalshi'|'fl'|'polymarket'|'oddsapi'|'manual_review'|'human_curated'
    confidence = Column(Float, nullable=False, default=1.0)

    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    team = relationship("Team", back_populates="aliases")

    __table_args__ = (
        UniqueConstraint("alias_normalized", "source", name="uq_team_aliases_alias_normalized_source"),
        Index("ix_team_aliases_alias_normalized", "alias_normalized"),
    )


class Fixture(SPBase):
    """A specific match between two teams at a specific time in a
    specific competition. The smallest unit of identity in the system.

    Identity rule (architecture §5.4): (home_team_id, away_team_id,
    date(kickoff_at)) is the resolver's lookup key, but `id` is the
    canonical identifier — stable across reschedules, stage label
    changes, score updates, etc.

    A fixture does NOT require any specific provider link. Fixtures
    with NULL fl_event_id (or NULL polymarket reference, etc.) are
    first-class — the serving layer renders them with whatever
    fields are available and tags each field with its source.
    """
    __tablename__ = "fixtures"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    home_team_id = Column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.teams.id"), nullable=False)
    away_team_id = Column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.teams.id"), nullable=False)
    competition_id = Column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.competitions.id"), nullable=True)

    kickoff_at = Column(DateTime(timezone=True), nullable=False)
    stage = Column(Text)                                        # group/round/leg/playoff metadata
    tie_id = Column(UUID(as_uuid=True))                         # optional, groups two-leg ties

    # state machine: scheduled | live | finished | cancelled | postponed | forfeit
    state = Column(Text, nullable=False, default="scheduled")

    score_home = Column(Integer)
    score_away = Column(Integer)
    score_source = Column(Text)                                 # which provider's score this is
    score_as_of = Column(DateTime(timezone=True))

    venue = Column(Text)
    neutral_ground = Column(Boolean, nullable=False, default=False)
    behind_closed_doors = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)

    home_team = relationship("Team", foreign_keys=[home_team_id])
    away_team = relationship("Team", foreign_keys=[away_team_id])
    competition = relationship("Competition")

    __table_args__ = (
        # Resolver lookup key: same-day fixtures involving the same
        # team pair. Date(kickoff_at) is computed in queries; index
        # on the composite of teams + kickoff supports the per-sport
        # drift-threshold scan.
        Index("ix_fixtures_home_away_kickoff", "home_team_id", "away_team_id", "kickoff_at"),
        Index("ix_fixtures_kickoff", "kickoff_at"),
        Index("ix_fixtures_competition_kickoff", "competition_id", "kickoff_at"),
    )


# ── 2. Provider records ──────────────────────────────────────────
#
# Each provider has a dedicated table. Primary key is the provider's
# own identifier. fixture_id is nullable — NULL means "not yet
# resolved" and is a queryable, first-class state.

class FLEvent(SPBase):
    __tablename__ = "fl_events"

    fl_event_id = Column(Text, primary_key=True)
    fixture_id = Column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.fixtures.id"))

    raw_payload = Column(JSONB, nullable=False)

    last_seen_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    last_changed_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    payload_hash = Column(String(64), nullable=False)           # sha256 of normalized JSON

    fixture = relationship("Fixture")

    __table_args__ = (
        Index("ix_fl_events_fixture_id", "fixture_id"),
        Index("ix_fl_events_unresolved", "fixture_id",
              postgresql_where=Column("fixture_id").is_(None)),
        Index("ix_fl_events_last_seen", "last_seen_at"),
    )


class KalshiMarket(SPBase):
    __tablename__ = "kalshi_markets"

    ticker = Column(Text, primary_key=True)
    fixture_id = Column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.fixtures.id"))

    market_type = Column(Text, nullable=False)                  # 'game'|'total'|'goal'|'outright'|...
    series_ticker = Column(Text)
    abbr_block = Column(Text)
    parsed_home_abbr = Column(Text)
    parsed_away_abbr = Column(Text)

    raw_payload = Column(JSONB, nullable=False)

    last_seen_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    last_changed_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    payload_hash = Column(String(64), nullable=False)

    fixture = relationship("Fixture")

    __table_args__ = (
        Index("ix_kalshi_markets_fixture_id", "fixture_id"),
        Index("ix_kalshi_markets_unresolved", "fixture_id",
              postgresql_where=Column("fixture_id").is_(None)),
        Index("ix_kalshi_markets_series", "series_ticker"),
        Index("ix_kalshi_markets_last_seen", "last_seen_at"),
    )


class PolymarketMarket(SPBase):
    __tablename__ = "polymarket_markets"

    condition_id = Column(Text, primary_key=True)
    fixture_id = Column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.fixtures.id"))

    market_slug = Column(Text)
    outcomes = Column(JSONB)
    raw_payload = Column(JSONB, nullable=False)

    last_seen_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    last_changed_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    payload_hash = Column(String(64), nullable=False)

    __table_args__ = (
        Index("ix_polymarket_markets_fixture_id", "fixture_id"),
        Index("ix_polymarket_markets_unresolved", "fixture_id",
              postgresql_where=Column("fixture_id").is_(None)),
    )


class OddsAPIEvent(SPBase):
    __tablename__ = "oddsapi_events"

    oddsapi_id = Column(Text, primary_key=True)
    fixture_id = Column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.fixtures.id"))

    home_team = Column(Text)
    away_team = Column(Text)
    commence_time = Column(DateTime(timezone=True))
    sport_key = Column(Text)
    raw_payload = Column(JSONB, nullable=False)

    last_seen_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    last_changed_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    payload_hash = Column(String(64), nullable=False)

    __table_args__ = (
        Index("ix_oddsapi_events_fixture_id", "fixture_id"),
        Index("ix_oddsapi_events_unresolved", "fixture_id",
              postgresql_where=Column("fixture_id").is_(None)),
        Index("ix_oddsapi_events_commence", "commence_time"),
    )


# ── 3. Resolution support ────────────────────────────────────────

class ResolutionLog(SPBase):
    """Append-only audit of every resolution decision.

    Never deleted. Read by the admin review UI and by debugging tools.
    Replay (architecture §7.6) re-runs the resolver against
    raw_payload data and writes new entries; the previous entries
    remain for diff and rollback.
    """
    __tablename__ = "resolution_log"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    run_id = Column(UUID(as_uuid=True), nullable=False)         # groups decisions from one resolver run
    provider = Column(Text, nullable=False)
    provider_record_id = Column(Text, nullable=False)
    fixture_id = Column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.fixtures.id"))
    confidence = Column(Float, nullable=False)
    reason_code = Column(Text, nullable=False)                  # 'strict'|'alias'|'fuzzy'|'corroboration'|'review_queue'|'no_match'
    reason_detail = Column(JSONB, default=dict)
    resolver_version = Column(Text, nullable=False)
    decided_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    __table_args__ = (
        Index("ix_resolution_log_run", "run_id"),
        Index("ix_resolution_log_provider_record", "provider", "provider_record_id"),
        Index("ix_resolution_log_fixture", "fixture_id"),
        Index("ix_resolution_log_decided_at", "decided_at"),
    )


class ReviewQueue(SPBase):
    """Pending low-confidence resolutions awaiting human approval.

    Health metrics (architecture §7.5):
      - Target steady-state depth: <20.
      - Alert threshold: >100 (resolver thresholds wrong, not the
        reviewer's pace).
      - Triage SLA: 24 hours.
    """
    __tablename__ = "review_queue"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider = Column(Text, nullable=False)
    provider_record_id = Column(Text, nullable=False)
    candidate_fixtures = Column(JSONB, nullable=False, default=list)
    confidence = Column(Float, nullable=False)
    status = Column(Text, nullable=False, default="pending")    # pending|approved|rejected
    reviewed_by = Column(Text)
    reviewed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    __table_args__ = (
        UniqueConstraint("provider", "provider_record_id", name="uq_review_queue_provider_record"),
        Index("ix_review_queue_status_created", "status", "created_at"),
    )


# ── 4. Operations ────────────────────────────────────────────────

class ProviderApiCall(SPBase):
    """Persistent destination for the provider_api_call events that
    Phase 0 emits as JSON logs. Phase 1 backfills from those logs;
    Phase 2+ writes here directly.

    Schema matches the structlog event shape exactly so the backfill
    is a straight load.
    """
    __tablename__ = "provider_api_calls"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    provider = Column(Text, nullable=False)
    endpoint = Column(Text, nullable=False)
    called_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    status = Column(Integer, nullable=False)                    # 0 if exception or cache hit
    latency_ms = Column(Integer, nullable=False)
    response_bytes = Column(Integer, nullable=False, default=0)
    error = Column(Text)
    extra = Column(JSONB, default=dict)                         # e.g. {"cache_hit": true, "event_id": "..."}

    __table_args__ = (
        Index("ix_provider_api_calls_provider_called", "provider", "called_at"),
        Index("ix_provider_api_calls_status", "status"),
    )


class ResolverRun(SPBase):
    """Per-run audit row written by scripts/run_resolver_pass.py and
    (Phase 2E onward) the live runner.

    One row per pass. Provides queryable parallel-run metrics without
    log-grepping. The run_mode column distinguishes parallel-run data
    ('standalone' | 'cron') from post-Phase-2E live activity ('live'),
    so day-7 reports can filter cleanly.
    """
    __tablename__ = "resolver_runs"

    id                  = Column(BigInteger, primary_key=True, autoincrement=True)
    run_id              = Column(UUID(as_uuid=True), nullable=False)
    resolver_version    = Column(Text, nullable=False)
    provider            = Column(Text, nullable=False)            # 'fl' | 'kalshi'
    run_mode            = Column(Text, nullable=False)            # 'standalone' | 'cron' | 'live'
    started_at          = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    finished_at         = Column(DateTime(timezone=True))
    records_scanned     = Column(Integer, nullable=False, default=0)
    auto_applies        = Column(Integer, nullable=False, default=0)
    no_match            = Column(Integer, nullable=False, default=0)
    crashes             = Column(Integer, nullable=False, default=0)
    legacy_diff_count   = Column(Integer)                         # Kalshi only; NULL for FL
    legacy_diff_details = Column(JSONB)
    latency_p95_ms      = Column(Integer)
    extra               = Column(JSONB, default=dict)

    __table_args__ = (
        Index("ix_resolver_runs_provider_started", "provider", "started_at"),
        Index("ix_resolver_runs_run_mode_started", "run_mode", "started_at"),
    )
