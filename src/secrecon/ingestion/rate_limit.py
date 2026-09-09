"""One fail-closed SEC request budget shared by all local processes."""

from __future__ import annotations

import time

from redis import Redis

LUA = """
local clock = redis.call('TIME')
local now = tonumber(clock[1]) * 1000 + math.floor(tonumber(clock[2]) / 1000)
local next_at = tonumber(redis.call('GET', KEYS[1]) or '0')
if next_at > now then return next_at - now end
redis.call('SET', KEYS[1], now + tonumber(ARGV[1]), 'PX', math.ceil(tonumber(ARGV[1]) * 2))
return 0
"""


class AccessPaused(RuntimeError):
    pass


class RateLimiter:
    def __init__(
        self, redis: Redis[bytes], requests_per_second: float = 2, namespace: str = "secrecon:sec"
    ) -> None:
        self.redis = redis
        self.interval_ms = 1000 / requests_per_second
        self.key = namespace + ":rate"
        self.pause_key = namespace + ":paused"

    def acquire(self) -> None:
        while True:
            if self.redis.exists(self.pause_key):
                raise AccessPaused("SEC access paused after 403; investigate before resuming")
            delay = int(self.redis.eval(LUA, 1, self.key, self.interval_ms))  # type: ignore[no-untyped-call]
            if delay == 0:
                return
            time.sleep(delay / 1000)

    def pause(self) -> None:
        self.redis.set(self.pause_key, "403")
