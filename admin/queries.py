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


# ── Navigation helper for "Go to next record" link ────────────


async def find_next_pending_record_id(
    session: AsyncSession,
) -> uuid.UUID | None:
    """Pick the highest-confidence pending review_queue record for
    the "Go to next record" link in the decision-result panel.

    Sort matches the list view's default (confidence DESC,
    created_at DESC). Returns None when the queue is drained —
    template falls through to "(no next record — drained for
    current filter)".

    Filter-context limitation (deferred to 2F.X): this ignores
    any sport/provider/confidence_min filters the operator had in
    the list view URL. The "next" record is always the queue-wide
    top by confidence. If the operator wants filter continuity
    they use the "Back to queue" link to return to their filter
    URL via browser history. Surfaced in the template's tooltip /
    aria-label if/when the limitation becomes operationally
    confusing.

    Uses the partial index ix_review_queue_pending_confidence
    (Phase 2F.0 migration) — single index scan, no sort. The
    just-decided record is excluded naturally because its status
    is no longer 'pending' by the time this query runs.
    """
    sql = text(
        """
        SELECT id FROM sp.review_queue
        WHERE status = 'pending'
        ORDER BY confidence DESC, created_at DESC
        LIMIT 1
        """
    )
    return (await session.execute(sql)).scalar()


# ── Mutation helpers (Phase 2F.1 sub-PR #3) ────────────────────


# Source value for sp.team_aliases written by the operator review UI.
# Distinct from 'alias_tier' / 'fuzzy_tier' (runner write-back) and
# 'operator_2d5' (2D.5 CLI, future). Day-7 queries split per-source
# attribution.
TEAM_ALIASES_SOURCE_OPERATOR_REVIEW = "operator_review"


class ApprovalError(Exception):
    """Raised by approve_record when the operator's submission can't
    be honored. The route handler converts this to an HTTP 400 with
    the .message rendered for the operator.
    """
    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _validate_candidate_team_id(
    submitted: uuid.UUID,
    *,
    side_collision: bool,
    side_colliding_ids: list[uuid.UUID],
    side_default_id: uuid.UUID | None,
    side_label: str,
) -> None:
    """Server-side validation that the operator-submitted team_id
    matches the candidate set the matcher surfaced. Raises
    ApprovalError on mismatch.

    Per design: never trust client-submitted IDs. The operator MUST
    pick from the matcher's candidates — arbitrary team_ids would
    let an attacker (or accidental URL tampering) link a provider
    record to any team they want.
    """
    if side_collision:
        if submitted not in side_colliding_ids:
            raise ApprovalError(
                f"{side_label}_team_id={submitted} is not in the "
                f"matcher's {side_label} collision set "
                f"({len(side_colliding_ids)} candidates). "
                "Refusing to link to an arbitrary team."
            )
    else:
        # Non-collision: matcher picked a specific team. Operator
        # submission must match it.
        if side_default_id is not None and submitted != side_default_id:
            raise ApprovalError(
                f"{side_label}_team_id={submitted} doesn't match the "
                f"matcher's selected {side_label}_team_id={side_default_id}. "
                "Non-collision records have a single candidate; "
                "operator can approve as-is or reject."
            )


