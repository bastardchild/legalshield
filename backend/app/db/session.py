"""
Async engine and session factory.

The engine is scoped to the running event loop rather than the process. RQ is synchronous,
so each job runs `asyncio.run(...)` in a fresh loop; a single process-wide engine hands
the second job an asyncpg connection created on the first job's (now closed) loop, which
fails with "attached to a different loop". The API, which owns one long-lived loop, still
gets a single pooled engine.
"""
import asyncio
import logging

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

logger = logging.getLogger(__name__)

_engines: dict[int, AsyncEngine] = {}
_sessionmakers: dict[int, async_sessionmaker[AsyncSession]] = {}


class Base(DeclarativeBase):
    pass


def _loop_key() -> int:
    try:
        return id(asyncio.get_running_loop())
    except RuntimeError:
        # Called outside a loop (e.g. Alembic's sync path). One shared slot is fine.
        return 0


def get_engine() -> AsyncEngine:
    key = _loop_key()
    eng = _engines.get(key)
    if eng is None:
        eng = create_async_engine(
            get_settings().database_url,
            echo=False,
            pool_pre_ping=True,
        )
        _engines[key] = eng
    return eng


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    key = _loop_key()
    maker = _sessionmakers.get(key)
    if maker is None:
        maker = async_sessionmaker(get_engine(), class_=AsyncSession, expire_on_commit=False)
        _sessionmakers[key] = maker
    return maker


async def dispose_engine() -> None:
    """
    Close the engine belonging to the current loop.

    Called at API shutdown and after every worker job, so a long-lived worker does not
    accumulate one connection pool per job it has ever run.
    """
    key = _loop_key()
    eng = _engines.pop(key, None)
    _sessionmakers.pop(key, None)
    if eng is not None:
        await eng.dispose()


class _SessionFactory:
    """
    Callable that resolves the per-loop sessionmaker at call time.

    Keeps the `async with AsyncSessionLocal() as db:` idiom used throughout the codebase
    working without every call site having to ask for the current loop's factory.
    """

    def __call__(self, **kwargs) -> AsyncSession:
        return get_sessionmaker()(**kwargs)


AsyncSessionLocal = _SessionFactory()


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
