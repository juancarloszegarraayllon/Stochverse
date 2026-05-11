"""Phase 2F.1 admin module — operator review-queue UI.

Mounted on the main FastAPI app at `/admin/`. Provides:

  - sub-PR #1 (this PR): auth scaffolding (login / logout) + base template.
  - sub-PR #2: review-queue list + detail views (read-only).
  - sub-PR #3: approve / reject mutating actions + sp.team_aliases write-back.
  - sub-PR #4: anchor_failed surface (read-only with handoff to 2D.5.1 CLI).

Configuration (per PHASE_2F_DESIGN.md rev1.1 Q2 + Q5 schema-migration
path):

  OPERATOR_PASSWORD_HASH  — bcrypt hash of the operator password.
                            Generate with `htpasswd -nbB operator <pw> | cut -d: -f2`
                            or `python -c "import bcrypt; print(bcrypt.hashpw(b'<pw>', bcrypt.gensalt()).decode())"`.
  OPERATOR_SESSION_SECRET — random secret for cookie signing. Generate
                            with `python -c "import secrets; print(secrets.token_urlsafe(32))"`.

If either env var is missing at startup, the admin router still mounts
but every route returns 503 "admin not configured" instead of crashing
the rest of the app (the public API on the same FastAPI instance
should keep serving).
"""
from .router import router

__all__ = ["router"]
