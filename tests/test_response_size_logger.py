"""Tests for the response-size logger (Investigation #2 attribution kit).

The middleware is an EGRESS INVESTIGATION TOOL — dark-launched
behind LOG_RESPONSE_SIZE=1 env var, default off. When off, it must
be effectively free (single env check, no body buffering). When on,
it must log every route+bytes+status+client_class to a "respsize"
logger for grep-based attribution.

Position is load-bearing: must be FIRST-added → OUTERMOST on the
response chain → sees WIRE bytes (post-gzip). Regression to LAST-
added (innermost) reports pre-gzip raw bytes, which mis-attributes
against the actual $15/day network egress.
"""
from __future__ import annotations

import inspect
import logging
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient


# ── Middleware position (load-bearing) ─────────────────────────────

def test_response_size_middleware_is_first_added():
    """ResponseSizeMiddleware MUST be `app.add_middleware()`'d BEFORE
    every other middleware (CORS, GZip, CloudflareCache, Timing,
    EventsETag). Starlette insert(0) semantics: FIRST-added ends up
    as OUTERMOST-close-to-client on the response, which is where
    wire bytes (post-gzip) live. Regression to a later position
    reports pre-gzip raw bytes and mis-attributes against the actual
    ~$15/day network egress."""
    import main
    src = inspect.getsource(main)
    idx_self = src.find("app.add_middleware(ResponseSizeMiddleware)")
    idx_cors = src.find("app.add_middleware(CORSMiddleware")
    idx_gzip = src.find("app.add_middleware(GZipMiddleware")
    idx_cf = src.find("app.add_middleware(CloudflareCacheMiddleware")
    idx_timing = src.find("app.add_middleware(TimingMiddleware)")
    idx_etag = src.find("app.add_middleware(EventsETagMiddleware)")

    assert idx_self > 0, "ResponseSizeMiddleware never registered"
    for label, idx in [
        ("CORSMiddleware", idx_cors), ("GZipMiddleware", idx_gzip),
        ("CloudflareCacheMiddleware", idx_cf),
        ("TimingMiddleware", idx_timing),
        ("EventsETagMiddleware", idx_etag),
    ]:
        assert idx > 0, f"reference middleware {label} not found in main"
        assert idx_self < idx, (
            f"ResponseSizeMiddleware registered AFTER {label} — "
            f"regression to inner position; middleware will see "
            f"pre-gzip bytes instead of wire bytes and mis-attribute "
            f"egress."
        )


# ── Env gating (default-off) ───────────────────────────────────────

def test_default_env_is_off():
    """Ships DARK. LOG_RESPONSE_SIZE default MUST be '0' / off so
    turning on takes an explicit operator env flip."""
    import main
    # Env reads happen at module load; verify the source code has
    # the correct default. We're not asserting the runtime value
    # because pytest may set the env var indirectly.
    src = inspect.getsource(main)
    assert 'os.environ.get("LOG_RESPONSE_SIZE", "0")' in src, (
        "LOG_RESPONSE_SIZE default is not '0' — logger would ship "
        "ENABLED, doubling the very attribution work we're trying "
        "to measure."
    )


def test_min_bytes_default_is_1024():
    """LOG_RESPONSE_SIZE_MIN_BYTES default MUST be 1024 — filter
    noise (hello-world payloads, 304s, empty JSON) from the log
    stream. Read-back is easier when only substantive responses
    appear."""
    import main
    src = inspect.getsource(main)
    assert 'os.environ.get("LOG_RESPONSE_SIZE_MIN_BYTES", "1024")' in src


# ── Behavioral: middleware is inert when disabled ──────────────────

def _make_test_app(handler_body: bytes, log_enabled: bool):
    """Isolate the middleware behavior from main.py's ~130 routes."""
    from main import ResponseSizeMiddleware
    app = FastAPI()
    app.add_middleware(ResponseSizeMiddleware)

    @app.get("/probe")
    def probe():
        return PlainTextResponse(content=handler_body.decode("utf-8"),
                                 media_type="text/plain")
    return app


def test_disabled_does_not_log(caplog):
    """When LOG_RESPONSE_SIZE is off, the middleware early-returns
    the response unchanged and emits nothing."""
    with patch("main._LOG_RESPONSE_SIZE", False):
        app = _make_test_app(b"x" * 5000, log_enabled=False)
        client = TestClient(app)
        with caplog.at_level(logging.INFO, logger="respsize"):
            r = client.get("/probe")
        assert r.status_code == 200
        assert len(r.content) == 5000  # unchanged body
        assert not any("resp " in rec.message for rec in caplog.records)


