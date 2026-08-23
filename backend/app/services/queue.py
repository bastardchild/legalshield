"""
queue.py — Redis/RQ job dispatch.

Jobs are enqueued as sync wrappers around the async orchestrator.

`enqueue_analysis` raises `EnqueueError` rather than letting a raw redis exception
escape, so the upload route can mark the contract `failed` instead of leaving it in
`uploaded` forever (KNOWN_ISSUES #9).
"""
import logging

from redis import Redis
from redis.exceptions import RedisError
from rq import Queue
from rq.exceptions import NoSuchJobError

from app.config import get_settings

logger = logging.getLogger(__name__)

QUEUE_NAME = "legalshield"
JOB_TIMEOUT = 600

_redis_conn: Redis | None = None
_queue: Queue | None = None


class EnqueueError(RuntimeError):
    """The analysis job could not be handed to Redis."""


def get_redis() -> Redis:
    global _redis_conn
    if _redis_conn is None:
        # socket_connect_timeout keeps an unreachable Redis from hanging the request
        # thread; without it the client waits on the OS TCP timeout.
        _redis_conn = Redis.from_url(
            get_settings().redis_url,
            socket_connect_timeout=5,
            socket_timeout=5,
            health_check_interval=30,
        )
    return _redis_conn


def get_queue() -> Queue:
    global _queue
    if _queue is None:
        _queue = Queue(QUEUE_NAME, connection=get_redis())
    return _queue


def reset_connection() -> None:
    """Drop cached clients. Used by tests and after a connection-level failure."""
    global _redis_conn, _queue
    _redis_conn = None
    _queue = None


def _run_analysis_sync(contract_id: str):
    """
    Sync wrapper so RQ (which is synchronous) can execute the async orchestrator.

    Each job gets a fresh event loop, so the engine is disposed at the end: asyncpg
    connections are bound to the loop that created them and cannot be reused by the next
    job. Skipping this raises "attached to a different loop" on the second job.
    """
    import asyncio

    from app.agents.orchestrator import run_analysis
    from app.db.session import dispose_engine

    async def _main():
        try:
            await run_analysis(contract_id)
        finally:
            await dispose_engine()

    asyncio.run(_main())


def enqueue_analysis(contract_id: str) -> str:
    """
    Push an analysis job for the given contract_id. Returns the RQ job id.

    Raises EnqueueError if Redis is unreachable or rejects the job.
    """
    try:
        job = get_queue().enqueue(_run_analysis_sync, contract_id, job_timeout=JOB_TIMEOUT)
    except (RedisError, OSError) as e:
        # Force a fresh connection next time; a stale socket would fail identically.
        reset_connection()
        logger.error(f"Could not enqueue analysis for contract {contract_id}: {e}")
        raise EnqueueError(str(e)) from e

    logger.info(f"Enqueued analysis job {job.id} for contract {contract_id}")
    return job.id


def queue_is_reachable() -> bool:
    """Cheap liveness probe used by /health."""
    try:
        return bool(get_redis().ping())
    except (RedisError, OSError) as e:
        reset_connection()
        logger.warning(f"Redis ping failed: {e}")
        return False


def job_exists(job_id: str) -> bool:
    """True if RQ still knows about the job (queued, started, finished, or failed)."""
    from rq.job import Job

    try:
        Job.fetch(job_id, connection=get_redis())
    except (NoSuchJobError, RedisError, OSError):
        return False
    return True