async def approve_record(
    session: AsyncSession,
    *,
    record_id: uuid.UUID,
    operator: str,
    home_team_id: uuid.UUID,
    away_team_id: uuid.UUID,
) -> dict[str, Any]:
    """Atomic approve flow per design Q3:
      1. Validate submitted team_ids against the matcher's candidate sets.
      2. ensure_fixture(home, away, kickoff_at) — find-or-create.
      3. UPDATE provider table SET fixture_id.
      4. UPDATE review_queue SET status='approved' + audit fields
         (idempotent via WHERE status='pending' per Q2).
      5. INSERT sp.team_aliases (source='operator_review') for both
         sides — same write-back semantics as the runner's auto-apply
         paths, just attributed to the operator's decision.

    All five steps in one `async with session.begin()` block. Partial
    failure → full rollback. Per-record txn isolation matches the
    runner's PR #108 pattern.

    On idempotency: if the row is already approved/rejected (status
    != 'pending'), the function returns the current state without
    re-writing — `decided_at` and `reviewed_by` stay at the first
    decision's values. Double-click protection.

    Returns a dict shape the route handler renders:
      {
        "action": "approved" | "already_decided",
        "record_id": UUID, "fixture_id": UUID,
        "fixture_created_new": bool, "previous_status": str,
      }

    Raises ApprovalError for:
      - record_id not found (404 in caller)
      - submitted team_id not in candidate set
      - kickoff_at unavailable from provider raw_payload
        (per Q1 edge-case refusal)
    """
    from resolver.fixtures import ensure_fixture

    # Pre-flight: load the record + its candidate context.
    detail = await get_review_queue_record(session, record_id)
    if detail is None:
        raise ApprovalError(
            f"review_queue record {record_id} not found", status_code=404,
        )

    # Idempotency check (Q2). If already decided, return the current
    # state without any DB writes — operator's double-click is a
    # no-op, not an error.
    if detail.row.status != "pending":
        return {
            "action": "already_decided",
            "record_id": record_id,
            "fixture_id": None,
            "fixture_created_new": False,
            "previous_status": detail.row.status,
        }

    # Q1 edge case: kickoff_at must come from the provider table
    # (kickoff isn't stored on review_queue). Refuse if neither
    # provider table has the row OR the row has no kickoff.
    if detail.row.kickoff_at is None:
        raise ApprovalError(
            "Fixture creation requires kickoff data, but the provider "
            f"record ({detail.row.provider}/{detail.row.provider_record_id}) "
            "doesn't carry a kickoff timestamp. File a manual ticket "
            "or wait for the provider to update its data."
        )

    # Server-side validation against the matcher's candidate sets.
    # For non-collision rows: candidate_fixtures stores team_ids
    # (NOT fixture_ids — see issue #121 for the rename plan), with
    # candidate_fixtures[0] = home_team_id and [1] = away_team_id by
    # the matcher's convention at resolver/alias_tier/matcher.py:337
    # and resolver/fuzzy_tier/matcher.py:320.
    raw_cf = await _load_candidate_team_ids(session, record_id)
    default_home = raw_cf[0] if len(raw_cf) >= 1 else None
    default_away = raw_cf[1] if len(raw_cf) >= 2 else None
    _validate_candidate_team_id(
        home_team_id,
        side_collision=detail.home_collision,
        side_colliding_ids=detail.colliding_home_team_ids,
        side_default_id=default_home,
        side_label="home",
    )
    _validate_candidate_team_id(
        away_team_id,
        side_collision=detail.away_collision,
        side_colliding_ids=detail.colliding_away_team_ids,
        side_default_id=default_away,
        side_label="away",
    )

    # Atomic write block (Q3). Single transaction wraps all four
    # writes — partial failure → full rollback.
    async with session.begin():
        # Step 1: ensure_fixture (find-or-create). Per Q1 (a): operator
        # authority overrides the matcher's no-auto-create rule.
        fixture_id, created_new = await ensure_fixture(
            session,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            kickoff_at=detail.row.kickoff_at,
        )

        # Step 2: UPDATE provider table fixture_id.
        # Provider-aware dispatch — same pattern as the runner.
        if detail.row.provider == "kalshi":
            provider_update_sql = text(
                "UPDATE sp.kalshi_markets SET fixture_id = :fid "
                "WHERE ticker = :pk"
            )
        else:  # fl
            provider_update_sql = text(
                "UPDATE sp.fl_events SET fixture_id = :fid "
                "WHERE fl_event_id = :pk"
            )
        await session.execute(provider_update_sql.bindparams(
            fid=fixture_id, pk=detail.row.provider_record_id,
        ))

        # Step 3: UPDATE review_queue. WHERE status='pending' is the
        # idempotency guard (Q2) — if a concurrent approve already
        # ran, this is a no-op rather than a double-write.
        # rowcount tells us whether THIS call won the race.
        update_result = await session.execute(text(
            """
            UPDATE sp.review_queue
            SET status = 'approved',
                reviewed_by = :operator,
                reviewed_at = NOW()
            WHERE id = :record_id AND status = 'pending'
            """
        ).bindparams(operator=operator, record_id=record_id))

        if update_result.rowcount == 0:
            # Lost the race to a concurrent approve. The earlier
            # decision stands; this transaction's writes (above)
            # roll back at commit time? Actually no — they don't,
            # because session.begin() commits on clean exit.
            #
            # To prevent partial-state writes, raise so the
            # transaction rolls back. The caller catches and
            # returns the current state.
            raise ApprovalError(
                "Concurrent decision detected — another operator "
                "session approved or rejected this record while "
                "your form was open. Reload to see current state.",
                status_code=409,  # Conflict
            )

        # Step 4: INSERT sp.team_aliases for both sides (write-back).
        # Same shape as the runner's auto-apply paths in
        # scripts/run_resolver_pass.py — ON CONFLICT DO NOTHING per
        # design D.5 (confidence is provenance, not a per-match score).
        for tid, alias_text in (
            (home_team_id, _operator_alias_text(detail, "home")),
            (away_team_id, _operator_alias_text(detail, "away")),
        ):
            if alias_text:
                await session.execute(text(
                    """
                    INSERT INTO sp.team_aliases
                      (id, team_id, alias, alias_normalized,
                       source, confidence, created_at)
                    VALUES
                      (gen_random_uuid(), :tid, :alias,
                       :alias_norm, :source, 1.0, NOW())
                    ON CONFLICT (alias_normalized, source) DO NOTHING
                    """
                ).bindparams(
                    tid=tid,
                    alias=alias_text,
                    alias_norm=_normalize_alias(alias_text),
                    source=TEAM_ALIASES_SOURCE_OPERATOR_REVIEW,
                ))

    return {
        "action": "approved",
        "record_id": record_id,
        "fixture_id": fixture_id,
        "fixture_created_new": created_new,
        "previous_status": "pending",
    }