def test_enabled_logs_route_bytes_status_ua_class(caplog):
    """When enabled, one INFO log per response above the min-bytes
    threshold, with the exact `resp <status> <method> <path> <bytes>
    <client_class>` format the read-back script will grep."""
    with patch("main._LOG_RESPONSE_SIZE", True), \
         patch("main._LOG_RESPONSE_SIZE_MIN_BYTES", 100):
        app = _make_test_app(b"x" * 5000, log_enabled=True)
        client = TestClient(app)
        with caplog.at_level(logging.INFO, logger="respsize"):
            r = client.get("/probe", headers={"User-Agent": "Mozilla/5.0 (Chrome/120)"})
        assert r.status_code == 200
        matched = [rec for rec in caplog.records if "respsize" in rec.name]
        assert len(matched) == 1, (
            f"expected 1 respsize log, got {len(matched)}: "
            f"{[rec.message for rec in matched]}"
        )
        msg = matched[0].message
        # Format: "resp 200 GET /probe <bytes> browser"
        assert msg.startswith("resp 200 GET /probe "), msg
        assert msg.endswith(" browser"), (
            f"UA class not appended: {msg!r}"
        )


def test_enabled_but_below_min_bytes_does_not_log(caplog):
    """Small responses (below LOG_RESPONSE_SIZE_MIN_BYTES) are
    filtered — noise reduction for the read-back."""
    with patch("main._LOG_RESPONSE_SIZE", True), \
         patch("main._LOG_RESPONSE_SIZE_MIN_BYTES", 1024):
        app = _make_test_app(b"hi", log_enabled=True)
        client = TestClient(app)
        with caplog.at_level(logging.INFO, logger="respsize"):
            client.get("/probe")
        matched = [rec for rec in caplog.records if "respsize" in rec.name]
        assert not matched, (
            f"below-min-bytes response logged: {[r.message for r in matched]}"
        )


def test_path_only_no_query_string(caplog):
    """Cardinality control — `/api/events?offset=0&limit=24` and
    `/api/events?offset=24&limit=24` MUST log as the same path so
    aggregation works. Regression to including query multiplies
    cardinality per unique query variant → attribution unusable."""
    with patch("main._LOG_RESPONSE_SIZE", True), \
         patch("main._LOG_RESPONSE_SIZE_MIN_BYTES", 100):
        app = _make_test_app(b"x" * 5000, log_enabled=True)
        client = TestClient(app)
        with caplog.at_level(logging.INFO, logger="respsize"):
            client.get("/probe?offset=0&limit=24&sort=desc")
        matched = [rec for rec in caplog.records if "respsize" in rec.name]
        assert len(matched) == 1
        msg = matched[0].message
        assert "?" not in msg, (
            f"query string leaked into log: {msg!r}"
        )
        assert "/probe" in msg


def test_body_passes_through_unchanged(caplog):
    """The middleware rebuilds the response after measuring; the body
    the client sees MUST be byte-identical to the handler's body.
    Regression here would break every route."""
    body = b"payload-" + b"z" * 4000
    with patch("main._LOG_RESPONSE_SIZE", True), \
         patch("main._LOG_RESPONSE_SIZE_MIN_BYTES", 100):
        app = _make_test_app(body, log_enabled=True)
        client = TestClient(app)
        r = client.get("/probe")
        assert r.content == body


# ── UA classification ──────────────────────────────────────────────

@pytest.mark.parametrize("ua,expected", [
    # Browsers
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36", "browser"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15", "browser"),
    ("Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0", "browser"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0", "browser"),
    # Bots / scrapers
    ("Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)", "bot"),
    ("curl/8.4.0", "bot"),
    ("Wget/1.21.3", "bot"),
    ("python-requests/2.31.0", "bot"),
    ("Go-http-client/1.1", "bot"),
    ("Java/17.0.9", "bot"),
    ("Scrapy/2.11.0 (+https://scrapy.org)", "bot"),
    ("PostmanRuntime/7.36.0", "bot"),
    # HeadlessChrome — browser tokens present, but a scraper marker wins.
    ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/120.0.0.0 Safari/537.36", "bot"),
    # Unknown
    ("", "unknown"),
    ("weird-custom-ua-nobody-knows", "unknown"),
])
def test_classify_ua(ua, expected):
    """UA classifier must resolve every common User-Agent to
    {browser, bot, unknown}. Bot detection prioritized over browser
    (HeadlessChrome test case: browser tokens present but a
    scraper marker MUST win)."""
    from main import _classify_ua
    assert _classify_ua(ua) == expected, (
        f"UA {ua!r} classified as {_classify_ua(ua)}, expected {expected}"
    )
