"""Tests for the WS fanout instrumentation (Investigation #42 —
egress attribution: origin HTTP proved ~nil; Kalshi upstream is
655 msg/s; the drinker side of the fanout is the missing bytes).

This PR adds:
  1. Per-connection counters on BrowserSubscriber (bytes_sent,
     msgs_sent, msgs_dropped, connected_at, ip_class, ua_class).
     Always-on, cheap.
  2. Global per-channel byte + msg totals in kalshi_ws.
  3. Ring buffer of connect/disconnect events for reconnect-storm
     detection.
  4. browser_fanout_snapshot() helper surfaced on /api/ws_status.
  5. Env-gated (LOG_WS_FANOUT_EVENTS=1) per-connect / per-subscribe
     / per-disconnect log lines from the /ws/prices handler.

Design-hole callout: there is currently NO CAP on tickers-per-
connection or connections-per-IP. A single client can subscribe to
all ~91k tickers. See PR body for the recommended follow-up cap PR
(deliberately out of scope for this attribution-only kit).
"""
from __future__ import annotations

import asyncio
import inspect
import logging as _logging
from unittest.mock import patch

import pytest

# Import order matters: main.py sets up the import environment
# (analytics, admin router, etc.) that lets kalshi_ws' cryptography
# import succeed cleanly under pytest. Bare `import kalshi_ws` at
# module load time panics on cffi backend init; importing main first
# resolves it. Same trick as tests/test_response_size_logger.py.
import main  # noqa: F401  (ensures downstream kalshi_ws imports work)


# ── BrowserSubscriber counters ──────────────────────────────────

def test_browser_subscriber_gains_instrumentation_fields():
    """BrowserSubscriber MUST carry connected_at, bytes_sent,
    msgs_sent, msgs_dropped, ip_class, ua_class. Regression to
    the old (tickers, queue)-only shape leaves the drinker side
    invisible again."""
    from kalshi_ws import BrowserSubscriber
    sub = BrowserSubscriber(ip_class="abc12345", ua_class="browser")
    for attr in ("connected_at", "bytes_sent", "msgs_sent",
                 "msgs_dropped", "ip_class", "ua_class"):
        assert hasattr(sub, attr), (
            f"BrowserSubscriber missing {attr!r} — instrumentation "
            f"regressed"
        )
    assert sub.bytes_sent == 0
    assert sub.msgs_sent == 0
    assert sub.msgs_dropped == 0
    assert sub.ip_class == "abc12345"
    assert sub.ua_class == "browser"
    assert sub.connected_at > 0


def test_browser_subscriber_defaults_are_unknown():
    """When no ip/ua supplied at construct time, both default to
    'unknown' — never None (would break log formatting downstream)."""
    from kalshi_ws import BrowserSubscriber
    sub = BrowserSubscriber()
    assert sub.ip_class == "unknown"
    assert sub.ua_class == "unknown"


# ── _broadcast_to_browsers counter increments ───────────────────

def test_broadcast_increments_bytes_and_msgs_on_successful_enqueue():
    """The success path MUST bump sub.bytes_sent by the payload
    size (JSON-encoded) AND sub.msgs_sent by 1. Also bumps the
    per-channel global counters."""
    import kalshi_ws
    from kalshi_ws import (
        BrowserSubscriber, register_browser, unregister_browser,
        _broadcast_to_browsers,
    )
    # Reset globals so test is isolated.
    kalshi_ws._bytes_sent_by_channel = {"price": 0, "orderbook_delta": 0, "trade": 0}
    kalshi_ws._msgs_sent_by_channel = {"price": 0, "orderbook_delta": 0, "trade": 0}
    sub = BrowserSubscriber(tickers={"KX-A"})
    try:
        register_browser(sub)
        _broadcast_to_browsers("KX-A", {"yes_bid": 87, "yes_ask": 88}, "price")
        assert sub.msgs_sent == 1
        assert sub.bytes_sent > 0
        assert kalshi_ws._msgs_sent_by_channel["price"] == 1
        assert kalshi_ws._bytes_sent_by_channel["price"] == sub.bytes_sent
    finally:
        unregister_browser(sub)


