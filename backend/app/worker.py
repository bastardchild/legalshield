"""
RQ Worker entrypoint.

RQ runs jobs synchronously, so `services.queue._run_analysis_sync` bridges to the async
orchestrator via asyncio.run(). The worker also hosts the reaper thread, which fails
contracts whose job died without reaching a terminal state.
"""
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
    import threading

    from redis import Redis
    from rq import Queue, Worker

    from app.config import get_settings
    from app.services.reaper import run_reaper_forever

    settings = get_settings()
    redis_conn = Redis.from_url(settings.redis_url)
    queues = [Queue("legalshield", connection=redis_conn)]

    # Sweeps contracts whose worker died mid-job. Daemon, so it never blocks shutdown.
    threading.Thread(target=run_reaper_forever, name="reaper", daemon=True).start()

    logger.info("LegalShield RQ worker starting...")
    worker = Worker(queues, connection=redis_conn)
    worker.work(burst=False)


if __name__ == "__main__":
    main()

