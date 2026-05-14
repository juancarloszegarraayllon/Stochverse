# SP Architecture local development helpers.
# See DEPLOYMENT.md for production env vars.

# Default DATABASE_URL for the local stack. Override by exporting
# DATABASE_URL in your shell to point at a different database
# (e.g., a Neon branch for migration testing).
export DATABASE_URL ?= postgresql+asyncpg://dev:dev@localhost:5432/sports_dev

.PHONY: help dev down clean psql migrate migrate-new migrate-down test test-corpus seed replay backfill-fl bootstrap-sp-teams bootstrap-sp-competitions bootstrap-national-teams backfill-sp-fl-events-sport-id resolver-pass-kalshi resolver-pass-fl dry-run-alias-tier dry-run-fuzzy-tier investigate-corroboration-gap vendor-htmx alias-add

help:
	@echo "SP Architecture dev targets:"
	@echo ""
	@echo "  make dev          # docker compose up — Postgres on :5432"
	@echo "  make down         # docker compose down (keeps volume)"
	@echo "  make clean        # docker compose down -v (deletes volume)"
	@echo "  make psql         # interactive psql into dev database"
	@echo ""
	@echo "  make migrate      # alembic upgrade head"
	@echo "  make migrate-new MSG='describe your change'"
	@echo "  make migrate-down # alembic downgrade -1"
	@echo ""
	@echo "  make test         # pytest"
	@echo "  make test-corpus  # pytest tests/corpus/  (regression suite)"
	@echo "  make seed         # load curated test fixtures into dev DB"
	@echo "  make replay       # replay last 24h of archived raw payloads"
	@echo ""
	@echo "  make backfill-fl       # FL backfill (±7 days)"
	@echo "  make backfill-fl ARGS=\"--days 7\""
	@echo "  make bootstrap-sp-teams        # one-time legacy → sp.* migration (Phase 2A.5)"
	@echo "  make bootstrap-sp-teams ARGS=\"--dry-run\""
	@echo "  make bootstrap-sp-competitions # seed sp.competitions from Kalshi (Phase 2A.6)"
	@echo "  make bootstrap-sp-competitions ARGS=\"--dry-run\""
	@echo "  make bootstrap-national-teams  # men's senior Soccer national teams (Issue #136)"
	@echo "  make bootstrap-national-teams ARGS=\"--dry-run\""
	@echo "  make backfill-sp-fl-events-sport-id  # populate sp.fl_events.sport_id (Phase 2A.7)"
	@echo "  make backfill-sp-fl-events-sport-id ARGS=\"--skip-backfill\""
	@echo "  make resolver-pass-kalshi      # Phase 2B strict matcher pass over sp.kalshi_markets"
	@echo "  make resolver-pass-fl          # same for sp.fl_events"
	@echo "  make resolver-pass-kalshi ARGS=\"--limit 100 --run-mode cron\""
	@echo ""
	@echo "  make dry-run-alias-tier ARGS=\"--provider kalshi --sport-code tennis --limit 600\""
	@echo "  # Phase 2C.2.5: read-only calibration of alias-tier thresholds"
	@echo ""
	@echo "  make dry-run-fuzzy-tier ARGS=\"--provider kalshi --sport-code tennis --limit 600\""
	@echo "  # Phase 2D.2.5: read-only calibration of fuzzy-tier corroboration rate"
	@echo ""
	@echo "  make investigate-corroboration-gap"
	@echo "  # Phase 2D.2.7: E.8 investigation runbook (Q1 tournament overlap, Q2 kickoff alignment, Q3 drift window)"
	@echo ""
	@echo "  make vendor-htmx               # Fetch htmx-1.9.10.min.js into admin/static/"
	@echo "  make vendor-htmx HTMX_VERSION=1.10.0   # Override version"
	@echo "  # Phase 2F.1: vendored client asset (per design Q4). Sub-PR #3 uses it."
	@echo ""
	@echo "  make alias-add ARGS=\"--sport tennis --team-canonical 'Jannik Sinner' --alias 'J. Sinner'\""
	@echo "  # Phase 2F.1 sub-PR #4: add one sp.team_aliases row. Idempotent."
	@echo "  # Default source=manual_anchor_failed. Add --dry-run to verify resolution before writing."
	@echo ""
	@echo "DATABASE_URL = $(DATABASE_URL)"

