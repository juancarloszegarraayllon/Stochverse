"""Kalshi → IdentityRegistry seeder — Phase C + C2.

Resolves Kalshi cache records through the canonical registry and
writes their fixture-level mappings into the alias index.

Three-tier match strategy at seed time:
    1. Strict abbr-equality — the deterministic path. Kalshi's
       parsed abbr_block must equal one of the FL fixture's
       team-pair concatenations (built from the team aliases in
       the registry).
    2. Alias-table — if the strict pass misses, expand each FL
       team's aliases through `normalize_fl_abbr` (Basketball:
       LAK↔LAL, OKL↔OKC, etc. — same map that Phase 5 punch-list
       seeded for the LAL@OKC pairing fix). Retry the equality
       check against the expanded form.
    3. Guarded fuzzy (Phase C2) — final fallback when (1) and (2)
       both miss. Fires ONLY when:
           a. the FL fixture and the Kalshi record are for the
              same sport and exact same date (no time-fuzz),
           b. the FL fixture has no other Kalshi alias yet,
           c. the bucket — all unpaired FL fixtures for (sport,
              date) intersected with all unpaired Kalshi records
              for (sport, date) — contains exactly ONE FL
              fixture and ONE Kalshi record.

       The 1+1-on-each-side guard is what prevents v1's
       wrong-fixture pairings: if there are two unpaired Atletico
       games and two unpaired Atletico-shaped Kalshi records on
       the same day, we don't gamble — leave them unpaired and
       let an alias-map entry resolve them next deploy.
       Confidence: 0.7 (substantially below strict/alias_table).

On a successful match, two aliases get written:
    source='kalshi', external_id=event_ticker  → fixture canonical id
    method='strict' or 'alias_table' depending on which tier hit.
    confidence=1.0 (strict) or 0.95 (alias_table — high but
    flags that the match required an alias rewrite).

Subsequent request-time pairing collapses to a single
`registry.resolve_through_alias('kalshi', ticker)` — O(1) dict
lookup, no fuzzy logic.

Phase C scope (this module):
    1. seed_kalshi_record  — single record → Fixture or None
    2. seed_kalshi_records — batch walk with stats

Phase C explicitly does NOT:
    * Migrate v2's request-time path. v2 still uses the
      compute_fl_identity / kalshi_join chain. That migration is
      Phase C+1 once we can prove the registry-based seeder hits
      the same pairings.
    * Seed market or outcome layers. The seeder writes a fixture-
      level alias only. Per-market and per-outcome alias seeding
      is Phase C2.
    * Implement guarded fuzzy. Phase C2.
"""
from __future__ import annotations
import re
from datetime import datetime, timezone
from typing import Optional

from identity_registry import IdentityRegistry, Fixture, Market
from kalshi_identity import (
    parse_ticker,
    normalize_fl_abbr,
    strip_known_suffix,
    parent_fixture_identity,
)


# Time-fuzz window for matching Kalshi G7 tickers (date+time+abbr)
# to FL fixtures. Mirrors v2's kalshi_join.match() default. Phase
# C2e — applied as a TIEBREAKER within an already-filtered candidate
# set, not as a hard filter. Records without time in their identity
# fall through this gate unchanged.
_TIME_FUZZ_MINUTES = 30


def _identity_time_to_minutes(identity_time: str) -> Optional[int]:
    """Convert an identity.time string ('HHMM') to minutes since
    midnight. Returns None on bad input — caller treats as 'no
    time component, fall through'."""
    if not identity_time or len(identity_time) < 4:
        return None
    try:
        return int(identity_time[:2]) * 60 + int(identity_time[2:4])
    except (TypeError, ValueError):
        return None


def _fixture_utc_minutes(fixture: Fixture) -> Optional[int]:
    """UTC minutes-since-midnight for the fixture's start_time.
    Returns None if start_time_utc is missing or malformed."""
    ts = fixture.start_time_utc
    if not ts:
        return None
    try:
        dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
        return dt.hour * 60 + dt.minute
    except (TypeError, ValueError, OSError):
        return None


def _pick_best_by_time(matching: list, identity,
                       fuzz_min: int = _TIME_FUZZ_MINUTES) -> Optional[Fixture]:
    """Phase C2e — among Fixtures already known to match the Kalshi
    identity by date+abbr_block, pick the one whose start_time_utc
    is closest to the identity's time component.

    Logic:
      * If the identity has no time (G1 tickers like
        KXNBAGAME-26MAY05CLEDET), any candidate is acceptable —
        return the first. Behavior matches the pre-C2e seeder.
      * If the identity has time, filter candidates to those within
        ±fuzz_min minutes of identity time, then return the closest.
      * If no candidate falls inside the fuzz window, return None
        (caller treats as 'no match', falls through to next tier).

    Edge cases:
      * Empty `matching` list → None.
      * Candidate with missing start_time_utc → skipped silently
        (excluded from time scoring; if it was the only candidate,
        falls through to first-match behavior).
    """
    if not matching:
        return None
    k_min = _identity_time_to_minutes(identity.time)
    if k_min is None:
        # No time on the Kalshi side → first abbr-match wins, same
        # as pre-C2e behavior.
        return matching[0]

    best = None
    best_diff = fuzz_min + 1  # strictly less-than below
    for fx in matching:
        f_min = _fixture_utc_minutes(fx)
        if f_min is None:
            continue
        diff = abs(k_min - f_min)
        # Handle wrap across UTC midnight: if diff > 12h, the
        # closer interpretation is via 24h wrap.
        if diff > 12 * 60:
            diff = 24 * 60 - diff
        if diff <= fuzz_min and diff < best_diff:
            best = fx
            best_diff = diff
    return best


