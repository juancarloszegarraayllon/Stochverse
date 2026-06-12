"""Unit tests for the FL-universe batch classifier (Amendment #26).

Day-36 BBL pilot finding: the original `classify_team_pure` did exact
`normalized_name` matching only. FL `"Bamberg"` (normalized `"bamberg"`)
silently classified INSERT against a legacy stub `"Bamberg Baskets"`
(normalized `"bamberg baskets"`) — a FALSE-INSERT that would have
fragmented production resolution. The hardened classifier adds an
Amendment #26 distinctive-token reconciliation pass before emitting
INSERT.

These tests cover:
  - The Bamberg shape: FL bare-city → BACKFILL-CANDIDATE onto fuller
    legacy canonical, NOT silent INSERT.
  - Bonn / Ulm shape: no token overlap → genuine INSERT preserved.
  - Existing exact-match BACKFILL still works.
  - Existing exact-match SKIP (country_code set) still works.
  - Reverse direction (FL fuller name, sp.teams bare city) also
    reconciles.
  - Distinct-entity-marker guard (senior vs reserve, men's vs women's)
    blocks reconciliation across distinct entities.
  - Backwards-compat: `sp_distinctive_index=None` (or omitted) preserves
    pre-#26 behavior.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from resolver.fragmentation import SPTeamLite  # noqa: E402
from scripts.fl_universe_batch import (  # noqa: E402
    ClassifiedTeam,
    FLTeam,
    _reconcile_distinctive,
    classify_team_pure,
)


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def sp(team_id: str, canonical: str, normalized: str,
       country: str | None = None) -> SPTeamLite:
    return SPTeamLite(
        team_id=team_id,
        canonical_name=canonical,
        normalized_name=normalized,
        country_code=country,
        created_at="2026-05-08T00:00:00Z",
    )


def fl(team_id: str, fl_canonical: str, country: str = "Germany") -> FLTeam:
    return FLTeam(team_id=team_id, fl_canonical=fl_canonical,
                  country=country, raw={})


def build_indices(teams: list[SPTeamLite]) -> tuple[dict, dict]:
    """Mirror what `load_sp_teams_for_sport` builds, but in pure-Python
    so tests don't need an asyncio session."""
    from resolver.text_match import distinctive_tokens
    by_normalized: dict[str, list[SPTeamLite]] = {}
    by_distinctive: dict[str, list[SPTeamLite]] = {}
    for t in teams:
        by_normalized.setdefault(t.normalized_name, []).append(t)
        for tok in distinctive_tokens(t.normalized_name):
            by_distinctive.setdefault(tok, []).append(t)
    return by_normalized, by_distinctive


# ──────────────────────────────────────────────────────────────────────
# Day-36 BBL pilot regression — Amendment #26
# ──────────────────────────────────────────────────────────────────────


