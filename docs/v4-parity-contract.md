# v4 parity contract — /api/events

Enumeration of every field the frontend reads from `/api/events`,
split into PARITY-REQUIRED (v4-served records must byte-match v3
in these fields) and OUT OF SCOPE (backend emits, no JS consumer).
Referenced from **SP Architecture v1.5 §8.1 (Deprecation policy)**
and **§11.4 (Phase 3 — Cutover)** as the compatibility mechanism
that supersedes path-versioning.

## Provenance & maintenance

- **Source**: reconstructed 2026-08-04 from a top-to-bottom read of
  `main.py` (`/api/events` handler + `_format_outcomes` +
  `_live_state` build) crossed against `static/index.html`
  (`/api/events` consumer). Consumers other than `index.html`
  (`static/sports.html`, `src/*.ts`, `static/dist/main.js`) do NOT
  read `/api/events` — they hit `/api/sports/{sport_id}/feed`,
  `/api/event/{ticker}/normalized`, and per-tab endpoints instead,
  so this contract is defined entirely by `index.html`.
- **Line-number caveat**: the `main.py:NNNN` and `index.html:NNNN`
  citations below reflect the source state at 2026-08-04 (before
  PR #288, #289, #290, #296, #299, #300, #301). Line numbers have
  drifted; the **symbol names** (function names, field names, HTML
  IDs) remain the authority. Re-anchor a specific citation with
  `grep -n` before quoting it in a new PR.
- **Delivery caveat carried forward from source**: the field
  inventory was assembled from the top ~700 lines of `sports.html`
  and top ~150 lines of `admin/router.py`; the tail of each was
  inferred from the imports + surrounding shape rather than
  line-by-line read. The `/api/events`-specific inventory (which
  is what this contract locks) was full-read on both sides and
  is high-confidence; the peripheral commentary in Section 2 of
  the original delivery (frontend state-of-the-world) is not
  reproduced here as it is out of scope for the parity contract.

---

# SECTION 1 — Field-usage inventory (`/api/events` parity contract)

**Consumers**: only `static/index.html` reads `/api/events` directly (lines 1436, 2537). `static/sports.html`, the `src/*.ts` bundle, and `static/dist/main.js` do NOT — they consume `/api/sports/{sport_id}/feed`, `/api/event/{ticker}/normalized`, and per-tab endpoints instead. So the parity contract for `/api/events` is defined entirely by `index.html`.

**Envelope** (`main.py:3532`): `{"total": int, "offset": int, "limit": int, "events": [event, …]}`.

## 1A — Top-level event keys

| Field | Source (main.py) | index.html usage | Status |
|---|---|---|---|
| `event_ticker` | 2517 | 1487, 2228, 2239, 2560, 7462 | **PARITY-REQUIRED** |
| `title` | 2518 | 2248 | **PARITY-REQUIRED** |
| `category` | 2519 | 2225-2226, 7458 | **PARITY-REQUIRED** |
| `series_ticker` | 2520 | 2229-2230 | **PARITY-REQUIRED** |
| `_sport` | 2521 | 1583, 1692, 1749, 2016, 2224, 7457, 7485, 7521 | **PARITY-REQUIRED** |
| `_subcat` | 2523 | 2240, 2245, 7461, 7475 | **PARITY-REQUIRED** |
| `_display_dt` | 2525 | 1608 (fallback when `_kickoff_dt` missing) | **PARITY-REQUIRED** |
| `_kickoff_dt` | 2526, 3320 (ESPN override) | 1600-1605, 1583-1585, 7464 | **PARITY-REQUIRED** |
| `_close_dt` | 2528 | 7465 (detail-page fallback) | PARITY-REQUIRED |
| `_exp_dt` | 2529 | 7465 (detail page) | PARITY-REQUIRED |
| `outcomes[]` | 2531 → `_format_outcomes` at 1936 | 2176-2220, 2636 | **PARITY-REQUIRED** (see 1B) |
| `market_groups[]` | 3113-3123 | 2268-2298, 2602-2633, 7737-7749 | **PARITY-REQUIRED** (see 1C) |
| `_live_state` | 3203+ (conditional) | extensive (see 1D) | **PARITY-REQUIRED** conditional |
| `_market_settling` | 3489-3518 (conditional) | 1671, 4186 | **PARITY-REQUIRED** conditional |
| `_soccer_comp` | 2522 | never read by JS | **OUT OF SCOPE** (backend-filter-only) |
| `_is_sport` | 2524 | never read | **OUT OF SCOPE** |
| `_game_end_dt` | 2527 | never read | **OUT OF SCOPE** |
| `_sort_ts` | 2530 | never read | **OUT OF SCOPE** (backend-sort-only) |
| `_vol24h_total` | 2535 | never read | **OUT OF SCOPE** (backend-Live-filter-only) |
| `_is_live` | 2582 | never read; JS uses `_live_state.state` instead | **OUT OF SCOPE** |

## 1B — Per-outcome shape (inside `outcomes[]` and `market_groups[].outcomes[]`)

`_format_outcomes` at `main.py:1936-1942` emits ONLY these 5 fields; ~20 underscored raw fields on the stored records are stripped:

| Field | JS reads at |
|---|---|
| `label` | 2195, 2198, 2200, 2628 |
| `ticker` | 2208, 2653 |
| `chance` | 2210, 2629 |
| `yes` | 2211, 2630 |
| `no` | (paired with `yes`), 2631 |

Live-price overlay at `main.py:3548-3579` may rewrite `chance`/`yes`/`no` in place.

## 1C — `market_groups[]` shape

Present only when `view != "all"` AND the primary market has sibling market-type events. Per-group fields:

| Field | JS reads at | Status |
|---|---|---|
| `type_code` | 2279, 2293, 2296, 2606 | **PARITY-REQUIRED** |
| `label` | 2283, 7734 | **PARITY-REQUIRED** |
| `event_ticker` | 2280, 7742, 7747 | **PARITY-REQUIRED** |
| `url` | 2281 | **PARITY-REQUIRED** |
| `outcomes[]` | 2296, 2611, 7741 | **PARITY-REQUIRED** (5-field outcome shape) |
| `series_ticker` | never read | **OUT OF SCOPE** |

## 1D — `_live_state` sub-object

Present when FL/ESPN match, or when the basketball-playoff / soccer-bracket synth paths fire.

**PARITY-REQUIRED**:

| Field | Source | Notes |
|---|---|---|
| `state` (`"pre"`\|`"in"`\|`"post"`\|`""`) | 3205 | isLive gate (1554), buildScoreMap gate (2008), detail (4181) |
| `short_detail` | 3206 | liveTimeLabel (1574) |
| `display_clock` | 3207 | liveTimeLabel (1573) |
| `period` | 3208 | soccer minute pick (1693) |
| `stage_start_ms` | 3209 | computeSoccerMinute (1692), attrs (1762) |
| `captured_at_ms` | 3211 | elapsed-since-poll (1706) |
| `clock_running` | 3212 | (1709, 3029) |
| `home_abbr`, `away_abbr` | 3213-3214 | scoreMap keywords (2046-2047), series pill (1652) |
| `home_display`, `away_display` | 3215-3216 | scoreMap (2046-2047), detail scoreboard (7580, 7588) |
| `home_score`, `away_score` | 3217-3218 | scoreMap (2006), liveScoreString (1618) |
| `score_display` | 3219 | liveScoreString primary (1616) |
| `title_home`, `title_away` | 3225/3270-3274 | scoreMap keyword merge (2048-2049) |
| `is_playoff` | 3229 | renderSeriesPill (1644) |
| `series_title` | 3230 | detail (7657) |
| `series_summary` | 3231 | fallback pill (1649) |
| `series_home_wins`, `series_away_wins` | 3232-3233 | series pill (1645-1646) |
| `series_game_number` | 3234 | (1647, 1658) |
| `is_two_leg` | 3236 | renderSeriesPill (1633) |
| `aggregate_home`, `aggregate_away` | 3237-3238 | (1634-1635) |
| `leg_number` | 3239 | (1640) |
| `round_name` | 3240 | detail (7604) |
| `tournament_name` | 3241 | detail (7605, fallback for round_name) |
| `aggregate_winner` | 3242 | detail (7607) |

**CONDITIONAL by sport**:

- Soccer: `added_time_1h`, `added_time_2h` (3220-3221) — index.html:1693-1694, 1770-1774
- Cricket: `cricket_home_wickets`, `cricket_away_wickets`, `cricket_home_overs`, `cricket_away_overs` (3250-3253) — index.html:2016-2029, 7573
- Tennis: `tennis` sub-object with `row1_name/row2_name/row1_sets/row2_sets/set_history[]/row1_point/row2_point/server` (3279-3309) — index.html:2060-2078

**OUT OF SCOPE** (backend emits, no JS reads):

- `_live_state.label` (frontend derives its own via `liveScoreString`)
- `_live_state.league`
- `_live_state.cricket_live_sentence`
- `_live_state.tennis.row1_games`, `.row2_games` (only `set_history[].row1/row2` is read)

## 1E — Two gaps operator should confirm

1. **`_live_state.clock_source`** — read by index.html at 1740, 1754, 3017-area, 7512, 7527 (as `live.clock_source`), but the agent could not find `_espn_clock_override` (defined at `main.py:3599`) called from within `get_events` at `main.py:2612-3532`. My grep confirms: the function is defined at 3599, no invocations elsewhere in main.py. **Likely never populated on `/api/events` responses today; frontend reads default to empty string.** If the frontend behavior of "unknown clock source" is fine, this stays out of scope for v4 parity. If it's a latent bug worth fixing, sibling ticket.

2. **Top-level `url`** — populated by `/api/event/{ticker}` (via `_kalshi_url` at `main.py:3668`), NOT by `/api/events`. Frontend detail page reads it at index.html:7463 from the detail response, not the list response. Not a parity issue for `/api/events`.
