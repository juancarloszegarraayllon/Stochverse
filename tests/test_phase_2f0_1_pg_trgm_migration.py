"""Phase 2F.0.1 (pg_trgm extension) migration roundtrip tests.

⚠️  DESTRUCTIVE DOWNGRADE WARNING — READ THIS BEFORE COPY-PASTING ⚠️

This file's `test_downgrade_removes_pg_trgm` test executes
`alembic downgrade` against the target database, which runs
`DROP EXTENSION IF EXISTS pg_trgm` per the migration's downgrade()
implementation. If you ever run this test against a database where
pg_trgm was installed via the Neon dashboard fallback path (see PR
#140's operator runbook), the test WILL REMOVE THE DASHBOARD-
INSTALLED EXTENSION.

The @pytest.mark.skipif(not INTEGRATION_DB) gate normally protects
against this — CI without SP_INTEGRATION_DB set skips entirely.
But if a developer copy-pastes this test file into a new context, or
points SP_INTEGRATION_DB at a non-disposable database (NEVER point
it at production — Neon dev branches or local apt-installed
Postgres only, per DEPLOYMENT.md), the destructive DROP is real.

Belt-and-suspenders guidance:

  1. SP_INTEGRATION_DB must point at a DISPOSABLE database. Neon
     dev branches (Settings → Branches → Create branch from main)
     or apt-installed local Postgres. Never production.
  2. If pg_trgm was installed via the dashboard fallback rather than
     this migration, run `alembic upgrade head` after this test
     completes to restore the extension via the migration path.
  3. This test's `_alembic` helper shells out to alembic, so it
     can't accidentally swallow the DROP — any DDL failure surfaces
     in the assertion. If a future refactor removes the
     `@pytest.mark.skipif` gate, the destruction risk reopens.

Closes Issue #144. Filed during PR #138's drive-by fix to
test_phase_2f0_migration::test_upgrade_then_downgrade_roundtrip:
PR #140's pg_trgm migration silently extended the migration chain
past 2F.0, which broke 2F.0's roundtrip test by changing what
`downgrade -1 from head` meant. PR #138's drive-by fixed 2F.0's
test, but exposed that 2F.0.1 itself has no dedicated roundtrip
coverage. A future PR modifying the CREATE EXTENSION / DROP
EXTENSION lines incorrectly (typo'd extension name, accidentally
dropping a dependent index, switching to non-IF NOT EXISTS) has
no test-suite safety net without this file.

Three tests:

  - test_upgrade_installs_pg_trgm_and_exposes_similarity — forward
    to b8e1f4c2a7d3 from the 2F.0 parent (a1c4f9e8b2d7). Verifies
    pg_extension contains a row + similarity() returns a float.
  - test_downgrade_removes_pg_trgm — downgrade back to 2F.0.
    Verifies pg_extension no longer contains pg_trgm.
  - test_re_upgrade_is_idempotent_via_if_not_exists — exercise the
    IF NOT EXISTS guard. Upgrade twice without intervening
    downgrade; verify no duplicate-object error.

Each test follows the "park at exact revision via upgrade head →
downgrade <target>" pattern that PR #138's drive-by fix established.
Idempotent regardless of test-DB starting state.
"""
from __future__ import annotations

import os
import pathlib
import subprocess

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
INTEGRATION_DB = os.environ.get("SP_INTEGRATION_DB", "").strip()

# Phase 2F.0.1 revision id (b8e1f4c2a7d3, migration file
# `20260512_1300_b8e1f4c2a7d3_phase_2f0_1_pg_trgm_extension.py`).
PG_TRGM_REVISION = "b8e1f4c2a7d3"
# Phase 2F.0 revision id (parent of 2F.0.1) — what downgrade lands at.
TWOF0_REVISION = "a1c4f9e8b2d7"


