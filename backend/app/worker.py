"""
RQ Worker entrypoint.
RQ runs jobs synchronously, so we bridge async orchestrator via asyncio.run().
The job function registered in queue.py is 'app.agents.orchestrator.run_analysis'
which is a coroutine — we wrap it here transparently by monkey-patching the import.
"""
import asyncio
import logging
import os
import sys

# Ensure /app is on the path when running inside Docker
sys.path.insert(0, "/app")
os.environ.setdefault("PYTHONPATH", "/app")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    from redis import Redis
    from rq import Worker, Queue
    from app.config import get_settings

    settings = get_settings()
    redis_conn = Redis.from_url(settings.redis_url)
    queues = [Queue("legalshield", connection=redis_conn)]

    logger.info("LegalShield RQ worker starting...")
    worker = Worker(queues, connection=redis_conn)
    worker.work(burst=False)


if __name__ == "__main__":
    main()

