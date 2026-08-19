"""
queue.py — Redis/RQ job dispatch.
Jobs are enqueued as sync wrappers around the async orchestrator.
"""
import logging
from redis import Redis
from rq import Queue
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_redis_conn: Redis | None = None
_queue: Queue | None = None


def get_redis() -> Redis:
    global _redis_conn
    if _redis_conn is None:
        _redis_conn = Redis.from_url(settings.redis_url)
    return _redis_conn


def get_queue() -> Queue:
    global _queue
    if _queue is None:
        _queue = Queue("legalshield", connection=get_redis())
    return _queue


def _run_analysis_sync(contract_id: str):
    """Sync wrapper so RQ (which is synchronous) can execute async orchestrator."""
    import asyncio
    from app.agents.orchestrator import run_analysis
    asyncio.run(run_analysis(contract_id))


def enqueue_analysis(contract_id: str) -> str:
    """Push an analysis job for the given contract_id onto the Redis queue."""
    q = get_queue()
    job = q.enqueue(
        _run_analysis_sync,
        contract_id,
        job_timeout=600,
    )
    logger.info(f"Enqueued analysis job {job.id} for contract {contract_id}")
    return job.id