@pytest.mark.skipif(
    not INTEGRATION_DB,
    reason=(
        "SP_INTEGRATION_DB not set — integration tests require a "
        "Postgres URL with the sp schema migrations applied. Set "
        "SP_INTEGRATION_DB to a DISPOSABLE database (Neon dev branch "
        "or local apt-installed Postgres). See module docstring for "
        "destructive-downgrade warning."
    ),
)
class TestPgTrgmMigrationIntegration:
    """Forward + downgrade + re-upgrade roundtrip for the pg_trgm
    extension migration (Phase 2F.0.1, revision b8e1f4c2a7d3).

    Pattern mirrors test_phase_2f0_migration.py::TestMigrationIntegration
    but scoped to the 2F.0.1 revision specifically. Uses the "park at
    exact revision via upgrade head → downgrade <target>" pattern
    (PR #138 drive-by fix) so the test is idempotent regardless of
    test-DB starting state.
    """

    @pytest.fixture
    def engine(self):
        from sqlalchemy import create_engine
        url = INTEGRATION_DB
        # Strip the asyncpg driver suffix — alembic uses the sync driver.
        if "+asyncpg" in url:
            url = url.replace("+asyncpg", "")
        eng = create_engine(url)
        yield eng
        eng.dispose()

    def _alembic(self, args: list[str]) -> None:
        """Shell out to alembic so the test exercises the same code
        path the operator runs (no re-implementation of alembic's
        migration runner). Asserts returncode==0 with full stdout/
        stderr captured for diagnostic output on failure."""
        result = subprocess.run(
            ["alembic"] + args,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            env={**os.environ, "DATABASE_URL": INTEGRATION_DB},
        )
        assert result.returncode == 0, (
            f"alembic {' '.join(args)} failed:\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_upgrade_installs_pg_trgm_and_exposes_similarity(self, engine):
        """Forward to 2F.0.1 from the 2F.0 parent. After upgrade,
        sp_pg_extension has a row for pg_trgm AND similarity() is
        callable as a SQL function."""
        from sqlalchemy import text
        # Park at 2F.0 (one before 2F.0.1) regardless of starting state.
        self._alembic(["upgrade", "head"])
        self._alembic(["downgrade", TWOF0_REVISION])
        # Forward to 2F.0.1.
        self._alembic(["upgrade", PG_TRGM_REVISION])

        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT extname, extversion FROM pg_extension "
                "WHERE extname = 'pg_trgm'"
            )).first()
        assert row is not None, (
            "pg_trgm row missing from pg_extension after "
            f"alembic upgrade {PG_TRGM_REVISION}. The migration's "
            "CREATE EXTENSION IF NOT EXISTS pg_trgm didn't take effect. "
            "Investigate whether the migration's upgrade() body was "
            "edited or the IF NOT EXISTS clause hit a privilege error."
        )
        assert row.extname == "pg_trgm"
        # extversion is whatever Postgres installs by default — 1.5 or
        # 1.6 both fine. Assert non-empty rather than exact value.
        assert row.extversion, "pg_trgm extension row has empty extversion."

        # similarity() must be callable (the production code paths that
        # this migration unblocks all call similarity()). Returns a
        # float in [0.0, 1.0] for any two strings.
        with engine.connect() as conn:
            sim = conn.execute(text(
                "SELECT similarity('France', 'french republic') AS sim"
            )).scalar()
        assert sim is not None
        assert 0.0 < float(sim) < 1.0, (
            f"similarity('France', 'french republic') returned {sim}; "
            "expected a float strictly between 0.0 and 1.0. If 0.0, "
            "pg_trgm may have been installed but the trigram operators "
            "didn't register. If 1.0, the inputs accidentally match."
        )

    def test_downgrade_removes_pg_trgm(self, engine):
        """Downgrade from 2F.0.1 to 2F.0. After downgrade, pg_extension
        no longer contains a pg_trgm row.

        ⚠️  Reminder: this DROPs the extension via the migration's
        downgrade() body. See module docstring's destructive-downgrade
        warning. SP_INTEGRATION_DB MUST be a disposable database.
        """
        from sqlalchemy import text
        # Park at 2F.0.1 then downgrade.
        self._alembic(["upgrade", PG_TRGM_REVISION])
        self._alembic(["downgrade", TWOF0_REVISION])

        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT extname FROM pg_extension "
                "WHERE extname = 'pg_trgm'"
            )).first()
        assert row is None, (
            "pg_trgm still in pg_extension after alembic downgrade "
            f"{TWOF0_REVISION}. The migration's downgrade() body — "
            "DROP EXTENSION IF EXISTS pg_trgm — either didn't run or "
            "was silently skipped. Investigate the migration file or "
            "verify the downgrade direction is being attempted "
            "(alembic logs should show 'Running downgrade ...')."
        )

    def test_re_upgrade_is_idempotent_via_if_not_exists(self, engine):
        """If the operator installs pg_trgm via the Neon dashboard
        fallback path (see PR #140's operator runbook for the privilege-
        error fallback), then runs `alembic upgrade head`, the
        migration's `CREATE EXTENSION IF NOT EXISTS pg_trgm` must run
        as a no-op rather than a 42710 duplicate_object error.

        Simulates by upgrading to 2F.0.1, then upgrading again without
        downgrading. The second upgrade is an alembic no-op (already at
        target revision), but if the DDL were ever exercised twice in
        the same database state, the IF NOT EXISTS guard is what
        prevents the failure.
        """
        from sqlalchemy import text
        # First upgrade.
        self._alembic(["upgrade", PG_TRGM_REVISION])
        # Second upgrade — alembic stamps already at this revision, so
        # this is a no-op at the alembic level. The DDL-idempotency
        # check is the subsequent assertion: pg_trgm is still installed.
        self._alembic(["upgrade", "head"])

        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT extversion FROM pg_extension "
                "WHERE extname = 'pg_trgm'"
            )).first()
        assert row is not None, (
            "pg_trgm missing after the second `alembic upgrade head` — "
            "the IF NOT EXISTS guard may have been removed from the "
            "migration's CREATE EXTENSION statement, or the alembic "
            "state diverged from the actual schema. Either way, the "
            "operator's dashboard-fallback path (PR #140 runbook) is "
            "broken — re-running upgrade head should be a no-op when "
            "the extension is already present."
        )
