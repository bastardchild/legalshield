"""
Queue dispatch and reaper tests (KNOWN_ISSUES #9).

Previously `enqueue_analysis` let a raw redis exception escape after the contract row had
already been committed, so a Redis outage left the contract in `uploaded` forever while the
front-end polled it indefinitely.
"""
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.services import queue as queue_mod
from app.services.queue import JOB_TIMEOUT, EnqueueError, enqueue_analysis, queue_is_reachable


class _FakeJob:
    id = "job-123"


class _FakeQueue:
    def __init__(self, exc: Exception | None = None):
        self.exc = exc
        self.calls: list[tuple] = []

    def enqueue(self, func, *args, **kwargs):
        self.calls.append((func, args, kwargs))
        if self.exc:
            raise self.exc
        return _FakeJob()


@pytest.fixture(autouse=True)
def _reset_queue_singletons():
    queue_mod.reset_connection()
    yield
    queue_mod.reset_connection()


class TestEnqueueAnalysis:
    def test_returns_job_id(self, monkeypatch):
        fake = _FakeQueue()
        monkeypatch.setattr(queue_mod, "get_queue", lambda: fake)
        assert enqueue_analysis("c-1") == "job-123"

    def test_passes_job_timeout(self, monkeypatch):
        fake = _FakeQueue()
        monkeypatch.setattr(queue_mod, "get_queue", lambda: fake)
        enqueue_analysis("c-1")
        assert fake.calls[0][2]["job_timeout"] == JOB_TIMEOUT

    @pytest.mark.parametrize(
        "exc", [RedisConnectionError("connection refused"), OSError("host unreachable")]
    )
    def test_wraps_connection_failures(self, monkeypatch, exc):
        monkeypatch.setattr(queue_mod, "get_queue", lambda: _FakeQueue(exc))
        with pytest.raises(EnqueueError):
            enqueue_analysis("c-1")

    def test_failure_clears_cached_connection(self, monkeypatch):
        """A stale socket would keep failing identically, so the client must be rebuilt."""
        queue_mod._redis_conn = object()
        queue_mod._queue = _FakeQueue(RedisConnectionError("boom"))
        monkeypatch.setattr(queue_mod, "get_queue", lambda: queue_mod._queue)
        with pytest.raises(EnqueueError):
            enqueue_analysis("c-1")
        assert queue_mod._redis_conn is None

    def test_unexpected_errors_are_not_swallowed(self, monkeypatch):
        monkeypatch.setattr(queue_mod, "get_queue", lambda: _FakeQueue(ValueError("bug")))
        with pytest.raises(ValueError):
            enqueue_analysis("c-1")


class TestQueueIsReachable:
    def test_true_when_ping_succeeds(self, monkeypatch):
        monkeypatch.setattr(queue_mod, "get_redis", lambda: type("R", (), {"ping": lambda s: True})())
        assert queue_is_reachable() is True

    def test_false_when_redis_down(self, monkeypatch):
        class _Down:
            def ping(self):
                raise RedisConnectionError("down")

        monkeypatch.setattr(queue_mod, "get_redis", lambda: _Down())
        assert queue_is_reachable() is False


class TestReaperConfiguration:
    def test_timeout_exceeds_job_timeout(self):
        """Reaping earlier than RQ's own timeout would kill healthy long-running jobs."""
        from app.config import get_settings

        assert get_settings().stuck_contract_timeout_seconds > JOB_TIMEOUT

    def test_reaper_targets_only_non_terminal_statuses(self):
        from app.db.models import ContractStatus
        from app.services.reaper import STUCK_STATUSES

        assert set(STUCK_STATUSES) == {ContractStatus.uploaded, ContractStatus.processing}
        assert ContractStatus.done not in STUCK_STATUSES
        assert ContractStatus.failed not in STUCK_STATUSES

    def test_disabled_loop_returns_immediately(self, monkeypatch):
        from app.config import Settings, get_settings
        from app.services import reaper

        get_settings.cache_clear()
        monkeypatch.setenv("REAPER_INTERVAL_SECONDS", "0")
        try:
            reaper.run_reaper_forever()  # must not block
        finally:
            monkeypatch.delenv("REAPER_INTERVAL_SECONDS", raising=False)
            get_settings.cache_clear()
        assert Settings().reaper_interval_seconds > 0


class TestSessionScoping:
    """
    asyncpg connections belong to the loop that created them, and RQ runs every job in a
    fresh `asyncio.run`. A process-wide engine therefore fails on the second job with
    "attached to a different loop".
    """

    def test_engine_is_created_per_loop(self):
        import asyncio

        from app.db.session import dispose_engine, get_engine

        async def grab():
            try:
                return id(get_engine())
            finally:
                await dispose_engine()

        first, second = asyncio.run(grab()), asyncio.run(grab())
        assert first != second

    def test_same_loop_reuses_one_engine(self):
        import asyncio

        from app.db.session import dispose_engine, get_engine

        async def grab_twice():
            try:
                return id(get_engine()), id(get_engine())
            finally:
                await dispose_engine()

        a, b = asyncio.run(grab_twice())
        assert a == b

    def test_dispose_clears_the_registry(self):
        import asyncio

        from app.db import session as session_mod

        async def cycle():
            session_mod.get_engine()
            await session_mod.dispose_engine()

        asyncio.run(cycle())
        assert session_mod._engines == {}
        assert session_mod._sessionmakers == {}

    def test_worker_job_disposes_the_engine(self):
        from pathlib import Path

        src = Path("app/services/queue.py").read_text(encoding="utf-8")
        assert "dispose_engine" in src


class TestUploadRouteWiring:
    """Static wiring checks: the route must handle enqueue failure, not just call it."""

    def _source(self) -> str:
        from pathlib import Path

        return Path("app/api/routes_upload.py").read_text(encoding="utf-8")

    def test_catches_enqueue_error(self):
        src = self._source()
        assert "EnqueueError" in src
        assert "ContractStatus.failed" in src

    def test_persists_job_id(self):
        assert "job_id" in self._source()

    def test_returns_503_when_queue_is_down(self):
        assert "503" in self._source()
