"""EuroLeague + ABA League combined seed manifest — Phase 2D.5-A
workstreams #8 + #9.

COMBINED WORKSTREAM rationale: both are predominantly BACKFILL
workstreams with low INSERT counts and low-to-moderate volume.
Combined scope reduces PR overhead.

  - EuroLeague #8: gap-fill after prior domestic workstreams cover
    most EuroLeague teams (~15 records/7d residual)
  - ABA League #9: 18-team Balkan multi-country competition
    (~90-100 records/7d Day-35 discovery)

24 manifest teams total (8 EuroLeague gap-fill + 16 ABA).
20 BACKFILLs + 4 INSERTs.

Two teams (Partizan Mozzart Bet, Dubai Basketball) appear in BOTH
leagues — single team_id covers both via cross-league fixture
strict resolution.

## Source

Source: 2025-26 EuroLeague season + 2024-25 ABA League season
Wikipedia rosters + Day-35 production discovery query
(operator-verified Day-35).

## Country codes (multi-country)

Per-team `country_code` (12 distinct codes):

  - SRB: Partizan Mozzart Bet, Crvena Zvezda Meridianbet,
    Mega Basket, FMP Beograd, Borac Mozzart, Spartak Subotica
  - MNE: Buducnost, SC Derby
  - BIH: KK Bosna, Igokea
  - SVN: Cedevita Olimpija, KK Krka Novo Mesto, Ilirija
  - CRO: KK Zadar, KK Split
  - AUT: BC Vienna
  - ROU: U-BT Cluj-Napoca
  - UAE: Dubai Basketball
  - MCO: Monaco
  - DEU: Bayern München
  - FRA: Lyon-Villeurbanne, Paris Basketball
  - LTU: Zalgiris Kaunas, Rytas

## Composition

EuroLeague gap-fill (8 teams):
  BACKFILL (7): Monaco (092518ec), Bayern München (bdb22a1c),
    Lyon-Villeurbanne (5481c8e7), Paris Basketball (e4e0e605),
    Partizan Mozzart Bet (575ec0fc — also ABA),
    Zalgiris Kaunas (a845d73b), Rytas (834075ed)
  INSERT (1): Dubai Basketball (also ABA)

ABA League (16 teams):
  BACKFILL (13): Crvena Zvezda Meridianbet (a3d095e9),
    Buducnost (063a1204), KK Bosna (99368c5b),
    Cedevita Olimpija (e7cce709), Mega Basket (5ef0b126),
    Igokea (ea0cd454), KK Zadar (bb0da184),
    FMP Beograd (1337e0d0), Borac Mozzart (949c6254),
    BC Vienna (3c7275fc), KK Split (d7a6e58e),
    KK Krka Novo Mesto (0674ed89), Spartak Subotica (3c6aa492)
  INSERT (3): SC Derby, Ilirija, U-BT Cluj-Napoca

## Canonical_name policy (F1)

BACKFILLs preserve legacy Phase 2A.5 canonical_name (production-
anchor discipline). INSERTs use Wikipedia 2025-26 / 2024-25 canonical
forms.

## Alias distinctiveness (F2) — empirical-coverage INCLUSION

Per F2 NEW empirical-coverage discipline (Turkish BSL #5, HEBA #6,
VTB #7): bare club names INCLUDED where FL sends bare forms AND
sport_id partition validates safety:

  - "Monaco" — Day-35 discovery dominant; distinctive
  - "Bayern" — Day-35; CROSS-SPORT WITH Bayern Munich FC + Bayern
    Munich Basketball history; dormant phantom risk on bare Bayern
    stub (b4318e7f) per amendment #22 audit
  - "Bosna" — distinctive enough
  - "Partizan" — bare EXCLUDED (too generic — Partizan exists in
    multiple Serbian sports)
  - "ASVEL", "Olimpija", "Krka", "Spartak", "FMP" — distinctive

Bare city/generic forms handled per-team — see notes.

## Diacritics (F3)

Same shape as prior workstreams:

  - "Budućnost" ↔ "Buducnost" (ć → c via NFD; same normalized key,
    documentation pair)
  - "Žalgiris" ↔ "Zalgiris" (ž → z; same key, documentation pair)
  - "Bayern München" ↔ "Bayern Munich" (ü → u; same key)

Cyrillic-script aliases OUT OF SCOPE per KBL Issue #165 precedent.

## Discovery query coverage (F7 / Pattern A.2 per amendment #21)

Day-35 discovery confirmed in-scope provider forms:

  - KK Partizan Belgrade (24/7d), KK Crvena zvezda Belgrade (14/7d)
  - KK Buducnost Voli (14+14/7d), KK Bosna Royal Sarajevo (14/7d)
  - Monaco / Monaco vs Olympiacos * (3/7d)
  - BC Rytas Vilnius (6/7d, EuroCup crossover with AEK Athens)
  - FC Universitatea Cluj (14+14/7d, ABA)

EuroLeague gap-fill volume is low (~15/7d residual) because most
EuroLeague teams already covered by prior domestic workstreams
(#2 ACB, #4 Israeli BSL, #5 Turkish BSL, #6 HEBA, #7 VTB, #3 LBA).

## Dormant phantom risk (Amendment #22 pre-apply audit MANDATORY)

EuroLeague:
  - Monaco Basket (51a337b9) — Monaco BACKFILL aliases
  - Bayern (b4318e7f) — bare Bayern alias on Bayern München
  - LDLC ASVEL Lyon-Villeurbanne Espoirs U21 (4541053d) — youth stub

ABA:
  - KK Crvena zvezda (1ebacd0f) — Crvena Zvezda Meridianbet aliases
  - KK Borac (26b9f2eb) — Borac Mozzart 'kk borac' alias
  - KK Student Igokea (707c2064) — Igokea aliases
  - Zadar (8d626c4b) — KK Zadar bare 'zadar' alias
  - Split (fd5eb539) — KK Split bare 'split' alias
  - KK SC Derby U19 (a78ebe1f) — SC Derby INSERT (youth stub
    SEPARATE entity)

Separate-entity stubs NOT to be aliased:
  - Cedevita Junior (96d58d34)
  - Paris Basketball Espoirs (32070716) + Paris U21 (323dd4e1)
  - KK Partizan Mozzart Bet U19 (7c9eaf1e)
  - FMP Beograd U19 (42e58805)
  - Borac Banja Luka (860644b7), KK Borac Zemun (3cef82a8),
    Borac Zemun (35c9b4ba)
  - Vienna Basket (2c920285), Vienna United (59a9707a),
    Vienna 3x3 (d074ef10)
  - Ilirija U19 (d4363c08), Perspektiva Ilirija (5bc575f8),
    ZKD Ilirija (64e7fa0a)
  - CSU Cluj-Napoca (d660fd87), Cluj-Napoca (506aa215)
  - Dubai (6b8852e4), BC Dubai (ccfa9b0e)
  - KK Crvena Zvezda U19 (71771608), ŽKK Crvena zvezda (d6a5ae20)

Per Day-33 HEBA + Day-34 VTB precedent: post-apply collision audit
MANDATORY regardless of clean amendment #22 pre-apply audit.

## Cross-league dual presence

Partizan Mozzart Bet (575ec0fc) — EuroLeague + ABA single team_id.
Dubai Basketball — EuroLeague + ABA (2024-25), INSERT fresh.

## Source value (Q3)

bootstrap_league_coverage (cohort-wide convention).

## Re-curation runbook

EuroLeague + ABA rosters churn annually. When updates needed:
  1. Visit Wikipedia "EuroLeague" + "ABA League" current season pages
  2. For each team: confirm canonical; add new sponsor alias
  3. For new teams: amendment #22 audit before adding
  4. Run --dry-run; apply; F7 with multi-country code filter
"""
from __future__ import annotations


