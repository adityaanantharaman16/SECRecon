from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from secrecon.config import Settings
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
