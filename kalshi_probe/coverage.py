"""Generate KALSHI_API_COVERAGE.md by diffing kalshi-openapi.json
against backend code. Greps main.py / kalshi_ws.py for /trade-api/v2
path literals, SDK method calls, and WebSocket channel names to find
which Kalshi endpoints we actually use.

Run: python3 kalshi_probe/coverage.py > KALSHI_API_COVERAGE.md

Stdlib-only — same constraint as fl_probe scripts so it can run in
CI without extra deps. The OpenAPI file is JSON (already converted
from the YAML on docs.kalshi.com).
"""
import datetime
import json
import os
import re
import subprocess
import sys


# REST paths the backend reaches today. Three sources:
#   1. Direct path literals in main.py (signed-request helpers for
#      orderbook / trades / candlesticks / single-market detail).
#   2. SDK method calls via `kalshi_python_sync` — paginate() uses
#      client.get_events() which is GET /events under the hood.
#   3. WebSocket connection (separate from REST surface).
#
# When the backend is extended with new wrappers, add the literal
# here OR pick it up automatically via the path-grep below. The
# SDK_METHOD_TO_PATH map exists because SDK calls don't carry the
# raw path string in the source, so we can't grep them.
SDK_METHOD_TO_PATH = {
    "get_events":           "/events",
    "get_event":            "/events/{event_ticker}",
    "get_markets":          "/markets",
    "get_market":           "/markets/{ticker}",
    "get_series":           "/series/{series_ticker}",
    "get_trades":           "/markets/trades",
    "get_event_metadata":   "/events/{event_ticker}/metadata",
    "get_market_orderbook": "/markets/{ticker}/orderbook",
}

# WebSocket channels the backend subscribes to. Update when adding
# new on-demand subscriptions in kalshi_ws.py.
WS_CHANNELS_USED = {"ticker", "orderbook_delta", "trade"}


def _grep(cmd: str, cwd: str) -> str:
    return subprocess.run(cmd, cwd=cwd, shell=True,
                          capture_output=True, text=True).stdout


def _scan_backend(repo_root: str) -> tuple:
    """Returns (rest_paths_used, sdk_methods_used, ws_channels_used).

    rest_paths_used: set of normalized API paths (no /trade-api/v2
                     prefix, with literal {var} placeholders).
    sdk_methods_used: set of SDK method names found in source.
    ws_channels_used: set of channel name strings found subscribed.
    """
    # Direct path literals — match /trade-api/v2/* and strip prefix.
    raw_paths = _grep(
        "grep -hroE '/trade-api/v2/[A-Za-z0-9_/{}-]+' "
        "main.py kalshi_ws.py 2>/dev/null | sort -u",
        repo_root,
    )
    rest_paths_used: set = set()
    for line in raw_paths.strip().split("\n"):
        if not line:
            continue
        path = line[len("/trade-api/v2"):]
        # Normalize {var} placeholders to spec naming. Spec uses
        # {ticker}, {event_ticker}, {series_ticker}; backend code
        # uses {mk}, {series}. Rewrite for matching.
        normalized = path
        normalized = re.sub(r"\{mk\}",         "{ticker}", normalized)
        normalized = re.sub(r"\{series\}",     "{series_ticker}", normalized)
        rest_paths_used.add(normalized)

    # SDK method calls — `client.get_events(`, `.get_market(`, etc.
    raw_sdk = _grep(
        "grep -hoE '\\.(get|create|delete|update)_[a-z_]+\\(' "
        "main.py kalshi_ws.py 2>/dev/null | sort -u",
        repo_root,
    )
    sdk_methods_used: set = set()
    for line in raw_sdk.strip().split("\n"):
        if not line:
            continue
        m = re.match(r"\.([a-z_]+)\(", line)
        if m:
            sdk_methods_used.add(m.group(1))

    # WS channel literals from subscribe payloads in kalshi_ws.py.
    raw_ws = _grep(
        "grep -hoE 'channels\\s*=\\s*\\[[^]]+\\]' "
        "kalshi_ws.py 2>/dev/null",
        repo_root,
    )
    ws_channels_used: set = set(WS_CHANNELS_USED)  # known baseline
    for line in raw_ws.strip().split("\n"):
        for ch in re.findall(r'"([a-z_]+)"', line):
            ws_channels_used.add(ch)

    return rest_paths_used, sdk_methods_used, ws_channels_used


