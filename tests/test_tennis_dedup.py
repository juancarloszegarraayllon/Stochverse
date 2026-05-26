"""Tennis cross-format dedup — pure-function unit tests.

No DB required. Tests the classifiers, union-find, tiebreaker, and
Phase A criterion check from scripts/tennis_dedup.py.

Integration tests (merge_cluster, FK cascade, JSONB rewrite, audit
row, rollback) ship in a separate PR gated on SP_INTEGRATION_DB.
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
