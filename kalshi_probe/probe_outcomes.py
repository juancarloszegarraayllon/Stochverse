"""Catalog Kalshi outcome shapes per (series_base, market_type) by
hitting /api/_debug/outcome_shapes.

Pretty-prints what shape outcomes take for each Kalshi market type
— how many outcomes, what classes the labels fall into (team-like
/ tie / yes_no / spread / total / ...), and which price fields are
populated. Drives the outcomes-shapes section of KALSHI_AUDIT.md
so /sports v2 can render outcomes deterministically.

Why this exists: Pic 1 missing-away-team showed our renderer is
shape-aware but the SHAPE inference is fragile. Without an explicit
catalog of what shapes exist, every renderer regression is a
surprise. This makes the inventory data, not tribal knowledge.

Run:
  python3 kalshi_probe/probe_outcomes.py --sport Soccer
  python3 kalshi_probe/probe_outcomes.py --base http://localhost:8000

Stdlib-only.
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
        "User-Agent": "stochverse-kalshi-outcomes/1.0",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="https://stochverse.com")
    parser.add_argument("--sport", default="")
    parser.add_argument("--limit", type=int, default=5000)
    args = parser.parse_args()

    qs = urllib.parse.urlencode({
        "sport": args.sport, "limit": str(args.limit),
    })
    url = f"{args.base.rstrip('/')}/api/_debug/outcome_shapes?{qs}"

    print("=" * 78)
    print("# Kalshi outcome shapes")
    print(f"# Source: {url}")
    print("=" * 78)

    try:
        data = http_get_json(url)
    except urllib.error.HTTPError as e:
        sys.exit(f"  HTTP {e.code}: {e.reason}")
    except Exception as e:
        sys.exit(f"  fetch failed: {e}")

    sport_lbl = data.get("sport_filter") or "all"
    sample_size = data.get("sample_size", 0)
    buckets = data.get("buckets") or []
    print(f"\nSample: {sample_size} records · sport={sport_lbl} · "
          f"buckets: {data.get('bucket_count', 0)}\n")

    if not buckets:
        print("  (no records — cache cold? try again in 30s)")
        return

    # Top section: bucket overview
    print("Buckets — most common outcome count + class mix")
    print("─" * 78)
    print(f"  {'series_base':<22} {'suffix':<10} {'market_type':<14} "
          f"{'rec':>5} {'n_out':>5}  classes")
    print("  " + "─" * 76)
    for b in buckets[:40]:
        base = b["series_base"][:20]
        suf = (b["suffix"] or "(plain)")[:8]
        mt = (b["market_type"] or "(headline)")[:12]
        rec = b["record_count"]
        most_n = b["most_common_outcome_count"]
        classes_short = ", ".join(
            f"{k}({v})" for k, v in
            sorted(b["label_classes"].items(), key=lambda x: -x[1])[:3]
        )
        if len(classes_short) > 36:
            classes_short = classes_short[:35] + "…"
        print(f"  {base:<22} {suf:<10} {mt:<14} {rec:>5} {most_n:>5}  {classes_short}")
    print()

    # Field-coverage histogram — which price fields are populated
    # across all outcomes in the sample
    print("Field coverage across all outcomes (% of outcomes with field set)")
    print("─" * 78)
    total_outcomes_by_field = {"prob": 0, "yes": 0, "no": 0, "ticker": 0}
    total_records = 0
    for b in buckets:
        for f in total_outcomes_by_field:
            if "field_coverage_pct" in b:
                # Approximate weighted average via record_count
                total_outcomes_by_field[f] += b["field_coverage_pct"][f] * b["record_count"]
        total_records += b["record_count"]
    if total_records:
        for f, w in total_outcomes_by_field.items():
            avg = round(w / total_records, 1)
            print(f"  {f:<10} {avg:>5}%  (population-weighted avg)")
    print()

    # Drill: top 5 buckets — full label samples
    print("Top 5 buckets — label samples per class")
    print("─" * 78)
    for b in buckets[:5]:
        print(f"\n  base: {b['series_base']}{b['suffix']}  "
              f"market_type='{b['market_type']}'  "
              f"({b['record_count']} records)")
        for cls, samples in (b.get("label_samples") or {}).items():
            print(f"    {cls:<14} → {samples}")
        ts = b.get("title_samples") or []
        if ts:
            print(f"    title samples:")
            for t in ts:
                print(f"      • {t}")
    print()


if __name__ == "__main__":
    main()
