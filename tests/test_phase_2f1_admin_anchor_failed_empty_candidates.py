"""Phase 2F.1 sub-PR #4.1 — regression tests for anchor_failed
empty-candidates handling.

Today's France-vs-Senegal smoke test (PR #133 post-merge) surfaced
that the Suggest-alias widget's `{% else %}` branch conflates three
distinct empty-dict states into one wrong message ("Matcher didn't
classify a sport"). This file captures the corrected contract.

The three paths and their correct UX:

  Path A — sport unclassified (reason_detail.sport missing/None)
      EXPECTED MESSAGE: "Matcher didn't classify a sport, so no
      candidate teams can be suggested." Current message is correct
      for THIS path only; keep as-is.

  Path B — sport classified + parsed names available + candidate
           query returned empty OR all-low-similarity
      EXPECTED MESSAGE: explains that no good candidate exists in
      sp.teams for the parsed name and emits a clipboard-copyable
      `make alias-add` STUB command with --alias pre-filled but
      --team-canonical LEFT BLANK for the operator to fill in
      manually. Operator types the canonical team name themselves;
      alias_add.py rejects with "team not found in sp.teams" if the
      operator typo'd or the canonical doesn't exist (per issues
      #135, #136 follow-ups).

  Path C — sport classified + parsed names NOT in reason_detail
           (pre-sub-PR-#5 fuzzy_no_team_resemblance records)
      EXPECTED MESSAGE: explains that the matcher dropped the parsed
      names for this record (a real resolver-side bug, tracked as
      sub-PR #5), and surfaces the raw provider title + payload so
      the operator can read what the provider intended and craft a
      manual `make alias-add` command themselves. Sub-PR #5 closes
      this gap at the resolver side; #4.1's job is to fail gracefully
      until #5 ships.

Tests are integration-level (real Postgres). They assert on rendered
HTML — same shape as the existing
tests/test_phase_2f1_admin_anchor_failed.py integration suite.
"""
from __future__ import annotations

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
_TEST_MARKER = "TEST-2F1-SUB4_1-EC"  # EC = Empty Candidates


