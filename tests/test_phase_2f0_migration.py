"""Phase 2F.0 schema migration tests.

Per PHASE_2F_DESIGN.md rev1.1 (PR #112). The migration adds three
columns and one partial index to sp.review_queue:

  - reason_detail   JSONB    NULL
  - provider_title  TEXT     NULL
  - rejection_count INTEGER  NOT NULL DEFAULT 0

  ix_review_queue_pending_confidence ON (status, confidence DESC, created_at)
    WHERE status = 'pending'

Two test layers:

1. Static tests — parse the migration file, assert structure. No DB
   required; runs unconditionally in CI. Catches typos, accidental
   API changes (e.g., dropping the partial-index `WHERE status =
   'pending'` clause), missing downgrade ops.

2. Integration tests — run the migration against a real Postgres
   instance, reflect the schema, verify the columns + index land.
   Skipped unless SP_INTEGRATION_DB is set (matches the convention
   used by tests/test_resolver_2b.py).
"""
from __future__ import annotations

import importlib.util
import inspect
import os
import pathlib
import re

import pytest


# ── Shared paths ───────────────────────────────────────────────


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
MIGRATION_PATH = (
    REPO_ROOT
    / "migrations"
    / "versions"
    / "20260510_1800_a1c4f9e8b2d7_phase_2f0_review_queue_columns.py"
)


