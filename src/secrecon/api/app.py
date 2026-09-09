from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from secrecon.config import Settings
from secrecon.db.projections import fact_provenance
from secrecon.db.session import make_engine


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings()
    engine = make_engine(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        engine.dispose()

    app = FastAPI(title="SECRecon", version="0.1.0", lifespan=lifespan)
    app.state.engine = engine

    @app.get("/v1/facts")
    def facts(
        accession: str | None = None,
        generation: str | None = None,
        limit: int = Query(default=50, ge=1, le=200),
    ) -> dict[str, Any]:
        with engine.connect() as connection:
            selected = generation or connection.scalar(
                text("SELECT value FROM system_state WHERE key='active_generation'")
            )
            rows = connection.execute(
                text("""
                SELECT fingerprint AS id,data FROM facts WHERE generation=:gen
                AND (CAST(:acc AS text) IS NULL OR accession=:acc)
                ORDER BY fingerprint LIMIT :limit
            """),
                {"gen": selected, "acc": accession, "limit": limit},
            ).mappings()
            return {"generation": selected, "items": [dict(row) for row in rows]}

    @app.get("/v1/facts/{fact_id}/provenance")
    def provenance(fact_id: str, generation: str | None = None) -> dict[str, Any]:
        with engine.connect() as connection:
            selected = generation or connection.scalar(
                text("SELECT value FROM system_state WHERE key='active_generation'")
            )
            result = fact_provenance(connection, selected, fact_id)
            if result is None:
                raise HTTPException(404, "Fact not found")
            return result

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
