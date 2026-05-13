"""Admin router — auth scaffolding (sub-PR #1) + review-queue read-
only list and detail views (sub-PR #2).

Routes:

  GET  /admin/login                 — render login form
  POST /admin/login                 — verify password, set session, redirect
  POST /admin/logout                — clear session, redirect to /admin/login
  GET  /admin/                      — redirect to /admin/review-queue
  GET  /admin/review-queue          — list view (paginated, filtered)
  GET  /admin/review-queue/<uuid>   — detail view (single record)

The mutating actions (approve / reject) ship in sub-PR #3. The
anchor_failed surface ships in sub-PR #4.

Static asset mount (`/admin/static`) and SessionMiddleware are wired
in main.py, not here — the router stays import-light and the operator
can disable the admin UI by not setting OPERATOR_SESSION_SECRET.
"""
from __future__ import annotations

import pathlib
import uuid as uuid_pkg
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import (
    SESSION_KEY_OPERATOR,
    admin_configured,
    require_operator,
    verify_password,
)
from . import queries


router = APIRouter(prefix="/admin", tags=["admin"])

_TEMPLATES_DIR = pathlib.Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def _not_configured_response() -> PlainTextResponse:
    """Returned by every admin route when OPERATOR_PASSWORD_HASH or
    OPERATOR_SESSION_SECRET is unset. 503 (rather than 500) signals
    "service intentionally unavailable" — Cloudflare / monitoring
    treat 503 as configuration, not a crash.
    """
    return PlainTextResponse(
        "admin UI is not configured on this deployment "
        "(OPERATOR_PASSWORD_HASH and OPERATOR_SESSION_SECRET must "
        "both be set; see DEPLOYMENT.md for provisioning).",
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    if not admin_configured():
        return _not_configured_response()
    # Already-logged-in operators bypass the form and land on /admin/.
    if request.session.get(SESSION_KEY_OPERATOR):
        return RedirectResponse(url="/admin/", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request, "login.html", {"error": None},
    )


@router.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request, password: str = Form(...)):
    if not admin_configured():
        return _not_configured_response()
    if not verify_password(password):
        # Re-render with an error message. NOT a redirect — preserves
        # the operator's place in the flow and avoids leaking failed-
        # password URLs into browser history.
        return templates.TemplateResponse(
            request, "login.html",
            {"error": "Invalid password."},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    # Set the session and redirect to /admin/. PRG (POST-redirect-GET)
    # pattern — reloading the destination doesn't re-submit the form.
    request.session[SESSION_KEY_OPERATOR] = "operator"
    return RedirectResponse(url="/admin/", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/logout")
async def logout(request: Request):
    # POST not GET — prevents `<img src="/admin/logout">` CSRF-style
    # log-out attacks. Operator clicks a form-button to sign out.
    request.session.pop(SESSION_KEY_OPERATOR, None)
    return RedirectResponse(url="/admin/login", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, operator: str = Depends(require_operator)):
    # No admin_configured() check here — require_operator handles it
    # as the single source of truth (raises 503 before reading
    # request.session, which would crash without SessionMiddleware).
    # The list view IS the operator's landing page; index redirects.
    return RedirectResponse(
        url="/admin/review-queue", status_code=status.HTTP_303_SEE_OTHER,
    )


# ── DB session dependency ──────────────────────────────────────


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding an AsyncSession. Imported lazily so
    the admin module stays loadable when DATABASE_URL is unset (tests
    without DB still hit auth flows). 503 if the DB isn't configured —
    consistent with the admin_configured() fallback shape.
    """
    from db import async_session
    if async_session is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="DATABASE_URL not configured; admin UI requires Postgres.",
        )
    async with async_session() as session:
        yield session


# ── Review-queue read-only views (sub-PR #2) ───────────────────


@router.get("/review-queue", response_class=HTMLResponse)
async def review_queue_list(
    request: Request,
    operator: str = Depends(require_operator),
    session: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(queries.DEFAULT_PAGE_SIZE, ge=1, le=queries.MAX_PAGE_SIZE),
    status_filter: str = Query("pending", alias="status"),
    provider: str | None = Query(None),
    sport: str | None = Query(None),
    # confidence_min bound as str | None (not float | None) because
    # the filter form submits an empty string when the input is
    # blank — FastAPI's float binder treats "" as a parse error and
    # returns 422, kicking the operator out of their flow. Parse to
    # float manually below with empty-string and out-of-range
    # fallbacks to None (= no filter applied).
    confidence_min_raw: str | None = Query(None, alias="confidence_min"),
):
    """Paginated review-queue list. Default sort confidence DESC,
    default status='pending' (uses the partial index from 2F.0).

    The status filter accepts 'pending' / 'approved' / 'rejected';
    other values fall through to empty results rather than 400 —
    operators sometimes paste arbitrary status values from query
    logs and a hard error mid-debug isn't helpful.
    """
    # Defensive parse of confidence_min — pre-fix the form's empty
    # input caused 422; now empty / malformed / out-of-range all
    # silently degrade to "no filter applied" rather than erroring
    # the operator out of the queue view.
    confidence_min: float | None = None
    if confidence_min_raw and confidence_min_raw.strip():
        try:
            parsed = float(confidence_min_raw.strip())
            if 0.0 <= parsed <= 1.0:
                confidence_min = parsed
        except ValueError:
            # Malformed numeric input (e.g., "xyz") → ignore filter.
            pass

    page_data = await queries.list_review_queue(
        session,
        status=status_filter,
        provider=provider,
        sport=sport,
        confidence_min=confidence_min,
        page=page,
        page_size=page_size,
    )
    return templates.TemplateResponse(
        request,
        "review_queue_list.html",
        {
            "operator": operator,
            "page_data": page_data,
            # Echo filter state into the template so the form
            # repopulates correctly on submit.
            "filters": {
                "status": status_filter,
                "provider": provider or "",
                "sport": sport or "",
                "confidence_min": (
                    f"{confidence_min:.2f}" if confidence_min is not None else ""
                ),
            },
        },
    )


@router.get("/review-queue/{record_id}", response_class=HTMLResponse)
async def review_queue_detail(
    request: Request,
    record_id: uuid_pkg.UUID,
    operator: str = Depends(require_operator),
    session: AsyncSession = Depends(get_db),
):
    """Single review_queue record + candidate-team JOIN (Q6 design
    lock). 404 if the UUID doesn't match a row.
    """
    detail = await queries.get_review_queue_record(session, record_id)
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"review_queue record {record_id} not found",
        )
    # next_record_id is only meaningful when the panel renders
    # _decision_result.html (status != 'pending'). For the pending
    # path the form is rendered instead, and next_record_id is
    # unused. Computing unconditionally keeps the template context
    # uniform — one extra index scan per page load is negligible.
    next_record_id = await queries.find_next_pending_record_id(session)
    return templates.TemplateResponse(
        request,
        "review_queue_detail.html",
        {
            "operator": operator,
            "detail": detail,
            "form_error": None,
            "next_record_id": next_record_id,
        },
    )


# ── Approve / reject (sub-PR #3) ───────────────────────────────


def _is_htmx_request(request: Request) -> bool:
    """True iff the request carries an HX-Request: true header.
    HTMX clients send this on every hx-* triggered request; plain
    browsers (form POST with no JS) don't.

    Per Q5 progressive enhancement: handler responds with a fragment
    template for HTMX, full-page redirect for plain browsers.
    """
    return request.headers.get("HX-Request", "").lower() == "true"


async def _decision_response(
    request: Request,
    *,
    record_id: uuid_pkg.UUID,
    decision_result: dict[str, Any],
    operator: str,
    next_record_id: uuid_pkg.UUID | None,
    session: AsyncSession,
):
    """Shape the response per Q4 (candidates panel fragment for HTMX,
    full-page redirect for no-JS).

    - HTMX path: re-load `detail` AFTER the mutation so the fragment
      template can render fresh audit fields (reviewed_by,
      reviewed_at, rejection_count). _decision_result.html references
      `detail.row.*` to show the audit grid — without re-loading we
      get `jinja2.exceptions.UndefinedError: 'detail' is undefined`
      from the fragment path.

      Then render _decision_result.html — replaces the candidates
      panel in-place via HTMX's hx-swap="outerHTML" target on the
      panel. Includes a "Go to next record" link per Q4 refinement
      (next_record_id resolved by queries.find_next_pending_record_id;
      None when the queue is drained).
    - No-JS path: 303 redirect to the detail view with the record's
      new state already loaded. The detail handler computes its own
      next_record_id when rendering — no need to pass it through the
      redirect. The GET detail handler also re-loads detail.
    """
    if _is_htmx_request(request):
        detail = await queries.get_review_queue_record(session, record_id)
        return templates.TemplateResponse(
            request,
            "_decision_result.html",
            {
                "decision": decision_result,
                "detail": detail,
                "record_id": record_id,
                "operator": operator,
                "next_record_id": next_record_id,
            },
        )
    # No-JS fallback: redirect back to detail view. Operator sees
    # the authoritative state (status=approved/rejected, audit fields
    # populated) AND the detail handler re-computes next_record_id
    # for the rendered _decision_result.html block.
    return RedirectResponse(
        url=f"/admin/review-queue/{record_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _approval_error_response(
    request: Request,
    *,
    record_id: uuid_pkg.UUID,
    error: queries.ApprovalError,
    operator: str,
    session: AsyncSession,
):
    """Operator's submission was rejected by server-side validation
    (bad team_id, missing kickoff, concurrent decision, etc.). Branch
    on the HX-Request header so HTMX clients get a targeted error
    fragment and no-JS clients get the existing full-page re-render
    with the error message at the top of the form panel.

    HTMX path (Phase 2F.1 sub-PR #7 / Issue #131):
      Render _error.html partial with response headers
      HX-Reswap: outerHTML + HX-Retarget: #form-error. HTMX swaps
      the partial into the always-present <div id="form-error">
      wrapper in _decision_form.html, leaving the form (radio
      buttons, hidden inputs) untouched. Status 200 because HTMX
      aborts swaps on non-2xx by default — without HX-Request-aware
      handling, the swap silently fails and the operator sees
      nothing change (the bug Issue #131 fixed).

    No-JS path (unchanged from sub-PR #3):
      Re-render review_queue_detail.html with form_error set at the
      original ApprovalError status code (400 / 404 / 409). Plain
      browsers see the full page with the form_error block visible
      at the top of the form.

    Returns a coroutine — caller must await.
    """
    if _is_htmx_request(request):
        # HTMX path: small fragment + HX-Reswap/HX-Retarget headers.
        # No detail re-load needed — the partial only renders the
        # error message; form state already lives in the browser DOM.
        async def _render_htmx():
            return templates.TemplateResponse(
                request,
                "_error.html",
                {"message": error.message},
                status_code=status.HTTP_200_OK,
                headers={
                    "HX-Reswap": "outerHTML",
                    "HX-Retarget": "#form-error",
                },
            )
        return _render_htmx()
    # No-JS path: full-page re-render at the error's original status
    # code. Operator sees the form_error block at the top of the
    # form (via _decision_form.html's {% if form_error %} branch).
    async def _render_no_js():
        detail = await queries.get_review_queue_record(session, record_id)
        if detail is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"review_queue record {record_id} not found",
            )
        return templates.TemplateResponse(
            request,
            "review_queue_detail.html",
            {
                "operator": operator,
                "detail": detail,
                "form_error": error.message,
            },
            status_code=error.status_code,
        )
    return _render_no_js()


@router.post("/review-queue/{record_id}/approve")
async def approve(
    request: Request,
    record_id: uuid_pkg.UUID,
    home_team_id: uuid_pkg.UUID = Form(...),
    away_team_id: uuid_pkg.UUID = Form(...),
    operator: str = Depends(require_operator),
    session: AsyncSession = Depends(get_db),
):
    """Operator approves the matcher's decision for this record.

    Body (form-encoded):
      - home_team_id (UUID): operator's chosen home team
      - away_team_id (UUID): operator's chosen away team

    For non-collision rows the operator's submission must match the
    matcher's single candidate pair (validated server-side). For
    collision rows the operator picks one team from each colliding
    side; submission validated against the collision sets.

    Idempotent on double-click: WHERE status='pending' guard ensures
    a second click returns the current state without re-writing.
    """
    try:
        result = await queries.approve_record(
            session,
            record_id=record_id,
            operator=operator,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
        )
    except queries.ApprovalError as e:
        return await _approval_error_response(
            request, record_id=record_id, error=e,
            operator=operator, session=session,
        )
    next_record_id = await queries.find_next_pending_record_id(session)
    return await _decision_response(
        request, record_id=record_id,
        decision_result=result, operator=operator,
        next_record_id=next_record_id,
        session=session,
    )


@router.post("/review-queue/{record_id}/reject")
async def reject(
    request: Request,
    record_id: uuid_pkg.UUID,
    operator: str = Depends(require_operator),
    session: AsyncSession = Depends(get_db),
):
    """Operator rejects the matcher's decision. No body — rejection
    is a single decision, not a candidate selection.

    Per Q4 design: rejection is re-queueable; the runner's WHERE
    status='pending' guard from PR #108 prevents re-surfacing. The
    rejection_count column (added in 2F.0 per Q4 refinement)
    increments on each reject — 2F.X adds the unreject button + the
    rejection_count >= 3 runner-side guard.
    """
    try:
        result = await queries.reject_record(
            session, record_id=record_id, operator=operator,
        )
    except queries.ApprovalError as e:
        return await _approval_error_response(
            request, record_id=record_id, error=e,
            operator=operator, session=session,
        )
    next_record_id = await queries.find_next_pending_record_id(session)
    return await _decision_response(
        request, record_id=record_id,
        decision_result=result, operator=operator,
        next_record_id=next_record_id,
        session=session,
    )


# ── Anchor-failed surface (sub-PR #4, design doc rev1.2 §Q6) ─────


@router.get("/anchor-failed", response_class=HTMLResponse)
async def anchor_failed_list(
    request: Request,
    operator: str = Depends(require_operator),
    session: AsyncSession = Depends(get_db),
    provider: str | None = Query(None),
    sport: str | None = Query(None),
    fail_reason: str | None = Query(None),
):
    """Anchor-failed records from the most recent
    ANCHOR_FAILED_RECENT_RUNS resolver_runs. Read-only — no POST
    handlers exist under /admin/anchor-failed/ (static guard test
    asserts this).

    No pagination: the run-window cap bounds the result to a few
    hundred rows in steady state. Operators filter via the
    provider/sport/fail_reason query params.
    """
    page = await queries.list_anchor_failed(
        session,
        provider=provider,
        sport=sport,
        fail_reason=fail_reason,
    )
    return templates.TemplateResponse(
        request,
        "anchor_failed_list.html",
        {
            "operator": operator,
            "page": page,
            "fail_reason_family": queries.ANCHOR_FAILED_FAIL_REASONS,
            "format_fail_reason": queries._format_fail_reason,
        },
    )


@router.get(
    "/anchor-failed/{provider}/{provider_record_id}",
    response_class=HTMLResponse,
)
async def anchor_failed_detail(
    request: Request,
    provider: str,
    provider_record_id: str,
    operator: str = Depends(require_operator),
    session: AsyncSession = Depends(get_db),
):
    """Single anchor-failed record. Shows the raw provider payload,
    the matcher's parsed signal, the fail_reason + reason_detail
    snapshot, and the 'Suggest alias' widget per side with the top-N
    closest sp.teams candidates.

    Read-only — no approve/reject. Operator's action is to copy the
    pre-filled `make alias-add` command from the Suggest-alias widget
    and run it locally.
    """
    detail = await queries.get_anchor_failed_record(
        session,
        provider=provider,
        provider_record_id=provider_record_id,
    )
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No anchor-failed record for ({provider!r}, "
                f"{provider_record_id!r}) in the most recent "
                f"{queries.ANCHOR_FAILED_RECENT_RUNS} resolver_runs. "
                f"Older records require a direct SQL query against "
                f"sp.resolution_log."
            ),
        )
    return templates.TemplateResponse(
        request,
        "anchor_failed_detail.html",
        {
            "operator": operator,
            "detail": detail,
            "format_fail_reason": queries._format_fail_reason,
        },
    )
