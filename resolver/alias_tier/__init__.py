"""Phase 2C alias-tier resolution layer.

Pure-Python modules — no DB, no matcher integration. The matcher
orchestration lands in 2C.3 and consumes the building blocks from
this package.

Public surface:

    from resolver.alias_tier import (
        StructuredName, structurally_normalize, INDIVIDUAL_SPORT_CODES,
        AliasTierScore, score_pair,
        AUTO_APPLY_THRESHOLD, REVIEW_QUEUE_THRESHOLD, TOP_2_MARGIN,
    )
"""
from .normalize import (
    INDIVIDUAL_SPORT_CODES,
    StructuredName,
    structurally_normalize,
)
from .scorer import (
    ANCHOR_SCORE,
    AUTO_APPLY_THRESHOLD,
    AliasTierScore,
    CORROBORATION_SCORE,
    PERSONAL_TOKEN_SET_THRESHOLD,
    REVIEW_QUEUE_THRESHOLD,
    TEAM_TOKEN_SET_THRESHOLD,
    TOKEN_SET_MAX_SCORE,
    TOP_2_MARGIN,
    score_pair,
)


__all__ = [
    # normalize
    "INDIVIDUAL_SPORT_CODES",
    "StructuredName",
    "structurally_normalize",
    # scorer
    "AliasTierScore",
    "score_pair",
    # threshold constants
    "ANCHOR_SCORE",
    "TOKEN_SET_MAX_SCORE",
    "CORROBORATION_SCORE",
    "PERSONAL_TOKEN_SET_THRESHOLD",
    "TEAM_TOKEN_SET_THRESHOLD",
    "AUTO_APPLY_THRESHOLD",
    "REVIEW_QUEUE_THRESHOLD",
    "TOP_2_MARGIN",
]
