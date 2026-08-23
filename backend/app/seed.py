"""
Seed CLI: `python -m app.seed`

Idempotent, so it is safe to run on every container start. Kept out of the FastAPI
lifespan so a seeding problem cannot stop the API from serving, and so it runs exactly
once per deployment rather than once per uvicorn reload.
"""
import asyncio
import logging
import sys

from app.db.session import AsyncSessionLocal, dispose_engine
from app.services.seed_loader import clause_pattern_count, seed_clause_patterns

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("app.seed")


async def _run() -> int:
    try:
        async with AsyncSessionLocal() as db:
            before = await clause_pattern_count(db)
            added = await seed_clause_patterns(db)
            after = await clause_pattern_count(db)
        logger.info(f"[Seed] clause_patterns: {before} -> {after} (+{added})")
        return 0
    finally:
        await dispose_engine()


def main() -> int:
    try:
        return asyncio.run(_run())
    except Exception as e:
        # Seeding is best-effort: the app works with an empty skill store, so a failure
        # here must not block startup.
        logger.error(f"[Seed] Failed: {e}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
