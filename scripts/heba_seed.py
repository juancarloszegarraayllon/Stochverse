"""Greek HEBA A1 (Basket League) seed manifest — Phase 2D.5-A workstream #6.

Data-driven league bootstrap: Greek HEBA A1 teams identified via
asymmetric_anchor_failure resolver signal (Day-32 afternoon discovery
query). ~50-70 records/7d resolving to review_queue (playoffs-only
window — full-season volume higher).

13 teams for the 2025-26 season (unusual count; Wikipedia confirms
13, not the typical 12 or 14). Workstream #6 of Phase 2D.5-A per
Day-31 re-sequencing in `docs/bootstraps/phase-2d5a-sequencing-decision.md`.

## Source

Source: 2025-26 Greek Basket League season Wikipedia roster
(operator-verified paste, Day-33 morning).

## Composition: 4 INSERTs + 9 BACKFILLs

INSERT teams (no clean Phase 2A.5 legacy stub):
  - AEK Athens
  - Aris Thessaloniki
  - Olympiakos BC
  - GS Karditsa

BACKFILL teams (Phase 2A.5 stubs created 2026-05-08; the three-branch
classifier matches on normalized_name and sets country_code='GRC'):
  - Iraklis BC (c17fa0b9-bad0-4027-9a96-8f50584873fb)
  - Kolossos Rhodes (ca5f6d4a-f75d-45ea-8a26-e610b40dbf31)
  - Maroussi BC (d8e37aa5-bfd3-4555-b5a1-f6173b034d12)
  - Mykonos (2f32272a-a077-43db-a024-75326f688acd)
  - PAOK BC (59eb93a6-fa3c-44f1-80c0-a67c5783352a)
  - Panathinaikos BC (6e1268f8-46dc-431d-a38c-9f0924c6922b)
  - Panionios (380f47bc-1057-4030-9064-f8896dc6e779)
  - Peristeri BC (6a00a818-b27a-4cbe-b1b5-dfd2a7364a9c)
  - Promitheas Patras BC Vikos Cola (eb0e7a18-7498-46aa-bf13-06b38c190795)

## Canonical_name policy (F1)

BACKFILLs keep Phase 2A.5 legacy canonical_name unchanged (F1
discipline; same as LBA's BACKFILLs and Turkish BSL's BACKFILLs).
INSERTs use city-qualified Wikipedia forms.

Note Promitheas Patras BC Vikos Cola retains the sponsor-prefixed
canonical per legacy stub form (F1 amendment #12 authoritative-
source primacy: legacy canonical is the production-anchor).

## Alias distinctiveness (F2) — empirical-coverage INCLUSION (F2 NEW)

Per F2 NEW empirical-coverage discipline established Turkish BSL
workstream #5 (Day-31): bare club names INCLUDED for football-overlap
teams because Day-32 discovery query confirms FL sends these forms.
Day-22 sport_id partition validates matcher-layer safety.

Bare club aliases INCLUDED (5 football-overlap teams):
  - "Olympiakos" / "Olympiacos" (FL spelling without k) on Olympiakos BC
  - "Panathinaikos" on Panathinaikos BC
  - "AEK" / "AEK Athens" on AEK Athens
  - "PAOK" on PAOK BC
  - "Aris" on Aris Thessaloniki

Bare city aliases EXCLUDED (too generic / cross-sport collision):
  - Athens, Thessaloniki, Piraeus, Patras, Marousi, Rhodes
  - "BC" bare (generic prefix shared by many BCs)

## Greek transliteration handling (F3)

Greek-to-Latin transliteration produces multiple valid forms:
  - Olympiakos (Modern Greek transliteration) ↔ Olympiacos (FL spelling)
  - Kolossos Rhodes ↔ Kolossos Rodou (Greek genitive form)

Both forms INCLUDED as aliases. The normalizer NFD-strips Latin
diacritics but does not handle cross-script transliterations —
manifest must enumerate both.

## Asterisk-suffix forms

Per general FL provider quirk (Italian LBA Day-30 finding, generalized
across LBA → Israeli BSL → Turkish BSL → HEBA): asterisk-suffix
variants INCLUDED defensively for top-volume teams. Discovery query
confirms "AEK Athens *" and "Olympiacos *" appear in production.

## Discovery query coverage (F7 / Pattern A.2 per amendment #21)

Day-32 afternoon production discovery (playoff-window, 7-day,
Basketball routing, Greek provider patterns):

  AEK Athens (~75/7d combined — highest volume), Olympiacos /
  BC Olympiakos Piraeus (~41/7d), Aris / BC Aris Thessaloniki
  (~35/7d), Kolossos Rhodes / BC Kolossos Rhodes (~28/7d),
  Panathinaikos / Panathinaikos BC (~14/7d), AEK Athens *,
  Olympiacos *

Inactive teams in 7-day window (eliminated or low-coverage):
Iraklis, Karditsa, Maroussi, Mykonos, Panionios, PAOK, Peristeri,
Promitheas. Full-season volume expected higher; F7 measurement opens
with cron data post-apply.

## Dormant phantom risk (Amendment #22 pre-apply audit MANDATORY)

5 dormant phantoms identified pre-apply that may produce alias
collisions post-apply (same Day-31/32 Turkish + Israeli BSL pattern):

  - Iraklis (b0602d2c) — bare form, collides with Iraklis BC BACKFILL
  - Kolossos Rodou (7260b8e5) — Greek transliteration, collides with
    Kolossos Rhodes BACKFILL via 'kolossos rodou' alias
  - Maroussi (11fb2774) — bare form, collides with Maroussi BC BACKFILL
  - Peristeri (0c6092b5) — bare form, collides with Peristeri BC BACKFILL
  - Promitheas (fca05a4b) — bare form, collides with Promitheas Patras
    BC Vikos Cola BACKFILL via 'promitheas' alias

EA Promitheas 2014 (4180be23) is a SEPARATE youth/reserve entity —
do NOT BACKFILL or alias to Promitheas Patras BC.

AS Karditsas (c7da3b82) and Karditsa Iaponiki (77ed94bd) are
SEPARATE entities — do NOT alias to GS Karditsa.

Per Amendment #22: pre-apply audit query MUST be run against
sp.team_aliases scoped to sport_id=3 with manifest alias_normalized
list. Any rows with team_count > 1 are pre-existing collisions that
must be resolved before apply.

## EuroCup crossovers (Day-32 discovery)

Cross-league FL provider strings confirmed:
  - Fenerbahce Istanbul vs BC Olympiakos Piraeus
  - BC Rytas Vilnius vs BC AEK Athens
  - Unicaja vs AEK Athens *
  - BC AEK Athens vs CB Malaga

These produce strict resolutions on HEBA side post-apply, same
pattern as Liga ACB Day-30 (Panathinaikos + Rytas crossovers) and
Turkish BSL Day-32 (Zalgiris crossover).

## Source value (Q3)

bootstrap_league_coverage (cohort-wide convention).

## Re-curation runbook

Greek HEBA A1 roster churns via promotion/relegation (Greek B
Basket League ↔ A1). Sponsor names change. When updates needed:

  1. Visit Wikipedia "Greek Basket League" current season page +
     greekbasketleague.gr
  2. For each team: confirm Wikipedia canonical unchanged; add new
     sponsor alias; retain old sponsor aliases (historical records)
  3. For promoted teams: add canonical row + aliases; verify cross-
     sport collision (Greek Super League football overlap); run
     amendment #22 pre-apply audit
  4. For relegated teams: leave canonical in place
  5. Run --dry-run; apply; F7 via JOIN to sp.fixtures + sp.teams
     with country_code='GRC'
"""
from __future__ import annotations


