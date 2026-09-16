"""Atomic operator requests and durable background completion."""

from collections.abc import Callable
from datetime import date
from typing import Any
from uuid import uuid4

from sqlalchemy import Connection, Engine, text

from secrecon.db.queries import selected_generation
from secrecon.db.reconciliation import reconcile
from secrecon.domain.types import canonical, fingerprint
from secrecon.jobs import store
from secrecon.orchestration import planner, replay
from secrecon.storage.archive import Archive


def submit(
    connection: Connection, action: str, body: dict[str, Any], key: str, actor: str
) -> dict[str, Any]:
    if not 1 <= len(key) <= 128:
        raise ValueError("Idempotency-Key must contain 1-128 characters")
    request_hash = fingerprint({"action": action, "body": body})
    # Serialize a key before looking up its existing response, including concurrent submissions.
    connection.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k,0))"), {"k": "admin:" + key}
    )
    existing = (
        connection.execute(
            text("SELECT * FROM admin_requests WHERE idempotency_key=:k"), {"k": key}
        )
        .mappings()
        .first()
    )
    if existing:
        if existing["request_hash"] != request_hash:
            raise store.IdempotencyConflict("Idempotency key belongs to another action/body")
        return {
            "operation_id": existing["id"],
            "job_id": existing["job_id"],
            "status_url": "/v1/admin/operations/" + existing["id"],
        }
    operation = str(uuid4())
    payload = dict(body)
    if action == "reconcile":
        payload["generation"] = selected_generation(connection, body["generation"])
        for accession in (body["original"], body["amendment"]):
            if not connection.scalar(
                text("SELECT accession FROM filings WHERE generation=:g AND accession=:a"),
                {"g": payload["generation"], "a": accession},
            ):
                raise ValueError("Both filings must exist in selected generation")
    elif action == "backfill":
        allowed = set(connection.execute(text("SELECT cik FROM watchlist WHERE enabled")).scalars())
        if not set(body["ciks"]) <= allowed:
            raise ValueError("Backfill companies must belong to the enabled watchlist")
    elif action == "replay":
        payload["generation"] = "replay-" + operation
    elif action in {"redrive", "cancel"}:
        table = "jobs" if action == "redrive" else "backfills"
        row = (
            connection.execute(
                text(f"SELECT * FROM {table} WHERE id=:id FOR UPDATE"), {"id": body["id"]}
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ValueError("Target operation does not exist")
        if action == "redrive" and row["state"] not in {"dead_letter", "quarantined"}:
            raise ValueError("Only terminal failed jobs can be redriven")
    else:
        raise ValueError("Unsupported operator action")
    payload.update(action=action, operation_id=operation)
    job = store.enqueue(connection, "operation", payload, "operation:" + operation, priority=15)
    connection.execute(
        text(
            "INSERT INTO admin_requests(id,idempotency_key,request_hash,action,body,job_id,actor) VALUES (:id,:k,:h,:a,CAST(:b AS jsonb),:j,:actor)"
        ),
        {
            "id": operation,
            "k": key,
            "h": request_hash,
            "a": action,
            "b": canonical(body),
            "j": job,
            "actor": actor,
        },
    )
    return {
        "operation_id": operation,
        "job_id": job,
        "status_url": "/v1/admin/operations/" + operation,
    }


def prepare(engine: Engine, archive: Archive, lease: store.Lease) -> Callable[[Connection], None]:
    payload = lease.payload
    result: dict[str, Any] = {}
    if payload["action"] == "replay":
        # Every replay write is fenced; checkpoints make a later lease resumable.
        result = replay.rebuild(
            engine,
            archive,
            payload["generation"],
            resume=True,
            parser_version=payload["parser_version"],
            guard=lambda connection: store.owned(connection, lease),
        )
        result["generation"] = payload["generation"]

    def commit(connection: Connection) -> None:
        action = payload["action"]
        if action == "reconcile":
            result["comparison_id"] = reconcile(
                connection,
                payload["original"],
                payload["amendment"],
                payload["generation"],
                payload.get("original_event"),
                payload.get("amendment_event"),
            )
        elif action == "backfill":
            result["backfill_id"] = planner.create_backfill(
                connection,
                payload["ciks"],
                date.fromisoformat(payload["start"]),
                date.fromisoformat(payload["end"]),
                payload["max_jobs"],
            )
        elif action == "cancel":
            planner.cancel_backfill(connection, payload["id"])
            result["backfill_id"] = payload["id"]
        elif action == "redrive":
            result["job_id"] = store.redrive(connection, payload["id"])
        connection.execute(
            text("UPDATE admin_requests SET result=CAST(:r AS jsonb) WHERE id=:id"),
            {"r": canonical(result), "id": payload["operation_id"]},
        )

    return commit
