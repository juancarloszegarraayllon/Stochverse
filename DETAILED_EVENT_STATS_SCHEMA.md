# Detailed Event Stats — Master Schema

> Sources: `fl_probe v2` (inventory across 31 sports), `fl_probe v3`
> (Q2/Q4 retest), `fl_probe v4` (canonical example IDs from FL OpenAPI
> spec). Every leaf below grounded in a real response key from one of
> those runs.

**⚠️ Critical mental-model fix (probe v4, 2026-05-02):**
**404 from any FL event endpoint = "no data for this event in this
category"**, NOT "endpoint dead". Probe v4 hit the spec's canonical
example IDs and confirmed `/player-stats`, `/player-statistics-alt`,
`/throw-by-throw`, `/no-duel-data`, `/rounds-results`, and
`/commentary` all return rich data — even though they 404'd against
random `/list` events in v2. **Treat capability per-event, not
per-endpoint.** The right architecture is capability flags driven by
`/v1/events/last-change` hashes, not endpoint tombstones.

**Status legend** &nbsp; ✅ confirmed live (probe v2 inventory or v4
canonical) &nbsp; ▲ partial / sport-specific &nbsp; ← NEW = not yet
surfaced by Stochverse &nbsp; ∅ 200 but empty &nbsp; · 404 (no data
for the event probed; endpoint may still return for other events)

---

## 1. Coverage tiers

We classify FL's 39 sport_ids into tiers based on what the inventory shows.
Each tier shares one diagram. Per-sport overrides are called out below.

| Tier | Profile | Sports |
|---|---|---|
| **A** | Full team sports (lineups + stats + incidents + odds) | Soccer, Hockey, Aussie Rules, Rugby League |
| **B** | Team sports without lineups (stats only, off-season noisy) | American Football, Rugby Union, Baseball |
| **C** | Score-tracking team sports (scoreboard + summaries, often + odds + points-history + per-player stats) | Basketball, Handball, Volleyball, Floorball, Futsal, Field Hockey, Beach Volleyball, Water Polo, Beach Soccer, Esports, Pesapallo, Netball |
| **D** | Individual / head-to-head sports | Tennis, Darts, Snooker, Boxing, MMA, Table Tennis, Badminton |
| **E** | Cricket — special case. Rich pre-match data via `/data` and `/summary`. Scorecard family (`/scorecard`, `/fall-of-wickets`, `/ball-by-ball`) 404'd on both v2 random events AND v4 spec canonical (`tK1xeE9p`) — endpoints documented but no data flowing to our IDs. Re-probe during a live IPL match before designing the cricket scorecard tab. | Cricket |
| **F** | No FL modal — only `/brief` + `/missing-players` and both return empty placeholders | Horse Racing |
| **G** | Confirmed nonexistent endpoint family. Probe v3 tested 11 candidate `/v1/races/*` paths × 4 param names → 100% 404. Probe v4 retested `/v1/events/racing-details` against the spec canonical (`sport=35, template=fsB7cpNF`) → also 404. Treat as no FL data via current API. | Motorsport, Cycling |
| **H** | No event in ±7d window during probe — re-probe in season | Bandy, Autoracing, Motoracing, Winter Sports, Ski Jumping, Cross Country, Biathlon, Kabaddi |
| **I** | Individual no-duel sports — uses `no_duel_event_id + event_id` pair, separate endpoint family. Probe v4 confirmed `/no-duel-data` (rich event metadata) + `/rounds-results` (per-round results). | Golf |

---

## 2. Universal modal — ground-truth schema

