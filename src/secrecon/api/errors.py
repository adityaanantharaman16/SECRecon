from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException

from secrecon.jobs.store import IdempotencyConflict


def install_errors(app: FastAPI) -> None:
    def response(
        request: Request, status: int, code: str, message: str, details: Any = None
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status,
            content={
                "error": {"code": code, "message": message, "details": details},
                "request_id": getattr(request.state, "request_id", None),
            },
        )

    @app.exception_handler(HTTPException)
    async def http(request: Request, exc: HTTPException) -> JSONResponse:
        return response(
            request,
            exc.status_code,
            {401: "unauthorized", 403: "forbidden", 404: "not_found", 409: "conflict"}.get(
                exc.status_code, "request_error"
            ),
            str(exc.detail),
        )

    @app.exception_handler(RequestValidationError)
    async def validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Never echo submitted passwords, tokens or whole request bodies.
        return response(
            request,
            422,
            "validation_error",
            "Invalid request",
            [{"path": list(e["loc"]), "type": e["type"]} for e in exc.errors()],
        )

    @app.exception_handler(IdempotencyConflict)
    async def conflict(request: Request, exc: IdempotencyConflict) -> JSONResponse:
        return response(request, 409, "idempotency_conflict", str(exc))

    @app.exception_handler(ValueError)
    async def value(request: Request, exc: ValueError) -> JSONResponse:
        return response(request, 422, "invalid_operation", str(exc)[:300])

    @app.exception_handler(SQLAlchemyError)
    async def database(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        return response(request, 503, "dependency_unavailable", "Database temporarily unavailable")
