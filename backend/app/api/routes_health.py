"""
Liveness and readiness probes (KNOWN_ISSUES #25).

`/health` answers "is this process up" and never touches a dependency, so an orchestrator
does not restart the container because Postgres blinked. `/health/ready` checks the
dependencies the app cannot work without and returns 503 when one is down, which is what
a load balancer should gate traffic on.
"""
import asyncio
import logging

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.db.session import AsyncSessionLocal
from app.services.queue import queue_is_reachable

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])

PROBE_TIMEOUT = 3.0


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


async def _database_ok() -> bool:
    try:
        async with AsyncSessionLocal() as db:
            await asyncio.wait_for(db.execute(text("SELECT 1")), timeout=PROBE_TIMEOUT)
        return True
    except Exception as e:
        logger.warning(f"[Health] Database probe failed: {e}")
        return False


async def _redis_ok() -> bool:
    # queue_is_reachable is a blocking call; keep it off the event loop.
    try:
        return await asyncio.wait_for(asyncio.to_thread(queue_is_reachable), timeout=PROBE_TIMEOUT)
    except Exception as e:
        logger.warning(f"[Health] Redis probe failed: {e}")
        return False


@router.get("/health/ready")
async def readiness(response: Response) -> dict:
    db_ok, redis_ok = await asyncio.gather(_database_ok(), _redis_ok())
    checks = {"database": db_ok, "redis": redis_ok}
    ready = all(checks.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ready" if ready else "degraded", "checks": checks}
