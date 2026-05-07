"""SP Architecture ingestion layer.

Per architecture v1.3 §6, each provider has its own ingestion module
sharing a common Protocol. Modules write only to their own provider
tables (sp.fl_events, sp.kalshi_markets, ...) and never call the
resolver or each other. This isolation is what allows a provider
failure to be contained.

Public interface for callers (main.py startup):

    from ingestion import start_all_ingestion
    asyncio.create_task(start_all_ingestion())

Each provider module exposes a single coroutine (e.g.,
`ingestion.fl.run`) that runs forever. Supervision wraps each one
with restart-on-crash + exponential backoff.
"""
from .runner import start_all_ingestion

__all__ = ["start_all_ingestion"]
