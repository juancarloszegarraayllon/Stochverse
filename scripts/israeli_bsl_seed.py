"""Israeli Basketball Premier League (Winner League) seed manifest — Phase 2D.5-A.

Data-driven league bootstrap: Israeli BSL teams identified via
asymmetric_anchor_failure resolver signal (Day-31 afternoon discovery
query, post-LBA-apply). ~300+ records/week resolving to review_queue
because sp.teams has no Basketball-sport canonical for Israeli league
team names.

14 teams for the 2025-26 season. Workstream #4 of Phase 2D.5-A per
Day-31 re-sequencing in `docs/bootstraps/phase-2d5a-sequencing-decision.md`
(Israeli BSL selected over EuroLeague-proper per amendment #15 —
domestic-league discovery volume ~300/7d dwarfs EuroLeague-proper
residual ~80/7d after subtracting domestic-league overlap).

## Source

Source: 2025-26 Israeli Basketball Premier League / Winner League
Wikipedia standings table (operator-verified paste, Day-31). All
14 teams listed at the time of bootstrap.

Operator-pasted authoritative roster:

    Maccabi Tel Aviv, Hapoel Tel Aviv, Hapoel Jerusalem,
    Bnei Herzliya, Hapoel Holon, Hapoel HaEmek,
    Maccabi Rishon LeZion, Hapoel Be'er Sheva/Dimona,
    Maccabi Ironi Ramat Gan, Ironi Kiryat Ata, Ironi Ness Ziona,
    Hapoel Galil Elyon, Elitzur Netanya, Maccabi Ironi Ra'anana

## Canonical_name policy (F1)

Use HERITAGE / Wikipedia-canonical form, NOT current sponsor form.
Mirrors LMB, Liga ACB, Italian LBA F1 precedent. Examples:

  - "Maccabi Tel Aviv" canonical ← "Maccabi Playtika Tel Aviv" alias
  - "Hapoel Be'er Sheva/Dimona" canonical (retain apostrophe + slash
    per Wikipedia form) ← "Hapoel Beer Sheva" alias (discovery form)

## Alias distinctiveness (F2) — HIGHEST cross-sport collision risk of Phase 2D.5-A

11 of 14 BSL teams have Israeli football counterparts. Bare-city
aliases EXCLUDED for these 11 cities. Operator-clarity discipline
(matcher-layer disambiguation already handled by sport_id partition
per Day-22 finding, but excluding bare-city aliases adds review-time
clarity).

EXCLUDED bare-city aliases (Israeli football overlap):
  - "Tel Aviv" — Maccabi Tel Aviv FC + Hapoel Tel Aviv FC; shared
    by both BSL Tel Aviv teams anyway, so prefix-disambiguation
    (Maccabi/Hapoel) mandatory
  - "Jerusalem" — Beitar Jerusalem FC + Hapoel Jerusalem FC
  - "Be'er Sheva" / "Beer Sheva" — Hapoel Be'er Sheva FC (Israeli
    Premier League multi-time champion)
  - "Holon" — Hapoel Holon FC
  - "Ra'anana" / "Raanana" — Hapoel Ra'anana FC
  - "Ness Ziona" — Hapoel Ness Ziona FC
  - "Ramat Gan" — Hapoel Ramat Gan FC
  - "Herzliya" — Hapoel Herzliya FC
  - "Rishon LeZion" / "Rishon" — Hapoel Rishon LeZion FC
  - "Netanya" — Maccabi Netanya FC (Israeli Premier League)

EXCLUDED within-league bare prefixes (collision-within-BSL risk):
  - "Maccabi" — 4 BSL teams (Tel Aviv, Rishon LeZion, Ironi Ramat
    Gan, Ironi Ra'anana). Always qualify.
  - "Hapoel" — 6 BSL teams (Tel Aviv, Jerusalem, Holon, HaEmek,
    Be'er Sheva/Dimona, Galil Elyon). Always qualify.
  - "Ironi" — 4 BSL teams (Kiryat Ata, Ness Ziona, Maccabi Ironi
    Ramat Gan, Maccabi Ironi Ra'anana). Always qualify.
  - "Bnei" — common Israeli sports-club prefix; future-collision risk
  - "Elitzur" — Elitzur Yavne in Liga Leumit could promote; future-
    collision risk

SAFE bare aliases (per operator paste; no football collision):
  - "HaEmek" / "Haemek" — regional name (Jezreel Valley)
  - "Galil Elyon" — regional name (Upper Galilee)
  - "Kiryat Ata" — no Israeli football top tier presence

## Apostrophe + special-character handling

The normalizer (`resolver/_normalize.py`) treats apostrophe + slash
as punctuation and converts to space, then collapses whitespace.

  "Hapoel Be'er Sheva" → "hapoel be er sheva"   (apostrophe → space)
  "Hapoel Beer Sheva"  → "hapoel beer sheva"     (no apostrophe)

These are DIFFERENT normalized keys. Both must be present as aliases
on Hapoel Be'er Sheva/Dimona to handle both production-string forms.

Same pattern for Ra'anana:

  "Maccabi Ironi Ra'anana" → "maccabi ironi ra anana"
  "Maccabi Ironi Raanana"  → "maccabi ironi raanana"

Different keys; both included.

The hyphenated "Tel-Aviv" form, by contrast, normalizes identically
to "Tel Aviv" (hyphen → space; whitespace collapses):

  "Hapoel Tel-Aviv" → "hapoel tel aviv"
  "Hapoel Tel Aviv" → "hapoel tel aviv"

Same key; including both is belt-and-suspenders for documentation.

## Discovery query coverage (F7 / Pattern A.2 per amendment #21)

Day-31 afternoon production discovery query (post-LBA-apply, 7-day
window, Basketball, Israeli/EuroLeague provider patterns) confirmed
the following in-scope provider forms map to manifest teams:

  Maccabi Tel Aviv, Maccabi Tel-Aviv, Hapoel Tel Aviv,
  Hapoel Tel-Aviv, Hapoel Jerusalem, Bnei Herzliya,
  Bnei Herzliya Basket, Hapoel HaEmek, Hapoel Haemek,
  Maccabi Rishon LeZion, Maccabi Rishon, Hapoel Beer Sheva,
  Ironi Kiryat Ata, Hapoel Galil Elyon, Galil Elyon,
  Elitzur Maccabi Netanya

Out-of-scope provider forms (Liga Leumit / Israeli National League /
second division — not in 2025-26 BSL roster):

  Maccabi Haifa, Maccabi Petah Tikva, Maccabi Kiryat Gat,
  Maccabi Maale Adumim, Migdal Haemek, Elitzur Yavne

These second-division leakage targets account for ~80-150/7d of
asymmetric_anchor_failure records. Out-of-scope for v1; investigate
FL sport-tier classifier as follow-up.

## Asterisk-suffix forms

Day-30 (Italian LBA) and Day-31 (EuroLeague discovery) confirmed the
asterisk-suffix pattern is a general FL provider-side artifact, not
LBA-specific. Defensive coverage included for top-volume BSL teams:

  "Maccabi Tel Aviv *", "Hapoel Tel Aviv *", "Hapoel Jerusalem *",
  "Hapoel Be'er Sheva *", "Hapoel Beer Sheva *"

## Hebrew script aliases

OUT OF SCOPE for v1 per KBL Issue #165 precedent (ASCII Latin-script
only). Hebrew-script provider strings (e.g., "מכבי תל אביב") would
require Hebrew-aware normalizer support. Filed as scope-doc follow-up.

## Source value (Q3)

bootstrap_league_coverage (cohort-wide convention; same as KBL, LMB,
Liga ACB, Italian LBA).

## Re-curation runbook

Israeli BSL roster churns via promotion/relegation (Liga Leumit ↔
BSL Premier). Sponsor names change. When updates needed:

  1. Visit Wikipedia "Israeli Basketball Premier League" current
     season page and basket.co.il
  2. For each team: confirm Wikipedia-canonical unchanged; add new
     sponsor alias; retain old sponsor aliases (historical FL records
     reference them)
  3. For promoted teams: add canonical row + aliases; verify
     cross-sport collision (Israeli football overlap)
  4. For relegated teams: leave canonical in place
  5. Run --dry-run; apply; F7 via JOIN to sp.fixtures + sp.teams
     with country_code='ISR' (per amendment #18 — sparse FL
     reason_detail JSON)
"""
from __future__ import annotations