def test_broadcast_skips_subscribers_without_matching_ticker():
    """A subscriber's counters MUST NOT bump for tickers it doesn't
    subscribe to. Regression here would over-count and make the
    top-N view misleading."""
    import kalshi_ws
    from kalshi_ws import (
        BrowserSubscriber, register_browser, unregister_browser,
        _broadcast_to_browsers,
    )
    kalshi_ws._bytes_sent_by_channel = {"price": 0, "orderbook_delta": 0, "trade": 0}
    kalshi_ws._msgs_sent_by_channel = {"price": 0, "orderbook_delta": 0, "trade": 0}
    sub_a = BrowserSubscriber(tickers={"KX-A"})
    sub_b = BrowserSubscriber(tickers={"KX-B"})
    try:
        register_browser(sub_a)
        register_browser(sub_b)
        _broadcast_to_browsers("KX-A", {"yes_bid": 87}, "price")
        assert sub_a.msgs_sent == 1
        assert sub_b.msgs_sent == 0
        assert sub_b.bytes_sent == 0
    finally:
        unregister_browser(sub_a)
        unregister_browser(sub_b)


def test_broadcast_increments_drops_on_queue_full():
    """Slow-consumer protection: full queue drops MUST bump
    msgs_dropped and MUST NOT bump bytes_sent / global counters
    (only successful enqueues count)."""
    import kalshi_ws
    from kalshi_ws import (
        BrowserSubscriber, register_browser, unregister_browser,
        _broadcast_to_browsers,
    )
    kalshi_ws._bytes_sent_by_channel = {"price": 0, "orderbook_delta": 0, "trade": 0}
    kalshi_ws._msgs_sent_by_channel = {"price": 0, "orderbook_delta": 0, "trade": 0}
    sub = BrowserSubscriber(tickers={"KX-A"})
    # Fill the queue to capacity (500) so the next put raises QueueFull.
    for _ in range(500):
        sub.queue.put_nowait({"filler": 1})
    try:
        register_browser(sub)
        _broadcast_to_browsers("KX-A", {"yes_bid": 87}, "price")
        assert sub.msgs_dropped == 1
        assert sub.msgs_sent == 0
        assert sub.bytes_sent == 0
        assert kalshi_ws._bytes_sent_by_channel["price"] == 0
    finally:
        unregister_browser(sub)


def test_broadcast_supports_all_three_channels():
    """price, orderbook_delta, trade must all attribute to their own
    channel bucket in _bytes_sent_by_channel."""
    import kalshi_ws
    from kalshi_ws import (
        BrowserSubscriber, register_browser, unregister_browser,
        _broadcast_to_browsers,
    )
    kalshi_ws._bytes_sent_by_channel = {"price": 0, "orderbook_delta": 0, "trade": 0}
    kalshi_ws._msgs_sent_by_channel = {"price": 0, "orderbook_delta": 0, "trade": 0}
    sub = BrowserSubscriber(tickers={"KX-A"})
    try:
        register_browser(sub)
        _broadcast_to_browsers("KX-A", {"yes_bid": 87}, "price")
        _broadcast_to_browsers("KX-A", {"yes": [[50, 100]]}, "orderbook_delta")
        _broadcast_to_browsers("KX-A", {"price": 87, "count": 5}, "trade")
        assert kalshi_ws._msgs_sent_by_channel["price"] == 1
        assert kalshi_ws._msgs_sent_by_channel["orderbook_delta"] == 1
        assert kalshi_ws._msgs_sent_by_channel["trade"] == 1
        # All three channels have positive bytes now.
        for ch in ("price", "orderbook_delta", "trade"):
            assert kalshi_ws._bytes_sent_by_channel[ch] > 0, (
                f"channel {ch!r} bytes_sent = 0 after broadcast"
            )
    finally:
        unregister_browser(sub)


# ── register / unregister emit events ────────────────────────────

def test_register_and_unregister_emit_ring_buffer_events():
    """The reconnect-storm detection loop reads the connect/
    disconnect event ring. Both hooks MUST append. Disconnect
    entries carry age_s / tickers / bytes_sent."""
    import kalshi_ws
    from kalshi_ws import (
        BrowserSubscriber, register_browser, unregister_browser,
    )
    kalshi_ws._ws_fanout_events.clear()
    sub = BrowserSubscriber(tickers={"KX-A"}, ip_class="abc12345", ua_class="bot")
    register_browser(sub)
    sub.bytes_sent = 1234
    sub.msgs_sent = 42
    unregister_browser(sub)
    assert len(kalshi_ws._ws_fanout_events) == 2
    connect_evt, disconnect_evt = list(kalshi_ws._ws_fanout_events)
    assert connect_evt["event"] == "connect"
    assert connect_evt["ip_class"] == "abc12345"
    assert connect_evt["ua_class"] == "bot"
    assert disconnect_evt["event"] == "disconnect"
    assert disconnect_evt["bytes_sent"] == 1234
    assert disconnect_evt["msgs_sent"] == 42
    assert disconnect_evt["tickers"] == 1
    assert "age_s" in disconnect_evt