def _is_path_covered(path: str, rest_paths: set,
                     sdk_methods: set) -> bool:
    """A spec path counts as covered if either:
      - the literal path appears in source (with {var} placeholders
        normalized), OR
      - one of the SDK method names that maps to it appears in source.
    """
    if path in rest_paths:
        return True
    for method, mapped in SDK_METHOD_TO_PATH.items():
        if mapped == path and method in sdk_methods:
            return True
    return False


# Tag → bucket. Updated to match Kalshi's actual tag taxonomy.
# Buckets reflect intent for the prediction-market trading product:
#   MARKET_DATA: things we need to render markets / charts / order
#                books — the public-data surface our /sports + event
#                pages live on top of.
#   PORTFOLIO:   user-account flows (positions, orders, fills) — we
#                don't trade on behalf of users today, so most stay
#                uncovered until the trading flow ships.
#   COMMS:       notifications / FCM / announcements — separate
#                product surface, deferred.
#   META:        exchange status, search, user-data sync — small
#                utility endpoints, mostly skip-for-now.
MARKET_DATA = {"events", "market", "historical", "live-data",
               "multivariate", "structured-targets"}
PORTFOLIO   = {"portfolio", "orders", "order-groups", "account",
               "api-keys", "milestone", "incentive-programs"}
COMMS       = {"communications", "fcm"}
META        = {"exchange", "search"}


