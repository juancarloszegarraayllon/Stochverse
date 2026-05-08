"""ResolverModule Protocol — per-provider extraction interface.

Per SP Architecture v1.4 §7.2. Each provider has its own resolver
module that knows how to read the provider's raw_payload shape and
extract a FixtureSignal. The central matcher (Phase 2B+) is
provider-agnostic — it operates on FixtureSignals only.

Adding a new provider in Phase 4 = implementing this Protocol +
registering the module. No changes to the matcher.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from .types import FixtureSignal


@runtime_checkable
class ResolverModule(Protocol):
    """Per-provider signal-extraction contract.

    Implementations must NOT touch the database, NOT call third-party
    APIs, NOT mutate global state. Pure function over a raw payload
    dict — same input produces same output. Architecture P5
    (deterministic resolution) and P4 (immutable raw payloads) both
    rely on this purity.

    Failures during extraction return None, NOT raise. The runner
    interprets None as "this record can't yet be matched" and will
    re-try when the resolver version changes or new alias data lands.
    """

    @property
    def provider(self) -> str: ...
    """'fl' | 'kalshi' | 'polymarket' | 'oddsapi'."""

    def extract_signal(self, raw_record: dict) -> FixtureSignal | None: ...
    """Pull a standardized FixtureSignal out of the provider's raw
    payload. Returns None if the record's shape doesn't carry enough
    to attempt resolution (e.g., a Kalshi record that parses as
    `outright`, not `per_fixture`)."""
