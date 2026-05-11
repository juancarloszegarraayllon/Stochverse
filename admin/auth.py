"""Auth primitives for the admin UI.

Single-operator MVP per PHASE_2F_DESIGN.md rev1.1 Q2:

  - Password lives as a bcrypt hash in the `OPERATOR_PASSWORD_HASH`
    env var. Never plaintext.
  - Session cookie signing key lives in `OPERATOR_SESSION_SECRET`.
  - SessionMiddleware in main.py manages the cookie; this module
    provides verify_password() + the require_operator dependency.

Multi-operator path (Q5): when DB-stored hashed passwords replace the
env-var hash, only verify_password()'s lookup changes. SessionMiddleware
and require_operator stay the same shape.
"""
from __future__ import annotations

import os

import bcrypt
from fastapi import HTTPException, Request, status


_OPERATOR_PASSWORD_HASH_ENV = "OPERATOR_PASSWORD_HASH"
_OPERATOR_SESSION_SECRET_ENV = "OPERATOR_SESSION_SECRET"

# Session-key conventions. Both kept short to minimize cookie size; the
# session cookie is signed (not encrypted), so values inside are
# readable by the operator — keep them non-sensitive.
SESSION_KEY_OPERATOR = "operator"


def admin_configured() -> bool:
    """True iff both auth env vars are present. Used by route handlers
    to short-circuit with 503 when the admin UI isn't provisioned —
    keeps the rest of the FastAPI app running even when admin is off.
    """
    return bool(
        os.environ.get(_OPERATOR_PASSWORD_HASH_ENV)
        and os.environ.get(_OPERATOR_SESSION_SECRET_ENV)
    )


def verify_password(plain_password: str) -> bool:
    """Constant-time compare of the operator-supplied password against
    the env-var bcrypt hash. Returns False if admin isn't configured
    (no env var) — avoids leaking whether the password is wrong vs the
    server is misconfigured.
    """
    stored_hash = os.environ.get(_OPERATOR_PASSWORD_HASH_ENV, "")
    if not stored_hash:
        return False
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            stored_hash.encode("utf-8"),
        )
    except (ValueError, TypeError):
        # Malformed hash in env var. Don't crash the request; treat as
        # auth failure. Operators see 401 and have to fix the env var.
        return False


def require_operator(request: Request) -> str:
    """FastAPI dependency. Returns the operator identity from the
    session cookie. Raises 401 when no session is present.

    Mounting route handlers with `Depends(require_operator)` ensures
    every protected route gets the session check without per-route
    boilerplate. Static guard in tests confirms every mutating route
    pulls this dependency.

    The 503-configuration check fires BEFORE the session read because
    `request.session` only exists when SessionMiddleware is installed,
    which only happens when OPERATOR_SESSION_SECRET is set. Reading
    `.session` on an unconfigured deployment would crash with
    `AssertionError`/`AttributeError` — turning a clean 503 into a
    500. This dependency is the single source of truth for the gate;
    route handlers don't need to repeat it.
    """
    if not admin_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "admin UI is not configured on this deployment "
                "(OPERATOR_PASSWORD_HASH and OPERATOR_SESSION_SECRET "
                "must both be set; see DEPLOYMENT.md for provisioning)."
            ),
        )
    operator = request.session.get(SESSION_KEY_OPERATOR)
    if not operator:
        # 401 with WWW-Authenticate so curl-style clients see the
        # challenge cleanly. Browsers hitting an HTML page will see
        # this as the unauthenticated state — the GET /admin/login
        # handler renders the form instead of 401-ing.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="not_authenticated",
            headers={"WWW-Authenticate": "FormBased"},
        )
    return operator
