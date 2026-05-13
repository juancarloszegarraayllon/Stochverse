"""Phase 2F.1 sub-PR #7 — approve/reject ApprovalError HTMX fragment tests.

Closes Issue #131: approve/reject routes' empty-body-on-error UX gap.
Pre-PR-#131, ApprovalError raised inside approve_record / reject_record
re-rendered the full review_queue_detail.html template with form_error
set + status_code=4xx. The no-JS path showed the error correctly. The
HTMX path silently aborted the swap (HTMX default behavior on non-2xx
responses) — operator clicked, saw nothing change, had to dig into
Railway logs to find out why.

PR #131 fix: branch on HX-Request header in _approval_error_response.

  - HTMX path: render the new _error.html partial with response
    headers HX-Reswap: outerHTML + HX-Retarget: #form-error, status
    200. HTMX swaps the partial into the always-present <div
    id="form-error"> wrapper in _decision_form.html, leaving the
    form itself (radio buttons, hidden inputs) untouched.
  - No-JS path: existing behavior — re-render full page at 4xx with
    form_error set.

Refinement 2: align reject's 409 race-detection message with approve's
("...Reload to see current state."). Pre-PR-#131 the reject 409
message was missing that guidance.

Tests are integration-level (real Postgres) AND include one static
AST guard (no DB needed).

Mirrors the shape of tests/test_phase_2f1_admin_mutations.py — same
fixture helpers, same SP_INTEGRATION_DB gating, same login flow.
"""
from __future__ import annotations

import ast
import json
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
_TEST_MARKER = "TEST-2F1-SUB7-ERR"


# ── Static AST guard (no DB needed) ────────────────────────────


class TestApprovalErrorResponseAstGuard:
    """Catches future regressions where _approval_error_response stops
    branching on the HX-Request header. Same shape as the anchor_failed
    static guard at tests/test_phase_2f1_admin_anchor_failed.py."""

    def test_approval_error_response_branches_on_htmx_header(self):
        """Parse admin/router.py AST, locate _approval_error_response,
        assert its body references _is_htmx_request at least once.

        Without the branch, the HTMX-path bug from Issue #131 returns:
        operator submits a bad team_id, gets a non-2xx response, HTMX
        aborts the swap, operator sees nothing change. This guard
        prevents that regression at import time, not in production.
        """
        router_path = REPO_ROOT / "admin" / "router.py"
        tree = ast.parse(router_path.read_text())
        target = None
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == "_approval_error_response"
            ):
                target = node
                break
        assert target is not None, (
            "Expected to find _approval_error_response in admin/router.py. "
            "If this function was renamed, update both the test and the "
            "downstream callers in approve / reject handlers."
        )
        # Recursively scan the function body for any Name or Attribute
        # node whose target identifier is _is_htmx_request.
        found_branch = False
        for inner in ast.walk(target):
            if isinstance(inner, ast.Name) and inner.id == "_is_htmx_request":
                found_branch = True
                break
            if isinstance(inner, ast.Attribute) and inner.attr == "_is_htmx_request":
                found_branch = True
                break
        assert found_branch, (
            "Sub-PR #7 contract: _approval_error_response must branch "
            "on _is_htmx_request(request) so HTMX clients get the "
            "_error.html partial and no-JS clients get the full-page "
            "re-render. See Issue #131 for the UX motivation."
        )


# ── Integration tests (require SP_INTEGRATION_DB) ──────────────


