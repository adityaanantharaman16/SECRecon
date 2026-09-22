"""Container-side phases for the isolated database outage and storage restart drill."""

from __future__ import annotations

import hashlib
import json
import sys
import time
from collections.abc import Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from secrecon.jobs import store
from secrecon.jobs.queue import Queue
from secrecon.jobs.worker import Worker
from secrecon.runtime import services
from secrecon.telemetry import runtime as telemetry

JSON_MARKER = "DRILL_JSON:"
SOURCE_BYTES = b"persistent source"


def wait_for_archive(operation: Callable[[], None], timeout_seconds: float = 30) -> float:
    started = time.monotonic()
    deadline = started + timeout_seconds
    while True:
        try:
            operation()
            return time.monotonic() - started
        except Exception:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.5)


def snapshot(deps: Any, key: str) -> dict[str, Any]:
    with deps.engine.connect() as connection:
        info = json.loads(
            connection.scalar(text("SELECT value FROM system_state WHERE key=:key"), {"key": key})
        )
        job = (
            connection.execute(
                text(
                    "SELECT state,owner,token,correlation_id,trace_context FROM jobs WHERE id=:id"
                ),
                {"id": info["job"]},
            )
            .mappings()
            .one()
        )
        attempts = list(
            connection.execute(
                text(
                    "SELECT token,outcome,trace_id,span_id FROM job_attempts WHERE job_id=:id ORDER BY token"
                ),
                {"id": info["job"]},
            ).mappings()
        )
        event_states = list(
            connection.execute(
                text("SELECT state FROM job_events WHERE job_id=:id ORDER BY id"),
                {"id": info["job"]},
            ).scalars()
        )
        outbox_pending = connection.scalar(
            text("SELECT count(*) FROM outbox WHERE job_id=:id AND sent_at IS NULL"),
            {"id": info["job"]},
        )
    source = deps.archive.get_manifest(info["event"])
    raw = deps.archive.load(source)
    queue = Queue(deps.redis, stream="drill:" + key)
    pending = queue.redis.xpending(queue.stream, queue.group)["pending"]
    trace_ids = sorted({row["trace_id"] for row in attempts if row["trace_id"]})
    return {
        "job_state": job["state"],
        "job_owner": job["owner"],
        "job_token": job["token"],
        "attempt_count": len(attempts),
        "attempt_outcomes": [row["outcome"] for row in attempts],
        "attempts": [dict(row) for row in attempts],
        "event_states": event_states,
        "outbox_pending": outbox_pending,
        "redis_pending": pending,
        "source_byte_length": len(raw),
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "correlation_id": job["correlation_id"],
        "trace_context": job["trace_context"],
        "trace_ids": trace_ids,
    }


def main() -> None:
    phase, key = sys.argv[1:]
    with services() as deps:
        queue = Queue(deps.redis, stream="drill:" + key)
        if phase == "seed":
            archive_ready_seconds = wait_for_archive(deps.archive.initialize)
            with telemetry.span("failure_drill.seed"):
                source = deps.archive.preserve(
                    SOURCE_BYTES,
                    kind="document",
                    url="https://www.sec.gov/test",
                    cik="0001234567",
                )
                with deps.engine.begin() as connection:
                    job = store.enqueue(connection, "drill", {}, key)
                assert queue.dispatch(deps.engine) == 1
                messages = queue.read("interrupted", block_ms=10)
                assert len(messages) == 1
                message_id, delivered_job = messages[0]
                assert delivered_job == job
                persisted_message_id = (
                    message_id.decode() if isinstance(message_id, bytes) else str(message_id)
                )
                lease = store.claim(deps.engine, job, "interrupted", 60)
                assert lease is not None
                with deps.engine.begin() as connection:
                    connection.execute(
                        text("INSERT INTO system_state VALUES (:key,:data)"),
                        {
                            "key": key,
                            "data": json.dumps(
                                {
                                    "job": job,
                                    "event": source.event_id,
                                    "message_id": persisted_message_id,
                                }
                            ),
                        },
                    )
                deps.redis.set(key, job)
            print(
                "seeded running SQL job, pending Redis delivery and immutable raw source; "
                f"archive ready in {archive_ready_seconds:.3f}s"
            )
        elif phase == "snapshot":
            print(JSON_MARKER + json.dumps(snapshot(deps, key), sort_keys=True))
        elif phase == "outage":
            job = deps.redis.get(key).decode()
            assert queue.redis.xpending(queue.stream, queue.group)["pending"] == 1
            try:
                store.claim(deps.engine, job, "during-outage", 60)
            except SQLAlchemyError:
                assert queue.redis.xpending(queue.stream, queue.group)["pending"] == 1
                print("SQL unavailable: no acknowledgement; delivery remains pending")
            else:
                raise AssertionError("Database was unexpectedly available")
        elif phase == "recover":
            with deps.engine.begin() as connection:
                info = json.loads(
                    connection.scalar(
                        text("SELECT value FROM system_state WHERE key=:key"), {"key": key}
                    )
                )
                connection.execute(
                    text("UPDATE jobs SET lease_until=now()-interval '1 second' WHERE id=:id"),
                    {"id": info["job"]},
                )
            assert store.sweep(deps.engine, notification_seconds=3600) == 0
            worker = Worker(
                deps.engine,
                queue,
                deps.settings,
                lambda lease: lambda connection: None,
                "recovered",
            )
            assert worker.process(info["job"])
            assert store.inspect(deps.engine, info["job"])["state"] == "succeeded"
            queue.ack(info["message_id"])

            def verify_source() -> None:
                source = deps.archive.get_manifest(info["event"])
                assert deps.archive.load(source) == SOURCE_BYTES

            archive_ready_seconds = wait_for_archive(verify_source)
            print(
                "recovered job; prior attempt retained; source bytes survive object-store restart; "
                f"archive ready in {archive_ready_seconds:.3f}s"
            )
        else:
            raise ValueError(f"unknown outage probe phase: {phase}")


if __name__ == "__main__":
    main()
