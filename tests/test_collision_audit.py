"""Unit tests for the amendment #22 collision audit pure function.

Covers the four scenarios from the FL-universe engine briefing:
  (a) clean alias — no existing mapping
  (b) same-normalized-form-different-team_id collision (the strict-tier
      punt condition)
  (c) same-normalized-form-SAME-team_id (BACKFILL idempotency — NOT a
      collision)
  (d) cross-source collision (legacy_bootstrap vs proposed
      bootstrap_league_coverage)

Plus regression-tests for the real-world collision shapes that drove
amendment #22's institutionalization:
  - Day-33 HEBA AO Mykonou surprise (alias_tier write-back collision)
  - Day-34 Uralmash spelling-variant collisions (multiple existing
    rows under different spellings)
  - Day-35 EuroLeague cross-language false collision (Turkish `mba` vs
    Russian MBA Moscow)

All tests use synthetic ExistingAliasMapping / ProposedAlias fixtures.
No database. No I/O. The pure function's contract must hold under
arbitrary input order, repeated normalized forms, etc.
"""
from __future__ import annotations

import pytest

from resolver.collision_audit import (
    Collision,
    CollisionReport,
    ExistingAliasMapping,
    ProposedAlias,
    audit_alias_collisions_pure,
    propose_alias,
)


# ──────────────────────────────────────────────────────────────────────
# Scenario (a) — clean alias
# ──────────────────────────────────────────────────────────────────────


class TestCleanAlias:
    """Proposed alias has no existing mapping under sport_id → clean."""

    def test_single_clean_proposal_empty_existing(self):
        p = propose_alias("trento", "Trento", "team-aquila-trento-uuid")
        report = audit_alias_collisions_pure(
            proposed=[p], existing=[], sport_id=3,
        )
        assert report.clean == (p,)
        assert report.same_team_already_present == tuple()
        assert report.colliding == tuple()
        assert report.total_proposed == 1
        assert report.emit_set == (p,)
        assert not report.has_collisions()

    def test_multiple_clean_proposals(self):
        ps = [
            propose_alias("trento", "Trento", "team-trento"),
            propose_alias("brescia", "Brescia", "team-brescia"),
            propose_alias("sassari", "Sassari", "team-sassari"),
        ]
        report = audit_alias_collisions_pure(
            proposed=ps, existing=[], sport_id=3,
        )
        assert len(report.clean) == 3
        assert set(report.clean) == set(ps)
        assert report.emit_set == report.clean

    def test_existing_under_different_normalized_does_not_pollute(self):
        """Existing mapping under `other-form` does NOT touch a proposal
        on `target-form`."""
        existing = [
            ExistingAliasMapping(
                alias_normalized="other-form",
                team_id="legacy-other-team",
                canonical_name="Other Team",
                source="legacy_bootstrap",
            ),
        ]
        p = propose_alias("target-form", "Target", "team-target")
        report = audit_alias_collisions_pure(
            proposed=[p], existing=existing, sport_id=3,
        )
        assert report.clean == (p,)


# ──────────────────────────────────────────────────────────────────────
# Scenario (b) — multi-team_id collision
# ──────────────────────────────────────────────────────────────────────


