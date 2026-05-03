"""Kalshi vs FlashLive sports coverage diff.

Compares the universe of sports that Kalshi has prediction markets
for against FL's 42 sport_ids. Output: a 3-column table showing:

  - Kalshi-only sports — markets we'd lose if we filtered to FL events
  - Both — sports where we can do the rich Kalshi+FL card
  - FL-only sports — sports we can browse via FL but Kalshi doesn't trade

Strategic use: confirms the working assumption that FL is far more
comprehensive for sports than Kalshi. Surfaces any Kalshi-only
sports we shouldn't drop from the /sports browse (per Phase 2b
"don't leave Kalshi or Polymarket events behind" requirement).

Run:
  python3 kalshi_probe/sports_diff.py
  python3 kalshi_probe/sports_diff.py --base https://stochverse.com
  python3 kalshi_probe/sports_diff.py --base http://localhost:8000

Stdlib-only — same constraint as the FL probes.
"""
import argparse
import json
import sys
import urllib.error
import urllib.request


# FL sport_id → display name. From fl-openapi.json.
# Updated via:
#   python3 -c "import json; d=json.load(open('fl-openapi.json'));
#               # ... but we just hardcode this since FL sports list
#               # rarely changes (and the OpenAPI spec doesn't actually
#               # include the human names — they live in /v1/sports/list
#               # response which we'd need to fetch live)
FL_SPORTS = {
    1: "Soccer", 2: "Tennis", 3: "Basketball", 4: "Hockey",
    5: "American Football", 6: "Baseball", 7: "Handball",
    8: "Rugby Union", 9: "Floorball", 10: "Bandy", 11: "Futsal",
    12: "Volleyball", 13: "Cricket", 14: "Darts", 15: "Snooker",
    16: "Boxing", 17: "Beach Volleyball", 18: "Aussie Rules",
    19: "Rugby League", 21: "Badminton", 22: "Water Polo", 23: "Golf",
    24: "Field Hockey", 25: "Table Tennis", 26: "Beach Soccer",
    28: "MMA", 29: "Netball", 30: "Pesapallo", 31: "Motorsport",
    32: "Autoracing", 33: "Motoracing", 34: "Cycling",
    35: "Horse Racing", 36: "Esports", 37: "Winter Sports",
    38: "Ski Jumping", 39: "Alpine Skiing", 40: "Cross Country",
    41: "Biathlon", 42: "Kabaddi",
}

FL_NAME_NORMS = {v.upper().replace(" ", "_"): k for k, v in FL_SPORTS.items()}


def http_get_json(url: str, timeout: int = 30):
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "stochverse-sports-diff/1.0",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def fetch_kalshi_sports(base: str) -> dict:
    """GET /api/events and bucket by `_sport` field. Returns a
    dict: { sport_name (UPPER_SNAKE) → event_count }.
    """
    counts: dict = {}
    page = 1
    while True:
        url = f"{base.rstrip('/')}/api/events?page={page}"
        try:
            data = http_get_json(url)
        except Exception as e:
            sys.stderr.write(f"  warning: page {page} failed: {e}\n")
            break
        events = data.get("data") or data.get("events") or []
        if not events:
            break
        for ev in events:
            s = ev.get("_sport") or ev.get("sport") or ""
            if not s:
                continue
            key = s.upper().replace(" ", "_")
            counts[key] = counts.get(key, 0) + 1
        # crude pagination — keep going while results come back
        if not data.get("next") and not data.get("has_more"):
            break
        page += 1
        if page > 100:  # safety
            break
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="https://stochverse.com",
                        help="Stochverse base URL (default: prod)")
    args = parser.parse_args()

    print("=" * 72)
    print("# Kalshi vs FlashLive sports coverage diff")
    print(f"# Source: {args.base}/api/events")
    print("=" * 72)

    print("\nFetching Kalshi events…")
    kalshi_counts = fetch_kalshi_sports(args.base)
    print(f"  found {sum(kalshi_counts.values())} events across "
          f"{len(kalshi_counts)} sports")

    # Bucket: in both / Kalshi-only / FL-only
    fl_keys = set(FL_NAME_NORMS.keys())
    kalshi_keys = set(kalshi_counts.keys())

    both = sorted(fl_keys & kalshi_keys)
    kalshi_only = sorted(kalshi_keys - fl_keys)
    fl_only = sorted(fl_keys - kalshi_keys, key=lambda k: FL_NAME_NORMS[k])

    # ── Both ──
    print(f"\n{'═' * 72}")
    print(f"BOTH ({len(both)}) — Kalshi market + FL stats. "
          "Rich card on /sports browse.")
    print("═" * 72)
    print(f"  {'sport':<24} {'fl_id':<6} {'kalshi events':<15}")
    print(f"  {'-' * 24} {'-' * 6} {'-' * 15}")
    for k in both:
        sid = FL_NAME_NORMS.get(k, "")
        cnt = kalshi_counts.get(k, 0)
        print(f"  {k.replace('_', ' ').title():<24} {sid:<6} {cnt:<15}")

    # ── Kalshi-only ──
    print(f"\n{'═' * 72}")
    print(f"KALSHI-ONLY ({len(kalshi_only)}) — Kalshi has markets but FL "
          "doesn't carry the sport.")
    print("═" * 72)
    if kalshi_only:
        print(f"  {'sport':<24} {'kalshi events':<15}")
        print(f"  {'-' * 24} {'-' * 15}")
        for k in kalshi_only:
            cnt = kalshi_counts.get(k, 0)
            print(f"  {k.replace('_', ' ').title():<24} {cnt:<15}")
        print()
        print("  ⚠ Per Phase 2b design: do NOT drop these from the")
        print("    /sports browse. They render as Kalshi-only cards")
        print("    (no FL stats panel) until we add a stats source.")
    else:
        print("  (none — every Kalshi sport is also in FL)")

    # ── FL-only ──
    print(f"\n{'═' * 72}")
    print(f"FL-ONLY ({len(fl_only)}) — FL covers but Kalshi doesn't trade. "
          "Lite card placeholder until Polymarket / sportsbook integration.")
    print("═" * 72)
    print(f"  {'sport':<24} {'fl_id':<6}")
    print(f"  {'-' * 24} {'-' * 6}")
    for k in fl_only:
        sid = FL_NAME_NORMS.get(k, "")
        print(f"  {k.replace('_', ' ').title():<24} {sid:<6}")

    # ── Summary ──
    print(f"\n{'═' * 72}")
    print("SUMMARY")
    print("═" * 72)
    total_universe = len(fl_keys | kalshi_keys)
    print(f"  Total sports across both platforms: {total_universe}")
    print(f"  Both:        {len(both):>3}  ({100*len(both)//total_universe}%)")
    print(f"  Kalshi-only: {len(kalshi_only):>3}  "
          f"({100*len(kalshi_only)//total_universe}%)")
    print(f"  FL-only:     {len(fl_only):>3}  "
          f"({100*len(fl_only)//total_universe}%)")
    print()
    print(f"  Kalshi catalogues {len(kalshi_keys)} sports.")
    print(f"  FL catalogues {len(fl_keys)} sports.")
    print(f"  FL is {'more' if len(fl_keys) > len(kalshi_keys) else 'less'}"
          f" comprehensive ({len(fl_keys)} vs {len(kalshi_keys)} sport_ids).")


if __name__ == "__main__":
    main()
