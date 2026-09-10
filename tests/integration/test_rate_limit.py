import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from redis import Redis
from redis.exceptions import ConnectionError

from secrecon.config import Settings
from secrecon.ingestion.rate_limit import RateLimiter

pytestmark = pytest.mark.integration


def test_shared_rate_budget_across_workers():
    redis = Redis.from_url(Settings().redis_url)
    namespace = "test:limit:" + uuid4().hex

    def request(_):
        RateLimiter(redis, 2, namespace).acquire()
        return time.monotonic()

    with ThreadPoolExecutor(max_workers=4) as executor:
        times = sorted(executor.map(request, range(4)))
    assert times[-1] - times[0] >= 1.45
    assert all(right - left >= 0.45 for left, right in zip(times, times[1:], strict=False))


def test_rate_limit_fails_closed_when_redis_is_unavailable():
    redis = Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.1)
    with pytest.raises(ConnectionError):
        RateLimiter(redis).acquire()
