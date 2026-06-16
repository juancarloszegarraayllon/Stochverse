"""Tennis cross-format dedup — unit tests + integration test stubs.

Pure-function tests (no DB required) test the classifiers, union-find,
tiebreaker, and Phase A criterion check.

Integration tests (SP_INTEGRATION_DB-gated) test merge_cluster
transaction isolation, FK cascade, JSONB rewrite, audit row capture,
and rollback.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest


# ══════════════════════════════════════════════════════════════
# Name-format classification (Class F / Class S / Unclassified)
# ══════════════════════════════════════════════════════════════


class TestClassifyNameFormat:
    """Per scope-doc §4.2 step 4: Class F = "Firstname Surname",
    Class S = "Surname I." / "Surname I. (Country)"."""

    def test_class_f_two_token_full_names(self):
        from scripts.tennis_dedup import classify_name_format, NameFormat
        for name in [
            "Carlos Alcaraz",
            "Hyeon Chung",
            "Novak Djokovic",
            "Naomi Osaka",
            "Iga Swiatek",
        ]:
            assert classify_name_format(name) == NameFormat.CLASS_F, (
                f"{name!r} should be Class F"
            )

    def test_class_s_initial_format(self):
        from scripts.tennis_dedup import classify_name_format, NameFormat
        for name in [
            "Chung H.",
            "Alcaraz C.",
            "Djokovic N.",
            "Osaka N.",
        ]:
            assert classify_name_format(name) == NameFormat.CLASS_S, (
                f"{name!r} should be Class S"
            )

    def test_class_s_with_country_code(self):
        from scripts.tennis_dedup import classify_name_format, NameFormat
        for name in [
            "Chung H. (Kor)",
            "Alcaraz C. (Esp)",
            "Chen Y. (Chn)",
            "Shin M. (Uzb)",
            "Pereira T. (Por)",
        ]:
            assert classify_name_format(name) == NameFormat.CLASS_S, (
                f"{name!r} should be Class S"
            )

    def test_three_token_names_unclassified(self):
        """3+ token names route to Population C (Phase B). Per
        scope-doc adjustment 2: multi-token surname complexity
        needs human judgment."""
        from scripts.tennis_dedup import classify_name_format, NameFormat
        for name in [
            "Carlos Alcaraz Garfia",
            "Pucinelli de Almeida M.",
            "Su-jeong Jang",
            "Jean-Luc Picard",
        ]:
            assert classify_name_format(name) == NameFormat.UNCLASSIFIED, (
                f"{name!r} should be UNCLASSIFIED (3+ tokens → Phase B)"
            )

    def test_empty_and_whitespace_unclassified(self):
        from scripts.tennis_dedup import classify_name_format, NameFormat
        assert classify_name_format("") == NameFormat.UNCLASSIFIED
        assert classify_name_format("   ") == NameFormat.UNCLASSIFIED

    def test_single_token_unclassified(self):
        from scripts.tennis_dedup import classify_name_format, NameFormat
        assert classify_name_format("Djokovic") == NameFormat.UNCLASSIFIED

    def test_diacritics_handled(self):
        """Names with accents/diacritics should still classify correctly."""
        from scripts.tennis_dedup import classify_name_format, NameFormat
        assert classify_name_format("Jiří Lehečka") == NameFormat.CLASS_F
        assert classify_name_format("Lehečka J.") == NameFormat.CLASS_S


# ══════════════════════════════════════════════════════════════
# Pairwise format-match (F8 conditions 4+5)
# ══════════════════════════════════════════════════════════════


class TestFormatMatch:
    """Per scope-doc F8 conditions 4+5: firstname-initial alignment +
    surname-token match."""

    def test_matching_pair(self):
        from scripts.tennis_dedup import format_match
        assert format_match("Carlos Alcaraz", "Alcaraz C.") is True
        assert format_match("Hyeon Chung", "Chung H. (Kor)") is True
        assert format_match("Naomi Osaka", "Osaka N.") is True

    def test_initial_mismatch(self):
        """Different initial → different player, not a dupe."""
        from scripts.tennis_dedup import format_match
        assert format_match("Carlos Alcaraz", "Alcaraz J.") is False

    def test_surname_mismatch(self):
        """Different surname → different player."""
        from scripts.tennis_dedup import format_match
        assert format_match("Carlos Alcaraz", "Garfia C.") is False

    def test_case_insensitive(self):
        from scripts.tennis_dedup import format_match
        assert format_match("carlos alcaraz", "Alcaraz C.") is True
        assert format_match("HYEON CHUNG", "chung H. (Kor)") is True

    def test_with_country_code(self):
        from scripts.tennis_dedup import format_match
        assert format_match("Tiago Torres", "Torres T. (Por)") is True

    def test_short_inputs_return_false(self):
        from scripts.tennis_dedup import format_match
        assert format_match("Alcaraz", "Alcaraz C.") is False
        assert format_match("Carlos Alcaraz", "C.") is False


# ══════════════════════════════════════════════════════════════
# Union-find cluster assembly
# ══════════════════════════════════════════════════════════════


class TestBuildClusters:

    def test_disjoint_pairs(self):
        from scripts.tennis_dedup import build_clusters
        pairs = [("a", "b"), ("c", "d")]
        clusters = build_clusters(pairs)
        assert len(clusters) == 2
        assert {"a", "b"} in clusters
        assert {"c", "d"} in clusters

    def test_connected_pairs_merge(self):
        from scripts.tennis_dedup import build_clusters
        pairs = [("a", "b"), ("b", "c"), ("d", "e")]
        clusters = build_clusters(pairs)
        assert len(clusters) == 2
        assert {"a", "b", "c"} in clusters
        assert {"d", "e"} in clusters

    def test_single_large_cluster(self):
        from scripts.tennis_dedup import build_clusters
        pairs = [("a", "b"), ("b", "c"), ("c", "d"), ("d", "e")]
        clusters = build_clusters(pairs)
        assert len(clusters) == 1
        assert clusters[0] == {"a", "b", "c", "d", "e"}

    def test_empty_input(self):
        from scripts.tennis_dedup import build_clusters
        assert build_clusters([]) == []

    def test_duplicate_pairs_idempotent(self):
        from scripts.tennis_dedup import build_clusters
        pairs = [("a", "b"), ("a", "b"), ("b", "a")]
        clusters = build_clusters(pairs)
        assert len(clusters) == 1
        assert clusters[0] == {"a", "b"}


# ══════════════════════════════════════════════════════════════
# F1 tiebreaker logic
# ══════════════════════════════════════════════════════════════


class TestPickCanonical:

    def _row(self, team_id, name, created_at, alias_count=0):
        from scripts.tennis_dedup import TeamRow
        return TeamRow(
            team_id=team_id,
            canonical_name=name,
            created_at=created_at,
            alias_count=alias_count,
        )

    def test_older_created_at_wins(self):
        from scripts.tennis_dedup import pick_canonical
        old = self._row("a", "Carlos Alcaraz",
                         datetime(2026, 1, 1, tzinfo=timezone.utc))
        new = self._row("b", "Alcaraz C.",
                         datetime(2026, 5, 1, tzinfo=timezone.utc))
        assert pick_canonical([old, new]).team_id == "a"

    def test_more_aliases_tiebreaker(self):
        from scripts.tennis_dedup import pick_canonical
        same_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
        fewer = self._row("a", "Carlos Alcaraz", same_time, alias_count=2)
        more = self._row("b", "Alcaraz C.", same_time, alias_count=5)
        assert pick_canonical([fewer, more]).team_id == "b"

    def test_longer_name_tiebreaker(self):
        from scripts.tennis_dedup import pick_canonical
        same_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
        short = self._row("a", "C. Alcaraz", same_time, alias_count=3)
        long = self._row("b", "Carlos Alcaraz", same_time, alias_count=3)
        assert pick_canonical([short, long]).team_id == "b"

    def test_single_member(self):
        from scripts.tennis_dedup import pick_canonical
        only = self._row("a", "Alcaraz", datetime(2026, 1, 1, tzinfo=timezone.utc))
        assert pick_canonical([only]).team_id == "a"

    def test_empty_raises(self):
        from scripts.tennis_dedup import pick_canonical
        with pytest.raises(ValueError):
            pick_canonical([])


# ══════════════════════════════════════════════════════════════
# Cluster partitioning (Phase A criterion)
# ══════════════════════════════════════════════════════════════


class TestPartitionCluster:

    def _row(self, team_id, name, created_at=None, alias_count=0):
        from scripts.tennis_dedup import TeamRow
        return TeamRow(
            team_id=team_id,
            canonical_name=name,
            created_at=created_at or datetime(2026, 1, 1, tzinfo=timezone.utc),
            alias_count=alias_count,
        )

    def test_valid_phase_a_cluster(self):
        """Clean 2-member cluster: 1 Class F + 1 Class S, matching."""
        from scripts.tennis_dedup import partition_cluster
        members = [
            self._row("a", "Carlos Alcaraz",
                      datetime(2026, 1, 1, tzinfo=timezone.utc)),
            self._row("b", "Alcaraz C. (Esp)",
                      datetime(2026, 5, 1, tzinfo=timezone.utc)),
        ]
        mg = partition_cluster(members, shared_records=50)
        assert mg is not None
        assert mg.canonical.team_id == "a"  # older created_at
        assert len(mg.dupes) == 1
        assert mg.dupes[0].team_id == "b"

    def test_cluster_too_large(self):
        from scripts.tennis_dedup import partition_cluster
        members = [
            self._row("a", "Carlos Alcaraz"),
            self._row("b", "Alcaraz C."),
            self._row("c", "Alcaraz C. (Esp)"),
            self._row("d", "Alcaraz C. (Fra)"),
            self._row("e", "Alcaraz C. (Ger)"),
        ]
        assert partition_cluster(members, shared_records=50) is None

    def test_insufficient_shared_records(self):
        from scripts.tennis_dedup import partition_cluster
        members = [
            self._row("a", "Carlos Alcaraz"),
            self._row("b", "Alcaraz C."),
        ]
        assert partition_cluster(members, shared_records=3) is None

    def test_no_class_f_member(self):
        """All Class S → common-surname false positive (Population B)."""
        from scripts.tennis_dedup import partition_cluster
        members = [
            self._row("a", "Chen Y."),
            self._row("b", "Chen M."),
            self._row("c", "Chen C."),
        ]
        assert partition_cluster(members, shared_records=50) is None

    def test_initial_mismatch_rejects(self):
        """Class F + Class S but initials don't align."""
        from scripts.tennis_dedup import partition_cluster
        members = [
            self._row("a", "Carlos Alcaraz"),
            self._row("b", "Alcaraz J."),  # J != C
        ]
        assert partition_cluster(members, shared_records=50) is None

    def test_surname_mismatch_rejects(self):
        from scripts.tennis_dedup import partition_cluster
        members = [
            self._row("a", "Carlos Alcaraz"),
            self._row("b", "Garfia C."),  # different surname
        ]
        assert partition_cluster(members, shared_records=50) is None

    def test_mixed_cluster_with_unclassified(self):
        """Cluster with 1 F + 1 S + 1 unclassified. The unclassified
        member is ignored for conditions 3-5; the F+S pair merges."""
        from scripts.tennis_dedup import partition_cluster
        members = [
            self._row("a", "Carlos Alcaraz"),
            self._row("b", "Alcaraz C."),
            self._row("c", "Carlos Alcaraz Garfia"),  # 3-token → unclassified
        ]
        mg = partition_cluster(members, shared_records=50)
        assert mg is not None
        assert mg.canonical.team_id == "a"
        assert len(mg.dupes) == 1
        assert mg.dupes[0].team_id == "b"

    def test_canonical_selection_uses_f1_tiebreaker(self):
        """The canonical is selected per F1: older created_at wins."""
        from scripts.tennis_dedup import partition_cluster
        members = [
            self._row("a", "Alcaraz C.", datetime(2026, 5, 1, tzinfo=timezone.utc)),
            self._row("b", "Carlos Alcaraz", datetime(2026, 1, 1, tzinfo=timezone.utc)),
        ]
        mg = partition_cluster(members, shared_records=50)
        assert mg is not None
        assert mg.canonical.team_id == "b"  # older
        assert mg.dupes[0].team_id == "a"


