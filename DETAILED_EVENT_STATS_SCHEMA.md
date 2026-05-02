# Detailed Event Stats — Master Schema

> Source of truth: `fl_probe v2 — Inventory` run #1 (2026-05-02).
> 31 sports with usable events, 26 endpoints per event, every leaf below
> grounded in a real response key. Where a key is `← NEW` it means FL
> ships it but Stochverse currently does not surface it.

**Status legend** &nbsp; ✅ live in inventory &nbsp; ▲ partial / sport-specific
&nbsp; ← NEW = not yet surfaced &nbsp; ∅ 200 but empty &nbsp; · 404 (N/A)

---

## 1. Coverage tiers

We classify FL's 39 sport_ids into tiers based on what the inventory shows.
Each tier shares one diagram. Per-sport overrides are called out below.

| Tier | Profile | Sports |
|---|---|---|
| **A** | Full team sports (lineups + stats + incidents + odds) | Soccer, Hockey, Aussie Rules, Rugby League |
| **B** | Team sports without lineups (stats only, off-season noisy) | American Football, Rugby Union, Baseball |
| **C** | Score-tracking team sports (scoreboard + summaries, often + odds + points-history) | Basketball, Handball, Volleyball, Floorball, Futsal, Field Hockey, Beach Volleyball, Water Polo, Beach Soccer, Esports, Pesapallo, Netball |
| **D** | Individual / head-to-head sports | Tennis, Darts, Snooker, Boxing, MMA, Table Tennis, Badminton |
| **E** | Cricket — special case (rich pre-match, scorecard family went 404 on our event) | Cricket |
| **F** | No FL data — skip the modal entirely | Golf, Horse Racing (only `/brief` + `/missing-players`, both empty placeholders) |
| **G** | ~~Different endpoint family~~ — `/v1/races/*` confirmed nonexistent (probe v3, 11 paths × 4 param names → 100% 404). Tournament-level endpoints reject the compound IDs too. Treat as no FL data. | Motorsport, Cycling |
| **H** | No event in ±7d window during probe — re-probe in season | Bandy, Autoracing, Motoracing, Winter Sports, Ski Jumping, Cross Country, Biathlon, Kabaddi |

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
 ├── Extended Details (beta)      /v1/events/details         ← NEW (universal team sports)
 │      adds: DATA.EVENT_PARTICIPANTS[].PARTICIPANTS[].PARTICIPANT.IMAGES
 │            DATA.LEAGUE_NAMES.NAME_A / NAME_C
 ├── Brief score                  /v1/events/brief           ← NEW — compact snapshot
 ├── Summary                      /v1/events/summary         ✅ Tier A/B (incidents)
 │                                                            ▲ Tier C/D (scoreboard only)
 ├── Summary results              /v1/events/summary-results ✅ scoreboard breakdown
 ├── Summary incidents            /v1/events/summary-incidents ✅ goals/cards/subs
 ├── Stats                        /v1/events/statistics      ▲ Tier A only
 │     └─ [darts]                 /v1/events/statistics-alt  ← NEW — only darts
 ├── Lineups                      /v1/events/lineups         ▲ Soccer / Hockey /
 │                                                            Aussie Rules / Rugby League
 ├── Predicted Lineups            /v1/events/predicted-lineups ← NEW — pre-match,
 │                                                              ~all team sports
 ├── Missing Players              /v1/events/missing-players ✅ universal
 ├── Highlights (video)           /v1/events/highlights      ← NEW — Soccer, Cricket,
 │                                                            Aussie Rules, Rugby League
 ├── News                         /v1/events/news            ← NEW — Snooker,
 │                                                            Rugby League only
 ├── Points History               /v1/events/points-history  ← NEW — Tennis, Basketball,
 │                                                            Handball, Volleyball,
 │                                                            Snooker, Beach Volleyball,
 │                                                            Aussie Rules
 ├── [tennis] Points History      same                       ✅ rich set/game progression
 ├── [cricket] Scorecard          /v1/events/scorecard       ⚠ 404 on our event — re-probe
 ├── [cricket] Fall of Wickets    /v1/events/fall-of-wickets ⚠ 404 — re-probe
 ├── [cricket] Ball-by-Ball       /v1/events/ball-by-ball    ⚠ 404 — re-probe
 ├── [darts]  Throw-by-Throw      /v1/events/throw-by-throw  ⚠ 404 — re-probe live
 ├── Commentary                   /v1/events/commentary      ∅ empty universally
 ├── Report                       /v1/events/report          ∅ empty universally
 │  (player-stats dropped — see §3, confirmed dead via probe v3)
 └── Last-change hash             /v1/events/last-change     ✅ universal (delta polling)

