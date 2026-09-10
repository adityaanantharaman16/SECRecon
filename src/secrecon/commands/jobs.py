import json
import logging
from uuid import uuid4

import typer
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from secrecon.jobs import store
from secrecon.jobs.handlers import CoreHandlers
from secrecon.jobs.worker import Worker
from secrecon.runtime import services, shutdown_event

app = typer.Typer(help="Inspect durable processing history.")


@app.command("list")
def list_jobs(status: str | None = None) -> None:
    with services() as deps, deps.engine.connect() as connection:
        rows = connection.execute(
            text("""
            SELECT id,kind,state,attempts,error FROM jobs
            WHERE (CAST(:state AS text) IS NULL OR state=:state)
            ORDER BY created_at DESC LIMIT 50
        """),
            {"state": status},
        ).mappings()
        typer.echo(json.dumps([dict(row) for row in rows], indent=2))


@app.command("show")
def show_job(job_id: str) -> None:
    with services() as deps:
        typer.echo(json.dumps(store.inspect(deps.engine, job_id), default=str, indent=2))


@app.command("redrive")
def redrive_job(job_id: str) -> None:
    with services() as deps:
        typer.echo(store.redrive(deps.engine, job_id))


def worker() -> None:
    """Run a lease-fenced worker; Ctrl+C drains current work."""
    stop = shutdown_event()
    with services() as deps:
        deps.archive.initialize()
        Worker(
            deps.engine,
            deps.queue,
            deps.settings,
            CoreHandlers(deps.engine, deps.archive, deps.client),
            "worker-" + uuid4().hex,
        ).run(stop)


def dispatch() -> None:
    """Republish due SQL work and dispatch the transactional outbox."""
    stop = shutdown_event()
    with services() as deps:
        while not stop.is_set():
            try:
                store.sweep(deps.engine)
                deps.queue.dispatch(deps.engine)
            except (SQLAlchemyError, RedisError):
                logging.getLogger(__name__).warning("dispatcher_dependency_unavailable")
            stop.wait(2)
