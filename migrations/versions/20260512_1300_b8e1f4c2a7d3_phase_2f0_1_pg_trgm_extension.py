"""Phase 2F.0.1: install pg_trgm extension for trigram similarity().

Surfaced by today's production smoke test of PR #133 (Phase 2F.1
sub-PR #4). The anchor_failed admin surface's "Suggest alias"
widget calls Postgres `similarity()` from the `pg_trgm` extension
to rank candidate teams within a sport. `pg_trgm` is present on
the Neon server (visible via `pg_available_extensions`) but was
never activated in the `sports_prod` database — every detail-view
click that reaches the suggested-alias query path hits
`function similarity(text, unknown) does not exist`.

Two existing call sites depend on `similarity()`:

  1. admin/queries.py:_build_suggested_aliases — the anchor_failed
     widget's per-side top-3 closest-team lookup.
  2. scripts/alias_add.py — the "team not found in sport" error
     path that surfaces the closest-3 candidates to help operators
     spot typos.

This migration installs the extension via the standard `CREATE
EXTENSION IF NOT EXISTS pg_trgm` shape. `IF NOT EXISTS` keeps the
migration idempotent across dev / staging / prod environments
that may have already installed it manually.

Privilege check (per PR #4.0.1 conversation Q1):

  pg_trgm has been marked TRUSTED in upstream Postgres since PG 13
  (2020). TRUSTED extensions can be installed by any role with
  CREATE privilege on the database, not just superuser. Neon's
  standard `<db>_owner` role (e.g. `neondb_owner`) has full CREATE
  privilege on the user's database by default. Across managed PG
  providers (Neon, RDS, Cloud SQL, Supabase), pg_trgm is consis-
  tently in the "no escalation needed" tier. Production-side
  evidence: a SELECT against pg_available_extensions returned one
  row with installed_version IS NULL, confirming the extension
  files are present on the server and only activation is needed.

If the migration fails on production with a privilege error
despite the trusted-status reasoning above, the fallback is to
re-run this migration as a no-op marker (commented `op.execute`
calls) and install the extension via the Neon dashboard
(Settings → Extensions → pg_trgm → Install). See the PR #4.0.1
operator-action-after-merge runbook for both paths.

Verification SQL (paste output of this query into PR #4.0.1 as
the verification artifact AFTER `alembic upgrade head` runs
against production):

  SELECT extname, extversion FROM pg_extension WHERE extname = 'pg_trgm';

Expected: 1 row, extname='pg_trgm', extversion='1.6' (or current
Neon default — 1.5 / 1.6 are both fine). The pg_extension
(activated) vs pg_available_extensions (available-on-server)
distinction is load-bearing — same distinction that triggered
this migration.

Revision ID: b8e1f4c2a7d3
Revises: a1c4f9e8b2d7 (Phase 2F.0 — review_queue columns)
Create Date: 2026-05-12 13:00:00 UTC
"""
from alembic import op


revision = "b8e1f4c2a7d3"
down_revision = "a1c4f9e8b2d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF NOT EXISTS keeps the migration idempotent — re-running
    # against a database where pg_trgm is already installed (dev
    # branches, locally-bootstrapped instances) is a no-op rather
    # than a 42710 duplicate_object error.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")


def downgrade() -> None:
    # DROP EXTENSION CASCADE would remove dependent indexes /
    # operators. We don't currently create any pg_trgm-backed
    # indexes (the only consumers do per-query similarity()
    # computation), so a plain DROP is safe — no cascade fan-out.
    #
    # If a future migration adds a GIN / GiST index using gin_trgm_ops
    # / gist_trgm_ops, downgrade behavior should be revisited
    # (either DROP CASCADE here, or split the index into a later
    # migration whose own downgrade handles the index first).
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