# ── Tie-word detection ───────────────────────────────────────────

_TIE_WORDS = frozenset({"tie", "draw", "no winner", "no result"})


# ── Per-sport per_leg market-type taxonomy ───────────────────────
# Canonical name + slug for the parameterized sub-market a per_leg
# Kalshi ticker resolves to. Keyed by sport. Unmapped sports get
# `None` and the per_leg path early-returns (we still register the
# fixture-level alias).
#
# Adding a sport:
#   * Map the FlashLive sport label to (canonical_name, slug).
#   * Each Kalshi outcome label still gets passed through
#     _classify_outcome_side, so the sport's home/away vocabulary
#     must overlap with FL's HOME_NAME / AWAY_NAME tokens.
_PER_LEG_MARKET_TYPES: dict = {
    "Tennis":  ("Set Winner", "set-winner"),
    "Esports": ("Map Winner", "map-winner"),
}


# ── Market-type detection ────────────────────────────────────────

def _is_winner_market(series_ticker: str) -> bool:
    """True if `series_ticker` ends in a known headline-fixture
    suffix (GAME, MATCH) — i.e., this Kalshi record IS the 2-way/3-way
    Winner market for the fixture, not a sub-market like Spread or
    Total.

    Same heuristic used by main._v2_pick_primary (Phase 5 punch-list
    fix for the empty WINNER-tab bug). Centralizing it here lets the
    market-layer seeder identify which records to canonicalize.
    """
    series = (series_ticker or "").upper()
    _, suffix = strip_known_suffix(series)
    return suffix in ("GAME", "MATCH")


# ── Title-based pairing tier (Phase C2f) ─────────────────────────
# Universal pairing route. Kalshi `title` field carries the full team
# names (e.g. "Bayern Munich vs PSG", "Always Ready vs Lanus"). FL
# ships HOME_NAME / AWAY_NAME with the same long forms. Token-overlap
# match between them reaches every pair Kalshi names in the title —
# without requiring an alias_table entry per team.
#
# Inserted between alias_table (tier 2) and guarded_fuzzy (tier 3).
# Confidence 0.85 — below alias_table (0.95) since title parsing is
# format-fragile, but above guarded_fuzzy (0.7) since both teams must
# show overlapping tokens (no blind 1+1 inference).

_KALSHI_TITLE_PATTERNS = (
    # "TeamA vs TeamB" or "TeamA vs TeamB: <suffix>"
    re.compile(r"^(.+?)\s+vs\s+(.+?)(?:\s*:.*)?$", re.IGNORECASE),
    # "TeamA at TeamB" or "TeamA at TeamB: <suffix>"
    re.compile(r"^(.+?)\s+at\s+(.+?)(?:\s*:.*)?$", re.IGNORECASE),
    # "Will TeamA beat TeamB?"
    re.compile(r"^Will\s+(.+?)\s+beat\s+(.+?)\??$", re.IGNORECASE),
    # "TeamA - TeamB" (em-dash variants — uncommon but seen)
    re.compile(r"^(.+?)\s+[-–—]\s+(.+?)(?:\s*:.*)?$"),
)


# Time-gate window for title-match. FL ships authoritative kickoff
# times; Kalshi's `_kickoff_dt` is either ESPN/SofaScore-authoritative
# (when matched upstream) or estimated via `expected_expiration_time
# − sport_duration` (precise to ~30 min). A ±30 min gate is tight
# enough to reject "same-name different-team" false positives like
# Nacional (Uru) vs Nacional-AM playing on the same date but at
# different kickoffs, while still admitting legitimate matches.
_TITLE_MATCH_TIME_WINDOW_SEC = 30 * 60


def _parse_iso_to_epoch(iso_str: str) -> Optional[int]:
    """Parse an ISO 8601 datetime string to UTC epoch seconds.

    Accepts common Kalshi shapes:
      '2026-05-07T05:00:00Z'         (Z suffix)
      '2026-05-07T05:00:00+00:00'    (explicit offset)
      '2026-05-07T05:00:00'          (naive — assumed UTC)
    Returns None on failure.
    """
    if not iso_str:
        return None
    try:
        s = str(iso_str).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except (ValueError, TypeError):
        return None


# Normalization for cross-source team-name matching. Kalshi titles use
# short/common forms ("Tolima", "U. Catolica", "Nacional"); FL ships
# qualified forms ("Deportes Tolima (Col)", "U. Catolica (Chi)",
# "Nacional (Uru)"). A static alias_table per team isn't sustainable
# (hundreds of teams, dozens of leagues). Stripping suffixes / expanding
# abbreviations BEFORE token comparison lets the same Jaccard scoring
# reach all of them with no per-team config.

_COUNTRY_SUFFIX_RE = re.compile(r"\s*\(\s*[A-Za-z]{2,4}\s*\)\s*$")

# Order matters — match longer or anchored patterns first so 'St.'
# doesn't get partially expanded inside another word.
_TEAM_ABBR_EXPANSIONS = (
    (re.compile(r"\bAtl\.\s*", re.I), "Atletico "),
    (re.compile(r"\bSt\.\s*",  re.I), "Saint "),
    (re.compile(r"\bU\.\s*",   re.I), "Universidad "),
    (re.compile(r"\bDep\.\s*", re.I), "Deportivo "),
)

