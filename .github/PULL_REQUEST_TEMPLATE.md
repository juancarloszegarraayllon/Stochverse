<!--
PR template for the SP Architecture project. Adapt sections that
apply; delete the rest. The "Migration applied to prod?" checkbox
exists because Railway does NOT auto-run alembic on deploy —
migrations are manual. See DEPLOYMENT.md → "Migration-bearing PR
checklist" for the full procedure.

There is no PR-blocking CI on this project as of 2026-05-10. The
test plan / verification checkboxes below are operator self-
attestations, not CI gates.
-->

## Summary

<!-- One paragraph: what changed and why. -->

## Test plan

<!-- What you ran locally to convince yourself this is right.
Check the ones you actually did; leave others unchecked rather
than ticking aspirationally. -->

- [ ] `python -m pytest` (or scoped subset) — all relevant tests pass locally
- [ ] Manual verification of the changed behavior (describe below)
- [ ] No new lints / type errors introduced
- [ ] **If this PR opens a SQLAlchemy session and writes to production tables:** integration tests ran against a real Postgres (`SP_INTEGRATION_DB=...`) AND went through the FastAPI dependency-injected session lifecycle (`TestClient.post()` → `Depends(get_db)`). See `DEPLOYMENT.md → DB-transaction PRs — integration tests required` for the full procedure (PR #123 → #125 incident).
- [ ] Tested against a real Postgres (Neon dev branch or docker-compose) — required for any PR touching SQL paths

<!-- Brief notes on what you actually verified, especially anything
the test suite doesn't cover. -->

## Migration impact

<!-- If this PR is a migration-bearing PR (adds a file under
`migrations/versions/`) OR depends on a not-yet-applied migration,
fill out the relevant subsection. Otherwise delete this whole
section. -->

**This PR contains an alembic revision:** _yes / no_

**This PR depends on a migration that must be applied to production first:** _yes / no — if yes, which PR introduced it_

If either is "yes", confirm:

- [ ] Forward + downgrade roundtrip verified on a disposable Postgres (Neon dev branch / docker-compose)
- [ ] `alembic current` against the dev DB matches the new revision (or, for dependent PRs: matches the upstream migration revision)
- [ ] For migration-bearing PRs: production migration plan documented in this PR description, with the exact `DATABASE_URL=<prod> alembic upgrade head` command and the verification SQL the operator should run after
- [ ] For dependent PRs: the upstream migration PR is merged AND applied to production (verified via `alembic current`) before this PR merges

See `DEPLOYMENT.md → Migration-bearing PR checklist` for the full procedure.

## Operator action after merge

<!-- If the PR requires the operator to do something after merge
(run a backfill, apply a migration, restart a service, monitor a
metric), describe it here as a checklist. Otherwise delete this
section. -->

- [ ]

## What this PR is NOT

<!-- Optional but encouraged for design-doc PRs and PRs that
intentionally defer scope. Helps reviewers calibrate. -->
