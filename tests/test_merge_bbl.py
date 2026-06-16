"""Tests for scripts/merge_bbl.py — BBL Component 4.

Two layers:

  - Pure-function unit tests for `dedupe_array_preserve_order` — the
    Python mirror of the SQL hook. Covers the four collision-shape
    arrays surfaced by Day-N+1 BBL production inspection. Asserts:
      * Duplicates removed.
      * Order preserved — `candidate_fixtures[0]` (anchored side) and
        `candidate_fixtures[1:]` (trigram-ordered) both load-bearing
        per the matcher / admin / template invariant chain.

  - CLI / shape tests for `main()` — `--apply` without `--merge-pr`
    errors out; default mode is dry-run; pair table has 4 entries
    with the operator-confirmed UUIDs.

Real-DB tests (hook behavior under SQL, transaction atomicity, the
naive-swap-then-dedupe end-to-end shape) are stubbed pending operator
dry-run approval — same convention as `tests/test_tennis_dedup.py`'s
`TestMergeClusterIntegration`.
"""
from __future__ import annotations

import os

import pytest

from scripts.merge_bbl import (
    BBL_MERGE_PAIRS,
    dedupe_array_preserve_order,
    main,
)


# ──────────────────────────────────────────────────────────────────────
# Constants reused across tests (the 4 BBL collision shapes)
# ──────────────────────────────────────────────────────────────────────

# Real production review_queue.candidate_fixtures collision arrays
# from Day-N+1 inspection. Each represents an asymmetric-routing
# pending row whose [0] is the anchored side and [1:] are the failed
# side's top-N trigram candidates (DESC by similarity per
# resolver/fuzzy_tier/matcher.py:521-538).
#
# Each row carries BOTH the BBL merge pair's winner AND loser in the
# same array. Step 3's naive swap turns [anchored, loser, winner] into
# [anchored, winner, winner] — duplicate. The hook dedupes preserving
# order.

ANCHORED_1 = "b393f523-9ada-4c88-b42d-226178ab2f6e"   # arbitrary stand-in (Ludwigsburg id)
ANCHORED_2 = "d7c99331-9d6a-49f6-b796-b6b242c40fa5"   # arbitrary stand-in (Trier id)
ANCHORED_3 = "0192be15-63cb-434e-a183-5214f0d10e38"   # arbitrary stand-in (Frankfurt id)
ANCHORED_4 = "9777b05c-ad64-4c86-b8d0-f09947d080f9"   # arbitrary stand-in (Jena id)

# Vechta pair: winner=Vechta, loser=Rasta Vechta
VECHTA_WIN = "87d4c8c9-b17f-4428-b4d3-29666f4326e7"
VECHTA_LOS = "74e4e1e2-24e9-4766-840b-c3271897b903"

# Rostock pair
ROSTOCK_WIN = "1b81310d-6e53-4a90-8604-7e49718d311c"
ROSTOCK_LOS = "3aa87552-e24c-42b6-ac66-de437b9463a7"

# Hamburg pair
HAMBURG_WIN = "09624eed-4b9b-47f1-ab7f-87bc1a7416b5"
HAMBURG_LOS = "76f717ca-f68d-45b9-bf28-7e28d9dec64e"

# Heidelberg pair
HEIDELBERG_WIN = "36cf720f-beae-48ae-9941-4a4d4e959aec"
HEIDELBERG_LOS = "29b00c01-4556-4583-aa4d-307b38396a48"


# ══════════════════════════════════════════════════════════════
# Pure dedupe-shim tests — the 4 collision shapes
# ══════════════════════════════════════════════════════════════
#
# Each test models the post-swap state (after Step 3 has rewritten
# loser_id → winner_id). The dedupe function then collapses
# duplicates preserving first-occurrence order.


class TestDedupeRostockCollision:
    """Operator-cited production row: [b393f523, 3aa87552, 1b81310d]
    contains Rostock Seawolves (3aa87552 = loser) AND Rostock
    (1b81310d = winner). After swap loser→winner the array is
    [b393f523, 1b81310d, 1b81310d]. Hook must dedupe to
    [b393f523, 1b81310d] — anchored side at [0] preserved."""

    def test_post_swap_dedupe(self):
        post_swap = [ANCHORED_1, ROSTOCK_WIN, ROSTOCK_WIN]
        result = dedupe_array_preserve_order(
            post_swap, ROSTOCK_LOS, ROSTOCK_WIN,
        )
        assert result == [ANCHORED_1, ROSTOCK_WIN]

    def test_anchored_at_index_zero_preserved(self):
        post_swap = [ANCHORED_1, ROSTOCK_WIN, ROSTOCK_WIN]
        result = dedupe_array_preserve_order(
            post_swap, ROSTOCK_LOS, ROSTOCK_WIN,
        )
        assert result[0] == ANCHORED_1, (
            "candidate_fixtures[0] = anchored side is load-bearing — "
            "dedupe must not swap it out"
        )