dev:
	docker compose up -d
	@echo "Postgres: $(DATABASE_URL)"
	@echo "Run 'make migrate' to apply schema."

down:
	docker compose down

clean:
	docker compose down -v

psql:
	docker compose exec postgres psql -U dev -d sports_dev

migrate:
	alembic upgrade head

migrate-new:
	@if [ -z "$(MSG)" ]; then \
		echo "ERROR: MSG is required, e.g. make migrate-new MSG='add foo column'"; \
		exit 1; \
	fi
	alembic revision --autogenerate -m "$(MSG)"

migrate-down:
	alembic downgrade -1

test:
	pytest

test-corpus:
	pytest tests/corpus/

seed:
	@if [ -f sp_seed_fixtures.py ]; then \
		python sp_seed_fixtures.py; \
	else \
		echo "sp_seed_fixtures.py not yet present — Phase 1B/1C deliverable"; \
	fi

replay:
	@if [ -f scripts/replay_archive.py ]; then \
		python scripts/replay_archive.py --hours 24; \
	else \
		echo "scripts/replay_archive.py not yet present — Phase 1F deliverable"; \
	fi

backfill-fl:
	python scripts/backfill_fl.py $(ARGS)

bootstrap-sp-teams:
	python scripts/bootstrap_sp_teams.py $(ARGS)

bootstrap-sp-competitions:
	python scripts/bootstrap_sp_competitions.py $(ARGS)

bootstrap-national-teams:
	python scripts/bootstrap_national_teams.py $(ARGS)

backfill-sp-fl-events-sport-id:
	python scripts/backfill_sp_fl_events_sport_id.py $(ARGS)

alias-add:
	python scripts/alias_add.py $(ARGS)

resolver-pass-kalshi:
	python scripts/run_resolver_pass.py --provider kalshi $(ARGS)

resolver-pass-fl:
	python scripts/run_resolver_pass.py --provider fl $(ARGS)

dry-run-alias-tier:
	python scripts/dry_run_alias_tier.py $(ARGS)

dry-run-fuzzy-tier:
	python scripts/dry_run_fuzzy_tier.py $(ARGS)

investigate-corroboration-gap:
	psql "$$DATABASE_URL" -f scripts/investigate_corroboration_gap.sql

# Phase 2F.1 admin UI: vendor htmx into admin/static/. Per design Q4
# (vendor vs CDN), htmx is committed to the repo with a versioned
# filename so upgrades are explicit. Sub-PR #2 ships this Make target
# + the static dir scaffolding; sub-PR #3 adds the <script> tag in
# base.html when HTMX-driven UI lands.
HTMX_VERSION ?= 1.9.10
vendor-htmx:
	@mkdir -p admin/static
	@echo "Fetching htmx@$(HTMX_VERSION) from raw.githubusercontent.com..."
	@curl -fSL -o admin/static/htmx-$(HTMX_VERSION).min.js \
		"https://raw.githubusercontent.com/bigskysoftware/htmx/v$(HTMX_VERSION)/dist/htmx.min.js"
	@SIZE=$$(wc -c < admin/static/htmx-$(HTMX_VERSION).min.js); \
	if [ $$SIZE -lt 30000 ] || [ $$SIZE -gt 100000 ]; then \
		echo "ERROR: htmx file size $$SIZE bytes is outside expected 30-100KB range"; \
		echo "Possible failed download or upstream change — inspect admin/static/htmx-$(HTMX_VERSION).min.js"; \
		exit 1; \
	fi
	@echo "OK: admin/static/htmx-$(HTMX_VERSION).min.js ($$(wc -c < admin/static/htmx-$(HTMX_VERSION).min.js) bytes)"
	@echo "Commit the file when ready: git add admin/static/htmx-$(HTMX_VERSION).min.js"