class TestMultiTeamIdCollision:
    """Proposed alias_normalized already maps to a DIFFERENT team_id.
    Emitting would expand the AliasIndex set beyond size 1 → strict
    tier punts."""

    def test_proposal_against_different_team_id_is_collision(self):
        existing = [
            ExistingAliasMapping(
                alias_normalized="mba",
                team_id="mersin-basketbol-uuid",
                canonical_name="Mersin Basketbol",
                source="legacy_bootstrap",
            ),
        ]
        p = propose_alias("mba", "MBA", "mba-moscow-uuid")
        report = audit_alias_collisions_pure(
            proposed=[p], existing=existing, sport_id=3,
        )
        assert report.clean == tuple()
        assert report.same_team_already_present == tuple()
        assert len(report.colliding) == 1
        assert report.colliding[0].proposed == p
        assert len(report.colliding[0].conflicting_mappings) == 1
        assert (report.colliding[0].conflicting_mappings[0].team_id
                == "mersin-basketbol-uuid")
        assert report.has_collisions()
        assert report.emit_set == tuple()  # auto-dropped

    def test_collision_does_not_leak_into_other_proposals(self):
        """One collision must not affect classification of unrelated
        clean proposals in the same report."""
        existing = [
            ExistingAliasMapping(
                alias_normalized="mba",
                team_id="mersin-basketbol-uuid",
                canonical_name="Mersin Basketbol",
                source="legacy_bootstrap",
            ),
        ]
        clean_p = propose_alias("brescia", "Brescia", "team-brescia")
        coll_p = propose_alias("mba", "MBA", "mba-moscow-uuid")
        report = audit_alias_collisions_pure(
            proposed=[clean_p, coll_p],
            existing=existing,
            sport_id=3,
        )
        assert report.clean == (clean_p,)
        assert len(report.colliding) == 1
        assert report.colliding[0].proposed == coll_p

    def test_multiple_existing_owners_all_reported_as_conflict(self):
        """If `alias_normalized` is held by 2+ existing team_ids
        already (corrupt state), the collision must surface ALL of
        them so the operator can manually clean up."""
        existing = [
            ExistingAliasMapping(
                alias_normalized="zadar",
                team_id="kk-zadar-uuid",
                canonical_name="KK Zadar",
                source="legacy_bootstrap",
            ),
            ExistingAliasMapping(
                alias_normalized="zadar",
                team_id="zadar-bare-stub-uuid",
                canonical_name="Zadar",
                source="alias_tier",
            ),
        ]
        p = propose_alias("zadar", "Zadar", "new-proposal-uuid")
        report = audit_alias_collisions_pure(
            proposed=[p], existing=existing, sport_id=3,
        )
        assert len(report.colliding) == 1
        # Both existing owners must be reported (operator needs both
        # to decide cleanup).
        team_ids_in_conflict = {
            m.team_id for m in report.colliding[0].conflicting_mappings
        }
        assert team_ids_in_conflict == {
            "kk-zadar-uuid", "zadar-bare-stub-uuid",
        }


# ──────────────────────────────────────────────────────────────────────
# Scenario (c) — same team_id (NOT a collision; BACKFILL idempotency)
# ──────────────────────────────────────────────────────────────────────


class TestSameTeamAlreadyPresent:
    """Existing alias mapping on the SAME target_team_id is NOT a
    collision. This is the BACKFILL idempotency case — re-running a
    bootstrap apply lands here for aliases already present."""

    def test_same_team_alias_already_present_classifies_correctly(self):
        existing = [
            ExistingAliasMapping(
                alias_normalized="iraklis",
                team_id="iraklis-bc-uuid",
                canonical_name="Iraklis BC",
                source="legacy_bootstrap",
            ),
        ]
        p = propose_alias("iraklis", "Iraklis", "iraklis-bc-uuid")
        report = audit_alias_collisions_pure(
            proposed=[p], existing=existing, sport_id=3,
        )
        assert report.clean == tuple()
        assert report.colliding == tuple()
        assert report.same_team_already_present == (p,)
        assert not report.has_collisions()
        # emit_set should NOT include same-team-already-present
        # (the bootstrap NOT-EXISTS guard handles it; no value in
        # re-emitting).
        assert report.emit_set == tuple()

    def test_same_team_under_multiple_sources_still_not_collision(self):
        """Same team_id holds the alias under TWO different sources.
        Not a collision — just two existing mappings on the same team."""
        existing = [
            ExistingAliasMapping(
                alias_normalized="iraklis",
                team_id="iraklis-bc-uuid",
                canonical_name="Iraklis BC",
                source="legacy_bootstrap",
            ),
            ExistingAliasMapping(
                alias_normalized="iraklis",
                team_id="iraklis-bc-uuid",
                canonical_name="Iraklis BC",
                source="alias_tier",
            ),
        ]
        p = propose_alias("iraklis", "Iraklis", "iraklis-bc-uuid")
        report = audit_alias_collisions_pure(
            proposed=[p], existing=existing, sport_id=3,
        )
        assert report.same_team_already_present == (p,)
        assert report.colliding == tuple()