def main() -> None:
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec_path = os.path.join(repo_root, "kalshi-openapi.json")
    if not os.path.exists(spec_path):
        sys.exit(f"missing {spec_path} — fetch from "
                 "https://docs.kalshi.com/openapi.yaml and convert")

    with open(spec_path) as f:
        spec = json.load(f)

    rest_paths_used, sdk_methods_used, ws_channels_used = \
        _scan_backend(repo_root)

    # Walk the spec. Each (path, method) is one operation. Group
    # operations by tag for the per-section tables below.
    by_tag: dict = {}
    total_ops = 0
    total_covered = 0
    for path, ops in sorted(spec.get("paths", {}).items()):
        for method, op in ops.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            total_ops += 1
            tags = op.get("tags") or ["?"]
            tag = tags[0]
            covered = (method == "get"
                       and _is_path_covered(path, rest_paths_used,
                                            sdk_methods_used))
            if covered:
                total_covered += 1
            by_tag.setdefault(tag, []).append({
                "path":    path,
                "method":  method.upper(),
                "summary": op.get("summary", ""),
                "params":  [p["name"] for p in op.get("parameters", [])
                            if p.get("in") in ("query", "path")],
                "covered": covered,
            })

    def bucket_stats(tags: set) -> tuple:
        items = [it for tag in tags for it in by_tag.get(tag, [])]
        n = len(items)
        c = sum(1 for it in items if it["covered"])
        pct = round(100 * c / n) if n else 0
        return c, n, pct

    md_c, md_n, md_p = bucket_stats(MARKET_DATA)
    po_c, po_n, po_p = bucket_stats(PORTFOLIO)
    co_c, co_n, co_p = bucket_stats(COMMS)
    me_c, me_n, me_p = bucket_stats(META)

    def emoji(pct: int) -> str:
        if pct >= 80:
            return "⭐"
        if pct >= 50:
            return "🟡"
        return "🚧"

    print("# Kalshi API Coverage")
    print()
    print("> Generated by `python3 kalshi_probe/coverage.py` against")
    print("> `kalshi-openapi.json` and our backend (`main.py`,")
    print("> `kalshi_ws.py`). Re-run when adding new endpoints or")
    print("> after Kalshi ships spec updates.")
    print(">")
    print(f"> **Headline:** {total_covered}/{total_ops} Kalshi REST")
    print(f"> operations reached. WebSocket channels in use: "
          f"{', '.join(sorted(ws_channels_used))}.")
    print(f"> Last regenerated: {datetime.date.today()}.")
    print()
    print("## Strategic summary")
    print()
    print("Kalshi exposes a much wider surface than we touch today —")
    print("most of it is the trading / portfolio side, which we don't")
    print("light up because we don't place orders on behalf of users.")
    print("The market-data bucket is what powers /sports, event pages,")
    print("and price charts; that's where coverage actually matters.")
    print()
    print("| Bucket | Coverage | Status |")
    print("|---|---|---|")
    print(f"| **Market data (events, markets, history, live)** | "
          f"{md_c}/{md_n} = {md_p}% | "
          f"{emoji(md_p)} The surface our product sits on. Gaps "
          f"here directly limit what /sports + event pages can show. |")
    print(f"| **Portfolio (positions, orders, fills, account)** | "
          f"{po_c}/{po_n} = {po_p}% | "
          f"{emoji(po_p)} Trading flow not in scope until users place "
          f"orders through Stochverse. Reserved for post-launch. |")
    print(f"| **Communications (announcements, FCM)** | "
          f"{co_c}/{co_n} = {co_p}% | "
          f"{emoji(co_p)} Separate notification product. Deferred. |")
    print(f"| **Meta (exchange status, search)** | "
          f"{me_c}/{me_n} = {me_p}% | "
          f"{emoji(me_p)} Small utility surface; pick endpoints up "
          f"as needed (status banner, etc.). |")
    print()

    print("## REST endpoints reached today")
    print()
    print("Direct path literals grep'd from `main.py` / `kalshi_ws.py`,")
    print("plus SDK method calls (`kalshi_python_sync`) that resolve")
    print("to a known path. Anything not in this list is unreached.")
    print()
    print("| Source | Path / SDK method |")
    print("|---|---|")
    for p in sorted(rest_paths_used):
        print(f"| literal | `{p}` |")
    for m in sorted(sdk_methods_used):
        if m in SDK_METHOD_TO_PATH:
            print(f"| SDK | `client.{m}()` → `{SDK_METHOD_TO_PATH[m]}` |")
    print()

    print("## WebSocket channels reached today")
    print()
    print("Subscribed via `_subscribe_batch()` in `kalshi_ws.py`.")
    print("Endpoint: `wss://api.elections.kalshi.com/trade-api/ws/v2`.")
    print()
    print("| Channel | Use |")
    print("|---|---|")
    print("| `ticker` | Per-market last price + volume — drives every "
          "live YES/NO chip on /sports and event pages. |")
    print("| `orderbook_delta` | On-demand when an event modal opens "
          "the order-book panel; ref-counted unsubscribe when the last "
          "browser client disconnects. |")
    print("| `trade` | On-demand same as orderbook_delta — feeds the "
          "live trades tape on the event page. |")
    print()

    print("## Per-endpoint detail")
    print()
    for tag in sorted(by_tag):
        items = by_tag[tag]
        n_cov = sum(1 for it in items if it["covered"])
        print(f"### `{tag}` — {n_cov}/{len(items)} covered")
        print()
        print("| Status | Method | Path | Params | Summary |")
        print("|---|---|---|---|---|")
        for it in items:
            mark = "✅" if it["covered"] else "⚪"
            params = ", ".join(it["params"]) if it["params"] else "(none)"
            summary = it["summary"].replace("|", "\\|")
            print(f"| {mark} | `{it['method']}` | `{it['path']}` | "
                  f"{params} | {summary} |")
        print()

    print("## Gap analysis")
    print()
    print("### Market-data gaps (product-relevant)")
    print()
    print("These are the endpoints worth evaluating for the next round")
    print("of /sports + event-page features:")
    print()
    print("| Endpoint | Why it matters | Status |")
    print("|---|---|---|")
    print("| `GET /events/{event_ticker}` | Single-event detail — "
          "richer than what's in our cache record. Useful for the "
          "modal's metadata block. | Cache covers most fields today; "
          "evaluate when modal needs more. |")
    print("| `GET /events/{event_ticker}/metadata` | Settlement source "
          "+ rules text. Currently surfaced from cache where present, "
          "but spec exposes a dedicated endpoint. | Pick up if cache "
          "lacks fields we want. |")
    print("| `GET /series/{series_ticker}` | Series-level metadata "
          "(category, frequency). Already covered via SDK "
          "`client.get_series()` for warm-loop sport classification. "
          "| ✅ Covered. |")
    print("| `GET /markets/candlesticks` historical bulk | Already "
          "covered for the per-event chart via `/series/{}/markets/"
          "{}/candlesticks`. | ✅ Covered. |")
    print()
    print("### Portfolio gaps (trading flow)")
    print()
    print("Light up when implementing the user-trading path. Until")
    print("then these are intentionally uncovered — they require")
    print("user-scoped API credentials and order-management UI.")
    print()
    print("### Communications + meta")
    print()
    print("- `GET /exchange/status` is a one-line health check we")
    print("  could surface in a status banner — ~5 min to wire up if")
    print("  we want a 'Kalshi is degraded' UX.")
    print("- `GET /search/series` could replace some of our manual")
    print("  series-prefix mapping in `_SPORT_SERIES`, but the manual")
    print("  mapping is required anyway for the sport bucketing logic.")
    print()


if __name__ == "__main__":
    main()
