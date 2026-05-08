"""Name normalization shared by all resolver modules.

Per architecture v1.4 §9.2: lowercase, strip Unicode accents (NFD then
drop combining marks), collapse whitespace, remove punctuation. Used
as the matching key against sp.team_aliases.alias_normalized; never
displayed to users (canonical_name on the team table preserves the
original encoding).

Pure function. Same input → same output. Used by every resolver
module so the alias table never sees inconsistent normalization.
"""
from __future__ import annotations

import re
import unicodedata


_PUNCT_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WS_RE = re.compile(r"\s+")


def normalize_name(s: str | None) -> str:
    """Canonical normalization for team name matching.

    Steps:
      1. None / empty → ''.
      2. NFD decompose; drop combining marks (accent strip).
         "Atlético" → "Atletico", "København" → "Kbenhavn"
         (the `ø` decomposes; the visible letter remains).
      3. Lowercase.
      4. Strip punctuation (parens, dots, hyphens, slashes, etc.).
      5. Collapse runs of whitespace to single space.
      6. Strip leading/trailing whitespace.

    Idempotent — normalize_name(normalize_name(x)) == normalize_name(x).
    """
    if not s:
        return ""
    decomposed = unicodedata.normalize("NFD", s)
    stripped_accents = "".join(
        ch for ch in decomposed if not unicodedata.combining(ch)
    )
    lowered = stripped_accents.lower()
    no_punct = _PUNCT_RE.sub(" ", lowered)
    return _WS_RE.sub(" ", no_punct).strip()
