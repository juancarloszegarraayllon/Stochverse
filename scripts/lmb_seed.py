"""LMB (Liga Mexicana de Béisbol) seed manifest — Phase 2D.5-A.

Data-driven league bootstrap: LMB teams identified via
asymmetric_anchor_failure resolver signal (Day-27 diagnostic).
~600 records/week resolving to review_queue because sp.teams has
no Baseball-sport canonical for Mexican league team names.

20 teams across two zones of 10 (Norte + Sur) for the 2026 season.

## Source

Source: Liga Mexicana de Béisbol 2026 season roster.
Primary verification:
  - Posta Deportes (April 2026):
    postadeportes.com/beisbol/general/lmb-estos-son-los-equipos-
    que-participaran-en-la-temporada-2026/vl2044090
Cross-referenced:
  - lmb.com.mx (official league site, team pages)
  - baseball-reference.com/register (Mexican League historical)

Note: 2026 season has 20 teams (expanded from 16 in prior seasons).
Águilas de Mexicali and Mariachis de Guadalajara are NOT in the 2026
roster — they were in prior seasons. Caliente de Durango, Charros de
Jalisco, Dorados de Chihuahua, Rieleros de Aguascalientes, Tecolotes
de los Dos Laredos, and Conspiradores de Querétaro are 2026 additions
or returnees.

## Canonical_name policy (F1)

Full official team name as canonical (e.g., "Sultanes de Monterrey").
Short forms (city-only: "Monterrey") and nickname-only ("Sultanes")
as aliases. Matches KBL F1 precedent.

## Alias distinctiveness (F2)

Bare city-name aliases are safe within sport_id=baseball because:
  - AliasIndex partitions by (alias_normalized, sport_id)
  - No within-baseball collision risk (one LMB team per city)
  - Cross-sport "Monterrey" (soccer) is under a different sport_id

Exception: "Tigres" as a bare alias excluded — collides with
"Tigres de Quintana Roo" within the same sport_id. Both LMB teams
share the "Tigres" nickname. City-prefixed aliases used instead.

## Source value (Q3)

bootstrap_league_coverage (cohort-wide convention per KBL Q3 decision).

## Re-curation runbook

LMB expanded to 20 teams for 2026. When updates are needed:
  1. Visit lmb.com.mx and compare team names against this manifest
  2. Add new aliases for renamed teams; keep legacy aliases
  3. Verify no within-baseball bare-name collisions
  4. Run --dry-run against Neon dev branch
  5. Apply to production; verify via F7 query
"""
from __future__ import annotations


LMB_ALIAS_SOURCE = "bootstrap_league_coverage"


# Format: (canonical_name, country_code, aliases_tuple, notes)
#
# All 20 LMB teams, 2026 season.
# Aliases include: city-only short form, nickname-only form,
# accented + ASCII-stripped variants where applicable.
LMB_TEAMS_SEED: list[tuple[str, str, tuple[str, ...], str]] = [
    # ── Zona Norte (10 teams) ──
    ("Acereros de Monclova", "MEX",
     ("Monclova", "Acereros", "Acereros de Monclova"),
     "LMB Norte"),
    ("Algodoneros de Unión Laguna", "MEX",
     ("Unión Laguna", "Union Laguna", "Algodoneros",
      "Algodoneros de Unión Laguna", "Algodoneros de Union Laguna",
      "Laguna"),
     "LMB Norte; diacritic variant included"),
    ("Caliente de Durango", "MEX",
     ("Durango", "Caliente", "Caliente de Durango"),
     "LMB Norte; 2026 roster addition"),
    ("Charros de Jalisco", "MEX",
     ("Jalisco", "Charros", "Charros de Jalisco"),
     "LMB Norte; 2026 Caribbean Series champion"),
    ("Dorados de Chihuahua", "MEX",
     ("Chihuahua", "Dorados", "Dorados de Chihuahua"),
     "LMB Norte; 2026 roster addition"),
    ("Rieleros de Aguascalientes", "MEX",
     ("Aguascalientes", "Rieleros", "Rieleros de Aguascalientes"),
     "LMB Norte"),
    ("Saraperos de Saltillo", "MEX",
     ("Saltillo", "Saraperos", "Saraperos de Saltillo"),
     "LMB Norte"),
    ("Sultanes de Monterrey", "MEX",
     ("Monterrey", "Sultanes", "Sultanes de Monterrey"),
     "LMB Norte; most-titled LMB franchise"),
    ("Tecolotes de los Dos Laredos", "MEX",
     ("Dos Laredos", "Laredos", "Tecolotes",
      "Tecolotes de los Dos Laredos"),
     "LMB Norte; bi-national team (Laredo TX + Nuevo Laredo)"),
    ("Toros de Tijuana", "MEX",
     ("Tijuana", "Toros", "Toros de Tijuana"),
     "LMB Norte"),

    # ── Zona Sur (10 teams) ──
    ("Bravos de León", "MEX",
     ("León", "Leon", "Bravos", "Bravos de León", "Bravos de Leon"),
     "LMB Sur; diacritic variant included"),
    ("Conspiradores de Querétaro", "MEX",
     ("Querétaro", "Queretaro", "Conspiradores",
      "Conspiradores de Querétaro", "Conspiradores de Queretaro"),
     "LMB Sur; 161 records/week in Day-27 unresolved-strings query; diacritic variant included"),
    ("Diablos Rojos del México", "MEX",
     ("México", "Mexico", "Diablos Rojos", "Diablos",
      "Diablos Rojos del México", "Diablos Rojos del Mexico"),
     "LMB Sur; based in Mexico City"),
    ("El Águila de Veracruz", "MEX",
     ("Veracruz", "Águila", "Aguila", "El Águila de Veracruz",
      "El Aguila de Veracruz", "Águila de Veracruz",
      "Aguila de Veracruz"),
     "LMB Sur; diacritic variant included; note 'El' article in official name"),
    ("Guerreros de Oaxaca", "MEX",
     ("Oaxaca", "Guerreros", "Guerreros de Oaxaca"),
     "LMB Sur"),
    ("Leones de Yucatán", "MEX",
     ("Yucatán", "Yucatan", "Leones", "Leones de Yucatán",
      "Leones de Yucatan"),
     "LMB Sur; diacritic variant included"),
    ("Olmecas de Tabasco", "MEX",
     ("Tabasco", "Olmecas", "Olmecas de Tabasco"),
     "LMB Sur"),
    ("Pericos de Puebla", "MEX",
     ("Puebla", "Pericos", "Pericos de Puebla"),
     "LMB Sur"),
    ("Piratas de Campeche", "MEX",
     ("Campeche", "Piratas", "Piratas de Campeche"),
     "LMB Sur"),
    ("Tigres de Quintana Roo", "MEX",
     ("Quintana Roo", "Tigres de Quintana Roo"),
     "LMB Sur; 'Tigres' bare excluded — collides with other LMB Tigres usage"),
]
