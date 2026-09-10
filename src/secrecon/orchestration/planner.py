"""Bounded discovery and durable scheduling; every checkpoint shares the child-job transaction."""

import random
from datetime import date, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import Connection, Engine, text

from secrecon.domain.types import cik_text, utcnow
from secrecon.ingestion.adapters import ParsedSource
from secrecon.jobs import store
from secrecon.storage.archive import Manifest

DEFAULT_WATCHLIST = ("0000320193", "0000789019", "0001652044", "0001018724", "0001874178")


def active_generation(connection: Connection) -> str:
    return str(
        connection.scalar(text("SELECT value FROM system_state WHERE key='active_generation'"))
    )


def add_watchlist(engine: Engine, ciks: list[str]) -> None:
    normalized = sorted({cik_text(cik) for cik in ciks})
    with engine.begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('watchlist',0))"))
        existing = set(
            connection.execute(text("SELECT cik FROM watchlist WHERE enabled")).scalars()
        )
        if len(existing | set(normalized)) > 25:
            raise ValueError("Demo watchlist is limited to 25 companies")
        for cik in normalized:
            connection.execute(
                text("""
                INSERT INTO watchlist(cik) VALUES (:cik)
                ON CONFLICT(cik) DO UPDATE SET enabled=true,next_poll_at=now()
            """),
                {"cik": cik},
            )


def discovery_payload(cik: str, start: date, end: date, **extra: Any) -> dict[str, Any]:
    return {
        "cik": cik,
        "url": f"https://data.sec.gov/submissions/CIK{cik}.json",
        "from": start.isoformat(),
        "to": end.isoformat(),
        **extra,
    }


def create_backfill(
    engine: Engine, ciks: list[str], start: date, end: date, max_jobs: int = 10000
) -> str:
    if start > end or end > utcnow().date() or (end - start).days > 3653:
        raise ValueError("Backfills require valid historical bounds of at most ten years")
    if not 1 <= max_jobs <= 10000:
        raise ValueError("max_jobs must be 1-10000")
    operation = str(uuid4())
    with engine.begin() as connection:
        selected = (
            sorted({cik_text(cik) for cik in ciks})
            if ciks
            else list(
                connection.execute(
                    text("SELECT cik FROM watchlist WHERE enabled ORDER BY cik")
                ).scalars()
            )
        )
        if not selected or len(selected) > 25 or max_jobs < len(selected):
            raise ValueError("Select 1-25 companies within the job limit")
        scope = {"ciks": selected, "from": start.isoformat(), "to": end.isoformat()}
        from secrecon.domain.types import canonical

        connection.execute(
            text(
                "INSERT INTO backfills(id,scope,max_jobs) VALUES (:id,CAST(:scope AS jsonb),:max)"
            ),
            {"id": operation, "scope": canonical(scope), "max": max_jobs},
        )
        for cik in selected:
            payload = discovery_payload(cik, start, end, backfill_id=operation)
            store.enqueue(
                connection, "discover", payload, f"backfill:{operation}:{cik}", priority=-10
            )
    return operation


def assert_operation_active(connection: Connection, payload: dict[str, Any]) -> None:
    if payload.get("backfill_id"):
        state = connection.scalar(
            text("SELECT state FROM backfills WHERE id=:id FOR UPDATE"),
            {"id": payload["backfill_id"]},
        )
        if state != "running":
            raise store.Cancelled("Backfill is no longer running")


def schedule_child(
    connection: Connection, kind: str, payload: dict[str, Any], key: str, priority: int
) -> str:
    assert_operation_active(connection, payload)
    if operation := payload.get("backfill_id"):
        limit = connection.scalar(
            text("SELECT max_jobs FROM backfills WHERE id=:id"), {"id": operation}
        )
        count = connection.scalar(
            text("SELECT count(*) FROM jobs WHERE payload->>'backfill_id'=:id"), {"id": operation}
        )
        exists = connection.scalar(
            text("SELECT id FROM jobs WHERE idempotency_key=:key"), {"key": key}
        )
        if count >= limit and not exists:
            raise ValueError("Backfill job cap exceeded; split the date/company scope")
    return store.enqueue(connection, kind, payload, key, priority=priority)


def after_discovery(
    connection: Connection,
    source: Manifest,
    parsed: ParsedSource,
    payload: dict[str, Any],
    job_id: str,
) -> None:
    assert_operation_active(connection, payload)
    operation = payload.get("backfill_id")
    priority = -10 if operation else 20
    start, end = payload["from"], payload["to"]
    common = {"cik": source.cik}
    if operation:
        common["backfill_id"] = operation
    for page in parsed.historical_pages:
        if page["filingTo"] >= start and page["filingFrom"] <= end:
            child = {
                **payload,
                "url": "https://data.sec.gov/submissions/" + page["name"],
                "historical": True,
            }
            schedule_child(
                connection,
                "discover",
                child,
                f"page:{operation or job_id}:{page['name']}",
                priority,
            )
    filings = [f for f in parsed.filings if start <= f["filing_date"] <= end]
    for filing in filings:
        accession = filing["accession"]
        child = {
            **common,
            "kind": "document",
            "url": filing["document_url"],
            "accession": accession,
        }
        schedule_child(
            connection,
            "fetch",
            child,
            f"document:{operation or 'incremental'}:{accession}",
            priority,
        )
        connection.execute(
            text("""
            INSERT INTO enrichment(accession,cik) VALUES (:acc,:cik) ON CONFLICT DO NOTHING
        """),
            {"acc": accession, "cik": source.cik},
        )
    if filings or not payload.get("historical"):
        child = {
            **common,
            "kind": "facts",
            "url": f"https://data.sec.gov/api/xbrl/companyfacts/CIK{source.cik}.json",
        }
        schedule_child(connection, "fetch", child, f"facts:{job_id}", priority)
    if operation:
        connection.execute(
            text("""
            INSERT INTO backfill_pages(backfill_id,url,event_id) VALUES (:id,:url,:event)
            ON CONFLICT DO NOTHING
        """),
            {"id": operation, "url": source.url, "event": source.event_id},
        )
    elif not payload.get("historical"):
        connection.execute(
            text("UPDATE watchlist SET last_success_at=now() WHERE cik=:cik"), {"cik": source.cik}
        )


