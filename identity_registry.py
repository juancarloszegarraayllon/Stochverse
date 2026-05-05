"""Canonical entity registry — Phase A foundation.

Source-agnostic IDs for sports entities (teams, players, fixtures,
competitions, markets, outcomes) and a resolver/registration API
that external sources will plug into via Phase B+ source mappers
(FL seed, Kalshi, Polymarket, OddsAPI, ESPN, SofaScore, SportsDB).

Phase A scope (this module):
    1. Entity dataclasses
    2. Canonical ID format + slug helpers
    3. In-memory IdentityRegistry with idempotent registration
    4. Public resolver/registration API
    5. Per-source alias index (source, external_id) → canonical_id

Phase A explicitly excludes:
    * Source mappers (FL, Kalshi, Polymarket, etc.) — those are
      Phase B and beyond. Each one is a thin module that calls the
      registry's `register_*` and `resolve_alias` API.
    * Persistent storage (SQLite, Postgres). The registry stays
      in-memory for now; the public API is shaped so a DB backend
      can drop in later without touching callers.
    * 3-tier strict→alias→guarded-fuzzy resolution. That logic
      lives in the source mappers (Phase C+) and writes its results
      back into the registry's alias index, so request-time matching
      becomes O(1) lookups.

ID format:
    team:<sport>:<slug>
    player:<sport>:<slug>
    competition:<sport>:<slug>
    fixture:<sport>:<YYYY-MM-DD>:<home_slug>-vs-<away_slug>
    market_type:<sport>:<slug>             (parameterized=False)
    market_type:<sport>:<slug>             (parameterized=True; params live on Market)
    market:<fixture_id>:<market_type_slug>[:<param_hash>]
    outcome:<market_id>:<side>

Sport canonicalization: sport names use the full FL form
("Basketball", "Soccer", "Hockey") slugified to lowercase
("basketball", "soccer", "hockey"). Mappers translate from each
source's idiosyncratic form into this canonical set.

Source precedence policy (per field, not per source):
    * FL          — authoritative for fixture metadata
                    (start_time_utc, scores, lineups, status).
    * Kalshi      — authoritative for ITS OWN market metadata.
    * Polymarket  — authoritative for ITS OWN prices.
    * OddsAPI     — authoritative for ITS OWN prices.
    * ESPN/SofaScore/SportsDB — fall back providers for live state
                    fields; precedence handled in live_source_selector.

No source overrides another's prices — each source's prices are
attached to the canonical Outcome under that source's namespace.

Versioning: every Fixture has a `version` counter and an
`updated_at_utc`. Mappers bump the version when they observe a
real change (rescheduled kickoff, postponement, cancellation).
Downstream caches key off `(fixture_id, version)`.

Auditability: every Alias row stores method+confidence+observed_at,
so 'why did we pair these?' becomes a registry lookup, not a code
read.
"""
from __future__ import annotations
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from typing import Optional
import hashlib
import re


# ── Slugify ──────────────────────────────────────────────────────

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(s: str) -> str:
    """Lowercase, replace any run of non-alphanumeric chars with '-',
    strip leading/trailing dashes. Stable, deterministic.

      slugify('Atl. Madrid')        → 'atl-madrid'
      slugify('Los Angeles Lakers') → 'los-angeles-lakers'
      slugify('FC Bayern München')  → 'fc-bayern-m-nchen'
    """
    if not s:
        return ""
    return _SLUG_RE.sub("-", s.strip().lower()).strip("-")


# ── ID builders ──────────────────────────────────────────────────

def make_team_id(sport: str, slug: str) -> str:
    return f"team:{slugify(sport)}:{slug}"


def make_player_id(sport: str, slug: str) -> str:
    return f"player:{slugify(sport)}:{slug}"


def make_competition_id(sport: str, slug: str) -> str:
    return f"competition:{slugify(sport)}:{slug}"


def make_fixture_id(sport: str, when: date,
                    home_slug: str, away_slug: str) -> str:
    return (f"fixture:{slugify(sport)}:{when.isoformat()}:"
            f"{home_slug}-vs-{away_slug}")


def make_market_type_id(sport: str, slug: str) -> str:
    return f"market_type:{slugify(sport)}:{slug}"