def _load_migration_module():
    """Import the 2F.0 migration file as a module."""
    spec = importlib.util.spec_from_file_location(
        "phase_2f0_migration", MIGRATION_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── Static tests (no DB) ───────────────────────────────────────


class TestMigrationStaticShape:
    """Static parse + structural assertions on the migration file."""

    def setup_method(self):
        self.module = _load_migration_module()
        self.source = MIGRATION_PATH.read_text()

    def test_revision_id_matches_filename(self):
        # Filename includes the revision id; mismatched values would
        # confuse alembic's history walk.
        assert self.module.revision == "a1c4f9e8b2d7"

    def test_down_revision_points_to_phase_2a7(self):
        # Phase 2A.7 (sp.fl_events.sport_id) was the prior head before
        # 2F.0 lands. Walking history from a1c4f9e8b2d7 must reach it.
        assert self.module.down_revision == "7c3f9b1a2e58"

    def test_upgrade_and_downgrade_are_callables(self):
        assert callable(self.module.upgrade)
        assert callable(self.module.downgrade)

    def test_upgrade_adds_three_columns(self):
        # Walk the upgrade function source for op.add_column calls
        # for each of the three expected columns.
        src = inspect.getsource(self.module.upgrade)
        for col in ("reason_detail", "provider_title", "rejection_count"):
            assert f'"{col}"' in src, (
                f"upgrade() must add the {col!r} column to sp.review_queue."
            )

    def test_rejection_count_is_not_null_with_default(self):
        # Per Q4 revised: existing 2,263 rows backfill via server_default,
        # so the column ships NOT NULL from day one. A nullable column
        # would let inserts skip the count entirely; a missing default
        # would block the migration on existing rows.
        #
        # Locate the rejection_count line and walk forward through the
        # enclosing sa.Column(...) call by matching parens. Captures
        # the full Column kwargs even though sa.Integer() introduces
        # a nested ()-pair.
        src = inspect.getsource(self.module.upgrade)
        anchor = src.index('"rejection_count"')
        # Walk back to the opening "sa.Column(" before this line.
        col_start = src.rindex("sa.Column(", 0, anchor)
        # Walk forward, tracking paren depth, until depth returns to 0.
        depth = 0
        i = col_start
        while i < len(src):
            if src[i] == "(":
                depth += 1
            elif src[i] == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        col_def = src[col_start:i + 1]
        assert "nullable=False" in col_def, (
            "rejection_count must be NOT NULL — operator-burnout guardrail "
            "depends on every row having a tracked count."
        )
        assert "server_default" in col_def, (
            "rejection_count needs a server_default so the migration "
            "backfills existing rows without a separate UPDATE pass."
        )

    def test_partial_index_filtered_to_pending(self):
        # The 2F.1 list view's hot query is WHERE status='pending'.
        # If the WHERE clause drops, the index bloats with decided
        # rows and stops covering the actual query plan.
        src = inspect.getsource(self.module.upgrade)
        assert "ix_review_queue_pending_confidence" in src
        assert "postgresql_where" in src
        assert "status = 'pending'" in src

    def test_partial_index_orders_confidence_desc(self):
        # The list view sorts by confidence DESC. Without DESC in the
        # index definition, Postgres sorts at query time.
        src = inspect.getsource(self.module.upgrade)
        assert 'sa.text("confidence DESC")' in src, (
            "Partial index must order by confidence DESC to match the "
            "2F.1 list-view query plan."
        )

    def test_downgrade_drops_in_reverse_order(self):
        # Downgrade should drop the index BEFORE the columns it
        # depends on. Postgres also auto-drops indexes when a column
        # they reference goes away, but explicit ordering is safer
        # and aids review.
        src = inspect.getsource(self.module.downgrade)
        idx_pos = src.find("drop_index")
        col_pos = src.find('drop_column("review_queue", "rejection_count"')
        assert idx_pos >= 0 and col_pos >= 0
        assert idx_pos < col_pos, (
            "Downgrade should drop the index before dropping columns."
        )

    def test_downgrade_drops_all_three_columns(self):
        src = inspect.getsource(self.module.downgrade)
        for col in ("reason_detail", "provider_title", "rejection_count"):
            assert f'"{col}"' in src, (
                f"downgrade() must drop the {col!r} column."
            )

    def test_uses_jsonb_dialect_type(self):
        # Generic sa.JSON would silently demote to TEXT on Postgres
        # 9.x; we use the postgresql.JSONB dialect type explicitly so
        # the column gets the right binary representation + GIN
        # indexability (future-proofing for 2F.X reason_detail queries).
        assert "from sqlalchemy.dialects.postgresql import JSONB" in self.source
        assert "JSONB()" in self.source


class TestReviewQueueOrmKeptInSync:
    """The ORM in sp_models.py must reflect the schema the migration
    produces. Drift between the two confuses readers and breaks any
    code that uses the ORM for reads (admin UI, day-7 queries).
    """

    def test_orm_has_new_columns(self):
        from sp_models import ReviewQueue
        col_names = {c.name for c in ReviewQueue.__table__.columns}
        assert "reason_detail" in col_names
        assert "provider_title" in col_names
        assert "rejection_count" in col_names

    def test_orm_rejection_count_default_is_zero(self):
        from sp_models import ReviewQueue
        col = ReviewQueue.__table__.columns["rejection_count"]
        assert col.nullable is False
        # server_default keeps the migration online for existing rows.
        assert col.server_default is not None

    def test_orm_partial_index_present(self):
        from sp_models import ReviewQueue
        idx_names = {i.name for i in ReviewQueue.__table__.indexes}
        assert "ix_review_queue_pending_confidence" in idx_names


# ── Integration tests (gated on SP_INTEGRATION_DB) ─────────────


INTEGRATION_DB = os.environ.get("SP_INTEGRATION_DB", "").strip()


@pytest.mark.skipif(
    not INTEGRATION_DB,
    reason=(
        "SP_INTEGRATION_DB not set — integration tests require a "
        "Postgres URL with the sp schema migration applied."
    ),
)
class TestMigrationIntegration:
    """Forward + downgrade against a real Postgres instance.

    Requires SP_INTEGRATION_DB pointed at a disposable database
    (e.g., a Neon branch, a docker-compose Postgres). The fixtures
    apply the migration to head, verify the schema, then downgrade
    one step and verify the columns + index are gone.
    """

    @pytest.fixture
    def engine(self):
        from sqlalchemy import create_engine
        url = INTEGRATION_DB
        # Strip the asyncpg driver suffix if present — alembic uses sync.
        if "+asyncpg" in url:
            url = url.replace("+asyncpg", "")
        eng = create_engine(url)
        yield eng
        eng.dispose()

    def _alembic(self, args: list[str]) -> None:
        """Shell out to alembic so the test exercises the same code path
        the operator runs. Avoids re-implementing alembic's runner."""
        import subprocess
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

    def test_upgrade_then_downgrade_roundtrip(self, engine):
        from sqlalchemy import inspect as sa_inspect

        # Park the DB at exactly the 2F.0 revision regardless of
        # starting state. This test is scoped to the 2F.0 migration's
        # roundtrip behavior; using "head" silently broke when PR #140
        # (2F.0.1 pg_trgm) added a subsequent migration, since
        # `downgrade -1` from head no longer rolls back 2F.0 — it
        # rolls back only 2F.0.1.
        #
        # Two-step "go to revision" pattern (idempotent regardless of
        # current state):
        #   1. upgrade head — deterministic: lands at head no matter
        #      where we started.
        #   2. downgrade a1c4f9e8b2d7 — deterministic: lands at 2F.0
        #      from any state at-or-past 2F.0.
        # Note that `alembic upgrade <past-revision>` is a silent no-op,
        # which is what broke the previous shape when prior tests in
        # the same session left the DB at head.
        TWOF0_REVISION = "a1c4f9e8b2d7"
        self._alembic(["upgrade", "head"])
        self._alembic(["downgrade", TWOF0_REVISION])

        insp = sa_inspect(engine)
        cols = {c["name"] for c in insp.get_columns("review_queue", schema="sp")}
        assert "reason_detail" in cols
        assert "provider_title" in cols
        assert "rejection_count" in cols

        idxs = {i["name"] for i in insp.get_indexes("review_queue", schema="sp")}
        assert "ix_review_queue_pending_confidence" in idxs

        # Downgrade one step from 2F.0 and verify the new columns +
        # index are gone. The other (pre-2F.0) review_queue columns
        # must still be present.
        self._alembic(["downgrade", "-1"])

        insp = sa_inspect(engine)
        cols_after = {c["name"] for c in insp.get_columns("review_queue", schema="sp")}
        assert "reason_detail" not in cols_after
        assert "provider_title" not in cols_after
        assert "rejection_count" not in cols_after
        # Pre-existing columns must survive the downgrade.
        for col in ("provider", "provider_record_id", "candidate_fixtures",
                    "confidence", "status"):
            assert col in cols_after, (
                f"Downgrade dropped {col!r} — must only remove 2F.0 additions."
            )

        idxs_after = {i["name"] for i in insp.get_indexes("review_queue", schema="sp")}
        assert "ix_review_queue_pending_confidence" not in idxs_after

        # Re-upgrade so the test DB ends in head state for any
        # follow-up tests in the same session.
        self._alembic(["upgrade", "head"])
