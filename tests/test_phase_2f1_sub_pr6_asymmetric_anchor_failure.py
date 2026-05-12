"""Phase 2F.1 sub-PR #6 — asymmetric anchor failure regression tests.

Closes Issue #143: when a `fuzzy_no_team_resemblance` record has one
side anchored cleanly and the other side anchored-failed with no
above-threshold candidates, the Suggest-alias panel must render BOTH
sides — the anchored side with its candidate buttons (existing
behaviour), the failed side with a per-side stub `make alias-add`
clipboard widget (NEW).

Pre-PR-#143: the template's `ok`-state branch iterates `["home",
"away"]` and conditionally renders only sides whose `sugg.candidates`
is non-empty. Asymmetric records (dominant shape per production
smoke testing of PRs #137/#138) had the failed side silently omitted.

Post-PR-#143: failed sides surface with a per-side Path B-style stub
widget. The widget's `data-alias` and the visible `<pre>` command
both pre-fill from `parsed_name` (the matcher's parsed canonical for
that side, now reliably present in `reason_detail` post-PR #138).
`--team-canonical ''` is the explicit empty-string signal that the
operator must type the canonical themselves; `--alias '<parsed>'`
gives the operator a real starting command rather than a blank stub.

Static guard test pins the `--alias` pre-fill behaviour against
regression — see test_asymmetric_failed_side_stub_pre_fills_alias.

Tests are integration-level (real Postgres). They mirror the shape
of `tests/test_phase_2f1_admin_anchor_failed_empty_candidates.py`
(PR #137's scaffold) — same fixture helpers, same SP_INTEGRATION_DB
gating, same login flow.
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
_TEST_MARKER = "TEST-2F1-SUB6-ASYM"


@pytest.mark.skipif(
    not INTEGRATION_DB,
    reason="SP_INTEGRATION_DB not set — asymmetric anchor failure tests need real Postgres.",
)
class TestAsymmetricAnchorFailureRendering:
    """When one side has above-threshold candidates and the other side
    has zero, the detail view must render BOTH sides — anchored side
    with candidate buttons, failed side with stub clipboard widget."""

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
            # sp.teams cleanup — only delete the rows this test inserted.
            conn.execute(text(
                "DELETE FROM sp.teams "
                "WHERE canonical_name LIKE :marker"
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
                  reason_detail: dict, fail_reason: str = "fuzzy_no_team_resemblance"):
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
                "        'fuzzy@2d.0', NOW())"
            ), {"run_id": run_id, "pk": pk, "rd": json.dumps(rd)})

    def _seed_one_basketball_team(self, engine, canonical_name: str) -> uuid.UUID:
        """Insert exactly one Basketball team that the anchored side's
        parsed name will match against via trigram similarity."""
        from sqlalchemy import text
        team_id = uuid.uuid4()
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO sp.teams "
                "(id, sport_id, canonical_name, normalized_name, country_code) "
                "SELECT :id, s.id, :canonical, :normalized, 'US' "
                "FROM sp.sports s WHERE s.name = 'Basketball'"
            ), {
                "id": team_id,
                "canonical": canonical_name,
                "normalized": canonical_name.lower(),
            })
        return team_id

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

    # ── Asymmetric anchor failure tests ────────────────────────

    def test_asymmetric_anchor_failure_renders_both_sides(self, engine, app):
        """One side has a matching canonical in sp.teams (anchors);
        the other side has no matching canonical (fails). Page must
        render BOTH sides — anchored side with candidate buttons,
        failed side with stub clipboard widget."""
        # Seed one Basketball team — the anchored side's parsed name
        # will match this via trigram similarity (>= 0.30).
        anchored_canonical = f"{_TEST_MARKER}-TeamAlpha"
        self._seed_one_basketball_team(engine, anchored_canonical)

        run_id = self._seed_run(engine)
        pk = f"{_TEST_MARKER}-ASYM"
        # Home side parsed name resembles the seeded team (anchors).
        # Away side parsed name has no match in sp.teams Basketball roster.
        self._seed_log(engine, run_id=run_id, pk=pk, reason_detail={
            "sport": "Basketball",
            "sport_id": 7,  # placeholder; helper looks up real id
            "home_provider_normalized": f"{_TEST_MARKER}-TeamAlpha",
            "away_provider_normalized": f"{_TEST_MARKER}-NoMatchTeam",
        })

        resp = app.get(f"/admin/anchor-failed/kalshi/{pk}")
        assert resp.status_code == 200
        body = resp.text

        # Wrong-message guard: the sub-PR #4 "Matcher didn't classify a
        # sport" string must NOT appear (sport IS classified).
        assert "didn't classify a sport" not in body.lower()

        # Anchored side: candidate button must render with the seeded
        # canonical name pre-filled.
        assert anchored_canonical in body
        assert "copy-alias-cmd" in body, (
            "Sub-PR #6 contract: anchored side must still render "
            "candidate buttons (existing ok-state behavior)."
        )

        # Failed side: parsed name must be visible in the rendered
        # page (regression check — pre-#6 silently omitted it).
        assert f"{_TEST_MARKER}-NoMatchTeam" in body, (
            "Sub-PR #6 contract: failed side's parsed name must be "
            "visible. Pre-PR-#6 the template silently omitted sides "
            "with empty candidates lists."
        )

        # Failed side: stub make alias-add command must render with
        # parsed name pre-filled into --alias.
        assert "make alias-add" in body
        # The visible <pre> command for the failed side. Match against
        # the actual command shape with --team-canonical '' empty and
        # --alias filled.
        assert "--team-canonical ''" in body or "--team-canonical ' '" in body or \
               "--team-canonical \"\"" in body, (
            "Sub-PR #6 contract: failed side's stub command must "
            "leave --team-canonical empty for the operator to fill in."
        )

    def test_asymmetric_failed_side_stub_pre_fills_alias(self, engine, app):
        """STATIC GUARD: when a side has parsed_name="X" and zero
        candidates, the stub clipboard widget's --alias value must be
        'X', NOT '' (blank). This is the whole point of the per-side
        fix — the parsed name IS available for the failed side
        post-PR #138; pre-filling --alias is what makes the widget
        actionable. Regression here would silently degrade the fix
        back to "operator types two values manually."

        Pre-PR-#138, the failed side wouldn't have a parsed name to
        pre-fill (it was dropped from reason_detail). Now that #138
        preserves it, the stub MUST surface it. Guard the contract.
        """
        anchored_canonical = f"{_TEST_MARKER}-AnchoredTeam"
        self._seed_one_basketball_team(engine, anchored_canonical)

        run_id = self._seed_run(engine)
        pk = f"{_TEST_MARKER}-PREFILL"
        # Distinctive parsed name on the failed side so we can assert
        # the EXACT string is in the rendered output.
        failed_parsed_name = f"{_TEST_MARKER}-DistinctParsedName"
        self._seed_log(engine, run_id=run_id, pk=pk, reason_detail={
            "sport": "Basketball",
            "home_provider_normalized": anchored_canonical,
            "away_provider_normalized": failed_parsed_name,
        })

        resp = app.get(f"/admin/anchor-failed/kalshi/{pk}")
        assert resp.status_code == 200
        body = resp.text

        # The parsed name must appear in the response. (Necessary
        # precondition; pinned separately by the per-side render test.)
        assert failed_parsed_name in body

        # Locate the failed side's button block. The button's
        # data-alias attribute must equal the parsed name, NOT ''.
        # The data-team attribute must be '' (operator types it).
        # Both shapes are valid:
        #   data-alias="<parsed>"
        #   data-alias='<parsed>'
        # We don't care about quote style; we DO care that the
        # parsed name is the data-alias value.
        # Static guard: search for the data-alias-equals-blank
        # anti-pattern AND assert it's not paired with this parsed
        # name (i.e. no Path B-style stub for THIS side has a blank
        # --alias).
        # Substring search is sufficient — the parsed name is
        # _TEST_MARKER-prefixed and unique to this side.
        assert (
            f'data-alias="{failed_parsed_name}"' in body
            or f"data-alias='{failed_parsed_name}'" in body
        ), (
            "Sub-PR #6 STATIC GUARD: failed side's clipboard button "
            f"must pre-fill data-alias with the parsed name "
            f"({failed_parsed_name!r}), not blank. The parsed name "
            "is available in reason_detail post-PR #138; the whole "
            "point of the per-side fix is to surface it as a real "
            "stub command rather than a blank --alias."
        )

        # The visible <pre> command must also contain --alias '<parsed>',
        # not --alias ''. Pin both surfaces (button data-attr + pre block)
        # because future template refactors might keep one and break
        # the other.
        assert f"--alias '{failed_parsed_name}'" in body, (
            "Sub-PR #6 STATIC GUARD: failed side's visible <pre> "
            f"command must include --alias '{failed_parsed_name}' "
            "(NOT --alias '' or --alias \"<blank>\"). The operator "
            "copies this command and runs it; a blank --alias would "
            "require them to type two values manually instead of one."
        )
