"""In-memory cache state for the Stochverse backend.

Each cache is a module-level dict imported and mutated from main.py
and the eventual route/enrichment modules. Mutation patterns
(`cache[key] = value`, `.pop`, `.update`, `.get`) all work across
import boundaries because Python dicts are passed by reference.

NEVER rebind these names (e.g. `_FOO_CACHE = {}` in another module
won't propagate back here — it'd just shadow the import locally).
Always mutate in place.

Helper functions that USE these caches (load-from-DB, save-to-DB,
warm-loops) currently live in main.py because some of them rebind
companion sentinel variables via `global` statements. Moving those
helpers into this module is a separate refactor step.
"""

# ─────────────────────────────────────────────────────────────────
# FL game lookup cache
#
# Per-Kalshi-ticker memo of the result of _find_fl_game (FL match
# resolution). Avoids a fan-out of search_flashlive_event calls when
# multiple sub-tabs of the same modal hit the resolver in quick
# succession. Negative results (no FL match) get a shorter TTL so a
# match that FL just hadn't loaded yet self-heals on the next request.
# Key: ticker (UPPER). Value: (expires_ts, game_dict_or_None).
# ─────────────────────────────────────────────────────────────────
_FL_GAME_CACHE: dict = {}
FL_GAME_CACHE_TTL = 600     # 10 min — generous; modal sessions are short
FL_GAME_NEG_CACHE_TTL = 30  # 30 s for None — let FL warm-up self-heal


# ─────────────────────────────────────────────────────────────────
# Series → tournament_stage_id cache
#
# Persists across deploys via cache_blobs:series_to_stage. Maps a
# Kalshi series_ticker (e.g. KXUCLGAME) to FL's tournament stage
# metadata so future-fixture cards can render aggregate pills + the
# bracket warm loop knows which stages to fetch.
# Key: series_ticker (UPPER).
# Value: {stage_id, season_id, league_name, country, ts}.
# ─────────────────────────────────────────────────────────────────
_SERIES_TO_STAGE_CACHE: dict = {}

# Targeted eviction list applied during _load_series_cache_from_db.
# Add a series here whenever its SOCCER_COMP value changes to force
# re-resolution from the corrected hint. Remove once a clean snapshot
# has been saved in production.
_EVICT_FROM_WARM_START = {
    # Was "CONCACAF" → matched "CONCACAF Nations League" (wrong
    # tournament). Tightened to "CONCACAF Champions Cup".
    "KXCONCACAFCCUPGAME",
}


# ─────────────────────────────────────────────────────────────────
# Tournament bracket cache
#
# Persists across deploys via cache_blobs:tournament_brackets. One
# compact bracket per tournament_stage_id. The cross-stage matcher
# in _bracket_aggregate_for_event walks this when the cached series
# stage_id misses, so multiple stages per league_name (UCL
# qualifying + knockout) can coexist here.
# Key: stage_id. Value: {bracket, ts, season_id, league_name}.
# ─────────────────────────────────────────────────────────────────
_TOURNAMENT_BRACKET_CACHE: dict = {}
_BRACKET_CACHE_TTL_S: float = 300.0  # refresh each entry every 5 min


# ─────────────────────────────────────────────────────────────────
# FL tournaments-list cache
#
# Per-sport-id memo of /v1/tournaments/list responses. The list
# barely changes across a season, so a 6-hour TTL is plenty fresh.
# Used by _find_stage_via_tournaments_list and
# _find_all_stages_for_league.
# Key: sport_id. Value: list of tournament dicts.
# ─────────────────────────────────────────────────────────────────
_FL_TOURNAMENTS_CACHE: dict = {}
_FL_TOURNAMENTS_TTL = 6 * 3600  # 6 hours


# ─────────────────────────────────────────────────────────────────
# Per-event capability probe cache
#
# Memoizes /api/event/{ticker}/capabilities responses so the
# frontend's tab-strip builder doesn't refetch on every modal open.
# 5-min TTL because sport caps are static within a session.
# Key: ticker (UPPER). Value: {payload, _ts}.
# ─────────────────────────────────────────────────────────────────
_EVENT_CAPS_CACHE: dict = {}
_EVENT_CAPS_TTL = 300  # seconds


# ─────────────────────────────────────────────────────────────────
# /api/event/{ticker}/stats response cache
#
# Short-lived (10 s) so live tennis sub-tabs stay current while
# successive sub-tab toggles within the modal hit the cache instead
# of re-fetching three FL endpoints.
# Key: ticker (UPPER). Value: {payload, _ts}.
# ─────────────────────────────────────────────────────────────────
_STATS_CACHE: dict = {}
_STATS_CACHE_TTL = 10  # seconds


# ─────────────────────────────────────────────────────────────────
# /api/event/{ticker}/normalized response cache
#
# Caches the full ~17-FL-endpoint fan-out for the Detailed Event
# Stats panel. 5-min TTL — long enough for sub-tab clicks within a
# modal session to be free, short enough that live data doesn't
# stale out. Stores the full top_scorers list (limit=0); the HTTP
# layer slices on the way out via _slice_top_scorers.
# Key: ticker (UPPER). Value: {payload, _ts}.
# ─────────────────────────────────────────────────────────────────
_EVENT_NORMALIZED_CACHE: dict = {}
_EVENT_NORMALIZED_TTL = 300  # seconds


# ─────────────────────────────────────────────────────────────────
# /api/event/{ticker}/h2h response cache
#
# H2H rows for a fixture rarely change — historical match results
# don't update mid-day, and the resolution chain (find_fl_game →
# search_past_event_for_teams → multi-search → team-results) is
# 5-6 sequential FL calls on cold cache. 5-min TTL eliminates the
# "sometimes shows, sometimes doesn't" pattern users hit when the
# sequential chain transiently fails on one of the round trips.
# Key: ticker (UPPER). Value: {payload, _ts}.
# ─────────────────────────────────────────────────────────────────
_H2H_CACHE: dict = {}
_H2H_CACHE_TTL = 300  # seconds