# Anchored to the START of the name only — generic brand modifiers
# like 'Deportes' / 'FC' / 'CD' precede the actual identifier in
# Spanish/Portuguese/etc. naming conventions. Stripping mid-string
# would break legitimate names. NOT included: 'Real' (canonical to
# Real Madrid / Sociedad / Betis — stripping loses identity).
_TEAM_GENERIC_PREFIXES_RE = re.compile(
    r"^(?:Deportes|Deportivo|Club|CD|FC|AC|SC|CF|AS|SK)\s+",
    re.IGNORECASE,
)


def _normalize_team_name(name: str) -> str:
    """Normalize a team name for cross-source matching.

    Pipeline:
      1. Strip country suffix in parens: 'Tolima (Col)' → 'Tolima'
      2. Expand common abbreviations: 'U. Catolica' → 'Universidad
         Catolica'; 'Atl. Madrid' → 'Atletico Madrid'
      3. Strip generic prefix: 'Deportes Tolima' → 'Tolima';
         'FC Köln' → 'Köln'
      4. Lowercase + trim

    Idempotent: normalize(normalize(x)) == normalize(x).
    """
    if not name:
        return ""
    s = _COUNTRY_SUFFIX_RE.sub("", name).strip()
    for pat, replacement in _TEAM_ABBR_EXPANSIONS:
        s = pat.sub(replacement, s)
    s = _TEAM_GENERIC_PREFIXES_RE.sub("", s).strip()
    return s.lower()


def _parse_kalshi_title(title: str) -> Optional[tuple]:
    """Extract (team_a, team_b) from a Kalshi title.

    Tries the common Kalshi title shapes in order. Returns None if
    no pattern matches.
    """
    if not title:
        return None
    title = title.strip()
    for pat in _KALSHI_TITLE_PATTERNS:
        m = pat.match(title)
        if m:
            a = m.group(1).strip()
            b = m.group(2).strip()
            if a and b:
                return (a, b)
    return None


def _title_name_tokens(s: str) -> set:
    """Lowercase ≥3-char alphanum tokens from a NORMALIZED team-name
    string. Normalization (suffix-strip, abbr-expand, prefix-strip)
    runs first so equivalent forms produce overlapping token sets.
    """
    if not s:
        return set()
    norm = _normalize_team_name(s)
    return {t for t in re.split(r"[^a-z0-9]+", norm) if len(t) >= 3}


def _title_overlap_score(fl_name: str, kalshi_name: str) -> float:
    """Token-set Jaccard between an FL HOME_NAME and a Kalshi
    title-side name: |A ∩ B| / |A ∪ B|. Returns 0..1.

    Both inputs are normalized inside `_title_name_tokens` before
    comparison, so 'Tolima' and 'Deportes Tolima (Col)' both produce
    {tolima} → score 1.0.

    Standard Jaccard (union denominator) penalizes pairs where one
    side has extra tokens. Critical for distinguishing same-prefix
    teams like 'Real Madrid' vs 'Real Sociedad' (intersection {real},
    union {real, madrid, sociedad} → 1/3 = 0.33, below threshold).
    """
    a = _title_name_tokens(fl_name)
    b = _title_name_tokens(kalshi_name)
    if not a or not b:
        return 0.0
    inter = a & b
    if not inter:
        return 0.0
    return len(inter) / len(a | b)


