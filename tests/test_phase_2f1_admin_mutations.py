"""Phase 2F.1 sub-PR #3 — approve/reject mutation tests.

Mutating endpoints are the most consequential of the four sub-PRs:
they write to production data through operator action. The user
asked for extra paranoia on tests — 9 specific scenarios called out
in the sub-PR #3 hand-off:

  1. Idempotency (double-click approve = no-op)
  2. Concurrency (two sessions racing on same record)
  3. Partial-failure (transaction rollback if any step fails)
  4. Audit trail (reviewed_by + reviewed_at populated)
  5. Team_aliases write-back (source='operator_review')
  6. Rejection_count increment
  7. Collision case validation (submitted team_ids must be in
     collision sets)
  8. HX-Request fragment (different response per header)
  9. No-JS fallback (form POST + redirect path)

Two layers, same as the other 2F.1 test files:
  - TestMutationHelpersUnit: no DB, pure validation logic.
  - TestMutationIntegration: real Postgres roundtrip via
    SP_INTEGRATION_DB.

The integration tests cover each numbered scenario above.
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


# ── Unit-level tests on the validation helpers ─────────────────


class TestMutationHelpersUnit:
    """No DB. Exercise _validate_candidate_team_id + ApprovalError."""

    def test_approval_error_carries_status_code(self):
        from admin.queries import ApprovalError
        e = ApprovalError("test message", status_code=409)
        assert e.message == "test message"
        assert e.status_code == 409
        # Default status code is 400.
        e_default = ApprovalError("bad request")
        assert e_default.status_code == 400

    def test_validate_accepts_id_in_collision_set(self):
        from admin.queries import _validate_candidate_team_id
        tid_a = uuid.uuid4()
        tid_b = uuid.uuid4()
        # No raise = pass.
        _validate_candidate_team_id(
            tid_a,
            side_collision=True,
            side_colliding_ids=[tid_a, tid_b],
            side_default_id=None,
            side_label="home",
        )

    def test_validate_rejects_id_not_in_collision_set(self):
        from admin.queries import _validate_candidate_team_id, ApprovalError
        in_set = uuid.uuid4()
        out_of_set = uuid.uuid4()
        with pytest.raises(ApprovalError) as exc_info:
            _validate_candidate_team_id(
                out_of_set,
                side_collision=True,
                side_colliding_ids=[in_set],
                side_default_id=None,
                side_label="home",
            )
        assert "collision set" in exc_info.value.message
        assert exc_info.value.status_code == 400

    def test_validate_accepts_matching_default_when_no_collision(self):
        from admin.queries import _validate_candidate_team_id
        tid = uuid.uuid4()
        _validate_candidate_team_id(
            tid,
            side_collision=False,
            side_colliding_ids=[],
            side_default_id=tid,
            side_label="away",
        )

    def test_validate_rejects_mismatched_default_when_no_collision(self):
        from admin.queries import _validate_candidate_team_id, ApprovalError
        default = uuid.uuid4()
        submitted = uuid.uuid4()
        with pytest.raises(ApprovalError) as exc_info:
            _validate_candidate_team_id(
                submitted,
                side_collision=False,
                side_colliding_ids=[],
                side_default_id=default,
                side_label="away",
            )
        assert "doesn't match" in exc_info.value.message


# ── Integration tests (require SP_INTEGRATION_DB) ──────────────


@pytest.mark.skipif(
    not INTEGRATION_DB,
    reason=(
        "SP_INTEGRATION_DB not set — mutation integration tests "
        "require a Postgres URL with sp schema migrations applied "
        "through Phase 2F.0 (revision a1c4f9e8b2d7)."
    ),
)
class TestMutationIntegration:
    """End-to-end: seed pending row → POST approve/reject → assert
    persisted state matches expectations. Each of the user's 9
    numbered paranoia scenarios gets a dedicated test."""

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
        from sqlalchemy import text
        # Apply migration to head.
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
        # Clean leftover test rows. Cascade through all the tables
        # this test touches.
        with engine.begin() as conn:
            conn.execute(text(
                "DELETE FROM sp.team_aliases "
                "WHERE source = 'operator_review' AND alias LIKE 'TEST-2F1-MUT-%'"
            ))
            conn.execute(text(
                "DELETE FROM sp.review_queue "
                "WHERE provider_record_id LIKE 'TEST-2F1-MUT-%'"
            ))
            conn.execute(text(
                "DELETE FROM sp.kalshi_markets "
                "WHERE ticker LIKE 'TEST-2F1-MUT-%'"
            ))
            conn.execute(text(
                "DELETE FROM sp.fl_events "
                "WHERE fl_event_id LIKE 'TEST-2F1-MUT-%'"
            ))
        yield
        with engine.begin() as conn:
            conn.execute(text(
                "DELETE FROM sp.team_aliases "
                "WHERE source = 'operator_review' AND alias LIKE 'TEST-2F1-MUT-%'"
            ))
            conn.execute(text(
                "DELETE FROM sp.review_queue "
                "WHERE provider_record_id LIKE 'TEST-2F1-MUT-%'"
            ))
            conn.execute(text(
                "DELETE FROM sp.kalshi_markets "
                "WHERE ticker LIKE 'TEST-2F1-MUT-%'"
            ))
            conn.execute(text(
                "DELETE FROM sp.fl_events "
                "WHERE fl_event_id LIKE 'TEST-2F1-MUT-%'"
            ))

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
        return client

    def _seed_non_collision_pending_row(self, engine, ticker, home_tid, away_tid):
        """Seed a pending review_queue row + matching kalshi_markets
        row with kickoff. Used for happy-path tests.
        """
        from sqlalchemy import text
        with engine.begin() as conn:
            conn.execute(text(
                """
                INSERT INTO sp.kalshi_markets
                  (ticker, market_type, raw_payload, last_seen_at,
                   last_changed_at, payload_hash)
                VALUES
                  (:ticker, 'game',
                   CAST(:payload AS jsonb), NOW(), NOW(), 'test-hash')
                ON CONFLICT (ticker) DO NOTHING
                """
            ).bindparams(
                ticker=ticker,
                payload=json.dumps({
                    "title": "Test Home vs Test Away",
                    "_kickoff_dt": "2026-06-15T14:30:00+00:00",
                }),
            ))
            reason_detail = {
                "sport": "Tennis",
                "fail_reason": "below_threshold",
                "home_canonical": "Test Home Canonical",
                "away_canonical": "Test Away Canonical",
                "home_team_id": str(home_tid),
                "away_team_id": str(away_tid),
            }
            conn.execute(text(
                """
                INSERT INTO sp.review_queue
                  (id, provider, provider_record_id, candidate_fixtures,
                   confidence, reason_detail, provider_title,
                   status, created_at)
                VALUES
                  (gen_random_uuid(), 'kalshi', :pk,
                   CAST(:cands AS jsonb), 0.78,
                   CAST(:rd AS jsonb), 'Test Home vs Test Away',
                   'pending', NOW())
                """
            ).bindparams(
                pk=ticker,
                cands=json.dumps([str(home_tid), str(away_tid)]),
                rd=json.dumps(reason_detail),
            ))
            record_id = conn.execute(text(
                "SELECT id FROM sp.review_queue WHERE provider_record_id = :pk"
            ).bindparams(pk=ticker)).scalar()
        return record_id

    def _seed_collision_pending_row(
        self, engine, ticker, colliding_home_ids, colliding_away_ids,
    ):
        """Seed a pending review_queue row representing a home AND
        away collision case. Both sides have multiple candidate
        team_ids."""
        from sqlalchemy import text
        with engine.begin() as conn:
            conn.execute(text(
                """
                INSERT INTO sp.kalshi_markets
                  (ticker, market_type, raw_payload, last_seen_at,
                   last_changed_at, payload_hash)
                VALUES
                  (:ticker, 'game',
                   CAST(:payload AS jsonb), NOW(), NOW(), 'test-hash')
                ON CONFLICT (ticker) DO NOTHING
                """
            ).bindparams(
                ticker=ticker,
                payload=json.dumps({
                    "title": "Coll Home vs Coll Away",
                    "_kickoff_dt": "2026-06-15T14:30:00+00:00",
                }),
            ))
            # Matcher's "best guess" pre-fills home_team_id /
            # away_team_id with the first colliding candidate; the
            # form's radio buttons default to those.
            reason_detail = {
                "sport": "Tennis",
                "fail_reason": "alias_collision",
                "home_canonical": "Coll Home Canonical",
                "away_canonical": "Coll Away Canonical",
                "home_team_id": str(colliding_home_ids[0]),
                "away_team_id": str(colliding_away_ids[0]),
                "home_collision": True,
                "away_collision": True,
                "colliding_home_team_ids": [str(t) for t in colliding_home_ids],
                "colliding_away_team_ids": [str(t) for t in colliding_away_ids],
            }
            conn.execute(text(
                """
                INSERT INTO sp.review_queue
                  (id, provider, provider_record_id, candidate_fixtures,
                   confidence, reason_detail, provider_title,
                   status, created_at)
                VALUES
                  (gen_random_uuid(), 'kalshi', :pk,
                   CAST(:cands AS jsonb), 0.0,
                   CAST(:rd AS jsonb), 'Coll Home vs Coll Away',
                   'pending', NOW())
                """
            ).bindparams(
                pk=ticker,
                cands=json.dumps(
                    [str(t) for t in colliding_home_ids]
                    + [str(t) for t in colliding_away_ids]
                ),
                rd=json.dumps(reason_detail),
            ))
            record_id = conn.execute(text(
                "SELECT id FROM sp.review_queue WHERE provider_record_id = :pk"
            ).bindparams(pk=ticker)).scalar()
        return record_id

    def _two_real_teams(self, engine):
        """Pick two real sp.teams rows for fixture creation. ensure_fixture
        FKs back to sp.teams; making up team_ids breaks the FK."""
        from sqlalchemy import text
        with engine.begin() as conn:
            rows = conn.execute(text(
                "SELECT id, canonical_name FROM sp.teams ORDER BY id LIMIT 4"
            )).all()
        if len(rows) < 4:
            pytest.skip("integration DB has fewer than 4 sp.teams rows")
        return rows  # use [0],[1] for one test, [2],[3] for another

    # ── #4: Audit trail + happy path (non-collision approve) ──

    def test_approve_non_collision_writes_audit_fields(self, app, engine):
        """Scenario 4: reviewed_by + reviewed_at populated on approve."""
        from sqlalchemy import text
        teams = self._two_real_teams(engine)
        home, away = teams[0], teams[1]
        record_id = self._seed_non_collision_pending_row(
            engine, "TEST-2F1-MUT-AUDIT", home.id, away.id,
        )

        resp = app.post(
            f"/admin/review-queue/{record_id}/approve",
            data={
                "home_team_id": str(home.id),
                "away_team_id": str(away.id),
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303, (
            f"expected 303 redirect (no-JS path); got {resp.status_code} "
            f"with body {resp.text[:300]}"
        )

        with engine.begin() as conn:
            row = conn.execute(text(
                "SELECT status, reviewed_by, reviewed_at "
                "FROM sp.review_queue WHERE id = :rid"
            ).bindparams(rid=record_id)).first()
        assert row.status == "approved"
        assert row.reviewed_by == "operator"  # single-operator default
        assert row.reviewed_at is not None

    # ── #5: Team_aliases write-back (source='operator_review') ──

    def test_approve_writes_team_aliases_with_operator_review_source(self, app, engine):
        from sqlalchemy import text
        teams = self._two_real_teams(engine)
        home, away = teams[0], teams[1]
        record_id = self._seed_non_collision_pending_row(
            engine, "TEST-2F1-MUT-ALIAS", home.id, away.id,
        )

        app.post(
            f"/admin/review-queue/{record_id}/approve",
            data={
                "home_team_id": str(home.id),
                "away_team_id": str(away.id),
            },
            follow_redirects=False,
        )

        with engine.begin() as conn:
            rows = conn.execute(text(
                "SELECT team_id, alias, source FROM sp.team_aliases "
                "WHERE source = 'operator_review' "
                "AND team_id IN (:home, :away)"
            ).bindparams(home=home.id, away=away.id)).all()

        # Two aliases written: one per side.
        assert len(rows) >= 2
        sources = {r.source for r in rows}
        assert sources == {"operator_review"}
        team_ids = {r.team_id for r in rows}
        assert home.id in team_ids
        assert away.id in team_ids

    # ── #6: Rejection_count increment ──

    def test_reject_increments_rejection_count(self, app, engine):
        from sqlalchemy import text
        teams = self._two_real_teams(engine)
        record_id = self._seed_non_collision_pending_row(
            engine, "TEST-2F1-MUT-REJECT", teams[0].id, teams[1].id,
        )

        # Verify starting count.
        with engine.begin() as conn:
            initial = conn.execute(text(
                "SELECT rejection_count FROM sp.review_queue WHERE id = :rid"
            ).bindparams(rid=record_id)).scalar()
        assert initial == 0

        resp = app.post(
            f"/admin/review-queue/{record_id}/reject",
            follow_redirects=False,
        )
        assert resp.status_code == 303

        with engine.begin() as conn:
            after = conn.execute(text(
                "SELECT status, rejection_count, reviewed_by "
                "FROM sp.review_queue WHERE id = :rid"
            ).bindparams(rid=record_id)).first()
        assert after.status == "rejected"
        assert after.rejection_count == 1
        assert after.reviewed_by == "operator"

    # ── #1: Idempotency (double-click = no-op) ──

    def test_approve_double_click_is_idempotent(self, app, engine):
        from sqlalchemy import text
        teams = self._two_real_teams(engine)
        home, away = teams[0], teams[1]
        record_id = self._seed_non_collision_pending_row(
            engine, "TEST-2F1-MUT-IDEMPOTENT", home.id, away.id,
        )

        # First click.
        app.post(
            f"/admin/review-queue/{record_id}/approve",
            data={
                "home_team_id": str(home.id),
                "away_team_id": str(away.id),
            },
            follow_redirects=False,
        )
        with engine.begin() as conn:
            first = conn.execute(text(
                "SELECT reviewed_at FROM sp.review_queue WHERE id = :rid"
            ).bindparams(rid=record_id)).scalar()

        # Second click — same params.
        resp = app.post(
            f"/admin/review-queue/{record_id}/approve",
            data={
                "home_team_id": str(home.id),
                "away_team_id": str(away.id),
            },
            follow_redirects=False,
        )
        # The second click should still complete cleanly (303 / 200),
        # NOT 500. Operator double-clicks shouldn't error.
        assert resp.status_code in (200, 303), (
            f"expected 200 or 303 on idempotent double-click; "
            f"got {resp.status_code} with body {resp.text[:300]}"
        )

        with engine.begin() as conn:
            second = conn.execute(text(
                "SELECT reviewed_at, status FROM sp.review_queue WHERE id = :rid"
            ).bindparams(rid=record_id)).first()
        # reviewed_at unchanged — second click was a no-op per Q2's
        # WHERE status='pending' guard.
        assert second.status == "approved"
        assert second.reviewed_at == first, (
            "Second approve click overwrote reviewed_at — idempotency "
            "guard (WHERE status='pending') failed."
        )

    # ── #7: Collision case validation ──

    def test_approve_rejects_team_id_not_in_collision_set(self, app, engine):
        from sqlalchemy import text
        teams = self._two_real_teams(engine)
        # Use teams 0 + 1 as the home collision set, 2 + 3 as away.
        record_id = self._seed_collision_pending_row(
            engine, "TEST-2F1-MUT-COLLIDE",
            colliding_home_ids=[teams[0].id, teams[1].id],
            colliding_away_ids=[teams[2].id, teams[3].id],
        )

        # Submit a home_team_id that's NOT in the collision set.
        bogus = uuid.uuid4()
        resp = app.post(
            f"/admin/review-queue/{record_id}/approve",
            data={
                "home_team_id": str(bogus),
                "away_team_id": str(teams[2].id),
            },
            follow_redirects=False,
        )
        assert resp.status_code == 400, (
            f"expected 400 for invalid team_id; got {resp.status_code}"
        )
        # Verify the row is still pending — server-side validation
        # blocked the write.
        with engine.begin() as conn:
            status_val = conn.execute(text(
                "SELECT status FROM sp.review_queue WHERE id = :rid"
            ).bindparams(rid=record_id)).scalar()
        assert status_val == "pending"

    def test_approve_collision_happy_path(self, app, engine):
        """Operator picks valid team_ids from both collision sets;
        approve succeeds. Confirms the radio-form → POST → server
        validation → write loop end-to-end."""
        from sqlalchemy import text
        teams = self._two_real_teams(engine)
        record_id = self._seed_collision_pending_row(
            engine, "TEST-2F1-MUT-COLLIDE-OK",
            colliding_home_ids=[teams[0].id, teams[1].id],
            colliding_away_ids=[teams[2].id, teams[3].id],
        )

        resp = app.post(
            f"/admin/review-queue/{record_id}/approve",
            data={
                "home_team_id": str(teams[1].id),  # not the default [0]
                "away_team_id": str(teams[2].id),
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

        with engine.begin() as conn:
            row = conn.execute(text(
                "SELECT status FROM sp.review_queue WHERE id = :rid"
            ).bindparams(rid=record_id)).first()
        assert row.status == "approved"

    # ── #8: HX-Request fragment response ──

    def test_approve_with_hx_request_header_returns_fragment(self, app, engine):
        teams = self._two_real_teams(engine)
        record_id = self._seed_non_collision_pending_row(
            engine, "TEST-2F1-MUT-HX", teams[0].id, teams[1].id,
        )

        resp = app.post(
            f"/admin/review-queue/{record_id}/approve",
            data={
                "home_team_id": str(teams[0].id),
                "away_team_id": str(teams[1].id),
            },
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )
        # HTMX path: 200 with fragment, NOT 303 redirect.
        assert resp.status_code == 200, (
            f"HX-Request path returned {resp.status_code}; expected 200 "
            f"with fragment body"
        )
        # Fragment shape: contains the decision-banner element but
        # NOT a full <html> shell.
        assert "decision-banner" in resp.text
        assert "<!DOCTYPE html>" not in resp.text, (
            "HX-Request response should be a fragment, not a full page"
        )

    # ── #9: No-JS fallback (plain form POST + redirect) ──

    def test_approve_without_hx_request_redirects(self, app, engine):
        teams = self._two_real_teams(engine)
        record_id = self._seed_non_collision_pending_row(
            engine, "TEST-2F1-MUT-NOJS", teams[0].id, teams[1].id,
        )

        resp = app.post(
            f"/admin/review-queue/{record_id}/approve",
            data={
                "home_team_id": str(teams[0].id),
                "away_team_id": str(teams[1].id),
            },
            follow_redirects=False,
        )
        # No-JS path: 303 redirect to detail view.
        assert resp.status_code == 303
        assert resp.headers["location"] == f"/admin/review-queue/{record_id}"

    # ── #2 + #3: Concurrency + partial-failure ─────────────

    def test_approve_concurrent_decision_returns_already_decided(self, app, engine):
        """Scenario 2 — simulated concurrency. A second session
        approves the row while the operator's form was open. The
        operator's submit hits the WHERE status='pending' guard
        and returns 'already_decided' (NOT a 500, NOT a duplicate
        write).

        We simulate the race by manually updating status='approved'
        before calling the route — equivalent in effect to a
        concurrent session committing first.
        """
        from sqlalchemy import text
        teams = self._two_real_teams(engine)
        record_id = self._seed_non_collision_pending_row(
            engine, "TEST-2F1-MUT-CONCUR", teams[0].id, teams[1].id,
        )

        # Pre-flight: another session "wins" the race.
        with engine.begin() as conn:
            conn.execute(text(
                "UPDATE sp.review_queue "
                "SET status='approved', reviewed_by='other_operator', "
                "    reviewed_at=NOW() "
                "WHERE id = :rid"
            ).bindparams(rid=record_id))

        # Now the operator submits approve. Their click should NOT
        # overwrite the other session's decision.
        resp = app.post(
            f"/admin/review-queue/{record_id}/approve",
            data={
                "home_team_id": str(teams[0].id),
                "away_team_id": str(teams[1].id),
            },
            follow_redirects=False,
        )
        # Either 303 (no-JS path, redirects to detail view that shows
        # the already-decided state) or 200 (fragment with
        # already_decided banner). Either way, NOT 500.
        assert resp.status_code in (200, 303)

        # The original session's reviewed_by stays put.
        with engine.begin() as conn:
            row = conn.execute(text(
                "SELECT reviewed_by FROM sp.review_queue WHERE id = :rid"
            ).bindparams(rid=record_id)).first()
        assert row.reviewed_by == "other_operator", (
            "Concurrent operator's reviewed_by was overwritten — the "
            "WHERE status='pending' guard failed to protect the prior "
            "decision."
        )

    def test_approve_refuses_when_kickoff_missing(self, app, engine):
        """Scenario 3 variant — partial-failure boundary. Q1 edge
        case: kickoff_at unavailable from provider raw_payload →
        ApprovalError 400 BEFORE the transaction starts. No
        side-effects on review_queue / provider table / team_aliases.
        """
        from sqlalchemy import text
        teams = self._two_real_teams(engine)
        record_id = self._seed_non_collision_pending_row(
            engine, "TEST-2F1-MUT-NOKICK", teams[0].id, teams[1].id,
        )
        # Strip the kickoff from the provider row to force the
        # refuse-on-missing-kickoff path.
        with engine.begin() as conn:
            conn.execute(text(
                "UPDATE sp.kalshi_markets "
                "SET raw_payload = raw_payload - '_kickoff_dt' "
                "WHERE ticker = 'TEST-2F1-MUT-NOKICK'"
            ))

        resp = app.post(
            f"/admin/review-queue/{record_id}/approve",
            data={
                "home_team_id": str(teams[0].id),
                "away_team_id": str(teams[1].id),
            },
            follow_redirects=False,
        )
        assert resp.status_code == 400, (
            f"expected 400 (refuse-on-missing-kickoff); got {resp.status_code}"
        )
        # Side-effect check: review_queue stayed pending, no
        # team_aliases written.
        with engine.begin() as conn:
            row = conn.execute(text(
                "SELECT status FROM sp.review_queue WHERE id = :rid"
            ).bindparams(rid=record_id)).first()
            alias_count = conn.execute(text(
                "SELECT COUNT(*) FROM sp.team_aliases "
                "WHERE source = 'operator_review' "
                "  AND team_id IN (:h, :a)"
            ).bindparams(h=teams[0].id, a=teams[1].id)).scalar()
        assert row.status == "pending"
        assert alias_count == 0, (
            "Server should not write team_aliases when approval fails "
            "pre-flight (Q3 single-transaction atomicity)."
        )
