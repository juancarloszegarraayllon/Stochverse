"""Phase 2F.1 sub-PR #4 — anchor_failed surface tests.

The surface is read-only: list view + detail view + "Suggest alias"
clipboard widget. No mutations on the route side. Tests cover:

  - Fail-reason family enumeration (exactly the 4 expected; carve-out
    for ingestion-level failures is asserted by NOT-including them).
  - DISTINCT ON query semantics (one row per provider_record_id even
    when the record failed across multiple cron cycles; latest wins).
  - Run-window cap (records older than LIMIT 7 most-recent
    resolver_runs don't appear).
  - LEFT JOIN to provider tables for title recovery.
  - Detail-view 404 on unknown keys.
  - Suggested-alias data shape (parsed_name + top-N closest teams).
  - Static guard: no POST routes under /admin/anchor-failed/ (the
    surface is structurally read-only).

Same two-layer pattern as the other 2F.1 tests:
  - TestAnchorFailedUnit: no DB, pure helpers + constants.
  - TestAnchorFailedIntegration: real Postgres via SP_INTEGRATION_DB.
"""
from __future__ import annotations

import os
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bcrypt
import pytest
from starlette.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parent.parent
INTEGRATION_DB = os.environ.get("SP_INTEGRATION_DB", "").strip()
_TEST_PASSWORD = "test-password-not-real-12345"
_TEST_MARKER = "TEST-2F1-SUB4-AF"


# ── Unit-level tests ───────────────────────────────────────────


class TestAnchorFailedUnit:
    """No DB. Constants, fail-reason gloss, title-recovery helper."""

    def test_fail_reason_family_is_exactly_four_terminal_states(self):
        from admin.queries import ANCHOR_FAILED_FAIL_REASONS
        # Per PHASE_2F_DESIGN.md rev1.2 §Q6 — the four terminal anchor-
        # failed fail_reasons. Adding to or removing from this set
        # requires updating the design doc in lockstep.
        assert set(ANCHOR_FAILED_FAIL_REASONS) == {
            "alias_no_team_resemblance",
            "fuzzy_no_team_resemblance",
            "alias_no_existing_fixture",
            "fuzzy_no_existing_fixture",
        }

    def test_ingestion_failures_are_not_in_anchor_failed_family(self):
        from admin.queries import ANCHOR_FAILED_FAIL_REASONS
        # Carve-out from rev1.2: ingestion-level failures belong to a
        # separate (future) operator-visibility bucket. Explicitly
        # assert here so future edits to the family list don't
        # silently absorb them.
        ingestion_failures = {
            "sport_not_classified",
            "kickoff_at_missing",
            "structural_normalize_failed",
            "home_and_away_same_team",
            "kalshi_competition_unresolvable",
            "alias_resolution_incomplete",
            "kickoff_confidence_below_threshold",
        }
        assert not (ingestion_failures & set(ANCHOR_FAILED_FAIL_REASONS))

    def test_recent_runs_limit_is_seven(self):
        from admin.queries import ANCHOR_FAILED_RECENT_RUNS
        # ~2-3 days at current cron cadence. Documented in the design
        # doc and PR #133 conversation. Change requires updating the
        # PR template's What-this-PR-is-NOT section ("historical audit
        # tool" carve-out).
        assert ANCHOR_FAILED_RECENT_RUNS == 7

    def test_format_fail_reason_returns_gloss_for_known(self):
        from admin.queries import _format_fail_reason
        gloss = _format_fail_reason("alias_no_team_resemblance")
        assert "no team name matched" in gloss

    def test_format_fail_reason_falls_back_to_raw_for_unknown(self):
        from admin.queries import _format_fail_reason
        assert _format_fail_reason("not_a_real_fail_reason") == "not_a_real_fail_reason"

    def test_anchor_failed_title_kalshi_uses_title_field(self):
        from admin.queries import _anchor_failed_title
        assert _anchor_failed_title(
            provider="kalshi", kalshi_title="Sinner vs Alcaraz",
            fl_home_name=None, fl_away_name=None,
        ) == "Sinner vs Alcaraz"

    def test_anchor_failed_title_fl_synthesizes_from_home_away(self):
        from admin.queries import _anchor_failed_title
        assert _anchor_failed_title(
            provider="fl", kalshi_title=None,
            fl_home_name="Real Madrid", fl_away_name="Barcelona",
        ) == "Real Madrid vs Barcelona"

    def test_anchor_failed_title_returns_none_when_no_data(self):
        from admin.queries import _anchor_failed_title
        assert _anchor_failed_title(
            provider="kalshi", kalshi_title=None,
            fl_home_name=None, fl_away_name=None,
        ) is None