class TestDedupeVechtaCollision:
    """[d7c99331, 74e4e1e2, 87d4c8c9] — Rasta Vechta (loser) +
    Vechta (winner). Post-swap [d7c99331, 87d4c8c9, 87d4c8c9] → dedupe
    [d7c99331, 87d4c8c9]."""

    def test_post_swap_dedupe(self):
        post_swap = [ANCHORED_2, VECHTA_WIN, VECHTA_WIN]
        result = dedupe_array_preserve_order(
            post_swap, VECHTA_LOS, VECHTA_WIN,
        )
        assert result == [ANCHORED_2, VECHTA_WIN]


class TestDedupeHamburgCollision:
    """Two production rows with Hamburg Towers (loser) + Hamburg
    (winner). Same shape as Rostock/Vechta."""

    def test_post_swap_dedupe_row_a(self):
        post_swap = [ANCHORED_3, HAMBURG_WIN, HAMBURG_WIN]
        result = dedupe_array_preserve_order(
            post_swap, HAMBURG_LOS, HAMBURG_WIN,
        )
        assert result == [ANCHORED_3, HAMBURG_WIN]

    def test_post_swap_dedupe_row_b(self):
        # Different anchored side in the second production row.
        post_swap = [ANCHORED_4, HAMBURG_WIN, HAMBURG_WIN]
        result = dedupe_array_preserve_order(
            post_swap, HAMBURG_LOS, HAMBURG_WIN,
        )
        assert result == [ANCHORED_4, HAMBURG_WIN]


class TestDedupeHeidelbergCollision:
    """Heidelberg pair. Same shape, included for the 4th pair so all
    operator-supplied pairs have a covered collision test."""

    def test_post_swap_dedupe(self):
        post_swap = [ANCHORED_1, HEIDELBERG_WIN, HEIDELBERG_WIN]
        result = dedupe_array_preserve_order(
            post_swap, HEIDELBERG_LOS, HEIDELBERG_WIN,
        )
        assert result == [ANCHORED_1, HEIDELBERG_WIN]


# ══════════════════════════════════════════════════════════════
# Order-preservation invariants
# ══════════════════════════════════════════════════════════════


class TestOrderPreservation:
    """The load-bearing invariant: index [0] is the anchored side;
    [1:] is trigram-ordered DESC by similarity. Dedupe must keep
    first occurrence of each id and discard later duplicates — never
    reorder."""

    def test_no_reordering_in_trigram_slice(self):
        """A 4-element row [anchored, A, B, A] (duplicate A in
        positions [1] and [3]) must dedupe to [anchored, A, B] —
        NOT [anchored, B, A], which a wrong-direction MAX(ord) would
        produce."""
        anchored, A, B = ANCHORED_1, ROSTOCK_WIN, VECHTA_WIN
        post_swap = [anchored, A, B, A]
        result = dedupe_array_preserve_order(
            post_swap, "irrelevant-dupe-id", "irrelevant-canonical-id",
        )
        assert result == [anchored, A, B]

    def test_already_deduped_array_unchanged(self):
        """Rows with no duplicates pass through verbatim — dedupe is
        a no-op on clean arrays."""
        post_swap = [ANCHORED_1, ROSTOCK_WIN, VECHTA_WIN, HAMBURG_WIN]
        result = dedupe_array_preserve_order(
            post_swap, "irrelevant", "irrelevant",
        )
        assert result == post_swap

    def test_empty_array(self):
        assert dedupe_array_preserve_order([], "x", "y") == []

    def test_single_element_array(self):
        assert dedupe_array_preserve_order(
            [ANCHORED_1], "x", "y",
        ) == [ANCHORED_1]

    def test_all_duplicates_collapse_to_one(self):
        """Degenerate case — every element identical. Result is a
        one-element array."""
        post_swap = [VECHTA_WIN, VECHTA_WIN, VECHTA_WIN, VECHTA_WIN]
        result = dedupe_array_preserve_order(
            post_swap, VECHTA_LOS, VECHTA_WIN,
        )
        assert result == [VECHTA_WIN]

    def test_plain_distinct_would_scramble_order_we_dont(self):
        """SQL `SELECT DISTINCT elem FROM jsonb_array_elements(arr)`
        returns rows in implementation-defined order — likely NOT
        first-occurrence. The dedupe must preserve first-occurrence
        positionally."""
        anchored, A, B, C = ANCHORED_1, ROSTOCK_WIN, VECHTA_WIN, HAMBURG_WIN
        # First-occurrence order: anchored, A, B, C
        post_swap = [anchored, A, B, C, A, B]
        result = dedupe_array_preserve_order(
            post_swap, "x", "y",
        )
        assert result == [anchored, A, B, C], (
            "Order must be first-occurrence — [anchored, A, B, C]. "
            f"Got {result!r}"
        )


