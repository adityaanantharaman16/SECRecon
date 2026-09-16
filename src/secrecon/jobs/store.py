"""Durable SQL state machine. Redis ownership is never a commit authorization."""

import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import Connection, Engine, text

from secrecon.db.transactions import transaction
from secrecon.domain.types import canonical, fingerprint
from secrecon.telemetry import runtime as telemetry


class LeaseLost(RuntimeError):
    pass


class IdempotencyConflict(ValueError):
    pass


class Cancelled(RuntimeError):
    pass


def cancel_claim(engine: Engine, lease: "Lease") -> None:
    with engine.begin() as connection:
        owned(connection, lease)
        connection.execute(
            text(
                "UPDATE jobs SET state='cancelled',lease_until=NULL,updated_at=now() WHERE id=:id"
            ),
            {"id": lease.job_id},
        )
        connection.execute(
            text(
                "UPDATE job_attempts SET outcome='cancelled',ended_at=now() WHERE job_id=:id AND token=:token"
            ),
            {"id": lease.job_id, "token": lease.token},
        )
        event(connection, lease.job_id, "cancelled")


@dataclass(frozen=True)
class Lease:
    job_id: str
    kind: str
    payload: dict[str, Any]
    owner: str
    token: int


def event(connection: Connection, job_id: str, state: str, **details: Any) -> None:
    connection.execute(
        text(
            "INSERT INTO job_events(job_id,state,details) VALUES (:id,:state,CAST(:details AS jsonb))"
        ),
        {"id": job_id, "state": state, "details": canonical(details)},
    )


def notify(connection: Connection, job_id: str) -> None:
    connection.execute(
        text("INSERT INTO outbox(job_id) VALUES (:id) ON CONFLICT DO NOTHING"), {"id": job_id}
    )


@telemetry.traced("job.enqueue")
def enqueue(
    connection: Connection,
    kind: str,
    payload: dict[str, Any],
    key: str,
    *,
    priority: int = 0,
    due_at: datetime | None = None,
    parent_id: str | None = None,
) -> str:
    job_id = str(uuid4())
    digest = fingerprint({"kind": kind, "payload": payload})
    inserted = connection.scalar(
        text("""
        INSERT INTO jobs(id,kind,payload,idempotency_key,request_hash,priority,due_at,parent_id,trace_context,correlation_id)
        VALUES (:id,:kind,CAST(:payload AS jsonb),:key,:hash,:priority,COALESCE(:due,now()),:parent,CAST(:trace AS jsonb),:correlation)
        ON CONFLICT(idempotency_key) DO NOTHING RETURNING id
    """),
        {
            "id": job_id,
            "kind": kind,
            "payload": canonical(payload),
            "key": key,
            "hash": digest,
            "priority": priority,
            "due": due_at,
            "parent": parent_id,
            "trace": canonical(telemetry.carrier()),
            "correlation": telemetry.ids()[0],
        },
    )
    if not inserted:
        existing = (
            connection.execute(
                text("SELECT id,request_hash FROM jobs WHERE idempotency_key=:key"), {"key": key}
            )
            .mappings()
            .one()
        )
        if existing["request_hash"] != digest:
            raise IdempotencyConflict("Idempotency key already belongs to another request")
        return str(existing["id"])
    event(connection, job_id, "queued")
    notify(connection, job_id)
    return job_id


