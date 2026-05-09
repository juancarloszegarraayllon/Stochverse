"""Phase 2D fuzzy-tier resolution layer.

Pure-Python primitives for the fuzzy tier — no DB, no matcher
integration. The matcher orchestration lands in 2D.2 and consumes
the building blocks from this package.

Public surface:

    from resolver.fuzzy_tier import (
        initials_compatible,
        candidate_surname_interpretations,
    )
"""
from .initial_expansion import (
    candidate_surname_interpretations,
    initials_compatible,
)


__all__ = [
    "initials_compatible",
    "candidate_surname_interpretations",
]
