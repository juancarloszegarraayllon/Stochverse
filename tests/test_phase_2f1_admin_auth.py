"""Phase 2F.1 sub-PR #1 — admin auth integration tests.

Per PHASE_2F_DESIGN.md rev1.1 Q2 (auth shape: bcrypt env var +
SessionMiddleware) and the implementation locks (bcrypt directly,
separate OPERATOR_SESSION_SECRET, progressive enhancement via
form POSTs).

This sub-PR ships the auth surface and a placeholder landing page.
Subsequent sub-PRs (#2 list, #3 mutations, #4 anchor_failed) build
on this scaffolding.

Tests cover:

  - Auth dependency: protected routes 401 without session.
  - Configuration gate: routes return 503 when env vars unset.
  - Login flow: GET form renders; POST with bad password 401;
    POST with good password sets session cookie + redirects.
  - Already-logged-in redirect: GET /admin/login while authed → /admin/.
  - Logout flow: POST /admin/logout clears session + redirects.
  - bcrypt.checkpw round-trip against a known hash.

Tests use FastAPI TestClient (synchronous) with monkeypatched env
vars. No database access — admin auth has no DB dependency in
sub-PR #1.
"""
from __future__ import annotations

import os

import bcrypt
import pytest
from starlette.testclient import TestClient


# ── Helpers ─────────────────────────────────────────────────────


_TEST_PASSWORD = "test-password-not-real-12345"


@pytest.fixture
def app_with_admin(monkeypatch):
    """Reload main.py with the admin env vars set so the FastAPI
    app re-mounts SessionMiddleware + the admin router. Returns a
    TestClient bound to the freshly-configured app.

    We re-import main rather than mutating an existing app object
    because SessionMiddleware can't be added after the app starts
    serving requests (Starlette caches the middleware stack).
    """
    test_hash = bcrypt.hashpw(_TEST_PASSWORD.encode(), bcrypt.gensalt()).decode()
    monkeypatch.setenv("OPERATOR_PASSWORD_HASH", test_hash)
    monkeypatch.setenv(
        "OPERATOR_SESSION_SECRET",
        "test-session-secret-not-real-aaaaaaaaaaaaaaaa",
    )

    # Drop any cached main module so the new env vars take effect.
    import sys
    for mod in list(sys.modules):
        if mod == "main" or mod.startswith("main.") or mod.startswith("admin"):
            del sys.modules[mod]

    import main  # noqa: E402  (intentional re-import after env mutation)
    return TestClient(main.app)


@pytest.fixture
def app_without_admin(monkeypatch):
    """Same shape, but with the admin env vars explicitly missing.
    Verifies the 503-when-not-configured fallback."""
    monkeypatch.delenv("OPERATOR_PASSWORD_HASH", raising=False)
    monkeypatch.delenv("OPERATOR_SESSION_SECRET", raising=False)

    import sys
    for mod in list(sys.modules):
        if mod == "main" or mod.startswith("main.") or mod.startswith("admin"):
            del sys.modules[mod]

    import main  # noqa: E402
    return TestClient(main.app)


# ── auth.py unit-level checks (no app needed) ──────────────────


class TestAuthPrimitives:
    def test_verify_password_round_trip(self, monkeypatch):
        from admin.auth import verify_password
        test_hash = bcrypt.hashpw(b"hello-world", bcrypt.gensalt()).decode()
        monkeypatch.setenv("OPERATOR_PASSWORD_HASH", test_hash)

        assert verify_password("hello-world") is True
        assert verify_password("hello-WORLD") is False
        assert verify_password("") is False
        assert verify_password("hello-world-extra") is False

    def test_verify_password_returns_false_when_unset(self, monkeypatch):
        from admin.auth import verify_password
        monkeypatch.delenv("OPERATOR_PASSWORD_HASH", raising=False)
        # No env var → always False. Don't leak misconfiguration as a
        # 500; treat as auth failure.
        assert verify_password("anything") is False

    def test_verify_password_handles_malformed_hash(self, monkeypatch):
        from admin.auth import verify_password
        # Bad bcrypt hash. The function should swallow the ValueError
        # and return False, not crash the request.
        monkeypatch.setenv("OPERATOR_PASSWORD_HASH", "not-a-real-bcrypt-hash")
        assert verify_password("anything") is False

    def test_admin_configured_requires_both_env_vars(self, monkeypatch):
        from admin.auth import admin_configured

        monkeypatch.delenv("OPERATOR_PASSWORD_HASH", raising=False)
        monkeypatch.delenv("OPERATOR_SESSION_SECRET", raising=False)
        assert admin_configured() is False

        monkeypatch.setenv("OPERATOR_PASSWORD_HASH", "x")
        assert admin_configured() is False

        monkeypatch.setenv("OPERATOR_SESSION_SECRET", "y")
        assert admin_configured() is True

        # Empty-string env vars count as unset — Railway sometimes
        # sets vars to empty during partial provisioning.
        monkeypatch.setenv("OPERATOR_PASSWORD_HASH", "")
        assert admin_configured() is False


# ── End-to-end auth flow (against the live FastAPI app) ────────


