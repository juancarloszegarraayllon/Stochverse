"""Identity-based join between Kalshi cache and FL events list.

Phase 3 of /sports v2 (see SPORTS_V2_PLAN.md). Replaces the
`_build_kalshi_index_for_sport()` + `_kalshi_title_corroborates_fl_game()`
+ second-pass attach + `_collect_unpaired_h2h_for_sport()` chain in
main.py with a single deterministic join layer.

Pure / no I/O. Imports only kalshi_identity (also pure).

Public API:
  Pairing                        — (fl_event, fl_id, kalshi_records)
  build_kalshi_index(records, s) — dict for fast date-bucket lookup
  join_with_fl(events, idx, s)   — walk FL, return pairings + unpaired
  find_unpaired_buckets(rs, s)   — group unpaired by per-fixture Identity

The join is O(N + M*F) where:
  N = total Kalshi records of the sport
  M = total FL events
  F = average records per (sport, date) bucket
For typical sport sizes (≤1000 records, ≤200 FL events) this runs
in single-digit milliseconds.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from kalshi_identity import (
    Identity, parse_ticker, compute_fl_identity, match,
    parent_fixture_identity,
)


# ── Data class for a successful pairing ──────────────────────────

@dataclass
class Pairing:
    """One FL event paired with the Kalshi records that share its identity.

    `kalshi_records` is the raw cache-record list — the renderer
    decides which is the "primary" (headline GAME) vs sub-markets
    (TOTAL/SPREAD/etc.) using outcome_shapes rules.

    Per-leg records (tennis sets, esports maps) are included alongside
    their parent per_fixture record because their parent_fixture
    Identity matches the same FL fixture.
    """
    fl_event: dict
    fl_identity: Identity
    kalshi_records: list[dict]


# ── build_kalshi_index ───────────────────────────────────────────

def _record_target_identity(record: dict, sport: str) -> Optional[Identity]:
    """Parse a cache record's ticker and return its per-fixture identity.

    For per_leg records (set/map sub-markets), returns the parent
    fixture's identity so they group with the parent.

    Returns None for non-per-fixture records (series, outright,
    tournament, unparsed) — those don't participate in the join.
    """
    ident = parse_ticker(
        record.get("event_ticker") or "",
        record.get("series_ticker") or "",
        sport,
    )
    if ident.kind == "per_leg":
        ident = parent_fixture_identity(ident) or ident
    if ident.kind != "per_fixture" or ident.date is None:
        return None
    return ident


def build_kalshi_index(records: list[dict], sport: str) -> dict:
    """Index Kalshi per-fixture records by (sport, date) for fast lookup.

    Records are filtered to `_sport == sport` first. Per-leg records
    are routed to their parent fixture's identity (via
    `parent_fixture_identity`) so all sub-markets sharing a fixture
    end up in the same date-bucket entry.

    Records that don't have a per_fixture identity (series-level,
    outrights, tournament-handle, unparsed) are skipped — they're
    handled by separate indexes (TODO: phase 5).

    Returns:
      {
        (sport, date): [(identity, record), (identity, record), ...],
        ...
      }
    """
    idx: dict[tuple, list] = {}
    for r in records:
        if not isinstance(r, dict):
            continue
        if (r.get("_sport") or "") != sport:
            continue
        ident = _record_target_identity(r, sport)
        if ident is None:
            continue
        idx.setdefault((sport, ident.date), []).append((ident, r))
    return idx


# ── join_with_fl ─────────────────────────────────────────────────

def join_with_fl(
    fl_events: list[dict],
    kalshi_idx: dict,
    sport: str,
    fuzz_days: int = 1,
) -> tuple[list[Pairing], list[dict]]:
    """Walk FL events, find Kalshi records that share their identity.

    For each FL event:
      1. Compute its Identity (via compute_fl_identity).
      2. For each ±fuzz_days date offset, look up the (sport, date)
         bucket in kalshi_idx.
      3. Within the bucket, call match() — handles orientation,
         time-fuzz for G7 sports.
      4. Collect ALL matching kalshi records into the pairing.
      5. A pairing absorbs every matched record into kalshi_records;
         if multiple FL events match the same kalshi record (rare),
         only the first FL event keeps it.

    Returns:
      (pairings, unpaired_kalshi_records)

    pairings: list[Pairing] — FL events that paired
    unpaired: list[dict]   — Kalshi records that didn't pair to any FL
    """
    pairings: list[Pairing] = []
    consumed_record_ids: set[int] = set()

    for ev in fl_events:
        if not isinstance(ev, dict):
            continue
        fl_id = compute_fl_identity(ev, sport)
        if fl_id is None:
            continue

        matches: list[dict] = []
        # ±fuzz_days date window
        for delta in range(-fuzz_days, fuzz_days + 1):
            trial_date = fl_id.date + timedelta(days=delta)
            bucket = kalshi_idx.get((sport, trial_date), [])
            for k_id, rec in bucket:
                if id(rec) in consumed_record_ids:
                    continue
                if match(k_id, fl_id, fuzz_days=fuzz_days):
                    matches.append(rec)
                    consumed_record_ids.add(id(rec))

        if matches:
            pairings.append(Pairing(
                fl_event=ev, fl_identity=fl_id,
                kalshi_records=matches,
            ))

    # Anything not consumed is unpaired.
    unpaired: list[dict] = []
    for bucket in kalshi_idx.values():
        for _id, rec in bucket:
            if id(rec) not in consumed_record_ids:
                unpaired.append(rec)

    return pairings, unpaired


# ── find_unpaired_buckets ────────────────────────────────────────

def _canonical_fixture_key(ident: Identity) -> tuple:
    """Hashable key that identifies a unique fixture, ignoring
    series_base / raw_suffix (which differ across sub-markets of
    the same fixture).
    """
    return (ident.sport, ident.date, ident.time, ident.abbr_block)


def find_unpaired_buckets(
    unpaired_records: list[dict],
    sport: str,
) -> dict:
    """Group leftover Kalshi records by their per-fixture Identity.

    Each group represents a Kalshi-only fixture (one Bayern-PSG with
    its 8 sub-markets), as a synthesized-event candidate.

    Records with non-per_fixture identities (outrights, etc.) are
    skipped — those go through different paths.

    Returns:
      { fixture_key: [records_sharing_this_fixture, ...], ... }

    Where `fixture_key` is `(sport, date, time, abbr_block)`.
    """
    buckets: dict[tuple, list[dict]] = {}
    for r in unpaired_records:
        if not isinstance(r, dict):
            continue
        ident = _record_target_identity(r, sport)
        if ident is None:
            continue
        key = _canonical_fixture_key(ident)
        buckets.setdefault(key, []).append(r)
    return buckets


# ── Convenience: full-pipeline ───────────────────────────────────

def join_pipeline(
    cache_records: list[dict],
    fl_events: list[dict],
    sport: str,
    fuzz_days: int = 1,
) -> dict:
    """Full pipeline: index + join + bucket. Returns a single dict
    suitable for serializing to a debug endpoint:

      {
        "sport": str,
        "kalshi_total_records": int,
        "kalshi_per_fixture_records": int,
        "fl_events": int,
        "pairings": [
          { "fl_event_id": str, "abbr_block": str, "date": iso,
            "kalshi_count": int, "tickers": [str, ...] },
          ...
        ],
        "unpaired_buckets": [
          { "key": [sport, date, time, abbr_block],
            "kalshi_count": int, "tickers": [str, ...] },
          ...
        ],
      }
    """
    idx = build_kalshi_index(cache_records, sport)
    pairings, unpaired = join_with_fl(fl_events, idx, sport, fuzz_days)
    buckets = find_unpaired_buckets(unpaired, sport)

    per_fixture_count = sum(len(v) for v in idx.values())

    return {
        "sport": sport,
        "kalshi_total_records": sum(
            1 for r in cache_records
            if isinstance(r, dict) and r.get("_sport") == sport
        ),
        "kalshi_per_fixture_records": per_fixture_count,
        "fl_events": len(fl_events),
        "pairings": [
            {
                "fl_event_id": p.fl_event.get("EVENT_ID") or "",
                "fl_home": p.fl_event.get("HOME_NAME") or "",
                "fl_away": p.fl_event.get("AWAY_NAME") or "",
                "fl_short": [
                    p.fl_event.get("SHORTNAME_HOME") or "",
                    p.fl_event.get("SHORTNAME_AWAY") or "",
                ],
                "date": p.fl_identity.date.isoformat() if p.fl_identity.date else None,
                "time": p.fl_identity.time,
                "kalshi_count": len(p.kalshi_records),
                "tickers": [
                    r.get("event_ticker", "") for r in p.kalshi_records
                ],
            }
            for p in pairings
        ],
        "unpaired_buckets": [
            {
                "key": [str(x) if x is not None else None for x in key],
                "kalshi_count": len(recs),
                "tickers": [r.get("event_ticker", "") for r in recs],
                "sample_title": (recs[0].get("title") or "") if recs else "",
            }
            for key, recs in buckets.items()
        ],
    }