@pytest.mark.skipif(
    not INTEGRATION_DB,
    reason=(
        "SP_INTEGRATION_DB not set — ApprovalError HTMX tests need real "
        "Postgres with sp schema migrations applied through 2F.0.1."
    ),
)
class TestApprovalErrorHtmxFragment:
    """End-to-end: seed a pending record, POST approve/reject with
    HX-Request: true and a bad form value, assert the response is the
    new _error.html partial — status 200, HX-Retarget header, body
    containing the error message wrapped in <div id="form-error">."""

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
            cwd=REPO_ROOT, capture_output=True, text=True,
            env={**os.environ, "DATABASE_URL": INTEGRATION_DB},
        )
        assert result.returncode == 0, (
            f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}"
        )
        self._purge_test_data(engine)
        yield
        self._purge_test_data(engine)

    def _purge_test_data(self, engine):
        from sqlalchemy import text
        with engine.begin() as conn:
            conn.execute(text(
                "DELETE FROM sp.review_queue "
                "WHERE provider_record_id LIKE :marker"
            ), {"marker": f"{_TEST_MARKER}%"})
            conn.execute(text(
                "DELETE FROM sp.kalshi_markets WHERE ticker LIKE :marker"
            ), {"marker": f"{_TEST_MARKER}%"})

    def _two_real_teams(self, engine):
        from sqlalchemy import text
        with engine.begin() as conn:
            rows = conn.execute(text(
                "SELECT id, canonical_name FROM sp.teams ORDER BY id LIMIT 4"
            )).all()
        if len(rows) < 4:
            pytest.skip(
                "integration DB has fewer than 4 sp.teams rows — "
                "seed via test_phase_2f1_admin_mutations or run "
                "scripts/bootstrap_sp_teams first."
            )
        return rows

    def _seed_non_collision_pending(self, engine, ticker, home_tid, away_tid):
        """Seed a non-collision pending review_queue row with kickoff
        populated (so approve doesn't trip the no-kickoff guard)."""
        from sqlalchemy import text
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO sp.kalshi_markets "
                "(ticker, market_type, raw_payload, last_seen_at, "
                " last_changed_at, payload_hash) "
                "VALUES (:ticker, 'game', CAST(:payload AS jsonb), "
                "        NOW(), NOW(), 'test-hash') "
                "ON CONFLICT (ticker) DO NOTHING"
            ), {
                "ticker": ticker,
                "payload": json.dumps({
                    "title": "Sub7 Home vs Sub7 Away",
                    "_kickoff_dt": "2026-06-15T14:30:00+00:00",
                }),
            })
            reason_detail = {
                "sport": "Tennis",
                "fail_reason": "below_threshold",
                "home_canonical": "Sub7 Home Canonical",
                "away_canonical": "Sub7 Away Canonical",
                "home_team_id": str(home_tid),
                "away_team_id": str(away_tid),
            }
            record_id = conn.execute(text(
                "INSERT INTO sp.review_queue "
                "(id, provider, provider_record_id, candidate_fixtures, "
                " confidence, reason_detail, provider_title, status, "
                " created_at) "
                "VALUES (gen_random_uuid(), 'kalshi', :pk, "
                "        CAST(:cands AS jsonb), 0.78, CAST(:rd AS jsonb), "
                "        'Sub7 Home vs Sub7 Away', 'pending', NOW()) "
                "RETURNING id"
            ), {
                "pk": ticker,
                "cands": json.dumps([str(home_tid), str(away_tid)]),
                "rd": json.dumps(reason_detail),
            }).scalar()
        return record_id

    def _seed_decided_record(self, engine, ticker, home_tid, away_tid):
        """Seed a record that's already been decided (status='approved').
        Used for concurrent-decision tests — operator submits against
        this; approve_record raises ApprovalError(409)."""
        from sqlalchemy import text
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO sp.kalshi_markets "
                "(ticker, market_type, raw_payload, last_seen_at, "
                " last_changed_at, payload_hash) "
                "VALUES (:ticker, 'game', CAST(:payload AS jsonb), "
                "        NOW(), NOW(), 'test-hash') "
                "ON CONFLICT (ticker) DO NOTHING"
            ), {
                "ticker": ticker,
                "payload": json.dumps({
                    "title": "Sub7 Decided Home vs Sub7 Decided Away",
                    "_kickoff_dt": "2026-06-15T14:30:00+00:00",
                }),
            })
            reason_detail = {
                "sport": "Tennis", "fail_reason": "below_threshold",
                "home_canonical": "Sub7 Decided Home",
                "away_canonical": "Sub7 Decided Away",
                "home_team_id": str(home_tid),
                "away_team_id": str(away_tid),
            }
            # status='approved' — concurrent-decision case.
            record_id = conn.execute(text(
                "INSERT INTO sp.review_queue "
                "(id, provider, provider_record_id, candidate_fixtures, "
                " confidence, reason_detail, provider_title, status, "
                " reviewed_by, reviewed_at, created_at) "
                "VALUES (gen_random_uuid(), 'kalshi', :pk, "
                "        CAST(:cands AS jsonb), 0.78, CAST(:rd AS jsonb), "
                "        'Sub7 Decided', 'approved', "
                "        'prior-operator', NOW() - interval '1 minute', NOW()) "
                "RETURNING id"
            ), {
                "pk": ticker,
                "cands": json.dumps([str(home_tid), str(away_tid)]),
                "rd": json.dumps(reason_detail),
            }).scalar()
        return record_id

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
        import main
        client = TestClient(main.app)
        client.post(
            "/admin/login",
            data={"password": _TEST_PASSWORD},
            follow_redirects=False,
        )
        yield client

    # ── HTMX path: error returns 200 + partial + headers ───────

    def test_approve_htmx_error_returns_200_with_error_partial(self, app, engine):
        """Bad team_id submitted via HX-Request → 200 + _error.html
        partial body + HX-Retarget: #form-error header. Sub-PR #7 core
        contract."""
        teams = self._two_real_teams(engine)
        home, away = teams[0], teams[1]
        wrong_team = teams[2]  # not in this record's candidate set
        ticker = f"{_TEST_MARKER}-APPROVE-BAD-TEAM"
        record_id = self._seed_non_collision_pending(
            engine, ticker, home.id, away.id,
        )
        resp = app.post(
            f"/admin/review-queue/{record_id}/approve",
            data={
                "home_team_id": str(wrong_team.id),  # ← not in candidate set
                "away_team_id": str(away.id),
            },
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )
        assert resp.status_code == 200, (
            f"Sub-PR #7 contract: HTMX error returns 200 (HTMX swaps "
            f"on 2xx by default). Got {resp.status_code}; body: "
            f"{resp.text[:300]}"
        )
        # Body must contain the form-error partial.
        assert 'id="form-error"' in resp.text, (
            "Body must contain <div id=\"form-error\"> for HX-Retarget "
            "to land. _error.html wraps the error div with that id."
        )
        # Headers must instruct HTMX to retarget + outer-swap.
        assert resp.headers.get("HX-Retarget") == "#form-error", (
            "HX-Retarget must equal '#form-error' so HTMX swaps into "
            "the form-error wrapper, not the form's default "
            "hx-target='#decision-panel'."
        )
        assert resp.headers.get("HX-Reswap") == "outerHTML", (
            "HX-Reswap must equal 'outerHTML' so the partial replaces "
            "the wrapper entirely (preserving id for future swaps)."
        )

    def test_approve_htmx_error_response_body_does_not_contain_form(self, app, engine):
        """Sub-PR #7's partial must be small/targeted, not a full page.
        Smoke check: response body must NOT contain <form>, <input>,
        or <button type="submit"> tags — those live in the form panel
        that the partial is NOT touching."""
        teams = self._two_real_teams(engine)
        home, away = teams[0], teams[1]
        wrong_team = teams[2]
        ticker = f"{_TEST_MARKER}-APPROVE-PARTIAL-SHAPE"
        record_id = self._seed_non_collision_pending(
            engine, ticker, home.id, away.id,
        )
        resp = app.post(
            f"/admin/review-queue/{record_id}/approve",
            data={"home_team_id": str(wrong_team.id),
                  "away_team_id": str(away.id)},
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )
        assert resp.status_code == 200
        body = resp.text
        assert "<form" not in body, (
            "Partial must not include <form> tags. Found one — likely "
            "indicates the full review_queue_detail.html rendered "
            "instead of _error.html. Check the HX-Request branch in "
            "_approval_error_response."
        )
        assert "<input" not in body, "Same reason — partial must be small."
        # The Copy / Reject buttons live in _decision_form.html; the
        # partial shouldn't include them either.
        assert "Approve & link fixture" not in body
        assert 'class="danger"' not in body  # the Reject button styling

    def test_approve_htmx_error_message_text_visible(self, app, engine):
        """The HTMX-path partial must surface the actual error message,
        not just generic "an error occurred." Smoke test: submit a bad
        team_id, confirm the message keywords from the ApprovalError
        appear in the response body.

        Scoped to a 400 validation error (bad team_id) because the 409
        concurrent-decision path is hard to trigger in single-process
        tests — approve_record short-circuits on status != 'pending'
        with an `already_decided` return rather than raising
        ApprovalError. Real concurrency requires interleaved
        transactions across two database sessions, out of scope for
        this regression guard. Refinement 2's "reject message
        consistency" assertion lives in a separate source-inspection
        test (test_reject_409_message_matches_approve_per_refinement_2).
        """
        teams = self._two_real_teams(engine)
        home, away = teams[0], teams[1]
        wrong_team = teams[2]
        ticker = f"{_TEST_MARKER}-APPROVE-MESSAGE-VISIBLE"
        record_id = self._seed_non_collision_pending(
            engine, ticker, home.id, away.id,
        )
        resp = app.post(
            f"/admin/review-queue/{record_id}/approve",
            data={"home_team_id": str(wrong_team.id),
                  "away_team_id": str(away.id)},
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )
        assert resp.status_code == 200
        # Message embeds the wrong team_id UUID — stable substring,
        # autoescape-safe.
        assert str(wrong_team.id) in resp.text, (
            "HTMX error partial must include the submitted team_id "
            f"({wrong_team.id}) — the validation message embeds it "
            "so the operator can see what they sent."
        )
        # Per Issue #149 autoescape-aware assertions: 'doesn't match'
        # gets rendered as 'doesn&#39;t match' inside <pre>/<code>/text
        # contexts. Accept both forms.
        assert (
            "doesn't match" in resp.text
            or "doesn&#39;t match" in resp.text
        ), (
            "HTMX error partial must include the validation message "
            "text 'doesn't match' (raw or HTML-escaped) so the "
            "operator sees why their submission failed."
        )

    # ── HTMX path: reject error ────────────────────────────────

    def test_reject_htmx_error_returns_200_with_error_partial(self, app, engine):
        """Reject path uses the same _approval_error_response shared
        helper. POST reject to a non-existent record_id triggers
        ApprovalError(404, "review_queue record ... not found") —
        easier to trigger reliably than the 409 concurrent-decision
        case (see test_approve_htmx_error_message_text_visible for
        the 409-is-hard-to-trigger note)."""
        fake_record_id = uuid.uuid4()  # not in DB
        resp = app.post(
            f"/admin/review-queue/{fake_record_id}/reject",
            data={},
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )
        # 404 → still 200 on HTMX path (per Sub-PR #7 contract).
        assert resp.status_code == 200
        assert 'id="form-error"' in resp.text
        assert resp.headers.get("HX-Retarget") == "#form-error"
        assert resp.headers.get("HX-Reswap") == "outerHTML"
        # 404 message embeds the record_id.
        assert str(fake_record_id) in resp.text
        assert "not found" in resp.text

    def test_reject_409_message_matches_approve_per_refinement_2(self):
        """Refinement 2: source-level assertion that reject_record's
        409 concurrent-decision ApprovalError message includes 'Reload
        to see current state' — same recovery guidance as approve's
        equivalent. Pre-PR-#131 the reject message was missing the
        guidance; assertion lives here so the alignment fix can't
        silently regress.

        Source inspection (rather than HTTP round-trip) because the
        409 path is hard to trigger in single-process tests — the
        idempotency check at the top of reject_record short-circuits
        on status != 'pending'. The MESSAGE STRING is what we want to
        pin; reading it from the source is the most direct way."""
        import inspect
        from admin import queries
        source = inspect.getsource(queries.reject_record)
        # The reject path raises exactly one ApprovalError(409) for
        # the rowcount=0 concurrent-decision case. Find that raise
        # and assert it contains the alignment string.
        assert "Concurrent decision detected" in source, (
            "reject_record must raise ApprovalError with the "
            "'Concurrent decision detected' message text on rowcount=0. "
            "If this assertion fails, the 409 raise was removed or "
            "the message was changed — re-check the alignment with "
            "approve_record's equivalent."
        )
        assert "Reload to see current state" in source, (
            "Refinement 2: reject_record's 409 ApprovalError message "
            "must include 'Reload to see current state' guidance, "
            "matching approve_record's equivalent message. Pre-PR-#131 "
            "the reject message was missing this; alignment fix is "
            "bundled into this PR."
        )

    # ── No-JS path regression guard ────────────────────────────

    def test_no_js_path_still_returns_4xx_with_form_error(self, app, engine):
        """Sub-PR #7 must NOT regress the no-JS path. Submitting WITHOUT
        the HX-Request header → existing behavior: 4xx status + full
        review_queue_detail.html re-render with form_error block
        visible. Operator's plain-form POST workflow stays intact.

        Autoescape note: Jinja escapes `'` to `&#39;` inside text
        content. The validation error message contains "doesn't match"
        → rendered as "doesn&#39;t match". Assertions accept both
        forms (per Issue #149 convention).
        """
        teams = self._two_real_teams(engine)
        home, away = teams[0], teams[1]
        wrong_team = teams[2]
        ticker = f"{_TEST_MARKER}-NO-JS"
        record_id = self._seed_non_collision_pending(
            engine, ticker, home.id, away.id,
        )
        resp = app.post(
            f"/admin/review-queue/{record_id}/approve",
            data={"home_team_id": str(wrong_team.id),
                  "away_team_id": str(away.id)},
            # No HX-Request header — plain browser POST.
            follow_redirects=False,
        )
        # 400 (validation error per ApprovalError default status).
        assert resp.status_code == 400, (
            "No-JS path: server returns the original ApprovalError "
            "status code (400 for validation). Sub-PR #7 only changes "
            "the HTMX path; no-JS stays untouched. Got "
            f"{resp.status_code} — investigate whether the HX-Request "
            "branch is matching incorrectly."
        )
        body = resp.text
        # Full page = includes <form>, <html>, etc.
        assert "<form" in body, (
            "No-JS path: full page re-render. Form must be present "
            "for the operator to retry."
        )
        # The form_error div renders the validation message at the top
        # of the form. The wrong_team's UUID appears in the message
        # (".._team_id=<UUID> doesn't match...") — UUID is autoescape-
        # safe, no special chars. Use that as the stable presence check.
        assert '<div class="error"' in body, (
            "form_error block must render an <div class=\"error\"> "
            "wrapper at the top of the form on the no-JS path."
        )
        assert str(wrong_team.id) in body, (
            "The validation message embeds the submitted team_id "
            f"({wrong_team.id}). UUID should appear in the rendered "
            "error message text."
        )
        # And the message text itself, in either raw or HTML-escaped form
        # (autoescape converts ' to &#39; inside template text content).
        assert (
            "doesn't match" in body
            or "doesn&#39;t match" in body
        ), (
            "Validation message text 'doesn\\'t match' must render "
            "(raw or HTML-escaped). Per Issue #149's autoescape-aware "
            "template-test convention."
        )

    # NOTE: an earlier draft of this file had a
    # test_no_js_path_concurrent_returns_409 test that seeded a
    # status='approved' record and submitted approve, expecting 409.
    # Removed because approve_record / reject_record short-circuit on
    # status != 'pending' with an `already_decided` SUCCESS return,
    # not a 409 raise. The actual 409 path requires interleaved
    # transactions across two database sessions to win the rowcount=0
    # race — out of scope for single-process integration tests in
    # this PR. The HTMX-path 409 message contract is asserted via
    # source inspection in
    # test_reject_409_message_matches_approve_per_refinement_2.
