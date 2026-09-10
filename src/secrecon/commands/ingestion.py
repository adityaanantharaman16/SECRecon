"""Operator-facing orchestration commands, kept separate from domain and persistence logic."""

import json
from datetime import date

import typer
from sqlalchemy import text

from secrecon.domain.types import cik_text, utcnow
from secrecon.jobs.store import enqueue
from secrecon.orchestration import planner
from secrecon.orchestration.replay import digest, inventory, promote, rebuild
from secrecon.runtime import services

watchlist_app = typer.Typer(help="Configure up to 25 monitored companies.")
backfills_app = typer.Typer(help="Inspect and cancel bounded historical operations.")


@watchlist_app.command("add")
def watchlist_add(ciks: list[str]) -> None:
    with services() as deps:
        planner.add_watchlist(deps.engine, ciks)
    typer.echo("Watchlist saved; live polling requires SEC_MODE=live and an approved User-Agent")


@watchlist_app.command("seed")
def watchlist_seed() -> None:
    watchlist_add(list(planner.DEFAULT_WATCHLIST))


@watchlist_app.command("list")
def watchlist_list() -> None:
    with services() as deps, deps.engine.connect() as connection:
        rows = connection.execute(text("SELECT * FROM watchlist ORDER BY cik")).mappings()
        typer.echo(json.dumps([dict(row) for row in rows], default=str, indent=2))


def ingest(cik: str = typer.Option(...)) -> None:
    """Queue one bounded company discovery. Network access remains explicitly controlled."""
    from datetime import timedelta

    with services() as deps, deps.engine.begin() as connection:
        today = utcnow().date()
        payload = planner.discovery_payload(cik_text(cik), today - timedelta(days=730), today)
        job = enqueue(
            connection, "discover", payload, "manual:" + utcnow().isoformat(), priority=20
        )
    typer.echo(job)


def backfill(
    cik: list[str] | None = typer.Option(None),
    start: str = typer.Option(..., "--from"),
    end: str = typer.Option(..., "--to"),
    max_jobs: int = 10000,
) -> None:
    """Queue a company/date backfill; omitted CIKs mean the current watchlist."""
    with services() as deps:
        result = planner.create_backfill(
            deps.engine, cik or [], date.fromisoformat(start), date.fromisoformat(end), max_jobs
        )
    typer.echo(result)


@backfills_app.command("show")
def backfill_show(operation: str) -> None:
    with services() as deps:
        planner.refresh_backfills(deps.engine)
        with deps.engine.connect() as connection:
            result = dict(
                connection.execute(text("SELECT * FROM backfills WHERE id=:id"), {"id": operation})
                .mappings()
                .one()
            )
            result["jobs"] = [
                dict(row)
                for row in connection.execute(
                    text("""
                SELECT state,count(*) AS count FROM jobs WHERE payload->>'backfill_id'=:id
                OR id IN (SELECT job_id FROM backfill_normalizations WHERE backfill_id=:id) GROUP BY state
            """),
                    {"id": operation},
                ).mappings()
            ]
            result["pages_completed"] = connection.scalar(
                text("SELECT count(*) FROM backfill_pages WHERE backfill_id=:id"), {"id": operation}
            )
        typer.echo(json.dumps(result, default=str, indent=2))


@backfills_app.command("cancel")
def backfill_cancel(operation: str) -> None:
    with services() as deps:
        planner.cancel_backfill(deps.engine, operation)
    typer.echo("Backfill cancelled; preserved sources and committed facts remain available")


def replay(
    generation: str = typer.Option(...),
    offline: bool = typer.Option(True, "--offline"),
    resume: bool = False,
    promote_result: bool = typer.Option(False, "--promote"),
    expected_digest: str | None = None,
) -> None:
    """Build a fresh generation solely from a frozen raw inventory."""
    if not offline:
        raise typer.BadParameter("Replay is always offline")
    if promote_result and not expected_digest:
        raise typer.BadParameter("--promote requires --expected-digest")
    with services() as deps:
        result = rebuild(deps.engine, deps.archive, generation, resume=resume)
        if promote_result:
            promote(deps.engine, generation, expected_digest or "", archive=deps.archive)
        typer.echo(json.dumps(result, indent=2))


def archive_sync() -> None:
    """Repair committed archive manifests missing SQL jobs."""
    with services() as deps:
        typer.echo(f"Registered {inventory(deps.engine, deps.archive)} eligible archive events")


def projection_digest(generation: str = typer.Option("active")) -> None:
    with services() as deps, deps.engine.connect() as connection:
        selected = planner.active_generation(connection) if generation == "active" else generation
        typer.echo(json.dumps(digest(connection, selected), indent=2))