HEBA_ALIAS_SOURCE = "bootstrap_league_coverage"


# Format: (canonical_name, country_code, aliases_tuple, notes)
#
# All 13 Greek HEBA A1 teams, 2025-26 season.
# 4 INSERTs + 9 BACKFILLs from Phase 2A.5 legacy stubs.
HEBA_TEAMS_SEED: list[tuple[str, str, tuple[str, ...], str]] = [
    ("AEK Athens", "GRC",
     ("AEK Athens", "AEK", "BC AEK Athens", "AEK Athens *"),
     "HEBA A1; INSERT (no Phase 2A.5 legacy stub). CROSS-SPORT "
     "COLLISION: AEK Athens FC (Greek Super League football). Bare "
     "city 'Athens' INTENTIONALLY EXCLUDED. Bare 'AEK' INCLUDED per "
     "F2 NEW empirical-coverage discipline (FL sends 'AEK Athens' "
     "form at ~75/7d, highest HEBA volume Day-32 discovery). "
     "Asterisk-suffix 'AEK Athens *' belt-and-suspenders per LBA "
     "Day-30 general FL pattern. 'BC AEK Athens' is EuroCup-prefixed "
     "form (crossover with Liga ACB Unicaja + Lithuanian Rytas)"),

    ("Aris Thessaloniki", "GRC",
     ("Aris Thessaloniki", "Aris", "BC Aris Thessaloniki", "Aris BC"),
     "HEBA A1; INSERT. CROSS-SPORT COLLISION: Aris FC (Greek Super "
     "League football). Bare city 'Thessaloniki' INTENTIONALLY "
     "EXCLUDED. Bare 'Aris' INCLUDED per F2 NEW empirical-coverage "
     "discipline (FL sends 'Aris' form at ~35/7d Day-32 discovery)"),

    ("Olympiakos BC", "GRC",
     ("Olympiakos BC", "Olympiacos", "Olympiakos",
      "BC Olympiakos Piraeus", "Olympiacos *", "Olympiakos Piraeus"),
     "HEBA A1; INSERT. CROSS-SPORT COLLISION: Olympiakos FC (top-5 "
     "Greek Super League football recognition). Bare city 'Piraeus' "
     "INTENTIONALLY EXCLUDED. Bare 'Olympiakos' + 'Olympiacos' "
     "INCLUDED per F2 NEW empirical-coverage discipline (FL sends "
     "'Olympiacos' spelling without k at ~41/7d Day-32 discovery — "
     "both transliterations required per F3 Greek-to-Latin "
     "transliteration handling). Asterisk-suffix 'Olympiacos *' "
     "belt-and-suspenders. 'BC Olympiakos Piraeus' EuroCup-prefixed "
     "form (crossover with Turkish BSL Fenerbahce)"),

    ("GS Karditsa", "GRC",
     ("GS Karditsa", "Karditsa", "Karditsa BC"),
     "HEBA A1; INSERT (no clean Phase 2A.5 BACKFILL — AS Karditsas "
     "(c7da3b82) and Karditsa Iaponiki (77ed94bd) are SEPARATE "
     "entities, not aliased here). 'Geoponiki Syllogos Karditsa' "
     "= Agricultural Society Karditsa. Low FL volume expected; "
     "scope-doc §6.3 follow-up if production sends different form"),

    ("Iraklis BC", "GRC",
     ("Iraklis BC", "Iraklis", "BC Iraklis"),
     "HEBA A1; BACKFILL — Phase 2A.5 legacy stub "
     "c17fa0b9-bad0-4027-9a96-8f50584873fb (created 2026-05-08). "
     "CROSS-SPORT COLLISION: Iraklis FC (Greek Super League "
     "football). DORMANT PHANTOM RISK: 'Iraklis' bare form may "
     "collide with phantom b0602d2c (Amendment #22 pre-apply audit "
     "discipline applies; post-apply remediation if collision detected)"),

    ("Kolossos Rhodes", "GRC",
     ("Kolossos Rhodes", "Kolossos", "BC Kolossos Rhodes",
      "Kolossos Rodou"),
     "HEBA A1; BACKFILL — Phase 2A.5 legacy stub "
     "ca5f6d4a-f75d-45ea-8a26-e610b40dbf31 (created 2026-05-08). "
     "GREEK TRANSLITERATION: 'Kolossos Rhodes' English + 'Kolossos "
     "Rodou' Greek genitive form (Rhodes → Rodou) both required per "
     "F3. DORMANT PHANTOM RISK: 'Kolossos Rodou' alias may collide "
     "with phantom 7260b8e5 (Amendment #22 audit). 'BC Kolossos "
     "Rhodes' EuroCup-prefixed form. Discovery query ~28/7d Day-32"),

    ("Maroussi BC", "GRC",
     ("Maroussi BC", "Maroussi", "BC Maroussi"),
     "HEBA A1; BACKFILL — Phase 2A.5 legacy stub "
     "d8e37aa5-bfd3-4555-b5a1-f6173b034d12 (created 2026-05-08). "
     "DORMANT PHANTOM RISK: 'Maroussi' bare may collide with phantom "
     "11fb2774 (Amendment #22 audit). Maroussi is Athens suburb "
     "(distinct from 'Marousi' transliteration variant)"),

    ("Mykonos", "GRC",
     ("Mykonos", "BC Mykonos", "AO Mykonou"),
     "HEBA A1; BACKFILL — Phase 2A.5 legacy stub "
     "2f32272a-a077-43db-a024-75326f688acd (created 2026-05-08). "
     "Newly promoted; low FL volume expected in 7-day window. "
     "'AO Mykonou' = Athletic Organization Mykonou (Greek form)"),

    ("PAOK BC", "GRC",
     ("PAOK BC", "PAOK", "BC PAOK"),
     "HEBA A1; BACKFILL — Phase 2A.5 legacy stub "
     "59eb93a6-fa3c-44f1-80c0-a67c5783352a (created 2026-05-08). "
     "CROSS-SPORT COLLISION: PAOK FC (top-5 Greek Super League "
     "football recognition). Bare 'PAOK' INCLUDED per F2 NEW "
     "empirical-coverage discipline. 'PAOK' = Panthessalonikeios "
     "Athlitikos Omilos Konstantinoupoliton (Pan-Thessaloniki "
     "Athletic Club of Constantinopolitans)"),

    ("Panathinaikos BC", "GRC",
     ("Panathinaikos BC", "Panathinaikos", "BC Panathinaikos"),
     "HEBA A1; BACKFILL — Phase 2A.5 legacy stub "
     "6e1268f8-46dc-431d-a38c-9f0924c6922b (created 2026-05-08). "
     "CROSS-SPORT COLLISION: Panathinaikos FC (top-5 Greek Super "
     "League football recognition). Bare 'Panathinaikos' INCLUDED "
     "per F2 NEW empirical-coverage discipline (FL sends 'Panathinaikos' "
     "form at ~14/7d Day-32 discovery). 6× EuroLeague champion "
     "(1996, 2000, 2002, 2007, 2009, 2011); EuroLeague crossover "
     "expected in F7 post-apply"),

    ("Panionios", "GRC",
     ("Panionios", "Panionios BC", "BC Panionios"),
     "HEBA A1; BACKFILL — Phase 2A.5 legacy stub "
     "380f47bc-1057-4030-9064-f8896dc6e779 (created 2026-05-08). "
     "CROSS-SPORT COLLISION: Panionios FC (Greek Super League "
     "football). Sport_id partition handles disambiguation per "
     "Day-22 finding"),

    ("Peristeri BC", "GRC",
     ("Peristeri BC", "Peristeri", "BC Peristeri"),
     "HEBA A1; BACKFILL — Phase 2A.5 legacy stub "
     "6a00a818-b27a-4cbe-b1b5-dfd2a7364a9c (created 2026-05-08). "
     "DORMANT PHANTOM RISK: 'Peristeri' bare may collide with "
     "phantom 0c6092b5 (Amendment #22 audit). Peristeri is Athens "
     "suburb"),

    ("Promitheas Patras BC Vikos Cola", "GRC",
     ("Promitheas Patras BC Vikos Cola", "Promitheas",
      "Promitheas Patras", "BC Promitheas"),
     "HEBA A1; BACKFILL — Phase 2A.5 legacy stub "
     "eb0e7a18-7498-46aa-bf13-06b38c190795 (created 2026-05-08). "
     "Sponsor-prefixed legacy canonical 'Vikos Cola' retained per "
     "F1 discipline (legacy canonical is production-anchor; F1 "
     "amendment #12 authoritative-source primacy). DORMANT PHANTOM "
     "RISK: 'Promitheas' bare may collide with phantom fca05a4b "
     "(Amendment #22 audit). EA Promitheas 2014 (4180be23) is "
     "SEPARATE youth/reserve entity — NOT aliased here"),
]
