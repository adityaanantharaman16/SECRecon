"""Container-side steps for the isolated database outage and storage restart drill."""

import json
import sys

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from secrecon.jobs import store
from secrecon.jobs.queue import Queue
from secrecon.runtime import services

phase, key = sys.argv[1:]
with services() as deps:
    queue = Queue(deps.redis, stream="drill:" + key)
    if phase == "seed":
        deps.archive.initialize()
        source = deps.archive.preserve(
            b"persistent source", kind="document", url="https://www.sec.gov/test", cik="0001234567"
        )
        with deps.engine.begin() as connection:
            job = store.enqueue(connection, "drill", {}, key)
            connection.execute(
                text("INSERT INTO system_state VALUES (:key,:data)"),
                {"key": key, "data": json.dumps({"job": job, "event": source.event_id})},
            )
        queue.initialize()
        queue.publish(job)
        messages = queue.read("interrupted", block_ms=10)
        assert len(messages) == 1
        lease = store.claim(deps.engine, job, "interrupted", 60)
        assert lease is not None
        deps.redis.set(key, job)
        print("seeded running SQL job, pending Redis delivery and immutable raw source")
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
        store.sweep(deps.engine, notification_seconds=0)
        lease = store.claim(deps.engine, info["job"], "recovered", 60)
        store.finish(deps.engine, lease, lambda connection: None)
        assert store.inspect(deps.engine, info["job"])["state"] == "succeeded"
        source = deps.archive.get_manifest(info["event"])
        assert deps.archive.load(source) == b"persistent source"
        print("recovered job; prior attempt retained; source bytes survive object-store restart")
