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
from typing import AsyncIterator

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
    confidence_min: float | None = Query(None, ge=0.0, le=1.0),
):
    """Paginated review-queue list. Default sort confidence DESC,
    default status='pending' (uses the partial index from 2F.0).

    The status filter accepts 'pending' / 'approved' / 'rejected';
    other values fall through to empty results rather than 400 —
    operators sometimes paste arbitrary status values from query
    logs and a hard error mid-debug isn't helpful.
    """
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
    return templates.TemplateResponse(
        request,
        "review_queue_detail.html",
        {"operator": operator, "detail": detail},
    )
