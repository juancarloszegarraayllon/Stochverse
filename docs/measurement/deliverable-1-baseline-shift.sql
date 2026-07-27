-- Deliverable 1 baseline-shift annotation.
--
-- Per docs/measurement/deliverable-1-scope-2026-07-25.md §4.7 and
-- §6 success criterion 6.
--
-- WHEN TO RUN: after the first daily-diff cron pass on which
-- Deliverable 1's legacy comparison is present (i.e., the first row
-- in `sp.daily_diff_reports` with `legacy_comparison_present = TRUE`
-- after the implementation PR merges).
--
-- WHY: `sp.daily_diff_reports.report_json` gains new dimensions
-- (`legacy_v1_diff`, `legacy_v2_diff`) at that moment. Historical
-- rows written before the flip lack those fields. Any downstream
-- reader comparing trends across the pre/post boundary must know
-- to account for the schema change — the shift row is that
-- structured breadcrumb.
--
-- The event does not represent a resolver-behavior change. Only
-- the measurement-dimension shape moves; the resolver itself is
-- unaffected. Item 7's acceptable-threshold remains operator-owned
-- and gets set from N days of Deliverable-1 report data separately.
--
-- Fill in <first-Deliverable-1-report-date> with the actual UTC date
-- of the first row that carries legacy_comparison_present = TRUE.
-- Fill in <PR-number> with the merged implementation PR's number.

INSERT INTO sp.baseline_shifts (
    event_type,
    event_date,
    affected_population,
    expected_metric_delta,
    notes,
    created_by
) VALUES (
    'measurement_expansion',
    DATE '<first-Deliverable-1-report-date>',
    'sp.daily_diff_reports.report_json — new dimensions '
    'legacy_v1_diff + legacy_v2_diff. '
    'legacy_comparison_present flips False → True on this date.',
    'No underlying resolver-behavior change; measurement dimension '
    'added. Historical reports lack the new fields; comparisons '
    'across the pre/post boundary must account for this.',
    'source_tag=deliverable_1_2026_07_25. Gate #2 close. '
    'PR #<PR-number>. Item 7 acceptable-threshold still operator-'
    'owned; this workstream produces the measurement dimension only.',
    'PR #<PR-number>'
);
