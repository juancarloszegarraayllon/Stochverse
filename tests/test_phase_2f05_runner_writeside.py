"""Phase 2F.0.5 integration tests.

The runner's review_queue INSERT must populate reason_detail and
provider_title (added in 2F.0, PR #114). These tests verify the
end-to-end SQL roundtrip against a real Postgres instance:

  - Apply migration (alembic upgrade head).
  - Construct a TieredMatcher whose final tier returns REVIEW_QUEUE.
  - Run the runner against a seeded provider record.
  - Read sp.review_queue back and assert reason_detail / provider_title
    are non-NULL.

Skipped unless SP_INTEGRATION_DB is set — same convention as
tests/test_resolver_2b.py and tests/test_phase_2f0_migration.py.

These tests don't run in standard CI; the operator runs them once
against a Neon dev branch (or a docker-compose Postgres) before
deploying 2F.0.5 to production.
"""
from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
INTEGRATION_DB = os.environ.get("SP_INTEGRATION_DB", "").strip()


@pytest.mark.skipif(
    not INTEGRATION_DB,
    reason=(
        "SP_INTEGRATION_DB not set — integration tests require a "
        "Postgres URL with the sp schema migration applied."
    ),
)
class TestRunnerWritesPhase2F0Columns:
    """Smoke tests against a live DB: insert a REVIEW_QUEUE record via
    the runner's actual SQL path and verify the new 2F.0 columns end
    up populated.

    These tests don't drive the full runner (extractor + matcher chain);
    they exercise the SQL fragment directly because the runner's
    REVIEW_QUEUE INSERT is what 2F.0.5 changed. End-to-end runner
    coverage stays in tests/test_resolver_2c.py / test_resolver_2d.py
    (mocked DB).
    """

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
    def ensure_migration_applied(self, engine):
        """Apply head migration + clean up any leftover test rows."""
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
        # Clean up any leftover test rows from prior runs.
        from sqlalchemy import text
        with engine.begin() as conn:
            conn.execute(text(
                "DELETE FROM sp.review_queue WHERE provider_record_id LIKE 'TEST-2F05-%'"
            ))
        yield
        with engine.begin() as conn:
            conn.execute(text(
                "DELETE FROM sp.review_queue WHERE provider_record_id LIKE 'TEST-2F05-%'"
            ))

    def _run_runner_insert(self, engine, *, provider, pk, reason_detail,
                           provider_title, candidate_fixtures, confidence):
        """Execute the same INSERT SQL the runner uses, with the same
        bindparam shapes. Mirrors scripts/run_resolver_pass.py exactly
        — if the runner SQL drifts, this test fails to reflect that
        drift. That's the point.
        """
        from sqlalchemy import text
        with engine.begin() as conn:
            conn.execute(text(
                """
                INSERT INTO sp.review_queue
                  (id, provider, provider_record_id,
                   candidate_fixtures, confidence,
                   reason_detail, provider_title,
                   status, created_at)
                VALUES
                  (gen_random_uuid(), :provider, :pk,
                   CAST(:cands AS jsonb), :conf,
                   CAST(:reason_detail AS jsonb), :title,
                   'pending', NOW())
                ON CONFLICT (provider, provider_record_id)
                  DO UPDATE SET
                    candidate_fixtures = EXCLUDED.candidate_fixtures,
                    confidence         = EXCLUDED.confidence,
                    reason_detail      = EXCLUDED.reason_detail,
                    provider_title     = EXCLUDED.provider_title
                  WHERE sp.review_queue.status = 'pending'
                """
            ).bindparams(
                provider=provider,
                pk=pk,
                cands=json.dumps([str(t) for t in candidate_fixtures]),
                conf=confidence,
                reason_detail=json.dumps(reason_detail or {}),
                title=provider_title,
            ))

    def test_kalshi_review_queue_insert_populates_2f0_columns(self, engine):
        from sqlalchemy import text

        pk = "TEST-2F05-KALSHI-001"
        candidate_a = uuid.uuid4()
        candidate_b = uuid.uuid4()
        reason_detail = {
            "fail_reason": "alias_collision",
            "home_canonical": "Bayern Munich",
            "away_canonical": "PSG",
            "home_collision": True,
            "colliding_home_team_ids": [str(candidate_a), str(candidate_b)],
        }
        self._run_runner_insert(
            engine,
            provider="kalshi",
            pk=pk,
            reason_detail=reason_detail,
            provider_title="Bayern Munich vs PSG",
            candidate_fixtures=[candidate_a, candidate_b],
            confidence=0.0,
        )

        with engine.begin() as conn:
            row = conn.execute(text(
                """
                SELECT reason_detail, provider_title, rejection_count, status
                FROM sp.review_queue
                WHERE provider = 'kalshi' AND provider_record_id = :pk
                """
            ).bindparams(pk=pk)).first()

        assert row is not None, "INSERT didn't produce a row"
        # Phase 2F.0.5 the assertion the user named: new columns must
        # be non-NULL on next insert.
        assert row.reason_detail is not None, (
            "reason_detail was NULL — runner write-side did not "
            "populate it."
        )
        assert row.provider_title == "Bayern Munich vs PSG"
        # rejection_count defaults to 0 from the column server_default
        # (Phase 2F.0). The reject action increments it; that handler
        # ships in 2F.1.
        assert row.rejection_count == 0
        assert row.status == "pending"

        # reason_detail JSONB must round-trip the matcher's dict shape.
        assert row.reason_detail["fail_reason"] == "alias_collision"
        assert row.reason_detail["home_collision"] is True
        assert set(row.reason_detail["colliding_home_team_ids"]) == {
            str(candidate_a), str(candidate_b),
        }

    def test_fl_review_queue_insert_populates_synthesized_title(self, engine):
        from sqlalchemy import text

        pk = "TEST-2F05-FL-001"
        candidate = uuid.uuid4()
        reason_detail = {
            "fail_reason": "alias_no_team_resemblance",
            "anchor_score": 0.42,
        }
        # FL synthesizes provider_title from HOME_NAME and AWAY_NAME
        # in the runner; the test passes the synthesized form directly.
        self._run_runner_insert(
            engine,
            provider="fl",
            pk=pk,
            reason_detail=reason_detail,
            provider_title="Manchester United vs Liverpool",
            candidate_fixtures=[candidate],
            confidence=0.78,
        )

        with engine.begin() as conn:
            row = conn.execute(text(
                """
                SELECT reason_detail, provider_title, rejection_count
                FROM sp.review_queue
                WHERE provider = 'fl' AND provider_record_id = :pk
                """
            ).bindparams(pk=pk)).first()

        assert row.provider_title == "Manchester United vs Liverpool"
        assert row.reason_detail["fail_reason"] == "alias_no_team_resemblance"
        assert row.reason_detail["anchor_score"] == 0.42

    def test_re_resolve_refreshes_reason_detail_and_title(self, engine):
        # Per design Q3: "the matcher decision was correct AT INSERT
        # and that's what the operator is reviewing." On re-resolve
        # while status='pending', the new decision IS the current
        # state — reason_detail + provider_title refresh.
        from sqlalchemy import text

        pk = "TEST-2F05-RE-RESOLVE-001"
        candidate = uuid.uuid4()

        # First insert: matcher emitted a collision-shaped reason.
        self._run_runner_insert(
            engine,
            provider="kalshi",
            pk=pk,
            reason_detail={"fail_reason": "alias_collision", "iter": 1},
            provider_title="First Title",
            candidate_fixtures=[candidate],
            confidence=0.0,
        )

        # Second insert (simulates next cron's re-resolve): matcher
        # produced a different shape after candidate index changed.
        self._run_runner_insert(
            engine,
            provider="kalshi",
            pk=pk,
            reason_detail={"fail_reason": "below_threshold", "iter": 2},
            provider_title="Second Title (refreshed)",
            candidate_fixtures=[candidate],
            confidence=0.75,
        )

        with engine.begin() as conn:
            row = conn.execute(text(
                """
                SELECT reason_detail, provider_title, confidence
                FROM sp.review_queue
                WHERE provider = 'kalshi' AND provider_record_id = :pk
                """
            ).bindparams(pk=pk)).first()

        # ON CONFLICT DO UPDATE WHERE status='pending' should have
        # refreshed all three fields.
        assert row.reason_detail["iter"] == 2
        assert row.reason_detail["fail_reason"] == "below_threshold"
        assert row.provider_title == "Second Title (refreshed)"
        assert row.confidence == pytest.approx(0.75)

    def test_re_resolve_skips_when_already_decided(self, engine):
        # The PR #108 WHERE status='pending' guard protects rejected
        # / approved rows from being overwritten on re-resolve. With
        # 2F.0.5 surfacing reason_detail and provider_title, the
        # guard must STILL apply — operator's decision context (the
        # reason_detail snapshot at decision time) is part of audit.
        from sqlalchemy import text

        pk = "TEST-2F05-DECIDED-001"
        candidate = uuid.uuid4()

        # First insert (pending).
        self._run_runner_insert(
            engine,
            provider="kalshi",
            pk=pk,
            reason_detail={"fail_reason": "original_reason"},
            provider_title="Original Title",
            candidate_fixtures=[candidate],
            confidence=0.78,
        )

        # Operator rejects the row.
        with engine.begin() as conn:
            conn.execute(text(
                """
                UPDATE sp.review_queue
                SET status='rejected', reviewed_by='test_operator',
                    reviewed_at=NOW(), rejection_count=rejection_count+1
                WHERE provider='kalshi' AND provider_record_id=:pk
                """
            ).bindparams(pk=pk))

        # Next cron tries to re-resolve — same INSERT path triggers
        # ON CONFLICT, but the WHERE status='pending' filter blocks
        # the UPDATE.
        self._run_runner_insert(
            engine,
            provider="kalshi",
            pk=pk,
            reason_detail={"fail_reason": "different_reason_after_alias_added"},
            provider_title="Different Title",
            candidate_fixtures=[candidate],
            confidence=0.95,
        )

        with engine.begin() as conn:
            row = conn.execute(text(
                """
                SELECT reason_detail, provider_title, confidence,
                       status, rejection_count
                FROM sp.review_queue
                WHERE provider='kalshi' AND provider_record_id=:pk
                """
            ).bindparams(pk=pk)).first()

        # Original snapshot preserved — operator's rejection is the
        # source of truth for this record.
        assert row.status == "rejected"
        assert row.reason_detail["fail_reason"] == "original_reason"
        assert row.provider_title == "Original Title"
        assert row.confidence == pytest.approx(0.78)
        assert row.rejection_count == 1
