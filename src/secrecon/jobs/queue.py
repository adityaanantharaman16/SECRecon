"""Small Redis delivery adapter; SQL jobs can reconstruct the entire queue."""

from __future__ import annotations

import json
from typing import Any

from redis import Redis
from redis.exceptions import ResponseError
from sqlalchemy import Engine, text


class Queue:
    def __init__(
        self, redis: Redis[bytes], stream: str = "secrecon:jobs", group: str = "workers"
    ) -> None:
        self.redis, self.stream, self.group = redis, stream, group

    def initialize(self) -> None:
        try:
            self.redis.xgroup_create(self.stream, self.group, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def publish(self, job_id: str, trace_context: dict[str, str] | None = None) -> Any:
        return self.redis.xadd(
            self.stream, {"job_id": job_id, "trace_context": json.dumps(trace_context or {})}
        )

    def ack(self, message_id: Any) -> None:
        self.redis.xack(self.stream, self.group, message_id)  # type: ignore[no-untyped-call]
        # One consumer group in v1; durable history lives in SQL, not this transport.
        self.redis.xdel(self.stream, message_id)

    def read(self, consumer: str, block_ms: int = 1000) -> list[tuple[Any, str]]:
        self.initialize()
        # Reclaim abandoned deliveries; actual ownership is checked in PostgreSQL.
        claimed = self.redis.xautoclaim(self.stream, self.group, consumer, 60000, "0-0", count=10)
        messages = claimed[1]
        if not messages:
            entries = self.redis.xreadgroup(
                self.group, consumer, {self.stream: ">"}, count=10, block=block_ms
            )
            messages = entries[0][1] if entries else []
        return [(message_id, fields[b"job_id"].decode()) for message_id, fields in messages]

    def dispatch(self, engine: Engine) -> int:
        self.initialize()
        with engine.begin() as connection:
            rows = (
                connection.execute(
                    text("""
                SELECT o.id,o.job_id,j.trace_context FROM outbox o JOIN jobs j ON j.id=o.job_id
                WHERE o.sent_at IS NULL AND j.due_at<=clock_timestamp()
                AND j.state IN ('queued','retry_wait')
                ORDER BY j.priority DESC,j.due_at,o.id LIMIT 100
                FOR UPDATE OF j SKIP LOCKED
            """)
                )
                .mappings()
                .all()
            )
            for row in rows:
                self.publish(row["job_id"], row["trace_context"])
                connection.execute(
                    text("UPDATE outbox SET sent_at=now() WHERE id=:id"), {"id": row["id"]}
                )
                connection.execute(
                    text("UPDATE jobs SET last_notified_at=now() WHERE id=:id"),
                    {"id": row["job_id"]},
                )
            return len(rows)
