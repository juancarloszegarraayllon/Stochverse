"""Liga ACB (Spanish Basketball League) seed manifest — Phase 2D.5-A.

Data-driven league bootstrap: Liga ACB teams identified via
asymmetric_anchor_failure resolver signal (Day-27 diagnostic).
~400 records/week resolving to review_queue because sp.teams has no
Basketball-sport canonical for Spanish league team names.

18 teams for the 2025-26 season.

## Source

Source: 2025-26 Liga ACB season (operator-verified via Wikipedia
teams table, with team-specific Wikipedia pages cross-referenced).

Sponsor-prefixed forms from Wikipedia 2025-26 ACB teams table:
  Asisa Joventut, Barça, Bàsquet Girona, Baxi Manresa, Casademont
  Zaragoza, Coviran Granada, Dreamland Gran Canaria, Hiopos Lleida,
  Kosner Baskonia, La Laguna Tenerife, MoraBanc Andorra, Real Madrid,
  Recoletas Salud San Pablo Burgos, Río Breogán, Surne Bilbao,
  UCAM Murcia, Unicaja, Valencia Basket

## Canonical_name policy (F1)

Use SPORT-HISTORICAL canonical names, NOT current sponsor forms.
Sponsor names change yearly; the basketball-specific club identity
is stable. Examples:
  - "Real Madrid Baloncesto" (canonical) ← "Real Madrid" (alias)
  - "FC Barcelona Bàsquet" (canonical) ← "Barça", "Barcelona" (aliases)
  - "Saski Baskonia" (canonical) ← "Kosner Baskonia" (current sponsor)
  - "Club Joventut Badalona" (canonical) ← "Asisa Joventut" (sponsor)

## Alias distinctiveness (F2)

Bare city/short forms safe under sport_id=basketball partition
(resolver/aliases.py:51,111). Day-22 finding: sport_id partition
prevents cross-sport collision at matcher layer (no risk of FL
"Real Madrid" basketball record matching the soccer canonical).

Within-league check: no two ACB teams share the same normalized
alias. Bare city forms (Madrid, Barcelona, Andorra, Manresa, etc.)
each map to exactly one ACB team.

Exception: "Madrid" bare alias INTENTIONALLY EXCLUDED from Real
Madrid Baloncesto — too generic, may collide with future Madrid-
area basketball clubs (Estudiantes, Movistar Estudiantes if they
return to ACB).

## Source value (Q3)

bootstrap_league_coverage (cohort-wide convention).

## Re-curation runbook

Liga ACB rosters churn via promotion/relegation; sponsor names
churn yearly. When updates are needed:
  1. Visit acb.com/clubes and Wikipedia's 2025-26 Liga ACB page
  2. For each team: confirm sport-historical canonical unchanged;
     add new sponsor alias; retain old sponsor aliases (historical
     records still reference them)
  3. For promoted teams: add new canonical row + aliases
  4. For relegated teams: leave canonical in place (historical FL
     records still resolve via strict tier); flag in notes
  5. Run --dry-run; apply; verify via F7 query
"""
from __future__ import annotations


LIGA_ACB_ALIAS_SOURCE = "bootstrap_league_coverage"


