"""Phase 2F.1 sub-PR #4 — tests for scripts/alias_add.py.

Two layers, same as the other 2F.1 test files:
  - TestAliasAddUnit: no DB, pure validation logic + arg parsing.
  - TestAliasAddIntegration: real Postgres roundtrip via
    SP_INTEGRATION_DB. Covers the idempotency contract that's the
    whole point of the script.

The script is a primitive — small, hand-runnable. Tests focus on the
contract the anchor_failed admin surface (and future 2D.5.1 CLI) will
depend on: same args twice = no second write; sport/team typos fail
loudly with helpful messages; normalization is consistent.
"""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
INTEGRATION_DB = os.environ.get("SP_INTEGRATION_DB", "").strip()


# ── Unit-level tests ───────────────────────────────────────────


class TestAliasAddUnit:
    """No DB. Argument parsing, source-set membership, normalization."""

    def test_known_sources_contains_expected_values(self):
        from scripts.alias_add import KNOWN_SOURCES
        # The 2F.1 sub-PR #4 work introduces the manual_anchor_failed
        # value. Existing-by-convention values must stay so audit
        # queries don't break.
        assert "manual_anchor_failed" in KNOWN_SOURCES
        assert "legacy_bootstrap" in KNOWN_SOURCES
        assert "operator_review" in KNOWN_SOURCES
        assert "alias_tier" in KNOWN_SOURCES
        assert "fuzzy_tier" in KNOWN_SOURCES

    def test_default_source_is_manual_anchor_failed(self):
        from scripts.alias_add import DEFAULT_SOURCE
        # The anchor_failed admin surface's clipboard pre-fill relies
        # on this default. Changing it requires updating the
        # admin/templates/anchor_failed_detail.html "Suggest alias"
        # widget in lockstep.
        assert DEFAULT_SOURCE == "manual_anchor_failed"

    def test_arg_parser_requires_sport_team_alias(self):
        from scripts.alias_add import main
        # argparse exits with code 2 on missing required args.
        with pytest.raises(SystemExit) as exc:
            main(["--sport", "tennis"])
        assert exc.value.code == 2

    def test_arg_parser_accepts_full_command_shape(self, monkeypatch):
        # Stub the async work so this stays a pure-argparse test;
        # we just want to confirm the parser accepts the canonical
        # shape the anchor_failed surface will emit on the clipboard.
        called_with = {}

        def fake_run(coro):
            # Walk into the coroutine's locals to see what args came in.
            # Easier: replace add_alias before main() builds the coro.
            return 0

        import scripts.alias_add as mod

        async def fake_add_alias(**kwargs):
            called_with.update(kwargs)
            return 0

        monkeypatch.setattr(mod, "add_alias", fake_add_alias)
        rc = mod.main([
            "--sport", "tennis",
            "--team-canonical", "Jannik Sinner",
            "--alias", "J. Sinner",
        ])
        assert rc == 0
        assert called_with["sport"] == "tennis"
        assert called_with["team_canonical"] == "Jannik Sinner"
        assert called_with["alias"] == "J. Sinner"
        assert called_with["source"] == "manual_anchor_failed"  # default
        assert called_with["dry_run"] is False

    def test_arg_parser_accepts_dry_run_and_custom_source(self, monkeypatch):
        called_with = {}
        import scripts.alias_add as mod

        async def fake_add_alias(**kwargs):
            called_with.update(kwargs)
            return 0

        monkeypatch.setattr(mod, "add_alias", fake_add_alias)
        mod.main([
            "--sport", "tennis",
            "--team-canonical", "Jannik Sinner",
            "--alias", "J. Sinner",
            "--source", "manual_review",
            "--dry-run",
        ])
        assert called_with["source"] == "manual_review"
        assert called_with["dry_run"] is True

    def test_unknown_source_warns_but_proceeds(self, monkeypatch, capsys):
        called = []
        import scripts.alias_add as mod

        async def fake_add_alias(**kwargs):
            called.append(kwargs)
            return 0

        monkeypatch.setattr(mod, "add_alias", fake_add_alias)
        rc = mod.main([
            "--sport", "tennis",
            "--team-canonical", "X",
            "--alias", "Y",
            "--source", "not_a_known_source",
        ])
        assert rc == 0
        assert len(called) == 1
        captured = capsys.readouterr()
        # Warning lands on stderr; the run continues.
        assert "WARNING" in captured.err
        assert "not_a_known_source" in captured.err


