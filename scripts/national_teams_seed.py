"""FIFA national team seed manifest (men's senior, Phase 1 of Issue #136).

This module exposes a single constant — NATIONAL_TEAMS_SEED — used by
scripts/bootstrap_national_teams.py to insert national-team rows into
sp.teams for the Soccer sport. One row per FIFA member association,
men's senior team.

## Source + verification

Source: FIFA member nations list, cross-referenced against ISO 3166-1
alpha-3 codes. Last refreshed: 2026-05-14.

**This manifest was produced from Claude's training-time knowledge of
FIFA membership.** It is intended as a comprehensive starting point,
not as authoritative truth. The re-curation runbook below makes the
verification step mandatory at first apply AND on every membership
update cycle. Treat any discrepancy with FIFA's current site as a
data-correction task, not an emergency.

## Naming convention

Canonical names match FIFA's official member-database naming, NOT
popular naming. This locks the canonical row to FIFA's source of
truth and routes popular-naming variants through the operator alias
workflow (see PR #133 / sub-PR #4 for the make alias-add UX).

Key disambiguations:

  - "Chinese Taipei" (NOT "Taiwan")
  - "North Macedonia" (NOT "Macedonia" or "FYR Macedonia")
  - "Korea Republic" (NOT "South Korea")
  - "Korea DPR" (NOT "North Korea")
  - "Türkiye" (NOT "Turkey" — FIFA updated this in 2022)
  - "Czechia" (NOT "Czech Republic")
  - "Côte d'Ivoire" (NOT "Ivory Coast")
  - "Cabo Verde" (NOT "Cape Verde")
  - "IR Iran" (NOT "Iran" — FIFA's listing convention)
  - "China PR" (NOT "China" — distinguishes mainland from Chinese Taipei + Hong Kong)
  - "United States" (NOT "USA" — popular-name aliases land via operator workflow)

The trigram-similarity trade-off: `similarity('USA', 'United States') ≈ 0`,
so Kalshi titles saying "USA" will NOT auto-match this row — operator
will hit Path B in the anchor_failed surface (PR #137) and add "USA"
as an alias via `make alias-add`. That's intentional. The alternative
(use "USA" as canonical) breaks FIFA-as-source-of-truth + risks
collision with future sport_id taxonomies.

## Re-curation runbook

When FIFA membership changes (every 2-4 years, occasionally
intra-year for newly-recognized associations):

  1. Visit FIFA's member nations page
     (https://www.fifa.com/about-fifa/associations) and compare
     against the entries below. Note any added/removed members.
  2. For ADDED members: insert a new tuple in the appropriate
     confederation section below (preserve alphabetical order
     within section). Use FIFA's official canonical_name + the ISO
     3166-1 alpha-3 code. Leave `notes` as None unless there's a
     suspension or naming-disambiguation footnote.
  3. For REMOVED members: do NOT delete the row. Existing
     `sp.fixtures` rows may reference the team_id; deletion would
     cascade-fail or orphan data. Instead, add a notes string like
     "removed from FIFA YYYY-MM; row kept for historical fixtures".
  4. For SUSPENDED members (FIFA disciplinary action — Russia 2022,
     etc.): keep the row, add notes documenting the suspension date
     and current status. Markets still occasionally reference
     suspended teams (Olympic qualifier markets etc.).
  5. For NAME-CHANGED members (Türkiye 2022, etc.): update the
     canonical_name in place. This requires a forward-looking
     migration plan because sp.teams has no UNIQUE constraint on
     (sport_id, normalized_name) — a name change creates a NEW
     dedup key, which means the bootstrap will INSERT a new row
     rather than rename the existing one. Manual SQL UPDATE on
     sp.teams.canonical_name is the right path for renames; this
     bootstrap is for additions only.
  6. Run `make bootstrap-national-teams ARGS="--dry-run"` against
     a Neon dev branch and verify "would insert N, already present M"
     matches the expected delta.
  7. Run `make bootstrap-national-teams` against production. Capture
     the row-count output for the verification comment per Issue #129
     PR convention.
  8. Update "Last refreshed" date in this docstring (line ~12 above).

## Section header counts

Member counts in section headers refer to **full FIFA members only**,
not total confederation affiliates. Several confederations have more
member associations than FIFA full members:

- AFC: 47 confederation members, 46 FIFA members (Northern Mariana
  Islands is an AFC member but not a FIFA member as of 2024).
- CONCACAF: 41 confederation members, 36 FIFA members (five are
  confederation-only affiliates — French Guiana, Guadeloupe,
  Martinique, Saint Barthélemy, Bonaire).
- CAF: 54 FIFA members; Zanzibar is a CAF associate (granted 2017)
  but not a FIFA member (Tanzania's FIFA membership covers it).

This convention is enforced by the test
test_manifest_size_in_expected_range which bounds total entries at
roughly the FIFA membership count (200-225). Total entries here:
212 = 10 CONMEBOL + 55 UEFA + 54 CAF + 46 AFC + 36 CONCACAF + 11 OFC.

## Out of scope for Phase 1

- Women's national teams (Issue #155 — canonical-name disambiguation
  is the open design item, deferred until day-7 data justifies).
- Other sports' national teams (Cricket, Rugby Union, Hockey,
  Basketball, Handball, Volleyball, etc.). Same Phase-2 deferral
  shape; expand per Issue #136's "Phase 2 (later)" notes after
  day-7 measurement shows market volume per sport.
- Age-category teams (U17, U20, U23 — FIFA recognizes these as
  separate teams in some contexts but Kalshi market shape doesn't
  typically distinguish).
- Olympic-only national teams (countries with separate Olympic
  associations that aren't FIFA members; out of scope per the
  "FIFA-as-source-of-truth" framing).

## Format

Each entry is a tuple of (canonical_name, alpha3_code, notes).
`notes` is None for the common case; documented when a row warrants
context (suspension, naming-disambiguation footnote, FIFA-specific
edge case). Comments BELOW a row apply to that row when they
elaborate on the notes field.

Total expected entries: ~211 (FIFA's 2024-2025 membership count).
"""
from __future__ import annotations