# ──────────────────────────────────────────────────────────────────────
# Scenario (d) — cross-source collision
# ──────────────────────────────────────────────────────────────────────


class TestCrossSourceCollision:
    """The amendment #22 raison d'être: cross-source collisions are
    NOT blocked by sp.team_aliases's `(alias_normalized, source)`
    UNIQUE constraint. They appear at the AliasIndex layer (keyed on
    `(alias_normalized, sport_id)`) as multi-team_id sets that punt
    strict tier. The audit catches them pre-emit."""

    def test_legacy_bootstrap_vs_proposed_bootstrap_league_coverage(self):
        """Day-29/30/31 production shape: legacy_bootstrap row exists
        on team A; bootstrap_league_coverage proposal targets team B
        with the same normalized form."""
        existing = [
            ExistingAliasMapping(
                alias_normalized="bahcesehir kol",
                team_id="bahcesehir-koleji-uuid",
                canonical_name="Bahçeşehir Koleji",
                source="legacy_bootstrap",
            ),
        ]
        p = propose_alias(
            "bahcesehir kol",
            "Bahcesehir Kol.",
            "different-target-uuid",
        )
        report = audit_alias_collisions_pure(
            proposed=[p], existing=existing, sport_id=3,
        )
        assert len(report.colliding) == 1
        assert (report.colliding[0].conflicting_mappings[0].source
                == "legacy_bootstrap")

    def test_alias_tier_writeback_collision_ao_mykonou_shape(self):
        """Day-33 HEBA AO Mykonou surprise: the legacy stub `AO
        Mykonou` already carried `ao mykonou` via alias_tier write-
        back. Proposing `ao mykonou` on the new Mykonos BACKFILL
        team_id collides."""
        existing = [
            ExistingAliasMapping(
                alias_normalized="ao mykonou",
                team_id="ao-mykonou-legacy-stub-01dac308",
                canonical_name="AO Mykonou",
                source="alias_tier",
            ),
        ]
        p = propose_alias(
            "ao mykonou", "AO Mykonou",
            "mykonos-manifest-team-2f32272a",
        )
        report = audit_alias_collisions_pure(
            proposed=[p], existing=existing, sport_id=3,
        )
        assert report.has_collisions()
        assert (report.colliding[0].conflicting_mappings[0].source
                == "alias_tier")
        assert (report.colliding[0].conflicting_mappings[0].team_id
                == "ao-mykonou-legacy-stub-01dac308")


# ──────────────────────────────────────────────────────────────────────
# Multi-source mixed inputs + emit_set semantics
# ──────────────────────────────────────────────────────────────────────


