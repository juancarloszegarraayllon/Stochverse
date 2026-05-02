"""Probe v3 — answers two open questions from DETAILED_EVENT_STATS_SCHEMA.md.

Q4: Motorsport / Cycling 422 — does FL expose a /v1/races/* endpoint
    family for compound 16-char event_ids?
Q2 retest: /v1/events/player-stats was 404 for every event in the v2
    inventory. Hypothesis (a) is that the endpoint only returns data
    for top-tier leagues (NBA, EPL, NHL, MLB). We retest with multiple
    fresh events per sport to find at least one 200.

Run: FLASHLIVE_API_KEY=... python3 fl_probe/probe_races.py

Designed for stdlib-only execution in GitHub Actions.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API_KEY = os.environ.get("FLASHLIVE_API_KEY", "").strip()
API_HOST = "flashlive-sports.p.rapidapi.com"
BASE_URL = f"https://{API_HOST}"
LOCALE = "en_INT"


def fl_get(path: str, params: dict, timeout: int = 12) -> tuple:
    qs = urllib.parse.urlencode(params)
    url = f"{BASE_URL}{path}?{qs}"
    req = urllib.request.Request(url, headers={
        "x-rapidapi-key": API_KEY,
        "x-rapidapi-host": API_HOST,
    })
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            ms = (time.monotonic() - t0) * 1000
            try:
                payload = json.loads(body)
            except Exception:
                payload = None
            return r.status, ms, len(body), payload
    except urllib.error.HTTPError as e:
        return e.code, (time.monotonic() - t0) * 1000, 0, None
    except Exception:
        return 0, (time.monotonic() - t0) * 1000, 0, None


# ── Q4: Motorsport / Cycling /races/* probe ────────────────────────

# Compound 16-char event_ids harvested from probe v2 inventory. These
# are the IDs that 422'd against /v1/events/* — we try them against
# every plausible /races/* path with two param names.
RACES_TEST_IDS = {
    31: ("Motorsport", "Y9HWxKnpMctvDyx2"),
    34: ("Cycling", "IRzfH3xHUTd0OATH"),
}

RACES_PATHS = [
    "/v1/races/data",
    "/v1/races/details",
    "/v1/races/results",
    "/v1/races/standings",
    "/v1/races/competitors",
    "/v1/races/laps",
    "/v1/races/qualifying",
    "/v1/races/list",
    # also try /tournaments/* — motorsport is event-as-tournament shaped
    "/v1/tournaments/data",
    "/v1/tournaments/standings",
    "/v1/tournaments/results",
]

PARAM_NAMES = ["race_id", "event_id", "tournament_id", "id"]


def probe_races_for_sport(sport_id: int, sport_name: str, event_id: str) -> None:
    print(f"\n## {sport_name} (sport_id={sport_id}) — race_id={event_id}")
    print(f"  {'path':<36} {'param':<16} {'status':>6} {'ms':>5} {'bytes':>7}")
    print(f"  {'-'*36} {'-'*16} {'-'*6} {'-'*5} {'-'*7}")
    hits = []
    for path in RACES_PATHS:
        for param_name in PARAM_NAMES:
            params = {"locale": LOCALE, param_name: event_id}
            status, ms, size, payload = fl_get(path, params)
            marker = "✅" if status == 200 and size > 50 else (
                     "∅" if status == 200 else
                     "404" if status == 404 else
                     str(status))
            print(f"  {path:<36} {param_name:<16} {marker:>6} {ms:>5.0f} {size:>7}")
            if status == 200 and size > 50:
                hits.append((path, param_name, size))
    if hits:
        print(f"\n  HITS for {sport_name}:")
        for path, param_name, size in hits:
            print(f"    {path}?{param_name}={event_id}  →  {size} bytes")
    else:
        print(f"\n  ⊗ no /races/* path matched for {sport_name}")


# ── Q2 retest: /v1/events/player-stats across many events per sport ──

# Sports where player-stats might plausibly return data. We focus on
# major team sports (basketball, hockey, baseball, AMF) since those
# all have detailed player tracking in real-life broadcast graphics.
PLAYER_STATS_SPORTS = {
    1: "Soccer",
    3: "Basketball",
    4: "Hockey",
    5: "American Football",
    6: "Baseball",
}

# How many fresh events per sport to retest. We sample from /list
# rather than just /live-list so we hit recently-finished events too
# (player-stats is often only generated post-match).
EVENTS_PER_SPORT = 8


def list_events_for_sport(sport_id: int) -> list[str]:
    """Walk a ±2 day window collecting event_ids for this sport."""
    out: list[str] = []
    for indent in (0, -1, 1, -2, 2):
        s, _, _, p = fl_get("/v1/events/list", {
            "locale": LOCALE, "sport_id": sport_id,
            "timezone": "0", "indent_days": indent,
        })
        if s == 200 and isinstance(p, dict):
            for t in (p.get("DATA") or []):
                for e in (t.get("EVENTS") or []):
                    eid = e.get("EVENT_ID")
                    home = e.get("HOME_NAME", "?")
                    away = e.get("AWAY_NAME", "?")
                    tname = t.get("NAME", "?")
                    if eid and eid not in [x[0] for x in out]:
                        out.append((eid, f"{home} vs {away}", tname))
            if len(out) >= EVENTS_PER_SPORT:
                break
    return out[:EVENTS_PER_SPORT]


def probe_player_stats_for_sport(sport_id: int, sport_name: str) -> None:
    print(f"\n## /player-stats retest — {sport_name} (sport_id={sport_id})")
    events = list_events_for_sport(sport_id)
    if not events:
        print(f"  ⊗ no events found for {sport_name}")
        return
    print(f"  {'event_id':<10} {'status':>6} {'ms':>5} {'bytes':>7}  match  /  tournament")
    print(f"  {'-'*10} {'-'*6} {'-'*5} {'-'*7}  {'-'*55}")
    hits = []
    for eid, matchup, tname in events:
        s, ms, size, _ = fl_get(
            "/v1/events/player-stats",
            {"locale": LOCALE, "event_id": eid},
        )
        marker = "✅" if s == 200 and size > 50 else (
                 "∅" if s == 200 else
                 "404" if s == 404 else str(s))
        label = f"{matchup}  /  {tname}"[:55]
        print(f"  {eid:<10} {marker:>6} {ms:>5.0f} {size:>7}  {label}")
        if s == 200 and size > 50:
            hits.append((eid, matchup, tname, size))
    if hits:
        print(f"\n  ✅ {sport_name}: {len(hits)}/{len(events)} events returned player-stats data")
        for eid, matchup, tname, size in hits:
            print(f"     {eid}  →  {size} bytes  ({matchup} / {tname})")
    else:
        print(f"\n  ⊗ {sport_name}: 0/{len(events)} events returned player-stats data")


def main() -> None:
    if not API_KEY:
        sys.exit("FLASHLIVE_API_KEY env var not set")

    print("=" * 78)
    print("# Probe v3 — /races/* discovery + /player-stats retest")
    print("=" * 78)

    print("\n" + "=" * 78)
    print("# Q4: Motorsport / Cycling — does /v1/races/* exist?")
    print("=" * 78)
    for sport_id, (sport_name, event_id) in RACES_TEST_IDS.items():
        probe_races_for_sport(sport_id, sport_name, event_id)

    print("\n" + "=" * 78)
    print("# Q2 retest: /v1/events/player-stats across multiple events")
    print("=" * 78)
    for sport_id, sport_name in PLAYER_STATS_SPORTS.items():
        probe_player_stats_for_sport(sport_id, sport_name)


if __name__ == "__main__":
    main()