# ── browser_fanout_snapshot ──────────────────────────────────────

def test_browser_fanout_snapshot_has_expected_shape():
    """Regression guard for the /api/ws_status browser_fanout key —
    operator reads this shape."""
    from kalshi_ws import browser_fanout_snapshot
    snap = browser_fanout_snapshot()
    for key in (
        "active_connections", "bytes_sent_by_channel",
        "msgs_sent_by_channel", "top_subscribers",
        "max_tickers_per_conn", "reconnects_last_1h", "recent_events",
    ):
        assert key in snap, f"browser_fanout_snapshot missing key {key!r}"


def test_browser_fanout_snapshot_ranks_top_subscribers_by_bytes_sent():
    """Top-N is ordered DESC by bytes_sent so operator eyeballs
    the fattest drinker first. Regression to unordered breaks
    attribution UX."""
    import kalshi_ws
    from kalshi_ws import (
        BrowserSubscriber, register_browser, unregister_browser,
        browser_fanout_snapshot,
    )
    sub_small = BrowserSubscriber(tickers={"KX-A"}, ip_class="aaa")
    sub_small.bytes_sent = 100
    sub_big = BrowserSubscriber(tickers={"KX-B"}, ip_class="bbb")
    sub_big.bytes_sent = 50000
    try:
        register_browser(sub_small)
        register_browser(sub_big)
        snap = browser_fanout_snapshot(top_n=2)
        assert len(snap["top_subscribers"]) == 2
        assert snap["top_subscribers"][0]["bytes_sent"] == 50000
        assert snap["top_subscribers"][0]["ip_class"] == "bbb"
        assert snap["top_subscribers"][1]["bytes_sent"] == 100
    finally:
        unregister_browser(sub_small)
        unregister_browser(sub_big)


def test_browser_fanout_snapshot_max_tickers_per_conn_spots_scraper():
    """max_tickers_per_conn is the single scalar that answers 'is
    somebody subscribed to all 91k tickers?' — the design-hole
    indicator this whole PR is built to surface."""
    from kalshi_ws import (
        BrowserSubscriber, register_browser, unregister_browser,
        browser_fanout_snapshot,
    )
    small = BrowserSubscriber(tickers={f"KX-{i}" for i in range(10)})
    huge = BrowserSubscriber(tickers={f"KX-{i}" for i in range(5000)})
    try:
        register_browser(small)
        register_browser(huge)
        snap = browser_fanout_snapshot()
        assert snap["max_tickers_per_conn"] == 5000
    finally:
        unregister_browser(small)
        unregister_browser(huge)


# ── /api/ws_status wiring ────────────────────────────────────────

def test_ws_status_endpoint_exposes_browser_fanout_key():
    """/api/ws_status MUST include the browser_fanout key. Regression
    hides the drinker side again."""
    import main
    src = inspect.getsource(main.ws_status)
    assert '"browser_fanout"' in src, (
        "/api/ws_status no longer exposes browser_fanout"
    )
    assert "browser_fanout_snapshot" in src, (
        "/api/ws_status does not call browser_fanout_snapshot"
    )


# ── main.py handler wiring + env gate ────────────────────────────

def test_ws_prices_handler_captures_ip_and_ua():
    """The /ws/prices handler MUST extract X-Forwarded-For + UA on
    accept, hash IP to a class, and pass both to BrowserSubscriber.
    Regression makes every connection show as 'unknown/unknown' in
    the snapshot."""
    import main
    src = inspect.getsource(main.ws_prices)
    assert "x-forwarded-for" in src.lower(), (
        "/ws/prices doesn't read X-Forwarded-For"
    )
    assert "_hash_ip_class(" in src, (
        "/ws/prices doesn't hash the client IP"
    )
    assert "_classify_ua(" in src, (
        "/ws/prices doesn't classify the UA"
    )
    assert "ip_class=_ip_class" in src and "ua_class=_ua_class" in src, (
        "captured ip/ua not threaded into BrowserSubscriber constructor"
    )