# ══════════════════════════════════════════════════════════════
# CLI argument validation (no DB required)
# ══════════════════════════════════════════════════════════════


class TestCLIArgValidation:
    """CLI argument parsing — validates error handling without
    needing a database connection."""

    def test_no_action_specified_exits_2(self):
        from scripts.tennis_dedup import main
        rc = main(["--phase", "a"])
        assert rc == 2

    def test_phase_b_not_implemented_exits_2(self):
        from scripts.tennis_dedup import main
        rc = main(["--phase", "b", "--dry-run"])
        assert rc == 2

    def test_rollback_without_audit_id_exits_2(self):
        from scripts.tennis_dedup import main
        rc = main(["--rollback"])
        assert rc == 2

    def test_apply_without_db_exits_1(self):
        """--apply without DATABASE_URL exits 1 (DB unavailable).
        Replaces the former apply-guard test — rollback is now
        implemented, so --apply is unblocked."""
        from scripts.tennis_dedup import main
        rc = main(["--phase", "a", "--apply"])
        assert rc == 1

    def test_window_days_flag_accepted(self):
        """--window-days flag is accepted by argparse without error.
        Actual propagation tested via integration tests."""
        from scripts.tennis_dedup import main
        # Without DB, exits 1 (DB unavailable) after parsing args
        rc = main(["--phase", "a", "--dry-run", "--window-days", "14"])
        assert rc == 1

    def test_rollback_with_audit_id_but_no_db_exits_1(self):
        """Rollback needs a DB connection; without DATABASE_URL it
        exits 1 (DB unavailable) rather than 2 (not implemented).
        The "not implemented" exit is only reachable when DB is
        available — tested via SP_INTEGRATION_DB-gated tests."""
        from scripts.tennis_dedup import main
        rc = main(["--rollback", "--audit-id", "deadbeef-0000-0000-0000-000000000000"])
        assert rc == 1


