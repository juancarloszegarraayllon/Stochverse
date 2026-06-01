"""Italian LBA Serie A (Lega Basket Serie A) seed manifest — Phase 2D.5-A.

Data-driven league bootstrap: Italian LBA teams identified via
asymmetric_anchor_failure resolver signal (Day-28/Day-30 diagnostic).
~110 records/week resolving to review_queue because sp.teams has no
Basketball-sport canonical for Italian league team names.

16 teams for the 2025-26 season. Workstream #3 of Phase 2D.5-A per
`docs/bootstraps/phase-2d5a-sequencing-decision.md` (Italian LBA
selected over EuroLeague default; cleaner methodology iteration
mirroring Liga ACB cross-sport collision pattern in a new country
with tighter single-country scope).

## Source

Source: 2025-26 LBA season Wikipedia roster table (operator-verified
paste, Day-30). All 16 teams listed in the season's regular-phase
table at the time of bootstrap.

Operator-pasted authoritative roster:

    Dinamo Sassari, Derthona Basket, Aquila Basket Trento,
    Olimpia Milano, APU Udine, Pallacanestro Brescia,
    Pallacanestro Cantù, Napoli Basket, Universo Treviso Basket,
    Pallacanestro Varese, Pallacanestro Trieste 2004,
    Trapani Shark, Reyer Venezia, Pallacanestro Reggiana,
    Vanoli Cremona, Virtus Bologna

## Canonical_name policy (F1)

Use SPORT-HISTORICAL / HERITAGE canonical names, NOT current sponsor
forms. Sponsor names change yearly; the basketball-specific club
identity is stable. Examples:

  - "Olimpia Milano" (canonical) ← "EA7 Emporio Armani Milano" (alias)
  - "Pallacanestro Brescia" (canonical) ← "Germani Brescia" (alias)
  - "Pallacanestro Reggiana" (canonical) ← "UnaHotels Reggio Emilia" (alias)
  - "Aquila Basket Trento" (canonical) ← "Dolomiti Energia Trentino" (alias)
  - "Derthona Basket" (canonical) ← "Bertram Yachts Tortona" (alias)

Mirrors LMB and Liga ACB F1 precedent. Same Bravos de León /
Real Madrid Baloncesto / FC Barcelona Bàsquet policy applied to LBA.

## Alias distinctiveness (F2) — cross-sport collision discipline

Bare city/short forms safe under sport_id=basketball partition
(resolver/aliases.py:51,111). Day-22 finding: sport_id partition
prevents cross-sport collision at matcher layer.

EXCLUDED bare-city aliases (cross-sport collision risk with Italian
Serie A football clubs):
  - "Milano" — AC Milan / Inter Milan (football). Use "Olimpia",
    "EA7", "Armani" as sport-disambiguators on Olimpia Milano.
  - "Bologna" — Bologna FC (football). Use "Virtus" + always-
    qualified "Virtus Bologna".
  - "Napoli" — SSC Napoli (football). Use "Basket Napoli" /
    "Gevi Napoli" / always-qualified Napoli Basket.
  - "Venezia" — Venezia FC (football, Serie A). Use "Reyer" /
    "Reyer Venezia" as sport-disambiguator.

EXCLUDED bare alias for within-LBA disambiguation:
  - "Virtus" — multiple Italian basketball clubs use "Virtus"
    (Bologna LBA, Roma 1960 in lower tiers, Cassino, etc.).
    Always qualify with city.

SAFE bare-city aliases (per operator paste list, Day-30):
  Trieste, Brescia, Trento, Sassari, Tortona, Treviso, Cantu,
  Reggiana, Varese, Cremona, Trapani, Udine.

## Discovery query coverage (F7 / Pattern A.2)

Day-28/Day-30 production sp.resolution_log discovery query
identified Italian-city provider strings routing to
asymmetric_anchor_failure. The 10 LBA-in-scope provider forms:

  Brescia (28/7d), Brescia * (14/7d, asterisk-suffix variant),
  Trieste (28/7d), Olimpia Milano (28/7d), Reggiana (28/7d),
  Tortona (14/7d), Treviso (14/7d), Cantu (14/7d),
  Basket Napoli (14/7d), Sassari (14/7d)

Out-of-scope provider forms (LBA Serie A only; Serie A2/B excluded):
  Fortitudo Bologna (28/7d) — Fortitudo NOT in 2025-26 LBA Serie A;
    plays Serie A2 per Wikipedia. Acknowledged noise; investigate
    FL sport_id misclassification as follow-up.
  Verona / Verona * (28+14/7d) — Tezenis Verona plays Serie A2 in
    2025-26 per Wikipedia, not LBA. Same noise pattern.
  Virtus Gvm Roma 1960 (10/7d) — Serie A2/B. Excluded per scope.
  Rucker San Vendemiano (6/7d) — Serie A2/B. Excluded per scope.

Every in-scope provider form above MUST appear as an alias on the
mapped canonical (verified by tests/test_bootstrap_lba.py
TestLBADiscoveryTargets).

## Asterisk-suffix handling

Day-30 discovery surfaced provider strings with trailing "*" suffix
("Brescia *", "Verona *"). Source of the asterisk is not yet
characterized (filed as follow-up). Manifest includes "Brescia *"
as an alias on Pallacanestro Brescia so the production strings
route to strict tier post-apply. "Verona *" is out-of-scope
because Verona itself is out-of-scope.

## Trapani Shark — open question

A WebSearch snippet (Day-30) referenced "Trapani Shark excluded on
2026-01-12." Wikipedia roster for 2025-26 LBA still lists Trapani
Shark among the 16 teams. Unclear whether exclusion was:

  - mid-season (post-2026-01-12) LBA exclusion — they were in at
    start, exited later
  - exclusion from a specific competition only (Coppa Italia,
    EuroCup qualifier)
  - different interpretation entirely

Decision: include in manifest per Wikipedia roster. If F7 post-apply
shows zero Trapani strict resolutions over a 7-day window, revisit.
Idempotent script means follow-up REMOVE-from-manifest is also safe.

## Source value (Q3)

bootstrap_league_coverage (cohort-wide convention; same as LMB,
Liga ACB, KBL).

## Re-curation runbook

LBA rosters churn via promotion/relegation; sponsor names churn
yearly. When updates are needed:

  1. Visit legabasket.it/teams and Wikipedia's 2026-27 (or current)
     LBA season page
  2. For each team: confirm sport-historical canonical unchanged;
     add new sponsor alias; retain old sponsor aliases (historical
     records still reference them)
  3. For promoted teams: add new canonical row + aliases
  4. For relegated teams: leave canonical in place (historical FL
     records still resolve via strict tier); flag in notes
  5. Run --dry-run; apply; verify via F7 query (JOIN to sp.fixtures
     + sp.teams + sp.teams country_code='ITA' filter; do NOT rely
     on reason_detail->>'home_provider_normalized' per Day-29
     finding about sparse FL strict-tier reason_detail)
"""
from __future__ import annotations