def _pair_via_title(registry: IdentityRegistry,
                     fixture_candidates: list,
                     kalshi_record: dict,
                     min_score: float = 0.5) -> Optional[Fixture]:
    """Pair a Kalshi record to one of the FL fixture candidates using
    BOTH name overlap AND kickoff-time proximity.

    Signals:
      1. Name overlap — token-set Jaccard against FL home/away
         canonical names, after normalization (suffix-strip,
         abbr-expand, prefix-strip). Both sides must contribute
         non-zero overlap, so single-team-name coincidences (e.g.
         'Real Madrid' vs 'Real Sociedad' sharing 'Real') can't
         satisfy the match unless the OTHER pair also overlaps.
      2. Time gate — when both sides have a kickoff timestamp, the
         FL fixture's `start_time_utc` and the Kalshi record's
         `_kickoff_dt` must be within ±30 min. Rejects the
         "same-name different-team different-kickoff" class —
         e.g. Nacional (Uru) vs Nacional-AM (Brazil) playing on
         the same date but at different kickoffs.

    The time gate is bypassed (name-only) only when one side is
    missing a kickoff — which is rare for both signals to be
    absent. Direction-blind: tries home/away in both orientations
    since Kalshi's 'X at Y' notation puts the away team first.
    """
    # ── TEMPORARY DEBUG (filtered to specific tickers only) ──
    _ticker_dbg = kalshi_record.get("event_ticker", "?")
    _debug_this = _ticker_dbg in (
        "KXCONMEBOLLIBGAME-26MAY06TOLNAC",
        "KXCONMEBOLLIBGAME-26MAY06UCCRU",
    )

    title = (kalshi_record.get("title") or "").strip()
    parsed = _parse_kalshi_title(title)
    if parsed is None:
        if _debug_this:
            print(
                f"DEBUG title_match[{_ticker_dbg}] "
                f"title_parse_FAILED title={title!r}",
                flush=True,
            )
        return None
    title_a, title_b = parsed

    # Time gate setup. _kickoff_dt may be set by the cache builder
    # via ESPN/SofaScore (authoritative) or via DURATION estimate.
    # Either is good enough for a ±30 min gate.
    kalshi_kickoff_ts = _parse_iso_to_epoch(kalshi_record.get("_kickoff_dt"))

    _kdt_raw = kalshi_record.get("_kickoff_dt")
    if _debug_this:
        print(
            f"DEBUG title_match[{_ticker_dbg}] entry "
            f"title={title!r} parsed=({title_a!r}, {title_b!r}) "
            f"_kickoff_dt={_kdt_raw!r} kalshi_kickoff_ts={kalshi_kickoff_ts!r} "
            f"candidates_count={len(fixture_candidates)}",
            flush=True,
        )
        # Diagnostic: which candidates fall within ±2hr of the Kalshi
        # kickoff? Reveals whether the time gate is too tight or
        # whether the expected FL fixture isn't in the pool at all.
        if kalshi_kickoff_ts is not None:
            near = []
            for fx in fixture_candidates:
                if not fx.start_time_utc:
                    continue
                diff = abs(fx.start_time_utc - kalshi_kickoff_ts)
                if diff < 2 * 3600:
                    h = registry.resolve_team(fx.home_team_id)
                    a = registry.resolve_team(fx.away_team_id)
                    near.append({
                        "fl_start": fx.start_time_utc,
                        "diff_s":   diff,
                        "home":     h.canonical_name if h else "?",
                        "away":     a.canonical_name if a else "?",
                    })
            print(
                f"DEBUG title_match[{_ticker_dbg}] near-time (±2hr): "
                f"{near}",
                flush=True,
            )
        # Diagnostic: does ANY candidate (regardless of time) have a
        # canonical team name that name-matches the parsed Kalshi
        # title sides? If yes but they're outside the time window,
        # it's a time-gate issue. If no, the team isn't in the pool
        # at all — upstream issue (FL didn't ship it, or local_date
        # bucketed it elsewhere).
        title_a_n = _normalize_team_name(title_a)
        title_b_n = _normalize_team_name(title_b)
        by_name = []
        for fx in fixture_candidates:
            h = registry.resolve_team(fx.home_team_id)
            a = registry.resolve_team(fx.away_team_id)
            h_n = _normalize_team_name(h.canonical_name) if h else ""
            a_n = _normalize_team_name(a.canonical_name) if a else ""
            # Substring check on normalized names — looser than
            # token-Jaccard, just for diagnosis.
            if (title_a_n in h_n or title_a_n in a_n
                or title_b_n in h_n or title_b_n in a_n
                or h_n in title_a_n or a_n in title_a_n):
                by_name.append({
                    "fl_start": fx.start_time_utc,
                    "home":     h.canonical_name if h else "?",
                    "away":     a.canonical_name if a else "?",
                })
        print(
            f"DEBUG title_match[{_ticker_dbg}] by-name (any time): "
            f"{by_name}",
            flush=True,
        )

    best_fx: Optional[Fixture] = None
    best_score = min_score
    for fx in fixture_candidates:
        # Time gate — only when BOTH sides have a kickoff. When
        # missing on either side, fall through to name-only (the
        # name guards above are still strict enough for most cases).
        time_diff = None
        gate_rejected = False
        if kalshi_kickoff_ts is not None and fx.start_time_utc:
            time_diff = abs(fx.start_time_utc - kalshi_kickoff_ts)
            if time_diff > _TITLE_MATCH_TIME_WINDOW_SEC:
                gate_rejected = True
        home_team = registry.resolve_team(fx.home_team_id)
        away_team = registry.resolve_team(fx.away_team_id)
        _h_name = home_team.canonical_name if home_team else "?"
        _a_name = away_team.canonical_name if away_team else "?"
        if gate_rejected:
            if _debug_this:
                print(
                    f"DEBUG title_match[{_ticker_dbg}]   cand "
                    f"FL={_h_name!r} vs {_a_name!r} "
                    f"fl_start={fx.start_time_utc} time_diff={time_diff}s "
                    f"-> REJECT (outside ±30 min gate)",
                    flush=True,
                )
            continue
        if home_team is None or away_team is None:
            if _debug_this:
                print(
                    f"DEBUG title_match[{_ticker_dbg}]   cand "
                    f"home/away team unresolved -> SKIP",
                    flush=True,
                )
            continue
        # Orientation 1: title_a → home, title_b → away
        h1 = _title_overlap_score(home_team.canonical_name, title_a)
        a1 = _title_overlap_score(away_team.canonical_name, title_b)
        # Orientation 2: title_a → away, title_b → home
        h2 = _title_overlap_score(home_team.canonical_name, title_b)
        a2 = _title_overlap_score(away_team.canonical_name, title_a)
        if h1 > 0 and a1 > 0:
            s1 = (h1 + a1) / 2.0
        else:
            s1 = 0.0
        if h2 > 0 and a2 > 0:
            s2 = (h2 + a2) / 2.0
        else:
            s2 = 0.0
        score = max(s1, s2)
        if _debug_this:
            print(
                f"DEBUG title_match[{_ticker_dbg}]   cand "
                f"FL={_h_name!r} vs {_a_name!r} "
                f"fl_start={fx.start_time_utc} time_diff={time_diff}s "
                f"h1={h1:.2f} a1={a1:.2f} h2={h2:.2f} a2={a2:.2f} "
                f"score={score:.3f} (threshold>{min_score})",
                flush=True,
            )
        if score > best_score:
            best_score = score
            best_fx = fx
    if _debug_this:
        print(
            f"DEBUG title_match[{_ticker_dbg}] FINAL "
            f"best_fx={best_fx.id if best_fx else None!r} "
            f"best_score={best_score:.3f}",
            flush=True,
        )
    return best_fx