ISRAELI_BSL_ALIAS_SOURCE = "bootstrap_league_coverage"


# Format: (canonical_name, country_code, aliases_tuple, notes)
#
# All 14 Israeli BSL Premier League teams, 2025-26 season.
# Aliases include: Wikipedia canonical, discovery-query provider forms,
# apostrophe + hyphen + ASCII variants, sponsored forms, asterisk-
# suffix variants for top-volume teams (defensive coverage).
ISRAELI_BSL_TEAMS_SEED: list[tuple[str, str, tuple[str, ...], str]] = [
    ("Bnei Herzliya", "ISR",
     ("Bnei Herzliya Basket", "Bnei Herzliya", "Bnei Herzliya BC"),
     "BSL; CROSS-SPORT COLLISION RISK: Hapoel Herzliya FC exists. "
     "Bare 'Herzliya' INTENTIONALLY EXCLUDED. Bare 'Bnei' INTENTIONALLY "
     "EXCLUDED (common Israeli sports-club prefix, future-collision "
     "risk). Discovery query (Day-31) sends both 'Bnei Herzliya' and "
     "'Bnei Herzliya Basket'"),

    ("Elitzur Netanya", "ISR",
     ("Elitzur Netanya", "Elitzur Maccabi Netanya",
      "Elitzur Netanya BC", "Elitzur BC Netanya"),
     "BSL; CROSS-SPORT COLLISION RISK: Maccabi Netanya FC (Israeli "
     "Premier League). Bare 'Netanya' INTENTIONALLY EXCLUDED. Bare "
     "'Elitzur' INTENTIONALLY EXCLUDED (Elitzur Yavne in Liga Leumit "
     "could promote; future-collision risk). Discovery query (Day-31) "
     "surfaced 'Elitzur Maccabi Netanya' 4-token sponsored variant — "
     "included as alias"),

    ("Hapoel Be'er Sheva/Dimona", "ISR",
     ("Hapoel Be'er Sheva/Dimona", "Hapoel Be'er Sheva Dimona",
      "Hapoel Beer Sheva/Dimona", "Hapoel Beer Sheva Dimona",
      "Hapoel Be'er Sheva", "Hapoel Beer Sheva",
      "Hapoel Be'er Sheva *", "Hapoel Beer Sheva *"),
     "BSL; CROSS-SPORT COLLISION RISK: Hapoel Be'er Sheva FC "
     "(Israeli Premier League multi-time champion). Bare 'Be'er Sheva' "
     "and 'Beer Sheva' INTENTIONALLY EXCLUDED. APOSTROPHE+SLASH "
     "handling: 'Be'er Sheva' normalizes to 'be er sheva' (apostrophe "
     "→ space); 'Beer Sheva' normalizes to 'beer sheva' — DIFFERENT "
     "normalized keys, both must be present. Discovery query (Day-31) "
     "sends 'Hapoel Beer Sheva' no-apostrophe variant. Asterisk-suffix "
     "defensive coverage for top-volume teams (Day-30/31 general FL "
     "pattern)"),

    ("Hapoel Galil Elyon", "ISR",
     ("Hapoel Galil Elyon", "Galil Elyon", "Hapoel Upper Galilee"),
     "BSL; 'Galil Elyon' = 'Upper Galilee' regional name; bare "
     "'Galil Elyon' SAFE per operator paste (no football collision). "
     "Discovery query (Day-31) sends both 'Hapoel Galil Elyon' and "
     "bare 'Galil Elyon'"),

    ("Hapoel HaEmek", "ISR",
     ("Hapoel HaEmek", "Hapoel Haemek", "HaEmek", "Haemek"),
     "BSL; 'HaEmek' = 'the Valley' (Jezreel Valley regional name); "
     "bare 'HaEmek' / 'Haemek' SAFE per operator paste (no football "
     "collision; distinct from Liga Leumit's 'Migdal Haemek' which "
     "normalizes to a different key 'migdal haemek'). Discovery query "
     "(Day-31) sends both capitalization variants ('HaEmek' + "
     "'Haemek') — both normalize to 'haemek' but included as belt-"
     "and-suspenders documentation"),

    ("Hapoel Holon", "ISR",
     ("Hapoel Holon", "Hapoel U-Net Holon", "Hapoel Yossi Avrahami Holon",
      "Hapoel Holon BC"),
     "BSL; CROSS-SPORT COLLISION RISK: Hapoel Holon FC. Bare 'Holon' "
     "INTENTIONALLY EXCLUDED. Historical Israeli BSL champion "
     "(multiple titles). 'U-Net' and 'Yossi Avrahami' recent sponsor "
     "variants"),

    ("Hapoel Jerusalem", "ISR",
     ("Hapoel Jerusalem", "Hapoel Jerusalem BC", "Hapoel Jerusalem *"),
     "BSL; CROSS-SPORT COLLISION RISK: Beitar Jerusalem FC + Hapoel "
     "Jerusalem FC. Bare 'Jerusalem' INTENTIONALLY EXCLUDED. Top-3 "
     "BSL team (3rd place 2024-25); asterisk-suffix defensive coverage"),

    ("Hapoel Tel Aviv", "ISR",
     ("Hapoel Tel Aviv", "Hapoel Tel-Aviv", "Hapoel Tel Aviv BC",
      "Hapoel Tel Aviv *"),
     "BSL; CROSS-SPORT COLLISION RISK: Hapoel Tel Aviv FC. Bare "
     "'Tel Aviv' INTENTIONALLY EXCLUDED (also shared with Maccabi "
     "Tel Aviv BSL — within-league disambiguation also requires "
     "'Hapoel'/'Maccabi' prefix). Hyphenated 'Tel-Aviv' from Day-31 "
     "discovery — normalizes identically to 'Tel Aviv' but included "
     "as belt-and-suspenders. Asterisk defensive coverage (top-volume "
     "BSL team)"),

    ("Ironi Kiryat Ata", "ISR",
     ("Ironi Kiryat Ata", "Kiryat Ata", "Ironi Kiryat Ata BC"),
     "BSL; bare 'Kiryat Ata' SAFE per operator paste (no Israeli "
     "football top tier presence at Kiryat Ata). Bare 'Ironi' "
     "INTENTIONALLY EXCLUDED (4 BSL 'Ironi' teams — collision). "
     "Discovery query (Day-31) sends 'Ironi Kiryat Ata'"),

    ("Ironi Ness Ziona", "ISR",
     ("Ironi Ness Ziona", "Ironi Ness Ziona BC"),
     "BSL; CROSS-SPORT COLLISION RISK: Hapoel Ness Ziona FC. Bare "
     "'Ness Ziona' INTENTIONALLY EXCLUDED. Bare 'Ironi' INTENTIONALLY "
     "EXCLUDED (4 BSL 'Ironi' teams)"),

    ("Maccabi Ironi Ra'anana", "ISR",
     ("Maccabi Ironi Ra'anana", "Maccabi Ironi Raanana",
      "Maccabi Ra'anana", "Maccabi Raanana"),
     "BSL; CROSS-SPORT COLLISION RISK: Hapoel Ra'anana FC. Bare "
     "'Ra'anana' and 'Raanana' INTENTIONALLY EXCLUDED. APOSTROPHE "
     "handling: 'Ra'anana' normalizes to 'ra anana'; 'Raanana' to "
     "'raanana' — DIFFERENT normalized keys, both forms must be "
     "present. 'Maccabi Ironi' 2-prefix disambiguator. Bare 'Maccabi' "
     "and 'Ironi' INTENTIONALLY EXCLUDED (within-BSL collisions)"),

    ("Maccabi Ironi Ramat Gan", "ISR",
     ("Maccabi Ironi Ramat Gan", "Maccabi Ramat Gan"),
     "BSL; CROSS-SPORT COLLISION RISK: Hapoel Ramat Gan FC. Bare "
     "'Ramat Gan' INTENTIONALLY EXCLUDED. 'Maccabi Ironi' 2-prefix "
     "disambiguator. Bare 'Maccabi' and 'Ironi' INTENTIONALLY "
     "EXCLUDED (within-BSL collisions)"),

    ("Maccabi Rishon LeZion", "ISR",
     ("Maccabi Rishon LeZion", "Maccabi Rishon",
      "Maccabi Rishon Le Zion", "Maccabi Rishon LeTzion",
      "Maccabi Rishon LeZion BC"),
     "BSL; CROSS-SPORT COLLISION RISK: Hapoel Rishon LeZion FC. Bare "
     "'Rishon LeZion' and 'Rishon' INTENTIONALLY EXCLUDED. 'LeZion' / "
     "'Le Zion' / 'LeTzion' transliteration variants. Discovery query "
     "(Day-31) sends both 'Maccabi Rishon LeZion' long form and "
     "'Maccabi Rishon' short form"),

    ("Maccabi Tel Aviv", "ISR",
     ("Maccabi Tel Aviv", "Maccabi Tel-Aviv",
      "Maccabi Playtika Tel Aviv", "Maccabi Tel Aviv BC",
      "Maccabi Tel Aviv *"),
     "BSL; 6× EuroLeague champion (1977, 1981, 2001, 2004, 2005, "
     "2014); Israeli basketball flagship club. CROSS-SPORT COLLISION "
     "RISK: Maccabi Tel Aviv FC (Israeli Premier League). Bare "
     "'Tel Aviv' INTENTIONALLY EXCLUDED (also shared with Hapoel "
     "Tel Aviv BSL). 'Playtika' current sponsor. Hyphenated 'Tel-Aviv' "
     "and asterisk-suffix defensive coverage. EuroLeague crossovers "
     "expected in F7 post-apply per Liga ACB Day-30 precedent "
     "(Panathinaikos + Rytas crossovers materialized)"),
]