def _params_hash(params: tuple) -> str:
    """Stable 8-char hash of parameter tuple for parameterized
    market IDs. Order-independent — sorted before hashing so
    {threshold: 2.5, line: 'home'} and {line: 'home', threshold: 2.5}
    produce the same ID."""
    if not params:
        return ""
    items = sorted((str(k), str(v)) for k, v in params)
    s = ";".join(f"{k}={v}" for k, v in items)
    return hashlib.md5(s.encode()).hexdigest()[:8]


def make_market_id(fixture_id: str, market_type_slug: str,
                   params: tuple = ()) -> str:
    base = f"market:{fixture_id.split(':', 1)[1]}:{market_type_slug}"
    h = _params_hash(params)
    return f"{base}:{h}" if h else base


def make_outcome_id(market_id: str, side: str) -> str:
    return f"outcome:{market_id}:{slugify(side)}"


# ── Entity dataclasses ───────────────────────────────────────────

@dataclass(frozen=True)
class Team:
    id: str
    sport: str
    canonical_name: str
    slug: str
    aliases: frozenset = field(default_factory=frozenset)


@dataclass(frozen=True)
class Player:
    id: str
    sport: str
    canonical_name: str
    slug: str
    aliases: frozenset = field(default_factory=frozenset)


@dataclass(frozen=True)
class Competition:
    id: str
    sport: str
    canonical_name: str
    slug: str
    aliases: frozenset = field(default_factory=frozenset)


@dataclass(frozen=True)
class Fixture:
    id: str
    sport: str
    competition_id: Optional[str]
    home_team_id: str
    away_team_id: str
    start_time_utc: int  # epoch seconds, UTC
    version: int = 1
    updated_at_utc: int = 0


@dataclass(frozen=True)
class MarketType:
    id: str
    sport: str
    canonical_name: str
    slug: str
    parameterized: bool = False
    aliases: frozenset = field(default_factory=frozenset)


@dataclass(frozen=True)
class Market:
    id: str
    fixture_id: str
    market_type_id: str
    params: tuple = ()  # tuple of (key, value) tuples


@dataclass(frozen=True)
class Outcome:
    id: str
    market_id: str
    side: str   # 'home', 'away', 'tie', 'yes', 'no', 'over', 'under',
                # 'player:<player_id>', etc.
    canonical_label: str


@dataclass(frozen=True)
class Alias:
    """Source-specific identifier mapped to a canonical entity ID.

    `method` records HOW the mapping was determined:
      'strict'        — exact equality match (e.g. abbr_block ==
                        SHORTNAME concat). 1.0 confidence.
      'alias_table'   — manually-curated alias map entry. 1.0 conf.
      'guarded_fuzzy' — name-token overlap with same-comp/same-date
                        guards (Phase C+ work). Confidence < 1.0.
      'manual'        — operator override. Highest priority — never
                        overwritten by automated processes.

    `observed_at_utc` is when this mapping was first written.
    """
    source: str
    external_id: str
    canonical_id: str
    method: str
    confidence: float
    observed_at_utc: int


# ── Registry ─────────────────────────────────────────────────────

