import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import Response

from secrecon.api.errors import install_errors
from secrecon.api.routes import router
from secrecon.api.ui import router as ui_router
from secrecon.config import Settings
from secrecon.db.projections import fact_provenance
from secrecon.db.queries import selected_generation
from secrecon.db.reconciliation import get_run
from secrecon.db.session import make_engine
from secrecon.telemetry import runtime as telemetry


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings()
    engine = make_engine(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        telemetry.configure(config.otlp_endpoint, config.telemetry_service)
        yield
        telemetry.current.close()
        engine.dispose()

    app = FastAPI(
        title="SECRecon", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None
    )
    app.state.engine = engine
    app.state.settings = config
    install_errors(app)
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver", "api"]
    )
    app.mount("/assets", StaticFiles(directory=Path(__file__).parent / "assets"), name="assets")
    app.include_router(router)
    app.include_router(ui_router)

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request.state.request_id = str(uuid4())
        request.state.csp_nonce = secrets.token_urlsafe(24)
        with telemetry.span("http.request", request_id=request.state.request_id):
            response = await call_next(request)
            response.headers["X-Request-ID"] = request.state.request_id
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
            )
            response.headers["Referrer-Policy"] = "same-origin"
            if request.url.path == "/docs":
                response.headers["Content-Security-Policy"] = (
                    "default-src 'self'; script-src 'self' 'nonce-"
                    + request.state.csp_nonce
                    + "'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
                )
            if request.url.path.startswith(("/ui", "/v1/admin")):
                response.headers["Cache-Control"] = "no-store"
            return response

    @app.get("/docs", include_in_schema=False)
    def documentation(request: Request) -> HTMLResponse:
        html = get_swagger_ui_html(
            openapi_url="/openapi.json",
            title="SECRecon API",
            swagger_js_url="/assets/swagger/swagger-ui-bundle.js",
            swagger_css_url="/assets/swagger/swagger-ui.css",
            swagger_favicon_url="/assets/favicon.svg",
        )
        return HTMLResponse(
            bytes(html.body)
            .decode()
            .replace("<script>", '<script nonce="' + request.state.csp_nonce + '">')
        )

    @app.get("/v1/reconciliations/{run_id}")
    def reconciliation(
        run_id: str,
        limit: int = Query(default=50, ge=1, le=200),
        cursor: str = Query(default="", max_length=64),
    ) -> dict[str, Any]:
        with engine.connect() as connection:
            result = get_run(connection, run_id, limit=limit, after=cursor)
            if result is None:
                raise HTTPException(404, "Comparison not found")
            return result

    @app.get("/v1/facts/{fact_id}/provenance")
    def provenance(
        fact_id: str,
        generation: str | None = None,
        limit: int = Query(default=50, ge=1, le=200),
        cursor: str = Query(default="", max_length=3000),
    ) -> dict[str, Any]:
        with engine.connect() as connection:
            selected = selected_generation(connection, generation)
            result = fact_provenance(connection, selected, fact_id, limit=limit, after=cursor)
            if result is None:
                raise HTTPException(404, "Fact not found")
            return {"generation": selected, **result}

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/health/ready")
    def ready() -> Any:
        try:
            with engine.connect() as connection:
                revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
                expected = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
                if revision != expected:
                    raise ValueError("Incompatible migration")
            return {"status": "ready", "schema": revision}
        except (SQLAlchemyError, ValueError):
            return JSONResponse(status_code=503, content={"status": "not_ready"})

    return app
