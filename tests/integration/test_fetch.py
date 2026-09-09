from uuid import uuid4

import httpx
import pytest
from redis import Redis
from sqlalchemy import text

from secrecon.config import Settings
from secrecon.ingestion.client import FetchError, SecClient
from secrecon.ingestion.rate_limit import RateLimiter

pytestmark = pytest.mark.integration


def client(engine, archive, handler, **overrides):
    settings = Settings().model_copy(
        update={"sec_mode": "live", "sec_user_agent": "SECRecon tests@example.invalid", **overrides}
    )
    limiter = RateLimiter(Redis.from_url(settings.redis_url), namespace="test:" + uuid4().hex)
    return SecClient(settings, engine, archive, limiter, transport=httpx.MockTransport(handler))


def test_http_errors_are_preserved_and_retry_after_respected(engine, archive):
    fetcher = client(
        engine,
        archive,
        lambda request: httpx.Response(429, content=b"slow down", headers={"Retry-After": "120"}),
    )
    with pytest.raises(FetchError) as captured:
        fetcher.fetch("https://data.sec.gov/test", "facts", "0001234567")
    assert captured.value.retryable
    assert captured.value.retry_after == 120
    source = list(archive.manifests())[0]
    assert archive.load(source) == b"slow down"
    assert source.status == 429
    fetcher.close()


def test_oversized_response_is_labeled_incomplete(engine, archive):
    fetcher = client(
        engine,
        archive,
        lambda request: httpx.Response(200, content=b"123456"),
        max_response_bytes=3,
    )
    with pytest.raises(FetchError):
        fetcher.fetch("https://data.sec.gov/test", "facts", "0001234567")
    source = list(archive.manifests())[0]
    assert not source.complete
    assert archive.load(source) == b"123"
    fetcher.close()


def test_transport_failure_has_durable_outcome(engine, archive):
    def fail(request):
        raise httpx.ReadTimeout("injected")

    fetcher = client(engine, archive, fail)
    with pytest.raises(FetchError):
        fetcher.fetch("https://data.sec.gov/transport-probe", "facts", "0001234567")
    with engine.connect() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM fetch_attempts WHERE url='https://data.sec.gov/transport-probe' AND status='transport_error'"
                )
            )
            >= 1
        )
    assert list(archive.manifests()) == []
    fetcher.close()


def test_403_pauses_other_requests(engine, archive):
    from secrecon.ingestion.rate_limit import AccessPaused

    fetcher = client(engine, archive, lambda request: httpx.Response(403))
    with pytest.raises(FetchError):
        fetcher.fetch("https://data.sec.gov/test", "facts", "0001234567")
    with pytest.raises(AccessPaused):
        fetcher.fetch("https://data.sec.gov/test", "facts", "0001234567")
    fetcher.close()
