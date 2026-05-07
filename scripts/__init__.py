"""Backfill scripts for the SP Architecture data layer.

Phase 1E per SP Architecture v1.3 §11.2: one-time scripts that pull
historical data from each provider and pump it through the SAME
ingestion pipeline as live-running ingestion. Backfilled rows are
indistinguishable from rows written by the daily passes — same
UPSERT, same idempotency, same hash-gated change detection.

Idempotent by construction: re-running a backfill that already
landed produces all-`unchanged` counts on the next pass. Safe to
run multiple times.

Available scripts:
  backfill_fl.py      — FL events for indent_days range
  backfill_kalshi.py  — Kalshi open + closed events (active SDK paginate)

See DEPLOYMENT.md for the runbook.
"""
