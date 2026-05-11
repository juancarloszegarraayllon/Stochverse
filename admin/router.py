"""Admin router — auth scaffolding for sub-PR #1 + landing page.

Routes:

  GET  /admin/login    — render login form
  POST /admin/login    — verify password, set session cookie, redirect
  POST /admin/logout   — clear session, redirect to /admin/login
  GET  /admin/         — landing page (placeholder for the
                          sub-PR #2 review-queue list view)

The review-queue + anchor_failed routes land in subsequent sub-PRs.
This PR ships only the auth surface so it's reviewable in isolation.

Static asset mount (`/admin/static`) and SessionMiddleware are wired
in main.py, not here — the router stays import-light and the operator
can disable the admin UI by not setting OPERATOR_SESSION_SECRET.
"""
from __future__ import annotations

import pathlib

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates

from .auth import (
    SESSION_KEY_OPERATOR,
    admin_configured,
    require_operator,
    verify_password,
)


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
    if not admin_configured():
        return _not_configured_response()
    # Placeholder for the sub-PR #2 review-queue list view. Sub-PR #1
    # exists to prove the auth surface end-to-end; the actual queue
    # rendering ships next.
    return templates.TemplateResponse(
        request, "index.html", {"operator": operator},
    )