class TestBambergFalseInsertRegression:
    """The Day-36 BBL pilot bug: FL bare-city 'Bamberg' classified
    INSERT when production held 'Bamberg Baskets' (Phase 2A.5,
    country_code NULL). The hardened classifier MUST surface
    BACKFILL-CANDIDATE."""

    def _bbl_world(self) -> tuple[dict, dict, SPTeamLite]:
        bamberg_stub = sp(
            "7370e1f3-faff-40be-9139-25075d40dd62",
            "Bamberg Baskets", "bamberg baskets", country=None,
        )
        # Other BBL teams that DO exact-match (the 15 BACKFILLs the
        # pilot got right).
        others = [
            sp("aaaaaaaa-0001", "Alba Berlin", "alba berlin", country=None),
            sp("aaaaaaaa-0002", "Bayern Munich", "bayern munich", country=None),
            sp("aaaaaaaa-0003", "EWE Baskets Oldenburg",
               "ewe baskets oldenburg", country=None),
        ]
        all_teams = [bamberg_stub, *others]
        by_normalized, by_distinctive = build_indices(all_teams)
        return by_normalized, by_distinctive, bamberg_stub

    def test_fl_bamberg_classifies_backfill_candidate_not_insert(self):
        by_normalized, by_distinctive, bamberg_stub = self._bbl_world()
        fl_bamberg = fl("8CRfmy39", "Bamberg")  # the FL bare-city form

        result = classify_team_pure(
            fl=fl_bamberg,
            by_normalized=by_normalized,
            sport_id=3,
            sp_distinctive_index=by_distinctive,
        )

        assert result.classification == "BACKFILL-CANDIDATE", (
            "Day-36 regression: FL 'Bamberg' must not silently INSERT "
            "when 'Bamberg Baskets' stub exists. Got "
            f"{result.classification!r}, notes: {result.notes!r}"
        )
        assert result.sp_team_id == bamberg_stub.team_id
        assert result.sp_canonical == "Bamberg Baskets"
        assert result.reconciliation_basis == "exact-distinctive"
        assert result.reconciliation_candidates == 1
        assert "Amendment #26" in result.notes

    def test_fl_bonn_with_no_stub_remains_insert(self):
        """Bonn shape: FL bare-city, no sp.teams stub holds 'bonn'
        token. Must remain genuine INSERT — reconciliation must not
        over-match."""
        by_normalized, by_distinctive, _ = self._bbl_world()
        fl_bonn = fl("86MXctlE", "Bonn")

        result = classify_team_pure(
            fl=fl_bonn,
            by_normalized=by_normalized,
            sport_id=3,
            sp_distinctive_index=by_distinctive,
        )

        assert result.classification == "INSERT", (
            f"Bonn must INSERT (no stub holds 'bonn'); got "
            f"{result.classification!r}"
        )
        assert result.sp_team_id is None
        assert result.reconciliation_basis is None

    def test_fl_ulm_with_no_stub_remains_insert(self):
        """Ulm shape: same as Bonn — no sp.teams stub holds 'ulm'
        token. Genuine INSERT."""
        by_normalized, by_distinctive, _ = self._bbl_world()
        fl_ulm = fl("fcks1zeJ", "Ulm")

        result = classify_team_pure(
            fl=fl_ulm,
            by_normalized=by_normalized,
            sport_id=3,
            sp_distinctive_index=by_distinctive,
        )

        assert result.classification == "INSERT"
        assert result.sp_team_id is None

    def test_existing_exact_match_backfill_still_works(self):
        """Regression guard: the 15/15 BACKFILLs the pilot got RIGHT
        must still classify BACKFILL via the exact-match fast path."""
        by_normalized, by_distinctive, _ = self._bbl_world()
        fl_alba = fl("aaaaaaaa", "Alba Berlin")

        result = classify_team_pure(
            fl=fl_alba,
            by_normalized=by_normalized,
            sport_id=3,
            sp_distinctive_index=by_distinctive,
        )

        assert result.classification == "BACKFILL"
        assert result.sp_team_id == "aaaaaaaa-0001"
        assert result.reconciliation_basis is None
        # Notes from the exact-match path, NOT from the reconciliation
        # path.
        assert "Amendment #26" not in result.notes
        assert "country_code is NULL" in result.notes


# ──────────────────────────────────────────────────────────────────────
# Reconciliation mechanics
# ──────────────────────────────────────────────────────────────────────


