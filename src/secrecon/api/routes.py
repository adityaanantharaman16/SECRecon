from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import text

from secrecon.api.auth import authorize
from secrecon.api.schemas import BackfillRequest, ComparisonRequest, EmptyRequest, ReplayRequest
from secrecon.db import queries
from secrecon.domain.types import cik_text
from secrecon.jobs import store
from secrecon.orchestration.operations import submit
from secrecon.telemetry import metrics

router = APIRouter()


def list_route(resource: str) -> Any:
    def listing(
        request: Request,
        generation: str | None = None,
        q: str | None = Query(default=None, max_length=100),
        cik: str | None = None,
        form: str | None = None,
        accession: str | None = None,
        concept: str | None = Query(default=None, max_length=200),
        period_end: date | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
        state: str | None = None,
        kind: str | None = None,
        limit: int = Query(default=50, ge=1, le=200),
        cursor: str | None = Query(default=None, max_length=3000),
    ) -> dict[str, Any]:
        values = {
            "q": q,
            "cik": cik_text(cik) if cik else None,
            "form": form,
            "accession": accession,
            "concept": concept,
            "period_end": period_end.isoformat() if period_end else None,
            "from_date": from_date.isoformat() if from_date else None,
            "to_date": to_date.isoformat() if to_date else None,
            "state": state,
            "kind": kind,
        }
        with request.app.state.engine.begin() as connection:
            connection.execute(text("SET LOCAL statement_timeout='5s'"))
            return queries.page(
                connection,
                resource,
                generation=generation,
                filters={k: v for k, v in values.items() if v is not None},
                limit=limit,
                cursor=cursor,
            )

    listing.__name__ = "list_" + resource
    return listing


for resource in queries.SPECS:
    admin = resource in {"jobs", "quarantine", "backfills", "replays"}
    router.add_api_route(
        ("/v1/admin/" if admin else "/v1/") + resource,
        list_route(resource),
        methods=["GET"],
        dependencies=[Depends(authorize)] if admin else [],
    )


@router.get("/v1/filings/{accession}")
def filing(request: Request, accession: str, generation: str | None = None) -> dict[str, Any]:
    with request.app.state.engine.begin() as connection:
        result = queries.filing_detail(
            connection, accession, queries.selected_generation(connection, generation)
        )
        if not result:
            raise HTTPException(404, "Filing not found")
        return result


@router.get("/v1/admin/jobs/{job_id}", dependencies=[Depends(authorize)])
def job(request: Request, job_id: str) -> dict[str, Any]:
    with request.app.state.engine.connect() as connection:
        if not connection.scalar(text("SELECT id FROM jobs WHERE id=:id"), {"id": job_id}):
            raise HTTPException(404, "Job not found")
    return store.inspect(request.app.state.engine, job_id)


@router.get("/v1/admin/operations/{operation_id}", dependencies=[Depends(authorize)])
def operation(request: Request, operation_id: str) -> dict[str, Any]:
    with request.app.state.engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT a.id,a.action,a.body,a.job_id,a.result,a.created_at,j.state,j.error FROM admin_requests a JOIN jobs j ON j.id=a.job_id WHERE a.id=:id"
                ),
                {"id": operation_id},
            )
            .mappings()
            .first()
        )
        if not row:
            raise HTTPException(404, "Operation not found")
        return dict(row)


def operation_detail(resource: str, key: str) -> Any:
    def detail(request: Request, operation_id: str) -> dict[str, Any]:
        with request.app.state.engine.connect() as connection:
            row = connection.execute(
                text(f"SELECT to_jsonb(t)-'event_ids' AS data FROM {resource} t WHERE {key}=:id"),
                {"id": operation_id},
            ).scalar()
            if not row:
                raise HTTPException(404, "Operation not found")
            if resource == "replays":
                row["total_sources"] = connection.scalar(
                    text("SELECT jsonb_array_length(event_ids) FROM replays WHERE generation=:id"),
                    {"id": operation_id},
                )
            return dict(row)

    return detail


for resource, key in (("backfills", "id"), ("replays", "generation")):
    router.add_api_route(
        "/v1/admin/" + resource + "/{operation_id}",
        operation_detail(resource, key),
        methods=["GET"],
        dependencies=[Depends(authorize)],
    )


def enqueue(
    request: Request, action: str, body: dict[str, Any], key: str, actor: str
) -> dict[str, Any]:
    with request.app.state.engine.begin() as connection:
        return submit(connection, action, body, key, actor)


@router.post("/v1/admin/reconciliations", status_code=202)
def comparison(
    request: Request,
    body: ComparisonRequest,
    idempotency_key: str = Header(...),
    actor: str = Depends(authorize),
) -> dict[str, Any]:
    return enqueue(request, "reconcile", body.model_dump(mode="json"), idempotency_key, actor)


@router.post("/v1/admin/backfills", status_code=202)
def backfill(
    request: Request,
    body: BackfillRequest,
    idempotency_key: str = Header(...),
    actor: str = Depends(authorize),
) -> dict[str, Any]:
    return enqueue(request, "backfill", body.model_dump(mode="json"), idempotency_key, actor)


@router.post("/v1/admin/replays", status_code=202)
def replay(
    request: Request,
    body: ReplayRequest,
    idempotency_key: str = Header(...),
    actor: str = Depends(authorize),
) -> dict[str, Any]:
    return enqueue(request, "replay", body.model_dump(mode="json"), idempotency_key, actor)


@router.post("/v1/admin/jobs/{job_id}/redrive", status_code=202)
def redrive(
    request: Request,
    job_id: str,
    body: EmptyRequest,
    idempotency_key: str = Header(...),
    actor: str = Depends(authorize),
) -> dict[str, Any]:
    return enqueue(request, "redrive", {"id": job_id}, idempotency_key, actor)


@router.post("/v1/admin/backfills/{operation_id}/cancel", status_code=202)
def cancel(
    request: Request,
    operation_id: str,
    body: EmptyRequest,
    idempotency_key: str = Header(...),
    actor: str = Depends(authorize),
) -> dict[str, Any]:
    return enqueue(request, "cancel", {"id": operation_id}, idempotency_key, actor)


@router.get("/v1/overview")
def overview(request: Request) -> dict[str, Any]:
    with request.app.state.engine.begin() as connection:
        return metrics.snapshot(
            connection,
            request.app.state.settings.redis_url,
            request.app.state.settings.sec_mode == "live",
        )


@router.get("/metrics", response_class=PlainTextResponse)
def metric_export(request: Request) -> str:
    with request.app.state.engine.begin() as connection:
        return metrics.prometheus(
            connection,
            request.app.state.settings.redis_url,
            request.app.state.settings.sec_mode == "live",
        )
