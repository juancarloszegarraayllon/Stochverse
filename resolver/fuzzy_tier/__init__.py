"""Phase 2D fuzzy-tier resolution layer.

Pure-Python primitives + matcher for the fuzzy tier. The runner
integration (3-tier TieredMatcher orchestration) lands in 2D.3
after the 2D.2.5 dry-run validates threshold assumptions.

Public surface:

    from resolver.fuzzy_tier import (
        initials_compatible,
        candidate_surname_interpretations,
        FuzzyTierMatcher,
        FUZZY_RESOLVER_VERSION,
    )
"""
from .initial_expansion import (
    candidate_surname_interpretations,
    initials_compatible,
)
from .matcher import (
    ANCHOR_SCORE,
    CORROBORATION_SCORE,
    FuzzyTierMatcher,
    INITIAL_EXPANSION_BONUS,
    PERSONAL_REMAINDER_TOKEN_SET_THRESHOLD,
    RESOLVER_VERSION as FUZZY_RESOLVER_VERSION,
    TEAM_FUZZ_RATIO_THRESHOLD,
    TOKEN_SET_MAX_SCORE,
)


__all__ = [
    # 2D.1 primitives
    "initials_compatible",
    "candidate_surname_interpretations",
    # 2D.2 matcher
    "FuzzyTierMatcher",
    "FUZZY_RESOLVER_VERSION",
    # 2D.2 score constants
    "ANCHOR_SCORE",
    "TOKEN_SET_MAX_SCORE",
    "CORROBORATION_SCORE",
    "INITIAL_EXPANSION_BONUS",
    "PERSONAL_REMAINDER_TOKEN_SET_THRESHOLD",
    "TEAM_FUZZ_RATIO_THRESHOLD",
]

