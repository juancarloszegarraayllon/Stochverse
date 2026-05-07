"""Pydantic boundary validation for provider payloads.

Architecture v1.3 §6.3: JSONB raw payloads tolerate any shape, but
the resolver extracts structured fields. Provider schema changes
silently break extractions — symptoms appear downstream as missing
data, days after the change.

At the ingestion boundary, we validate the minimum fields the
resolver needs using a Pydantic model per provider. Validation
failures DO NOT block storage of the raw payload (P4 immutability
stands), but they DO increment a per-provider metric and emit a
structured log event with the field that failed. Threshold-based
alerting catches schema drift in minutes.

Each provider module imports its validator and calls
`validate_or_drift(...)` per record.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from observability import get_logger

_log = get_logger("schema_drift")


# ── FL ──────────────────────────────────────────────────────────
#
# Minimum fields the FL resolver module will need from /v1/events/list.
# Anything else (lineups, commentary, etc.) lives in raw_payload and
# resolution doesn't depend on it.
class FLEventValidator(BaseModel):
    model_config = ConfigDict(extra="allow")

    EVENT_ID: str = Field(min_length=1)
    HOME_NAME: str | None = None
    AWAY_NAME: str | None = None
    SHORTNAME_HOME: str | None = None
    SHORTNAME_AWAY: str | None = None
    HOME_PARTICIPANT_TEAM_ID: list[str] | None = None
    AWAY_PARTICIPANT_TEAM_ID: list[str] | None = None
    START_TIME: int | None = None      # unix epoch
    START_UTIME: int | None = None     # unix epoch alternate
    STAGE_TYPE: str | None = None      # SCHEDULED | LIVE | FINISHED
    STAGE: str | None = None
    HOME_SCORE_CURRENT: int | None = None
    AWAY_SCORE_CURRENT: int | None = None
    HOME_IMAGES: list[str] | None = None
    AWAY_IMAGES: list[str] | None = None


class FLTournamentValidator(BaseModel):
    model_config = ConfigDict(extra="allow")

    TOURNAMENT_STAGE_ID: str | None = None
    NAME: str | None = None
    NAME_PART_1: str | None = None      # region (e.g., "Europe")
    NAME_PART_2: str | None = None      # league name
    COUNTRY_NAME: str | None = None
    EVENTS: list[dict] = Field(default_factory=list)


# ── Kalshi ──────────────────────────────────────────────────────
#
# Minimum fields the Kalshi resolver module will need from the cache
# records. The cache shape is what get_data() / _build_cache() in
# main.py produces — a flat list where each row is one event with
# its sub-markets bundled. Field names here match the Kalshi API's
# /events response after main.py's enrichment passes.
class KalshiMarketValidator(BaseModel):
    model_config = ConfigDict(extra="allow")

    event_ticker: str = Field(min_length=1)
    series_ticker: str | None = None
    title: str | None = None
    category: str | None = None
    _sport: str | None = None
    _soccer_comp: str | None = None
    _kickoff_dt: str | None = None       # ISO 8601 string
    expected_expiration_time: str | None = None
    close_time: str | None = None
    status: str | None = None
    markets: list[dict] | None = None    # sub-market records
    outcomes: list[dict] | None = None   # outcome rows from extract()



# ── Boundary validator ──────────────────────────────────────────

def validate_or_drift(
    *,
    provider: str,
    record_kind: str,                   # 'event' | 'tournament' | 'market' | ...
    record_id: str,
    raw: Any,
    validator: type[BaseModel],
) -> tuple[BaseModel | None, bool]:
    """Validate `raw` against `validator`. Return (parsed_or_None, drift_seen).

    On success: (parsed, False).
    On failure: (None, True) plus a structured log event naming
    every field that failed validation, with a snippet of the bad
    payload (truncated to 1KB).

    Caller responsibility:
      * Persist raw_payload regardless (P4 immutability).
      * Increment a counter when drift_seen is True. The structlog
        event is the audit; counters are for alerting (architecture
        §9.5).
    """
    try:
        parsed = validator.model_validate(raw)
        return parsed, False
    except ValidationError as exc:
        # Each .errors() entry has loc / msg / type.
        bad_fields = [
            {
                "field": ".".join(str(p) for p in err["loc"]),
                "msg":   err["msg"],
                "type":  err["type"],
            }
            for err in exc.errors()
        ]
        # Truncate raw for log size sanity.
        try:
            import json as _json
            raw_snippet = _json.dumps(raw, default=str)[:1024]
        except Exception:
            raw_snippet = str(raw)[:1024]
        _log.warning(
            "schema_drift",
            provider=provider,
            record_kind=record_kind,
            record_id=record_id,
            bad_fields=bad_fields,
            raw_snippet=raw_snippet,
        )
        return None, True