# ── Static guard: no POST routes under /admin/anchor-failed/ ────


class TestAnchorFailedStaticGuard:
    """The anchor_failed surface is read-only by design (rev1.2 §Q6
    — no approve/reject because there are no candidates to approve
    against). This test asserts that structurally — no POST handler
    can sneak into the surface via routine module edits."""

    def test_no_post_routes_under_anchor_failed_path(self):
        # Import lazily so module-level env-var requirements don't
        # break the static guard's runnability.
        os.environ.setdefault("OPERATOR_PASSWORD_HASH",
                              "$2b$12$" + "x" * 53)
        os.environ.setdefault("OPERATOR_SESSION_SECRET",
                              "static-guard-secret-not-real-aaaaa")
        from admin.router import router
        offending = []
        for route in router.routes:
            path = getattr(route, "path", "")
            methods = getattr(route, "methods", set()) or set()
            if "/anchor-failed" in path and methods - {"GET", "HEAD"}:
                offending.append((path, sorted(methods)))
        assert not offending, (
            f"Mutating routes found under /admin/anchor-failed/: "
            f"{offending}. The surface is read-only by design "
            f"(PHASE_2F_DESIGN rev1.2 §Q6)."
        )


# ── Integration tests (require SP_INTEGRATION_DB) ──────────────