async def reject_record(
    session: AsyncSession,
    *,
    record_id: uuid.UUID,
    operator: str,
) -> dict[str, Any]:
    """Atomic reject flow per Q4 design semantics:

      1. UPDATE review_queue SET status='rejected',
         reviewed_by, reviewed_at, rejection_count += 1
         WHERE status='pending'  (idempotency guard Q2).

    Single statement, idempotent. Re-clicking reject on an already-
    rejected row is a no-op (rowcount=0 path returns
    action='already_decided').

    Per design Q4 revised: rejection is operator-sticky. The next
    cron's runner picks the record up again but the PR #108 INSERT
    ON CONFLICT DO UPDATE WHERE status='pending' guard prevents
    re-surfacing. 2F.X adds the unreject button + the
    rejection_count >= 3 runner-side guard; this PR just persists
    the count.

    Returns:
      {
        "action": "rejected" | "already_decided",
        "record_id": UUID,
        "previous_status": str,
      }
    """
    # Pre-flight: load current state for idempotency check + audit.
    detail = await get_review_queue_record(session, record_id)
    if detail is None:
        raise ApprovalError(
            f"review_queue record {record_id} not found", status_code=404,
        )
    if detail.row.status != "pending":
        return {
            "action": "already_decided",
            "record_id": record_id,
            "previous_status": detail.row.status,
        }

    async with session.begin():
        result = await session.execute(text(
            """
            UPDATE sp.review_queue
            SET status = 'rejected',
                reviewed_by = :operator,
                reviewed_at = NOW(),
                rejection_count = rejection_count + 1
            WHERE id = :record_id AND status = 'pending'
            """
        ).bindparams(operator=operator, record_id=record_id))

        if result.rowcount == 0:
            raise ApprovalError(
                "Concurrent decision detected — another operator "
                "session decided this record while your form was open.",
                status_code=409,
            )

    return {
        "action": "rejected",
        "record_id": record_id,
        "previous_status": "pending",
    }


# ── Mutation-helper internals ──────────────────────────────────


async def _load_candidate_team_ids(
    session: AsyncSession, record_id: uuid.UUID,
) -> list[uuid.UUID]:
    """Read sp.review_queue.candidate_fixtures for the record. The
    column is misnamed (stores team_ids, NOT fixture_ids — see
    issue #121 for the rename plan). For non-collision rows the
    matcher stores exactly [home_team_id, away_team_id] in that
    order; collision rows store a flat concatenation of both sides'
    colliding team_ids.

    This helper is used only by approve_record's validation path
    for non-collision rows; collision rows validate against
    reason_detail.colliding_*_team_ids directly.
    """
    sql = text(
        "SELECT candidate_fixtures FROM sp.review_queue WHERE id = :rid"
    ).bindparams(rid=record_id)
    row = (await session.execute(sql)).first()
    if row is None or not row.candidate_fixtures:
        return []
    out: list[uuid.UUID] = []
    for item in row.candidate_fixtures:
        try:
            out.append(uuid.UUID(str(item)))
        except (ValueError, TypeError):
            continue
    return out


def _operator_alias_text(detail: "ReviewQueueDetail", side: str) -> str | None:
    """Choose the alias text for the operator's team_aliases write-back.

    Per the runner's existing alias-tier write-back convention
    (scripts/run_resolver_pass.py:434+), the alias stored is the
    provider's raw team string ("Bayern Munich"). The admin handler
    doesn't have the matcher's `signal` object to read
    `signal.home_team_candidates[0].raw` directly — so we use
    `reason_detail.home_canonical` / `away_canonical`, which is the
    matcher's interpretation of the provider's raw name.

    Functional equivalence: alias_normalized (the ON CONFLICT key)
    strips case + accents identically regardless of which form we
    write. Operators browsing sp.team_aliases see the matcher's
    canonical form rather than the literal provider raw, which is
    slightly less faithful but acceptable for the operator-review
    write-back source.

    Returns None if reason_detail doesn't carry the canonical field —
    in that case the alias write-back is skipped for that side
    (operator-approve still succeeds; the strict tier compounding
    just doesn't fire for this record on the next cron).
    """
    key = "home_canonical" if side == "home" else "away_canonical"
    text_value = detail.reason_detail.get(key)
    if not text_value:
        return None
    text_value = str(text_value).strip()
    return text_value or None


def _normalize_alias(text_value: str) -> str:
    """Match the same alias_normalized convention the runner uses
    (resolver._normalize.normalize_name). Imported lazily so the
    queries module stays import-light at admin module load.
    """
    from resolver._normalize import normalize_name
    return normalize_name(text_value)
