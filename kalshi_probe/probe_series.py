"""Catalog Kalshi series_ticker values per sport by hitting
/api/_debug/series_tickers.

Groups variants of the same base together (KXUCL / KXUCLGAME /
KXUCLSPREAD / KXUCLTOTAL / KXUCLBTTS / ...) so we can see at a
glance which sub-markets each league exposes. Output drives the
series-tickers section of KALSHI_API_COVERAGE.md.

Why this exists: sub-market suffixes (TCORNERS vs CORNERS,
ADVANCE for two-leg ties, 1H for first-half) bit us during the
matchup-key dedup work — we kept finding new ones in production.
This makes the full inventory explicit so the next addition is
deliberate, not a surprise.

Run:
  python3 kalshi_probe/probe_series.py
  python3 kalshi_probe/probe_series.py --sport Soccer
  python3 kalshi_probe/probe_series.py --base http://localhost:8000

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
        "User-Agent": "stochverse-kalshi-series/1.0",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="https://stochverse.com",
                        help="Stochverse base URL")
    parser.add_argument("--sport", default="",
                        help="Sport filter (empty = all sports)")
    parser.add_argument("--limit", type=int, default=5000,
                        help="Max records to inspect (max 10000)")
    args = parser.parse_args()

    qs = urllib.parse.urlencode({
        "sport": args.sport, "limit": str(args.limit),
    })
    url = f"{args.base.rstrip('/')}/api/_debug/series_tickers?{qs}"

    print("=" * 78)
    print("# Kalshi series tickers")
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
    bases = data.get("bases") or []
    print(f"\nSample: {sample_size} records · sport={sport_lbl} · "
          f"bases: {data.get('base_count', 0)} · "
          f"distinct tickers: {data.get('ticker_count', 0)}\n")

    if not bases:
        print("  (no records — cache cold? try again in 30s)")
        return

    # Top section: bases by total record count.
    print("Top bases by record count")
    print("─" * 78)
    print(f"  {'base':<28} {'records':>8}  suffixes")
    print("  " + "─" * 76)
    for b in bases[:30]:
        sufs = ", ".join(s if s else "(plain)" for s in b["suffixes"])
        if len(sufs) > 38:
            sufs = sufs[:37] + "…"
        base_disp = b["base"]
        if len(base_disp) > 26:
            base_disp = base_disp[:25] + "…"
        print(f"  {base_disp:<28} {b['total']:>8}  {sufs}")
    print()

    # Suffix histogram across the whole sample.
    suffix_totals: dict = {}
    for b in bases:
        for v in b["variants"]:
            suf = v["suffix"] or "(plain)"
            suffix_totals[suf] = suffix_totals.get(suf, 0) + v["count"]
    print("Suffix histogram (records per market-type marker)")
    print("─" * 78)
    print(f"  {'suffix':<14} {'records':>8}  meaning")
    print("  " + "─" * 76)
    SUFFIX_MEANING = {
        "(plain)":   "season-long / outright",
        "GAME":      "per-fixture h2h (e.g. KXEPLGAME)",
        "MATCH":     "per-fixture h2h (variant — same as GAME)",
        "SPREAD":    "spread / handicap",
        "TOTAL":     "totals / over-under",
        "BTTS":      "both teams to score (soccer)",
        "1H":        "first-half markets",
        "1Q":        "first-quarter markets",
        "2Q":        "second-quarter markets",
        "3Q":        "third-quarter markets",
        "4Q":        "fourth-quarter markets",
        "HALFTIME":  "halftime score",
        "ADVANCE":   "two-leg tie advancement (soccer cups)",
        "CORNERS":   "corner-count totals (soccer)",
        "TCORNERS":  "team corner-count (soccer)",
        "OUTRIGHT":  "season-long futures (explicit suffix)",
    }
    for suf, n in sorted(suffix_totals.items(), key=lambda x: -x[1]):
        meaning = SUFFIX_MEANING.get(suf, "?")
        print(f"  {suf:<14} {n:>8}  {meaning}")
    print()

    # Drill-down: top 5 bases — show every variant.
    print("Top 5 bases — variant detail")
    print("─" * 78)
    for b in bases[:5]:
        print(f"\n  base: {b['base']}  ({b['total']} records)")
        for v in b["variants"]:
            suf = v["suffix"] or "(plain)"
            print(f"    {v['series_ticker']:<26} suf={suf:<10} "
                  f"count={v['count']}")
            for ex in v["examples"][:1]:
                print(f"      ↳ {ex}")
    print()


if __name__ == "__main__":
    main()
