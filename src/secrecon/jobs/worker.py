"""Prepare outside SQL transactions; commit only with the current fenced lease."""

import logging
import threading
from collections.abc import Callable
from typing import Protocol

from botocore.exceptions import BotoCoreError, ClientError
from redis.exceptions import RedisError
from sqlalchemy import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

from secrecon.config import Settings
from secrecon.ingestion.adapters import SchemaError
from secrecon.ingestion.client import FetchError
from secrecon.ingestion.rate_limit import AccessPaused
from secrecon.jobs import store
from secrecon.jobs.queue import Queue
from secrecon.storage.archive import ArchiveIntegrityError

logger = logging.getLogger(__name__)


class Handler(Protocol):
    def __call__(self, lease: store.Lease) -> Callable[[Connection], None]: ...


class Worker:
    def __init__(
        self, engine: Engine, queue: Queue, settings: Settings, handler: Handler, owner: str
    ) -> None:
        self.engine, self.queue, self.settings = engine, queue, settings
        self.handler, self.owner = handler, owner

    def process(self, job_id: str, *, after_commit: Callable[[], None] | None = None) -> bool:
        lease = store.claim(self.engine, job_id, self.owner, self.settings.lease_seconds)
        if lease is None:
            return True  # SQL retains terminal, future or currently owned work.
        stop = threading.Event()

        def renew() -> None:
            while not stop.wait(self.settings.heartbeat_seconds):
                try:
                    if not store.heartbeat(self.engine, lease, self.settings.lease_seconds):
                        return
                except SQLAlchemyError:
                    logger.warning("heartbeat_dependency_failure", extra={"job_id": job_id})
                    return

        thread = threading.Thread(target=renew, daemon=True)
        thread.start()
        try:
            commit = self.handler(lease)
            store.finish(self.engine, lease, commit)
            if after_commit:
                after_commit()
            return True
        except store.LeaseLost:
            logger.info("stale_worker_rejected", extra={"job_id": job_id})
            return True
        except Exception as exc:
            quarantined = isinstance(exc, (SchemaError, ArchiveIntegrityError))
            retryable = isinstance(
                exc,
                (SQLAlchemyError, BotoCoreError, ClientError, RedisError, OSError, AccessPaused),
            )
            retry_after = 0.0
            if isinstance(exc, FetchError):
                retryable, retry_after = exc.retryable, exc.retry_after
            try:
                store.fail(
                    self.engine,
                    lease,
                    type(exc).__name__
                    + ": "
                    + (
                        str(exc)
                        if isinstance(exc, (SchemaError, ArchiveIntegrityError, FetchError))
                        else "processing failed"
                    ),
                    retryable=retryable,
                    quarantine=quarantined,
                    retry_after=retry_after,
                )
                return True
            except store.LeaseLost:
                return True
            except SQLAlchemyError:
                logger.warning("job_failure_not_persisted", extra={"job_id": job_id})
                return False
        finally:
            stop.set()
            thread.join(timeout=6)

    def run(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                for message_id, job_id in self.queue.read(self.owner):
                    if stop.is_set():
                        break
                    if self.process(job_id):
                        self.queue.ack(message_id)
            except (SQLAlchemyError, RedisError):
                logger.warning("worker_dependency_unavailable")
                stop.wait(2)