# ══════════════════════════════════════════════════════════════
# Dry-run report formatting (no DB required)
# ══════════════════════════════════════════════════════════════


class TestDryRunReportFormat:
    """Verifies the dry-run report includes the right sections
    for operator review."""

    def test_report_includes_merge_group_details(self):
        from scripts.tennis_dedup import (
            format_dry_run_report, MergeGroup, TeamRow,
        )
        mg = MergeGroup(
            canonical=TeamRow("a", "Carlos Alcaraz",
                              datetime(2026, 1, 1, tzinfo=timezone.utc), 3),
            dupes=[TeamRow("b", "Alcaraz C. (Esp)",
                           datetime(2026, 5, 1, tzinfo=timezone.utc), 1)],
            shared_records=50,
        )
        report = {
            "canonical_id": "a",
            "dupe_ids": ["b"],
            "aliases_transferring": 2,
            "affected_fixtures": 5,
            "affected_review_queue": 1,
        }
        team_map = {
            "a": mg.canonical,
            "b": mg.dupes[0],
        }
        output = format_dry_run_report([mg], [], team_map, [report])
        assert "Carlos Alcaraz" in output
        assert "Alcaraz C. (Esp)" in output
        assert "Merge-group 1" in output
        assert "Shared records: 50" in output
        assert "Aliases transferring: 2" in output
        assert "Affected fixtures: 5" in output

    def test_report_includes_skipped_clusters(self):
        from scripts.tennis_dedup import (
            format_dry_run_report, TeamRow,
        )
        skipped = [{"x", "y", "z"}]
        team_map = {
            "x": TeamRow("x", "Chen Y.", datetime(2026, 1, 1, tzinfo=timezone.utc), 0),
            "y": TeamRow("y", "Chen M.", datetime(2026, 1, 1, tzinfo=timezone.utc), 0),
            "z": TeamRow("z", "Chen C.", datetime(2026, 1, 1, tzinfo=timezone.utc), 0),
        }
        output = format_dry_run_report([], skipped, team_map, [])
        assert "Skipped clusters (1)" in output
        assert "3 members" in output
        assert "Chen" in output

    def test_empty_population_report(self):
        from scripts.tennis_dedup import format_dry_run_report
        output = format_dry_run_report([], [], {}, [])
        assert "Merge-groups: 0" in output
        assert "Skipped clusters (Phase B or skip): 0" in output


