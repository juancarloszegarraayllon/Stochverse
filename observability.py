"""Structured logging and provider-call instrumentation.

Phase 0 deliverable per SP Architecture v1.2 §11.1:
  * structlog with JSON output for production, console for dev.
  * `provider_api_call` events emitted in the schema-equivalent shape
    of the future `provider_api_calls` table (§6.3 of the architecture
    doc), so Phase 1's Postgres migration can backfill from logs.

Coexists with the existing `logging` module — does not replace it.
Modules can import `get_logger()` to emit JSON-shaped events; existing
`logging.getLogger(...)` calls keep working.
"""
from __future__ import annotations

import logging
import os
import sys
import time
from typing import Optional

import structlog


_CONFIGURED = False


def configure_structlog() -> None:
    """Idempotent structlog configuration. Safe to call multiple times.

    Output format:
      * Production (default): one JSON object per line on stdout.
        Railway log aggregation parses these directly.
      * Development (ENV=development or STOCHVERSE_LOG_FORMAT=console):
        human-readable colored output.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    log_format = os.environ.get("STOCHVERSE_LOG_FORMAT", "").lower()
    is_dev = (
        log_format == "console"
        or os.environ.get("ENV", "").lower() == "development"
    )

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if is_dev:
        renderer = structlog.dev.ConsoleRenderer(colors=True)
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=shared_processors + [renderer],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Bridge stdlib logging → structlog so existing log.info(...) calls
    # also emit JSON. Don't reset existing handlers; just ensure root
    # has at least a stream handler that writes to stdout.
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        root.addHandler(handler)
        root.setLevel(
            logging.DEBUG if is_dev else logging.INFO
        )

    _CONFIGURED = True


def get_logger(name: Optional[str] = None) -> structlog.stdlib.BoundLogger:
    """Return a structlog-bound logger. Configures on first call."""
    if not _CONFIGURED:
        configure_structlog()
    if name:
        return structlog.get_logger(name)
    return structlog.get_logger()


def provider_call_event(
    *,
    provider: str,
    endpoint: str,
    status: int,
    latency_ms: int,
    response_bytes: int = 0,
    error: Optional[str] = None,
    extra: Optional[dict] = None,
) -> None:
    """Emit a `provider_api_call` event matching the future
    `provider_api_calls` table schema (architecture doc §6.3).

    Schema fields:
      provider          — 'fl' | 'kalshi' | 'polymarket' | 'oddsapi'
      endpoint          — provider-specific path, e.g. '/v1/events/list'
      called_at         — implicit via TimeStamper (UTC ISO-8601)
      status            — HTTP status code, 0 if exception before response
      latency_ms        — wall-clock time of the call
      response_bytes    — body size on success, 0 otherwise
      error             — exception class name on failure, None on success

    `extra` may carry endpoint-specific context (event_id, sport_id,
    cache_hit, etc.) without polluting the canonical schema. Phase 1's
    backfill script can pluck the canonical fields and store the rest
    as JSONB metadata if useful.
    """
    log = get_logger("provider_api_call")
    payload = {
        "provider":       provider,
        "endpoint":       endpoint,
        "status":         status,
        "latency_ms":     latency_ms,
        "response_bytes": response_bytes,
    }
    if error:
        payload["error"] = error
    if extra:
        payload["extra"] = extra
    log.info("provider_api_call", **payload)


class _CallTimer:
    """Context manager that records a `provider_api_call` event on exit.

    Usage:
        async with _CallTimer(provider="fl", endpoint=path) as t:
            r = await client.get(...)
            t.status = r.status_code
            t.response_bytes = len(r.content)
    """

    def __init__(self, *, provider: str, endpoint: str):
        self.provider = provider
        self.endpoint = endpoint
        self.status = 0
        self.response_bytes = 0
        self.error: Optional[str] = None
        self.extra: Optional[dict] = None
        self._started = 0.0

    def __enter__(self):
        self._started = time.monotonic()
        return self

    def __exit__(self, exc_type, exc, tb):
        latency_ms = int((time.monotonic() - self._started) * 1000)
        if exc_type is not None and self.error is None:
            self.error = exc_type.__name__
        provider_call_event(
            provider=self.provider,
            endpoint=self.endpoint,
            status=self.status,
            latency_ms=latency_ms,
            response_bytes=self.response_bytes,
            error=self.error,
            extra=self.extra,
        )
        return False  # don't suppress exceptions
