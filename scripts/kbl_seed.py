"""KBL (Korean Basketball League) seed manifest — Phase 2 coverage pilot.

This module exposes two constants — KBL_TEAMS_SEED and
KBL_ALIAS_SOURCE — used by scripts/bootstrap_kbl.py to insert
Basketball team rows + their alias coverage into sp.teams and
sp.team_aliases for the Korean Basketball League's 2025-26 season.

One entry per KBL team. KBL has 10 teams.

## Source + verification

Source: Korean Basketball League 2025-26 active rosters, cross-
referenced across multiple sources during 2026-05-19 seed-write:

  - asia-basket.com KBL standings (2025-26)
  - basketball.realgm.com KBL team registry
  - en.wikipedia.org/wiki/Korean_Basketball_League (search-result
    summary; direct fetch blocked by host 403 — see PR description)
  - en.namu.wiki for sponsor-rebrand history (Anyang KGC → JeongKwanJang)
  - korealocalpages.com KBL team introduction article

**This manifest was produced from web-fetched + curated 2025-26 data.**
The Hangul coverage is partial — see F3 decision below. Operator
spot-check at PR review is required before production-apply.

## Why this bootstrap exists (KBL pilot framing)

Methodology pilot for the 5-sport zero-coverage cohort surfaced
in PROJECT_STATE 2026-05-17: Handball, Snooker, Volleyball, Rugby
League, Golf. KBL is league-level rather than sport-level (it's
ONE league inside Basketball, not a full new sport), and at 10
teams it's small enough to validate the workflow before larger
bootstraps. PR #156's national-teams pattern is the precedent;
KBL extends it with an aliases-write dimension.

## Seed manifest format

Each entry is a 4-tuple: (canonical_name, country_code, aliases, notes).

Extends PR #156's 3-tuple format (canonical_name, alpha3_code, notes)
with an `aliases` dimension (tuple of strings). Aliases get written
to sp.team_aliases on the same team row — whether INSERT-branch or
UPDATE-branch (see semantics below).

### `canonical_name` semantics under F1 decision (mirror PR #156)

The bootstrap discovers INSERT vs UPDATE per team by looking up
normalize_name(canonical_name) in sp.teams. Therefore:

  - For INSERT-branch teams (not yet in sp.teams): `canonical_name`
    is the **current 2025-26 official form**, e.g., "Anyang
    JeongKwanJang Red Boosters".

  - For UPDATE-branch teams (existing in sp.teams with
    country_code NULL, surfaced via Query A2 on 2026-05-19):
    `canonical_name` **MATCHES the existing sp.teams row's stored
    canonical_name** (i.e., the legacy short form like "Goyang
    Sono" or "KCC Egis") so the lookup discovers the existing row
    and routes to the UPDATE-branch backfill. The current 2025-26
    official form lives as an alias on the same row.

This mirrors PR #156's Phase 1.5 backfill precedent exactly — the
2A.5 legacy bootstrap's canonical_name is preserved; only the
country_code field gets updated. Alternative (UPDATE the
canonical_name to current official) was explicitly rejected during
2026-05-19 scope discussion to avoid creating new precedent and
to avoid drift with FL's §9.3 canonical_name authority in the
automatic ingestion flow.

### Alias distinctiveness constraint (cross-league)

All aliases are multi-token, sponsor-prefixed, or otherwise
distinctive within `sport_id=3` (Basketball) globally. **No bare
team-name aliases** like "Goyang", "Egis", "Sono", "KCC", etc.

Precedent for this constraint (from 2026-05-19 Query A2):

  - "Sono" as a bare alias would collide with Mexican baseball
    teams "Cimarrones de Sonora" / "Soles de Sonora" via trigram
    similarity on the shared "sono" substring.
  - "Egis" as a bare alias would collide with Hungarian basketball
    team "Egis Körmend" — both sport_id=3, identical normalized
    alias. Hard collision, not just trigram similarity.
  - "Goyang" as a bare alias would collide with any other-sport
    Goyang team (e.g., Goyang Citizen FC if it existed in sp.teams,
    or any future Korean Goyang team).

The alias-tier matcher's collision detection runs on sport_id +
normalized alias, **not** country_code. Country_code backfilling
helps with FL's automatic disambiguation flow but does NOT prevent
alias-tier collisions for bare aliases. Multi-token discipline is
load-bearing.

## Hangul coverage (F3 decision: partial — 3 of 10 teams)

Per F3 decision on 2026-05-19 — KBL records currently in
sp.review_queue use romanized text only (verified via Hangul
regex query). Hangul aliases ship only for the 3 teams where
authoritative Hangul forms were confirmed during seed-write:

  - Goyang Sono Skygunners → 고양 소노 스카이거너스
  - Anyang JeongKwanJang Red Boosters → 안양 정관장 레드부스터스
  - Changwon LG Sakers → 창원 LG 세이커스

Other 7 teams ship with romanized aliases only. Follow-up tracking
issue (filed alongside this PR) covers the remaining 7 teams'
Hangul expansion. The remaining-7 Hangul work is low-priority:
production records don't currently surface Hangul-only text for
those teams, so the missing aliases don't create matching gaps.

Hangul characters survive `normalize_name()` unchanged
(verified 2026-05-19 against the actual `normalize_name`
function — see PR description). The defensive empty-normalized
guard in the bootstrap won't fire on Hangul-only aliases.

## `sp.team_aliases.source` value (Q3 decision)

This bootstrap uses `KBL_ALIAS_SOURCE = "bootstrap_league_coverage"`.
New value not in the existing source enum comment in
sp_models.py:193 (which lists 'kalshi', 'fl', 'polymarket',
'oddsapi', 'manual_review', 'human_curated'). Rationale:

  - **Generic across the 5-sport cohort.** The same source value
    is reused by Handball / Snooker / Volleyball / Rugby League /
    Golf / Darts bootstraps as they ship. Per-bootstrap source
    values ('bootstrap_kbl', 'bootstrap_handball', etc.) were
    considered and rejected — too specific for the analytics use
    case.
  - **Analytics use case.** The reason we want a non-default
    source value at all is to distinguish bootstrap-seeded
    aliases from operator-added (manual_review, human_curated)
    and from provider-discovered (kalshi, fl, polymarket,
    oddsapi). One generic label covers that distinction without
    over-tagging.
  - **Forward compatibility.** Future tooling that wants to count
    or audit "bootstrap-seeded aliases across the entire coverage
    program" gets a single WHERE-clause value to filter on, not a
    union across N per-bootstrap labels.

## Re-curation runbook

When KBL membership or sponsor branding changes (typical cadence:
sponsor changes once every 2-4 years, team renames/relocations
intra-decade; new-season rosters published ~September each year):

  1. Visit korealocalpages.com or asia-basket.com KBL pages and
     compare against the entries below. Note any added/removed
     teams or renames.
  2. For RENAMED teams (e.g., Anyang KGC → Anyang JeongKwanJang
     in 2023): add new alias for the new sponsor brand, KEEP the
     legacy sponsor alias for older market records. Do NOT change
     canonical_name (per F1).
  3. For RELOCATED teams (e.g., Jeonju KCC → Busan KCC in
     2022-23): add new city-prefixed alias for the new city, KEEP
     the legacy city-prefixed alias.
  4. For new SPONSOR variants: include each distinct romanization
     of the sponsor brand (Korean sports media uses varied romanizations).
  5. Verify all new aliases are multi-token sponsor-prefixed — no
     bare team-name aliases. See "Alias distinctiveness constraint"
     above for the precedent and three concrete collision examples.
  6. Run `make bootstrap-kbl ARGS="--dry-run"` against a Neon dev
     branch and verify "would insert N, would backfill M, would
     add aliases K, already present X" matches the expected delta.
  7. Run `make bootstrap-kbl` against production. Capture row-count
     output in the PR verification comment per Issue #129 convention.
  8. Spot-check via:
       `SELECT t.canonical_name, t.country_code,
               array_agg(ta.alias ORDER BY ta.alias) AS aliases
        FROM sp.teams t
        JOIN sp.sports s ON t.sport_id = s.id
        LEFT JOIN sp.team_aliases ta ON ta.team_id = t.id
              AND ta.source = 'bootstrap_league_coverage'
        WHERE s.name = 'Basketball' AND t.country_code = 'KOR'
        GROUP BY t.canonical_name, t.country_code
        ORDER BY t.canonical_name;`

## Out of scope for this bootstrap

- Predecessor team names (Goyang Carrot-Day One Jumpers,
  Goyang Orion Orions, pre-2022 KCC iterations). Out of frame
  for 2025-26 season scope. Can be added via `make alias-add`
  if historical-record matching needs arise.
- Hangul aliases for the 7 teams without confirmed Hangul
  (deferred per F3; tracking issue filed alongside this PR).
- KBL roster player coverage (separate concern — this bootstrap
  is teams only).
- WKBL (Women's Korean Basketball League) — out of scope per
  yesterday's scope doc.
- Other Korean basketball tiers (lower-division leagues) — out
  of scope per yesterday's scope doc.

## Sponsor abbreviation reference (for operator review)

For reviewer convenience when verifying that sponsor names match
authoritative Korean sources:

  - DB        = Dongbu Securities (financial services)
  - LG        = LG Corporation (consumer electronics)
  - KGC       = Korea Ginseng Corporation (pre-2023 sponsor brand)
  - JeongKwanJang / 정관장 = Korea Ginseng Corp's premium ginseng
                  product brand (current sponsor brand since 2023)
  - KCC       = Korea Cement and Chemical (now KCC Corporation)
  - Samsung   = Samsung Electronics
  - SK        = SK Group
  - Sono      = Daemyung Sono Group (resort/hospitality, sponsor
                since 2023-24)
  - KOGAS     = Korea Gas Corporation
  - KT        = KT Corporation (telecommunications)
  - Hyundai Mobis = Hyundai Mobis Co., Ltd. (auto parts subsidiary
                of Hyundai Motor Group)
"""
from __future__ import annotations