[2] H2H                            /v1/events/h2h            ✅ universal, rich
[3] STANDINGS                      (separate endpoint family — out of scope for v2 modal)
[4] DRAW / BRACKET                 (separate endpoint family — out of scope for v2 modal)
[5] ODDS                           /v1/events/odds           ▲ 10 sports — see §4
[6] NEWS                           /v1/events/news           ▲ Snooker, Rugby League only
```

---

## 3. Endpoints currently empty across all sports

These are wired-up FL endpoints that returned no useful payload on any
sport's representative event. Don't build blocks for them in v2.

| Endpoint | Result | Decision |
|---|---|---|
| `/v1/events/commentary` | 404 every sport | drop |
| `/v1/events/commentary-alt` | 404 every sport | drop |
| `/v1/events/report` | 404 every sport | drop |
| `/v1/events/player-stats` | 0/40 events across 5 sports in probe v3 retest (also 404 in v2) | **drop permanently**. If we ever specifically need NBA/EPL player stats later, retest then. |
| `/v1/events/player-statistics-alt` | 404 every sport | drop |

---

## 4. Odds availability (∗ = sport returns `/v1/events/odds`)

Basketball ∗, Hockey ∗ (32 kB), Handball ∗, Darts ∗, Snooker ∗ (23 kB),
Boxing ∗, Aussie Rules ∗ (36 kB), Rugby League ∗ (32 kB), MMA ∗, Esports ∗.

Same 12-key shape across all 10. One block design, ten sports.

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
- ⚠ `/scorecard`, `/fall-of-wickets`, `/ball-by-ball`: **all 404** for
  our event. Either the match was between innings or these endpoints
  are conditional on tournament tier. **Re-probe during an IPL match
  before designing the cricket scorecard block.**
- `/highlights`: works (13-key video shape).

### Darts (sport_id=14) — Tier D special
- `/statistics`: 404. `/statistics-alt`: ✅ 5 keys (`CATEGORY`, `ID`,
  `VALUE_AWAY`, `VALUE_HOME`) — *the* darts stats endpoint.
- `/throw-by-throw`: 404 for our (non-live) event. Likely live-only.
- ⚠ `/last-change`: **404 for darts**. **Resolution: tab-open polling
  only — no live polling for darts.** Hashing the body ourselves saves
  no bandwidth (we'd still re-fetch to hash), and darts is low-demand
  enough that stale data between user clicks is acceptable.

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

### Tier F — Golf (23), Horse Racing (35)
Only `/brief` and `/missing-players` return, both 11-byte empty
placeholders. **Skip the modal entirely** — show only the card header.

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

## 6. Round-1 build recommendation (~5 days)

Tackle in this order. Every item below is backed by real keys from
this inventory.

1. **`/v1/events/predicted-lineups` across team sports** — universal
   pre-match block we currently miss. Soccer, Basketball, Hockey,
   Baseball, AMF, Volleyball, Cricket all return data with the same
   8-key shape (`PREDICTED_LINEUP.FORMATION` + `GROUPS` + `PLAYERS`).
   ~1 day.

2. **`/v1/events/highlights` for the 4 sports that return video** —
   Soccer, Cricket, Aussie Rules, Rugby League. 13-key shape uniform.
   ~1 day.

3. **`/v1/events/odds` for the 10 sports that return it** (see §4).
   Same 12-key shape across all 10 → one block design, ten sports
   enabled. ~1.5 days.

4. **`/v1/events/points-history` for Tennis** — set/game/point
   progression. Fundamentally different from the cross-sport summary
   block; tennis users expect this. ~1 day.

5. **`/v1/events/statistics-alt` for Darts** — only path to darts
   stats. ~0.5 day.

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

1. **Cricket scorecard family** — 404 for our event. Action: re-probe
   during a live IPL match. Status: open, not blocking — design the
   cricket modal without scorecard for now, add later if re-probe shows
   data.
2. ✅ **`/player-stats` universally 404** — resolved as **dead**.
   Probe v3 retested with 40 fresh events across 5 sports; 0 returned
   data, including top-flight leagues like the Albanian Superliga and
   Australian AIHL. Dropped from §2 modal blueprint and §3. If we
   ever specifically need NBA/EPL player stats later, retest then.
3. ✅ **Darts polling** — resolved: tab-open only, no live polling
   (darts is low-demand, hashing the body saves no bandwidth).
4. ✅ **Motorsport / Cycling 422** — resolved as **no FL data via
   current API**. Probe v3 tested 11 candidate `/races/*` and
   `/tournaments/*` paths × 4 param names against the compound 16-char
   event_ids — all 404 or 422. Tier G is now equivalent to Tier F
   (Golf / Horse Racing): show card header only, no modal.
5. ✅ **Per-sport probe re-runs** — resolved: weekly cron added to
   `fl_probe_inventory.yml` (Sundays 06:00 UTC). Mega plan has 10GB/mo
   bandwidth + unlimited requests, so weekly cron costs ~0.5% of quota
   (~50MB/month). Catches newly-in-season sports automatically.

---

*Generated from `fl_probe/probe_inventory.py` run on main @ `f481c0d`.
Re-generate any time via Actions → "FL Probe v2 — Inventory" →
Run workflow.*
