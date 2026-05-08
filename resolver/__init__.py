"""SP Architecture resolver layer.

Per architecture v1.4 §7. The resolver answers a single question
repeatedly for every provider record: "which canonical fixture does
this belong to?"

Phase 2A: scaffolding only. Defines the contracts (FixtureSignal,
TeamCandidate, ResolverModule Protocol, MatchResult) and the
extraction logic that pulls signals out of provider raw_payload
records. NO database writes, NO matching, NO resolution_log writes.
Subsequent sub-phases (2B onward) build the matching tiers, the
three-loop runner, and the review queue.

Public surface from this module:

    from resolver import (
        FixtureSignal,         # standardized shape consumed by the matcher
        TeamCandidate,         # one of N candidates per side
        ResolverModule,        # per-provider Protocol
        MatchResult,           # output of central matcher
        FLResolverModule,
        KalshiResolverModule,
    )
"""
from .types import FixtureSignal, TeamCandidate, MatchResult, ReasonCode
from .protocol import ResolverModule
from .fl import FLResolverModule
from .kalshi import KalshiResolverModule

# Phase 2B additions:
from .aliases import AliasResolver
from .fixtures import ensure_fixture, find_fixture
from .matcher import StrictMatcher, RESOLVER_VERSION as STRICT_MATCHER_VERSION

# Phase 2A.6 additions:
from .competitions import CompetitionResolver

__all__ = [
    "FixtureSignal",
    "TeamCandidate",
    "MatchResult",
    "ReasonCode",
    "ResolverModule",
    "FLResolverModule",
    "KalshiResolverModule",
    # Phase 2B
    "AliasResolver",
    "ensure_fixture",
    "find_fixture",
    "StrictMatcher",
    "STRICT_MATCHER_VERSION",
    # Phase 2A.6
    "CompetitionResolver",
]