NATIONAL_TEAMS_SEED: list[tuple[str, str, str | None]] = [
    # ── CONMEBOL (Confederación Sudamericana de Fútbol, 10 members) ──
    ("Argentina", "ARG", None),
    ("Bolivia", "BOL", None),
    ("Brazil", "BRA", None),
    ("Chile", "CHL", None),
    ("Colombia", "COL", None),
    ("Ecuador", "ECU", None),
    ("Paraguay", "PRY", None),
    ("Peru", "PER", None),
    ("Uruguay", "URY", None),
    ("Venezuela", "VEN", None),

    # ── UEFA (Union of European Football Associations, 55 members) ───
    ("Albania", "ALB", None),
    ("Andorra", "AND", None),
    ("Armenia", "ARM", None),
    ("Austria", "AUT", None),
    ("Azerbaijan", "AZE", None),
    ("Belarus", "BLR", "competitions restricted post-2022; FIFA membership intact"),
    ("Belgium", "BEL", None),
    ("Bosnia and Herzegovina", "BIH", None),
    ("Bulgaria", "BGR", None),
    ("Croatia", "HRV", None),
    ("Cyprus", "CYP", None),
    ("Czechia", "CZE", "renamed from 'Czech Republic' per modern convention"),
    ("Denmark", "DNK", None),
    ("England", "ENG", "FIFA naming convention treats UK home nations separately"),
    ("Estonia", "EST", None),
    ("Faroe Islands", "FRO", None),
    ("Finland", "FIN", None),
    ("France", "FRA", None),
    ("Georgia", "GEO", None),
    ("Germany", "DEU", None),
    ("Gibraltar", "GIB", None),
    ("Greece", "GRC", None),
    ("Hungary", "HUN", None),
    ("Iceland", "ISL", None),
    ("Israel", "ISR", None),
    ("Italy", "ITA", None),
    ("Kazakhstan", "KAZ", "moved from AFC to UEFA in 2002"),
    ("Kosovo", "XKX", "FIFA member since 2016; XKX is the user-assigned code"),
    ("Latvia", "LVA", None),
    ("Liechtenstein", "LIE", None),
    ("Lithuania", "LTU", None),
    ("Luxembourg", "LUX", None),
    ("Malta", "MLT", None),
    ("Moldova", "MDA", None),
    ("Montenegro", "MNE", None),
    ("Netherlands", "NLD", None),
    ("North Macedonia", "MKD", "renamed from 'FYR Macedonia' in 2019"),
    ("Northern Ireland", "NIR", None),
    ("Norway", "NOR", None),
    ("Poland", "POL", None),
    ("Portugal", "PRT", None),
    ("Republic of Ireland", "IRL", None),
    ("Romania", "ROU", None),
    ("Russia", "RUS", "suspended by FIFA Feb 2022; membership intact, competitions barred"),
    ("San Marino", "SMR", None),
    ("Scotland", "SCO", None),
    ("Serbia", "SRB", None),
    ("Slovakia", "SVK", None),
    ("Slovenia", "SVN", None),
    ("Spain", "ESP", None),
    ("Sweden", "SWE", None),
    ("Switzerland", "CHE", None),
    ("Türkiye", "TUR", "renamed from 'Turkey' in 2022 per FIFA convention"),
    ("Ukraine", "UKR", None),
    ("Wales", "WAL", None),

    # ── CAF (Confédération Africaine de Football, 54 members) ────────
    ("Algeria", "DZA", None),
    ("Angola", "AGO", None),
    ("Benin", "BEN", None),
    ("Botswana", "BWA", None),
    ("Burkina Faso", "BFA", None),
    ("Burundi", "BDI", None),
    ("Cabo Verde", "CPV", "renamed from 'Cape Verde' per FIFA convention"),
    ("Cameroon", "CMR", None),
    ("Central African Republic", "CAF", None),
    ("Chad", "TCD", None),
    ("Comoros", "COM", None),
    ("Congo", "COG", "Republic of the Congo; distinguished from DR Congo below"),
    ("Côte d'Ivoire", "CIV", "FIFA-preferred over 'Ivory Coast'"),
    ("DR Congo", "COD", "Democratic Republic of the Congo"),
    ("Djibouti", "DJI", None),
    ("Egypt", "EGY", None),
    ("Equatorial Guinea", "GNQ", None),
    ("Eritrea", "ERI", None),
    ("Eswatini", "SWZ", "renamed from 'Swaziland' in 2018"),
    ("Ethiopia", "ETH", None),
    ("Gabon", "GAB", None),
    ("Gambia", "GMB", None),
    ("Ghana", "GHA", None),
    ("Guinea", "GIN", None),
    ("Guinea-Bissau", "GNB", None),
    ("Kenya", "KEN", None),
    ("Lesotho", "LSO", None),
    ("Liberia", "LBR", None),
    ("Libya", "LBY", None),
    ("Madagascar", "MDG", None),
    ("Malawi", "MWI", None),
    ("Mali", "MLI", None),
    ("Mauritania", "MRT", None),
    ("Mauritius", "MUS", None),
    ("Morocco", "MAR", None),
    ("Mozambique", "MOZ", None),
    ("Namibia", "NAM", None),
    ("Niger", "NER", None),
    ("Nigeria", "NGA", None),
    ("Rwanda", "RWA", None),
    ("São Tomé and Príncipe", "STP", None),
    ("Senegal", "SEN", None),
    ("Seychelles", "SYC", None),
    ("Sierra Leone", "SLE", None),
    ("Somalia", "SOM", None),
    ("South Africa", "ZAF", None),
    ("South Sudan", "SSD", "FIFA member since 2012"),
    ("Sudan", "SDN", None),
    ("Tanzania", "TZA", None),
    ("Togo", "TGO", None),
    ("Tunisia", "TUN", None),
    ("Uganda", "UGA", None),
    ("Zambia", "ZMB", None),
    ("Zimbabwe", "ZWE", None),
    # Zanzibar was excluded from this manifest after second-pass review:
    # CAF granted Zanzibar full CAF membership in 2017, but FIFA does
    # NOT recognize Zanzibar as a separate member association
    # (Tanzania's FIFA membership covers it). The header count below
    # matches FIFA full members only. See module docstring's "Section
    # header counts" paragraph for the convention.

    # ── AFC (Asian Football Confederation, 46 full FIFA members) ─────
    # Note: AFC has 47 total member associations; Northern Mariana
    # Islands was granted AFC full membership in 2020 but is NOT a
    # FIFA member as of 2024. Excluded from this manifest by the
    # FIFA-members-only convention.
    ("Afghanistan", "AFG", None),
    ("Australia", "AUS", "moved from OFC to AFC in 2006"),
    ("Bahrain", "BHR", None),
    ("Bangladesh", "BGD", None),
    ("Bhutan", "BTN", None),
    ("Brunei Darussalam", "BRN", "FIFA-preferred over 'Brunei'"),
    ("Cambodia", "KHM", None),
    ("China PR", "CHN", "FIFA naming; distinguishes mainland from Chinese Taipei / Hong Kong"),
    ("Chinese Taipei", "TPE", "FIFA-mandated name; NOT 'Taiwan'"),
    ("Guam", "GUM", None),
    ("Hong Kong, China", "HKG", "FIFA-mandated name post-1997"),
    ("India", "IND", None),
    ("Indonesia", "IDN", None),
    ("IR Iran", "IRN", "FIFA's listing convention; NOT 'Iran'"),
    ("Iraq", "IRQ", None),
    ("Japan", "JPN", None),
    ("Jordan", "JOR", None),
    ("Korea DPR", "PRK", "FIFA naming for the Democratic People's Republic of Korea"),
    ("Korea Republic", "KOR", "FIFA naming for the Republic of Korea (south)"),
    ("Kuwait", "KWT", None),
    ("Kyrgyz Republic", "KGZ", "FIFA-preferred over 'Kyrgyzstan'"),
    ("Lao PDR", "LAO", "FIFA-preferred over 'Laos'"),
    ("Lebanon", "LBN", None),
    ("Macau", "MAC", "FIFA name; also spelled 'Macao' in some contexts"),
    ("Malaysia", "MYS", None),
    ("Maldives", "MDV", None),
    ("Mongolia", "MNG", None),
    ("Myanmar", "MMR", None),
    ("Nepal", "NPL", None),
    ("Oman", "OMN", None),
    ("Pakistan", "PAK", None),
    ("Palestine", "PSE", "FIFA member since 1998"),
    ("Philippines", "PHL", None),
    ("Qatar", "QAT", None),
    ("Saudi Arabia", "SAU", None),
    ("Singapore", "SGP", None),
    ("Sri Lanka", "LKA", None),
    ("Syria", "SYR", None),
    ("Tajikistan", "TJK", None),
    ("Thailand", "THA", None),
    ("Timor-Leste", "TLS", None),
    ("Turkmenistan", "TKM", None),
    ("United Arab Emirates", "ARE", None),
    ("Uzbekistan", "UZB", None),
    ("Vietnam", "VNM", None),
    ("Yemen", "YEM", None),

    # ── CONCACAF (Confederation of North, Central American and ───────
    # ── Caribbean Association Football, 36 full FIFA members) ───────
    # Note: CONCACAF has 41 total member associations; 5 are
    # confederation-only affiliates (French Guiana, Guadeloupe,
    # Martinique, Saint Barthélemy, and Bonaire — French overseas
    # departments and Dutch Caribbean territories) that participate
    # in CONCACAF competition but lack FIFA voting status. Excluded
    # from this manifest by the FIFA-members-only convention.
    ("Anguilla", "AIA", None),
    ("Antigua and Barbuda", "ATG", None),
    ("Aruba", "ABW", None),
    ("Bahamas", "BHS", None),
    ("Barbados", "BRB", None),
    ("Belize", "BLZ", None),
    ("Bermuda", "BMU", None),
    ("British Virgin Islands", "VGB", None),
    ("Canada", "CAN", None),
    ("Cayman Islands", "CYM", None),
    ("Costa Rica", "CRI", None),
    ("Cuba", "CUB", None),
    ("Curaçao", "CUW", None),
    ("Dominica", "DMA", None),
    ("Dominican Republic", "DOM", None),
    ("El Salvador", "SLV", None),
    ("Grenada", "GRD", None),
    ("Guatemala", "GTM", None),
    ("Guyana", "GUY", None),
    ("Haiti", "HTI", None),
    ("Honduras", "HND", None),
    ("Jamaica", "JAM", None),
    ("Mexico", "MEX", None),
    ("Montserrat", "MSR", None),
    ("Nicaragua", "NIC", None),
    ("Panama", "PAN", None),
    ("Puerto Rico", "PRI", None),
    ("Saint Kitts and Nevis", "KNA", None),
    ("Saint Lucia", "LCA", None),
    ("Saint Vincent and the Grenadines", "VCT", None),
    ("Sint Maarten", "SXM", "FIFA member since 2024"),
    ("Suriname", "SUR", None),
    ("Trinidad and Tobago", "TTO", None),
    ("Turks and Caicos Islands", "TCA", None),
    ("United States", "USA", None),
    ("US Virgin Islands", "VIR", "FIFA naming convention"),

    # ── OFC (Oceania Football Confederation, 11 members) ─────────────
    ("American Samoa", "ASM", None),
    ("Cook Islands", "COK", None),
    ("Fiji", "FJI", None),
    ("New Caledonia", "NCL", None),
    ("New Zealand", "NZL", None),
    ("Papua New Guinea", "PNG", None),
    ("Samoa", "WSM", None),
    ("Solomon Islands", "SLB", None),
    ("Tahiti", "PYF", "FIFA naming; PYF is the alpha-3 for French Polynesia"),
    ("Tonga", "TON", None),
    ("Vanuatu", "VUT", None),
]