# ── Outcome side classification ──────────────────────────────────

def _classify_outcome_side(label: str,
                            registry: IdentityRegistry,
                            fixture: Fixture) -> Optional[str]:
    """Map a Kalshi outcome label to a canonical side.

    Returns 'home' / 'away' / 'tie', or None if the label can't be
    confidently classified.

    Strategy: token-overlap against the home and away teams' canonical
    names AND aliases. The team with the most token overlap wins the
    label. Tie words ('tie', 'draw', etc.) short-circuit to 'tie'.
    A label that overlaps zero tokens with both teams returns None.
    """
    if not label:
        return None
    label_lc = label.strip().lower()
    if label_lc in _TIE_WORDS:
        return "tie"

    home = registry.resolve_team(fixture.home_team_id)
    away = registry.resolve_team(fixture.away_team_id)
    if home is None or away is None:
        return None

    def _tokens(s: str) -> set:
        # Same min-length=3 token rule as main._extract_winner_prices —
        # so 'NY' / 'LA' (which collide with common English words)
        # don't count as evidence by themselves.
        return {t for t in
                "".join(c if c.isalnum() else " "
                        for c in s.lower()).split()
                if len(t) >= 3}

    home_tokens = _tokens(home.canonical_name)
    away_tokens = _tokens(away.canonical_name)
    for a in home.aliases:
        home_tokens |= _tokens(a)
    for a in away.aliases:
        away_tokens |= _tokens(a)

    label_tokens = _tokens(label_lc)
    home_overlap = len(home_tokens & label_tokens)
    away_overlap = len(away_tokens & label_tokens)
    if home_overlap == 0 and away_overlap == 0:
        return None
    if home_overlap > away_overlap:
        return "home"
    if away_overlap > home_overlap:
        return "away"
    # Tie in overlap — refuse to guess.
    return None


# ── Market-layer seeding (Phase C2b — Winner markets only) ───────

def _seed_winner_market_layer(registry: IdentityRegistry,
                               fixture: Fixture,
                               kalshi_record: dict,
                               sport: str) -> Optional[Market]:
    """Register MarketType / Market / Outcomes for a Kalshi Winner
    record and write Kalshi aliases on each.

    Phase C2b scope: ONLY Winner markets (series_ticker ends in
    GAME or MATCH). Parameterized sub-markets (Spread, Total,
    Over/Under, etc.) are deferred to Phase C2c — they need title
    parsing to extract thresholds, which is parser-heavy work that
    deserves its own pass.

    Aliases written under namespaced sources so the fixture-level
    alias (source='kalshi') doesn't collide with the market-level
    alias for the same event_ticker:
        source='kalshi_market',  external_id=event_ticker
                              → market.id  (strict, 1.0)
        source='kalshi_outcome', external_id=<per-outcome ticker>
                              → outcome.id (strict, 1.0)

    Returns the Market on success, None if the record isn't a Winner
    or any required field is missing.
    """
    series = (kalshi_record.get("series_ticker") or "").upper().strip()
    ticker = (kalshi_record.get("event_ticker") or "").upper().strip()
    if not series or not ticker:
        return None
    if not _is_winner_market(series):
        return None

    # Idempotent registrations.
    mt = registry.register_market_type(
        sport=sport, canonical_name="Winner", slug="winner",
        parameterized=False,
    )
    market = registry.register_market(
        fixture_id=fixture.id, market_type_id=mt.id,
    )
    registry.register_alias(
        source="kalshi_market", external_id=ticker,
        canonical_id=market.id, method="strict", confidence=1.0,
    )

    # Outcome layer
    outcomes = (kalshi_record.get("outcomes")
                or kalshi_record.get("_outcomes")
                or [])
    for o in outcomes:
        if not isinstance(o, dict):
            continue
        label = (o.get("label") or "").strip()
        if not label:
            continue
        side = _classify_outcome_side(label, registry, fixture)
        if side is None:
            # Unrecognized outcome — skip silently. This catches
            # malformed Kalshi data without blowing up the whole
            # batch seed.
            continue
        outcome = registry.register_outcome(
            market_id=market.id, side=side,
            canonical_label=label,
        )
        # Kalshi ships a per-outcome ticker (`ticker` field) that
        # the frontend WebSocket subscribes to. Register it as a
        # `kalshi_outcome` alias so post-Phase-D downstream consumers
        # can resolve outcome.id → live tick stream.
        outcome_ticker = (o.get("ticker") or "").strip()
        if outcome_ticker:
            registry.register_alias(
                source="kalshi_outcome",
                external_id=outcome_ticker,
                canonical_id=outcome.id, method="strict",
                confidence=1.0,
            )
    return market


# ── Per_leg market-layer seeding (Phase C2c) ─────────────────────