```
DETAILED EVENT STATS — modal blueprint (capability-driven render)

[1] MATCH
 ├── Header                       /v1/events/data            ✅ universal
 │      capability flags:           DATA.EVENT.HAS_LINEPS
 │                                  DATA.EVENT.HAS_LIVE_CENTRE
 │                                  DATA.EVENT.STATS_DATA
 │                                  DATA.TOURNAMENT.HAS_LIVE_TABLE
 ├── Extended Details             /v1/events/details         ✅ confirmed v4 (was beta)
 │      DATA.__TYPENAME, IS_LIVE_UPDATE_EVENT, SETTINGS,
 │      EVENT_ROUND, LEAGUE_NAMES.{NAME_A,NAME_C},
 │      EVENT_PARTICIPANTS[].PARTICIPANTS[].PARTICIPANT.IMAGES
 ├── Brief score                  /v1/events/brief           ← NEW — compact snapshot
 ├── Summary                      /v1/events/summary         ✅ Tier A/B (incidents)
 │                                                            ▲ Tier C/D (scoreboard only)
 ├── Summary results              /v1/events/summary-results ✅ scoreboard breakdown
 ├── Summary incidents            /v1/events/summary-incidents ✅ goals/cards/subs
 ├── Stats                        /v1/events/statistics      ▲ Tier A only
 │     └─ [darts]                 /v1/events/statistics-alt  ← NEW — only darts
 ├── Lineups                      /v1/events/lineups         ▲ Soccer / Hockey /
 │                                                            Aussie Rules / Rugby League
 │      MEMBERS[].INCIDENTS[]: per-player event IDs (decoder = stat-type
 │      enum from API docs "List of Object" page; e.g. 1=YELLOW_CARD)
 ├── Predicted Lineups            /v1/events/predicted-lineups ← NEW — pre-match,
 │                                                              ~all team sports
 ├── Missing Players              /v1/events/missing-players ✅ universal
 ├── Player Stats                 /v1/events/player-stats    ← NEW (probe v4) — RICH
 │      DATA.{TEAMS, PLAYERS, STATS_TYPE_GROUPS, STATS_TYPES, STATS, RATINGS}
 │      Spec canonical (Sbld5SC5) returned 335 KB. Per-event capability
 │      flag, gate render on /last-change.PLAYER_STATISTICS hash present.
 │     └─ [basketball] Player Stats (alt)  /v1/events/player-statistics-alt
 │           DATA.{TABS, BLOCKS} — 6 KB tabular, basketball-specific shape
 ├── Highlights (video)           /v1/events/highlights      ← NEW — Soccer, Cricket,
 │                                                            Aussie Rules, Rugby League
 ├── News                         /v1/events/news            ← NEW — Snooker,
 │                                                            Rugby League only
 ├── Points History               /v1/events/points-history  ← NEW — Tennis, Basketball,
 │                                                            Handball, Volleyball,
 │                                                            Snooker, Beach Volleyball,
 │                                                            Aussie Rules
 ├── [tennis] Points History      same                       ✅ rich set/game progression
 ├── [cricket] Scorecard          /v1/events/scorecard       ⚠ 404 on v2 random + v4 canonical
 ├── [cricket] Fall of Wickets    /v1/events/fall-of-wickets ⚠ 404 — re-probe live IPL
 ├── [cricket] Ball-by-Ball       /v1/events/ball-by-ball    ⚠ 404 — re-probe live IPL
 ├── [darts]  Throw-by-Throw      /v1/events/throw-by-throw  ✅ confirmed v4 (canonical j9TDJ0XI)
 │      DATA.{VALUE, BLOCKS} — 10 KB. Probe v2 404'd because event
 │      wasn't live; spec canonical is a stored historical example.
 ├── Commentary                   /v1/events/commentary      ✅ confirmed v4 (canonical 4U8yxaPL)
 │      DATA=[{END_MATCH}, ...×145] — 29 KB. NOT universally dead — was
 │      data-conditional 404 in v2 inventory. Render gated by capability.
 ├── Report                       /v1/events/report          · 404 on canonical (4U8yxaPL)
 │                                                            also empty in v2 — leave dropped
 └── Last-change hash             /v1/events/last-change     ✅ universal (delta polling)
        Hashes returned: COMMON, SUMMARY, STATISTICS, LINEUPS,
        PLAYER_STATISTICS, HIGHLIGHTS — drives capability gating.

[1b] GOLF (Tier I — separate endpoint family)
 ├── No-Duel Data                 /v1/events/no-duel-data    ✅ confirmed v4
 │      Params: locale + no_duel_event_id + event_id (NOT just event_id)
 │      DATA.{FEATURES, RANKINGS, STAGE, EVENT_PARTICIPANT_*, ...}
 └── Rounds Results               /v1/events/rounds-results  ✅ confirmed v4
        DATA=[{GOLF_ROUND, ITEMS}, ×4 rounds]

[2] H2H                            /v1/events/h2h            ✅ universal, rich
                                                              (3 tabs, 114 KB on canonical)
[3] STANDINGS                      (separate endpoint family — out of scope for v2 modal)
[4] DRAW / BRACKET                 (separate endpoint family — out of scope for v2 modal)
[5] ODDS                           /v1/events/odds           ▲ 10 sports — see §4
 ├── Prematch Odds                /v1/events/prematch-odds   ← NEW (probe v4)
 │      Params: locale + sport_id + event_id (NOT just event_id)
 │      DATA=[{BOOKMAKER_ID, BOOKMAKER_BETTING_TYPE, BOOKMAKER_NAME, ITEMS}, ...]
 ├── Live Odds (alt)              /v1/events/live-odds-alt   ⚠ needs live event
 │      Params: locale + bet_type (HOME_AWAY|HOME_DRAW_AWAY) + event_id + book_id
 │      Probe v4 got 404 on canonical (event wasn't live). Re-probe live.
 └── Bulk odds list               /v1/events/list-main-odds  ← NEW (probe v4) — sport+date
        Params: locale + sport_id + timezone + indent_days
        Returns ~1000 events with odds. Different shape — not modal-level,
        belongs in cards-list architecture (Step E).
[6] NEWS                           /v1/events/news            ▲ Snooker, Rugby League only

[REAL-TIME — for live polling architecture, not modal]
 ├── Live List                    /v1/events/live-list       ✅ confirmed v4
 │      Params: locale + sport_id + timezone
 │      Returns currently-live events (74 soccer events at probe time)
 └── Live Update                  /v1/events/live-update     ✅ confirmed v4
        Params: locale + sport_id
        Returns just event_ids that changed; call every 5 sec per FL docs.
```