# Format: (canonical_name, country_code, aliases_tuple, notes)
#
# All 18 Liga ACB teams, 2025-26 season.
# Aliases include: current sponsor form, sport-historical short form,
# bare city form, ASCII + accented diacritic pairs, historical sponsors
# still appearing in production records.
LIGA_ACB_TEAMS_SEED: list[tuple[str, str, tuple[str, ...], str]] = [
    ("BC Andorra", "AND",
     ("MoraBanc Andorra", "BC Andorra", "Bàsquet Club Andorra",
      "Basquet Club Andorra", "Andorra"),
     "Liga ACB; Andorran club competing in Spanish league; "
     "country_code='AND' per sp.teams convention (geographic residence)"),

    ("Basket Zaragoza", "ESP",
     ("Casademont Zaragoza", "Basket Zaragoza", "Basket Zaragoza 2002",
      "Zaragoza", "CAI Zaragoza", "Tecnyconta Zaragoza"),
     "Liga ACB; 'Casademont' = current sponsor; 'Basket Zaragoza 2002' "
     "= production form (Day-27 unresolved-strings); 'CAI' + 'Tecnyconta' "
     "= historical sponsors"),

    ("Bàsquet Girona", "ESP",
     ("Bàsquet Girona", "Basquet Girona", "Girona"),
     "Liga ACB; no current sponsor; founded by Marc Gasol in 2014"),

    ("Bàsquet Manresa", "ESP",
     ("BAXI Manresa", "Baxi Manresa", "Bàsquet Manresa",
      "Basquet Manresa", "CB Manresa", "Manresa"),
     "Liga ACB; 'BAXI' = current sponsor; 'CB Manresa' = legacy abbreviation"),

    ("Bilbao Basket", "ESP",
     ("Surne Bilbao", "Surne Bilbao Basket", "Bilbao Basket",
      "Bilbao", "Gescrap Bizkaia"),
     "Liga ACB; 'Surne' = current sponsor; 'Gescrap Bizkaia' = old sponsor"),

    ("CB Canarias", "ESP",
     ("La Laguna Tenerife", "CB Canarias", "Club Baloncesto Canarias",
      "CB 1939 Canarias", "Lenovo Tenerife", "Iberostar Tenerife",
      "Tenerife"),
     "Liga ACB; 'La Laguna Tenerife' = current branding; '1939' = founding "
     "year; 'Lenovo' + 'Iberostar' = old sponsors; FL sends 'CB 1939 "
     "Canarias' per Day-27 data"),

    ("CB Gran Canaria", "ESP",
     ("Dreamland Gran Canaria", "CB Gran Canaria",
      "Club Baloncesto Gran Canaria", "Gran Canaria",
      "Herbalife Gran Canaria"),
     "Liga ACB; 'Dreamland' = current sponsor; 'Herbalife' = previous "
     "sponsor; FL sends 'CB Gran Canaria' per Day-27 data"),

    ("CB San Pablo Burgos", "ESP",
     ("Recoletas Salud San Pablo Burgos", "CB San Pablo Burgos",
      "San Pablo Burgos", "Hereda San Pablo Burgos", "Burgos"),
     "Liga ACB; 'Recoletas Salud' = current sponsor; 'Hereda' = old "
     "sponsor; FL sends 'CB San Pablo Burgos' per Day-27 data"),

    ("Club Joventut Badalona", "ESP",
     ("Asisa Joventut", "Joventut Badalona", "Club Joventut Badalona",
      "Joventut", "Penya", "Divina Seguros Joventut", "Badalona"),
     "Liga ACB; 'Asisa' = current sponsor; 'Penya' = nickname; 'Divina "
     "Seguros' = old sponsor; FL sends 'Joventut Badalona' per Day-27 data"),

    ("FC Barcelona Bàsquet", "ESP",
     ("Barça", "Barcelona", "FC Barcelona Bàsquet",
      "FC Barcelona Basquet", "FC Barcelona Basket", "Barca"),
     "Liga ACB; CROSS-SPORT COLLISION RISK: FC Barcelona (Soccer) likely "
     "exists in sp.teams. Bare 'Barcelona' alias safe under sport_id "
     "partition (resolver/aliases.py:51,111). FL sends 'Barcelona' per "
     "Day-27 data"),

    ("Força Lleida CE", "ESP",
     ("Hiopos Lleida", "Força Lleida CE", "Forca Lleida CE",
      "Força Lleida", "Caprabo Lleida", "Lleida Bàsquet", "Lleida"),
     "Liga ACB; 'Hiopos' = current sponsor; 'Caprabo' = old sponsor seen "
     "in production data; 'Lleida Bàsquet' = predecessor club name"),

    ("Fundación CB Granada", "ESP",
     ("Coviran Granada", "Fundación CB Granada", "Fundacion CB Granada",
      "CB Granada", "Covirán Granada", "Granada"),
     "Liga ACB; 'Coviran' = current sponsor (with + without accent); "
     "Wikipedia canonical not directly verifiable (403 from en.wikipedia "
     "during draft); operator accepted 'Fundación CB Granada' per "
     "Wikipedia article-title convention; revisit if production data "
     "shows mismatch"),

    ("Real Madrid Baloncesto", "ESP",
     ("Real Madrid", "Real Madrid Baloncesto", "Madrid Baloncesto",
      "Real Madrid Basket"),
     "Liga ACB; CROSS-SPORT COLLISION RISK: Real Madrid CF (Soccer) exists "
     "in sp.teams. Bare 'Real Madrid' alias safe under sport_id partition; "
     "'Madrid' bare alias INTENTIONALLY EXCLUDED — too generic, may collide "
     "with future Madrid-area basketball clubs (Estudiantes etc.). FL sends "
     "'Real Madrid' per Day-27 data (35 records/7d)"),

    ("Río Breogán", "ESP",
     ("Río Breogán", "Rio Breogan", "Breogán", "Breogan",
      "CB Breogán", "CB Breogan"),
     "Liga ACB; Lugo-based; 'Río' = part of name (not sponsor); ASCII "
     "variants for normalizer NFD parity"),

    ("Saski Baskonia", "ESP",
     ("Kosner Baskonia", "Baskonia", "Saski Baskonia",
      "TD Systems Baskonia", "Baskonia Vitoria"),
     "Liga ACB; 'Kosner' = current sponsor; 'TD Systems' = old sponsor; "
     "based in Vitoria-Gasteiz; FL sends 'Baskonia' per Day-27 data"),

    ("UCAM Murcia CB", "ESP",
     ("UCAM Murcia", "UCAM Murcia CB", "CB Murcia", "Murcia"),
     "Liga ACB; 'UCAM' = longstanding sponsor (effectively part of "
     "identity, no rebrand pressure); included in canonical_name for "
     "stability"),

    ("Unicaja Málaga", "ESP",
     ("Unicaja", "Unicaja Málaga", "Unicaja Malaga", "CB Málaga",
      "CB Malaga", "Málaga", "Malaga"),
     "Liga ACB; 'Unicaja' = longstanding sponsor (decades, part of "
     "identity); ASCII + accented variants"),

    ("Valencia Basket", "ESP",
     ("Valencia Basket", "Valencia Basket Club", "Pamesa Valencia",
      "Valencia"),
     "Liga ACB; no current sponsor; 'Pamesa' = historical sponsor"),
]