# ══════════════════════════════════════════════════════════════
# Integration tests (SP_INTEGRATION_DB-gated)
# ══════════════════════════════════════════════════════════════


import os

INTEGRATION_DB = os.environ.get("SP_INTEGRATION_DB", "").strip()


@pytest.mark.skipif(
    not INTEGRATION_DB,
    reason="SP_INTEGRATION_DB not set — Tennis dedup integration tests need real Postgres.",
)
class TestMergeClusterIntegration:
    """Real-DB tests against a Postgres with the Tennis dedup
    migration (e2a7f3c1d4b8) applied.

    Run via:
        SP_INTEGRATION_DB=postgresql+asyncpg://... pytest tests/test_tennis_dedup.py -v
    """

    @pytest.mark.skip(reason="Integration test — implementation pending operator dry-run approval")
    def test_merge_cluster_transaction_isolation(self):
        """Each merge_cluster call is a single transaction per Phase 2D.3.1.
        Failure mid-merge rolls back the entire cluster — no partial state."""
        pass

    @pytest.mark.skip(reason="Integration test — implementation pending")
    def test_fk_cascade_correctness(self):
        """sp.fixtures home/away team_ids rewrite to canonical. sp.team_aliases
        copy to canonical via INSERT ON CONFLICT DO NOTHING. Original dupe
        aliases deleted via CASCADE on sp.teams DELETE."""
        pass

    @pytest.mark.skip(reason="Integration test — implementation pending")
    def test_jsonb_candidate_fixtures_rewrite(self):
        """sp.review_queue.candidate_fixtures JSONB array entries matching
        dupe team_id are rewritten to canonical team_id."""
        pass

    @pytest.mark.skip(reason="Integration test — implementation pending")
    def test_audit_row_pre_state_capture(self):
        """sp.dedup_audit.pre_state captures: team rows + alias sets +
        affected fixtures with original FKs + affected review_queue
        rows with original candidate_fixtures JSONB."""
        pass

    @pytest.mark.skip(reason="Integration test — implementation pending")
    def test_rollback_restores_original_state(self):
        """Rollback from sp.dedup_audit row re-inserts deleted team +
        aliases, reverts fixture FKs, writes back original
        candidate_fixtures JSONB."""
        pass

    @pytest.mark.skip(reason="Integration test — implementation pending")
    def test_concurrent_merge_select_for_update_failfast(self):
        """Two concurrent merge_cluster calls targeting overlapping
        team_ids: SELECT FOR UPDATE at step 0 causes the second to
        fail-fast with ValueError (row count mismatch)."""
        pass