---

## 3. Endpoints status — revised after probe v4

**Original framing (probe v2/v3): "endpoints universally empty across
all sports" → drop permanently. This framing was wrong.** Probe v4
(2026-05-02) hit each endpoint with the FL OpenAPI spec's canonical
example IDs and found that 5 of the 6 "dead" endpoints actually return
rich data on the spec's canonical event. The 404s in v2 were random
events that happened to lack data for that category — not endpoint
death.

| Endpoint | v2/v3 verdict | v4 result (canonical) | Revised decision |
|---|---|---|---|
| `/v1/events/player-stats` | 0/40 events → "permanently dead" | ✅ **OK 335 KB** on `Sbld5SC5` (`TEAMS, PLAYERS, STATS_TYPE_GROUPS, STATS_TYPES, STATS, RATINGS`) | **Build block.** Per-event capability flag, gate on `/last-change.PLAYER_STATISTICS` hash. |
| `/v1/events/player-statistics-alt` | 404 every sport | ✅ **OK 6 KB** on `fXx7UFrK` (`TABS, BLOCKS`) | **Build basketball block.** Different shape from `/player-stats`. |
| `/v1/events/throw-by-throw` | 404 (non-live) | ✅ **OK 10 KB** on `j9TDJ0XI` (`VALUE, BLOCKS`) | **Build darts block.** Was data-conditional, not dead. |
| `/v1/events/no-duel-data` | 422 (wrong params used in v2) | ✅ **OK** on golf `tOTtyuU7+n78WB41T` | **Build golf block** — Tier I, requires `no_duel_event_id + event_id` pair. |
| `/v1/events/rounds-results` | 422 (wrong params) | ✅ **OK** on golf `tOTtyuU7+n78WB41T` | **Build golf block** — 4 rounds × ITEMS shape. |
| `/v1/events/commentary` | 404 every sport in v2 | ✅ **OK 29 KB** on `4U8yxaPL` (`[{END_MATCH}, ...×145]`) | **Reclassify** — data-conditional, not dead. Re-probe more events to find which sports/leagues populate it. |
| `/v1/events/racing-details` | 404 (compound IDs) | · 404 on canonical (`sport=35, template=fsB7cpNF`) | Stays dropped (Tier G). Spec canonical also failed → stronger evidence. |
| `/v1/events/commentary-alt` | 404 every sport | · 404 on cricket canonical `tK1xeE9p` | Stays dropped — paired with the cricket scorecard family failure (likely same root cause). |
| `/v1/events/report` | 404 every sport | · 404 on canonical `4U8yxaPL` | Stays dropped — only endpoint where spec canonical also 404'd. |
| `/v1/events/last-change` | ✅ universal (delta polling) | · 404 on canonical `4U8yxaPL` | Keep as universal — canonical 404 is a data-conditional anomaly; v2 inventory confirmed it works on most events. |
| `/v1/events/highlights` | ✅ 4 sports in v2 | · 404 on canonical `Mss8F4uf` | Keep as confirmed — canonical is stale, v2 inventory has higher confidence. |

