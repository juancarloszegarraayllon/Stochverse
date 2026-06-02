"""Turkish Basketbol Süper Ligi (BSL) seed manifest — Phase 2D.5-A workstream #5.

Data-driven league bootstrap: Turkish BSL teams identified via
asymmetric_anchor_failure resolver signal (Day-31 afternoon discovery
query, post-Israeli-BSL-apply). ~234 records/7d resolving to
review_queue because sp.teams has no Basketball-sport canonical for
Turkish league team names.

16 teams for the 2025-26 season. Workstream #5 of Phase 2D.5-A per
Day-31 re-sequencing in `docs/bootstraps/phase-2d5a-sequencing-decision.md`.

## Source

Source: 2025-26 Türkiye Basketbol Süper Ligi (BSL) Wikipedia roster
(operator-verified paste, Day-31 afternoon). All 16 teams listed at
the time of bootstrap.

## NEW METHODOLOGY DIMENSIONS THIS WORKSTREAM

### 1. Empirical-coverage discipline overrides operator-clarity for bare aliases

Israeli BSL workstream #4 (Day-31 afternoon) excluded bare-city
aliases for 11 football-overlap cities as operator-clarity discipline
(sport_id partition already handles matcher-layer safety). Turkish BSL
INCLUDES bare-name aliases for 5 football-overlap teams (Galatasaray,
Fenerbahçe, Beşiktaş, Trabzonspor, Bursaspor) because the Day-31
discovery shows production provider strings ARE the bare forms at
material rates (e.g., "Galatasaray" 30+/7d, "Besiktas *" 14+/7d,
"Fenerbahce" + variants 28+/7d).

When empirical data and operator-clarity conflict, **empirical data
wins**. Day-22 sport_id partition is the safety guarantee; the
operator-clarity layer is optional documentation discipline. This
refines the F2 framing established Day-29 (Liga ACB) and re-affirmed
Day-30 (Italian LBA) + Day-31 (Israeli BSL).

### 2. Diacritic empirical verification before manifest commit

Production sp.team_aliases inspection (Day-31 afternoon) confirmed
NFD normalizer correctly handles Turkish ş/ç/ü/ğ via decomposition +
combining-mark strip:

  - `Fenerbahçe` → NFD: `Fenerbahçe` (ç = c + ̧) → strip → `Fenerbahce`
  - `Beşiktaş` → `Besiktas`
  - `Büyükçekmece` → `Buyukcekmece`
  - `Türk Telekom` → `Turk Telekom`
  - `Bahçeşehir` → `Bahcesehir`
  - `Tofaş` → `Tofas`

For these characters, belt-and-suspenders ASCII-stripped pairs in the
manifest are functionally redundant (normalizer collapses them).
Pairs INCLUDED for documentation clarity.

**EXCEPTION — Turkish dotless `ı` (U+0131 LATIN SMALL LETTER DOTLESS
I) does NOT decompose under NFD.** It is a precomposed base letter
distinct from `i`. Local verification:

  - `Karşıyaka` → NFD → `Karşıyaka` (ş decomposes; ı does NOT) →
    strip + lower → `karsıyaka` (still has ı)
  - `Karsiyaka` → `karsiyaka` (regular i)
  - These produce DIFFERENT normalized keys.

For ı-containing teams (Karşıyaka Basket, Pınar Karşıyaka historical
sponsor variant), the ı and i variants are FUNCTIONALLY REQUIRED in
the manifest — not just documentation belt-and-suspenders. Production
strings sending either form must match.

Affected manifest teams:
  - Karşıyaka Basket: includes both "Karşıyaka Basket" + "Karsiyaka
    Basket" + "Pınar Karşıyaka" + "Pinar Karsiyaka" variants
  - Petkim Spor: "Aliağa" has ğ (decomposes correctly), no ı issue
  - Note: this scope-doc may be updated when normalizer is enhanced
    to map ı → i; not blocking; defensive coverage adequate

### 3. Canonical-name fragmentation pattern (dormant phantom acceptance)

2 manifest canonicals deliberately diverge from Phase 2A.5 legacy
stubs. Legacy stubs become DORMANT PHANTOMS — kept in sp.teams,
not BACKFILLed with country_code, no new bootstrap aliases added.

  - Manifest `Karşıyaka Basket` (NEW INSERT) vs legacy `Karşıyaka`
    (id ff68785a-0698-4934-b594-c68ccfdb1711, normalized `karsiyaka`).
    Normalized forms differ: manifest=`karsiyaka basket`,
    legacy=`karsiyaka`. Manifest follows Wikipedia 2025-26 roster
    exactly per F1 amendment #12.
  - Manifest `Türk Telekom Ankara` (NEW INSERT) vs legacy
    `Turk Telekom` (id d436ec55-a303-49a5-84af-0e3f0e90156b,
    normalized `turk telekom`). Normalized forms differ:
    manifest=`turk telekom ankara`, legacy=`turk telekom`.
    Manifest adds "Ankara" location-disambiguator per operator
    canonical-name policy.

Manifest does NOT add bare "Karşıyaka" / "Karsiyaka" / "Türk Telekom"
/ "Turk Telekom" aliases to the new canonicals. Production strings
sending bare legacy forms continue resolving to the dormant phantoms
via canonical_name lookup (still strict-tier, just no country_code).

**Methodology refinement**: when manifest canonical (per Wikipedia)
differs from legacy canonical, accept dormant phantom over canonical
compromise. Authoritative-source primacy (F1 amendment #12) overrides
BACKFILL coverage maximization.

## Canonical_name policy (F1)

Use HERITAGE / Wikipedia-canonical form, NOT current sponsor form.
Mirrors LMB, Liga ACB, Italian LBA, Israeli BSL F1 precedent. Examples:

  - "Galatasaray" canonical ← "Galatasaray MCT Technic" alias
  - "Fenerbahçe" canonical ← "Fenerbahçe Beko" alias
  - "Beşiktaş" canonical ← "Beşiktaş Gain" alias
  - "Manisa Basket" canonical ← "Glint Manisa Basket" alias
  - "Büyükçekmece Basketbol" canonical ← "ONVO Büyükçekmece" alias
  - "Merkezefendi Basket" canonical ← "Yukatel Merkezefendi Basket" alias

Two canonicals retain non-Wikipedia-strict forms for disambiguation:
  - "Türk Telekom Ankara" — adds Ankara location-disambiguator from
    legacy `Turk Telekom`
  - "Trabzonspor (Basketbol)" — Wikipedia canonical with parenthetical

## Cross-sport collision empirical-inclusion (F2 NEW)

5 BSL canonicals share names with Süper Lig football clubs. Day-22
sport_id partition validates matcher-layer safety (5th empirical
validation). Bare-name aliases INCLUDED per empirical discipline:

  - Galatasaray (BSL) + Galatasaray SK (Süper Lig)
  - Fenerbahçe (BSL) + Fenerbahçe SK (Süper Lig)
  - Beşiktaş (BSL) + Beşiktaş JK (Süper Lig)
  - Trabzonspor (Basketbol) (BSL) + Trabzonspor (Süper Lig)
  - Bursaspor Basketbol (BSL) + Bursaspor (Süper Lig)

Production sp.teams inspection (Day-31 afternoon) confirms sport_id=1
(Soccer) rows already exist for `Besiktas`, `Bursaspor`, `Fenerbahce`,
`Galatasaray`, `Trabzonspor`. Basketball-side canonicals coexist
safely under sport_id=3.

## Discovery query coverage (F7 / Pattern A.2 per amendment #21)

Day-31 afternoon production discovery (post-Israeli-BSL-apply, 7-day
window, Basketball routing, Turkish provider patterns). 21 distinct
provider-form pairs at occurrences ≥7. In-scope BSL provider forms
(map to manifest teams):

  Galatasaray, Galatasaray SK, Besiktas, Besiktas JK, Besiktas *,
  Fenerbahce, Fenerbahce Istanbul, Fenerbahce *, Esenler Erokspor,
  Bursaspor, Merkezefendi, Merkezefendi Belediyesi Denizli Basket,
  Turk Telekom (routes to dormant phantom by design),
  Trabzonspor, Manisa, Mersin SK, Bahcesehir Kol.,
  Bahcesehir Kol. *

EuroLeague crossover confirmed: Fenerbahce Istanbul vs BC Olympiakos
Piraeus (14/7d) + Olympiacos vs Fenerbahce (7/7d). Cross-workstream
signal preserved for Greek HEBA #6 (Olympiakos provider form is
"BC Olympiakos Piraeus").

Out-of-scope: TBL (Türkiye Basketbol Ligi second-division) FL leakage
expected per LBA Serie A2/B + Israeli BSL Liga Leumit pattern.
Estimated ~80-150/7d; not in v1 manifest. Filed as §6.1 follow-up.

## Source value (Q3)

bootstrap_league_coverage (cohort-wide convention; same as KBL, LMB,
Liga ACB, Italian LBA, Israeli BSL).

## Re-curation runbook

Turkish BSL roster churns via promotion/relegation (TBL ↔ BSL).
Sponsor names change yearly. When updates needed:

  1. Visit Wikipedia "Basketbol Süper Ligi" current season page +
     tbf.org.tr
  2. For each team: confirm Wikipedia canonical unchanged; add new
     sponsor alias; retain old sponsor aliases (historical records)
  3. For promoted teams: add canonical row + aliases; verify cross-
     sport collision (Süper Lig football overlap)
  4. For relegated teams: leave canonical in place
  5. Run --dry-run; apply; F7 via JOIN to sp.fixtures + sp.teams
     with country_code='TUR'
"""
from __future__ import annotations