@pytest.mark.skipif(
    not INTEGRATION_DB,
    reason="SP_INTEGRATION_DB not set — empty-candidates tests need real Postgres.",
)
class TestEmptyCandidatesPaths:
    """Three distinct empty-dict paths in the Suggest-alias widget;
    one test per path asserting the corrected template behavior."""

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

    def _seed_run(self, engine) -> uuid.UUID:
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
                "started": datetime.now(timezone.utc),
                "finished": datetime.now(timezone.utc) + timedelta(minutes=5),
                "extra": json.dumps({"test_marker": _TEST_MARKER}),
            })
        return run_id

    def _seed_log(self, engine, *, run_id: uuid.UUID, pk: str,
                  reason_detail: dict, fail_reason: str = "alias_no_team_resemblance"):
        from sqlalchemy import text
        rd = dict(reason_detail)
        rd["fail_reason"] = fail_reason
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO sp.resolution_log "
                "(run_id, provider, provider_record_id, fixture_id, "
                " confidence, reason_code, reason_detail, "
                " resolver_version, decided_at) "
                "VALUES (:run_id, 'kalshi', :pk, NULL, 0.0, "
                "        'no_match', CAST(:rd AS jsonb), "
                "        'alias@2c.0', NOW())"
            ), {"run_id": run_id, "pk": pk, "rd": json.dumps(rd)})

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
        client.post("/admin/login", data={"password": _TEST_PASSWORD},
                    follow_redirects=False)
        yield client

    def test_path_A_sport_unclassified_keeps_existing_message(self, engine, app):
        """Path A: reason_detail.sport is missing → "Matcher didn't
        classify a sport" message is correct, keep it.

        This path is rare in practice (alias/fuzzy tier always
        classifies sport before the anchor check; sport-unclassified
        would have failed earlier with `sport_not_classified` which
        is OUTSIDE the anchor-failed family). Test it anyway because
        a future emission site could hit this state."""
        run_id = self._seed_run(engine)
        pk = f"{_TEST_MARKER}-PATH-A"
        # Intentionally NO 'sport' key in reason_detail.
        self._seed_log(engine, run_id=run_id, pk=pk, reason_detail={
            "home_provider_normalized": "Some Team",
            "away_provider_normalized": "Other Team",
        })
        resp = app.get(f"/admin/anchor-failed/kalshi/{pk}")
        assert resp.status_code == 200
        # Path A's message must mention sport classification.
        assert "didn't classify a sport" in resp.text.lower() or \
               "sport unclassified" in resp.text.lower(), \
               "Path A should keep the existing sport-unclassified message"
        # And must NOT show the Path B/C clipboard widget.
        assert "copy-alias-cmd" not in resp.text

    def test_path_B_sport_classified_no_good_candidates_shows_stub_command(
        self, engine, app,
    ):
        """Path B: sport classified, parsed names available, but
        candidate query returns empty (no rows in sp.teams for that
        sport) or all-low-similarity.

        Expected: distinct message explaining "no good candidate
        exists" + a clipboard-copyable make alias-add STUB with the
        parsed name pre-filled as --alias and --team-canonical blank
        for the operator to fill in.

        Seed shape: a Soccer record (sport_id 1) with parsed names,
        but no Soccer teams in the test DB → candidate query returns
        empty → Path B."""
        # Ensure no Soccer teams exist in the test DB.
        from sqlalchemy import text
        with engine.begin() as conn:
            conn.execute(text(
                "DELETE FROM sp.teams WHERE sport_id = "
                "(SELECT id FROM sp.sports WHERE name = 'Soccer')"
            ))

        run_id = self._seed_run(engine)
        pk = f"{_TEST_MARKER}-PATH-B"
        self._seed_log(engine, run_id=run_id, pk=pk, reason_detail={
            "sport": "Soccer",
            "sport_id": 1,
            "home_provider_normalized": "France",
            "away_provider_normalized": "Senegal",
        })
        resp = app.get(f"/admin/anchor-failed/kalshi/{pk}")
        assert resp.status_code == 200
        # MUST NOT show the wrong "didn't classify a sport" message.
        assert "didn't classify a sport" not in resp.text.lower()
        # MUST show parsed names so operator knows what to alias.
        assert "France" in resp.text and "Senegal" in resp.text
        # MUST emit a stub clipboard command (button OR pre-rendered
        # command string) with --alias pre-filled and --team-canonical
        # awaiting operator input.
        assert "make alias-add" in resp.text
        assert "--alias 'France'" in resp.text or '"France"' in resp.text or \
               "France" in resp.text  # at minimum, parsed name visible
        # MUST mention that operator types --team-canonical manually.
        # Exact wording flexible; assert key terms.
        body_lower = resp.text.lower()
        assert ("type" in body_lower and "team-canonical" in body_lower) or \
               ("supply" in body_lower and "team-canonical" in body_lower) or \
               ("fill in" in body_lower and "team-canonical" in body_lower), \
               "Path B must instruct operator to type --team-canonical"

    def test_path_C_parsed_names_missing_shows_raw_payload_fallback(
        self, engine, app,
    ):
        """Path C: sport classified, but parsed names are NOT in
        reason_detail (pre-sub-PR-#5 fuzzy_no_team_resemblance records).

        Expected: distinct message explaining that the matcher
        dropped parsed names for this record (resolver bug, sub-PR #5
        fix), and surfaces the raw provider payload so the operator
        can read the intended team labels and craft a manual command.

        Seed shape: a fuzzy_no_team_resemblance record with ONLY the
        anchor_failed flags + sport — no parsed names. This matches
        what production records emitted before sub-PR #5 ships."""
        run_id = self._seed_run(engine)
        pk = f"{_TEST_MARKER}-PATH-C"
        self._seed_log(engine, run_id=run_id, pk=pk,
                       fail_reason="fuzzy_no_team_resemblance",
                       reason_detail={
                           "sport": "Soccer",
                           "sport_id": 1,
                           "home_anchor_failed": False,
                           "away_anchor_failed": True,
                           # NO home_provider_normalized / home_canonical
                       })
        resp = app.get(f"/admin/anchor-failed/kalshi/{pk}")
        assert resp.status_code == 200
        # MUST NOT show the wrong sport-unclassified message.
        assert "didn't classify a sport" not in resp.text.lower()
        # MUST mention that parsed names are missing / point to raw payload.
        body_lower = resp.text.lower()
        assert "raw payload" in body_lower or "raw provider payload" in body_lower
        assert "parsed name" in body_lower or "didn't preserve" in body_lower \
               or "preserved" in body_lower, \
               "Path C must surface that parsed names were not preserved"
        # MUST NOT show a clipboard button (operator has no parsed name
        # to put in --alias yet; surface the raw payload first).
        assert "copy-alias-cmd" not in resp.text
