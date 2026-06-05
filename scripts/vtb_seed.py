"""Russian VTB United League seed manifest — Phase 2D.5-A workstream #7.

Data-driven league bootstrap: Russian VTB United League teams
identified via asymmetric_anchor_failure resolver signal (Day-34
afternoon discovery query). ~230+ records/7d resolving to
review_queue.

11 teams. Workstream #7 of Phase 2D.5-A per Day-31 re-sequencing in
`docs/bootstraps/phase-2d5a-sequencing-decision.md`.

## Source

Source: 2025-26 VTB United League season Wikipedia roster
(operator-verified paste, Day-34) + Day-34 discovery query.

## Composition: 5 INSERTs + 6 BACKFILLs

INSERT teams (no clean Phase 2A.5 legacy stub OR operator-judged
fresh insert per separate-entity discipline):
  - CSKA Moscow
  - BC Uralmash Yekaterinburg (Uralmash Ekaterinburg 9684b3a4 +
    Uralmash Yekaterinburg ce125faf are SEPARATE entities)
  - BC Nizhny Novgorod
  - BC Avtodor (Avtodor Saratov c0766622 is SEPARATE entity)
  - MBA Moscow (the three-branch classifier will BACKFILL if
    legacy stub 1f5f991a matches normalized_name at apply time;
    INSERT otherwise — bootstrap script handles dynamically)

BACKFILL teams (Phase 2A.5 stubs created 2026-05-08):
  - Lokomotiv Kuban (1dae39ae-fbb5-4727-ba12-080d383a3cd3)
  - UNICS Kazan (b1d198b0-e06d-48da-8c0b-fd6c7c146ea5)
  - Enisey (eef30d44-b25d-4673-9cb9-a586a7212263)
  - Zenit Petersburg (d639c09a-517e-4950-b291-5cddc493c1b7)
  - Parma Perm (a1973c38-48f2-4a53-bba7-859f67d9e1e3)
  - Khimki M. (b2fbeb14-c4f3-4f59-a9d0-ae3e4489f127)

## Khimki M. — out-of-roster inclusion

Khimki M. is NOT on the 2025-26 VTB United League Wikipedia roster
(likely relegated or withdrew). HOWEVER, Day-34 discovery query
confirmed FL is actively sending ~42 records/7d for Khimki. Included
in manifest to resolve active FL volume; scope-doc §6.1 monitoring
follow-up.

## BC Samara — Wikipedia roster but not in manifest

Wikipedia 2025-26 roster includes BC Samara (Samara). NOT in this
manifest per operator's explicit Day-34 spec (Day-34 discovery did
not surface Samara; possible low/zero FL volume or fixture-window
absence). Scope-doc §6.5 monitoring follow-up — add if FL ever
sends Samara records.

## Canonical_name policy (F1)

BACKFILLs preserve legacy Phase 2A.5 canonical_name unchanged (F1
production-anchor discipline). INSERTs use common forms.

Notable: legacy canonicals retained for BACKFILLs even where they
differ from Wikipedia 2025-26 form (UNICS Kazan vs Wikipedia
"BC UNICS"; Zenit Petersburg vs "BC Zenit Saint Petersburg"; Parma
Perm vs "BC Parma"; Khimki M. with trailing period). The legacy
canonical IS the production-anchor (F1 amendment #12).

## Alias distinctiveness (F2) — empirical-coverage INCLUSION

Per F2 NEW empirical-coverage discipline established Turkish BSL #5
(Day-31) and reinforced Greek HEBA #6 (Day-33):

Bare club aliases INCLUDED where FL sends bare forms at material
rates AND sport_id partition validates safety:
  - "CSKA Moscow" — Day-34 discovery dominant form despite
    CSKA Moscow FC (football top-5) + CSKA Moscow HC (hockey)
    sharing the name; Day-22 sport_id partition validates safety
  - "Lokomotiv Kuban" — distinctive enough (city-qualified)
  - "Avtodor", "Uralmash", "Parma" — distinctive single-team forms
  - "Enisey", "UNICS" — distinctive

Bare generic forms EXCLUDED (cross-sport collision or generic):
  - "Zenit" bare — Zenit Saint Petersburg FC (top-5 Russian football
    recognition); city-qualified "Zenit Petersburg" / "Zenit Saint
    Petersburg" INCLUDED instead
  - "Moscow" bare — two VTB teams in Moscow (CSKA + MBA);
    within-league collision
  - "Kazan", "Perm" bare — too generic

## Spelling variants (F3)

Russian-to-Latin transliteration produces multiple valid forms:

  - "Yekaterinburg" ↔ "Ekaterinburg" (with/without Y) — DIFFERENT
    normalized keys; both required on BC Uralmash Yekaterinburg
  - "UNICS" ↔ "Uniks" (spelling) — DIFFERENT normalized keys; both
    required on UNICS Kazan (FL sends both)
  - "Saint Petersburg" ↔ "St Petersburg" ↔ "Petersburg" — variants
    included on Zenit Petersburg
  - "Lokomotiv Kuban" ↔ "BC Lokomotiv Kuban" ↔ "PBC Lokomotiv
    Kuban" — provider-form variants
  - "Khimki" (no period) ↔ "Khimki M." (legacy canonical with
    period) — DIFFERENT normalized keys ("khimki" vs "khimki m");
    both required

Cyrillic-script aliases OUT OF SCOPE per KBL Issue #165 precedent
(ASCII Latin-script only). Cyrillic-aware normalizer required to
expand coverage; deferred.

## Discovery query coverage (F7 / Pattern A.2 per amendment #21)

Day-34 discovery (7-day, Basketball, Russian provider patterns):
  - BC Lokomotiv Kuban, Lokomotiv Kuban
  - CSKA Moscow, CSKA Moscow *
  - BC Uniks Kazan, Unics Kazan
  - Enisey, BC Enisey
  - Khimki M., Khimki
  - Chelyabinsk (NOT in 2025-26 VTB roster — out-of-scope; see §6.3)

EuroLeague/EuroCup crossover potential: CSKA Moscow + UNICS Kazan
historically active in European competitions (pre-2022 sanctions);
current 2025-26 EuroLeague participation depends on geopolitical
status. Crossover signal will be empirically validated post-apply
via F7.

## Dormant phantom risk (Amendment #22 pre-apply audit MANDATORY)

3 Phase 2A.5 legacy stubs identified as collision risks:

| Dormant phantom | UUID | Collides with manifest |
|---|---|---|
| PBC Lokomotiv-Kuban | f4cd06c6 | Lokomotiv Kuban (hyphen variant) |
| Parma Permsky Kray | 065f0ed5 | Parma Perm aliases |
| Avtodor Saratov | c0766622 | BC Avtodor aliases (different team) |

Plus separate-entity stubs that are NOT to be aliased:
  - Uralmash Ekaterinburg (9684b3a4) — separate from BC Uralmash
    Yekaterinburg INSERT
  - Uralmash Yekaterinburg (ce125faf) — separate from BC Uralmash
    Yekaterinburg INSERT

Per amendment #22: pre-apply audit MUST be run before wet apply.
Per Day-33 HEBA AO Mykonou finding: post-apply collision audit
remains MANDATORY regardless of clean pre-apply audit.

## Source value (Q3)

bootstrap_league_coverage (cohort-wide convention).

## Re-curation runbook

VTB United League roster churns annually. When updates needed:
  1. Visit Wikipedia "VTB United League" current season page
  2. For each team: confirm Wikipedia canonical unchanged; add new
     sponsor/branding alias; retain old aliases
  3. For promoted teams: add canonical row + aliases; verify cross-
     sport collision (Russian sport overlaps); run amendment #22
     pre-apply audit
  4. For relegated teams: leave canonical in place
  5. Re-evaluate Khimki M. (§6.1) and Samara absence (§6.5)
  6. Run --dry-run; apply; F7 via JOIN with country_code='RUS'
"""
from __future__ import annotations