---

## 4. Odds availability

**`/v1/events/odds`** (∗ = sport returns it):
Basketball ∗, Hockey ∗ (32 kB), Handball ∗, Darts ∗, Snooker ∗ (23 kB),
Boxing ∗, Aussie Rules ∗ (36 kB), Rugby League ∗ (32 kB), MMA ∗, Esports ∗.
Same 12-key shape across all 10. One block design, ten sports.

**`/v1/events/prematch-odds`** (probe v4 confirmed for soccer
canonical `G8hqiThp`, sport=1): 1.2 KB, shape `[{BOOKMAKER_ID,
BOOKMAKER_BETTING_TYPE, BOOKMAKER_NAME, ITEMS}, ...]`. Per-sport
availability needs a sweep — distinct from `/odds` which is event-
state-agnostic.

**`/v1/events/live-odds-alt`** (needs live event to test): requires
`bet_type` enum (`HOME_AWAY` or `HOME_DRAW_AWAY`) + `book_id` (1–1000;
examples in spec: 453=1xbet, 16=bet365). Both bet_type variants 404'd
on canonical `6ZCocWsb` because event wasn't live at probe time.
Re-probe against currently-live event when designing the live odds tab.

**`/v1/events/list-main-odds`** (probe v4 confirmed): 395 KB / 1009
events for soccer today. Sport+date bulk — *not* a modal endpoint.
Belongs in cards-list architecture (Step E) for showing odds on the
front-page event list.

---

## 5. Sport-by-sport notes (only where sport diverges from its tier)

### Soccer (sport_id=1) — Tier A reference
Real data we don't surface today:
- `DATA.EVENT.AWAY_RED_CARDS` / `HOME_RED_CARDS` (running totals)
- `DATA.EVENT.HAS_LIVE_CENTRE`, `STATS_DATA` (capability flags)
- `DATA.EVENT.TV_LIVE_STREAMING.*` (broadcaster info)
- `/lineups` includes `FORMATION_DISPOSTION` (geometric layout) and
  `PLAYER_POSITION_ID` (numeric position) — richer than what we render.
- `/predicted-lineups.PREDICTED_LINEUP.FORMATION` (e.g. "4-3-3").
- `/highlights` returns 13 keys of video URLs we don't show.

### Tennis (sport_id=2) — Tier D
- `/points-history` returns set/game progression with `CURRENT_GAME`,
  `FIFTEENS_CONTENT`, `SERVING`, `LOST_SERVE`, `LAST_SCORED` —
  *the* tennis-specific block we should add.
- `/h2h` includes `SURFACE_CODE` / `SURFACE_NAME` — surface filter on
  H2H is tennis-specific value.
- `/data` carries `AWAY_PARTICIPANT_NAME_TWO` and `COUNTRY_ID_2` —
  doubles support is built in.
- No lineups (single-player). Render the tab as N/A.

### Basketball (sport_id=3) — Tier C
- `/odds`: 12 keys × 13 kB — surface as Odds tab.
- `/points-history` includes `HOME_AHEAD` field — running margin chart.
- No `/statistics`: stats live inside `/data.EVENT.STATS_DATA` only.