def _seed_per_leg_market_layer(registry: IdentityRegistry,
                                parent_fixture: Fixture,
                                kalshi_record: dict,
                                sport: str,
                                leg_n: int) -> Optional[Market]:
    """Register a parameterized sub-market (e.g. tennis Set Winner,
    esports Map Winner) for a per_leg Kalshi record, anchored to
    its parent fixture.

    Each per_leg ticker (one per set / per map) creates ONE Market
    under the parent fixture, with `params=(("leg_n", N),)` so
    different legs of the same match map to distinct canonical
    Markets. Outcomes are home/away (no tie — sets and maps decide
    a winner).

    Aliases written under the same namespaced sources as the Winner
    market layer:
        source='kalshi_market',  external_id=event_ticker
                              → market.id  (strict, 1.0)
        source='kalshi_outcome', external_id=<per-outcome ticker>
                              → outcome.id (strict, 1.0)

    Returns the Market on success, None if the sport has no per_leg
    market-type mapping in _PER_LEG_MARKET_TYPES or required fields
    are missing.
    """
    series = (kalshi_record.get("series_ticker") or "").upper().strip()
    ticker = (kalshi_record.get("event_ticker") or "").upper().strip()
    if not series or not ticker:
        return None
    mt_info = _PER_LEG_MARKET_TYPES.get(sport)
    if mt_info is None:
        return None
    mt_name, mt_slug = mt_info

    mt = registry.register_market_type(
        sport=sport, canonical_name=mt_name, slug=mt_slug,
        parameterized=True,
    )
    market = registry.register_market(
        fixture_id=parent_fixture.id,
        market_type_id=mt.id,
        params=(("leg_n", leg_n),),
    )
    registry.register_alias(
        source="kalshi_market", external_id=ticker,
        canonical_id=market.id, method="strict", confidence=1.0,
    )

    outcomes = (kalshi_record.get("outcomes")
                or kalshi_record.get("_outcomes")
                or [])
    for o in outcomes:
        if not isinstance(o, dict):
            continue
        label = (o.get("label") or "").strip()
        if not label:
            continue
        side = _classify_outcome_side(label, registry, parent_fixture)
        # Sets / maps have no tie — if the label happens to match a
        # tie-word, skip rather than register a meaningless outcome.
        if side is None or side == "tie":
            continue
        outcome = registry.register_outcome(
            market_id=market.id, side=side,
            canonical_label=label,
        )
        outcome_ticker = (o.get("ticker") or "").strip()
        if outcome_ticker:
            registry.register_alias(
                source="kalshi_outcome",
                external_id=outcome_ticker,
                canonical_id=outcome.id,
                method="strict", confidence=1.0,
            )
    return market


# ── Orientation builders ─────────────────────────────────────────

def _team_alias_set(registry: IdentityRegistry,
                     team_id: str) -> frozenset:
    """All known short-form aliases for the team, as a frozenset.
    Returns empty if team isn't registered or has no aliases.
    """
    team = registry.resolve_team(team_id)
    if team is None:
        return frozenset()
    return team.aliases


def _orientations_strict(registry: IdentityRegistry,
                          fixture: Fixture) -> set:
    """Cross-product (home_alias × away_alias) in BOTH orientations.

    Returns the set of concatenated strings to compare against a
    Kalshi abbr_block. No alias-table expansion at this tier.
    """
    home_aliases = _team_alias_set(registry, fixture.home_team_id)
    away_aliases = _team_alias_set(registry, fixture.away_team_id)
    out = set()
    for h in home_aliases:
        for a in away_aliases:
            out.add(h + a)
            out.add(a + h)
    return out


def _orientations_with_alias_table(registry: IdentityRegistry,
                                    fixture: Fixture,
                                    sport: str) -> set:
    """Like _orientations_strict, but each home/away alias is
    expanded through normalize_fl_abbr first. Picks up FL/Kalshi
    abbreviation divergence (LAK↔LAL, etc.).
    """
    home_aliases = _team_alias_set(registry, fixture.home_team_id)
    away_aliases = _team_alias_set(registry, fixture.away_team_id)
    expanded_home: set = set()
    for h in home_aliases:
        expanded_home |= normalize_fl_abbr(sport, h)
    expanded_away: set = set()
    for a in away_aliases:
        expanded_away |= normalize_fl_abbr(sport, a)
    out = set()
    for h in expanded_home:
        for a in expanded_away:
            out.add(h + a)
            out.add(a + h)
    return out


# ── Per-record seeder ────────────────────────────────────────────