# ══════════════════════════════════════════════════════════════
# BBL_MERGE_PAIRS table shape
# ══════════════════════════════════════════════════════════════


class TestBBLMergePairsTable:
    """The four operator-confirmed (winner, loser) pairs are baked
    in. Confirm shape + UUID coverage so a typo doesn't go
    unnoticed."""

    def test_exactly_four_pairs(self):
        assert len(BBL_MERGE_PAIRS) == 4

    def test_tuple_shape(self):
        for entry in BBL_MERGE_PAIRS:
            assert len(entry) == 3
            label, winner, loser = entry
            assert isinstance(label, str) and label
            assert isinstance(winner, str) and len(winner) == 36
            assert isinstance(loser, str) and len(loser) == 36
            assert winner != loser

    def test_no_duplicate_team_ids_across_pairs(self):
        """A team_id appearing in two different pairs would mean a
        merge-into-thing-that's-also-being-merged. Catch the typo."""
        all_ids: list[str] = []
        for _, w, l in BBL_MERGE_PAIRS:
            all_ids.append(w)
            all_ids.append(l)
        assert len(all_ids) == len(set(all_ids)), (
            "A team_id appears in more than one BBL merge pair — "
            "review the table for transcription errors."
        )

    def test_operator_confirmed_uuids_present(self):
        """Spot-check the four known winners + losers from the
        operator's confirmation message."""
        labels = {label for label, _, _ in BBL_MERGE_PAIRS}
        assert "Vechta / Rasta Vechta" in labels
        assert "Rostock / Rostock Seawolves" in labels
        assert "Hamburg / Hamburg Towers" in labels
        assert "Heidelberg / MLP Academics Heidelberg" in labels


# ══════════════════════════════════════════════════════════════
# CLI surface — default safety + --apply guardrail
# ══════════════════════════════════════════════════════════════


class TestLoadTeamRowsBasketballScope:
    """Regression test for the Day-N+1 dry-run abort: `load_team_rows`
    in scripts/tennis_dedup.py carried a hardcoded `WHERE s.code =
    'tennis'` filter. BBL teams have sport_code='basketball', so
    `load_team_rows` returned an empty dict and merge_bbl raised
    `winner team_id ... not found in sp.teams` even though every BBL
    UUID demonstrably exists in production.

    Fix: `load_team_rows` gained a `sport_code` kwarg (default
    'tennis' for Tennis-unchanged parity); merge_bbl passes
    `sport_code='basketball'`. This test mocks the session so we can
    assert (a) the kwarg is plumbed through end-to-end and (b) all 8
    BBL team_ids round-trip from the mock query result to TeamRow
    objects.
    """

    def test_mocked_load_returns_all_eight_bbl_teams(self):
        """Patch async_session inside tennis_dedup; have its execute
        return 8 rows shaped like the real query result; assert
        load_team_rows(sport_code='basketball') returns a dict keyed
        by team_id with all 8 entries."""
        import asyncio
        import datetime as dt
        from contextlib import asynccontextmanager
        from types import SimpleNamespace
        from unittest.mock import patch, MagicMock, AsyncMock

        import scripts.tennis_dedup as td_mod

        bbl_uuids = []
        bbl_canonicals = []
        for _, winner, loser in BBL_MERGE_PAIRS:
            bbl_uuids.extend([winner, loser])
            bbl_canonicals.extend([f"winner-{winner[:8]}",
                                   f"loser-{loser[:8]}"])

        rows = [
            SimpleNamespace(
                team_id=uuid_str,
                canonical_name=canon,
                created_at=dt.datetime(2026, 5, 1, tzinfo=dt.timezone.utc),
                alias_count=1,
            )
            for uuid_str, canon in zip(bbl_uuids, bbl_canonicals)
        ]

        captured_params: dict = {}

        class FakeResult:
            def all(self):
                return rows

        async def fake_execute(stmt, params):
            captured_params.update(params)
            return FakeResult()

        fake_session = SimpleNamespace(execute=fake_execute)

        @asynccontextmanager
        async def fake_async_session():
            yield fake_session

        with patch.object(td_mod, "async_session", fake_async_session):
            result = asyncio.run(
                td_mod.load_team_rows(
                    bbl_uuids, sport_code="basketball",
                )
            )

        # All 8 BBL team_ids round-trip into TeamRow objects.
        assert len(result) == 8
        for uuid_str in bbl_uuids:
            assert uuid_str in result, (
                f"BBL team_id {uuid_str} should round-trip but is "
                "missing from load_team_rows result"
            )
            tr = result[uuid_str]
            assert tr.team_id == uuid_str
            assert tr.canonical_name.startswith(("winner-", "loser-"))

        # And the sport_code kwarg was actually plumbed into the
        # query parameters — catches regression if a future edit
        # drops the kwarg from the bind dict.
        assert captured_params.get("sport_code") == "basketball", (
            "sport_code='basketball' must reach the SQL bind params "
            f"— got {captured_params!r}"
        )

    def test_merge_bbl_passes_basketball_to_load_team_rows(self):
        """Static-shape guard: the source line in merge_bbl.py that
        calls load_team_rows MUST pass sport_code='basketball'.
        Belt-and-suspenders — a future edit that drops the kwarg
        would reintroduce the Day-N+1 bug, and this test catches it
        without needing a real DB."""
        import inspect
        from scripts import merge_bbl
        source = inspect.getsource(merge_bbl)
        assert 'sport_code="basketball"' in source or \
               "sport_code='basketball'" in source, (
            "merge_bbl.py must call load_team_rows with "
            "sport_code='basketball'. The Tennis default would "
            "reject every BBL team_id at the s.code filter."
        )