def claim(engine: Engine, job_id: str, owner: str, lease_seconds: int) -> Lease | None:
    with engine.begin() as connection:
        row = (
            connection.execute(
                text("""
            UPDATE jobs SET state='running',owner=:owner,token=token+1,attempts=attempts+1,
              lease_until=clock_timestamp()+make_interval(secs=>:seconds),updated_at=now()
            WHERE id=:id AND state IN ('queued','retry_wait')
              AND due_at<=clock_timestamp() AND attempts<max_attempts
            RETURNING id,kind,payload,token
        """),
                {"id": job_id, "owner": owner, "seconds": lease_seconds},
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        connection.execute(
            text(
                "INSERT INTO job_attempts(job_id,token,owner,trace_id,span_id) VALUES (:id,:token,:owner,:trace,:span)"
            ),
            {
                "id": job_id,
                "token": row["token"],
                "owner": owner,
                "trace": telemetry.ids()[0],
                "span": telemetry.ids()[1],
            },
        )
        event(connection, job_id, "running", owner=owner, token=row["token"])
        return Lease(job_id, row["kind"], row["payload"], owner, row["token"])


def heartbeat(engine: Engine, lease: Lease, seconds: int) -> bool:
    with engine.begin() as connection:
        result = connection.execute(
            text("""
            UPDATE jobs SET lease_until=clock_timestamp()+make_interval(secs=>:seconds)
            WHERE id=:id AND owner=:owner AND token=:token AND state='running'
              AND lease_until>clock_timestamp()
            RETURNING id
        """),
            {"id": lease.job_id, "owner": lease.owner, "token": lease.token, "seconds": seconds},
        )
        return result.scalar() is not None


def owned(connection: Connection, lease: Lease) -> dict[str, Any]:
    row = (
        connection.execute(text("SELECT * FROM jobs WHERE id=:id FOR UPDATE"), {"id": lease.job_id})
        .mappings()
        .one()
    )
    now = connection.scalar(text("SELECT clock_timestamp()"))
    if (
        row["state"] != "running"
        or row["token"] != lease.token
        or row["owner"] != lease.owner
        or row["lease_until"] <= now
    ):
        raise LeaseLost(lease.job_id)
    return dict(row)


def finish(engine: Engine, lease: Lease, commit: Callable[[Connection], None]) -> None:
    with engine.begin() as connection:
        owned(connection, lease)
        commit(connection)
        owned(connection, lease)
        # Lock held throughout projection write: a sweeper cannot transfer ownership.
        connection.execute(
            text("""
            UPDATE jobs SET state='succeeded',lease_until=NULL,updated_at=now(),error=NULL WHERE id=:id
        """),
            {"id": lease.job_id},
        )
        connection.execute(
            text("""
            UPDATE job_attempts SET outcome='succeeded',ended_at=now()
            WHERE job_id=:id AND token=:token
        """),
            {"id": lease.job_id, "token": lease.token},
        )
        event(connection, lease.job_id, "succeeded", token=lease.token)


def fail(
    engine: Engine,
    lease: Lease,
    reason: str,
    *,
    retryable: bool,
    quarantine: bool = False,
    retry_after: float = 0,
) -> str:
    with engine.begin() as connection:
        row = owned(connection, lease)
        state = "quarantined" if quarantine else "dead_letter"
        if retryable and row["attempts"] < row["max_attempts"]:
            state = "retry_wait"
        delay = max(retry_after, random.uniform(0, min(900, 5 * 2 ** (row["attempts"] - 1))))
        connection.execute(
            text("""
            UPDATE jobs SET state=:state,error=:error,lease_until=NULL,
              due_at=clock_timestamp()+make_interval(secs=>:delay),updated_at=now()
            WHERE id=:id
        """),
            {"id": lease.job_id, "state": state, "error": reason[:2000], "delay": delay},
        )
        connection.execute(
            text("""
            UPDATE job_attempts SET outcome=:state,error=:error,ended_at=now()
            WHERE job_id=:id AND token=:token
        """),
            {"id": lease.job_id, "token": lease.token, "state": state, "error": reason[:2000]},
        )
        event(connection, lease.job_id, state, error=reason[:2000], retry_delay=delay)
        if state == "retry_wait":
            notify(connection, lease.job_id)
        return state


def sweep(engine: Engine, notification_seconds: int = 30) -> int:
    with engine.begin() as connection:
        expired = (
            connection.execute(
                text("""
            SELECT id,token,attempts,max_attempts FROM jobs
            WHERE state='running' AND lease_until<=clock_timestamp()
            ORDER BY id FOR UPDATE SKIP LOCKED
        """)
            )
            .mappings()
            .all()
        )
        for row in expired:
            state = "dead_letter" if row["attempts"] >= row["max_attempts"] else "queued"
            connection.execute(
                text("""
                UPDATE jobs SET state=:state,owner=NULL,lease_until=NULL,
                  error='worker lease expired',due_at=now(),updated_at=now() WHERE id=:id
            """),
                {"id": row["id"], "state": state},
            )
            connection.execute(
                text("""
                UPDATE job_attempts SET outcome='lease_expired',ended_at=now()
                WHERE job_id=:id AND token=:token
            """),
                {"id": row["id"], "token": row["token"]},
            )
            event(connection, row["id"], state, reason="lease_expired", token=row["token"])
        due = (
            connection.execute(
                text("""
            SELECT id FROM jobs WHERE state IN ('queued','retry_wait') AND due_at<=clock_timestamp()
            AND (last_notified_at IS NULL OR last_notified_at < clock_timestamp()-make_interval(secs=>:seconds))
            ORDER BY priority DESC,due_at,id LIMIT 100 FOR UPDATE SKIP LOCKED
        """),
                {"seconds": notification_seconds},
            )
            .scalars()
            .all()
        )
        for job_id in due:
            notify(connection, job_id)
        return len(due)


def redrive(engine: Engine | Connection, job_id: str) -> str:
    with transaction(engine) as connection:
        row = (
            connection.execute(text("SELECT * FROM jobs WHERE id=:id FOR UPDATE"), {"id": job_id})
            .mappings()
            .one()
        )
        if row["state"] not in {"dead_letter", "quarantined"}:
            raise ValueError("Only terminal failed jobs can be redriven")
        return enqueue(
            connection, row["kind"], row["payload"], "redrive:" + str(uuid4()), parent_id=job_id
        )


def inspect(engine: Engine, job_id: str) -> dict[str, Any]:
    with engine.connect() as connection:
        job = dict(
            connection.execute(text("SELECT * FROM jobs WHERE id=:id"), {"id": job_id})
            .mappings()
            .one()
        )
        job["attempt_history"] = [
            dict(row)
            for row in connection.execute(
                text("SELECT * FROM job_attempts WHERE job_id=:id ORDER BY token"), {"id": job_id}
            ).mappings()
        ]
        job["events"] = [
            dict(row)
            for row in connection.execute(
                text("SELECT * FROM job_events WHERE job_id=:id ORDER BY id"), {"id": job_id}
            ).mappings()
        ]
        return job
