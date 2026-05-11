"""Phase 2F.1 sub-PR #2 — review-queue read-only views.

Two test layers:

1. Unit tests on admin.queries pure helpers (no DB). Cover the
   collision-confidence cosmetic rule (Q8), candidate-count derivation
   from JSONB shape, team-id list parsing, page-bounds clamping.

2. Integration tests against a live Postgres (skipped without
   SP_INTEGRATION_DB). Seed sp.review_queue + sp.teams + sp.resolution_log
   rows, hit GET /admin/review-queue and GET /admin/review-queue/<id>,
   assert rendered HTML carries the expected fields.

The integration tests share the SP_INTEGRATION_DB gating convention
with tests/test_resolver_2b.py / test_phase_2f0_migration.py.
"""
from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path

import bcrypt
import pytest
from starlette.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parent.parent
INTEGRATION_DB = os.environ.get("SP_INTEGRATION_DB", "").strip()
_TEST_PASSWORD = "test-password-not-real-12345"


# ── Unit-level tests on admin.queries helpers ──────────────────


class TestQueriesPureHelpers:
    """No DB required — exercise the shape-shaping logic in
    admin.queries that runs after SQL results land."""

    def test_format_confidence_shows_collision_for_zero_with_flag(self):
        from admin.queries import _format_confidence
        assert _format_confidence(0.0, is_collision=True) == "(collision)"

    def test_format_confidence_shows_numeric_for_nonzero(self):
        from admin.queries import _format_confidence
        assert _format_confidence(0.78, is_collision=False) == "0.78"
        # Even with collision flag, non-zero confidence renders as
        # numeric — the cosmetic rule is "0.0 + collision" only.
        # If a future change makes the alias-tier emit non-zero with
        # collision (e.g., when collision detection ranks candidates),
        # the operator sees the actual score.
        assert _format_confidence(0.55, is_collision=True) == "0.55"

    def test_format_confidence_zero_without_collision_flag_shows_zero(self):
        from admin.queries import _format_confidence
        # Defensive: if collision flag isn't set in reason_detail
        # but confidence is 0.0, we render "0.00" rather than
        # masking it as "(collision)". The collision rule is keyed
        # off the flag, not the value.
        assert _format_confidence(0.0, is_collision=False) == "0.00"

    def test_detect_collision_reads_home_and_away_flags(self):
        from admin.queries import _detect_collision
        assert _detect_collision({}) == (False, False)
        assert _detect_collision({"home_collision": True}) == (True, False)
        assert _detect_collision({"away_collision": True}) == (False, True)
        assert _detect_collision(
            {"home_collision": True, "away_collision": True}
        ) == (True, True)
        # Truthy-but-not-True values still count (defensive against
        # JSONB returning 1 / "true" / etc.).
        assert _detect_collision({"home_collision": 1}) == (True, False)

    def test_candidate_count_handles_null_and_lists(self):
        from admin.queries import _candidate_count
        assert _candidate_count(None) == 0
        assert _candidate_count([]) == 0
        assert _candidate_count([uuid.uuid4(), uuid.uuid4()]) == 2
        # Defensive: ints shouldn't be there (JSONB column is list)
        # but if a malformed row sneaks in, return 0 rather than 500.
        assert _candidate_count(42) == 0

    def test_parse_team_id_list_handles_uuid_strings(self):
        from admin.queries import _parse_team_id_list
        tid_a = uuid.uuid4()
        tid_b = uuid.uuid4()
        # Strings as stored in JSONB.
        out = _parse_team_id_list([str(tid_a), str(tid_b)])
        assert out == [tid_a, tid_b]

    def test_parse_team_id_list_skips_malformed_entries(self):
        from admin.queries import _parse_team_id_list
        good = uuid.uuid4()
        out = _parse_team_id_list(
            [str(good), "not-a-uuid", None, "", 42]
        )
        assert out == [good]

    def test_parse_team_id_list_empty(self):
        from admin.queries import _parse_team_id_list
        assert _parse_team_id_list(None) == []
        assert _parse_team_id_list([]) == []

    def test_review_queue_page_pagination_math(self):
        from admin.queries import ReviewQueuePage
        # 137 rows / 50 per page = 3 pages (50, 50, 37).
        p = ReviewQueuePage(
            rows=[], total=137, page=1, page_size=50,
            filter_status="pending", filter_provider=None,
            filter_sport=None, filter_confidence_min=None,
        )
        assert p.page_count == 3
        assert p.has_prev is False
        assert p.has_next is True

        p_mid = ReviewQueuePage(
            rows=[], total=137, page=2, page_size=50,
            filter_status="pending", filter_provider=None,
            filter_sport=None, filter_confidence_min=None,
        )
        assert p_mid.has_prev is True
        assert p_mid.has_next is True

        p_last = ReviewQueuePage(
            rows=[], total=137, page=3, page_size=50,
            filter_status="pending", filter_provider=None,
            filter_sport=None, filter_confidence_min=None,
        )
        assert p_last.has_prev is True
        assert p_last.has_next is False

    def test_review_queue_page_zero_total_gives_one_empty_page(self):
        from admin.queries import ReviewQueuePage
        p = ReviewQueuePage(
            rows=[], total=0, page=1, page_size=50,
            filter_status="pending", filter_provider=None,
            filter_sport=None, filter_confidence_min=None,
        )
        # Empty state isn't a divide-by-zero; render "Page 1 of 1".
        assert p.page_count == 1
        assert p.has_prev is False
        assert p.has_next is False

    def test_extract_kickoff_parses_kalshi_iso(self):
        from datetime import timezone
        from admin.queries import _extract_kickoff
        result = _extract_kickoff(
            "kalshi",
            kalshi_kickoff_iso="2026-06-15T14:30:00+00:00",
            fl_kickoff_epoch=None,
        )
        assert result is not None
        assert result.year == 2026
        assert result.month == 6
        assert result.day == 15
        assert result.hour == 14
        assert result.minute == 30
        assert result.tzinfo is not None  # tz-aware

    def test_extract_kickoff_parses_fl_epoch(self):
        from admin.queries import _extract_kickoff
        # Unix epoch 1781015400 = 2026-06-15 14:30:00 UTC.
        result = _extract_kickoff(
            "fl",
            kalshi_kickoff_iso=None,
            fl_kickoff_epoch="1781015400",
        )
        assert result is not None
        assert result.year == 2026
        assert result.month == 6
        assert result.hour == 14
        assert result.tzinfo is not None

    def test_extract_kickoff_returns_none_on_provider_mismatch(self):
        from admin.queries import _extract_kickoff
        # kalshi provider with only fl_kickoff_epoch set → None.
        # Defensive: the LEFT JOIN can match the wrong side if data
        # gets weird; the helper trusts provider, not the JOIN result.
        assert _extract_kickoff(
            "kalshi",
            kalshi_kickoff_iso=None,
            fl_kickoff_epoch="1781015400",
        ) is None

    def test_extract_kickoff_returns_none_on_malformed_input(self):
        from admin.queries import _extract_kickoff
        # Malformed ISO string → None (not 500).
        assert _extract_kickoff(
            "kalshi",
            kalshi_kickoff_iso="not-an-iso-string",
            fl_kickoff_epoch=None,
        ) is None
        # Malformed FL epoch (non-numeric) → None.
        assert _extract_kickoff(
            "fl",
            kalshi_kickoff_iso=None,
            fl_kickoff_epoch="banana",
        ) is None

    def test_extract_kickoff_returns_none_when_both_missing(self):
        from admin.queries import _extract_kickoff
        # Most common case in practice: LEFT JOIN miss on both sides.
        assert _extract_kickoff("kalshi", None, None) is None
        assert _extract_kickoff("fl", None, None) is None


