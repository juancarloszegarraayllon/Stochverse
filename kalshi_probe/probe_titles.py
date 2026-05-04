"""Catalog Kalshi title shapes per sport by hitting
/api/_debug/title_shapes.

Pretty-prints the long tail of head-to-head + sub-market title
patterns Kalshi ships, with frequency counts and example series
tickers. Output drives the title-shapes section of
KALSHI_API_COVERAGE.md so the parser whitelist stays in sync.

Run:
  python3 kalshi_probe/probe_titles.py
  python3 kalshi_probe/probe_titles.py --sport Basketball
  python3 kalshi_probe/probe_titles.py --base http://localhost:8000

Stdlib-only — same constraint as the other probe scripts.
"""
import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request


def http_get_json(url: str, timeout: int = 30):
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "stochverse-kalshi-titles/1.0",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="https://stochverse.com",
                        help="Stochverse base URL")
    parser.add_argument("--sport", default="",
                        help="Sport name filter (Soccer / Basketball / "
                             "Tennis / Hockey / Baseball / Football / "
                             "...). Empty = all sports.")
    parser.add_argument("--limit", type=int, default=2000,
                        help="Max records to inspect (max 5000)")
    args = parser.parse_args()

    qs = urllib.parse.urlencode({
        "sport": args.sport, "limit": str(args.limit),
    })
    url = f"{args.base.rstrip('/')}/api/_debug/title_shapes?{qs}"

    print("=" * 78)
    print("# Kalshi title shapes")
    print(f"# Source: {url}")
    print("=" * 78)

    try:
        data = http_get_json(url)
    except urllib.error.HTTPError as e:
        sys.exit(f"  HTTP {e.code}: {e.reason} — is the endpoint deployed?")
    except Exception as e:
        sys.exit(f"  fetch failed: {e}")

    sport_lbl = data.get("sport_filter") or "all"
    sample_size = data.get("sample_size", 0)
    shapes = data.get("shapes") or []
    shape_count = data.get("shape_count", 0)
    print(f"\nSample: {sample_size} records · sport={sport_lbl} · "
          f"distinct shapes: {shape_count}\n")

    if not shapes:
        print("  (no records — cache cold? try again in 30s)")
        return

    print(f"  {'shape':<54} {'count':>6} {'%':>5}")
    print("  " + "─" * 76)
    for s in shapes:
        shape = s["shape"]
        if len(shape) > 52:
            shape = shape[:51] + "…"
        print(f"  {shape:<54} {s['count']:>6} {s['pct']:>4}%")
    print()

    # Drill-down: top 8 shapes get an example title + series.
    print("Top shapes — example titles + series tickers")
    print("─" * 78)
    for s in shapes[:8]:
        print(f"\n  {s['shape']}  ({s['count']} records)")
        for ex in s["examples"][:3]:
            print(f"    title:  {ex}")
        ser = ", ".join(s["series_examples"][:6])
        if ser:
            print(f"    series: {ser}")
    print()


if __name__ == "__main__":
    main()