def seed_kalshi_record(registry: IdentityRegistry,
                        kalshi_record: dict,
                        sport: str) -> Optional[Fixture]:
    """Resolve a Kalshi cache record to a canonical Fixture.

    Walks the three-tier ladder (strict → alias_table; guarded
    fuzzy is Phase C2). On a match, registers a 'kalshi' alias
    against the fixture canonical id and returns the Fixture.
    Returns None for outright records, unparseable tickers, and
    records that miss every tier.

    `kalshi_record` shape (subset, what we actually read):
        {
            'event_ticker':  'KXUCLGAME-26MAY05ARSATM',
            'series_ticker': 'KXUCLGAME',
            ...
        }
    """
    ticker = (kalshi_record.get("event_ticker") or "").upper().strip()
    series = (kalshi_record.get("series_ticker") or "").upper().strip()
    if not ticker or not series:
        return None

    identity = parse_ticker(ticker, series, sport)
    if identity is None:
        return None

    # Per_leg path (Phase C2c): tennis sets, esports maps. The
    # per_leg ticker resolves to its PARENT fixture (the match) +
    # a parameterized sub-market for the specific leg.
    if identity.kind == "per_leg":
        parent_id = parent_fixture_identity(identity)
        if parent_id is None:
            return None
        parent_date = parent_id.date
        parent_abbr = parent_id.abbr_block
        if not parent_date or not parent_abbr:
            return None
        candidates = registry.lookup_fixtures_by_date(sport, parent_date)
        # Phase C2e: collect ALL abbr-matching candidates, then pick
        # the time-closest. Per_leg tickers carry both date+time when
        # they hit PATTERN_LEG_DATE_TIME (e.g. esports maps); G_LEG_DATE
        # tennis-set tickers don't, in which case time is None and
        # _pick_best_by_time falls through to first-match.
        leg_matching = [
            fx for fx in candidates
            if (parent_abbr in _orientations_strict(registry, fx)
                or parent_abbr in _orientations_with_alias_table(
                    registry, fx, sport,
                ))
        ]
        leg_winner = _pick_best_by_time(leg_matching, parent_id)
        if leg_winner is not None:
            registry.register_alias(
                source="kalshi", external_id=ticker,
                canonical_id=leg_winner.id, method="strict",
                confidence=1.0,
            )
            _seed_per_leg_market_layer(
                registry, leg_winner, kalshi_record, sport,
                leg_n=identity.leg_n or 0,
            )
            return leg_winner
        return None

    if identity.kind != "per_fixture":
        # Outright / per_series — don't pair to fixtures.
        return None

    fixture_date = identity.date
    abbr_block = identity.abbr_block
    if not fixture_date or not abbr_block:
        return None

    candidates = registry.lookup_fixtures_by_date(sport, fixture_date)
    if not candidates:
        return None

    # Tier 1: strict abbr-equality on team aliases. Phase C2e —
    # collect ALL abbr-matching candidates, then pick the
    # time-closest one. For G7 tickers (date+time+abbr) this
    # disambiguates MLB doubleheaders, same-day intl basketball
    # multi-fixture cases, etc. For G1 tickers (date+abbr only),
    # _pick_best_by_time short-circuits to first-match (no time
    # to score against), preserving pre-C2e behavior.
    strict_matching = [
        fx for fx in candidates
        if abbr_block in _orientations_strict(registry, fx)
    ]
    strict_winner = _pick_best_by_time(strict_matching, identity)
    if strict_winner is not None:
        registry.register_alias(
            source="kalshi", external_id=ticker,
            canonical_id=strict_winner.id, method="strict",
            confidence=1.0,
        )
        _seed_winner_market_layer(
            registry, strict_winner, kalshi_record, sport,
        )
        return strict_winner

    # Tier 2: alias-table expansion. Same time-aware tiebreaker.
    alias_matching = [
        fx for fx in candidates
        if abbr_block in _orientations_with_alias_table(registry, fx, sport)
    ]
    alias_winner = _pick_best_by_time(alias_matching, identity)
    if alias_winner is not None:
        registry.register_alias(
            source="kalshi", external_id=ticker,
            canonical_id=alias_winner.id, method="alias_table",
            confidence=0.95,
        )
        _seed_winner_market_layer(
            registry, alias_winner, kalshi_record, sport,
        )
        return alias_winner

    # Tier 2.5: title-match — name normalization + ±30 min time gate.
    # Mirrors the batch seeder's behavior (Phase C2f+) so singular
    # and batch paths behave identically per-record.
    title_winner = _pair_via_title(registry, candidates, kalshi_record)
    if title_winner is not None:
        registry.register_alias(
            source="kalshi", external_id=ticker,
            canonical_id=title_winner.id, method="title_match",
            confidence=0.85,
        )
        _seed_winner_market_layer(
            registry, title_winner, kalshi_record, sport,
        )
        return title_winner

    # Tier 3 (guarded fuzzy) — only available via the batch seeder
    # since it requires bucket-level visibility (count of unpaired
    # FL fixtures + unpaired Kalshi records on the same date).
    return None


# ── Batch seeder with stats ──────────────────────────────────────

