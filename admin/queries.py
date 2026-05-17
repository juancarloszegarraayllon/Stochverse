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

    # ── Phase 2D.5 sub-PR #1: asymmetric anchor-failure routing ──
    #
    # routing_shape is the discriminator the template branches on.
    # Distinct from collision shape (which sets {side}_collision +
    # colliding_{side}_team_ids); asymmetric rows use these fields
    # instead. None for collision rows + pre-2D.5 rows.
    #
    # When routing_shape == "asymmetric_anchor_failure":
    #   - asymmetric_failed_side: "home" | "away" — which side
    #     anchor-failed. Derived from reason_detail's
    #     {side}_anchor_failed booleans (exactly one is True).
    #   - asymmetric_failed_side_candidate_team_ids: top-N trigram
    #     candidates for the failed side, surfaced by the matcher in
    #     row.candidate_fixtures[1:]. row.candidate_fixtures[0] is the
    #     anchored side's team_id (operator-side rendering convention).
    routing_shape: str | None = None
    asymmetric_failed_side: str | None = None
    asymmetric_failed_side_candidate_team_ids: list[uuid.UUID] = field(
        default_factory=list
    )


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

    # Phase 2D.5 sub-PR #1: derive asymmetric-routing fields.
    # row.candidate_fixtures is a JSONB array of UUID strings; for
    # asymmetric rows the matcher emits [anchored_team_id, ...top-N
    # failed-side trigram candidates] (resolver/fuzzy_tier/matcher.py
    # asymmetric branch). Parse + slice it here so the template can
    # render the failed-side radio buttons directly.
    routing_shape = reason_detail.get("routing_shape")
    asymmetric_failed_side: str | None = None
    asymmetric_failed_candidates: list[uuid.UUID] = []
    if routing_shape == "asymmetric_anchor_failure":
        if reason_detail.get("home_anchor_failed"):
            asymmetric_failed_side = "home"
        elif reason_detail.get("away_anchor_failed"):
            asymmetric_failed_side = "away"
        # Slice the candidate_fixtures list — index 0 is anchored side,
        # index 1+ are the failed-side top-N. Defensive: tolerate
        # malformed/empty lists rather than 500ing the operator's
        # detail-view fetch.
        cf_parsed = _parse_team_id_list(row.candidate_fixtures)
        if len(cf_parsed) >= 2:
            asymmetric_failed_candidates = cf_parsed[1:]

    all_team_ids: list[uuid.UUID] = list(set(
        colliding_home + colliding_away + asymmetric_failed_candidates
    ))
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
        routing_shape=routing_shape,
        asymmetric_failed_side=asymmetric_failed_side,
        asymmetric_failed_side_candidate_team_ids=asymmetric_failed_candidates,
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
    asymmetric_candidates: list[uuid.UUID] | None = None,
) -> None:
    """Server-side validation that the operator-submitted team_id
    matches the candidate set the matcher surfaced. Raises
    ApprovalError on mismatch.

    Per design: never trust client-submitted IDs. The operator MUST
    pick from the matcher's candidates — arbitrary team_ids would
    let an attacker (or accidental URL tampering) link a provider
    record to any team they want.

    Three validation modes, checked in this precedence order:

      (1) `asymmetric_candidates is not None` (Phase 2D.5 sub-PR #1
          asymmetric failed-side) → submitted must be in the
          asymmetric candidate set (top-N trigram for the failed
          side). The candidate set is sport-gated upstream by
          `resolver/fuzzy_tier/matcher.py::_top_n_trigram_candidates`,
          so this validation is both an in-set check AND a
          defense-in-depth sport-gate.

      (2) `side_collision=True` → submitted must be in
          `side_colliding_ids`. Existing collision validation.

      (3) Otherwise (non-collision, non-asymmetric) → submitted
          must equal `side_default_id` (matcher's single pick).
          When `side_default_id is None`, validation is a noop —
          rare; happens for asymmetric anchored side where the
          matcher's pick IS the operator's only choice. The
          asymmetric failed-side ALWAYS goes through mode (1);
          asymmetric anchored side goes through mode (3) with a
          non-None side_default_id.

    Precedence rationale: in practice a row is either
    asymmetric-shaped or collision-shaped, never both — the matcher
    emits one or the other, not both. The defensive precedence
    ordering here (asymmetric first) covers the hypothetical case
    where a future code change accidentally sets both shapes on
    the same row. Asymmetric wins because its discriminator is
    explicit (`routing_shape` string constant) and the template
    branches the same way at `admin/templates/_decision_form.html`
    — keeping validation and rendering consistent prevents a
    "rendered radio buttons over candidate set A but validated
    against candidate set B" UX inconsistency.

    Mode (1) additionally guards: `asymmetric_candidates=[]` would
    silently accept no team_ids; we reject explicitly. Empty
    candidate set means the matcher's trigram lookup returned zero
    matches above the similarity floor — the approval form
    shouldn't have been rendered in that state, and accepting any
    submission would defeat the in-set check entirely.
    """
    if asymmetric_candidates is not None:
        # Phase 2D.5 sub-PR #1: asymmetric failed-side validation
        # (highest precedence — explicit discriminator wins).
        if not asymmetric_candidates:
            raise ApprovalError(
                f"{side_label}_team_id={submitted} cannot be validated: "
                f"asymmetric {side_label} side has no candidate set. "
                "Operator should reject this record rather than approve."
            )
        if submitted not in asymmetric_candidates:
            raise ApprovalError(
                f"{side_label}_team_id={submitted} is not in the "
                f"matcher's asymmetric candidate set for the "
                f"{side_label} (failed) side "
                f"({len(asymmetric_candidates)} candidates). "
                "Refusing to link to an arbitrary team."
            )
        return
    if side_collision:
        if submitted not in side_colliding_ids:
            raise ApprovalError(
                f"{side_label}_team_id={submitted} is not in the "
                f"matcher's {side_label} collision set "
                f"({len(side_colliding_ids)} candidates). "
                "Refusing to link to an arbitrary team."
            )
        return
    # Non-collision, non-asymmetric: matcher picked a specific team.
    # Operator submission must match it.
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

    All five steps inside one `async with session.begin()` block.
    Pre-flight reads (get_review_queue_record, _load_candidate_team_ids)
    ALSO live inside the block — moved there to fix the production
    500 surfaced by PR #123: pre-flight reads outside the block
    auto-begin a transaction in SQLAlchemy 2.0 autobegin mode, then
    the explicit `session.begin()` raises
    `InvalidRequestError: A transaction is already begun on this
    Session.` Matches the runner's pattern at
    scripts/run_resolver_pass.py where matcher reads happen inside
    the per-record transaction.

    Partial failure → full rollback. Per-record txn isolation matches
    the runner's PR #108 pattern.

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

    # Single transaction wrapping reads AND writes (Q3 + autobegin
    # fix). Early-return / raise paths roll back / commit an empty
    # transaction cleanly via __aexit__.
    async with session.begin():
        # Pre-flight: load the record + its candidate context.
        detail = await get_review_queue_record(session, record_id)
        if detail is None:
            raise ApprovalError(
                f"review_queue record {record_id} not found",
                status_code=404,
            )

        # Idempotency check (Q2). If already decided, return the
        # current state without any DB writes — operator's double-
        # click is a no-op, not an error.
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
        #
        # Defaults come from reason_detail.home_team_id / away_team_id
        # — the matcher unconditionally populates these for both sides
        # regardless of collision shape (resolver/alias_tier/matcher.py:216-221
        # and the corresponding fuzzy-tier emission). Per-side, mapped
        # by name.
        #
        # PR #127 fix: positional indexing into candidate_fixtures
        # ([0]/[1]) was the prior source. That worked for pure
        # non-collision rows ([home_id, away_id] flat list) but
        # BROKE for partial-collision rows: when home collides and
        # away doesn't, the matcher stores
        # candidate_fixtures = list(home_match.colliding_team_ids)
        # + list(away_match.colliding_team_ids), and away's
        # colliding_team_ids is EMPTY when away has no collision.
        # So raw_cf[1] picks the SECOND home candidate as the
        # "away default" — operator submits Cleveland and validation
        # rejects with "doesn't match home-side-team-2" (the actual
        # Minnesota / Cleveland 400 traced via PR #126 DIAG logs).
        #
        # reason_detail.{home,away}_team_id is always present per the
        # matcher's invariant and correctly identifies the per-side
        # default regardless of collision shape.
        rd_home = detail.reason_detail.get("home_team_id")
        rd_away = detail.reason_detail.get("away_team_id")
        default_home = uuid.UUID(rd_home) if rd_home else None
        default_away = uuid.UUID(rd_away) if rd_away else None

        # Phase 2D.5 sub-PR #1: asymmetric routing thread-through.
        # Per-side asymmetric_candidates is set only for the failed
        # side of an asymmetric record. The anchored side falls
        # through to standard non-collision validation (single
        # candidate, must match matcher's pick).
        asym_home_candidates: list[uuid.UUID] | None = None
        asym_away_candidates: list[uuid.UUID] | None = None
        if detail.routing_shape == "asymmetric_anchor_failure":
            if detail.asymmetric_failed_side == "home":
                asym_home_candidates = (
                    detail.asymmetric_failed_side_candidate_team_ids
                )
            elif detail.asymmetric_failed_side == "away":
                asym_away_candidates = (
                    detail.asymmetric_failed_side_candidate_team_ids
                )

        _validate_candidate_team_id(
            home_team_id,
            side_collision=detail.home_collision,
            side_colliding_ids=detail.colliding_home_team_ids,
            side_default_id=default_home,
            side_label="home",
            asymmetric_candidates=asym_home_candidates,
        )
        _validate_candidate_team_id(
            away_team_id,
            side_collision=detail.away_collision,
            side_colliding_ids=detail.colliding_away_team_ids,
            side_default_id=default_away,
            side_label="away",
            asymmetric_candidates=asym_away_candidates,
        )

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
            # Lost the race to a concurrent approve. Raise so the
            # transaction rolls back (preventing partial-state writes
            # from ensure_fixture / provider UPDATE).
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

    Pre-flight read (get_review_queue_record) and write happen in
    the SAME session.begin() block — same autobegin-fix discipline
    as approve_record above.

    Returns:
      {
        "action": "rejected" | "already_decided",
        "record_id": UUID,
        "previous_status": str,
      }
    """
    async with session.begin():
        # Pre-flight: load current state for idempotency check + audit.
        detail = await get_review_queue_record(session, record_id)
        if detail is None:
            raise ApprovalError(
                f"review_queue record {record_id} not found",
                status_code=404,
            )
        if detail.row.status != "pending":
            return {
                "action": "already_decided",
                "record_id": record_id,
                "previous_status": detail.row.status,
            }

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
            # Refinement 2 (Phase 2F.1 sub-PR #7): aligned with
            # approve_record's equivalent 409 message which includes
            # "Reload to see current state." Operators get consistent
            # recovery guidance across approve vs reject.
            raise ApprovalError(
                "Concurrent decision detected — another operator "
                "session decided this record while your form was open. "
                "Reload to see current state.",
                status_code=409,
            )

    return {
        "action": "rejected",
        "record_id": record_id,
        "previous_status": "pending",
    }


# ── Mutation-helper internals ──────────────────────────────────
#
# _load_candidate_team_ids was removed in PR #127. It read
# sp.review_queue.candidate_fixtures and returned the flat list, but
# its only call site (approve_record's default-team-id sourcing)
# was misusing positional indexing [0]/[1] on a list whose shape
# varies with collision state — the source of the Minnesota /
# Cleveland 400. Defaults now come from
# reason_detail.{home,away}_team_id which is name-keyed and
# correctly identifies per-side defaults regardless of collision
# shape. The candidate_fixtures column itself stays; only the
# helper that mis-indexed it was removed.


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


# ── Anchor-failed surface (sub-PR #4, design doc rev1.2 §Q6) ─────


# The four terminal "anchor-failed family" fail_reason values. Enumerated
# explicitly here because the resolver emits other no_match fail_reasons
# too (sport_not_classified, kickoff_at_missing, structural_normalize_
# failed, etc.) that belong to a different operator-visibility bucket
# (ingestion-level failures, not anchor-failed). See PHASE_2F_DESIGN.md
# rev1.2 §Q6 for the carve-out.
#
# Emission sites:
#   alias_no_team_resemblance       — resolver/alias_tier/matcher.py:211
#                                     (terminal for sports that don't
#                                     route to fuzzy)
#   fuzzy_no_team_resemblance       — resolver/fuzzy_tier/matcher.py:221
#                                     (terminal after alias deferred +
#                                     fuzzy ran)
#   alias_no_existing_fixture       — resolver/alias_tier/matcher.py:318
#                                     (matched teams, no fixture exists)
#   fuzzy_no_existing_fixture       — resolver/fuzzy_tier/matcher.py:301
ANCHOR_FAILED_FAIL_REASONS: tuple[str, ...] = (
    "alias_no_team_resemblance",
    "fuzzy_no_team_resemblance",
    "alias_no_existing_fixture",
    "fuzzy_no_existing_fixture",
)


# Number of most-recent sp.resolver_runs to include in the anchor-failed
# surface. The design lock (PR #133 conversation) is "fix-forward, not
# audit" — operators care about recent unresolvable records to drive
# alias-coverage extensions, not a historical archive. LIMIT 7 captures
# ~2-3 days at current cron cadence; older anchor-failed records require
# a future filter or a direct SQL query.
ANCHOR_FAILED_RECENT_RUNS: int = 7


@dataclass(frozen=True)
class AnchorFailedRow:
    """One row in the anchor-failed list view. Pre-shaped per the same
    convention as ReviewQueueRow — handler not template.
    """
    provider: str
    provider_record_id: str
    provider_title: str | None
    sport: str | None
    fail_reason: str
    decided_at: Any                 # datetime
    resolver_version: str
    run_id: uuid.UUID


@dataclass(frozen=True)
class AnchorFailedPage:
    """List-view result. No pagination — the run-window cap bounds the
    total to low-hundreds rows in steady state, so we return everything
    and let the operator filter client-side if needed."""
    rows: list[AnchorFailedRow]
    total: int
    filter_provider: str | None
    filter_sport: str | None
    filter_fail_reason: str | None
    recent_runs: int


@dataclass(frozen=True)
class SuggestedTeam:
    """One candidate team for the 'Suggest alias' widget. The detail
    view surfaces the top-N closest matches per side; operator picks
    one and the template builds the make alias-add command around the
    pick."""
    team_id: uuid.UUID
    canonical_name: str
    country_code: str | None
    similarity: float


@dataclass(frozen=True)
class AnchorFailedDetail:
    """Detail-view shape. Mirrors ReviewQueueDetail's three-panel
    layout: raw payload + parsed signal + matcher decision. The
    'Suggest alias' widget lives in the matcher-decision panel."""
    provider: str
    provider_record_id: str
    provider_title: str | None
    sport: str | None
    fail_reason: str
    reason_detail: dict[str, Any]
    decided_at: Any
    resolver_version: str
    run_id: uuid.UUID

    # Provider-table fields surfaced via LEFT JOIN. Either both Kalshi
    # fields or both FL fields are non-None; the other side is None.
    kalshi_raw_payload: dict[str, Any] | None
    fl_raw_payload: dict[str, Any] | None

    # Suggest-alias widget data. Keyed by side ("home"/"away"); each
    # entry is the parsed provider name + top-N closest team candidates
    # within the matcher-classified sport.
    suggested_aliases: dict[str, dict[str, Any]]

    # Path-state field added in PR #137 (sub-PR #4.1). Distinguishes
    # the four "no candidates shown" causes so the template can render
    # the right operator message. Sub-PR #4 (PR #133) used empty-dict
    # truthiness to fall through to one shared message, which today's
    # France/Senegal smoke test surfaced as factually wrong for three
    # of the four causes. See SUGGESTED_ALIASES_STATE_* constants and
    # _build_suggested_aliases for the state-machine.
    suggested_aliases_state: str


def _format_fail_reason(fail_reason: str) -> str:
    """Operator-readable rendering of the fail_reason string.
    The raw values are snake_case implementation names; the UI shows
    a brief human gloss next to them so non-developer operators don't
    have to mentally translate.
    """
    glosses = {
        "alias_no_team_resemblance":
            "no team name matched (alias tier — no fuzzy fallback for this sport)",
        "fuzzy_no_team_resemblance":
            "no team name matched (fuzzy tier — alias tier also failed)",
        "alias_no_existing_fixture":
            "teams matched, but no fixture in sp.fixtures for them",
        "fuzzy_no_existing_fixture":
            "teams matched (fuzzy), but no fixture in sp.fixtures for them",
    }
    return glosses.get(fail_reason, fail_reason)


async def list_anchor_failed(
    session: AsyncSession,
    *,
    provider: str | None = None,
    sport: str | None = None,
    fail_reason: str | None = None,
    recent_runs: int = ANCHOR_FAILED_RECENT_RUNS,
) -> AnchorFailedPage:
    """List anchor-failed records from the most recent N resolver_runs.

    Query shape (per sub-PR #4 design lock):
      - DISTINCT ON (provider, provider_record_id) to surface one row
        per record even when the same record failed across multiple
        cron cycles.
      - Scoped to the LIMIT N most recent sp.resolver_runs.id values
        — bounds the resolution_log scan to ~2-3 days at current
        cadence.
      - Filtered to the four-element ANCHOR_FAILED_FAIL_REASONS family.
      - LEFT JOIN to provider tables for the title (same pattern as
        the review_queue list view).

    No new index needed — the existing ix_resolution_log_run +
    ix_resolution_log_provider_record cover the access plan.
    """
    where_clauses = [
        "rl.reason_code = 'no_match'",
        "rl.reason_detail->>'fail_reason' = ANY(:fail_reasons)",
        # sp.resolver_runs.id is BIGINT autoincrement; the UUID linkage
        # to sp.resolution_log.run_id is the `run_id` column on BOTH
        # tables. Joining on `id` would type-error (UUID vs bigint).
        "rl.run_id IN (SELECT run_id FROM sp.resolver_runs "
        "              ORDER BY started_at DESC LIMIT :recent_runs)",
    ]
    params: dict[str, Any] = {
        "fail_reasons": list(ANCHOR_FAILED_FAIL_REASONS),
        "recent_runs": int(recent_runs),
    }
    if provider:
        where_clauses.append("rl.provider = :provider")
        params["provider"] = provider
    if sport:
        where_clauses.append("rl.reason_detail->>'sport' = :sport")
        params["sport"] = sport
    if fail_reason:
        # Narrow within the family. If the operator passes a fail_reason
        # outside the family, the ANY(:fail_reasons) clause above
        # excludes it — no special-case error needed.
        where_clauses.append("rl.reason_detail->>'fail_reason' = :fail_reason")
        params["fail_reason"] = fail_reason
    where_sql_joined = " AND ".join(where_clauses)

    rows_sql = text(
        f"""
        SELECT DISTINCT ON (rl.provider, rl.provider_record_id)
               rl.provider,
               rl.provider_record_id,
               rl.reason_detail,
               rl.resolver_version,
               rl.decided_at,
               rl.run_id,
               km.raw_payload->>'title' AS kalshi_title,
               fe.raw_payload->>'HOME_NAME' AS fl_home_name,
               fe.raw_payload->>'AWAY_NAME' AS fl_away_name
        FROM sp.resolution_log rl
        LEFT JOIN sp.kalshi_markets km
          ON rl.provider = 'kalshi'
         AND rl.provider_record_id = km.ticker
        LEFT JOIN sp.fl_events fe
          ON rl.provider = 'fl'
         AND rl.provider_record_id = fe.fl_event_id
        WHERE {where_sql_joined}
        ORDER BY rl.provider, rl.provider_record_id, rl.id DESC
        """
    ).bindparams(**params)
    rows_result = (await session.execute(rows_sql)).all()

    shaped_rows: list[AnchorFailedRow] = []
    for r in rows_result:
        reason_detail = r.reason_detail or {}
        title = _anchor_failed_title(
            provider=r.provider,
            kalshi_title=r.kalshi_title,
            fl_home_name=r.fl_home_name,
            fl_away_name=r.fl_away_name,
        )
        shaped_rows.append(AnchorFailedRow(
            provider=r.provider,
            provider_record_id=r.provider_record_id,
            provider_title=title,
            sport=reason_detail.get("sport"),
            fail_reason=reason_detail.get("fail_reason") or "(unknown)",
            decided_at=r.decided_at,
            resolver_version=r.resolver_version,
            run_id=r.run_id,
        ))

    return AnchorFailedPage(
        rows=shaped_rows,
        total=len(shaped_rows),
        filter_provider=provider,
        filter_sport=sport,
        filter_fail_reason=fail_reason,
        recent_runs=int(recent_runs),
    )


def _anchor_failed_title(
    *, provider: str,
    kalshi_title: str | None,
    fl_home_name: str | None,
    fl_away_name: str | None,
) -> str | None:
    """Recover the human title from the JOINed provider table. Kalshi
    stores it directly; FL synthesizes from HOME_NAME + AWAY_NAME. The
    sp.review_queue table has its own provider_title column populated
    by the runner (PR #115), but sp.resolution_log doesn't — so we
    JOIN at query time.
    """
    if provider == "kalshi" and kalshi_title:
        return kalshi_title
    if provider == "fl" and (fl_home_name or fl_away_name):
        return f"{fl_home_name or '?'} vs {fl_away_name or '?'}"
    return None


async def get_anchor_failed_record(
    session: AsyncSession,
    *,
    provider: str,
    provider_record_id: str,
    recent_runs: int = ANCHOR_FAILED_RECENT_RUNS,
) -> AnchorFailedDetail | None:
    """Detail-view query: the most recent anchor-failed resolution_log
    row for this (provider, provider_record_id), plus the LEFT-JOINed
    provider record's raw_payload, plus the suggested-alias widget
    data per side.

    Returns None if no anchor-failed row exists for this key in the
    recent-runs window — the route handler renders 404.
    """
    detail_sql = text(
        """
        SELECT rl.provider, rl.provider_record_id,
               rl.reason_detail, rl.resolver_version,
               rl.decided_at, rl.run_id,
               km.raw_payload AS kalshi_payload,
               fe.raw_payload AS fl_payload
        FROM sp.resolution_log rl
        LEFT JOIN sp.kalshi_markets km
          ON rl.provider = 'kalshi'
         AND rl.provider_record_id = km.ticker
        LEFT JOIN sp.fl_events fe
          ON rl.provider = 'fl'
         AND rl.provider_record_id = fe.fl_event_id
        WHERE rl.provider = :provider
          AND rl.provider_record_id = :pk
          AND rl.reason_code = 'no_match'
          AND rl.reason_detail->>'fail_reason' = ANY(:fail_reasons)
          AND rl.run_id IN (SELECT run_id FROM sp.resolver_runs
                            ORDER BY started_at DESC LIMIT :recent_runs)
        ORDER BY rl.id DESC
        LIMIT 1
        """
    ).bindparams(
        provider=provider,
        pk=provider_record_id,
        fail_reasons=list(ANCHOR_FAILED_FAIL_REASONS),
        recent_runs=int(recent_runs),
    )
    row = (await session.execute(detail_sql)).first()
    if row is None:
        return None

    reason_detail = row.reason_detail or {}
    fail_reason = reason_detail.get("fail_reason") or "(unknown)"
    sport = reason_detail.get("sport")
    title = _anchor_failed_title(
        provider=row.provider,
        kalshi_title=(row.kalshi_payload or {}).get("title"),
        fl_home_name=(row.fl_payload or {}).get("HOME_NAME"),
        fl_away_name=(row.fl_payload or {}).get("AWAY_NAME"),
    )

    state, suggested = await _build_suggested_aliases(
        session,
        reason_detail=reason_detail,
        sport_name=sport,
        provider=row.provider,
        kalshi_raw_payload=row.kalshi_payload,
        fl_raw_payload=row.fl_payload,
    )

    return AnchorFailedDetail(
        provider=row.provider,
        provider_record_id=row.provider_record_id,
        provider_title=title,
        sport=sport,
        fail_reason=fail_reason,
        reason_detail=reason_detail,
        decided_at=row.decided_at,
        resolver_version=row.resolver_version,
        run_id=row.run_id,
        kalshi_raw_payload=row.kalshi_payload,
        fl_raw_payload=row.fl_payload,
        suggested_aliases=suggested,
        suggested_aliases_state=state,
    )


# How many sp.teams candidates the "Suggest alias" widget surfaces per
# side. Three is enough for the common typo / variant case; more would
# clutter the UI without operationally helping.
SUGGESTED_TEAMS_PER_SIDE: int = 3


# Minimum trigram similarity for a candidate to qualify as a suggestion.
# Below this threshold, the candidate-suggestion query returns no rows
# and the template falls into the "no good candidates" state (Path B in
# the PR #137 conversation). pg_trgm similarity() returns a float in
# [0.0, 1.0]; 0.30 is a conservative cutoff that excludes wildly
# unrelated rows (e.g. "France" vs "Real Madrid" returns ~0.0-0.1) while
# allowing near-matches ("France" vs "France National Team" ~0.4-0.5)
# through. Tunable — bump up if operators report wrong-looking
# suggestions; bump down if real near-matches are missed.
SUGGESTED_TEAMS_MIN_SIMILARITY: float = 0.30


# Path states for the Suggest-alias widget. Surfaced on
# AnchorFailedDetail.suggested_aliases_state so the template can render
# the right message per state instead of falling through to a shared
# wrong message (the bug PR #137 fixes). Strings (not enum) so Jinja
# can compare them with literal string equality without an import.
SUGGESTED_ALIASES_STATE_OK = "ok"
SUGGESTED_ALIASES_STATE_NO_GOOD_CANDIDATES = "no_good_candidates"
SUGGESTED_ALIASES_STATE_NO_PARSED_NAMES = "no_parsed_names"
SUGGESTED_ALIASES_STATE_UNCLASSIFIED = "unclassified"


def _extract_parsed_name_for_side(
    *,
    side: str,
    reason_detail: dict[str, Any],
    provider: str,
    kalshi_raw_payload: dict[str, Any] | None,
    fl_raw_payload: dict[str, Any] | None,
) -> str | None:
    """Return the provider-supplied parsed name for the given side, or
    None if no source has it.

    Lookup chain (PR #137 B-aware fallback):
      1. reason_detail['<side>_provider_normalized']
           — alias tier sets this before its alias_no_team_resemblance
             early-return (resolver/alias_tier/matcher.py:208-211).
      2. reason_detail['<side>_canonical']
           — fuzzy tier sets this for its non-anchor-failure path
             (and PR #138 will lift it above the fuzzy_no_team_
             resemblance early-return).
      3. FL raw_payload's HOME_NAME / AWAY_NAME
           — recovers parsed name for FL records where the matcher
             didn't preserve it in reason_detail (e.g. pre-PR-#138
             fuzzy_no_team_resemblance records).

    Kalshi's title field is intentionally not split into home/away by
    this helper — title format varies ("Sinner vs Alcaraz", "Sinner @
    Alcaraz", "TournamentName: Sinner vs Alcaraz") and a naive split
    risks attaching an alias to the wrong side. For Kalshi pre-PR-#138
    fuzzy records, the helper returns None for both sides and the UI
    falls into the no_parsed_names state — the template then surfaces
    the raw payload below so the operator reads the title manually
    (which is fine because PR #138 closes this gap for new records).
    """
    parsed = (
        reason_detail.get(f"{side}_provider_normalized")
        or reason_detail.get(f"{side}_canonical")
    )
    if parsed:
        return parsed
    # FL raw_payload fallback. FL stores parsed home/away names in
    # HOME_NAME / AWAY_NAME directly; recovery is unambiguous.
    if provider == "fl" and fl_raw_payload:
        key = "HOME_NAME" if side == "home" else "AWAY_NAME"
        value = fl_raw_payload.get(key)
        if value:
            return str(value).strip() or None
    # Kalshi: title is a single string with both sides combined; no
    # safe per-side split. Return None and let the template render the
    # raw payload for the operator.
    return None


async def _build_suggested_aliases(
    session: AsyncSession,
    *,
    reason_detail: dict[str, Any],
    sport_name: str | None,
    provider: str,
    kalshi_raw_payload: dict[str, Any] | None,
    fl_raw_payload: dict[str, Any] | None,
) -> tuple[str, dict[str, dict[str, Any]]]:
    """Build the "Suggest alias" widget data + state.

    Returns (state, suggestions). State is one of:
      'ok'                  — sport classified, parsed names found, at
                              least one candidate above similarity
                              threshold. Template renders the
                              candidate-button list (sub-PR #4
                              original behavior).
      'no_good_candidates'  — sport classified, parsed names found,
                              candidate query returned no rows above
                              SUGGESTED_TEAMS_MIN_SIMILARITY. Template
                              renders a stub `make alias-add` command
                              with --team-canonical left blank for the
                              operator to fill in (Path B).
      'no_parsed_names'     — sport classified, but parsed names not
                              recoverable from reason_detail OR
                              JOINed provider payload. Template
                              surfaces raw payload + points at PR #138
                              for the resolver-side fix (Path C).
      'unclassified'        — sport not classified by matcher
                              (reason_detail.sport missing). Template
                              renders the existing
                              "no sport classified" message (Path A —
                              kept as-is since it's correct for this
                              path).

    Suggestions dict structure (same shape as sub-PR #4 except always
    has 'candidates' key even when empty):
      {side: {parsed_name: str, candidates: list[SuggestedTeam]}}

    For 'unclassified' and 'no_parsed_names', returns {} for the dict.
    For 'no_good_candidates', returns dict with parsed_name set and
    candidates as empty list — template uses parsed_name for the stub
    clipboard widget.
    """
    if not sport_name:
        return SUGGESTED_ALIASES_STATE_UNCLASSIFIED, {}

    # Resolve sport_id from sport_name. sp.sports has 17 rows; an
    # extra query is negligible.
    sport_id_row = (await session.execute(
        text("SELECT id FROM sp.sports WHERE name = :name"),
        {"name": sport_name},
    )).first()
    if sport_id_row is None:
        # Sport name doesn't resolve to an sp.sports row. This is a
        # different cause than "unclassified" — matcher classified
        # SOMETHING but it doesn't match the canonical sport list.
        # Bucket with 'unclassified' for now; if real cases surface,
        # split into its own state in a follow-up.
        return SUGGESTED_ALIASES_STATE_UNCLASSIFIED, {}
    sport_id = sport_id_row.id

    out: dict[str, dict[str, Any]] = {}
    any_parsed_name_found = False
    any_candidate_found = False
    for side in ("home", "away"):
        parsed = _extract_parsed_name_for_side(
            side=side,
            reason_detail=reason_detail,
            provider=provider,
            kalshi_raw_payload=kalshi_raw_payload,
            fl_raw_payload=fl_raw_payload,
        )
        if not parsed:
            continue
        any_parsed_name_found = True
        candidates = (await session.execute(
            text(
                """
                SELECT id, canonical_name, country_code,
                       similarity(canonical_name, :parsed) AS sim
                FROM sp.teams
                WHERE sport_id = :sport_id
                  AND similarity(canonical_name, :parsed) >= :min_sim
                ORDER BY sim DESC
                LIMIT :limit
                """
            ),
            {
                "parsed": parsed,
                "sport_id": sport_id,
                "min_sim": SUGGESTED_TEAMS_MIN_SIMILARITY,
                "limit": SUGGESTED_TEAMS_PER_SIDE,
            },
        )).all()
        shaped_candidates = [
            SuggestedTeam(
                team_id=c.id,
                canonical_name=c.canonical_name,
                country_code=c.country_code,
                similarity=float(c.sim or 0.0),
            )
            for c in candidates
        ]
        if shaped_candidates:
            any_candidate_found = True
        out[side] = {
            "parsed_name": parsed,
            "candidates": shaped_candidates,
        }

    if not any_parsed_name_found:
        # Path C — sport classified, parsed names not recoverable.
        # Drop any partial dict (shouldn't have one, but defensive).
        return SUGGESTED_ALIASES_STATE_NO_PARSED_NAMES, {}
    if not any_candidate_found:
        # Path B — parsed names found, but no candidate cleared the
        # similarity threshold. out has parsed_name set per side,
        # candidates=[] per side.
        return SUGGESTED_ALIASES_STATE_NO_GOOD_CANDIDATES, out
    # Path D / "ok" — sub-PR #4 original happy path.
    return SUGGESTED_ALIASES_STATE_OK, out
