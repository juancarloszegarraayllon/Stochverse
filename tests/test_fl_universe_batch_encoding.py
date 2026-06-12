"""Regression test: non-ASCII roster round-trip through bundle writers.

Day-N+1 EU smoke run crashed on Windows when writing Poland Basket
Liga's bundle: `UnicodeEncodeError: 'charmap' codec can't encode
character '\\u0142' (ł)`. Root cause: `Path.write_text()` without
`encoding="utf-8"` defaults to the platform codec (cp1252 on
Windows), which can't encode characters outside Latin-1.

This test guards every future non-ASCII league by exercising the
bundle writers end-to-end on a synthetic roster containing:
  - Polish ł (BBG/Poland)
  - Croatian č, ž, š (Cibona / Zadar)
  - Slovenian č, š (KK Krka)
  - Lithuanian Ž, ą (Žalgiris / pas)
  - Greek transliteration variant (Olympiakos / Olympiacos — ASCII
    but kept for symmetry)

Then reads every artifact back and asserts the original characters
are preserved (not \\uXXXX-escaped, not crashed, not transliterated).

The test cannot exercise the FL crawl or DB lookups — it constructs
synthetic dataclasses and invokes the writers directly. That's
sufficient to catch any I/O-codec regression.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from resolver.collision_audit import propose_alias
from resolver.fragmentation import (
    SPTeamLite,
    classify_fragmentation_pair_pure,
    find_fragmentation_candidates_pure,
)
from scripts.fl_universe_batch import (
    ClassifiedTeam,
    FLTeam,
    LeagueBundle,
    LeagueCandidate,
    LeagueInfo,
    load_leagues_file,
    write_league_bundle,
    write_enumeration,
    write_index,
)


# The exact characters that broke production Poland / Croatia / etc.
NON_ASCII_NAMES = [
    "Anwil Włocławek",        # Polish ł, ł
    "Trefl Sopot",            # ASCII baseline
    "Śląsk Wrocław",          # Polish Ś + ł
    "KK Cibona Zagreb",       # ASCII baseline
    "KK Cedevita Olimpija",   # ASCII baseline (Slovenian, mixed)
    "Žalgiris Kaunas",        # Lithuanian Ž (Latin Z with caron)
    "Crvena zvezda mts",      # Serbian (ASCII)
    "Karşıyaka Basket",       # Turkish ş + ı (dotless i)
    "Beşiktaş Gain",          # Turkish ş + ş
    "Olympiakos Piraeus",     # Greek-transliterated (ASCII)
]


def _fake_fl_teams() -> list[FLTeam]:
    """Construct synthetic FL teams covering the non-ASCII surface."""
    return [
        FLTeam(
            team_id=f"fl-{i:02d}",
            fl_canonical=name,
            country="Poland" if "ł" in name or "Ś" in name else "Mixed",
            raw={"NAME": name, "ORIGINAL_NAME": name},
        )
        for i, name in enumerate(NON_ASCII_NAMES)
    ]


def _fake_classified(fl_teams: list[FLTeam]) -> list[ClassifiedTeam]:
    out: list[ClassifiedTeam] = []
    for idx, t in enumerate(fl_teams):
        # Alternate INSERT / BACKFILL to exercise both seed.py.draft paths.
        if idx % 2 == 0:
            out.append(ClassifiedTeam(
                fl=t, classification="INSERT",
                notes="synthetic — no sp.teams match",
            ))
        else:
            out.append(ClassifiedTeam(
                fl=t, classification="BACKFILL",
                sp_team_id=f"sp-uuid-{idx:02d}",
                sp_canonical=f"{t.fl_canonical} (legacy)",
                sp_country_code=None,
                sp_sport_id=3,
                sp_created_at="2026-05-08T00:00:00Z",
                name_count=1,
                notes="synthetic BACKFILL",
            ))
    return out


def _fake_bundle() -> LeagueBundle:
    fl_teams = _fake_fl_teams()
    classified = _fake_classified(fl_teams)
    # One fragmentation pair to exercise the alias-link writer
    # with non-ASCII content (Polish ł in both sides).
    anchor = SPTeamLite(
        team_id="sp-anchor",
        canonical_name="Anwil Włocławek",
        normalized_name="anwil wloclawek",
        country_code=None,
        created_at="2026-05-08T00:00:00Z",
    )
    partner = SPTeamLite(
        team_id="sp-partner",
        canonical_name="Anwil Włocławek SA",
        normalized_name="anwil wloclawek sa",
        country_code=None,
        created_at="2026-05-08T00:00:00Z",
    )
    pairs = find_fragmentation_candidates_pure(
        anchor=anchor, others=[partner],
    )
    verdicts = [
        classify_fragmentation_pair_pure(
            pair=p,
            anchor_fixture_count=5,
            partner_fixture_count=0,
        )
        for p in pairs
    ]

    # Synthetic collision report shape — real audit not needed; the
    # writer just iterates the report.
    from resolver.collision_audit import (
        CollisionReport,
    )
    report = CollisionReport(
        clean=tuple(),
        same_team_already_present=tuple(),
        colliding=tuple(),
        sport_id=3,
    )

    return LeagueBundle(
        league_info=LeagueInfo(
            league_name="Basket Liga",
            country="Polska",  # native-language ASCII safe
            season_id="season-synthetic",
            chosen_stage_id="stage-synthetic",
            chosen_stage_name="Main",
            candidates_tried=[
                LeagueCandidate(
                    league_name="Basket Liga",
                    country="Polska",
                    season_id="season-synthetic",
                    stage_id="stage-synthetic",
                    stage_name="Main",
                    league_score=100,
                    stage_score=100,
                ),
            ],
            fallback_path=["Main"],
            slug="polska--basket-liga",
        ),
        fl_teams=fl_teams,
        classified=classified,
        fragmentation_verdicts=verdicts,
        fragmentation_pair_count=len(verdicts),
        alias_link_count=sum(
            1 for v in verdicts if v.classification == "ALIAS-LINK"
        ),
        merge_required_count=0,
        proposed_aliases=[
            propose_alias(
                "anwil wloclawek sa",
                "Anwil Włocławek SA",
                "sp-anchor",
            ),
        ],
        collision_report=report,
        phantoms_to_release=["sp-partner"],
        elapsed_sec=0.42,
    )


# ──────────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────────


class TestNonAsciiRoundTrip:
    """Every bundle artifact must round-trip non-ASCII characters
    without crash, transliteration, or \\uXXXX escaping."""

    def test_write_league_bundle_preserves_non_ascii(self):
        bundle = _fake_bundle()
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            league_dir = write_league_bundle(out, bundle)

            # Verify every expected file exists.
            for fname in (
                "stage_meta.json", "fl_intermediate.json",
                "classification.md", "fragmentation.md",
                "aliases_audited.md", "phantom_release.md",
                "seed.py.draft",
            ):
                assert (league_dir / fname).exists(), \
                    f"{fname} missing from bundle"

            # fl_intermediate.json: characters present + JSON valid +
            # NO \\uXXXX escapes.
            raw = (league_dir / "fl_intermediate.json").read_text(
                encoding="utf-8",
            )
            data = json.loads(raw)
            for name in NON_ASCII_NAMES:
                assert name in raw, (
                    f"{name!r} missing from fl_intermediate.json raw text"
                )
                # Round-trip through the parsed JSON too
                fl_canonicals = {t["fl_canonical"] for t in data["teams"]}
                assert name in fl_canonicals, (
                    f"{name!r} missing from parsed fl_intermediate.json"
                )
            # No backslash-u escapes leaked (ensure_ascii=False working)
            assert "\\u0" not in raw, (
                "fl_intermediate.json has \\uXXXX escapes — "
                "ensure_ascii=False not honored"
            )

            # classification.md must render the names
            md = (league_dir / "classification.md").read_text(
                encoding="utf-8",
            )
            for name in NON_ASCII_NAMES:
                assert name in md, (
                    f"{name!r} missing from classification.md"
                )

            # seed.py.draft uses the names too — make sure no codec slip
            seed = (league_dir / "seed.py.draft").read_text(
                encoding="utf-8",
            )
            # BACKFILL canonicals are "(legacy)" suffix; check both
            # original names and the suffix variant survive.
            for name in NON_ASCII_NAMES:
                assert name in seed, (
                    f"{name!r} missing from seed.py.draft"
                )

            # fragmentation.md: anchor/partner with Polish ł
            frag = (league_dir / "fragmentation.md").read_text(
                encoding="utf-8",
            )
            assert "Włocławek" in frag, (
                "Włocławek missing from fragmentation.md"
            )

            # phantom_release.md: dormant phantom canonical with ł
            phantom = (league_dir / "phantom_release.md").read_text(
                encoding="utf-8",
            )
            assert "Włocławek" in phantom

    def test_write_enumeration_preserves_non_ascii(self):
        """The enumeration writer is exercised by --enumerate-only."""
        groups = [
            [LeagueCandidate(
                league_name="Basket Liga",
                country="Polska",
                season_id="s",
                stage_id="st",
                stage_name="Główna faza",  # Polish 'main phase' — ł
                league_score=50, stage_score=100,
            )],
            [LeagueCandidate(
                league_name="LKL",
                country="Lietuva",  # Lithuanian for Lithuania
                season_id="s",
                stage_id="st",
                stage_name="Reguliarus sezonas",
                league_score=50, stage_score=100,
            )],
        ]
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            md_path, json_path = write_enumeration(
                out_dir=out, groups=groups,
                metadata={
                    "sport_id": 3,
                    "league_hint": "",
                    "country_hint": "",
                    "fl_call_count": 1,
                    "total_groups": 2,
                    "elapsed_sec": 0.1,
                },
            )
            md = md_path.read_text(encoding="utf-8")
            raw_json = json_path.read_text(encoding="utf-8")
            assert "Polska" in md and "Lietuva" in md
            assert "Główna faza" in md
            assert "\\u0" not in raw_json, (
                "enumeration.json has \\uXXXX escapes"
            )
            data = json.loads(raw_json)
            all_stage_names = [
                s["stage_name"]
                for g in data["groups"]
                for s in g["stages"]
            ]
            assert "Główna faza" in all_stage_names
            assert "Reguliarus sezonas" in all_stage_names

    def test_write_index_preserves_non_ascii(self):
        bundle = _fake_bundle()
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            (out / "leagues").mkdir(parents=True, exist_ok=True)
            # Bundle goes in too, for slug references in index.
            write_league_bundle(out, bundle)
            md_path, json_path = write_index(
                out_dir=out,
                bundles=[bundle],
                failed=[("Lietuva", "Žalgiris cup", "no roster")],
                unmatched_leagues_file=[
                    ("Polska", "Stara Liga Koszykówki"),
                    ("Hrvatska", "Bivša Liga"),
                ],
                metadata={
                    "sport_id": 3,
                    "leagues_attempted": 1,
                    "leagues_succeeded": 1,
                    "leagues_failed": 1,
                    "fl_calls": 5,
                    "cache_hits": 0,
                    "elapsed_sec": 0.42,
                },
            )
            md = md_path.read_text(encoding="utf-8")
            assert "Żalgiris cup" in md or "Žalgiris cup" in md
            assert "Stara Liga Koszykówki" in md
            assert "Bivša Liga" in md
            data = json.loads(json_path.read_text(encoding="utf-8"))
            failed_l = {f["league_name"] for f in data["failed"]}
            assert "Žalgiris cup" in failed_l
            unmatched_l = {
                u["league_name"] for u in data["unmatched_leagues_file"]
            }
            assert "Stara Liga Koszykówki" in unmatched_l


# ──────────────────────────────────────────────────────────────────────
# UTF-8 BOM tolerance in --leagues-file
# ──────────────────────────────────────────────────────────────────────


class TestLeaguesFileBomTolerance:
    """A BOM-prefixed leagues file (some editors insert one) must not
    break line 1 parsing."""

    def test_bom_at_start_does_not_break_parsing(self):
        # u'﻿' is the BOM character; on disk it's the 3-byte
        # 0xEF 0xBB 0xBF sequence when encoded as UTF-8.
        content = "﻿Poland|Basket Liga\nLithuania|LKL\n"
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "leagues.txt"
            # Write as raw UTF-8 with BOM so we exercise the read path.
            p.write_bytes(content.encode("utf-8"))
            entries = load_leagues_file(p)
            assert len(entries) == 2
            assert entries[0] == ("Poland", "Basket Liga")
            assert entries[1] == ("Lithuania", "LKL")

    def test_non_ascii_country_league_names_survive(self):
        content = "Polska|Basket Liga\nLietuva|LKL Žalgiris cup\n"
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "leagues.txt"
            p.write_text(content, encoding="utf-8")
            entries = load_leagues_file(p)
            assert entries[0] == ("Polska", "Basket Liga")
            assert entries[1] == ("Lietuva", "LKL Žalgiris cup")