def test_log_ws_fanout_events_defaults_off():
    """Dark launch — regression to default-on doubles log volume
    when active connection count is high."""
    import main
    src = inspect.getsource(main)
    assert 'os.environ.get(\n    "LOG_WS_FANOUT_EVENTS", "0",\n)' in src or \
           'os.environ.get("LOG_WS_FANOUT_EVENTS", "0")' in src, (
        "LOG_WS_FANOUT_EVENTS default is not '0'"
    )


def test_ws_prices_disconnect_log_captures_stats_before_unregister():
    """The disconnect log MUST read sub counters BEFORE the
    finally-block unregister_browser drops the reference. There
    are multiple `unregister_browser(sub)` in the handler (error
    paths for the initial hello + the finally); the ORDER we care
    about is capture-before-unregister-in-the-same-block."""
    import main
    src = inspect.getsource(main.ws_prices)
    idx_capture = src.find("_bytes_sent = sub.bytes_sent")
    assert idx_capture > 0, "disconnect stat capture missing"
    # The next unregister_browser AFTER the capture is the finally-
    # block one — that's the one that must come AFTER capture.
    idx_unregister_after = src.find("unregister_browser(sub)", idx_capture)
    assert idx_unregister_after > 0, (
        "no unregister_browser after the stat capture — capture is "
        "orphaned"
    )
    assert idx_capture < idx_unregister_after, (
        "disconnect log captures stats AFTER unregister — regression: "
        "logs would show zeros for bytes_sent / msgs_sent"
    )


# ── _hash_ip_class ───────────────────────────────────────────────

@pytest.mark.parametrize("ip,expected_len", [
    ("1.2.3.4", 8),                     # IPv4
    ("192.168.1.100", 8),               # IPv4
    ("2001:db8::1", 8),                 # IPv6 shortened
    ("2001:0db8:0000:0000:0000:0000:0000:0001", 8),  # IPv6 expanded
    ("", 0),                            # empty → unknown (len('unknown') != 8, so we check separately)
    ("garbage-not-an-ip", 8),           # unparseable → still hashed (best-effort)
])
def test_hash_ip_class_returns_short_stable_id(ip, expected_len):
    """Same input → same output (stable). Empty → 'unknown'.
    All others → 8-char hex hash."""
    from main import _hash_ip_class
    result = _hash_ip_class(ip)
    if not ip:
        assert result == "unknown"
    else:
        assert len(result) == expected_len
        # Stability: same input, same output.
        assert result == _hash_ip_class(ip)


def test_hash_ip_class_ipv4_slash24_bucketing():
    """Two IPs in the same /24 hash to the SAME bucket (aggregation
    across a natural network boundary — spot a scraper subnet)."""
    from main import _hash_ip_class
    a = _hash_ip_class("192.168.1.5")
    b = _hash_ip_class("192.168.1.99")
    c = _hash_ip_class("192.168.2.5")
    assert a == b, "same /24 didn't bucket together"
    assert a != c, "different /24 collapsed to same bucket"


def test_hash_ip_class_ipv6_slash48_bucketing():
    """Two IPv6 addresses in the same /48 hash to the SAME bucket."""
    from main import _hash_ip_class
    a = _hash_ip_class("2001:db8:1234:5678::1")
    b = _hash_ip_class("2001:db8:1234:9999::1")
    assert a == b, "same /48 didn't bucket together"


def test_hash_ip_class_does_not_leak_raw_ip():
    """Regression guard: the raw IP prefix must NOT appear verbatim
    in the return value — hashing is the opsec seatbelt. Check the
    full /24 prefix string; single-digit hex overlaps are expected
    and not a leak."""
    from main import _hash_ip_class
    ip = "192.168.1.100"
    h = _hash_ip_class(ip)
    # /24 prefix: "192.168.1"
    assert "192.168.1" not in h, (
        f"raw /24 prefix leaked into hash {h!r}"
    )
    # Also assert it's an 8-char hex string (SHA output shape).
    assert len(h) == 8
    int(h, 16)  # raises if not valid hex