# ── Route-level tests requiring no DB (auth/redirect surface) ──


@pytest.fixture
def app_with_admin(monkeypatch):
    """Same shape as the sub-PR #1 auth-test fixture. Reloads main
    with the admin env vars set so SessionMiddleware mounts."""
    test_hash = bcrypt.hashpw(_TEST_PASSWORD.encode(), bcrypt.gensalt()).decode()
    monkeypatch.setenv("OPERATOR_PASSWORD_HASH", test_hash)
    monkeypatch.setenv(
        "OPERATOR_SESSION_SECRET",
        "test-session-secret-not-real-aaaaaaaaaaaaaaaa",
    )
    # Unset DATABASE_URL so admin.router.get_db raises 503 cleanly —
    # the auth-only tests below don't need real DB; the integration
    # tests further down create their own engine.
    monkeypatch.delenv("DATABASE_URL", raising=False)

    import sys
    for mod in list(sys.modules):
        if mod == "main" or mod.startswith("main.") or mod.startswith("admin") or mod == "db":
            del sys.modules[mod]

    import main  # noqa: E402
    return TestClient(main.app)


class TestRouteAuthSurface:
    """The list + detail routes must enforce auth and DB-presence
    consistently with the rest of the admin module."""

    def test_review_queue_list_requires_auth(self, app_with_admin):
        resp = app_with_admin.get("/admin/review-queue", follow_redirects=False)
        assert resp.status_code == 401

    def test_review_queue_detail_requires_auth(self, app_with_admin):
        fake_id = uuid.uuid4()
        resp = app_with_admin.get(
            f"/admin/review-queue/{fake_id}", follow_redirects=False,
        )
        assert resp.status_code == 401

    def test_review_queue_list_returns_503_when_db_unset(self, app_with_admin):
        # Authenticate first; get_db then trips on missing DATABASE_URL.
        app_with_admin.post(
            "/admin/login",
            data={"password": _TEST_PASSWORD},
            follow_redirects=False,
        )
        resp = app_with_admin.get("/admin/review-queue", follow_redirects=False)
        assert resp.status_code == 503
        # Clear error message — operator sees this when DATABASE_URL
        # isn't set, which can happen in dev without docker-compose
        # running.
        assert "database" in resp.text.lower() or "postgres" in resp.text.lower()

    def test_review_queue_detail_returns_503_when_db_unset(self, app_with_admin):
        app_with_admin.post(
            "/admin/login",
            data={"password": _TEST_PASSWORD},
            follow_redirects=False,
        )
        fake_id = uuid.uuid4()
        resp = app_with_admin.get(
            f"/admin/review-queue/{fake_id}", follow_redirects=False,
        )
        assert resp.status_code == 503

    def test_root_redirects_to_review_queue(self, app_with_admin):
        # Authenticated GET /admin/ → 303 to /admin/review-queue
        # (whether the list view itself returns 200 or 503 depends on
        # DB state — this test only verifies the redirect target).
        app_with_admin.post(
            "/admin/login",
            data={"password": _TEST_PASSWORD},
            follow_redirects=False,
        )
        resp = app_with_admin.get("/admin/", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/admin/review-queue"

    def test_list_view_handles_empty_confidence_min(self, app_with_admin):
        # Regression guard for the 422 bug found during sub-PR #2
        # production verification: typing "Soccer" in the Sport
        # filter input and clicking Apply returned 422 because
        # confidence_min was bound as float | None — empty form
        # input becomes "" which FastAPI tried to parse as float
        # and rejected.
        #
        # Fix (PR #123 commit A): bind confidence_min as str | None
        # via Query(..., alias="confidence_min") and parse defensively
        # in the handler. Empty / malformed / out-of-range all
        # degrade to "no filter applied".
        #
        # Pre-fix: 422. Post-fix: 503 (DB unset in this fixture) or
        # 200 (DB set). Anything that isn't 422 is acceptable — the
        # point is the form doesn't reject the empty input.
        app_with_admin.post(
            "/admin/login",
            data={"password": _TEST_PASSWORD},
            follow_redirects=False,
        )
        resp = app_with_admin.get(
            "/admin/review-queue?confidence_min=&sport=Soccer",
            follow_redirects=False,
        )
        assert resp.status_code != 422, (
            f"Empty confidence_min query param should not 422; got "
            f"{resp.status_code} with body {resp.text[:200]}"
        )

    def test_list_view_ignores_malformed_confidence_min(self, app_with_admin):
        # Same defensive parse handles "xyz" or "5.0" (out of range)
        # gracefully — silently ignore the filter rather than 422.
        app_with_admin.post(
            "/admin/login",
            data={"password": _TEST_PASSWORD},
            follow_redirects=False,
        )
        for bad_value in ("xyz", "5.0", "-1.0", "1.5"):
            resp = app_with_admin.get(
                f"/admin/review-queue?confidence_min={bad_value}",
                follow_redirects=False,
            )
            assert resp.status_code != 422, (
                f"confidence_min={bad_value!r} returned 422; defensive "
                f"parse should silently ignore."
            )

    def test_static_css_is_served(self, app_with_admin):
        # Phase 2F.1 CSS extraction (issue #120): admin styles
        # consolidated into admin/static/admin.css, linked from
        # base.html. The /admin/static mount in main.py serves it.
        # Regression guard against:
        #   - accidentally removing the StaticFiles mount
        #   - moving or renaming admin.css
        #   - removing the <link> tag from base.html
        # Static files don't require auth (the CSS is non-sensitive
        # and browsers prefetch via <link>, not via cookie).
        resp = app_with_admin.get("/admin/static/admin.css")
        assert resp.status_code == 200, (
            f"GET /admin/static/admin.css returned {resp.status_code}. "
            f"Either the StaticFiles mount is missing in main.py, the "
            f"admin/static/ directory doesn't exist, or admin.css was "
            f"renamed/moved."
        )
        content_type = resp.headers.get("content-type", "")
        assert content_type.startswith("text/css"), (
            f"admin.css served with wrong content-type: {content_type!r}"
        )
        # Spot-check the file contents — confirms the right file is
        # served (not an empty placeholder or a misrouted response).
        assert ":root" in resp.text, "admin.css missing CSS token block"
        assert "--accent" in resp.text, "admin.css missing --accent token"


# ── Integration tests against a real Postgres ──────────────────


@pytest.mark.skipif(
    not INTEGRATION_DB,
    reason=(
        "SP_INTEGRATION_DB not set — integration tests require a "
        "Postgres URL with the sp schema migrations applied through "
        "Phase 2F.0 (revision a1c4f9e8b2d7)."
    ),
)
class TestReviewQueueIntegration:
    """End-to-end SQL roundtrip: seed rows, hit the routes, assert
    rendered HTML carries the expected fields. Mirrors the
    test_phase_2f05_runner_writeside.py pattern."""

    @pytest.fixture
    def engine(self):
        from sqlalchemy import create_engine
        url = INTEGRATION_DB
        if "+asyncpg" in url:
            url = url.replace("+asyncpg", "")
        eng = create_engine(url)
        yield eng
        eng.dispose()

    @pytest.fixture(autouse=True)
    def setup_schema(self, engine):
        # Apply migration to head + clean leftover test rows.
        result = subprocess.run(
            ["alembic", "upgrade", "head"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            env={**os.environ, "DATABASE_URL": INTEGRATION_DB},
        )
        assert result.returncode == 0, (
            f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}"
        )

        from sqlalchemy import text
        with engine.begin() as conn:
            conn.execute(text(
                "DELETE FROM sp.resolution_log "
                "WHERE provider_record_id LIKE 'TEST-2F1-%'"
            ))
            conn.execute(text(
                "DELETE FROM sp.review_queue "
                "WHERE provider_record_id LIKE 'TEST-2F1-%'"
            ))
        yield
        with engine.begin() as conn:
            conn.execute(text(
                "DELETE FROM sp.resolution_log "
                "WHERE provider_record_id LIKE 'TEST-2F1-%'"
            ))
            conn.execute(text(
                "DELETE FROM sp.review_queue "
                "WHERE provider_record_id LIKE 'TEST-2F1-%'"
            ))

    @pytest.fixture
    def app(self, monkeypatch, engine):
        # Configure the admin env vars + DATABASE_URL so the route
        # handlers don't 503.
        test_hash = bcrypt.hashpw(_TEST_PASSWORD.encode(), bcrypt.gensalt()).decode()
        monkeypatch.setenv("OPERATOR_PASSWORD_HASH", test_hash)
        monkeypatch.setenv(
            "OPERATOR_SESSION_SECRET",
            "test-session-secret-not-real-aaaaaaaaaaaaaaaa",
        )
        monkeypatch.setenv("DATABASE_URL", INTEGRATION_DB)

        import sys
        for mod in list(sys.modules):
            if mod == "main" or mod.startswith("main.") or mod.startswith("admin") or mod == "db":
                del sys.modules[mod]

        import main  # noqa: E402
        client = TestClient(main.app)
        # Authenticate once; the session cookie sticks for follow-up
        # requests in the same test.
        client.post(
            "/admin/login",
            data={"password": _TEST_PASSWORD},
            follow_redirects=False,
        )
        return client

    def _seed_pending_row(
        self, engine, *,
        ticker: str,
        provider: str = "kalshi",
        confidence: float = 0.78,
        provider_title: str = "Test vs Other",
        reason_detail: dict | None = None,
        also_seed_provider_row: bool = False,
        kickoff_iso: str | None = None,
    ):
        """Seed a sp.review_queue row. When also_seed_provider_row is
        True, additionally seed a matching sp.kalshi_markets or
        sp.fl_events row carrying the kickoff timestamp — exercises
        the LEFT JOIN path in queries.py.
        """
        from sqlalchemy import text
        rd = reason_detail or {
            "sport": "Tennis",
            "fail_reason": "below_threshold",
            "home_canonical": "Test Home",
            "away_canonical": "Test Away",
        }
        with engine.begin() as conn:
            conn.execute(text(
                """
                INSERT INTO sp.review_queue
                  (id, provider, provider_record_id, candidate_fixtures,
                   confidence, reason_detail, provider_title,
                   status, created_at)
                VALUES
                  (gen_random_uuid(), :provider, :pk,
                   CAST(:cands AS jsonb), :conf,
                   CAST(:rd AS jsonb), :title,
                   'pending', NOW())
                """
            ).bindparams(
                provider=provider,
                pk=ticker,
                cands=json.dumps([str(uuid.uuid4())]),
                conf=confidence,
                rd=json.dumps(rd),
                title=provider_title,
            ))
            if also_seed_provider_row:
                # Seed the matching provider table row carrying the
                # kickoff timestamp. The LEFT JOIN in queries.py picks
                # it up. raw_payload uses the provider-native key:
                # kalshi stores ISO string under '_kickoff_dt', fl
                # stores Unix epoch under 'START_TIME'.
                if provider == "kalshi":
                    payload = {
                        "title": provider_title,
                        "_kickoff_dt": kickoff_iso or "2026-06-15T14:30:00+00:00",
                    }
                    conn.execute(text(
                        """
                        INSERT INTO sp.kalshi_markets
                          (ticker, market_type, raw_payload, last_seen_at,
                           last_changed_at, payload_hash)
                        VALUES (:ticker, 'game', CAST(:payload AS jsonb),
                                NOW(), NOW(), 'test-hash-placeholder')
                        ON CONFLICT (ticker) DO NOTHING
                        """
                    ).bindparams(ticker=ticker, payload=json.dumps(payload)))
                else:  # fl
                    payload = {
                        "EVENT_ID": ticker,
                        "HOME_NAME": "Test Home",
                        "AWAY_NAME": "Test Away",
                        # Unix epoch for 2026-06-15 14:30 UTC.
                        "START_TIME": 1781015400,
                    }
                    conn.execute(text(
                        """
                        INSERT INTO sp.fl_events
                          (fl_event_id, raw_payload, last_seen_at,
                           last_changed_at, payload_hash)
                        VALUES (:fl_event_id, CAST(:payload AS jsonb),
                                NOW(), NOW(), 'test-hash-placeholder')
                        ON CONFLICT (fl_event_id) DO NOTHING
                        """
                    ).bindparams(fl_event_id=ticker, payload=json.dumps(payload)))

    def test_list_view_renders_seeded_rows(self, app, engine):
        self._seed_pending_row(engine, ticker="TEST-2F1-LIST-001")
        self._seed_pending_row(
            engine, ticker="TEST-2F1-LIST-002", confidence=0.82,
            provider_title="Higher Confidence Match",
        )

        resp = app.get("/admin/review-queue?status=pending")
        assert resp.status_code == 200
        # Both seeded rows should appear in the list.
        assert "TEST-2F1-LIST-001" in resp.text
        assert "TEST-2F1-LIST-002" in resp.text
        # Provider title snapshots render.
        assert "Higher Confidence Match" in resp.text
        # Confidence sort: 0.82 row should appear before 0.78 row.
        pos_high = resp.text.find("TEST-2F1-LIST-002")
        pos_low = resp.text.find("TEST-2F1-LIST-001")
        assert pos_high < pos_low, (
            "Sort order should be confidence DESC; higher-confidence "
            "row should appear earlier in the HTML."
        )
        # Sport column populated from reason_detail snapshot.
        assert "Tennis" in resp.text
        # All 10 columns render in the header.
        for header in ("Provider", "Ticker", "Title", "Sport",
                       "Kickoff (UTC)", "Confidence", "Tier",
                       "Candidates", "Status", "Created"):
            assert f"<th>{header}</th>" in resp.text, (
                f"List view missing <th>{header}</th> column header."
            )

    def test_list_view_renders_kickoff_via_provider_join(self, app, engine):
        """Kickoff column comes from a LEFT JOIN to sp.kalshi_markets
        or sp.fl_events — the runner snapshots reason_detail / title
        but NOT kickoff into review_queue, so the JOIN is the source.
        """
        # Kalshi row with a matching kalshi_markets entry → kickoff
        # renders as "2026-06-15 14:30".
        self._seed_pending_row(
            engine,
            ticker="TEST-2F1-KICKOFF-KALSHI",
            provider="kalshi",
            also_seed_provider_row=True,
            kickoff_iso="2026-06-15T14:30:00+00:00",
        )
        # FL row with a matching fl_events entry → kickoff renders
        # (epoch 1781015400 = 2026-06-15 14:30 UTC).
        self._seed_pending_row(
            engine,
            ticker="TEST-2F1-KICKOFF-FL",
            provider="fl",
            also_seed_provider_row=True,
        )
        # Kalshi row WITHOUT matching provider row → kickoff renders
        # as "(unknown)" (LEFT JOIN miss; helper returns None).
        self._seed_pending_row(
            engine,
            ticker="TEST-2F1-KICKOFF-MISSING",
            provider="kalshi",
            also_seed_provider_row=False,
        )

        resp = app.get("/admin/review-queue?status=pending")
        assert resp.status_code == 200
        # Both seeded kickoff timestamps render.
        assert "2026-06-15 14:30" in resp.text
        # The missing-provider-row case shows the "(unknown)" fallback.
        assert "(unknown)" in resp.text

    def test_list_view_filters_by_provider(self, app, engine):
        self._seed_pending_row(engine, ticker="TEST-2F1-FILTER-KALSHI", provider="kalshi")
        self._seed_pending_row(engine, ticker="TEST-2F1-FILTER-FL", provider="fl")

        # Provider filter on kalshi → only the kalshi row.
        resp = app.get("/admin/review-queue?provider=kalshi")
        assert resp.status_code == 200
        assert "TEST-2F1-FILTER-KALSHI" in resp.text
        assert "TEST-2F1-FILTER-FL" not in resp.text

    def test_detail_view_renders_seeded_row(self, app, engine):
        from sqlalchemy import text
        ticker = "TEST-2F1-DETAIL-001"
        self._seed_pending_row(
            engine, ticker=ticker,
            provider_title="Detail View Test Title",
        )
        with engine.begin() as conn:
            record_id = conn.execute(text(
                "SELECT id FROM sp.review_queue WHERE provider_record_id = :pk"
            ).bindparams(pk=ticker)).scalar()

        resp = app.get(f"/admin/review-queue/{record_id}")
        assert resp.status_code == 200
        assert ticker in resp.text
        assert "Detail View Test Title" in resp.text
        # The matcher's fail_reason renders.
        assert "below_threshold" in resp.text

    def test_detail_view_404_for_unknown_uuid(self, app):
        unknown = uuid.uuid4()
        resp = app.get(f"/admin/review-queue/{unknown}")
        assert resp.status_code == 404

    def test_detail_view_collision_renders_team_names(self, app, engine):
        """The Q6 design lock — colliding team_ids JOIN to sp.teams
        and render canonical names. Without this JOIN, operators see
        opaque UUIDs."""
        from sqlalchemy import text
        # Find any two existing teams to use as the collision set.
        # (Using real teams keeps the JOIN test meaningful — fake
        # team_ids would just hit the "(team not found)" fallback.)
        with engine.begin() as conn:
            existing = conn.execute(text(
                "SELECT id, canonical_name FROM sp.teams LIMIT 2"
            )).all()
        if len(existing) < 2:
            pytest.skip("integration DB has fewer than 2 sp.teams rows")

        team_a, team_b = existing[0], existing[1]
        ticker = "TEST-2F1-COLLISION-001"
        self._seed_pending_row(
            engine, ticker=ticker, confidence=0.0,
            reason_detail={
                "sport": "Tennis",
                "fail_reason": "alias_collision",
                "home_collision": True,
                "colliding_home_team_ids": [str(team_a.id), str(team_b.id)],
                "home_canonical": "Collision Home",
                "away_canonical": "Solo Away",
            },
        )

        with engine.begin() as conn:
            record_id = conn.execute(text(
                "SELECT id FROM sp.review_queue WHERE provider_record_id = :pk"
            ).bindparams(pk=ticker)).scalar()

        resp = app.get(f"/admin/review-queue/{record_id}")
        assert resp.status_code == 200
        # Both candidate team names render (Q6 JOIN-in-handler check).
        assert team_a.canonical_name in resp.text
        assert team_b.canonical_name in resp.text
        # Collision-style confidence display.
        assert "(collision)" in resp.text