class TestReconciliationMechanics:
    def test_reverse_direction_fl_fuller_sp_bare(self):
        """If FL holds a fuller form than sp.teams' bare city, the
        same subset reconciliation should fire."""
        sp_bare = sp("xxx", "Munich", "munich", country=None)
        by_normalized, by_distinctive = build_indices([sp_bare])
        # FL has the fuller 'Bayern Munich' form (distinctive tokens
        # {bayern, munich}). sp.teams has {munich}. Subset relation
        # holds either direction.
        fl_full = fl("yyy", "Bayern Munich")

        result = classify_team_pure(
            fl=fl_full,
            by_normalized=by_normalized,
            sport_id=3,
            sp_distinctive_index=by_distinctive,
        )

        assert result.classification == "BACKFILL-CANDIDATE"
        assert result.sp_team_id == "xxx"
        assert result.reconciliation_basis == "subset-distinctive"

    def test_ambiguous_reconciliation_flagged(self):
        """If multiple sp.teams reconcile to the same FL team, set
        `reconciliation_candidates > 1` and flag ambiguity in notes."""
        sp_a = sp("a", "Madrid Basket", "madrid basket", country=None)
        sp_b = sp("b", "Madrid Sports Club", "madrid sports club",
                  country=None)
        by_normalized, by_distinctive = build_indices([sp_a, sp_b])
        fl_madrid = fl("z", "Madrid")

        result = classify_team_pure(
            fl=fl_madrid,
            by_normalized=by_normalized,
            sport_id=3,
            sp_distinctive_index=by_distinctive,
        )

        # Both candidates have distinctive {madrid} (basket / sports /
        # club are generic) → both equal-match, ambiguous.
        assert result.classification == "BACKFILL-CANDIDATE"
        assert result.reconciliation_candidates >= 2
        assert "AMBIGUOUS" in result.notes

    def test_skip_country_code_set_via_exact_match(self):
        """Exact match + country_code set → SKIP. Reconciliation pass
        does not even fire."""
        sp_alba = sp("a", "Alba Berlin", "alba berlin", country="DEU")
        by_normalized, by_distinctive = build_indices([sp_alba])
        fl_alba = fl("z", "Alba Berlin")

        result = classify_team_pure(
            fl=fl_alba,
            by_normalized=by_normalized,
            sport_id=3,
            sp_distinctive_index=by_distinctive,
        )

        assert result.classification == "SKIP"
        assert result.sp_country_code == "DEU"

    def test_empty_normalized_skips(self):
        sp_empty = sp("a", "Whatever", "whatever", country=None)
        by_normalized, by_distinctive = build_indices([sp_empty])
        fl_noisy = fl("z", "   ")

        result = classify_team_pure(
            fl=fl_noisy,
            by_normalized=by_normalized,
            sport_id=3,
            sp_distinctive_index=by_distinctive,
        )

        assert result.classification == "SKIP"


# ──────────────────────────────────────────────────────────────────────
# Distinct-entity-marker guard — must NOT reconcile across senior/
# reserve or men's/women's splits
# ──────────────────────────────────────────────────────────────────────


class TestDistinctEntityGuardInReconciliation:
    def test_senior_fl_not_reconciled_to_reserve_stub(self):
        """If sp.teams holds 'Monaco U21' (reserve) and FL sends bare
        'Monaco' (senior), reconciliation must NOT match — they're
        distinct entities."""
        sp_reserve = sp("r", "Monaco U21", "monaco u21", country=None)
        by_normalized, by_distinctive = build_indices([sp_reserve])
        fl_senior = fl("s", "Monaco", country="Monaco")

        result = classify_team_pure(
            fl=fl_senior,
            by_normalized=by_normalized,
            sport_id=3,
            sp_distinctive_index=by_distinctive,
        )

        # Reconciliation guard blocks → genuine INSERT.
        assert result.classification == "INSERT"

    def test_mens_fl_not_reconciled_to_womens_stub(self):
        """Day-N+1+1 shape: sp.teams holds 'Zaragoza Femenino' (women's)
        and FL sends bare 'Zaragoza' (men's). Must NOT reconcile."""
        sp_womens = sp("w", "Casademont Zaragoza Femenino",
                       "casademont zaragoza femenino", country=None)
        by_normalized, by_distinctive = build_indices([sp_womens])
        fl_mens = fl("m", "Zaragoza", country="Spain")

        result = classify_team_pure(
            fl=fl_mens,
            by_normalized=by_normalized,
            sport_id=3,
            sp_distinctive_index=by_distinctive,
        )

        assert result.classification == "INSERT"

    def test_womens_fl_reconciles_to_womens_stub(self):
        """Both sides women's → guard passes → reconciliation fires."""
        sp_womens = sp("w", "Casademont Zaragoza Femenino",
                       "casademont zaragoza femenino", country=None)
        by_normalized, by_distinctive = build_indices([sp_womens])
        fl_womens = fl("z", "Zaragoza Femenino", country="Spain")

        result = classify_team_pure(
            fl=fl_womens,
            by_normalized=by_normalized,
            sport_id=3,
            sp_distinctive_index=by_distinctive,
        )

        assert result.classification == "BACKFILL-CANDIDATE"
        assert result.sp_team_id == "w"


