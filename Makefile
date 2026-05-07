# SP Architecture local development helpers.
# See DEPLOYMENT.md for production env vars.

# Default DATABASE_URL for the local stack. Override by exporting
# DATABASE_URL in your shell to point at a different database
# (e.g., a Neon branch for migration testing).
export DATABASE_URL ?= postgresql+asyncpg://dev:dev@localhost:5432/sports_dev

.PHONY: help dev down clean psql migrate migrate-new migrate-down test test-corpus seed replay backfill-fl

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
