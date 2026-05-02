"""Probe v4 — hits each documented FL endpoint with its canonical
example IDs from the OpenAPI spec, to settle which endpoints actually
return data when called correctly.

Why this exists: probe v2 used random events from /v1/events/list and
got 404 on /player-stats, /scorecard, /throw-by-throw, etc. We marked
those endpoints "dead" in DETAILED_EVENT_STATS_SCHEMA.md §3, but the
OpenAPI spec documents canonical example IDs the API author put there
because *those events are known to return data*. So 404 on a random
event ≠ endpoint dead — it just means that event has no data for that
category.

This probe hits each canonical example exactly as the spec says, and
prints status / size / top-level response keys. After it runs we know
which §3 "dead permanently" decisions to revert.

Run: FLASHLIVE_API_KEY=... python3 fl_probe/probe_canonicals.py
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


def top_keys(payload, limit: int = 8) -> str:
    """Compact summary of response top-level keys for the log."""
    if payload is None:
        return ""
    if isinstance(payload, dict):
        data = payload.get("DATA")
        if isinstance(data, dict):
            keys = list(data.keys())[:limit]
            extra = "" if len(data) <= limit else f" (+{len(data) - limit})"
            return f"DATA={{{', '.join(keys)}{extra}}}"
        if isinstance(data, list) and data:
            first = data[0]
            if isinstance(first, dict):
                keys = list(first.keys())[:limit]
                extra = "" if len(first) <= limit else f" (+{len(first) - limit})"
                return f"DATA=[{{{', '.join(keys)}{extra}}}, ...×{len(data)}]"
            return f"DATA=[...×{len(data)}]"
        if isinstance(data, list):
            return "DATA=[]"
        keys = list(payload.keys())[:limit]
        return f"{{{', '.join(keys)}}}"
    if isinstance(payload, list):
        return f"[...×{len(payload)}]"
    return ""


# ── Test cases ──────────────────────────────────────────────────────
#
# Each case = (group, label, path, params, expected). Expected is just
# a hint for log readability; the probe doesn't fail on mismatch — it
# always prints actual status.
#
# Canonical IDs come straight from the OpenAPI spec (HugeAPI portal).
# If a probe returns 404 here, the endpoint is genuinely conditional
# on the data category being present for that specific event — not
# the endpoint being dead.

CASES = [
    # Q1 — Cricket scorecard family. Expected: 200 if cricket data
    # is present for tK1xeE9p, otherwise 404 (still informative).
    ("Q1 cricket", "scorecard",
     "/v1/events/scorecard", {"event_id": "tK1xeE9p"}),
    ("Q1 cricket", "fall-of-wickets",
     "/v1/events/fall-of-wickets", {"event_id": "tK1xeE9p"}),
    ("Q1 cricket", "ball-by-ball",
     "/v1/events/ball-by-ball", {"event_id": "tK1xeE9p"}),
    ("Q1 cricket", "commentary-alt",
     "/v1/events/commentary-alt", {"event_id": "tK1xeE9p"}),

    # Q2 reopened — player-stats with the spec's canonical IDs.
    # Note: prior assistant transcribed "Sb1d5SC5" from a screenshot;
    # actual spec example is "Sbld5SC5" (lowercase L).
    ("Q2 player-stats", "player-stats (canonical)",
     "/v1/events/player-stats", {"event_id": "Sbld5SC5"}),
    ("Q2 player-stats", "player-statistics-alt (basketball)",
     "/v1/events/player-statistics-alt", {"event_id": "fXx7UFrK"}),

    # Tier F reclassification — golf. Probe v2 only hit /events/data
    # which 422'd because golf uses no_duel_event_id + event_id pair.
    ("Tier F golf", "no-duel-data",
     "/v1/events/no-duel-data",
     {"no_duel_event_id": "tOTtyuU7", "event_id": "n78WB41T"}),
    ("Tier F golf", "rounds-results",
     "/v1/events/rounds-results",
     {"no_duel_event_id": "tOTtyuU7", "event_id": "n78WB41T"}),

    # Tier G horse racing — uses sport_id + tournament_template_id,
    # not event_id. Probe v3 confirmed /races/* doesn't exist;
    # /racing-details is the actual endpoint.
    ("Tier G horse racing", "racing-details",
     "/v1/events/racing-details",
     {"sport_id": "35", "timezone": "0", "tournament_template_id": "fsB7cpNF"}),

    # Darts throw-by-throw — was 404 in probe v2 (non-live event).
    # Spec canonical may be a stored historical example.
    ("Darts", "throw-by-throw",
     "/v1/events/throw-by-throw", {"event_id": "j9TDJ0XI"}),

    # Predicted lineups — universal pre-match block, never probed
    # against the spec's canonical.
    ("Predicted lineups", "predicted-lineups",
     "/v1/events/predicted-lineups", {"event_id": "27cNiVKa"}),

    # Highlights — confirmed working in v2 inventory but spec has its
    # own canonical, useful as a baseline shape check.
    ("Highlights", "highlights",
     "/v1/events/highlights", {"event_id": "Mss8F4uf"}),

    # New event-level endpoints we never probed in v2.
    ("New odds", "prematch-odds",
     "/v1/events/prematch-odds",
     {"sport_id": "1", "event_id": "G8hqiThp"}),
    ("New odds", "live-odds-alt (HOME_AWAY, book=453)",
     "/v1/events/live-odds-alt",
     {"bet_type": "HOME_AWAY", "event_id": "6ZCocWsb", "book_id": "453"}),
    ("New odds", "live-odds-alt (HOME_DRAW_AWAY, book=16)",
     "/v1/events/live-odds-alt",
     {"bet_type": "HOME_DRAW_AWAY", "event_id": "6ZCocWsb", "book_id": "16"}),
    ("New odds", "list-main-odds (sport=1, today)",
     "/v1/events/list-main-odds",
     {"sport_id": "1", "timezone": "0", "indent_days": "0"}),

    # Real-time endpoints — never probed in v2 (we only care about
    # modal v2, but worth a baseline check for Step E later).
    ("Real-time", "live-list (sport=1)",
     "/v1/events/live-list",
     {"sport_id": "1", "timezone": "0"}),
    ("Real-time", "live-update (sport=1)",
     "/v1/events/live-update", {"sport_id": "1"}),

    # Last-change / commentary against canonicals — sanity baselines.
    ("Baseline", "last-change",
     "/v1/events/last-change", {"event_id": "4U8yxaPL"}),
    ("Baseline", "commentary",
     "/v1/events/commentary", {"event_id": "4U8yxaPL"}),
    ("Baseline", "report",
     "/v1/events/report", {"event_id": "4U8yxaPL"}),

    # H2H against spec canonical — should always return data.
    ("Baseline", "h2h",
     "/v1/events/h2h", {"event_id": "n9Wtc6KT"}),

    # Details (beta) — never confirmed live.
    ("Beta", "details",
     "/v1/events/details", {"event_id": "6ZCocWsb"}),
]


def run() -> None:
    print("=" * 78)
    print("# Probe v4 — canonical example IDs from FL OpenAPI spec")
    print("=" * 78)
    print(f"\n{'group':<22} {'label':<40} {'status':>6} {'ms':>5} {'bytes':>7}  keys")
    print(f"{'-'*22} {'-'*40} {'-'*6} {'-'*5} {'-'*7}  {'-'*40}")

    summary: dict[str, dict[str, int]] = {}

    for group, label, path, params in CASES:
        full_params = {"locale": LOCALE, **params}
        status, ms, size, payload = fl_get(path, full_params)
        if status == 200 and size > 50:
            marker = "OK"
        elif status == 200:
            marker = "EMPTY"
        elif status == 404:
            marker = "404"
        elif status == 422:
            marker = "422"
        else:
            marker = str(status)
        keys = top_keys(payload) if status == 200 else ""
        print(f"{group:<22} {label:<40} {marker:>6} {ms:>5.0f} {size:>7}  {keys}")
        bucket = summary.setdefault(group, {})
        bucket[marker] = bucket.get(marker, 0) + 1

    print("\n" + "=" * 78)
    print("# Summary by group")
    print("=" * 78)
    for group, counts in summary.items():
        parts = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        print(f"  {group:<22}  {parts}")

    print("\n" + "=" * 78)
    print("# Decision matrix (read this against DETAILED_EVENT_STATS_SCHEMA.md)")
    print("=" * 78)
    print("""
  Q1 cricket (tK1xeE9p):
    if scorecard / fall-of-wickets / ball-by-ball all OK → §9 Q1 RESOLVED
      cricket modal can render scorecard tab, just gate on event having data
    if all 404 → endpoints documented but don't actually return for this ID
      → reach out to FL support before designing cricket modal

  Q2 player-stats (Sbld5SC5):
    if OK → REVERT §3 'drop permanently'; add /player-stats to §2 modal
      blueprint with capability flag; mark §9 Q2 reopened-and-resolved-positive
    if 404 → endpoint documented but data not flowing for canonical event;
      keep §3 decision but note the canonical was tried (stronger evidence)

  Tier F golf (no-duel-data, rounds-results):
    if OK → reclassify golf out of Tier F; design golf modal with
      Rounds Results + No-Duel Data tabs
    if 404/422 → confirm Tier F (skip modal entirely)

  Tier G horse racing (racing-details):
    if OK → reclassify out of Tier G; design horse-racing card with
      racing-details block (different from event modal — sport-level)
    if 404/422 → keep Tier G as 'no FL modal'

  New odds endpoints (live-odds-alt, prematch-odds, list-main-odds):
    confirms odds block design — multiple feeds, not just /odds

  Predicted lineups (27cNiVKa):
    sanity baseline — should be OK; if not, our v2 inventory was wrong
""")


def main() -> None:
    if not API_KEY:
        sys.exit("FLASHLIVE_API_KEY env var not set")
    run()


if __name__ == "__main__":
    main()