class TestEmitSetAndMixedInputs:
    """End-to-end shapes mixing all three classifications."""

    def test_mixed_proposals_classify_correctly(self):
        existing = [
            ExistingAliasMapping(
                alias_normalized="mba",
                team_id="mersin-basketbol-uuid",
                canonical_name="Mersin Basketbol",
                source="legacy_bootstrap",
            ),
            ExistingAliasMapping(
                alias_normalized="iraklis",
                team_id="iraklis-bc-uuid",
                canonical_name="Iraklis BC",
                source="legacy_bootstrap",
            ),
        ]
        proposals = [
            propose_alias("brescia", "Brescia",
                          "pallacanestro-brescia"),  # clean
            propose_alias("mba", "MBA",
                          "mba-moscow-uuid"),         # collision
            propose_alias("iraklis", "Iraklis",
                          "iraklis-bc-uuid"),         # same team
            propose_alias("trento", "Trento",
                          "aquila-basket-trento"),    # clean
        ]
        report = audit_alias_collisions_pure(
            proposed=proposals, existing=existing, sport_id=3,
        )
        assert len(report.clean) == 2
        assert len(report.same_team_already_present) == 1
        assert len(report.colliding) == 1
        assert report.total_proposed == 4

        # emit_set is clean-only (auto-drop colliders + same-team)
        assert len(report.emit_set) == 2
        assert set(p.alias_normalized for p in report.emit_set) == {
            "brescia", "trento",
        }

    def test_summarize_format(self):
        report = audit_alias_collisions_pure(
            proposed=[propose_alias("a", "A", "t1")],
            existing=[],
            sport_id=3,
        )
        s = report.summarize()
        assert "sport_id=3" in s
        assert "total=1" in s
        assert "clean=1" in s

    def test_empty_proposal_set_returns_empty_report(self):
        report = audit_alias_collisions_pure(
            proposed=[], existing=[], sport_id=3,
        )
        assert report.total_proposed == 0
        assert report.emit_set == tuple()
        assert not report.has_collisions()


# ──────────────────────────────────────────────────────────────────────
# excluded_team_ids — Day-37 BBL gate Finding 2 (phantom-release)
# ──────────────────────────────────────────────────────────────────────


class TestExcludedTeamIdsPhantomRelease:
    """Day-37 BBL gate Finding 2: ALIAS-LINK is a two-part operation.
    The dormant phantom owns the canonical_name; alias-add cannot
    succeed while the phantom exists. The collision audit must treat
    rows belonging to soon-to-be-released phantoms as gone."""

    def test_excluded_owner_makes_proposal_clean(self):
        """The single owner of the colliding alias is scheduled for
        release → proposal becomes clean."""
        existing = [
            ExistingAliasMapping(
                alias_normalized="ewe baskets oldenburg",
                team_id="ewe-baskets-phantom-uuid",
                canonical_name="EWE Baskets Oldenburg",
                source="legacy_bootstrap",
            ),
        ]
        p = propose_alias(
            "ewe baskets oldenburg",
            "EWE Baskets Oldenburg",
            "oldenburg-live-uuid",
        )
        report = audit_alias_collisions_pure(
            proposed=[p], existing=existing, sport_id=3,
            excluded_team_ids=("ewe-baskets-phantom-uuid",),
        )
        assert report.clean == (p,)
        assert report.colliding == tuple()
        assert report.emit_set == (p,)

    def test_excluded_irrelevant_team_id_has_no_effect(self):
        existing = [
            ExistingAliasMapping(
                alias_normalized="mba",
                team_id="mersin-basketbol-uuid",
                canonical_name="Mersin Basketbol",
                source="legacy_bootstrap",
            ),
        ]
        p = propose_alias("mba", "MBA", "mba-moscow-uuid")
        # Excluding some unrelated team_id should not rescue this.
        report = audit_alias_collisions_pure(
            proposed=[p], existing=existing, sport_id=3,
            excluded_team_ids=("some-unrelated-uuid",),
        )
        assert len(report.colliding) == 1

    def test_multiple_owners_only_some_excluded_still_collides(self):
        """Phantom A scheduled for release, but phantom B also owns
        the same form → still colliding against B."""
        existing = [
            ExistingAliasMapping(
                alias_normalized="zadar",
                team_id="zadar-phantom-A",
                canonical_name="Zadar",
                source="legacy_bootstrap",
            ),
            ExistingAliasMapping(
                alias_normalized="zadar",
                team_id="zadar-phantom-B",
                canonical_name="Zadar (legacy variant)",
                source="alias_tier",
            ),
        ]
        p = propose_alias("zadar", "Zadar", "kk-zadar-new-uuid")
        report = audit_alias_collisions_pure(
            proposed=[p], existing=existing, sport_id=3,
            excluded_team_ids=("zadar-phantom-A",),
        )
        # Phantom B still owns it → still colliding.
        assert len(report.colliding) == 1
        # Only B reported as conflict (A was filtered out)
        team_ids = {
            m.team_id
            for m in report.colliding[0].conflicting_mappings
        }
        assert team_ids == {"zadar-phantom-B"}

    def test_all_owners_excluded_makes_proposal_clean(self):
        existing = [
            ExistingAliasMapping(
                alias_normalized="zadar",
                team_id="zadar-phantom-A",
                canonical_name="Zadar",
                source="legacy_bootstrap",
            ),
            ExistingAliasMapping(
                alias_normalized="zadar",
                team_id="zadar-phantom-B",
                canonical_name="Zadar (variant)",
                source="alias_tier",
            ),
        ]
        p = propose_alias("zadar", "Zadar", "kk-zadar-new-uuid")
        report = audit_alias_collisions_pure(
            proposed=[p], existing=existing, sport_id=3,
            excluded_team_ids=("zadar-phantom-A", "zadar-phantom-B"),
        )
        assert report.clean == (p,)

    def test_empty_excluded_set_unchanged_behavior(self):
        """Backwards compat — empty / default excluded_team_ids
        produces identical results to the pre-Finding-2 behavior."""
        existing = [
            ExistingAliasMapping(
                alias_normalized="mba",
                team_id="mersin-basketbol-uuid",
                canonical_name="Mersin Basketbol",
                source="legacy_bootstrap",
            ),
        ]
        p = propose_alias("mba", "MBA", "mba-moscow-uuid")
        report_default = audit_alias_collisions_pure(
            proposed=[p], existing=existing, sport_id=3,
        )
        report_empty = audit_alias_collisions_pure(
            proposed=[p], existing=existing, sport_id=3,
            excluded_team_ids=(),
        )
        assert len(report_default.colliding) == 1
        assert len(report_empty.colliding) == 1


