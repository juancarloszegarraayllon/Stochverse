"""LMB (Liga Mexicana de Béisbol) seed manifest — Phase 2D.5-A.

Data-driven league bootstrap: LMB teams identified via
asymmetric_anchor_failure resolver signal (Day-27 diagnostic).
~600 records/week resolving to review_queue because sp.teams has
no Baseball-sport canonical for Mexican league team names.

16 teams across two zones (Norte + Sur) for the 2026 season.

## Source

Source: Liga Mexicana de Béisbol 2026 season rosters.
Cross-referenced:
  - en.wikipedia.org/wiki/Liga_Mexicana_de_Béisbol (team list)
  - lmb.com.mx (official league site, team pages)
  - baseball-reference.com/register (Mexican League historical)

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

LMB membership is stable (16 teams since 2017). Sponsor changes are
rare. When updates are needed:
  1. Visit lmb.com.mx and compare team names
  2. Add new aliases for renamed teams; keep legacy aliases
  3. Verify no within-baseball bare-name collisions
  4. Run --dry-run against Neon dev branch
  5. Apply to production; verify via F7 query
"""
from __future__ import annotations


LMB_ALIAS_SOURCE = "bootstrap_league_coverage"


# Format: (canonical_name, country_code, aliases_tuple, notes)
#
# All 16 LMB teams, 2026 season.
# Aliases include: city-only short form, nickname-only form,
# accented + ASCII-stripped variants where applicable.
LMB_TEAMS_SEED: list[tuple[str, str, tuple[str, ...], str]] = [
    # ── Zona Norte ──
    ("Sultanes de Monterrey", "MEX",
     ("Monterrey", "Sultanes", "Sultanes de Monterrey"),
     "LMB Norte; most-titled LMB franchise"),
    ("Acereros de Monclova", "MEX",
     ("Monclova", "Acereros", "Acereros de Monclova"),
     "LMB Norte"),
    ("Saraperos de Saltillo", "MEX",
     ("Saltillo", "Saraperos", "Saraperos de Saltillo"),
     "LMB Norte"),
    ("Algodoneros de Unión Laguna", "MEX",
     ("Unión Laguna", "Union Laguna", "Algodoneros",
      "Algodoneros de Unión Laguna", "Algodoneros de Union Laguna",
      "Laguna"),
     "LMB Norte; diacritic variant included"),
    ("Toros de Tijuana", "MEX",
     ("Tijuana", "Toros", "Toros de Tijuana"),
     "LMB Norte"),
    ("Águilas de Mexicali", "MEX",
     ("Mexicali", "Águilas", "Aguilas", "Águilas de Mexicali",
      "Aguilas de Mexicali"),
     "LMB Norte; diacritic variant included"),
    ("Mariachis de Guadalajara", "MEX",
     ("Guadalajara", "Mariachis", "Mariachis de Guadalajara"),
     "LMB Norte"),
    ("Bravos de León", "MEX",
     ("León", "Leon", "Bravos", "Bravos de León", "Bravos de Leon"),
     "LMB Norte; diacritic variant included"),

    # ── Zona Sur ──
    ("Diablos Rojos del México", "MEX",
     ("México", "Mexico", "Diablos Rojos", "Diablos",
      "Diablos Rojos del México", "Diablos Rojos del Mexico"),
     "LMB Sur; based in Mexico City"),
    ("Tigres de Quintana Roo", "MEX",
     ("Quintana Roo", "Tigres de Quintana Roo"),
     "LMB Sur; 'Tigres' bare excluded — collides with other LMB Tigres usage"),
    ("Leones de Yucatán", "MEX",
     ("Yucatán", "Yucatan", "Leones", "Leones de Yucatán",
      "Leones de Yucatan"),
     "LMB Sur; diacritic variant included"),
    ("Piratas de Campeche", "MEX",
     ("Campeche", "Piratas", "Piratas de Campeche"),
     "LMB Sur"),
    ("Olmecas de Tabasco", "MEX",
     ("Tabasco", "Olmecas", "Olmecas de Tabasco"),
     "LMB Sur"),
    ("Pericos de Puebla", "MEX",
     ("Puebla", "Pericos", "Pericos de Puebla"),
     "LMB Sur"),
    ("Guerreros de Oaxaca", "MEX",
     ("Oaxaca", "Guerreros", "Guerreros de Oaxaca"),
     "LMB Sur"),
    ("El Águila de Veracruz", "MEX",
     ("Veracruz", "Águila", "Aguila", "El Águila de Veracruz",
      "El Aguila de Veracruz", "Águila de Veracruz",
      "Aguila de Veracruz"),
     "LMB Sur; diacritic variant included; note 'El' article in official name"),
]