# ──────────────────────────────────────────────────────────────────────
# Backwards compatibility
# ──────────────────────────────────────────────────────────────────────


class TestBackwardsCompat:
    def test_no_index_falls_back_to_pre_amendment_behavior(self):
        """When `sp_distinctive_index` is omitted/None, the reconciliation
        pass is skipped. No-exact-match → silent INSERT (the pre-#26
        Day-36 behavior). This is the compat tier for any caller that
        hasn't been updated yet."""
        sp_baskets = sp("x", "Bamberg Baskets", "bamberg baskets",
                        country=None)
        by_normalized, _ = build_indices([sp_baskets])
        fl_bamberg = fl("z", "Bamberg")

        result = classify_team_pure(
            fl=fl_bamberg,
            by_normalized=by_normalized,
            sport_id=3,
            # NOTE: sp_distinctive_index omitted — defaults to None.
        )

        # Without the index, exact-match misses and the function falls
        # through to INSERT (the original Day-36 false-INSERT shape).
        # Production callers MUST pass the index; this test documents
        # the compat-fallback semantics.
        assert result.classification == "INSERT"


# ──────────────────────────────────────────────────────────────────────
# Direct unit test for `_reconcile_distinctive` (internal helper)
# ──────────────────────────────────────────────────────────────────────


class TestReconcileDistinctiveHelper:
    def test_empty_fl_distinctive_returns_no_matches(self):
        sp_team = sp("a", "Bamberg Baskets", "bamberg baskets")
        _, by_distinctive = build_indices([sp_team])
        result = _reconcile_distinctive(
            fl_distinctive=tuple(),
            fl_canonical="",
            sp_distinctive_index=by_distinctive,
        )
        assert result == []

    def test_exact_distinctive_match_reported(self):
        sp_team = sp("a", "Bamberg Baskets", "bamberg baskets")
        _, by_distinctive = build_indices([sp_team])
        result = _reconcile_distinctive(
            fl_distinctive=("bamberg",),
            fl_canonical="Bamberg",
            sp_distinctive_index=by_distinctive,
        )
        assert len(result) == 1
        sp_hit, basis = result[0]
        assert sp_hit.team_id == "a"
        assert basis == "exact-distinctive"

    def test_subset_distinctive_match_reported(self):
        sp_team = sp("a", "Madrid", "madrid")
        _, by_distinctive = build_indices([sp_team])
        result = _reconcile_distinctive(
            fl_distinctive=("real", "madrid"),
            fl_canonical="Real Madrid",
            sp_distinctive_index=by_distinctive,
        )
        assert len(result) == 1
        _, basis = result[0]
        assert basis == "subset-distinctive"

    def test_no_token_overlap_returns_empty(self):
        sp_team = sp("a", "Bamberg Baskets", "bamberg baskets")
        _, by_distinctive = build_indices([sp_team])
        result = _reconcile_distinctive(
            fl_distinctive=("bonn",),
            fl_canonical="Bonn",
            sp_distinctive_index=by_distinctive,
        )
        assert result == []
