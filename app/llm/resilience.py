"""Protecting ourselves (and Google's free tier) from each other.

RateLimiter   - a token bucket in Redis, so ALL worker processes together stay under
                the per-minute limit. A per-process limiter would let 4 workers send
                4x the limit.
DailyQuota    - a per-day counter in Redis; when it runs out, degrade to rules.
CircuitBreaker- after repeated failures, stop calling Gemini for a while and fail fast.
"""

import time
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum

from redis.asyncio import Redis

# Runs inside Redis as one atomic step. Reading the bucket, refilling it and taking a token
# must happen together; done as separate commands from Python, two workers could both
# read "1 token left" and both take it.
_TOKEN_BUCKET = """
local key = KEYS[1]
local rate = tonumber(ARGV[1])        -- tokens added per second
local capacity = tonumber(ARGV[2])    -- bucket size (max burst)
local t = redis.call('TIME')          -- Redis's clock, the same for every worker
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
local state = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(state[1]) or capacity
local ts = tonumber(state[2]) or now
tokens = math.min(capacity, tokens + (now - ts) * rate)
local wait = 0
if tokens >= 1 then
  tokens = tokens - 1
else
  wait = (1 - tokens) / rate
end
redis.call('HSET', key, 'tokens', tokens, 'ts', now)
redis.call('EXPIRE', key, 3600)
return tostring(wait)
"""


class RateLimiter:
    def __init__(self, redis: Redis, key: str, per_minute: float, burst: int | None = None):
        self.redis = redis
        self.key = key
        self.rate = per_minute / 60.0
        self.capacity = burst or max(1, int(per_minute // 6))  # allow ~10 s worth at once
        self._script = redis.register_script(_TOKEN_BUCKET)

    async def try_acquire(self) -> float:
        """Take a token. Returns 0 if we got one, else the seconds to wait before retrying."""
        wait = await self._script(keys=[self.key], args=[self.rate, self.capacity])
        return float(wait)


class DailyQuota:
    def __init__(self, redis: Redis, key_prefix: str, per_day: int):
        self.redis = redis
        self.prefix = key_prefix
        self.per_day = per_day

    async def take(self) -> bool:
        key = f"{self.prefix}:{datetime.now(UTC):%Y-%m-%d}"
        # INCR is atomic, so two workers can't both take the last unit.
        used = await self.redis.incr(key)
        if used == 1:
            await self.redis.expire(key, 2 * 24 * 3600)
        return used <= self.per_day


class BreakerState(StrEnum):
    CLOSED = "closed"  # normal: calls go through
    OPEN = "open"  # tripped: fail fast, don't even try
    HALF_OPEN = "half_open"  # cool-down over: let one trial call through


class CircuitBreaker:
    """Like the breaker in your home's fuse box. When something keeps failing, cut it off
    so we stop wasting time (and rate-limit budget) on calls that will fail. After a
    cool-down, try once; success closes the circuit, failure opens it again.

    Per process (in memory). Good enough here: each worker learns on its own within a
    few failures. A shared Redis breaker would react faster across many workers.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        cooldown_seconds: float = 60,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.failure_threshold = failure_threshold
        self.cooldown = cooldown_seconds
        self.clock = clock  # injectable, so tests don't have to sleep
        self.failures = 0
        self.opened_at: float | None = None
        self.trial_in_flight = False

    @property
    def state(self) -> BreakerState:
        if self.opened_at is None:
            return BreakerState.CLOSED
        if self.clock() - self.opened_at >= self.cooldown:
            return BreakerState.HALF_OPEN
        return BreakerState.OPEN

    def allow(self) -> bool:
        state = self.state
        if state == BreakerState.CLOSED:
            return True
        if state == BreakerState.HALF_OPEN and not self.trial_in_flight:
            self.trial_in_flight = True  # exactly one trial call
            return True
        return False

    def record_success(self) -> None:
        self.failures, self.opened_at, self.trial_in_flight = 0, None, False

    def record_failure(self) -> None:
        self.failures += 1
        if self.state == BreakerState.HALF_OPEN or self.failures >= self.failure_threshold:
            self.opened_at = self.clock()  # (re)open, restarting the cool-down
        self.trial_in_flight = False
