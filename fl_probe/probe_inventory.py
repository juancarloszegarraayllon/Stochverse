"""Field-discovery probe v2 — comprehensive FL endpoint inventory.

For each FL-recognized sport (1..42 per OpenAPI spec):
  1. Find a representative event_id (live first, then today, ±N days).
  2. Hit every event_id-based endpoint.
  3. Walk the response recursively and collect every key path with its
     value type. Multiple list elements are unioned so we discover
     fields that only appear on some entries.

Output:
  - Per-sport per-endpoint table (status, latency, payload size, # keys)
  - Full key-path inventory at the end as JSON for machine consumption
  - Summary table at the very end showing which endpoints have data
    in which sports — drives the per-sport diagrams.

Run:
  FLASHLIVE_API_KEY=... python3 fl_probe/probe_inventory.py
  FLASHLIVE_API_KEY=... python3 fl_probe/probe_inventory.py 1 13 14   # subset

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


# Sport-id → human name. From FL OpenAPI spec + List of Object docs.
SPORTS: dict[int, str] = {
    1: "Soccer", 2: "Tennis", 3: "Basketball", 4: "Hockey",
    5: "American Football", 6: "Baseball", 7: "Handball",
    8: "Rugby Union", 9: "Floorball", 10: "Bandy", 11: "Futsal",
    12: "Volleyball", 13: "Cricket", 14: "Darts", 15: "Snooker",
    16: "Boxing", 17: "Beach Volleyball", 18: "Aussie Rules",
    19: "Rugby League", 21: "Badminton", 22: "Water Polo",
    23: "Golf", 24: "Field Hockey", 25: "Table Tennis",
    26: "Beach Soccer", 28: "MMA", 29: "Netball", 30: "Pesapallo",
    31: "Motorsport", 32: "Autoracing", 33: "Motoracing",
    34: "Cycling", 35: "Horse Racing", 36: "Esports",
    37: "Winter Sports", 38: "Ski Jumping", 40: "Cross Country",
    41: "Biathlon", 42: "Kabaddi",
}


# Every endpoint that takes a single event_id (the modal core). Order
# preserved so output rows line up consistently across sports.
EVENT_ENDPOINTS: list[str] = [
    "/v1/events/data",
    "/v1/events/details",            # beta
    "/v1/events/brief",
    "/v1/events/summary",
    "/v1/events/summary-results",
    "/v1/events/summary-incidents",
    "/v1/events/statistics",
    "/v1/events/statistics-alt",     # darts
    "/v1/events/lineups",
    "/v1/events/predicted-lineups",
    "/v1/events/missing-players",
    "/v1/events/news",
    "/v1/events/commentary",
    "/v1/events/commentary-alt",     # cricket
    "/v1/events/scorecard",          # cricket
    "/v1/events/fall-of-wickets",    # cricket
    "/v1/events/ball-by-ball",       # cricket
    "/v1/events/throw-by-throw",     # darts
    "/v1/events/points-history",     # tennis
    "/v1/events/highlights",
    "/v1/events/report",
    "/v1/events/h2h",
    "/v1/events/last-change",
    "/v1/events/odds",
    "/v1/events/player-stats",
    "/v1/events/player-statistics-alt",  # basketball
]


def fl_get(path: str, params: dict, timeout: int = 12) -> tuple:
    """Plain HTTPS GET with RapidAPI auth. Returns
    (status, ms, bytes, json_payload_or_None)."""
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


def find_event_for_sport(sport_id: int) -> tuple[str | None, str | None]:
    """Try /live-list first (best signal: in-progress data),
    then /list with widening indent_days. Returns (event_id, source)."""
    s, _, _, p = fl_get("/v1/events/live-list", {
        "locale": LOCALE, "sport_id": sport_id, "timezone": "0",
    })
    if s == 200 and isinstance(p, dict):
        for t in (p.get("DATA") or []):
            for e in (t.get("EVENTS") or []):
                eid = e.get("EVENT_ID")
                if eid:
                    return eid, "live"
    # Walk a ±7 day window — covers seasonal sports and weekly leagues.
    for indent in (0, -1, 1, -2, 2, -3, 3, -7, 7):
        s, _, _, p = fl_get("/v1/events/list", {
            "locale": LOCALE, "sport_id": sport_id,
            "timezone": "0", "indent_days": indent,
        })
        if s == 200 and isinstance(p, dict):
            for t in (p.get("DATA") or []):
                for e in (t.get("EVENTS") or []):
                    eid = e.get("EVENT_ID")
                    if eid:
                        return eid, f"indent={indent}"
    return None, None


def collect_keys(obj, prefix: str = "", out: dict | None = None,
                 max_depth: int = 8, list_sample: int = 5) -> dict:
    """Recursively flatten a JSON value into {dotted_path: type_name}.

    Walks up to `list_sample` elements per list and unions their keys
    so we catch fields that only appear on some entries. `[]` in the
    path indicates a list level.
    """
    if out is None:
        out = {}
    if max_depth <= 0:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else k
            out[path] = type(v).__name__
            collect_keys(v, path, out, max_depth - 1, list_sample)
    elif isinstance(obj, list) and obj:
        for i, item in enumerate(obj[:list_sample]):
            collect_keys(item, f"{prefix}[]", out, max_depth - 1, list_sample)
    return out


def probe_sport(sport_id: int, sport_name: str) -> dict | None:
    """Probe every event endpoint for a representative event in this
    sport. Prints a per-row table to stdout, returns structured result."""
    print(f"\n{'='*78}")
    print(f"## {sport_name}  (sport_id={sport_id})")
    print(f"{'='*78}")
    eid, source = find_event_for_sport(sport_id)
    if not eid:
        print(f"  ⊗ no event found in ±7 day window — skipping {sport_name}")
        return {
            "sport_id": sport_id, "sport_name": sport_name,
            "event_id": None, "source": None, "endpoints": {},
        }
    print(f"  event_id: {eid}    source: {source}")
    print(f"  {'endpoint':<42} {'st':>3} {'ms':>5} {'bytes':>7} {'keys':>5}")
    print(f"  {'-'*42} {'-'*3} {'-'*5} {'-'*7} {'-'*5}")
    endpoint_results: dict[str, dict] = {}
    for path in EVENT_ENDPOINTS:
        params = {"locale": LOCALE, "event_id": eid}
        st, ms, sz, payload = fl_get(path, params)
        keys = (collect_keys(payload)
                if st == 200 and payload is not None else {})
        endpoint_results[path] = {
            "status":    st,
            "ms":        round(ms),
            "bytes":     sz,
            "key_count": len(keys),
            "keys":      sorted(keys.keys()),
            "key_types": keys,
        }
        marker = "✅" if (st == 200 and keys) else (
                 "∅" if st == 200 else
                 "404" if st == 404 else
                 f"E{st}")
        print(f"  {path:<42} {marker:>3} {ms:>5.0f} {sz:>7} {len(keys):>5}")
    return {
        "sport_id":   sport_id,
        "sport_name": sport_name,
        "event_id":   eid,
        "source":     source,
        "endpoints":  endpoint_results,
    }


def print_master_summary(inventory: list[dict]) -> None:
    """Cross-sport coverage matrix at the end. One row per endpoint,
    one column per sport, ✅/∅/✗ per cell. Drives diagram decisions."""
    print(f"\n\n{'='*78}")
    print(f"## MASTER COVERAGE MATRIX")
    print(f"{'='*78}")
    sports_with_event = [s for s in inventory if s["event_id"]]
    if not sports_with_event:
        print("  no sports with events found")
        return
    # Column headers (short sport name)
    short = {
        "Soccer":"SOC","Tennis":"TEN","Basketball":"BSK","Hockey":"HKY",
        "American Football":"AMF","Baseball":"BSB","Handball":"HBL",
        "Rugby Union":"RUG","Floorball":"FLB","Bandy":"BND","Futsal":"FUT",
        "Volleyball":"VOL","Cricket":"CRK","Darts":"DRT","Snooker":"SNK",
        "Boxing":"BOX","Beach Volleyball":"BVL","Aussie Rules":"AUS",
        "Rugby League":"RGL","Badminton":"BAD","Water Polo":"WTP",
        "Golf":"GLF","Field Hockey":"FHK","Table Tennis":"TBT",
        "Beach Soccer":"BSC","MMA":"MMA","Netball":"NET",
        "Pesapallo":"PSP","Motorsport":"MTS","Autoracing":"AUT",
        "Motoracing":"MTR","Cycling":"CYC","Horse Racing":"HRR",
        "Esports":"ESP","Winter Sports":"WTS","Ski Jumping":"SKI",
        "Cross Country":"CCT","Biathlon":"BTH","Kabaddi":"KBD",
    }
    cols = [(s["sport_name"], short.get(s["sport_name"], s["sport_name"][:3]))
            for s in sports_with_event]
    print(f"  {'endpoint':<42}  " + " ".join(c[1] for c in cols))
    print(f"  {'-'*42}  " + " ".join("-" * 3 for _ in cols))
    for path in EVENT_ENDPOINTS:
        cells = []
        for s in sports_with_event:
            r = s["endpoints"].get(path, {})
            st = r.get("status", 0)
            kc = r.get("key_count", 0)
            if st == 200 and kc:
                cells.append("✅ ")
            elif st == 200:
                cells.append("∅  ")  # 200 but empty
            elif st == 404:
                cells.append("·  ")  # 404 = not applicable
            else:
                cells.append("?  ")
        print(f"  {path:<42}  " + " ".join(cells))


def main() -> None:
    if not API_KEY:
        sys.exit("FLASHLIVE_API_KEY env var not set")
    args = sys.argv[1:]
    if args:
        sport_ids = [int(x) for x in args if x.isdigit()]
    else:
        sport_ids = sorted(SPORTS.keys())
    print(f"# FL Probe v2 — Field Discovery Inventory")
    print(f"sports to probe: {len(sport_ids)}")
    print(f"endpoints per event: {len(EVENT_ENDPOINTS)}")
    print(f"max calls (worst case): {len(sport_ids) * (10 + len(EVENT_ENDPOINTS))}")
    inventory: list[dict] = []
    for sid in sport_ids:
        result = probe_sport(sid, SPORTS.get(sid, f"sport_{sid}"))
        if result is not None:
            inventory.append(result)
    print_master_summary(inventory)
    # Compact JSON dump (no key_types — those bloat the artifact;
    # human-readable run already showed key paths per endpoint).
    compact = []
    for s in inventory:
        compact.append({
            "sport_id":   s["sport_id"],
            "sport_name": s["sport_name"],
            "event_id":   s["event_id"],
            "source":     s["source"],
            "endpoints": {
                p: {"status": r["status"], "key_count": r["key_count"],
                    "keys": r["keys"]}
                for p, r in s["endpoints"].items()
            },
        })
    print(f"\n\n## INVENTORY JSON (machine-readable)")
    print(json.dumps(compact, indent=2))


if __name__ == "__main__":
    main()