# ── Integration tests (require SP_INTEGRATION_DB) ──────────────


@pytest.mark.skipif(
    not INTEGRATION_DB,
    reason=(
        "SP_INTEGRATION_DB not set — alias_add integration tests "
        "require a Postgres URL with sp schema migrations applied."
    ),
)
class TestAliasAddIntegration:
    """End-to-end: seed sport+team → run script → assert persisted
    state matches expectations. Covers the idempotency contract the
    anchor_failed admin surface depends on."""

    TEST_ALIAS_MARKER = "TEST-2F1-SUB4-ALIAS"

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
        # Apply migrations + clean leftover test rows. Cascade through
        # the tables the script touches.
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
        from sqlalchemy import text
        with engine.begin() as conn:
            # Clean any team_aliases rows the test created. Match on
            # alias LIKE pattern so we don't trample production
            # operator_review rows in a shared dev DB.
            conn.execute(text(
                "DELETE FROM sp.team_aliases "
                "WHERE alias LIKE :marker OR alias_normalized LIKE :marker_norm"
            ), {
                "marker": f"%{self.TEST_ALIAS_MARKER}%",
                "marker_norm": f"%{self.TEST_ALIAS_MARKER.lower()}%",
            })

    def _pick_real_team(self, engine):
        """Pick an arbitrary (sport_name, team_canonical) from the
        integration DB. Tests need a real team to alias against; the
        DB is shared with other tests so we don't create our own."""
        from sqlalchemy import text
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT s.name AS sport_name, t.canonical_name, t.id "
                "FROM sp.teams t JOIN sp.sports s ON t.sport_id = s.id "
                "LIMIT 1"
            )).first()
        if row is None:
            pytest.skip("integration DB has no sp.teams rows to alias against")
        return row.sport_name, row.canonical_name, row.id

    def _run_script(self, *args, expect_rc=0):
        """Invoke scripts/alias_add.py as a subprocess. Returns
        (rc, stdout, stderr). Subprocess instead of in-process call
        because the script uses asyncio.run() and module-level
        async_session — the cleanest way to exercise the actual
        `make alias-add` path is to invoke it the same way the
        Makefile does."""
        result = subprocess.run(
            [sys.executable, "scripts/alias_add.py", *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            env={**os.environ, "DATABASE_URL": INTEGRATION_DB},
        )
        assert result.returncode == expect_rc, (
            f"alias_add.py exited {result.returncode} (expected {expect_rc}):\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )
        return result.returncode, result.stdout, result.stderr

    def test_inserts_one_alias_row(self, engine):
        sport, team_name, team_id = self._pick_real_team(engine)
        alias_text = f"{self.TEST_ALIAS_MARKER}-{uuid.uuid4().hex[:8]}"

        _, stdout, _ = self._run_script(
            "--sport", sport,
            "--team-canonical", team_name,
            "--alias", alias_text,
        )
        assert "Inserted" in stdout
        assert alias_text in stdout

        from sqlalchemy import text
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT team_id, source, confidence FROM sp.team_aliases "
                "WHERE alias = :alias"
            ), {"alias": alias_text}).first()
        assert row is not None
        assert row.team_id == team_id
        assert row.source == "manual_anchor_failed"
        assert row.confidence == 1.0

    def test_idempotent_on_second_run(self, engine):
        sport, team_name, team_id = self._pick_real_team(engine)
        alias_text = f"{self.TEST_ALIAS_MARKER}-{uuid.uuid4().hex[:8]}"

        # First run: insert.
        self._run_script(
            "--sport", sport,
            "--team-canonical", team_name,
            "--alias", alias_text,
        )

        # Second run with identical args: must be a no-op.
        _, stdout2, _ = self._run_script(
            "--sport", sport,
            "--team-canonical", team_name,
            "--alias", alias_text,
        )
        assert "Already present" in stdout2

        # Exactly one row exists for this alias_normalized + source pair.
        from sqlalchemy import text
        with engine.connect() as conn:
            count = conn.execute(text(
                "SELECT COUNT(*) FROM sp.team_aliases "
                "WHERE alias = :alias AND source = 'manual_anchor_failed'"
            ), {"alias": alias_text}).scalar()
        assert count == 1

    def test_conflict_refuses_when_same_alias_points_to_different_team(self, engine):
        """If alias_normalized + source already points to a different
        team_id, the script must refuse instead of silently overwriting.
        The UNIQUE constraint would also catch this, but the pre-check
        gives the operator a useful error instead of an IntegrityError."""
        from sqlalchemy import text
        with engine.connect() as conn:
            teams = conn.execute(text(
                "SELECT s.name AS sport_name, t.canonical_name, t.id "
                "FROM sp.teams t JOIN sp.sports s ON t.sport_id = s.id "
                "WHERE s.id = (SELECT sport_id FROM sp.teams "
                "              GROUP BY sport_id ORDER BY COUNT(*) DESC LIMIT 1) "
                "LIMIT 2"
            )).all()
        if len(teams) < 2:
            pytest.skip("integration DB needs >=2 teams in one sport for this test")
        team_a, team_b = teams[0], teams[1]
        alias_text = f"{self.TEST_ALIAS_MARKER}-{uuid.uuid4().hex[:8]}"

        # Point alias at team A.
        self._run_script(
            "--sport", team_a.sport_name,
            "--team-canonical", team_a.canonical_name,
            "--alias", alias_text,
        )

        # Try to point the SAME alias_normalized + default source at team B.
        _, _, stderr = self._run_script(
            "--sport", team_b.sport_name,
            "--team-canonical", team_b.canonical_name,
            "--alias", alias_text,
            expect_rc=1,
        )
        assert "already points to a different team" in stderr

        # Team A's row survives unchanged.
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT team_id FROM sp.team_aliases "
                "WHERE alias = :alias AND source = 'manual_anchor_failed'"
            ), {"alias": alias_text}).first()
        assert row.team_id == team_a.id

    def test_unknown_sport_exits_with_helpful_error(self, engine):
        _, _, stderr = self._run_script(
            "--sport", "not-a-real-sport",
            "--team-canonical", "doesntmatter",
            "--alias", f"{self.TEST_ALIAS_MARKER}-{uuid.uuid4().hex[:8]}",
            expect_rc=1,
        )
        assert "sport not found" in stderr
        assert "Available sports" in stderr

    def test_unknown_team_surfaces_closest_matches(self, engine):
        sport, _, _ = self._pick_real_team(engine)
        _, _, stderr = self._run_script(
            "--sport", sport,
            "--team-canonical", "Definitely Not A Real Team Name XYZ",
            "--alias", f"{self.TEST_ALIAS_MARKER}-{uuid.uuid4().hex[:8]}",
            expect_rc=1,
        )
        assert "team not found" in stderr
        # Closest-3 line is best-effort; trigram extension may not be
        # installed everywhere. Only assert the primary error line.

    def test_dry_run_doesnt_write(self, engine):
        sport, team_name, _ = self._pick_real_team(engine)
        alias_text = f"{self.TEST_ALIAS_MARKER}-{uuid.uuid4().hex[:8]}"

        _, stdout, _ = self._run_script(
            "--sport", sport,
            "--team-canonical", team_name,
            "--alias", alias_text,
            "--dry-run",
        )
        assert "dry-run" in stdout.lower()
        assert "Would insert" in stdout

        from sqlalchemy import text
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT 1 FROM sp.team_aliases WHERE alias = :alias"
            ), {"alias": alias_text}).first()
        assert row is None