class IdentityRegistry:
    """In-memory canonical entity registry.

    Idempotent registration: calling `register_team()` twice with the
    same (sport, slug) returns the SAME Team and merges any new
    aliases into the existing record. No duplicates. No race
    conditions in the single-threaded usage we're targeting; if
    multi-threading enters later, wrap mutations with a Lock.

    The public API is shaped so a SQL backend can replace the dict
    storage without touching callers — every method takes/returns
    plain dataclasses, no exposed dict-of-dicts.
    """

    def __init__(self):
        self._teams: dict[str, Team] = {}
        self._players: dict[str, Player] = {}
        self._competitions: dict[str, Competition] = {}
        self._fixtures: dict[str, Fixture] = {}
        self._market_types: dict[str, MarketType] = {}
        self._markets: dict[str, Market] = {}
        self._outcomes: dict[str, Outcome] = {}
        # Alias index: (source, external_id) → Alias.
        # external_id is the source's natural identifier — Kalshi
        # ticker, FL EVENT_ID, Polymarket condition ID, OddsAPI
        # event id, etc. — kept opaque here.
        self._aliases: dict[tuple[str, str], Alias] = {}

    # ── Team ─────────────────────────────────────────────────────

    def register_team(self, sport: str, canonical_name: str,
                      slug: Optional[str] = None,
                      aliases: Optional[set] = None) -> Team:
        s = slug or slugify(canonical_name)
        if not s:
            raise ValueError(
                f"Cannot register team without canonical_name or slug: "
                f"sport={sport!r}, canonical_name={canonical_name!r}"
            )
        tid = make_team_id(sport, s)
        existing = self._teams.get(tid)
        new_aliases = frozenset(aliases or set())
        if existing is not None:
            merged_aliases = existing.aliases | new_aliases
            if merged_aliases != existing.aliases:
                merged = replace(existing, aliases=merged_aliases)
                self._teams[tid] = merged
                return merged
            return existing
        team = Team(
            id=tid, sport=sport, canonical_name=canonical_name,
            slug=s, aliases=new_aliases,
        )
        self._teams[tid] = team
        return team

    def resolve_team(self, team_id: str) -> Optional[Team]:
        return self._teams.get(team_id)

    def lookup_team(self, sport: str, slug: str) -> Optional[Team]:
        return self._teams.get(make_team_id(sport, slug))

    # ── Player ───────────────────────────────────────────────────

    def register_player(self, sport: str, canonical_name: str,
                        slug: Optional[str] = None,
                        aliases: Optional[set] = None) -> Player:
        s = slug or slugify(canonical_name)
        if not s:
            raise ValueError(
                f"Cannot register player without canonical_name or slug: "
                f"sport={sport!r}, canonical_name={canonical_name!r}"
            )
        pid = make_player_id(sport, s)
        existing = self._players.get(pid)
        new_aliases = frozenset(aliases or set())
        if existing is not None:
            merged_aliases = existing.aliases | new_aliases
            if merged_aliases != existing.aliases:
                merged = replace(existing, aliases=merged_aliases)
                self._players[pid] = merged
                return merged
            return existing
        player = Player(
            id=pid, sport=sport, canonical_name=canonical_name,
            slug=s, aliases=new_aliases,
        )
        self._players[pid] = player
        return player

    def resolve_player(self, player_id: str) -> Optional[Player]:
        return self._players.get(player_id)

    def lookup_player(self, sport: str, slug: str) -> Optional[Player]:
        return self._players.get(make_player_id(sport, slug))

    # ── Competition ──────────────────────────────────────────────

    def register_competition(self, sport: str, canonical_name: str,
                             slug: Optional[str] = None,
                             aliases: Optional[set] = None
                             ) -> Competition:
        s = slug or slugify(canonical_name)
        if not s:
            raise ValueError(
                f"Cannot register competition without canonical_name or "
                f"slug: sport={sport!r}, canonical_name={canonical_name!r}"
            )
        cid = make_competition_id(sport, s)
        existing = self._competitions.get(cid)
        new_aliases = frozenset(aliases or set())
        if existing is not None:
            merged_aliases = existing.aliases | new_aliases
            if merged_aliases != existing.aliases:
                merged = replace(existing, aliases=merged_aliases)
                self._competitions[cid] = merged
                return merged
            return existing
        comp = Competition(
            id=cid, sport=sport, canonical_name=canonical_name,
            slug=s, aliases=new_aliases,
        )
        self._competitions[cid] = comp
        return comp

    def resolve_competition(self, competition_id: str
                             ) -> Optional[Competition]:
        return self._competitions.get(competition_id)

    # ── Fixture ──────────────────────────────────────────────────

    def register_fixture(self, sport: str, when: date,
                         home_team_id: str, away_team_id: str,
                         start_time_utc: int,
                         competition_id: Optional[str] = None
                         ) -> Fixture:
        # Build slug from registered team slugs, not arbitrary strings,
        # so the fixture ID composes deterministically from team IDs.
        home = self._teams.get(home_team_id)
        away = self._teams.get(away_team_id)
        if home is None or away is None:
            raise ValueError(
                f"Both teams must be registered before fixture: "
                f"home={home_team_id!r}, away={away_team_id!r}"
            )
        fid = make_fixture_id(sport, when, home.slug, away.slug)
        now = int(datetime.now(timezone.utc).timestamp())
        existing = self._fixtures.get(fid)
        if existing is not None:
            # Idempotent: bump version only if a real field changed.
            changed = (
                existing.start_time_utc != start_time_utc
                or existing.competition_id != competition_id
            )
            if changed:
                bumped = replace(
                    existing,
                    start_time_utc=start_time_utc,
                    competition_id=competition_id,
                    version=existing.version + 1,
                    updated_at_utc=now,
                )
                self._fixtures[fid] = bumped
                return bumped
            return existing
        fixture = Fixture(
            id=fid, sport=sport, competition_id=competition_id,
            home_team_id=home_team_id, away_team_id=away_team_id,
            start_time_utc=start_time_utc,
            version=1, updated_at_utc=now,
        )
        self._fixtures[fid] = fixture
        return fixture

    def resolve_fixture(self, fixture_id: str) -> Optional[Fixture]:
        return self._fixtures.get(fixture_id)

    # ── MarketType ───────────────────────────────────────────────

    def register_market_type(self, sport: str, canonical_name: str,
                             slug: Optional[str] = None,
                             parameterized: bool = False,
                             aliases: Optional[set] = None
                             ) -> MarketType:
        s = slug or slugify(canonical_name)
        if not s:
            raise ValueError(
                f"Cannot register market_type without name or slug: "
                f"sport={sport!r}, canonical_name={canonical_name!r}"
            )
        mtid = make_market_type_id(sport, s)
        existing = self._market_types.get(mtid)
        new_aliases = frozenset(aliases or set())
        if existing is not None:
            # parameterized flag must agree
            if existing.parameterized != parameterized:
                raise ValueError(
                    f"market_type {mtid!r} already registered with "
                    f"parameterized={existing.parameterized}; refusing "
                    f"to flip to {parameterized}."
                )
            merged_aliases = existing.aliases | new_aliases
            if merged_aliases != existing.aliases:
                merged = replace(existing, aliases=merged_aliases)
                self._market_types[mtid] = merged
                return merged
            return existing
        mt = MarketType(
            id=mtid, sport=sport, canonical_name=canonical_name,
            slug=s, parameterized=parameterized, aliases=new_aliases,
        )
        self._market_types[mtid] = mt
        return mt

    def resolve_market_type(self, market_type_id: str
                             ) -> Optional[MarketType]:
        return self._market_types.get(market_type_id)

    # ── Market ───────────────────────────────────────────────────

    def register_market(self, fixture_id: str, market_type_id: str,
                        params: Optional[tuple] = None) -> Market:
        fixture = self._fixtures.get(fixture_id)
        mt = self._market_types.get(market_type_id)
        if fixture is None:
            raise ValueError(f"Unknown fixture: {fixture_id!r}")
        if mt is None:
            raise ValueError(f"Unknown market_type: {market_type_id!r}")
        params_t = tuple(params or ())
        # Sanity: parameterized market types require params; non-
        # parameterized must NOT carry params.
        if mt.parameterized and not params_t:
            raise ValueError(
                f"market_type {market_type_id!r} is parameterized; "
                f"params must be non-empty."
            )
        if not mt.parameterized and params_t:
            raise ValueError(
                f"market_type {market_type_id!r} is NOT parameterized; "
                f"params must be empty (got {params_t!r})."
            )
        mid = make_market_id(fixture_id, mt.slug, params_t)
        existing = self._markets.get(mid)
        if existing is not None:
            return existing
        market = Market(
            id=mid, fixture_id=fixture_id,
            market_type_id=market_type_id, params=params_t,
        )
        self._markets[mid] = market
        return market

    def resolve_market(self, market_id: str) -> Optional[Market]:
        return self._markets.get(market_id)

    # ── Outcome ──────────────────────────────────────────────────

    def register_outcome(self, market_id: str, side: str,
                          canonical_label: str) -> Outcome:
        if market_id not in self._markets:
            raise ValueError(f"Unknown market: {market_id!r}")
        oid = make_outcome_id(market_id, side)
        existing = self._outcomes.get(oid)
        if existing is not None:
            return existing
        outcome = Outcome(
            id=oid, market_id=market_id,
            side=side, canonical_label=canonical_label,
        )
        self._outcomes[oid] = outcome
        return outcome

    def resolve_outcome(self, outcome_id: str) -> Optional[Outcome]:
        return self._outcomes.get(outcome_id)

    # ── Alias index ──────────────────────────────────────────────

    def register_alias(self, source: str, external_id: str,
                       canonical_id: str, method: str,
                       confidence: float = 1.0,
                       observed_at_utc: Optional[int] = None) -> Alias:
        """Idempotent alias registration. If an alias for (source,
        external_id) already exists, the call returns it unchanged
        — UNLESS the new method has a higher precedence (manual >
        strict > alias_table > guarded_fuzzy), in which case the
        new alias wins.

        This protects manual operator overrides from being silently
        replaced by automated processes — a key requirement for
        production debuggability.
        """
        if not source or not external_id or not canonical_id:
            raise ValueError(
                f"All of source, external_id, canonical_id required: "
                f"source={source!r}, external_id={external_id!r}, "
                f"canonical_id={canonical_id!r}"
            )
        if not 0.0 <= confidence <= 1.0:
            raise ValueError(
                f"confidence must be in [0.0, 1.0]; got {confidence!r}"
            )
        if observed_at_utc is None:
            observed_at_utc = int(
                datetime.now(timezone.utc).timestamp()
            )
        key = (source, external_id)
        existing = self._aliases.get(key)
        if existing is not None:
            if _method_priority(method) <= _method_priority(existing.method):
                return existing
        alias = Alias(
            source=source, external_id=external_id,
            canonical_id=canonical_id, method=method,
            confidence=confidence, observed_at_utc=observed_at_utc,
        )
        self._aliases[key] = alias
        return alias

    def resolve_alias(self, source: str,
                       external_id: str) -> Optional[Alias]:
        return self._aliases.get((source, external_id))

    def resolve_through_alias(self, source: str, external_id: str):
        """Convenience: resolve external (source, external_id) all
        the way to the canonical entity. Returns None if no alias,
        or the resolved entity (Team / Player / Fixture / Market /
        Outcome / Competition / MarketType) if found.

        This is what request-time pairing will call once the registry
        is populated — O(1) dict lookup, no fuzzy logic.
        """
        alias = self.resolve_alias(source, external_id)
        if alias is None:
            return None
        cid = alias.canonical_id
        if cid.startswith("team:"):
            return self._teams.get(cid)
        if cid.startswith("player:"):
            return self._players.get(cid)
        if cid.startswith("competition:"):
            return self._competitions.get(cid)
        if cid.startswith("fixture:"):
            return self._fixtures.get(cid)
        if cid.startswith("market_type:"):
            return self._market_types.get(cid)
        if cid.startswith("market:"):
            return self._markets.get(cid)
        if cid.startswith("outcome:"):
            return self._outcomes.get(cid)
        return None

    # ── Stats / introspection ────────────────────────────────────

    def stats(self) -> dict:
        """Counts of each entity type. Useful for diff-endpoint
        observability and for asserting seed completeness."""
        return {
            "teams":         len(self._teams),
            "players":       len(self._players),
            "competitions":  len(self._competitions),
            "fixtures":      len(self._fixtures),
            "market_types":  len(self._market_types),
            "markets":       len(self._markets),
            "outcomes":      len(self._outcomes),
            "aliases":       len(self._aliases),
        }


# ── Method precedence ────────────────────────────────────────────

_METHOD_PRIORITY = {
    "manual":        4,  # operator override — never replaced
    "strict":        3,
    "alias_table":   2,
    "guarded_fuzzy": 1,
}


def _method_priority(method: str) -> int:
    return _METHOD_PRIORITY.get(method, 0)


# ── Module-level singleton (optional, for convenience) ───────────
# Callers can instantiate their own IdentityRegistry for tests.
# Production code uses _global_registry().

_GLOBAL: Optional[IdentityRegistry] = None


def global_registry() -> IdentityRegistry:
    """Process-wide registry. Lazy-initialized on first access."""
    global _GLOBAL
    if _GLOBAL is None:
        _GLOBAL = IdentityRegistry()
    return _GLOBAL


def reset_global_registry() -> None:
    """Clear the process-wide registry. Tests use this to isolate."""
    global _GLOBAL
    _GLOBAL = None