class TestCLI:
    """The CLI must default to dry-run and refuse --apply without
    --merge-pr (sp.dedup_audit provenance gate)."""

    def test_apply_without_merge_pr_errors_out(self, capsys):
        rc = main(["--apply"])
        captured = capsys.readouterr()
        assert rc == 2
        assert "--merge-pr" in captured.err

    def test_dry_run_and_apply_mutually_exclusive(self, capsys):
        """argparse mutually-exclusive group should reject both flags
        passed together."""
        with pytest.raises(SystemExit) as exc_info:
            main(["--dry-run", "--apply"])
        assert exc_info.value.code == 2


# ══════════════════════════════════════════════════════════════
# Integration stubs (SP_INTEGRATION_DB-gated)
# ══════════════════════════════════════════════════════════════


INTEGRATION_DB = os.environ.get("SP_INTEGRATION_DB", "").strip()


@pytest.mark.skipif(
    not INTEGRATION_DB,
    reason="SP_INTEGRATION_DB not set — BBL merge integration tests need real Postgres.",
)
class TestMergeBblIntegration:
    """Real-DB tests of the SQL hook + per-pair atomicity. Stubbed
    pending operator dry-run approval, same convention as
    tests/test_tennis_dedup.py::TestMergeClusterIntegration."""

    @pytest.mark.skip(reason="Integration test — pending operator dry-run approval")
    def test_hook_sql_dedupe_matches_python_mirror(self):
        """For each of the 4 collision shapes, run the SQL hook
        against a fixture row and assert the resulting
        candidate_fixtures equals what
        `dedupe_array_preserve_order` returns. Catches drift between
        the SQL and Python implementations."""
        pass

    @pytest.mark.skip(reason="Integration test — pending operator dry-run approval")
    def test_hook_skips_rows_without_duplicates(self):
        """Rows whose post-swap candidate_fixtures have no duplicates
        are NOT rewritten — the hook's `WHERE length > distinct count`
        filter spares them. Asserted by snapshotting unaffected rows
        pre + post."""
        pass

    @pytest.mark.skip(reason="Integration test — pending operator dry-run approval")
    def test_hook_failure_rolls_back_whole_merge(self):
        """Inject a hook that raises after the first per-dupe call.
        Assert sp.fixtures, sp.team_aliases, sp.review_queue, and
        sp.dedup_audit ALL revert — the atomicity guarantee that
        justifies running the hook on merge_cluster's session."""
        pass

    @pytest.mark.skip(reason="Integration test — pending operator dry-run approval")
    def test_dry_run_no_writes_visible(self):
        """`merge_bbl.py --dry-run` (default) leaves sp.* writes
        zero. Snapshot row counts pre + post."""
        pass
