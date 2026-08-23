"""
Fixed-window rate limiting (KNOWN_ISSUES #5).

Two windows, because the two kinds of traffic cost wildly different amounts:

* a general per-minute cap on every request, to blunt scraping and accidental poll storms;
* a per-hour cap on uploads, because each upload spends real LLM budget.

State lives in Redis so the limit holds across API replicas, keyed on a fixed window
(`INCR` + `EXPIRE`) rather than a sliding one — that costs one round trip instead of a
sorted-set read/write and is accurate enough for abuse control.

If Redis is unreachable the limiter **fails open** and falls back to a per-process
in-memory window. Rate limiting is a protective measure, not a correctness one: refusing
all traffic because the limiter is down would convert a Redis blip into an outage. The
fallback still bounds a single process, so an attacker gains only the replica count.
"""
import logging
import time
from threading import Lock

from redis.exceptions import RedisError

from app.services.queue import get_redis

logger = logging.getLogger(__name__)

KEY_PREFIX = "ratelimit"

# Fallback state: {key: (window_start, count)}. Bounded by _MAX_LOCAL_KEYS so a spray of
# spoofed client identities cannot grow it without limit.
_local: dict[str, tuple[int, int]] = {}
_local_lock = Lock()
_MAX_LOCAL_KEYS = 10_000


class RateLimitExceeded(Exception):
    """The caller exceeded its window. `retry_after` is whole seconds."""

    def __init__(self, retry_after: int, limit: int, window_seconds: int):
        super().__init__(f"rate limit exceeded: {limit} per {window_seconds}s")
        self.retry_after = max(1, retry_after)
        self.limit = limit
        self.window_seconds = window_seconds


def _window_start(now: float, window_seconds: int) -> int:
    return int(now // window_seconds) * window_seconds


def _hit_local(key: str, limit: int, window_seconds: int, now: float) -> int:
    start = _window_start(now, window_seconds)
    with _local_lock:
        if len(_local) > _MAX_LOCAL_KEYS:
            # Cheapest safe eviction: drop everything whose window has already closed.
            for k, (s, _) in list(_local.items()):
                if s < start:
                    _local.pop(k, None)
        prev_start, count = _local.get(key, (start, 0))
        count = count + 1 if prev_start == start else 1
        _local[key] = (start, count)
        return count


def _hit_redis(key: str, window_seconds: int, now: float) -> int | None:
    """Increment the window counter in Redis. None when Redis is unusable."""
    redis_key = f"{KEY_PREFIX}:{key}:{_window_start(now, window_seconds)}"
    try:
        pipe = get_redis().pipeline()
        pipe.incr(redis_key, 1)
        # Expiry is set on every hit rather than only the first: a key that lost its TTL
        # (a failed EXPIRE, a restored snapshot) would otherwise block the caller forever.
        pipe.expire(redis_key, window_seconds + 1)
        return int(pipe.execute()[0])
    except (RedisError, OSError) as e:
        logger.warning(f"[RateLimit] Redis unavailable, falling back to local window: {e}")
        return None


def check(key: str, limit: int, window_seconds: int, now: float | None = None) -> int:
    """
    Register one hit against `key`. Returns the hit count within the current window.

    Raises RateLimitExceeded once the count passes `limit`. A `limit` of 0 or less
    disables the check entirely.
    """
    if limit <= 0:
        return 0

    now = time.time() if now is None else now
    count = _hit_redis(key, window_seconds, now)
    if count is None:
        count = _hit_local(key, limit, window_seconds, now)

    if count > limit:
        elapsed = now - _window_start(now, window_seconds)
        raise RateLimitExceeded(int(window_seconds - elapsed) + 1, limit, window_seconds)
    return count


def reset() -> None:
    """Clear the in-memory fallback. Tests only; Redis windows expire on their own."""
    with _local_lock:
        _local.clear()