class TestLoginFlow:
    def test_login_form_renders(self, app_with_admin):
        resp = app_with_admin.get("/admin/login")
        assert resp.status_code == 200
        # Sanity-check the form HTML — full template tests would be
        # too brittle; just verify the POST target + password field.
        assert 'action="/admin/login"' in resp.text
        assert 'name="password"' in resp.text

    def test_login_with_bad_password_returns_401(self, app_with_admin):
        resp = app_with_admin.post(
            "/admin/login",
            data={"password": "wrong-password"},
        )
        assert resp.status_code == 401
        # No session cookie set on failed login.
        assert "stochverse_admin_session" not in resp.cookies
        # Form re-renders with the error message visible.
        assert "Invalid password" in resp.text

    def test_login_with_good_password_redirects_and_sets_cookie(self, app_with_admin):
        resp = app_with_admin.post(
            "/admin/login",
            data={"password": _TEST_PASSWORD},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/admin/"
        assert "stochverse_admin_session" in resp.cookies

    def test_already_logged_in_redirects_from_login_form(self, app_with_admin):
        # Authenticate first.
        app_with_admin.post(
            "/admin/login",
            data={"password": _TEST_PASSWORD},
            follow_redirects=False,
        )
        # GET /admin/login with the session cookie set → redirect to /admin/.
        resp = app_with_admin.get("/admin/login", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/admin/"


class TestProtectedRoutes:
    def test_root_admin_requires_auth(self, app_with_admin):
        resp = app_with_admin.get("/admin/", follow_redirects=False)
        # require_operator raises 401; FastAPI returns it as the
        # response. (Future-improvement: redirect to /admin/login
        # instead — out of scope for sub-PR #1.)
        assert resp.status_code == 401

    def test_root_admin_renders_after_login(self, app_with_admin):
        # POST login (sets session), then GET /admin/.
        app_with_admin.post(
            "/admin/login",
            data={"password": _TEST_PASSWORD},
            follow_redirects=False,
        )
        resp = app_with_admin.get("/admin/")
        assert resp.status_code == 200
        # Placeholder landing page shows the operator identity.
        assert "Signed in" in resp.text
        # Logout button is visible on the authed page (rendered by
        # base.html's header block).
        assert 'action="/admin/logout"' in resp.text


class TestLogoutFlow:
    def test_logout_clears_session(self, app_with_admin):
        # Authenticate.
        app_with_admin.post(
            "/admin/login",
            data={"password": _TEST_PASSWORD},
            follow_redirects=False,
        )
        # Logout.
        resp = app_with_admin.post("/admin/logout", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/admin/login"
        # After logout, /admin/ should 401 again.
        resp2 = app_with_admin.get("/admin/", follow_redirects=False)
        assert resp2.status_code == 401

    def test_logout_must_be_post_not_get(self, app_with_admin):
        # GET /admin/logout shouldn't exist as a route — prevents
        # `<img src="/admin/logout">` CSRF-style logout attacks.
        resp = app_with_admin.get("/admin/logout", follow_redirects=False)
        assert resp.status_code == 405  # method not allowed


# ── Not-configured fallback ────────────────────────────────────


class TestNotConfiguredFallback:
    def test_login_returns_503_when_unset(self, app_without_admin):
        resp = app_without_admin.get("/admin/login")
        assert resp.status_code == 503
        assert "not configured" in resp.text.lower()

    def test_login_post_returns_503_when_unset(self, app_without_admin):
        # Posting to a not-configured admin shouldn't 500 even if a
        # request slips in mid-deploy — return 503 consistently.
        resp = app_without_admin.post(
            "/admin/login",
            data={"password": "whatever"},
        )
        assert resp.status_code == 503

    def test_main_app_still_works_when_admin_unset(self, app_without_admin):
        # The public API mounted at / should be unaffected by the
        # admin not being configured. Hit the root route as a smoke
        # test. The actual content doesn't matter for this test;
        # what matters is that the response is NOT 503.
        resp = app_without_admin.get("/", follow_redirects=False)
        # 200, 301, or 404 are all fine — we just need to confirm
        # the rest of the app didn't get poisoned by admin's
        # not-configured state.
        assert resp.status_code != 503, (
            "Admin UI being unset should not break the rest of the "
            "FastAPI app's routes."
        )


# ── Static guards on the auth surface ──────────────────────────


class TestAuthStaticGuards:
    """Static guards that protect against accidental security regressions
    — e.g., a future refactor that drops the require_operator dep on
    the index route, or one that introduces GET /admin/logout."""

    def test_index_route_has_require_operator_dependency(self):
        # Walk the routes for /admin/ and confirm the require_operator
        # dependency is wired. Without it, the landing page becomes
        # public.
        from admin import router as admin_router
        from admin.auth import require_operator
        for route in admin_router.routes:
            if getattr(route, "path", None) == "/admin/" and "GET" in getattr(route, "methods", set()):
                deps = [d.call for d in route.dependant.dependencies]
                assert require_operator in deps, (
                    "GET /admin/ must depend on require_operator. "
                    "Dropping the dep makes the landing page public."
                )
                return
        raise AssertionError("GET /admin/ route not found")

    def test_logout_route_is_post_only(self):
        from admin import router as admin_router
        for route in admin_router.routes:
            if getattr(route, "path", None) == "/admin/logout":
                methods = getattr(route, "methods", set())
                assert "POST" in methods
                assert "GET" not in methods, (
                    "Logout must be POST-only to prevent "
                    "<img src=/admin/logout> CSRF-style attacks."
                )
                return
        raise AssertionError("/admin/logout route not found")
