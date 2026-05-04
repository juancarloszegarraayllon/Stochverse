"""Catalog Kalshi cache record schema by hitting /api/_debug/cache_schema.

Walks every key observed across cache records for a sport, with
frequency, type, and sample values. Output is the source data for
the cache-record-schema section of KALSHI_API_COVERAGE.md.

Why this exists: yesterday's `_live_state` discovery cost us ~3 hours
of "fix-and-pray" debugging. The cache record's actual shape was
opaque because we'd never enumerated it. This probe makes that
opacity an explicit document so the next person doesn't have the
same surprise.

Run:
  python3 kalshi_probe/probe_cache_schema.py
  python3 kalshi_probe/probe_cache_schema.py --base http://localhost:8000
  python3 kalshi_probe/probe_cache_schema.py --sport Basketball --limit 500

Stdlib-only — same constraint as fl_probe scripts so it runs in CI.
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
        "User-Agent": "stochverse-kalshi-cache-schema/1.0",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def fmt_types(types: dict) -> str:
    """'str: 198, NoneType: 2' → ordered by count descending."""
    parts = sorted(types.items(), key=lambda x: -x[1])
    return ", ".join(f"{t}({c})" for t, c in parts)


def fmt_sample(samples: list, max_chars: int = 60) -> str:
    """Truncate / quote sample values for the table."""
    if not samples:
        return "—"
    out = []
    for s in samples:
        sv = json.dumps(s) if not isinstance(s, str) else s
        if len(sv) > max_chars:
            sv = sv[:max_chars - 1] + "…"
        out.append(sv)
    return " · ".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="https://stochverse.com",
                        help="Stochverse base URL")
    parser.add_argument("--sport", default="",
                        help="Sport name filter (Soccer / Basketball / "
                             "Tennis / Hockey / Baseball / Football / "
                             "...). Empty = all sports.")
    parser.add_argument("--limit", type=int, default=200,
                        help="Max records to inspect (default 200, max 1000)")
    args = parser.parse_args()

    qs = urllib.parse.urlencode({
        "sport": args.sport,
        "limit": str(args.limit),
        "include_examples": "1",
    })
    url = f"{args.base.rstrip('/')}/api/_debug/cache_schema?{qs}"

    print("=" * 78)
    print("# Kalshi cache record schema")
    print(f"# Source: {url}")
    print("=" * 78)

    try:
        data = http_get_json(url)
    except urllib.error.HTTPError as e:
        sys.exit(f"  HTTP {e.code}: {e.reason} — is /api/_debug/cache_schema deployed?")
    except Exception as e:
        sys.exit(f"  fetch failed: {e}")

    sport_lbl = data.get("sport_filter") or "all"
    sample_size = data.get("sample_size", 0)
    fields = data.get("fields") or []
    print(f"\nSample: {sample_size} records · sport={sport_lbl}\n")

    if not fields:
        print("  (no records — cache might be cold; try again in 30s)")
        return

    # Group fields by their underscore-prefix convention. Kalshi cache
    # keys split into two camps:
    #   - normal API fields (title, event_ticker, series_ticker, ...)
    #   - underscore-prefixed enrichment fields (_sport, _exp_dt, ...)
    # Putting them in separate buckets surfaces the distinction.
    api_fields = [f for f in fields if not f["key"].startswith("_")]
    enriched = [f for f in fields if f["key"].startswith("_")]

    def emit(title: str, rows: list) -> None:
        if not rows:
            return
        print(title)
        print("  " + "─" * 76)
        print(f"  {'key':<30} {'%':>6}  {'types':<32}  samples")
        print(f"  {'-' * 30} {'-' * 6}  {'-' * 32}  {'-' * 30}")
        for f in rows:
            key = f["key"]
            pct = f"{f['present_pct']}%"
            types = fmt_types(f["types"])
            sample = fmt_sample(f.get("samples") or [])
            if len(types) > 32:
                types = types[:31] + "…"
            print(f"  {key:<30} {pct:>6}  {types:<32}  {sample}")
        print()

    emit("RAW API FIELDS (from Kalshi events / markets endpoints)",
         api_fields)
    emit("ENRICHMENT FIELDS (added by our cache builder)",
         enriched)

    # Quick summary: what's universally populated vs sometimes-null.
    universal = [f for f in fields if f["present_pct"] >= 99]
    rare = [f for f in fields if f["present_pct"] < 50]
    print("─" * 78)
    print("SUMMARY")
    print("─" * 78)
    print(f"  Universal (≥99% records):  {len(universal)} fields")
    print(f"  Sometimes (50-99%):        {len(fields) - len(universal) - len(rare)} fields")
    print(f"  Rare (<50%):               {len(rare)} fields")
    print()
    print("  Notable absences from cache schema (we discovered the hard way):")
    print("    - _live_state             /api/events computes it per request")
    print("    - aggregate_home/away     in _live_state, NOT top-level (yesterday's bug)")
    print("    - series_home_wins/away   same — _live_state-only")
    print("    - display_clock           same — _live_state-only")
    print()
    print("  Confirm by looking for those keys in the table above. If they")
    print("  appear, the cache builder DID populate them. If absent, that")
    print("  field is request-time-only.")


if __name__ == "__main__":
    main()