### Hockey (sport_id=4) — Tier A
- Full `/lineups` (12 kB, 20 keys, includes `PLAYER_POSITION_ID`).
- Period-level scoring: `HOME_SCORE_PART_1`…
- `/odds`: 32 kB — heavy odds market.
- `/player-stats` returned **424** (not 404) — endpoint exists but
  rejects this request shape. File as question for FL.

### American Football (sport_id=5) — Tier B
- Probe ran during off-season → `/summary` returned 3 keys, `/lineups`
  was 404. Capability flags should drive "no data yet", not absence.

### Baseball (sport_id=6) — Tier B
- `/summary-results` returns 25 keys: per-inning runs through 9 innings.
- `/data` carries `AWAY_HITS`, `AWAY_ERRORS`, `HOME_HITS`, `HOME_ERRORS`
  — surface H/E/R line under the scoreboard.

### Cricket (sport_id=13) — Tier E special
- `/data` is rich live state: `CRICKET_LIVE_SENTENCE`, `RU` (runs),
  `RV` (run-rate), `WX` (wickets), score-by-innings — every key cricket-
  specific.
- `/summary`: `AWAY_OVERS_AND_BALLS_FIRST_INNING`, `AWAY_WICKETS_FIRST_INNING`
  — surface as Innings card.
- ⚠ `/scorecard`, `/fall-of-wickets`, `/ball-by-ball`: **404 on both
  v2 random events AND v4 spec canonical** (`tK1xeE9p`). The fact that
  the spec's own canonical 404'd is the strongest signal yet that
  these endpoints are gated on a condition we haven't identified
  (live state? tournament tier? data-feed contract?). **Action: file
  with FL/RapidAPI support before designing the cricket scorecard tab.**
  Design the cricket modal *without* scorecard for now.
- `/commentary-alt` (cricket-specific): also 404 on canonical → likely
  same root cause as scorecard family.
- `/highlights`: works (13-key video shape).

### Darts (sport_id=14) — Tier D special
- `/statistics`: 404. `/statistics-alt`: ✅ 5 keys (`CATEGORY`, `ID`,
  `VALUE_AWAY`, `VALUE_HOME`) — *the* darts stats endpoint.
- `/throw-by-throw`: ✅ **confirmed live in probe v4** (canonical
  `j9TDJ0XI`, 10 KB, `{VALUE, BLOCKS}`). The v2 404 was because the
  event wasn't live; spec canonical is a stored historical example.
  Build the throw-by-throw block — gate render on capability.
