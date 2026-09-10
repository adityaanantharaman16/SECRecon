"""Composition root: create dependencies once, and close them deterministically."""

from __future__ import annotations

import logging
import signal
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from redis import Redis
from sqlalchemy import Engine

from secrecon.config import Settings
from secrecon.db.session import make_engine
from secrecon.ingestion.client import SecClient
from secrecon.ingestion.rate_limit import RateLimiter
from secrecon.jobs.queue import Queue
from secrecon.storage.archive import Archive


@dataclass
class Services:
    settings: Settings
    engine: Engine
    archive: Archive
    redis: Redis[bytes]
    queue: Queue
    client: SecClient


@contextmanager
def services() -> Iterator[Services]:
    settings = Settings()
    engine = make_engine(settings)
    redis = Redis.from_url(settings.redis_url, socket_connect_timeout=3, socket_timeout=5)
    archive = Archive(settings)
    client = SecClient(
        settings, engine, archive, RateLimiter(redis, settings.sec_requests_per_second)
    )
    try:
        yield Services(settings, engine, archive, redis, Queue(redis), client)
    finally:
        client.close()
        redis.close()
        engine.dispose()


def shutdown_event() -> threading.Event:
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    return stop
