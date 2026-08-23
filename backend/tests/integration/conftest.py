"""
Integration-test fixtures: a real PostgreSQL database, migrated by Alembic.

The rest of the suite is unit tests and static guards, which is why `--no-deps` works. The
things that were previously only ever verified by hand live here: the migration chain, the
orchestrator's `ON CONFLICT` upsert, the owner-scoped reads, the reaper's UPDATE, and the
parity between migration `0002`'s SQL fingerprint backfill and the Python implementation.

Two deliberate choices:

* **A separate database.** Everything runs against `<name>_test`, created and dropped by
  these fixtures. Pointing them at the development database would let a truncation between
  tests destroy real data.
* **Skip, don't fail, when PostgreSQL is absent.** `docker compose run --rm --no-deps test`
  is the documented fast path and starts no services; these tests must not turn that into a
  red suite. Run `docker compose run --rm test` (without `--no-deps`) to include them.
"""
import os

import pytest

pytest.importorskip("psycopg2", reason="psycopg2 is required to manage the test database")

import psycopg2  # noqa: E402
from psycopg2 import sql  # noqa: E402
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT  # noqa: E402

TEST_DB_SUFFIX = "_test"
CONNECT_TIMEOUT = 3

TABLES = ("analysis_results", "negotiation_sends", "clause_patterns", "contracts")


def _split(url: str) -> tuple[str, str]:
    """('postgresql+asyncpg://user:pw@host:5432', 'dbname')"""
    prefix, _, database = url.rpartition("/")
    return prefix, database


def _sync_dsn(url: str) -> str:
    """asyncpg URL -> a libpq DSN psycopg2 understands."""
    return url.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgresql+psycopg2://", "postgresql://"
    )


BASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://legalshield:legalshield@postgres:5432/legalshield",
)
PREFIX, BASE_DB = _split(BASE_URL)
TEST_DB = BASE_DB + TEST_DB_SUFFIX
TEST_URL = f"{PREFIX}/{TEST_DB}"


def _maintenance_connection():
    """
    Connection to the `postgres` database, used to CREATE/DROP the test database.

    Returns None when PostgreSQL is unreachable, which is how the skip is decided. A short
    connect_timeout keeps `--no-deps` runs fast instead of blocking on the OS TCP timeout.
    """
    try:
        conn = psycopg2.connect(
            _sync_dsn(f"{PREFIX}/postgres"), connect_timeout=CONNECT_TIMEOUT
        )
    except psycopg2.Error:
        return None
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    return conn


def _reload_settings() -> None:
    from app.config import get_settings

    get_settings.cache_clear()


def _alembic_config():
    from alembic.config import Config

    config = Config("alembic.ini")
    config.set_main_option("script_location", "alembic")
    return config


def alembic_upgrade(revision: str) -> None:
    from alembic import command

    command.upgrade(_alembic_config(), revision)


def alembic_downgrade(revision: str) -> None:
    from alembic import command

    command.downgrade(_alembic_config(), revision)


def alembic_head() -> str:
    """The newest revision on disk, for comparison against what the database has applied."""
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(_alembic_config()).get_current_head()


@pytest.fixture(scope="session")
def migrated_database():
    """
    A freshly created, fully migrated test database.

    Session-scoped: migrating once per test would dominate the runtime for no extra coverage.
    """
    conn = _maintenance_connection()
    if conn is None:
        pytest.skip(
            f"PostgreSQL not reachable at {PREFIX}. Run without --no-deps to include "
            "integration tests."
        )

    ident = sql.Identifier(TEST_DB)
    with conn.cursor() as cur:
        # Dropped first: a database left behind by an interrupted run would otherwise be
        # migrated from an unknown revision.
        cur.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(ident))
        cur.execute(sql.SQL("CREATE DATABASE {}").format(ident))
    conn.close()

    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = TEST_URL
    _reload_settings()

    try:
        alembic_upgrade("head")
        yield TEST_URL
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
        _reload_settings()

        drop = _maintenance_connection()
        if drop is not None:
            with drop.cursor() as cur:
                cur.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(ident))
            drop.close()


@pytest.fixture
def sync_connection(migrated_database):
    """Direct psycopg2 connection, for schema introspection and raw-SQL parity checks."""
    conn = psycopg2.connect(_sync_dsn(migrated_database))
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture(autouse=True)
async def clean_tables(migrated_database):
    """
    Empty every table before each test and dispose the engine afterwards.

    Truncating *before* rather than after means a failed test leaves its rows behind for
    inspection. The dispose matters because pytest-asyncio gives each test its own event loop
    and `db/session.py` keys engines by loop id, so without it every test leaks a pool.
    """
    from sqlalchemy import text

    from app.db.session import AsyncSessionLocal, dispose_engine

    async with AsyncSessionLocal() as session:
        await session.execute(text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))
        await session.commit()
    try:
        yield
    finally:
        await dispose_engine()


@pytest.fixture
async def db():
    """An AsyncSession on the test database, matching how application code opens one."""
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        yield session


def owner_id(tag: str = "a") -> str:
    """A 32-hex owner id, the shape `security.new_owner_id()` produces."""
    return (tag * 32)[:32]
