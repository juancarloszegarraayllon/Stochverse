"""Catalog Kalshi event_ticker grammar per series_base by hitting
/api/_debug/ticker_grammar.

Decodes how Kalshi encodes (date, teams, handle) in event tickers
so /sports v2 can derive a deterministic fixture identity from
the ticker alone — no team-name parsing, no fuzzy matching.

Patterns probed:
  G1  {series}-{YYMMDD}{TEAMS}      KXUCLGAME-26MAY05ARSATM
  G2  {series}-{YYMMDD}-{TEAMS}     KXNFLGAME-26JAN05-DETPHI
  G3  {series}-{YYYY}               KXNCAACHAMP-2026
  G4  {series}-{NN}                 KXTEAMSINUCL-26
  G5  {series}-{HANDLE}             KXJOINRONALDO-PSG
  G6  {series}-{INT-LETTERS}        KXELONMARS-99-AAA

Why this exists: today's pairing layer infers fixture identity by
parsing TITLES + matching TEAMS — fragile at every step. The
ticker is structured data Kalshi already emits. If we can decode
it deterministically we sidestep title/team fuzziness entirely.

Run:
  python3 kalshi_probe/probe_tickers.py --sport Soccer
  python3 kalshi_probe/probe_tickers.py --sport Basketball

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
        "User-Agent": "stochverse-kalshi-tickers/1.0",
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
    url = f"{args.base.rstrip('/')}/api/_debug/ticker_grammar?{qs}"

    print("=" * 78)
    print("# Kalshi event_ticker grammar")
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
    bases = data.get("bases") or []
    print(f"\nSample: {sample_size} records · sport={sport_lbl} · "
          f"bases: {data.get('base_count', 0)}\n")

    if not bases:
        print("  (no records — cache cold? try again in 30s)")
        return

    # Top section — bases by ticker count + their pattern mix
    print("Bases by ticker count + dominant grammar pattern")
    print("─" * 78)
    print(f"  {'base':<26} {'total':>6}  patterns (top 3)")
    print("  " + "─" * 76)
    for b in bases[:40]:
        patterns = sorted(b["patterns"].items(), key=lambda x: -x[1])[:3]
        ptn_str = ", ".join(f"{k}({v})" for k, v in patterns) or "(none)"
        if len(ptn_str) > 42:
            ptn_str = ptn_str[:41] + "…"
        base = b["base"][:24]
        print(f"  {base:<26} {b['total']:>6}  {ptn_str}")
    print()

    # Pattern histogram across all bases
    pat_totals: dict = {}
    unparsed_total = 0
    for b in bases:
        for p, n in b["patterns"].items():
            pat_totals[p] = pat_totals.get(p, 0) + n
        unparsed_total += len(b.get("unparsed", []))
    print("Grammar pattern distribution (records per pattern)")
    print("─" * 78)
    print(f"  {'pattern':<22} {'records':>8}  meaning")
    print("  " + "─" * 76)
    PATTERN_MEANING = {
        "G1_date_teams":       "{YYMMDD}{TEAMS} — per-fixture h2h",
        "G2_date_dash_teams":  "{YYMMDD}-{TEAMS} — variant per-fixture",
        "G3_year":             "{YYYY} — year-long futures",
        "G4_short_int":        "{NN} — sequence-numbered outright",
        "G5_handle":           "{HANDLE} — alpha-only handle",
        "G6_handle_dash":      "{N-LETTERS} — handle with dash",
    }
    for p, n in sorted(pat_totals.items(), key=lambda x: -x[1]):
        meaning = PATTERN_MEANING.get(p, "?")
        print(f"  {p:<22} {n:>8}  {meaning}")
    print(f"  (unparsed across all bases: ~{unparsed_total} samples)")
    print()

    # Drill — top 5 bases — show example ticker + decoded groups
    print("Top 5 bases — decoded examples")
    print("─" * 78)
    for b in bases[:5]:
        print(f"\n  base: {b['base']}  ({b['total']} tickers)")
        for pname, exs in (b.get("examples") or {}).items():
            for ex in exs[:2]:
                groups_disp = (" → groups=" + str(ex["groups"])) if ex.get("groups") else ""
                print(f"    {pname:<22} {ex['ticker']}{groups_disp}")
        if b.get("team_abbr_lengths"):
            tal = ", ".join(f"len={k}: {v}" for k, v in
                            sorted(b["team_abbr_lengths"].items()))
            print(f"    team-abbr block lengths: {tal}")
        if b.get("unparsed"):
            print(f"    unparsed examples:")
            for u in b["unparsed"][:3]:
                print(f"      • {u['ticker']:<40}  suffix='{u['suffix']}'")
    print()


if __name__ == "__main__":
    main()