# Source value for sp.team_aliases.source on this bootstrap's writes.
# See module docstring "Q3 decision" section for rationale + operator
# override path.
KBL_ALIAS_SOURCE = "bootstrap_league_coverage"


# Format: (canonical_name, country_code, aliases_tuple, notes).
# All entries are country_code="KOR" (KBL is South Korean).
# All entries are Basketball sport (resolved at bootstrap time via
# sp.sports lookup). See module docstring for canonical_name semantics
# under F1 (mirror PR #156 precedent) and alias distinctiveness
# constraint precedent.

KBL_TEAMS_SEED: list[tuple[str, str, tuple[str, ...], str | None]] = [
    # ── UPDATE-branch teams (existing in sp.teams, country_code NULL) ──
    #
    # These two teams were surfaced by Query A2 on 2026-05-19. The
    # bootstrap's normalized-name lookup will find these existing
    # rows and route them to the backfill branch. Current 2025-26
    # official names live as aliases on the same row.
    (
        "Goyang Sono",
        "KOR",
        (
            "Goyang Sono Skygunners",        # 2025-26 official full
            "고양 소노 스카이거너스",            # Hangul full (F3 confirmed)
        ),
        "UPDATE-branch — existing sp.teams row needs country_code='KOR' "
        "backfill. Canonical retained as 'Goyang Sono' per F1 (mirror PR "
        "#156 precedent); current 2025-26 official 'Goyang Sono Skygunners' "
        "lives as alias. Daemyung Sono Group sponsor since 2023-24. "
        "Predecessor names (Goyang Carrot-Day One Jumpers, Goyang Orion "
        "Orions) NOT seeded — out-of-scope per 2025-26 frame.",
    ),
    (
        "KCC Egis",
        "KOR",
        (
            "Busan KCC Egis",                # 2025-26 official full
            "Jeonju KCC Egis",               # pre-2023 city (legacy)
        ),
        "UPDATE-branch — existing sp.teams row needs country_code='KOR' "
        "backfill. Canonical retained as 'KCC Egis' per F1. Current "
        "2025-26 official is 'Busan KCC Egis' (team relocated from Jeonju "
        "~2022-23); both city-prefixed forms seeded as aliases. KCC = "
        "Korea Cement and Chemical. Hangul not seeded for this team in v1 "
        "(deferred per F3 partial-coverage decision; tracking issue filed).",
    ),

    # ── INSERT-branch teams (not in sp.teams; canonical = current official) ──
    (
        "Anyang JeongKwanJang Red Boosters",
        "KOR",
        (
            # Three distinct romanizations of 정관장, compound-token form
            # (sponsor-only, multi-token via Anyang prefix per
            # distinctiveness constraint):
            "Anyang JeongKwanJang",
            "Anyang JungKwanJang",
            "Anyang Cheongkwanjang",
            # Wikipedia's 6-token space-separated form. Normalizes
            # distinctly from "Anyang JungKwanJang" because the inner
            # whitespace survives normalize_name (token count = 4 in
            # the normalized form vs 2 for the compound forms).
            "Anyang Jung Kwan Jang",
            # Legacy sponsor brand (pre-2023 rebrand):
            "Anyang KGC",
            # Hangul full (F3 confirmed):
            "안양 정관장 레드부스터스",
            # F2 dedup note: "Anyang Jeongkwanjang" (operator-requested
            # case-folded form) normalizes identically to
            # "Anyang JeongKwanJang" → not seeded separately to avoid
            # idempotency-skip noise; both raw forms still match through
            # the normalized alias at query time.
        ),
        "INSERT-branch. Rebranded from 'Anyang KGC' in 2023 (sponsor moved "
        "from KGC corporate brand to Jeonggwanjang ginseng product brand; "
        "both belong to Korea Ginseng Corporation). Four distinct "
        "normalized romanizations of 정관장 covered (jeongkwanjang / "
        "jungkwanjang / cheongkwanjang / 'jung kwan jang' 6-token form). "
        "Legacy 'Anyang KGC' retained for older market records.",
    ),
    (
        "Wonju DB Promy",
        "KOR",
        (
            "Wonju DB",                      # short form (sponsor-only)
        ),
        "INSERT-branch. DB = Dongbu Securities. Hangul deferred per F3.",
    ),
    (
        "Changwon LG Sakers",
        "KOR",
        (
            "Changwon LG",                   # short form (sponsor-only)
            "창원 LG 세이커스",                # Hangul full (F3 confirmed)
        ),
        "INSERT-branch. LG sponsor since founding (1997). Hangul seeded "
        "per F3 confirmed-Hangul subset.",
    ),
    (
        "Daegu KOGAS Pegasus",
        "KOR",
        (
            "Daegu KOGAS",                   # short form
            "Daegu Korea Gas",               # expanded sponsor name
        ),
        "INSERT-branch. KOGAS = Korea Gas Corporation. Hangul deferred "
        "per F3.",
    ),
    (
        "Seoul Samsung Thunders",
        "KOR",
        (
            "Seoul Samsung",                 # short form
        ),
        "INSERT-branch. Samsung Electronics sponsor. Hangul deferred per F3.",
    ),
    (
        "Seoul SK Knights",
        "KOR",
        (
            "Seoul SK",                      # short form
        ),
        "INSERT-branch. SK Group sponsor. Hangul deferred per F3.",
    ),
    (
        "Suwon KT Sonicboom",
        "KOR",
        (
            "Suwon KT",                      # short form
        ),
        "INSERT-branch. KT Corporation telecommunications sponsor. Hangul "
        "deferred per F3.",
    ),
    (
        "Ulsan Hyundai Mobis Phoebus",
        "KOR",
        (
            "Ulsan Hyundai Mobis",           # short form (sponsor-only)
            "Ulsan Mobis Phoebus",           # sometimes shortened
        ),
        "INSERT-branch. Hyundai Mobis (auto parts subsidiary of Hyundai "
        "Motor Group) sponsor. Hangul deferred per F3.",
    ),
]