LBA_ALIAS_SOURCE = "bootstrap_league_coverage"


# Format: (canonical_name, country_code, aliases_tuple, notes)
#
# All 16 LBA Serie A teams, 2025-26 season.
# Aliases include: current sponsor form, sport-historical short
# form, bare city form (where collision-safe), discovery-query
# provider forms, asterisk-suffix variants where applicable,
# ASCII + diacritic pairs.
LBA_TEAMS_SEED: list[tuple[str, str, tuple[str, ...], str]] = [
    ("Aquila Basket Trento", "ITA",
     ("Dolomiti Energia Trentino", "Dolomiti Energia Trento",
      "Aquila Basket Trento", "Aquila Basket", "Aquila Trento",
      "Trento"),
     "LBA; 'Dolomiti Energia' longstanding regional-utility sponsor; "
     "'Aquila' (eagle) heritage moniker; bare 'Trento' safe (no LBA "
     "naming collision, no major Italian football club)"),

    ("APU Udine", "ITA",
     ("Old Wild West Udine", "APU Udine", "APU Old Wild West Udine",
      "APU", "Udine"),
     "LBA; promoted to top flight 2025-26; 'Old Wild West' current "
     "sponsor; 'APU' (Associazione Pallacanestro Udinese) heritage "
     "abbreviation; bare 'Udine' safe per operator paste (Udinese "
     "football exists but sport_id partition handles disambiguation "
     "at matcher layer per Day-22 finding)"),

    ("Derthona Basket", "ITA",
     ("Bertram Yachts Tortona", "Bertram Derthona", "Derthona Basket",
      "Derthona", "Derthona Tortona", "Bertram Tortona", "Tortona"),
     "LBA; based in Tortona; 'Derthona' = Latin name for Tortona, "
     "heritage moniker; 'Bertram Yachts' current sponsor; FL/Kalshi "
     "send 'Tortona' per Day-28/30 discovery (14/7d)"),

    ("Dinamo Sassari", "ITA",
     ("Banco di Sardegna Sassari", "Dinamo Sassari",
      "Dinamo Banco di Sardegna Sassari", "Dinamo", "Sassari"),
     "LBA; 'Banco di Sardegna' longstanding regional-bank sponsor "
     "(decades, part of identity); 'Dinamo' heritage moniker; bare "
     "'Sassari' safe per operator paste (14/7d in Day-30 discovery)"),

    ("Napoli Basket", "ITA",
     ("Gevi Napoli Basket", "Napoli Basket", "Basket Napoli",
      "GeVi Napoli", "Napoli Pallacanestro"),
     "LBA; CROSS-SPORT COLLISION RISK: SSC Napoli (Serie A football) "
     "exists. Bare 'Napoli' INTENTIONALLY EXCLUDED. 'Basket Napoli' "
     "production form (14/7d in Day-30 discovery) is sport-"
     "disambiguated and included as alias"),

    ("Olimpia Milano", "ITA",
     ("EA7 Emporio Armani Milano", "Olimpia Milano", "Armani Milano",
      "Olimpia EA7 Milano", "AX Armani Exchange Milano",
      "Pallacanestro Olimpia Milano", "EA7 Milano", "Olimpia"),
     "LBA; CROSS-SPORT COLLISION RISK: AC Milan + Inter Milan "
     "(both Serie A football) exist. Bare 'Milano' INTENTIONALLY "
     "EXCLUDED. 'Olimpia' + 'EA7' + 'Armani' all safe sport-"
     "disambiguators. FL/Kalshi send 'Olimpia Milano' per Day-28/30 "
     "discovery (28/7d). 7× EuroLeague champion; Italian basketball "
     "flagship club"),

    ("Pallacanestro Brescia", "ITA",
     ("Germani Brescia", "Pallacanestro Brescia", "Brescia",
      "Brescia *", "Basket Brescia Leonessa", "Brescia Leonessa",
      "Leonessa Brescia"),
     "LBA; 'Germani' (consumer-electronics) current sponsor; "
     "'Leonessa' (lioness) heritage moniker; bare 'Brescia' safe "
     "per operator paste. ASTERISK-SUFFIX: 'Brescia *' appears in "
     "Day-30 production discovery (14/7d) alongside non-asterisk "
     "form; included as alias pending asterisk-source investigation"),

    ("Pallacanestro Cantù", "ITA",
     ("Acqua S. Bernardo Cantù", "Acqua S. Bernardo Cantu",
      "Pallacanestro Cantù", "Pallacanestro Cantu", "Cantù", "Cantu"),
     "LBA; promoted to top flight 2025-26; 'Acqua S. Bernardo' "
     "(mineral water) current sponsor; ASCII + diacritic (ù → u) "
     "pairs for normalizer NFD parity. FL/Kalshi send 'Cantu' per "
     "Day-30 discovery (14/7d)"),

    ("Pallacanestro Reggiana", "ITA",
     ("UnaHotels Reggio Emilia", "Pallacanestro Reggiana", "Reggiana",
      "Reggio Emilia", "UnaHotels Reggiana", "UnaHotels Reggio"),
     "LBA; 'UnaHotels' current sponsor; 'Reggiana' heritage moniker; "
     "FL/Kalshi send 'Reggiana' per Day-28/30 discovery (28/7d) — "
     "highest LBA single-team discovery volume tied with Olimpia "
     "Milano, Trieste, and Brescia. AC Reggiana 1919 football club "
     "exists but sport_id partition makes 'Reggiana' safe at matcher "
     "layer (Day-22 finding)"),

    ("Pallacanestro Trieste 2004", "ITA",
     ("Allianz Pallacanestro Trieste", "Allianz Trieste",
      "Pallacanestro Trieste 2004", "Pallacanestro Trieste",
      "Trieste"),
     "LBA; '2004' founding-year disambiguator (new club after 2003-04 "
     "dissolution of predecessor Pallacanestro Trieste); 'Allianz' "
     "longstanding insurance sponsor; bare 'Trieste' safe per "
     "operator paste; FL/Kalshi send 'Trieste' per Day-28/30 "
     "discovery (28/7d)"),

    ("Pallacanestro Varese", "ITA",
     ("Openjobmetis Varese", "Pallacanestro Varese",
      "Pallacanestro Varese 1945", "Varese"),
     "LBA; 'Openjobmetis' current sponsor; '1945' founding-year "
     "qualifier in some communications; bare 'Varese' safe per "
     "operator paste"),

    ("Reyer Venezia", "ITA",
     ("Umana Reyer Venezia", "Reyer Venezia", "Reyer Venezia Mestre",
      "Reyer", "Pallacanestro Reyer Venezia 1872"),
     "LBA; 'Umana' current sponsor; 'Reyer' heritage name (founder "
     "Marco Foscari Reyer, 1872); bare 'Venezia' INTENTIONALLY "
     "EXCLUDED — Venezia FC (Serie A football) cross-sport collision "
     "risk; 'Reyer' is the safe sport-disambiguator"),

    ("Trapani Shark", "ITA",
     ("Trapani Shark", "Trapani Sharks", "Shark Trapani", "Trapani"),
     "LBA; new club (relatively recent founding); no longstanding "
     "sponsor pattern; bare 'Trapani' safe per operator paste. "
     "OPEN QUESTION: Day-30 WebSearch snippet referenced 'Trapani "
     "Shark excluded on 2026-01-12'; Wikipedia roster still lists "
     "them. Included pending F7 post-apply confirmation; safe to "
     "remove via re-run if F7 shows zero strict resolutions"),

    ("Universo Treviso Basket", "ITA",
     ("Universo Treviso Basket", "NutriBullet Treviso", "Treviso Basket",
      "Treviso", "TVB Treviso", "TVB"),
     "LBA; 'Universo' current sponsor; 'NutriBullet' previous sponsor; "
     "founded 2012 as successor to historic Pallacanestro Treviso "
     "(Benetton era); 'TVB' = Treviso Basket abbreviation; bare "
     "'Treviso' safe per operator paste; FL/Kalshi send 'Treviso' "
     "per Day-28/30 discovery (14/7d)"),

    ("Vanoli Cremona", "ITA",
     ("Vanoli Cremona", "Vanoli Basket Cremona",
      "Guerino Vanoli Basket", "Vanoli", "Cremona"),
     "LBA; 'Vanoli' longstanding sponsor (decades, effectively part "
     "of identity per Unicaja Málaga precedent — included in canonical "
     "for stability); 'Guerino Vanoli Basket' official long form; bare "
     "'Cremona' safe per operator paste"),

    ("Virtus Bologna", "ITA",
     ("Virtus Segafredo Bologna", "Virtus Bologna",
      "Virtus Pallacanestro Bologna", "Pallacanestro Virtus Bologna",
      "Segafredo Virtus Bologna", "Segafredo Bologna"),
     "LBA; CROSS-SPORT COLLISION RISK: Bologna FC (Serie A football) "
     "exists. Bare 'Bologna' INTENTIONALLY EXCLUDED. WITHIN-LBA "
     "DISAMBIGUATION: bare 'Virtus' INTENTIONALLY EXCLUDED — multiple "
     "Italian basketball clubs use 'Virtus' (Roma 1960 in Serie A2, "
     "Cassino, etc.); always qualify with 'Bologna'. 'Virtus Segafredo' "
     "current sponsor. 2024-25 LBA champion; EuroLeague entrant"),
]
