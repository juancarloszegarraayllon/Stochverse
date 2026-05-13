"""Phase 2F.1 tech-debt — pin SUGGESTED_ALIASES_STATE_* constant values.

Closes Issue #141. admin/queries.py defines four state-machine
constants at module-level (lines 1321-1324):

    SUGGESTED_ALIASES_STATE_OK              = "ok"
    SUGGESTED_ALIASES_STATE_NO_GOOD_CANDIDATES = "no_good_candidates"
    SUGGESTED_ALIASES_STATE_NO_PARSED_NAMES  = "no_parsed_names"
    SUGGESTED_ALIASES_STATE_UNCLASSIFIED    = "unclassified"

admin/templates/anchor_failed_detail.html consumes these via LITERAL
STRING comparisons at lines 58, 140, 179, 194:

    {% if detail.suggested_aliases_state == "ok" %}
    {% elif detail.suggested_aliases_state == "no_good_candidates" %}
    {% elif detail.suggested_aliases_state == "no_parsed_names" %}
    {% elif detail.suggested_aliases_state == "unclassified" %}

The Python ↔ template coupling is intentional (strings-not-enum so
Jinja can compare with literal string equality without an import,
see admin/queries.py:1314 inline comment) but structurally
unprotected: renaming a constant's VALUE here without updating the
template would silently break the per-state rendering. The
state-machine tests in test_phase_2f1_admin_anchor_failed_empty_
candidates.py would correctly fail under this scenario, but their
failure trace points at template rendering, not at the constant
rename. This file is the immediate-failure-mode test — fails fast,
points the debugger directly at the cause.

Same class-of-bug shape as PR #140's migration-chain extension
silently breaking test_phase_2f0_migration::test_upgrade_then_
downgrade_roundtrip (caught by PR #138's drive-by). Forcing-function
guard before the next refactor introduces the same hazard.
"""
from admin.queries import (
    SUGGESTED_ALIASES_STATE_NO_GOOD_CANDIDATES,
    SUGGESTED_ALIASES_STATE_NO_PARSED_NAMES,
    SUGGESTED_ALIASES_STATE_OK,
    SUGGESTED_ALIASES_STATE_UNCLASSIFIED,
)


# Common boilerplate the four assertion messages share. The
# admin/templates/anchor_failed_detail.html line numbers below are
# accurate at the time of this commit; if the template is restructured
# the line numbers may drift but the broader instruction (update the
# four state-literal branches in lockstep) still applies.
_TEMPLATE_LITERAL_LOCATIONS = (
    "admin/templates/anchor_failed_detail.html "
    "(lines 58, 140, 179, 194 at PR #141 commit time)"
)


def test_state_constant_values_pinned():
    """If you're here because this test failed, you renamed the VALUE
    of a SUGGESTED_ALIASES_STATE_* constant. The template
    anchor_failed_detail.html compares against the LITERAL strings,
    not the imported constants, so a Python-side rename silently
    drifts the rendering. Either revert the rename, OR update the
    four template literals in lockstep AND update this test."""
    assert SUGGESTED_ALIASES_STATE_OK == "ok", (
        "State constant SUGGESTED_ALIASES_STATE_OK changed from 'ok'. "
        f"This test pins the value because {_TEMPLATE_LITERAL_LOCATIONS} "
        "uses literal string comparisons (e.g. `{% if "
        "detail.suggested_aliases_state == 'ok' %}`). If you intended "
        "this rename: update the template's first state-literal branch "
        "to match the new value AND update this test in lockstep."
    )
    assert SUGGESTED_ALIASES_STATE_NO_GOOD_CANDIDATES == "no_good_candidates", (
        "State constant SUGGESTED_ALIASES_STATE_NO_GOOD_CANDIDATES "
        "changed from 'no_good_candidates'. This test pins the value "
        f"because {_TEMPLATE_LITERAL_LOCATIONS} uses literal string "
        "comparisons (e.g. `{% elif detail.suggested_aliases_state == "
        "'no_good_candidates' %}` — the Path B branch). If you intended "
        "this rename: update the template's Path B literal AND update "
        "this test in lockstep."
    )
    assert SUGGESTED_ALIASES_STATE_NO_PARSED_NAMES == "no_parsed_names", (
        "State constant SUGGESTED_ALIASES_STATE_NO_PARSED_NAMES changed "
        "from 'no_parsed_names'. This test pins the value because "
        f"{_TEMPLATE_LITERAL_LOCATIONS} uses literal string comparisons "
        "(e.g. `{% elif detail.suggested_aliases_state == "
        "'no_parsed_names' %}` — the Path C branch). If you intended "
        "this rename: update the template's Path C literal AND update "
        "this test in lockstep."
    )
    assert SUGGESTED_ALIASES_STATE_UNCLASSIFIED == "unclassified", (
        "State constant SUGGESTED_ALIASES_STATE_UNCLASSIFIED changed "
        "from 'unclassified'. This test pins the value because "
        f"{_TEMPLATE_LITERAL_LOCATIONS} uses literal string comparisons "
        "(e.g. `{% elif detail.suggested_aliases_state == "
        "'unclassified' %}` — the Path A branch). If you intended this "
        "rename: update the template's Path A literal AND update this "
        "test in lockstep."
    )
