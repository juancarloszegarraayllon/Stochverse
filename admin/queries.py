"""DB query helpers for the admin review-queue UI.

Single-purpose module: route handlers call these helpers to fetch and
shape data; the helpers do the SQL + JOINs and return Python
dataclasses that the templates render directly. Q6 from the
implementation locks lives here — candidate-team-name JOINs happen
in `load_candidate_team_names`, NOT in the template.

Per-page query budget (sub-PR #2 list view):
  1. Main page query (sp.review_queue, partial index ix_review_queue_pending_confidence)
  2. COUNT(*) over the same WHERE clause (for total / pagination math)
  3. Latest resolver_version per (provider, provider_record_id) pair
     (sp.resolution_log, batched via IN-clause)

Per-page query budget (sub-PR #2 detail view):
  1. Single review_queue row by id
  2. Latest resolution_log row for the same (provider, provider_record_id)
  3. Candidate team names — batched IN-lookup against sp.teams
     (Q6 design lock — handler not template)
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


# Hard cap so a misbehaving query string can't request a 100k-row
# page. The UI default is 50; operators typing ?page_size=N in the
# URL still get capped here.
MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 50


# ── Dataclasses (returned to the template context) ─────────────


@dataclass(frozen=True)
class ReviewQueueRow:
    """One row in the list view. Pre-shaped so the template renders
    without any data massaging. Confidence display ("(collision)"
    instead of 0.0) per design Q8 lives here, not in the template,
    so the cosmetic rule is testable in isolation."""
    id: uuid.UUID
    provider: str
    provider_record_id: str
    provider_title: str | None
    sport: str | None               # from reason_detail['sport'] (snapshotted by PR #115)
    kickoff_at: datetime | None     # JOINed from provider tables; None if neither table has a row
    confidence: float
    confidence_display: str         # "(collision)" or formatted "0.78"
    is_collision: bool
    tier: str                       # "fuzzy@2d.0" / "alias@2c.0" / "(unknown)"
    candidate_count: int
    status: str
    rejection_count: int
    created_at: Any                 # datetime, kept as Any for template flexibility


@dataclass(frozen=True)
class ReviewQueuePage:
    """Result of a list-view query — rows + pagination math."""
    rows: list[ReviewQueueRow]
    total: int                      # total matching rows across all pages
    page: int                       # 1-indexed
    page_size: int
    # Filters echoed back so the template can repopulate the form.
    filter_status: str
    filter_provider: str | None
    filter_sport: str | None
    filter_confidence_min: float | None

    @property
    def page_count(self) -> int:
        if self.total == 0:
            return 1
        return (self.total + self.page_size - 1) // self.page_size

    @property
    def has_prev(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.page_count


@dataclass(frozen=True)
class CandidateTeam:
    """Display shape for one candidate team in the detail-view
    Candidates panel. Q6 design lock — JOIN to sp.teams happens in the
    handler (via load_candidate_team_names below)."""
    team_id: uuid.UUID
    canonical_name: str
    country_code: str | None
    sport_id: int | None


@dataclass(frozen=True)
class ReviewQueueDetail:
    """One review_queue record's full context for the detail view.
    Builds on ReviewQueueRow with additional shape: the matcher's
    raw reason_detail (rendered as JSON in the UI), the resolution_log
    metadata (fail_reason path, resolver_version stamp), and the
    candidate-team join lookup keyed by team_id.

    candidate_team_names is a {UUID: CandidateTeam} dict the template
    walks to render the Candidates panel. None when reason_detail
    didn't surface any candidate UUIDs (e.g., score-driven REVIEW_QUEUE
    without collision flags).
    """
    row: ReviewQueueRow
    reason_detail: dict[str, Any]
    home_collision: bool
    away_collision: bool
    colliding_home_team_ids: list[uuid.UUID] = field(default_factory=list)
    colliding_away_team_ids: list[uuid.UUID] = field(default_factory=list)
    candidate_team_names: dict[uuid.UUID, CandidateTeam] = field(default_factory=dict)
    # The matcher's reason_detail['fail_reason'] (alias_collision,
    # below_threshold, deferred_to_2d, etc.). None when the matcher
    # didn't classify; falls back to "(no fail_reason)" in the
    # template.
    fail_reason: str | None = None


# ── Query helpers ──────────────────────────────────────────────


def _format_confidence(confidence: float, is_collision: bool) -> str:
    """Display-layer cosmetic fix from design Q8: 0.0-confidence rows
    are alias-tier collision cases (`resolver/alias_tier/matcher.py:239`
    deliberately emits 0.0 when there's no single-best candidate).
    Rendering "0.0" misleads the operator; "(collision)" makes the
    state legible. Issue #113 tracks the deeper "should the column
    store something more meaningful" question separately.
    """
    if is_collision and confidence == 0.0:
        return "(collision)"
    return f"{confidence:.2f}"


def _detect_collision(reason_detail: dict[str, Any]) -> tuple[bool, bool]:
    """Inspect the matcher's reason_detail dict for collision flags.
    Returns (home_collision, away_collision)."""
    return (
        bool(reason_detail.get("home_collision")),
        bool(reason_detail.get("away_collision")),
    )


def _candidate_count(row_candidate_fixtures: Any) -> int:
    """sp.review_queue.candidate_fixtures is a JSONB array (UUIDs as
    strings, sometimes team_id pairs from collision cases). Return
    the length, falling back to 0 if the column is NULL or malformed.
    """
    if not row_candidate_fixtures:
        return 0
    try:
        return len(row_candidate_fixtures)
    except TypeError:
        return 0


def _extract_kickoff(
    provider: str,
    kalshi_kickoff_iso: str | None,
    fl_kickoff_epoch: str | None,
) -> datetime | None:
    """Per-provider kickoff extraction from the JOINed raw_payload fields:

    - Kalshi: raw_payload->>'_kickoff_dt' is an ISO 8601 string set by
      ingestion.kalshi's get_data() classification. Parse via fromisoformat.
    - FL: raw_payload->>'START_TIME' is the Unix epoch (seconds, stored
      as int in JSONB; the ->>'...' cast yields a string). Convert via
      datetime.fromtimestamp.

    Returns None when:
      - The provider's record doesn't exist in its table (LEFT JOIN miss).
      - The raw_payload field is missing (older ingestion rows).
      - The value is malformed (defensive — operators shouldn't see 500s
        because of one bad row).
    """
    try:
        if provider == "kalshi" and kalshi_kickoff_iso:
            return datetime.fromisoformat(kalshi_kickoff_iso)
        if provider == "fl" and fl_kickoff_epoch:
            return datetime.fromtimestamp(int(fl_kickoff_epoch), tz=timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None
    return None


async def _load_latest_tier_versions(
    session: AsyncSession,
    pairs: list[tuple[str, str]],
) -> dict[tuple[str, str], str]:
    """Batch lookup of the latest sp.resolution_log.resolver_version
    for each (provider, provider_record_id) pair. The list view shows
    one tier per row; without this lookup the table column reads
    "(unknown)" everywhere.

    Uses DISTINCT ON to take the latest decided_at per pair — the
    runner can write multiple resolution_log rows per record across
    cron passes (per design D.4: each tier consulted gets its own
    row), so we want the most recent.

    Empty `pairs` returns an empty dict without querying.
    """
    if not pairs:
        return {}

    # Postgres-native row-tuple IN clause via VALUES — avoids the
    # N-parameter quadratic-string approach. ARRAY indexing keeps the
    # bindparam count fixed regardless of pair count.
    providers = [p[0] for p in pairs]
    record_ids = [p[1] for p in pairs]
    sql = text(
        """
        SELECT DISTINCT ON (provider, provider_record_id)
            provider, provider_record_id, resolver_version
        FROM sp.resolution_log
        WHERE (provider, provider_record_id) IN (
            SELECT * FROM UNNEST(
                CAST(:providers AS text[]),
                CAST(:record_ids AS text[])
            )
        )
        ORDER BY provider, provider_record_id, decided_at DESC
        """
    ).bindparams(providers=providers, record_ids=record_ids)
    result = await session.execute(sql)
    return {
        (r.provider, r.provider_record_id): r.resolver_version
        for r in result.all()
    }


async def list_review_queue(
    session: AsyncSession,
    *,
    status: str = "pending",
    provider: str | None = None,
    sport: str | None = None,
    confidence_min: float | None = None,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> ReviewQueuePage:
    """Paginated list-view query.

    The default (status='pending', no other filters, sort by
    confidence DESC) is covered by the partial index
    `ix_review_queue_pending_confidence` from the 2F.0 migration —
    Postgres serves the LIMIT/OFFSET directly from the index without
    sorting.

    Other status values (approved / rejected) trigger a sequential
    scan today; that's fine because operators rarely browse decided
    rows (and when they do, the volume is the all-time total, not a
    hot path).

    `confidence_min` filters numerically; `sport` filters on
    `reason_detail->>'sport'` (the runner snapshots sport into
    reason_detail per 2F.0.5).
    """
    # Clamp + sanitize inputs. Negative/zero pages collapse to 1;
    # oversize page_size caps at MAX_PAGE_SIZE.
    page = max(1, int(page))
    page_size = max(1, min(int(page_size), MAX_PAGE_SIZE))
    offset = (page - 1) * page_size

    # Build the WHERE clause dynamically with rq.-qualified column
    # names so it composes cleanly with the LEFT JOIN below (ambiguous
    # column references in JOINed queries cause Postgres to error).
    # Using parameterized text() rather than SQLAlchemy ORM keeps the
    # partial-index access plan transparent (the ORM tends to add
    # column expressions that disqualify the partial index).
    where_clauses = ["rq.status = :status"]
    params: dict[str, Any] = {"status": status}
    if provider:
        where_clauses.append("rq.provider = :provider")
        params["provider"] = provider
    if sport:
        where_clauses.append("rq.reason_detail->>'sport' = :sport")
        params["sport"] = sport
    if confidence_min is not None:
        where_clauses.append("rq.confidence >= :confidence_min")
        params["confidence_min"] = float(confidence_min)
    where_sql_joined = " AND ".join(where_clauses)
    # Count query uses unqualified column names (no JOINs).
    where_sql_count = where_sql_joined.replace("rq.", "")

    # Page query. LEFT JOINs to the provider tables surface kickoff
    # time on the list view — operators triage urgent (kicks off in
    # 2h) differently from non-urgent (in 3 weeks), and kickoff isn't
    # stored on review_queue. The provider PKs (kalshi_markets.ticker,
    # fl_events.fl_event_id) are indexed; one JOIN per provider keeps
    # the plan cheap. NULL on either side is acceptable — the helper
    # _extract_kickoff falls back to None and the template renders
    # "(unknown)".
    rows_sql = text(
        f"""
        SELECT rq.id, rq.provider, rq.provider_record_id, rq.provider_title,
               rq.candidate_fixtures, rq.confidence, rq.status,
               rq.rejection_count, rq.created_at, rq.reason_detail,
               km.raw_payload->>'_kickoff_dt' AS kalshi_kickoff_iso,
               fe.raw_payload->>'START_TIME' AS fl_kickoff_epoch
        FROM sp.review_queue rq
        LEFT JOIN sp.kalshi_markets km
          ON rq.provider = 'kalshi'
         AND rq.provider_record_id = km.ticker
        LEFT JOIN sp.fl_events fe
          ON rq.provider = 'fl'
         AND rq.provider_record_id = fe.fl_event_id
        WHERE {where_sql_joined}
        ORDER BY rq.confidence DESC, rq.created_at DESC
        LIMIT :limit OFFSET :offset
        """
    ).bindparams(**params, limit=page_size, offset=offset)
    rows_result = (await session.execute(rows_sql)).all()

    # Total count (separate query — keeps the page query simple +
    # cacheable). For the default status='pending' filter, Postgres
    # uses the partial index for both queries. No JOINs here (count
    # is over review_queue alone), so the unqualified WHERE form.
    count_sql = text(
        f"SELECT COUNT(*) AS total FROM sp.review_queue WHERE {where_sql_count}"
    ).bindparams(**params)
    total = (await session.execute(count_sql)).scalar() or 0

    # Batch tier lookup for this page's rows.
    pairs = [(r.provider, r.provider_record_id) for r in rows_result]
    tier_map = await _load_latest_tier_versions(session, pairs)

    # Shape into ReviewQueueRow dataclasses.
    shaped_rows: list[ReviewQueueRow] = []
    for r in rows_result:
        reason_detail = r.reason_detail or {}
        home_coll, away_coll = _detect_collision(reason_detail)
        is_collision = home_coll or away_coll
        shaped_rows.append(ReviewQueueRow(
            id=r.id,
            provider=r.provider,
            provider_record_id=r.provider_record_id,
            provider_title=r.provider_title,
            sport=reason_detail.get("sport"),
            kickoff_at=_extract_kickoff(
                r.provider,
                kalshi_kickoff_iso=r.kalshi_kickoff_iso,
                fl_kickoff_epoch=r.fl_kickoff_epoch,
            ),
            confidence=float(r.confidence),
            confidence_display=_format_confidence(float(r.confidence), is_collision),
            is_collision=is_collision,
            tier=tier_map.get((r.provider, r.provider_record_id), "(unknown)"),
            candidate_count=_candidate_count(r.candidate_fixtures),
            status=r.status,
            rejection_count=int(r.rejection_count or 0),
            created_at=r.created_at,
        ))

    return ReviewQueuePage(
        rows=shaped_rows,
        total=total,
        page=page,
        page_size=page_size,
        filter_status=status,
        filter_provider=provider,
        filter_sport=sport,
        filter_confidence_min=confidence_min,
    )


async def load_candidate_team_names(
    session: AsyncSession,
    team_ids: list[uuid.UUID],
) -> dict[uuid.UUID, CandidateTeam]:
    """Q6 design lock — batch JOIN to sp.teams for the candidate team_ids
    surfaced in reason_detail. Single query per detail-view render.

    sp.teams is small (~25k rows) and rarely churns; the IN-clause
    lookup is index-served via the primary key. No need to denormalize
    canonical_name into review_queue.

    Empty `team_ids` returns an empty dict without querying.
    """
    if not team_ids:
        return {}
    sql = text(
        """
        SELECT id, canonical_name, country_code, sport_id
        FROM sp.teams
        WHERE id = ANY(CAST(:team_ids AS uuid[]))
        """
    ).bindparams(team_ids=[str(t) for t in team_ids])
    result = await session.execute(sql)
    return {
        r.id: CandidateTeam(
            team_id=r.id,
            canonical_name=r.canonical_name,
            country_code=r.country_code,
            sport_id=r.sport_id,
        )
        for r in result.all()
    }


def _parse_team_id_list(raw: Any) -> list[uuid.UUID]:
    """reason_detail's colliding_*_team_ids fields are stored as
    JSONB arrays of UUID strings. Coerce defensively — operators
    don't deserve a 500 when an old row has a slightly-different
    JSONB shape.
    """
    if not raw:
        return []
    out: list[uuid.UUID] = []
    for item in raw:
        try:
            out.append(uuid.UUID(str(item)))
        except (ValueError, AttributeError, TypeError):
            continue
    return out


async def get_review_queue_record(
    session: AsyncSession,
    record_id: uuid.UUID,
) -> ReviewQueueDetail | None:
    """Single-record fetch for the detail view. Returns None if the
    UUID doesn't match any row — handler renders 404.

    Three queries: review_queue row, latest resolution_log
    resolver_version, candidate team names batched JOIN.
    """
    row_sql = text(
        """
        SELECT rq.id, rq.provider, rq.provider_record_id, rq.provider_title,
               rq.candidate_fixtures, rq.confidence, rq.status,
               rq.rejection_count, rq.created_at, rq.reason_detail,
               km.raw_payload->>'_kickoff_dt' AS kalshi_kickoff_iso,
               fe.raw_payload->>'START_TIME' AS fl_kickoff_epoch
        FROM sp.review_queue rq
        LEFT JOIN sp.kalshi_markets km
          ON rq.provider = 'kalshi'
         AND rq.provider_record_id = km.ticker
        LEFT JOIN sp.fl_events fe
          ON rq.provider = 'fl'
         AND rq.provider_record_id = fe.fl_event_id
        WHERE rq.id = :record_id
        """
    ).bindparams(record_id=record_id)
    row = (await session.execute(row_sql)).first()
    if row is None:
        return None

    reason_detail = row.reason_detail or {}
    home_coll, away_coll = _detect_collision(reason_detail)
    is_collision = home_coll or away_coll

    # Tier lookup (same as list view, single-row batch).
    tier_map = await _load_latest_tier_versions(
        session, [(row.provider, row.provider_record_id)]
    )
    tier = tier_map.get(
        (row.provider, row.provider_record_id), "(unknown)"
    )

    # Candidate team_ids → JOIN to sp.teams for display names.
    colliding_home = _parse_team_id_list(
        reason_detail.get("colliding_home_team_ids")
    )
    colliding_away = _parse_team_id_list(
        reason_detail.get("colliding_away_team_ids")
    )
    all_team_ids: list[uuid.UUID] = list(set(colliding_home + colliding_away))
    candidate_names = await load_candidate_team_names(session, all_team_ids)

    shaped_row = ReviewQueueRow(
        id=row.id,
        provider=row.provider,
        provider_record_id=row.provider_record_id,
        provider_title=row.provider_title,
        sport=reason_detail.get("sport"),
        kickoff_at=_extract_kickoff(
            row.provider,
            kalshi_kickoff_iso=row.kalshi_kickoff_iso,
            fl_kickoff_epoch=row.fl_kickoff_epoch,
        ),
        confidence=float(row.confidence),
        confidence_display=_format_confidence(float(row.confidence), is_collision),
        is_collision=is_collision,
        tier=tier,
        candidate_count=_candidate_count(row.candidate_fixtures),
        status=row.status,
        rejection_count=int(row.rejection_count or 0),
        created_at=row.created_at,
    )
    return ReviewQueueDetail(
        row=shaped_row,
        reason_detail=reason_detail,
        home_collision=home_coll,
        away_collision=away_coll,
        colliding_home_team_ids=colliding_home,
        colliding_away_team_ids=colliding_away,
        candidate_team_names=candidate_names,
        fail_reason=reason_detail.get("fail_reason"),
    )
