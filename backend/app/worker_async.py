"""
Thin async bridge so RQ (which is synchronous) can execute async orchestrator jobs.
RQ enqueues 'app.agents.orchestrator.run_analysis' — but since RQ jobs run in
a sync context, we wrap the coroutine here with asyncio.run().
"""
import asyncio
import logging

logger = logging.getLogger(__name__)


def run_async_job(coro_func, *args, **kwargs):
    """Run an async function synchronously inside RQ worker."""
    return asyncio.run(coro_func(*args, **kwargs))