def after_facts(connection: Connection, source: Manifest, parsed: ParsedSource) -> None:
    accessions = sorted({fact["accession"] for fact in parsed.facts})
    if accessions:
        connection.execute(
            text("""
            UPDATE enrichment SET state='available',source_event_id=:event
            WHERE accession=ANY(:accessions)
        """),
            {"event": source.event_id, "accessions": accessions},
        )


def tick(engine: Engine) -> int:
    scheduled = 0
    with engine.begin() as connection:
        companies = (
            connection.execute(
                text("""
            SELECT * FROM watchlist WHERE enabled AND next_poll_at<=clock_timestamp()
            ORDER BY next_poll_at,cik FOR UPDATE SKIP LOCKED LIMIT 25
        """)
            )
            .mappings()
            .all()
        )
        today = utcnow().date()
        for company in companies:
            start = (
                company["last_success_at"].date() - timedelta(days=7)
                if company["last_success_at"]
                else today - timedelta(days=730)
            )
            payload = discovery_payload(company["cik"], start, today)
            store.enqueue(
                connection,
                "discover",
                payload,
                f"poll:{company['cik']}:{company['next_poll_at'].isoformat()}",
                priority=20,
            )
            connection.execute(
                text("""
                UPDATE watchlist SET next_poll_at=clock_timestamp()+make_interval(secs=>:delay)
                WHERE cik=:cik
            """),
                {"cik": company["cik"], "delay": 900 + random.randint(-60, 60)},
            )
            scheduled += 1
        pending = (
            connection.execute(
                text("""
            SELECT * FROM enrichment WHERE state='pending' AND next_check_at<=clock_timestamp()
            ORDER BY next_check_at,accession FOR UPDATE SKIP LOCKED LIMIT 100
        """)
            )
            .mappings()
            .all()
        )
        # Absolute offsets from first observation: 1m,5m,30m,6h,24h, then daily to day 7.
        offsets = (60, 300, 1800, 21600, 86400, 172800, 259200, 345600, 432000, 518400, 604800)
        for row in pending:
            if row["step"] >= len(offsets):
                connection.execute(
                    text("UPDATE enrichment SET state='unavailable' WHERE accession=:acc"),
                    {"acc": row["accession"]},
                )
                continue
            payload = {
                "cik": row["cik"],
                "kind": "facts",
                "url": f"https://data.sec.gov/api/xbrl/companyfacts/CIK{row['cik']}.json",
            }
            store.enqueue(
                connection, "fetch", payload, f"enrich:{row['accession']}:{row['step']}", priority=5
            )
            step = row["step"] + 1
            next_delay = offsets[step] if step < len(offsets) else 691200
            connection.execute(
                text("""
                UPDATE enrichment SET step=:step,next_check_at=first_seen_at+make_interval(secs=>:delay)
                WHERE accession=:acc
            """),
                {"step": step, "delay": next_delay, "acc": row["accession"]},
            )
            scheduled += 1
    refresh_backfills(engine)
    return scheduled


def refresh_backfills(engine: Engine) -> None:
    with engine.begin() as connection:
        for operation in connection.execute(
            text("SELECT id FROM backfills WHERE state='running' FOR UPDATE SKIP LOCKED")
        ).scalars():
            states = list(
                connection.execute(
                    text("""SELECT state FROM jobs WHERE payload->>'backfill_id'=:id
                         OR id IN (SELECT job_id FROM backfill_normalizations WHERE backfill_id=:id)"""),
                    {"id": operation},
                ).scalars()
            )
            if states and not set(states) & {"queued", "running", "retry_wait"}:
                state = (
                    "completed_with_errors"
                    if set(states) & {"dead_letter", "quarantined"}
                    else "completed"
                )
                connection.execute(
                    text("UPDATE backfills SET state=:state,completed_at=now() WHERE id=:id"),
                    {"state": state, "id": operation},
                )


def cancel_backfill(engine: Engine, operation: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            text("SELECT id FROM backfills WHERE id=:id FOR UPDATE"), {"id": operation}
        ).one()
        connection.execute(
            text(
                "UPDATE backfills SET state='cancelled',completed_at=now() WHERE id=:id AND state='running'"
            ),
            {"id": operation},
        )
        # Running jobs check the operation inside their final commit transaction.
        jobs = connection.execute(
            text("""
            UPDATE jobs SET state='cancelled',updated_at=now()
            WHERE payload->>'backfill_id'=:id AND state IN ('queued','retry_wait') RETURNING id
        """),
            {"id": operation},
        ).scalars()
        for job in jobs:
            store.event(connection, job, "cancelled", reason="backfill_cancelled")