# ──────────────────────────────────────────────────────────────────────
# Regression: Day-34 Uralmash variants
# ──────────────────────────────────────────────────────────────────────


class TestUralmashVariantsRegression:
    """Day-34 VTB workstream: post-apply audit found 2 surprise
    collisions on `uralmash ekaterinburg` AND `uralmash yekaterinburg`
    (separate Phase 2A.5 legacy stubs ce125faf + 9684b3a4) both
    colliding with the BC Uralmash Yekaterinburg INSERT.

    A pre-emit audit must surface both surprises."""

    def test_two_separate_existing_stubs_both_flagged(self):
        existing = [
            ExistingAliasMapping(
                alias_normalized="uralmash ekaterinburg",
                team_id="uralmash-ekaterinburg-stub-ce125faf",
                canonical_name="Uralmash Ekaterinburg",
                source="legacy_bootstrap",
            ),
            ExistingAliasMapping(
                alias_normalized="uralmash yekaterinburg",
                team_id="uralmash-yekaterinburg-stub-9684b3a4",
                canonical_name="Uralmash Yekaterinburg",
                source="legacy_bootstrap",
            ),
        ]
        proposals = [
            propose_alias("uralmash ekaterinburg",
                          "Uralmash Ekaterinburg",
                          "bc-uralmash-yekaterinburg-new"),
            propose_alias("uralmash yekaterinburg",
                          "Uralmash Yekaterinburg",
                          "bc-uralmash-yekaterinburg-new"),
        ]
        report = audit_alias_collisions_pure(
            proposed=proposals, existing=existing, sport_id=3,
        )
        assert len(report.colliding) == 2
        colliding_norms = {
            c.proposed.alias_normalized for c in report.colliding
        }
        assert colliding_norms == {
            "uralmash ekaterinburg", "uralmash yekaterinburg",
        }
        assert report.emit_set == tuple()