- ⚠ `/last-change`: **404 for darts**. **Resolution: tab-open polling
  only — no live polling for darts.** Hashing the body ourselves saves
  no bandwidth (we'd still re-fetch to hash), and darts is low-demand
  enough that stale data between user clicks is acceptable.

### Golf (sport_id=23) — Tier I (was Tier F, reclassified by probe v4)
- Uses **`no_duel_event_id + event_id` pair**, not just `event_id`.
  Probe v2 hit golf with `event_id` only and got 422 → wrongly
  classified as "no FL data" (Tier F).
- `/no-duel-data` (probe v4 canonical `tOTtyuU7+n78WB41T`): 619 bytes,
  `DATA.{FEATURES, BIRTHDAY_TIMESTAMP, EVENT_PARTICIPANT_RANKING,
  EVENT_PARTICIPANT_COUNTRY, STAGE, ...×13}` — golf event metadata
  + per-participant ranking.
- `/rounds-results` (same params): 4.5 KB,
  `[{GOLF_ROUND, ITEMS}, ×4 rounds]` — per-round results, the canonical
  golf scorecard view.
- Build a golf-specific modal: Header + Rounds Results + No-Duel Data.
  Skip Lineups/Stats/H2H tabs (don't apply).

### Snooker (sport_id=15) — Tier D
- `/news`: ✅ 12 keys (publishers, links, images). Snooker is one of
  only two sports with news.
- `/points-history`: frame-by-frame progression.
- `/last-change.NEWS` field present → news polls separately.

### Aussie Rules (sport_id=18) — Tier A
- Highest `/data` key count (120). Full lineups, big odds market, video
  highlights. Closest analogue to soccer in terms of feature parity.

### Rugby League (sport_id=19) — Tier A
- `/news` ✅ (only sport other than Snooker). `/lineups` ✅. `/odds` 32 kB.

### Boxing / MMA (sport_ids 16, 28) — Tier D individual
- `/odds` ✅ for both.
- `/data.EVENT.MMA_HOME_FINAL_RESULT`, `MMA_HOME_FINISHED_IN_ROUND`
  → fight-result tab makes sense.

### Esports (sport_id=36) — Tier C
- Full team-sport surface (data/details/brief/summary/odds/h2h all
  return). Surprisingly close to Basketball in shape.

### Tier F — Horse Racing (35) only
(Golf moved to Tier I after probe v4 reclassification — see Golf
section above.) Probe v4 confirmed Horse Racing's `/racing-details`
endpoint also 404s on the spec canonical (`sport=35,
template=fsB7cpNF`). Only `/brief` + `/missing-players` return,
both empty placeholders. **Skip the modal entirely** — show only
the card header.

### Tier G — Motorsport (31), Cycling (34)
Every `/v1/events/*` endpoint returns 422 because event_ids returned
from `/list` are 16-char compound IDs (e.g. `Y9HWxKnpMctvDyx2`).
Probe v3 confirmed no `/v1/races/*` family exists (11 paths × 4 param
names → 100% 404), and `/v1/tournaments/*` rejects the compound IDs
too. **Out of scope for v2 modal — show only the card header, no
modal.** Could revisit if FL adds a races API; not actionable today.

### Tier H — re-probe needed
Bandy, Autoracing, Motoracing, Winter Sports, Ski Jumping, Cross Country,
Biathlon, Kabaddi: no events in ±7d during probe. Re-run the inventory
when each sport is in season.

---

## 6. Round-1 build recommendation (~7 days, revised after probe v4)

Tackle in this order. Every item below is backed by real response
keys from probe v2 inventory or v4 canonical retest.

1. **`/v1/events/player-stats` block** — NEW priority #1 after probe v4.
   335 KB on canonical, 6 top-level data keys (`TEAMS, PLAYERS,
   STATS_TYPE_GROUPS, STATS_TYPES, STATS, RATINGS`). This is the
   single highest-value block in the inventory — per-player tracking
   we've been missing. Gate render on
   `/last-change.PLAYER_STATISTICS` hash. Need a per-sport sweep to
   know which sports populate it; spec hints at major team sports.
   ~1.5 days (block + capability gating + sport sweep).

2. **`/v1/events/predicted-lineups` across team sports** — universal
   pre-match block we currently miss. Soccer, Basketball, Hockey,
   Baseball, AMF, Volleyball, Cricket all return data with the same
   8-key shape (`PREDICTED_LINEUP.FORMATION` + `GROUPS` + `PLAYERS`).
   ~1 day.

3. **`/v1/events/odds` + `/prematch-odds` for the 10 sports** (see §4).
   Same shape, two endpoints (live odds vs prematch). Build as one
   tab with mode toggle. ~1.5 days.

4. **`/v1/events/highlights` for the 4 sports that return video** —
   Soccer, Cricket, Aussie Rules, Rugby League. 13-key shape uniform.
   ~1 day.

5. **`/v1/events/points-history` for Tennis** — set/game/point
   progression. Fundamentally different from the cross-sport summary
   block; tennis users expect this. ~1 day.

6. **Darts blocks** — `/statistics-alt` (basic stats) +
   `/throw-by-throw` (live throw progression, confirmed live by v4).
   Combined ~0.5 day.

7. **Golf modal** — new sport, dedicated modal: Header + Rounds Results
   (`/rounds-results`) + No-Duel Data (`/no-duel-data`). Requires
   `no_duel_event_id + event_id` pair-passing in our routing layer.
   ~0.5 day.

---

## 7. Round-2 candidates (defer)

- `/v1/events/news` for Snooker + Rugby League — 2 sports isn't enough
  to justify shared infra; revisit if FL adds more.
- `/v1/events/details` (beta) — adds player headshots and longer league
  names, but no user-visible value yet. Nice-to-have.
- Cricket scorecard family (`/scorecard`, `/fall-of-wickets`,
  `/ball-by-ball`) — pending re-probe during a live match.

---

## 8. Out of scope for v2 modal

- Player drill-in (`/v1/players/*` family).
- Team drill-in (`/v1/teams/*` family).
- Tournament drill-in (`/v1/tournaments/*` family).
- Standings tab — separate endpoint family.
- Draw / Bracket tab — separate endpoint family.
- Motorsport / Cycling / Horse Racing — different endpoint families.

These are Step E candidates after the modal lands.

---

## 9. Open questions

1. **Cricket scorecard family** — 404 on both v2 random events AND
   v4 spec canonical (`tK1xeE9p`). The spec-canonical 404 is the
   strongest signal yet that these endpoints are gated on a condition
   we haven't identified. Status: **open**. Action: file with
   FL/RapidAPI support OR re-probe specifically during a live IPL
   ball-by-ball state. Non-blocking — design cricket modal without
   scorecard for now.

2. ✅ **`/player-stats` reopened and resolved POSITIVE** — probe v4
   hit the spec canonical (`Sbld5SC5`) and got **335 KB** of rich
   per-player data (`TEAMS, PLAYERS, STATS_TYPE_GROUPS, STATS_TYPES,
   STATS, RATINGS`). The probe v3 verdict "dead" was wrong — random
   `/list` events were data-conditional 404s, not endpoint death.
   `/player-statistics-alt` (basketball) also confirmed working
   (canonical `fXx7UFrK`). Both added to §2 modal blueprint and §3
   reclassified. **NEW priority #1 in §6 round-1.**

3. ✅ **Darts polling** — resolved: tab-open only, no live polling
   (darts is low-demand, hashing the body saves no bandwidth).
   `/throw-by-throw` confirmed working in v4 — was data-conditional
   404 in v2, not endpoint death.

4. ✅ **Motorsport / Cycling 422** — resolved as **no FL data via
   current API**. Probe v3 tested 11 candidate `/races/*` and
   `/tournaments/*` paths × 4 param names against the compound 16-char
   event_ids — all 404 or 422. Probe v4 also confirmed
   `/v1/events/racing-details` 404s on the spec canonical
   (`sport=35, template=fsB7cpNF`). Tier G stays "no FL modal".
   Note: Tier F now contains only Horse Racing (Golf reclassified
   to Tier I after probe v4 — see §5).

5. ✅ **Per-sport probe re-runs** — resolved: weekly cron added to
   `fl_probe_inventory.yml` (Sundays 06:00 UTC). Mega plan has 10GB/mo
   bandwidth + unlimited requests, so weekly cron costs ~0.5% of quota
   (~50MB/month). Catches newly-in-season sports automatically.

6. **NEW: `/v1/events/live-odds-alt` shape** — probe v4 got 404 on
   both bet_type variants (HOME_AWAY and HOME_DRAW_AWAY) against
   canonical `6ZCocWsb` because event wasn't live at probe time.
   Action: re-probe against a currently-live event before designing
   the live odds tab. Non-blocking — `/odds` and `/prematch-odds`
   cover the static cases.

7. **NEW: `/v1/events/commentary` per-sport availability** — probe v4
   found commentary returns 29 KB on canonical `4U8yxaPL`, contradicting
   the v2 inventory's "404 every sport" finding. Need a sport-by-sport
   sweep to know which sports actually populate commentary so we can
   classify it correctly in §3 (right now it's reclassified as
   "data-conditional, scope unknown"). Non-blocking.

---

*Last revised by `fl_probe/probe_canonicals.py` (probe v4) on
2026-05-02. Re-run any time via Actions → "FL Probe v4 — Canonical
IDs" → Run workflow. Earlier sources: probe v2 inventory
(`probe_inventory.py`), probe v3 races/player-stats retest
(`probe_races.py`).*