# ══════════════════════════════════════════════════════════════
# BBL Component 4 hook — parity + invocation contract
# ══════════════════════════════════════════════════════════════
#
# The `post_review_queue_swap_hook` parameter was added to
# `merge_cluster()` for BBL Component 4. Tennis path passes None
# (default) → branch never entered → behavior identical to the
# pre-hook primitive. These tests are the load-bearing guarantee
# that touching tested code didn't change Tennis behavior.


class TestPostSwapHookSignature:
    """Pure-signature tests (no DB) — the hook parameter exists,
    has the right default, and Tennis-default callers' signature
    contract is unchanged."""

    def test_hook_parameter_exists_with_none_default(self):
        """The new parameter must default to None so all existing
        Tennis callers (which don't pass it) get zero behavior
        change."""
        import inspect
        from scripts.tennis_dedup import merge_cluster
        sig = inspect.signature(merge_cluster)
        assert "post_review_queue_swap_hook" in sig.parameters
        param = sig.parameters["post_review_queue_swap_hook"]
        assert param.default is None, (
            "Default MUST be None — any other default would change "
            "Tennis behavior since Tennis callers don't pass this "
            "parameter."
        )
        # Keyword-only enforcement keeps callers from passing the
        # hook positionally and silently breaking when arg order
        # shifts.
        assert param.kind == inspect.Parameter.KEYWORD_ONLY

    def test_existing_required_params_unchanged(self):
        """Tennis callers pass `mg=...`, `merge_phase=...`, and
        optionally `merge_pr=...`, `dry_run=...`. None of those
        signatures change with the hook addition."""
        import inspect
        from scripts.tennis_dedup import merge_cluster
        sig = inspect.signature(merge_cluster)
        assert "mg" in sig.parameters
        assert sig.parameters["mg"].default is inspect.Parameter.empty
        assert "merge_phase" in sig.parameters
        assert sig.parameters["merge_phase"].default is inspect.Parameter.empty
        assert "merge_pr" in sig.parameters
        assert sig.parameters["merge_pr"].default is None
        assert "dry_run" in sig.parameters
        assert sig.parameters["dry_run"].default is False