TURKISH_BSL_ALIAS_SOURCE = "bootstrap_league_coverage"


# Format: (canonical_name, country_code, aliases_tuple, notes)
#
# All 16 Turkish BSL Premier League teams, 2025-26 season.
# Aliases include: Wikipedia canonical, discovery-query provider forms,
# sponsor variants, diacritic + ASCII-stripped pairs (documentation
# clarity; normalizer collapses them), asterisk-suffix variants,
# bare-name aliases for 5 football-overlap teams (empirical-coverage
# discipline per F2 NEW).
TURKISH_BSL_TEAMS_SEED: list[tuple[str, str, tuple[str, ...], str]] = [
    ("Anadolu Efes", "TUR",
     ("Anadolu Efes", "Anadolu Efes SK", "Efes Pilsen"),
     "BSL; 2× EuroLeague champion (2021, 2022); 'Anadolu Efes' "
     "brewery parent IS identity (no sponsor stripping needed). "
     "BACKFILL prediction: Phase 2A.5 legacy stub "
     "ca2f4866-c4ac-4a26-976f-d54401ce8c1d exists (normalized "
     "match 'anadolu efes'). No football overlap"),

    ("Bahçeşehir Koleji", "TUR",
     ("Bahçeşehir Koleji", "Bahcesehir Koleji", "Bahçeşehir",
      "Bahcesehir", "Bahcesehir Kol.", "Bahcesehir Kol. *",
      "Bahçeşehir Basket", "Bahcesehir Basket"),
     "BSL; 'Koleji' (the College) = educational parent (Bahçeşehir "
     "University network), part of identity not sponsor; abbreviated "
     "'Bahcesehir Kol.' + asterisk-suffix from Day-31 discovery "
     "(general FL provider quirk); no major football collision "
     "(Bahçeşehir is an Istanbul district)"),

    ("Beşiktaş", "TUR",
     ("Beşiktaş", "Besiktas", "Beşiktaş JK", "Besiktas JK",
      "Beşiktaş Gain", "Besiktas Gain", "Besiktas *"),
     "BSL; CROSS-SPORT WITH Beşiktaş JK (Süper Lig football). "
     "Bare 'Beşiktaş' + 'Besiktas' INCLUDED per F2 NEW empirical-"
     "coverage discipline (Day-31 discovery shows bare forms "
     "dominate). Day-22 sport_id partition validates safety "
     "(5th empirical validation). 'Gain' current sponsor; 'JK' = "
     "Jimnastik Kulübü (Gymnastics Club). Asterisk-suffix from "
     "discovery"),

    ("Bursaspor Basketbol", "TUR",
     ("Bursaspor Basketbol", "Bursaspor", "Bursaspor Basket",
      "Bursaspor Durmazlar"),
     "BSL; CROSS-SPORT WITH Bursaspor (Süper Lig football). Bare "
     "'Bursaspor' INCLUDED per F2 NEW empirical-coverage discipline. "
     "BACKFILL prediction: Phase 2A.5 legacy stub "
     "85c6d6bf-8ffb-4309-b0aa-9ba3d146ad4c exists (normalized match "
     "'bursaspor basketbol'). 'Durmazlar' historical sponsor"),

    ("Büyükçekmece Basketbol", "TUR",
     ("Büyükçekmece Basketbol", "Buyukcekmece Basketbol",
      "ONVO Büyükçekmece", "ONVO Buyukcekmece",
      "Büyükçekmece Basket", "Buyukcekmece Basket"),
     "BSL; 'ONVO' current sponsor; 'Büyükçekmece' Istanbul district; "
     "no major football collision; diacritic + ASCII-stripped pairs "
     "(ü → u; ç → c)"),

    ("Esenler Erokspor", "TUR",
     ("Esenler Erokspor", "Esenler Erok", "Erokspor"),
     "BSL; new club entrant; 'Erokspor' compound (Erok + spor); "
     "Esenler Istanbul district; no longstanding sponsor pattern; "
     "no football collision. Discovery sends 'Esenler Erokspor' "
     "full form"),

    ("Fenerbahçe", "TUR",
     ("Fenerbahçe", "Fenerbahce", "Fenerbahce *", "Fenerbahce Istanbul",
      "Fenerbahce SK", "Fenerbahçe Beko", "Fenerbahce Beko",
      "Fenerbahçe Basketbol", "Fenerbahce Basketbol"),
     "BSL; CROSS-SPORT WITH Fenerbahçe SK (Süper Lig football). "
     "Bare 'Fenerbahçe' + 'Fenerbahce' INCLUDED per F2 NEW empirical-"
     "coverage discipline (Day-31 discovery shows 'Fenerbahce' + "
     "'Fenerbahce Istanbul' + 'Fenerbahce *' all at material rates). "
     "EUROLEAGUE CROSSOVER: Fenerbahce vs Olympiakos (BC Olympiakos "
     "Piraeus) 21/7d in Day-31 discovery — cross-workstream signal "
     "for Greek HEBA #6 design. 2023 EuroLeague champion. 'Beko' "
     "current sponsor; asterisk-suffix from discovery"),

    ("Galatasaray", "TUR",
     ("Galatasaray", "Galatasaray SK", "Galatasaray MCT Technic",
      "Galatasaray Basket", "Galatasaray Cafe Crown",
      "Galatasaray Basketbol"),
     "BSL; CROSS-SPORT WITH Galatasaray SK (Süper Lig football). "
     "Canonical IS the bare form; bare alias 'Galatasaray' explicitly "
     "INCLUDED per F2 NEW empirical-coverage discipline (Day-31 "
     "discovery shows 30+/7d). 'MCT Technic' current sponsor; "
     "'Cafe Crown' historical sponsor"),

    ("Karşıyaka Basket", "TUR",
     ("Karşıyaka Basket", "Karsiyaka Basket", "Pınar Karşıyaka",
      "Pinar Karsiyaka", "Karşıyaka Basketbol", "Karsiyaka Basketbol"),
     "BSL; DORMANT PHANTOM ACCEPTANCE: legacy Phase 2A.5 stub "
     "'Karşıyaka' (id ff68785a-0698-4934-b594-c68ccfdb1711) stays "
     "dormant — no BACKFILL country_code, no new bootstrap aliases "
     "added. Manifest canonical 'Karşıyaka Basket' per Wikipedia "
     "2025-26 roster (F1 amendment #12 authoritative-source primacy). "
     "Bare 'Karşıyaka'/'Karsiyaka' aliases INTENTIONALLY EXCLUDED — "
     "those route to dormant phantom via canonical_name lookup. "
     "'Pınar' decades-long historical sponsor. İzmir-based"),

    ("Manisa Basket", "TUR",
     ("Manisa Basket", "Glint Manisa Basket", "Manisa", "Glint Manisa"),
     "BSL; 'Glint' current sponsor prefix; bare 'Manisa' INCLUDED "
     "(Day-31 discovery sends bare form; Manisaspor football in "
     "lower tier — sport_id partition handles per Day-22 finding)"),

    ("Merkezefendi Basket", "TUR",
     ("Merkezefendi Basket", "Merkezefendi", "Yukatel Merkezefendi Basket",
      "Merkezefendi Belediyesi Denizli Basket", "Denizli Basket",
      "Yukatel Merkezefendi", "Merkezefendi Denizli"),
     "BSL; 'Yukatel' current sponsor; 'Merkezefendi' Denizli district + "
     "'Belediyesi' (Municipality); Day-31 discovery sends both bare "
     "'Merkezefendi' and full 'Merkezefendi Belediyesi Denizli Basket' "
     "long form"),

    ("Mersin MSK", "TUR",
     ("Mersin MSK", "Mersin SK", "Mersin Spor Kulübü", "Mersin Spor",
      "Mersin Spor Kulubu"),
     "BSL; 'MSK' = Mersin Spor Kulübü (Sports Club); Day-31 discovery "
     "sends 'Mersin SK' (note SK not MSK abbreviation). Mersin "
     "İdmanyurdu football is separate identity"),

    ("Petkim Spor", "TUR",
     ("Petkim Spor", "Petkim", "Petkimspor", "Aliağa Petkim",
      "Aliaga Petkim"),
     "BSL; 'Petkim' petrochemical parent company (state-affiliated); "
     "Aliağa is the İzmir district; diacritic ğ → g for ASCII"),

    ("Tofaş", "TUR",
     ("Tofaş", "Tofas", "Tofaş SK", "Tofas SK", "Tofaş Bursa",
      "Tofas Bursa"),
     "BSL; 'Tofaş' = Fiat-Tofaş Turkish-Italian automotive parent; "
     "BACKFILL prediction: Phase 2A.5 legacy stub "
     "7f3d7ec1-c48f-48cf-8b8f-089faec3fc53 exists (NFD normalize "
     "match: manifest `Tofaş`→`tofas`, legacy `Tofas`→`tofas`). "
     "Bursa-based"),

    ("Trabzonspor (Basketbol)", "TUR",
     ("Trabzonspor (Basketbol)", "Trabzonspor Basketbol", "Trabzonspor",
      "Trabzonspor Basket"),
     "BSL; CROSS-SPORT WITH Trabzonspor (Süper Lig football). Bare "
     "'Trabzonspor' INCLUDED per F2 NEW empirical-coverage discipline "
     "(Day-31 discovery 14+/7d). Wikipedia canonical uses "
     "parenthetical disambiguator '(Basketbol)' for the basketball "
     "section of the multi-sport club"),

    ("Türk Telekom Ankara", "TUR",
     ("Türk Telekom Ankara", "Turk Telekom Ankara",
      "Türk Telekom Basket", "Turk Telekom Basket",
      "Türk Telekom Basketbol", "Turk Telekom Basketbol"),
     "BSL; DORMANT PHANTOM ACCEPTANCE: legacy Phase 2A.5 stub "
     "'Turk Telekom' (id d436ec55-a303-49a5-84af-0e3f0e90156b) "
     "stays dormant. Manifest canonical 'Türk Telekom Ankara' adds "
     "Ankara location-disambiguator per F1 amendment #12. Bare "
     "'Türk Telekom'/'Turk Telekom' aliases INTENTIONALLY EXCLUDED — "
     "Day-31 discovery 'Turk Telekom' provider strings route to "
     "dormant phantom via canonical_name lookup (still strict-tier, "
     "just no country_code). Türk Telekom state-affiliated telecom "
     "parent"),
]