VTB_ALIAS_SOURCE = "bootstrap_league_coverage"


# Format: (canonical_name, country_code, aliases_tuple, notes)
#
# 11 Russian VTB United League teams.
# 5 INSERTs + 6 BACKFILLs (one out-of-roster: Khimki M.).
# BC Samara from Wikipedia roster intentionally excluded per
# operator's Day-34 spec (no Day-34 discovery volume; §6.5 follow-up).
VTB_TEAMS_SEED: list[tuple[str, str, tuple[str, ...], str]] = [
    ("CSKA Moscow", "RUS",
     ("CSKA Moscow", "CSKA Moscow *", "PBC CSKA Moscow", "CSKA"),
     "VTB; INSERT (no Phase 2A.5 legacy stub). CROSS-SPORT COLLISION: "
     "CSKA Moscow FC (football, top-5 Russian recognition) + CSKA "
     "Moscow HC (hockey). Bare 'CSKA Moscow' INCLUDED per F2 NEW "
     "empirical-coverage discipline (FL sends this form at dominant "
     "Day-34 rates; Day-22 sport_id partition validates safety). "
     "Bare 'Moscow' EXCLUDED — within-VTB collision with MBA Moscow. "
     "Asterisk-suffix belt-and-suspenders per LBA Day-30 general "
     "FL pattern. 'PBC' = Professional Basketball Club"),

    ("BC Uralmash Yekaterinburg", "RUS",
     ("BC Uralmash Yekaterinburg", "Uralmash", "Uralmash Yekaterinburg",
      "Uralmash Ekaterinburg"),
     "VTB; INSERT. Yekaterinburg ↔ Ekaterinburg spelling variants "
     "(with/without leading Y) produce DIFFERENT normalized keys; "
     "both required per F3. Note: Uralmash Ekaterinburg (9684b3a4) "
     "and Uralmash Yekaterinburg (ce125faf) exist as SEPARATE Phase "
     "2A.5 legacy stubs — operator judgment INSERT fresh as BC "
     "Uralmash Yekaterinburg (Wikipedia canonical with BC prefix)"),

    ("BC Nizhny Novgorod", "RUS",
     ("BC Nizhny Novgorod", "Nizhny Novgorod", "BC Nizhny"),
     "VTB; INSERT. No major football collision at Nizhny Novgorod "
     "top tier currently. Bare 'Nizhny Novgorod' city-qualified "
     "alias INCLUDED"),

    ("BC Avtodor", "RUS",
     ("BC Avtodor", "Avtodor", "Avtodor Saratov"),
     "VTB; INSERT. Saratov-based. Avtodor Saratov (c0766622) exists "
     "as Phase 2A.5 legacy stub but is SEPARATE entity (different "
     "canonical_name 'Avtodor Saratov' vs manifest 'BC Avtodor'). "
     "INSERT fresh; legacy stub remains as dormant phantom risk per "
     "amendment #22"),

    ("MBA Moscow", "RUS",
     ("MBA Moscow", "MBA"),
     "VTB; INSERT or BACKFILL dynamically — Phase 2A.5 legacy stub "
     "MBA Moscow (1f5f991a) may exist (operator-flagged for runtime "
     "verification). Three-branch classifier handles dynamically: "
     "if normalized_name 'mba moscow' matches legacy stub, BACKFILL; "
     "otherwise INSERT fresh. Bare 'Moscow' EXCLUDED — within-VTB "
     "collision with CSKA Moscow"),

    ("Lokomotiv Kuban", "RUS",
     ("Lokomotiv Kuban", "BC Lokomotiv Kuban", "PBC Lokomotiv Kuban",
      "Lokomotiv Kuban Krasnodar"),
     "VTB; BACKFILL — Phase 2A.5 legacy stub "
     "1dae39ae-fbb5-4727-ba12-080d383a3cd3 (created 2026-05-08). "
     "Day-34 discovery: FL sends both bare 'Lokomotiv Kuban' and "
     "BC-prefixed 'BC Lokomotiv Kuban'. Krasnodar-based. DORMANT "
     "PHANTOM RISK: 'PBC Lokomotiv-Kuban' (f4cd06c6, hyphenated "
     "form) is SEPARATE stub — do NOT alias here. Bare 'Lokomotiv' "
     "EXCLUDED — Lokomotiv Moscow FC + Lokomotiv Yaroslavl HC etc. "
     "share the name. City-qualified 'Lokomotiv Kuban' distinctive"),

    ("UNICS Kazan", "RUS",
     ("UNICS Kazan", "BC Uniks Kazan", "Unics Kazan", "BC UNICS",
      "UNICS"),
     "VTB; BACKFILL — Phase 2A.5 legacy stub "
     "b1d198b0-e06d-48da-8c0b-fd6c7c146ea5 (created 2026-05-08). "
     "SPELLING VARIANTS: 'UNICS' (Wikipedia) ↔ 'Uniks' (FL form) "
     "produce DIFFERENT normalized keys; both required per F3. FL "
     "sends 'BC Uniks Kazan'. Wikipedia uses 'BC UNICS'. Bare "
     "'UNICS' INCLUDED per F2 NEW empirical-coverage discipline "
     "(distinctive single-team form)"),

    ("Enisey", "RUS",
     ("Enisey", "BC Enisey", "Enisey Krasnoyarsk"),
     "VTB; BACKFILL — Phase 2A.5 legacy stub "
     "eef30d44-b25d-4673-9cb9-a586a7212263 (created 2026-05-08). "
     "Krasnoyarsk-based. FL sends bare 'Enisey' form per Day-34 "
     "discovery"),

    ("Zenit Petersburg", "RUS",
     ("Zenit Petersburg", "BC Zenit", "Zenit Saint Petersburg",
      "BC Zenit Saint Petersburg", "Zenit St Petersburg"),
     "VTB; BACKFILL — Phase 2A.5 legacy stub "
     "d639c09a-517e-4950-b291-5cddc493c1b7 (created 2026-05-08). "
     "CROSS-SPORT COLLISION: Zenit Saint Petersburg FC (football, "
     "top-5 Russian recognition). Bare 'Zenit' INTENTIONALLY "
     "EXCLUDED — too generic, football collision risk. City-"
     "qualified forms ('Zenit Petersburg', 'Zenit Saint Petersburg', "
     "'Zenit St Petersburg') INCLUDED — distinctive sport-disambiguator"),

    ("Parma Perm", "RUS",
     ("Parma Perm", "BC Parma", "Parma", "Parma Permsky Kray"),
     "VTB; BACKFILL — Phase 2A.5 legacy stub "
     "a1973c38-48f2-4a53-bba7-859f67d9e1e3 (created 2026-05-08). "
     "Wikipedia name is 'BC Parma'; legacy canonical 'Parma Perm' "
     "retained per F1 production-anchor discipline. DORMANT PHANTOM "
     "RISK: 'Parma Permsky Kray' (065f0ed5) is SEPARATE stub — "
     "alias collision risk per amendment #22 audit. Bare 'Perm' "
     "EXCLUDED (too generic)"),

    ("Khimki M.", "RUS",
     ("Khimki M.", "Khimki", "BC Khimki"),
     "VTB; BACKFILL — Phase 2A.5 legacy stub "
     "b2fbeb14-c4f3-4f59-a9d0-ae3e4489f127 (created 2026-05-08). "
     "OUT-OF-ROSTER: Khimki M. is NOT on 2025-26 VTB United League "
     "Wikipedia roster (likely relegated/withdrew). HOWEVER: FL "
     "actively sending ~42/7d Day-34 discovery. Included to resolve "
     "active FL volume. Legacy canonical includes trailing period; "
     "'Khimki M.' normalizes to 'khimki m' vs bare 'Khimki' → "
     "'khimki' — DIFFERENT normalized keys, both required. Scope-"
     "doc §6.1 monitoring follow-up"),
]