def seed_kalshi_records(registry: IdentityRegistry,
                          records: list, sport: str) -> dict:
    """Walk Kalshi cache records for a sport, attempt to seed each
    through the three-tier ladder (strict → alias_table → guarded
    fuzzy). Returns a stats dict for observability:

        {
            'total':              int,  # records in
            'paired_strict':      int,  # tier-1 hits
            'paired_alias':       int,  # tier-2 hits
            'paired_guarded':     int,  # tier-3 hits (Phase C2)
            'unpaired':           int,  # missed every tier
            'outright':           int,  # parsed as outright
            'unparseable':        int,  # parse_ticker None or wrong kind
        }

    Implementation: two passes.
      Pass 1 — for each record, run tier 1 + tier 2. Records that
               miss both are buffered for pass 2 along with their
               parsed identity.
      Pass 2 — group buffered records by (sport, fixture_date).
               For each bucket, find the unpaired FL fixtures (those
               with zero kalshi aliases) for that (sport, date). If
               the bucket has EXACTLY one unpaired FL fixture and
               EXACTLY one buffered Kalshi record, pair them with
               method='guarded_fuzzy', confidence=0.7. Anything
               else: leave unpaired.

    The 1+1 guard is the safety. If the bucket has two unpaired FL
    fixtures or two unparied Kalshi records on the same date, we
    refuse to guess — the caller should add an alias-map entry to
    disambiguate next deploy.
    """
    stats = {
        "total":          0,
        "paired_strict":  0,
        "paired_alias":   0,
        "paired_title":   0,
        "paired_guarded": 0,
        "paired_per_leg": 0,
        "unpaired":       0,
        "outright":       0,
        "unparseable":    0,
    }
    # Pass-2 buffer: (ticker, identity, raw record) per still-
    # unpaired per_fixture record. Per_leg never enters this buffer
    # because guarded fuzzy doesn't make sense for per_leg (the
    # parent fixture is always known via parent_fixture_identity).
    buffered: list = []

    # ── Pass 1: strict + alias_table ───────────────────────────
    for rec in records:
        if not isinstance(rec, dict):
            continue
        stats["total"] += 1
        ticker = (rec.get("event_ticker") or "").upper().strip()
        series = (rec.get("series_ticker") or "").upper().strip()
        if not ticker or not series:
            stats["unparseable"] += 1
            continue
        identity = parse_ticker(ticker, series, sport)
        if identity is None:
            stats["unparseable"] += 1
            continue
        if identity.kind == "outright":
            stats["outright"] += 1
            continue
        if identity.kind == "per_leg":
            # Per_leg: resolve parent fixture via parent_fixture_identity,
            # then attach a parameterized sub-market for the leg.
            parent_id = parent_fixture_identity(identity)
            if parent_id is None or not parent_id.date or not parent_id.abbr_block:
                stats["unpaired"] += 1
                continue
            cands = registry.lookup_fixtures_by_date(sport, parent_id.date)
            # Phase C2e: same time-aware tiebreaker as the per_fixture
            # path. parent_id.time is populated for G_LEG_DATE_TIME
            # tickers (esports maps); empty for G_LEG_DATE (tennis sets).
            leg_matching = [
                fx for fx in cands
                if (parent_id.abbr_block in _orientations_strict(registry, fx)
                    or parent_id.abbr_block in _orientations_with_alias_table(
                        registry, fx, sport,
                    ))
            ]
            hit_leg = _pick_best_by_time(leg_matching, parent_id)
            if hit_leg is None:
                stats["unpaired"] += 1
                continue
            registry.register_alias(
                source="kalshi", external_id=ticker,
                canonical_id=hit_leg.id, method="strict",
                confidence=1.0,
            )
            _seed_per_leg_market_layer(
                registry, hit_leg, rec, sport,
                leg_n=identity.leg_n or 0,
            )
            stats["paired_per_leg"] += 1
            continue
        if identity.kind != "per_fixture":
            stats["unparseable"] += 1
            continue

        fixture_date = identity.date
        abbr_block = identity.abbr_block
        if not fixture_date or not abbr_block:
            stats["unpaired"] += 1
            continue

        candidates = registry.lookup_fixtures_by_date(sport, fixture_date)
        hit = None
        hit_method = None
        # Phase C2e: time-aware tiebreaker. Collect ALL abbr-matching
        # candidates, then pick by time. See _pick_best_by_time docs.
        strict_matching = [
            fx for fx in candidates
            if abbr_block in _orientations_strict(registry, fx)
        ]
        strict_winner = _pick_best_by_time(strict_matching, identity)
        if strict_winner is not None:
            hit, hit_method = strict_winner, "strict"
        if hit is None:
            alias_matching = [
                fx for fx in candidates
                if abbr_block in _orientations_with_alias_table(
                    registry, fx, sport,
                )
            ]
            alias_winner = _pick_best_by_time(alias_matching, identity)
            if alias_winner is not None:
                hit, hit_method = alias_winner, "alias_table"

        # Tier 2.5 — title-based matching. Universal route that
        # bypasses alias_table for any pair Kalshi mentions by name.
        if hit is None:
            title_winner = _pair_via_title(registry, candidates, rec)
            if title_winner is not None:
                hit, hit_method = title_winner, "title_match"

        if hit is not None:
            confidence_by_method = {
                "strict":      1.0,
                "alias_table": 0.95,
                "title_match": 0.85,
            }
            registry.register_alias(
                source="kalshi", external_id=ticker,
                canonical_id=hit.id, method=hit_method,
                confidence=confidence_by_method.get(hit_method, 0.7),
            )
            _seed_winner_market_layer(registry, hit, rec, sport)
            if hit_method == "strict":
                stats["paired_strict"] += 1
            elif hit_method == "alias_table":
                stats["paired_alias"] += 1
            elif hit_method == "title_match":
                stats["paired_title"] += 1
            continue

        # Buffer for tier-3 attempt
        buffered.append((ticker, identity, rec))

    # ── Pass 2: guarded fuzzy ──────────────────────────────────
    # Group buffered records by (sport, fixture_date).
    by_date: dict = {}
    for ticker, identity, raw in buffered:
        key = (sport, identity.date)
        by_date.setdefault(key, []).append((ticker, identity, raw))

    for (sp, dt), bucket_records in by_date.items():
        # Find unpaired FL fixtures for this (sport, date).
        all_fixtures = registry.lookup_fixtures_by_date(sp, dt)
        unpaired_fixtures = [
            fx for fx in all_fixtures
            if registry.count_aliases_for(fx.id, source="kalshi") == 0
        ]
        # 1+1 guard
        if len(unpaired_fixtures) == 1 and len(bucket_records) == 1:
            fx = unpaired_fixtures[0]
            ticker, _, raw = bucket_records[0]
            registry.register_alias(
                source="kalshi", external_id=ticker,
                canonical_id=fx.id, method="guarded_fuzzy",
                confidence=0.7,
            )
            _seed_winner_market_layer(registry, fx, raw, sport)
            stats["paired_guarded"] += 1
        else:
            # Bucket too ambiguous — leave every record unpaired.
            stats["unpaired"] += len(bucket_records)

    return stats