EUROLEAGUE_ABA_ALIAS_SOURCE = "bootstrap_league_coverage"


# Format: (canonical_name, country_code, aliases_tuple, notes)
EUROLEAGUE_ABA_TEAMS_SEED: list[tuple[str, str, tuple[str, ...], str]] = [
    # ═════════════════════════════════════════════════════════════
    # EuroLeague gap-fill (#8)
    # ═════════════════════════════════════════════════════════════

    ("Monaco", "MCO",
     ("Monaco", "AS Monaco", "AS Monaco Basket"),
     "EuroLeague #8 BACKFILL — Phase 2A.5 stub "
     "092518ec-4e4e-4523-9235-8a938de1d2e7. AS Monaco Basketball. "
     "Day-35 discovery: 'Monaco vs Olympiacos *' (3/7d). DORMANT "
     "PHANTOM RISK: Monaco Basket (51a337b9) — separate stub, "
     "amendment #22 audit"),

    ("Bayern München", "DEU",
     ("Bayern München", "Bayern Munich", "FC Bayern Munich", "Bayern"),
     "EuroLeague #8 BACKFILL — Phase 2A.5 stub "
     "bdb22a1c-f2f6-4804-9a9d-cb8871c00170. CROSS-SPORT WITH "
     "Bayern Munich FC (football top-5 German recognition) + "
     "Bayern Munich Basketball historical naming. Bare 'Bayern' "
     "INCLUDED per F2 NEW empirical-coverage discipline. DORMANT "
     "PHANTOM RISK: Bayern (b4318e7f) — separate bare-form stub; "
     "post-apply audit may require DELETE of bare 'Bayern' alias "
     "(§6.6 follow-up)"),

    ("Lyon-Villeurbanne", "FRA",
     ("Lyon-Villeurbanne", "ASVEL", "LDLC ASVEL", "Villeurbanne",
      "LDLC ASVEL Villeurbanne"),
     "EuroLeague #8 BACKFILL — Phase 2A.5 stub "
     "5481c8e7-cff6-4b9f-b2fc-d22e953296f7. Full name: LDLC ASVEL "
     "Lyon-Villeurbanne. 'LDLC ASVEL Lyon-Villeurbanne Espoirs U21' "
     "(4541053d) is SEPARATE youth stub — not aliased here"),

    ("Paris Basketball", "FRA",
     ("Paris Basketball", "Paris Basket", "Paris BB"),
     "EuroLeague #8 BACKFILL — Phase 2A.5 stub "
     "e4e0e605-dfe7-42c0-bbfa-7ae895feaede. Paris Basketball "
     "Espoirs (32070716) and Paris U21 (323dd4e1) are SEPARATE "
     "youth stubs"),

    ("Partizan Mozzart Bet", "SRB",
     ("Partizan Mozzart Bet", "KK Partizan Belgrade",
      "Partizan Belgrade", "Partizan"),
     "EuroLeague #8 + ABA #9 BACKFILL — single team_id covers both "
     "leagues (575ec0fc-e1e9-4420-96aa-31376443a664). Phase 2A.5 "
     "stub. FL sends 'KK Partizan Belgrade' (24/7d Day-35). KK "
     "Partizan Mozzart Bet U19 (7c9eaf1e) is SEPARATE youth stub. "
     "'Mozzart Bet' current sponsor"),

    ("Zalgiris Kaunas", "LTU",
     ("Zalgiris Kaunas", "Zalgiris", "BC Zalgiris", "BC Zalgiris Kaunas",
      "Žalgiris"),
     "EuroLeague #8 BACKFILL — Phase 2A.5 stub "
     "a845d73b-d8ec-4f96-8695-1c9f6dc9de13. Diacritic pair "
     "'Žalgiris' ↔ 'Zalgiris' (NFD collapses ž → z; same key). "
     "1999 EuroLeague champion"),

    ("Rytas", "LTU",
     ("Rytas", "BC Rytas", "BC Rytas Vilnius", "Rytas Vilnius"),
     "EuroLeague #8 BACKFILL — Phase 2A.5 stub "
     "834075ed-c190-4c46-be1d-7fcd263ee9b3. FL sends 'BC Rytas "
     "Vilnius' in EuroCup crossovers vs AEK Athens (6/7d Day-35). "
     "EuroCup-not-EuroLeague-proper; included as EuroLeague-"
     "adjacent gap-fill"),

    ("Dubai Basketball", "UAE",
     ("Dubai Basketball",),
     "EuroLeague #8 + ABA #9 INSERT — new UAE franchise, no Phase "
     "2A.5 senior legacy stub. SEPARATE STUBS not aliased here: "
     "Dubai (6b8852e4) + BC Dubai (ccfa9b0e). Appears in both "
     "leagues 2024-25 (ABA) + 2025-26 (EuroLeague entrant)"),

    # ═════════════════════════════════════════════════════════════
    # ABA League (#9)
    # ═════════════════════════════════════════════════════════════

    ("Crvena Zvezda Meridianbet", "SRB",
     ("Crvena Zvezda Meridianbet", "KK Crvena zvezda Belgrade",
      "Crvena zvezda", "Red Star Belgrade"),
     "ABA #9 BACKFILL — Phase 2A.5 stub "
     "a3d095e9-32c5-4491-acf2-30866bb1350a. Also in EuroLeague. FL "
     "sends 'KK Crvena zvezda Belgrade' (14/7d Day-35). DORMANT "
     "PHANTOM RISK: KK Crvena zvezda (1ebacd0f). Separate stubs "
     "(NOT aliased): KK Crvena Zvezda U19 (71771608), ŽKK Crvena "
     "zvezda (d6a5ae20)"),

    ("Buducnost", "MNE",
     ("Buducnost", "KK Buducnost Voli", "Buducnost VOLI",
      "Budućnost VOLI", "Budućnost"),
     "ABA #9 BACKFILL — Phase 2A.5 stub "
     "063a1204-fdd6-4930-a23f-d4df5975902e. Podgorica-based. FL "
     "sends 'KK Buducnost Voli' (14+14/7d Day-35). Diacritic pair "
     "'Budućnost' ↔ 'Buducnost' (NFD collapses ć → c)"),

    ("KK Bosna", "BIH",
     ("KK Bosna", "KK Bosna Royal Sarajevo", "Bosna Royal Sarajevo",
      "Bosna BH Telecom", "Bosna Sarajevo"),
     "ABA #9 BACKFILL — Phase 2A.5 stub "
     "99368c5b-9da7-4c56-827c-a981788875a9. Sarajevo-based. FL "
     "sends 'KK Bosna Royal Sarajevo' (14/7d Day-35). Wikipedia "
     "current name 'Bosna BH Telecom' — sponsor variant"),

    ("Cedevita Olimpija", "SVN",
     ("Cedevita Olimpija", "KK Cedevita Olimpija", "Olimpija"),
     "ABA #9 BACKFILL — Phase 2A.5 stub "
     "e7cce709-8d55-4955-a9be-49d8afdf0d0f. Ljubljana-based. "
     "Cedevita Junior (96d58d34) is SEPARATE stub"),

    ("Mega Basket", "SRB",
     ("Mega Basket", "Mega Superbet", "KK Mega", "Mega MIS",
      "Mega Bemax"),
     "ABA #9 BACKFILL — Phase 2A.5 stub "
     "5ef0b126-635e-464a-9e1b-ea44c2c40e1e. Belgrade-based. "
     "Wikipedia current name 'Mega Superbet'. Frequent sponsor "
     "changes — 'MIS', 'Bemax' historical sponsors"),

    ("Igokea", "BIH",
     ("Igokea", "Igokea m:tel", "KK Igokea"),
     "ABA #9 BACKFILL — Phase 2A.5 stub "
     "ea0cd454-6efc-4cc2-a3a8-f357ada59e55. Aleksandrovac-based "
     "(Bosnian Serb Republic). DORMANT PHANTOM RISK: KK Student "
     "Igokea (707c2064)"),

    ("KK Zadar", "CRO",
     ("KK Zadar", "Zadar", "KK Zadar Zadar"),
     "ABA #9 BACKFILL — Phase 2A.5 stub "
     "bb0da184-2077-48ff-b80e-9e377567961a. Bare 'Zadar' INCLUDED. "
     "DORMANT PHANTOM RISK: Zadar (8d626c4b) — separate bare stub"),

    ("FMP Beograd", "SRB",
     ("FMP Beograd", "KK FMP"),
     "ABA #9 BACKFILL — Phase 2A.5 stub "
     "1337e0d0-31f5-4021-9574-e3c8683aed0e. FMP Beograd U19 "
     "(42e58805) is SEPARATE youth stub"),

    ("Borac Mozzart", "SRB",
     ("Borac Mozzart", "Borac Cacak", "KK Borac Cacak"),
     "ABA #9 BACKFILL — Phase 2A.5 stub "
     "949c6254-c8b2-4500-a0ee-edd03c47a206. Čačak-based. DORMANT "
     "PHANTOM RISK: KK Borac (26b9f2eb) collides with 'KK Borac' "
     "alias. Separate stubs NOT aliased: Borac Banja Luka "
     "(860644b7), KK Borac Zemun (3cef82a8), Borac Zemun "
     "(35c9b4ba)"),

    ("BC Vienna", "AUT",
     ("BC Vienna", "Vienna", "Basketball Club Vienna"),
     "ABA #9 BACKFILL — Phase 2A.5 stub "
     "3c7275fc-407b-45c7-bd74-a95874f82ec3. Separate stubs NOT "
     "aliased: Vienna Basket (2c920285), Vienna United (59a9707a), "
     "Vienna 3x3 (d074ef10)"),

    ("KK Split", "CRO",
     ("KK Split", "Split", "HKK Split"),
     "ABA #9 BACKFILL — Phase 2A.5 stub "
     "d7a6e58e-1dba-48ac-bcf0-9d16b7baca88. Bare 'Split' INCLUDED. "
     "DORMANT PHANTOM RISK: Split (fd5eb539) — separate bare stub"),

    ("KK Krka Novo Mesto", "SVN",
     ("KK Krka Novo Mesto", "Krka", "KK Krka"),
     "ABA #9 BACKFILL — Phase 2A.5 stub "
     "0674ed89-7b97-4cc1-808b-54fe385820c3"),

    ("Spartak Subotica", "SRB",
     ("Spartak Subotica", "Spartak", "KK Spartak"),
     "ABA #9 BACKFILL — Phase 2A.5 stub "
     "3c6aa492-5fa0-4ba6-a12a-9476601e96b3"),

    ("SC Derby", "MNE",
     ("SC Derby", "KK SC Derby", "SC Derby Podgorica"),
     "ABA #9 INSERT — no senior Phase 2A.5 legacy stub. KK SC Derby "
     "U19 (a78ebe1f) is youth-only SEPARATE stub. Podgorica-based "
     "(Montenegro, country_code='MNE')"),

    ("Ilirija", "SVN",
     ("Ilirija", "KK Ilirija", "Ilirija Ljubljana"),
     "ABA #9 INSERT — no clean senior Phase 2A.5 legacy stub. "
     "Separate stubs (NOT aliased): Ilirija U19 (d4363c08), "
     "Perspektiva Ilirija (5bc575f8), ZKD Ilirija (64e7fa0a)"),

    ("U-BT Cluj-Napoca", "ROU",
     ("U-BT Cluj-Napoca", "U-BT Cluj", "FC Universitatea Cluj",
      "Cluj-Napoca"),
     "ABA #9 INSERT — FL sends 'FC Universitatea Cluj' (14+14/7d "
     "Day-35). Wikipedia canonical 'U-BT Cluj-Napoca'. Separate "
     "stubs (NOT aliased): CSU Cluj-Napoca (d660fd87), Cluj-Napoca "
     "(506aa215)"),
]