@pytest.mark.skipif(
    not INTEGRATION_DB,
    reason="SP_INTEGRATION_DB not set — Tennis dedup integration tests need real Postgres.",
)
class TestPostSwapHookInvocation:
    """Real-DB integration tests for the hook contract. Stubbed
    pending operator dry-run approval, same convention as the rest
    of `TestMergeClusterIntegration`."""

    @pytest.mark.skip(reason="Integration test — implementation pending operator dry-run approval")
    def test_none_default_path_unchanged(self):
        """Calling merge_cluster() without the hook parameter (or
        passing None) produces identical sp.* writes as the
        pre-hook primitive — the parity guarantee that justifies
        touching tested code. Verify by snapshotting sp.fixtures,
        sp.team_aliases, sp.review_queue, sp.dedup_audit pre + post
        and asserting they match the recorded Tennis dedup
        Day-25/26 production baseline."""
        pass

    @pytest.mark.skip(reason="Integration test — implementation pending operator dry-run approval")
    def test_hook_awaited_once_per_dupe_with_correct_args(self):
        """Pass an AsyncMock as the hook. After merge_cluster()
        returns, assert:
          - mock.await_count == len(mg.dupes)
          - each call's args = (session, dupe_id_str, canonical_id_str)
          - the `session` arg is the SAME session merge_cluster opened
            internally (so the hook participates in the transaction)
        Verifies the per-dupe-in-loop placement + in-transaction
        contract."""
        pass

    @pytest.mark.skip(reason="Integration test — implementation pending operator dry-run approval")
    def test_hook_exception_rolls_back_whole_merge(self):
        """Pass a hook that raises after Step 3. Assert:
          - sp.fixtures FK changes from Step 2 are reverted
          - sp.team_aliases reparenting from Step 1 is reverted
          - sp.review_queue.candidate_fixtures from Step 3 is reverted
          - sp.dedup_audit has NO row for this merge attempt
          - sp.teams DELETE from Step 5 did NOT happen
        Atomicity guarantee — the whole reason the hook must run
        on the merge_cluster session."""
        pass
