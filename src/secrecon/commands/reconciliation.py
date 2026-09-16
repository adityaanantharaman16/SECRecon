"""Local operator commands; web administration/authentication remains M5."""

import json

import typer
from sqlalchemy import text

from secrecon.db.generations import generation_report
from secrecon.db.projections import resolve_generation
from secrecon.db.reconciliation import get_run, reconcile, refresh_company
from secrecon.runtime import services

app = typer.Typer(help="Compare explicit accessions and inspect immutable results.")


@app.command("create")
def create(
    original: str,
    amendment: str,
    generation: str = "active",
    original_event: str | None = None,
    amendment_event: str | None = None,
) -> None:
    with services() as deps:
        typer.echo(
            reconcile(deps.engine, original, amendment, generation, original_event, amendment_event)
        )


@app.command("show")
def show(run_id: str) -> None:
    with services() as deps, deps.engine.connect() as connection:
        result = get_run(connection, run_id)
        if result is None:
            raise typer.BadParameter("Comparison not found")
        typer.echo(json.dumps(result, indent=2))


@app.command("links")
def links(generation: str = "active", refresh: bool = False) -> None:
    with services() as deps, deps.engine.begin() as connection:
        generation = resolve_generation(connection, generation)
        if refresh:
            ciks = list(
                connection.execute(
                    text("SELECT DISTINCT cik FROM filings WHERE generation=:g ORDER BY cik"),
                    {"g": generation},
                ).scalars()
            )
            for cik in ciks:
                connection.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                    {"key": f"{generation}:{cik}"},
                )
                refresh_company(connection, generation, cik)
        rows = list(
            connection.execute(
                text(
                    "SELECT l.data FROM amendment_heads h JOIN amendment_links l ON l.id=h.link_id WHERE h.generation=:g ORDER BY h.amendment"
                ),
                {"g": generation},
            ).scalars()
        )
        typer.echo(json.dumps(rows, indent=2))


def generation_diff(original: str, target: str) -> None:
    with (
        services() as deps,
        deps.engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection,
    ):
        typer.echo(json.dumps(generation_report(connection, original, target), indent=2))


def quarantine_list(generation: str = "active") -> None:
    with services() as deps, deps.engine.begin() as connection:
        selected = resolve_generation(connection, generation)
        rows = [
            dict(row)
            for row in connection.execute(
                text(
                    "SELECT event_id,parser_version,diagnostics FROM quarantine_records WHERE generation=:g ORDER BY created_at,event_id"
                ),
                {"g": selected},
            ).mappings()
        ]
        typer.echo(json.dumps(rows, indent=2))
