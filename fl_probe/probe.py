"""Standalone FlashLive endpoint probe.

Run: FLASHLIVE_API_KEY=... python fl_probe/probe.py [EVENT_ID] [SPORT_ID]
Default: a soccer event resolved at runtime from /v1/events/live-list.

Goal: ground-truth latency + payload size + LAST_CHANGE_KEY semantics on
the Mega-tier RapidAPI proxy from this machine. No app dependencies.
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request

API_KEY = os.environ.get("FLASHLIVE_API_KEY", "").strip()
API_HOST = "flashlive-sports.p.rapidapi.com"
BASE_URL = f"https://{API_HOST}"
LOCALE = "en_INT"

if not API_KEY:
    sys.exit("FLASHLIVE_API_KEY env var not set")


def fl_get(path: str, params: dict) -> tuple[int, float, int, dict | list | None]:
    qs = urllib.parse.urlencode(params)
    url = f"{BASE_URL}{path}?{qs}"
    req = urllib.request.Request(
        url,
        headers={"x-rapidapi-key": API_KEY, "x-rapidapi-host": API_HOST},
    )
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            body = r.read()
            elapsed_ms = (time.monotonic() - t0) * 1000
            try:
                payload = json.loads(body)
            except Exception:
                payload = None
            return r.status, elapsed_ms, len(body), payload
    except urllib.error.HTTPError as e:
        elapsed_ms = (time.monotonic() - t0) * 1000
        return e.code, elapsed_ms, 0, None


def last_change_key(payload) -> str | None:
    if isinstance(payload, dict):
        if "LAST_CHANGE_KEY" in payload:
            return str(payload["LAST_CHANGE_KEY"])
        meta = payload.get("META")
        if isinstance(meta, dict) and "LAST_CHANGE_KEY" in meta:
            return str(meta["LAST_CHANGE_KEY"])
    return None


def pick_event_id(sport_id: int) -> str | None:
    status, ms, _, payload = fl_get(
        "/v1/events/live-list",
        {"locale": LOCALE, "sport_id": sport_id, "timezone": "0"},
    )
    print(f"  /v1/events/live-list → {status} in {ms:.0f}ms")
    if status != 200 or not isinstance(payload, dict):
        return None
    data = payload.get("DATA") or []
    for tournament in data:
        for event in tournament.get("EVENTS", []) or []:
            eid = event.get("EVENT_ID")
            if eid:
                home = event.get("HOME_NAME", "?")
                away = event.get("AWAY_NAME", "?")
                print(f"  picked live event: {eid}  ({home} vs {away})")
                return eid
    return None


PROBE_ENDPOINTS = [
    ("/v1/events/last-change", "🔑 hash dispatcher"),
    ("/v1/events/data", "current fan-out: data"),
    ("/v1/events/statistics", "current fan-out: stats"),
    ("/v1/events/lineups", "current fan-out: lineups"),
    ("/v1/events/summary-incidents", "current fan-out: incidents"),
    ("/v1/events/missing-players", "current fan-out: missing"),
    ("/v1/events/news", "current fan-out: news"),
    ("/v1/events/player-stats", "current fan-out: player_stats"),
    ("/v1/events/h2h", "🆕 direct H2H (replaces 5-call chain)"),
    ("/v1/events/details", "🆕 details (beta)"),
    ("/v1/events/summary", "summary"),
    ("/v1/events/highlights", "highlights video"),
    ("/v1/events/predicted-lineups", "🆕 pre-match lineups"),
]


def probe_once(event_id: str, label: str) -> dict[str, str | None]:
    print(f"\n=== ROUND: {label} ===")
    print(f"{'endpoint':<42} {'status':>6} {'ms':>7} {'bytes':>8}  hash")
    print("-" * 90)
    hashes: dict[str, str | None] = {}
    for path, _desc in PROBE_ENDPOINTS:
        status, ms, size, payload = fl_get(
            path, {"locale": LOCALE, "event_id": event_id}
        )
        h = last_change_key(payload) if status == 200 else None
        hashes[path] = h
        h_short = (h[:16] + "…") if h else "—"
        print(f"{path:<42} {status:>6} {ms:>6.0f}  {size:>8}  {h_short}")
    return hashes


def probe_live_update(sport_id: int):
    print(f"\n=== /v1/events/live-update sport_id={sport_id} ===")
    for i in range(3):
        status, ms, size, payload = fl_get(
            "/v1/events/live-update", {"locale": LOCALE, "sport_id": sport_id}
        )
        n = len(payload.get("DATA", [])) if isinstance(payload, dict) else 0
        print(f"  call {i+1}: status={status} {ms:.0f}ms {size}B events_changed={n}")
        time.sleep(5)


def main():
    args = sys.argv[1:]
    sport_id = int(args[1]) if len(args) > 1 else 1  # default soccer
    if args:
        event_id = args[0]
        print(f"using provided event_id={event_id} sport_id={sport_id}")
    else:
        print(f"resolving a live event for sport_id={sport_id}…")
        event_id = pick_event_id(sport_id)
        if not event_id:
            sys.exit("no live events found; pass an event_id explicitly")

    h1 = probe_once(event_id, "T+0")
    print("\nsleeping 30s…")
    time.sleep(30)
    h2 = probe_once(event_id, "T+30s")

    print("\n=== HASH DELTA ===")
    print(f"{'endpoint':<42} {'changed':>8}")
    print("-" * 60)
    for path, _desc in PROBE_ENDPOINTS:
        a, b = h1.get(path), h2.get(path)
        if a is None and b is None:
            mark = "no-hash"
        elif a == b:
            mark = "same"
        else:
            mark = "CHANGED"
        print(f"{path:<42} {mark:>8}")

    probe_live_update(sport_id)


if __name__ == "__main__":
    main()