@pytest.mark.skipif(
    not INTEGRATION_DB,
    reason=(
        "SP_INTEGRATION_DB not set — anchor_failed integration tests "
        "require a Postgres URL with sp schema migrations applied."
    ),
)
class TestAnchorFailedIntegration:
    """End-to-end: seed resolver_runs + resolution_log rows → GET
    /admin/anchor-failed → assert shaped response."""

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
        self._purge_test_data(engine)
        yield
        self._purge_test_data(engine)

    def _purge_test_data(self, engine):
        # Schema notes:
        #   sp.resolver_runs.id is BIGINT autoincrement; the UUID
        #   linkage to sp.resolution_log is `run_id`. Resolver_runs
        #   has no `notes` column — we tag test rows via the JSONB
        #   `extra` field so purge can find them on the next run.
        from sqlalchemy import text
        with engine.begin() as conn:
            conn.execute(text(
                "DELETE FROM sp.resolution_log "
                "WHERE provider_record_id LIKE :marker"
            ), {"marker": f"{_TEST_MARKER}%"})
            conn.execute(text(
                "DELETE FROM sp.resolver_runs "
                "WHERE extra->>'test_marker' = :marker"
            ), {"marker": _TEST_MARKER})
            conn.execute(text(
                "DELETE FROM sp.kalshi_markets WHERE ticker LIKE :marker"
            ), {"marker": f"{_TEST_MARKER}%"})

    def _seed_run(self, engine, started_at: datetime) -> uuid.UUID:
        """Insert one sp.resolver_runs row, return its run_id UUID.

        Schema gotcha: sp.resolver_runs.id is BIGINT (autoincrement);
        the UUID linkage to sp.resolution_log.run_id is the `run_id`
        column. The anchor_failed query joins on run_id, not id.
        """
        import json
        from sqlalchemy import text
        run_id = uuid.uuid4()
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO sp.resolver_runs "
                "(run_id, provider, run_mode, started_at, finished_at, "
                " resolver_version, records_scanned, auto_applies, "
                " no_match, crashes, extra) "
                "VALUES (:run_id, 'kalshi', 'test', :started, :finished, "
                "        'tiered@2d.0', 0, 0, 0, 0, CAST(:extra AS jsonb))"
            ), {
                "run_id": run_id,
                "started": started_at,
                "finished": started_at + timedelta(minutes=5),
                "extra": json.dumps({"test_marker": _TEST_MARKER}),
            })
        return run_id

    def _seed_log(
        self, engine, *, run_id: uuid.UUID, pk: str,
        fail_reason: str, sport: str | None = "Tennis",
        provider_home: str = "Sinner J.",
        provider_away: str = "Alcaraz C.",
        decided_at: datetime | None = None,
    ):
        """Insert one sp.resolution_log row."""
        import json
        from sqlalchemy import text
        if decided_at is None:
            decided_at = datetime.now(timezone.utc)
        reason_detail = {
            "fail_reason": fail_reason,
            "sport": sport,
            "home_provider_normalized": provider_home,
            "away_provider_normalized": provider_away,
        }
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO sp.resolution_log "
                "(run_id, provider, provider_record_id, fixture_id, "
                " confidence, reason_code, reason_detail, "
                " resolver_version, decided_at) "
                "VALUES (:run_id, 'kalshi', :pk, NULL, 0.0, "
                "        'no_match', CAST(:rd AS jsonb), "
                "        'alias@2c.0', :decided_at)"
            ), {
                "run_id": run_id, "pk": pk,
                "rd": json.dumps(reason_detail),
                "decided_at": decided_at,
            })

    def _seed_kalshi_payload(self, engine, *, ticker: str, title: str):
        """Insert one sp.kalshi_markets row so the LEFT JOIN finds a
        title for the detail view. Schema requires market_type +
        payload_hash + last_seen_at / last_changed_at (no fetched_at;
        the column was renamed)."""
        import hashlib
        import json
        from sqlalchemy import text
        payload_json = json.dumps({
            "title": title,
            "_kickoff_dt": "2026-06-01T12:00:00+00:00",
        })
        payload_hash = hashlib.sha256(payload_json.encode()).hexdigest()
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO sp.kalshi_markets "
                "(ticker, market_type, raw_payload, payload_hash, "
                " last_seen_at, last_changed_at) "
                "VALUES (:ticker, 'event', CAST(:payload AS jsonb), "
                "        :payload_hash, NOW(), NOW())"
            ), {
                "ticker": ticker,
                "payload": payload_json,
                "payload_hash": payload_hash,
            })

    @pytest.fixture
    def app(self, monkeypatch, engine):
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
        client.post(
            "/admin/login",
            data={"password": _TEST_PASSWORD},
            follow_redirects=False,
        )
        yield client

    def test_list_returns_only_anchor_failed_family(self, engine, app):
        run_id = self._seed_run(engine, datetime.now(timezone.utc))
        # Two target rows + two non-target rows.
        self._seed_log(engine, run_id=run_id,
                       pk=f"{_TEST_MARKER}-IN-1",
                       fail_reason="alias_no_team_resemblance")
        self._seed_log(engine, run_id=run_id,
                       pk=f"{_TEST_MARKER}-IN-2",
                       fail_reason="fuzzy_no_team_resemblance")
        # Non-target — must NOT appear.
        self._seed_log(engine, run_id=run_id,
                       pk=f"{_TEST_MARKER}-OUT-1",
                       fail_reason="sport_not_classified")
        self._seed_log(engine, run_id=run_id,
                       pk=f"{_TEST_MARKER}-OUT-2",
                       fail_reason="kickoff_at_missing")

        resp = app.get("/admin/anchor-failed")
        assert resp.status_code == 200
        body = resp.text
        assert f"{_TEST_MARKER}-IN-1" in body
        assert f"{_TEST_MARKER}-IN-2" in body
        assert f"{_TEST_MARKER}-OUT-1" not in body
        assert f"{_TEST_MARKER}-OUT-2" not in body

    def test_list_distinct_on_collapses_multi_run_recurrence(self, engine, app):
        """If the same (provider, provider_record_id) failed in
        multiple cron cycles within the window, list shows ONE row."""
        from sqlalchemy import text
        now = datetime.now(timezone.utc)
        run_old = self._seed_run(engine, now - timedelta(hours=6))
        run_new = self._seed_run(engine, now)
        pk = f"{_TEST_MARKER}-RECUR"
        self._seed_log(engine, run_id=run_old, pk=pk,
                       fail_reason="alias_no_team_resemblance",
                       decided_at=now - timedelta(hours=6))
        self._seed_log(engine, run_id=run_new, pk=pk,
                       fail_reason="fuzzy_no_team_resemblance",
                       decided_at=now)

        resp = app.get("/admin/anchor-failed")
        assert resp.status_code == 200
        # DISTINCT ON ... ORDER BY id DESC takes the higher id (the
        # later insert). Body should show fuzzy_no_team_resemblance,
        # not alias_no_team_resemblance, for this pk.
        body = resp.text
        # Slice the row containing our marker so we don't false-match
        # off another row's fail_reason rendering.
        row_start = body.find(pk)
        assert row_start != -1
        row_end = body.find("</tr>", row_start)
        row_html = body[row_start:row_end]
        assert "fuzzy_no_team_resemblance" in row_html
        assert "alias_no_team_resemblance" not in row_html

    def test_list_excludes_runs_outside_window(self, engine, app):
        """Anchor-failed records older than the LIMIT 7 most-recent
        resolver_runs are excluded. Seed 8+ newer runs that come AFTER
        an old run, then assert the old run's record doesn't appear."""
        now = datetime.now(timezone.utc)
        # The old run we want excluded.
        run_excluded = self._seed_run(engine, now - timedelta(days=30))
        pk_excluded = f"{_TEST_MARKER}-OLD"
        self._seed_log(engine, run_id=run_excluded, pk=pk_excluded,
                       fail_reason="alias_no_team_resemblance",
                       decided_at=now - timedelta(days=30))

        # Eight newer runs to push the old one outside the LIMIT 7.
        for i in range(8):
            self._seed_run(engine, now - timedelta(hours=i))

        resp = app.get("/admin/anchor-failed")
        assert resp.status_code == 200
        assert pk_excluded not in resp.text

    def test_list_joins_kalshi_title(self, engine, app):
        run_id = self._seed_run(engine, datetime.now(timezone.utc))
        pk = f"{_TEST_MARKER}-WITHTITLE"
        self._seed_kalshi_payload(
            engine, ticker=pk, title="Sinner vs Alcaraz — Roland Garros",
        )
        self._seed_log(engine, run_id=run_id, pk=pk,
                       fail_reason="alias_no_team_resemblance")
        resp = app.get("/admin/anchor-failed")
        assert resp.status_code == 200
        assert "Sinner vs Alcaraz — Roland Garros" in resp.text

    def test_detail_200s_on_existing_key(self, engine, app):
        run_id = self._seed_run(engine, datetime.now(timezone.utc))
        pk = f"{_TEST_MARKER}-DETAIL-OK"
        self._seed_kalshi_payload(engine, ticker=pk, title="Test Match")
        self._seed_log(engine, run_id=run_id, pk=pk,
                       fail_reason="alias_no_team_resemblance")
        resp = app.get(f"/admin/anchor-failed/kalshi/{pk}")
        assert resp.status_code == 200
        assert "Test Match" in resp.text
        # Suggest-alias widget should be rendered (parsed_name present).
        assert "Sinner J." in resp.text or "Alcaraz C." in resp.text

    def test_detail_404s_on_unknown_key(self, engine, app):
        resp = app.get(
            f"/admin/anchor-failed/kalshi/{_TEST_MARKER}-DOESNT-EXIST",
        )
        assert resp.status_code == 404

    def test_filter_provider_narrows_results(self, engine, app):
        run_id = self._seed_run(engine, datetime.now(timezone.utc))
        # Seed one kalshi + one fl row.
        self._seed_log(engine, run_id=run_id,
                       pk=f"{_TEST_MARKER}-KALSHI",
                       fail_reason="alias_no_team_resemblance")
        # FL row via direct insert (helper assumes kalshi).
        import json
        from sqlalchemy import text
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO sp.resolution_log "
                "(run_id, provider, provider_record_id, fixture_id, "
                " confidence, reason_code, reason_detail, "
                " resolver_version, decided_at) "
                "VALUES (:run_id, 'fl', :pk, NULL, 0.0, "
                "        'no_match', CAST(:rd AS jsonb), "
                "        'fuzzy@2d.0', NOW())"
            ), {
                "run_id": run_id, "pk": f"{_TEST_MARKER}-FL",
                "rd": json.dumps({
                    "fail_reason": "fuzzy_no_team_resemblance",
                    "sport": "Tennis",
                }),
            })

        resp = app.get("/admin/anchor-failed?provider=kalshi")
        assert resp.status_code == 200
        assert f"{_TEST_MARKER}-KALSHI" in resp.text
        assert f"{_TEST_MARKER}-FL" not in resp.text
