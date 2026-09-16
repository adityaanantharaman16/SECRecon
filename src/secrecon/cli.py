from pathlib import Path
from typing import Literal

import typer
import uvicorn

from secrecon.commands import ingestion as ingestion_commands
from secrecon.commands import reconciliation as reconciliation_commands
from secrecon.commands.jobs import app as jobs_app
from secrecon.commands.jobs import dispatch, worker
from secrecon.config import Settings
from secrecon.db.projections import process_source
from secrecon.db.session import make_engine
from secrecon.domain.types import cik_text
from secrecon.storage.archive import Archive

app = typer.Typer(no_args_is_help=True)
app.add_typer(jobs_app, name="jobs")
app.add_typer(reconciliation_commands.app, name="reconcile")
app.command()(reconciliation_commands.generation_diff)
app.command()(reconciliation_commands.quarantine_list)
app.command()(worker)
app.command()(dispatch)
app.add_typer(ingestion_commands.watchlist_app, name="watchlist")
app.add_typer(ingestion_commands.backfills_app, name="backfills")
app.command()(ingestion_commands.ingest)
app.command()(ingestion_commands.backfill)
app.command()(ingestion_commands.replay)
app.command()(ingestion_commands.archive_sync)
app.command()(ingestion_commands.projection_digest)


@app.command()
def serve(host: str = "127.0.0.1") -> None:
    """Run the local API."""
    uvicorn.run("secrecon.api.app:create_app", factory=True, host=host, port=8000)


@app.command("import-source")
def import_source(
    path: Path,
    kind: str = typer.Option(...),
    cik: str = typer.Option(...),
    url: str = typer.Option(...),
    accession: str | None = None,
) -> None:
    """Preserve and normalize a local response without SEC network access."""
    from typing import cast

    if kind not in {"submissions", "facts", "document"}:
        raise typer.BadParameter("kind must be submissions, facts or document")
    settings = Settings()
    archive = Archive(settings)
    archive.initialize()
    source = archive.preserve(
        path.read_bytes(),
        kind=cast(Literal["submissions", "facts", "document"], kind),
        cik=cik_text(cik),
        url=url,
        accession=accession,
    )
    engine = make_engine(settings)
    try:
        result = process_source(engine, archive, source)
        typer.echo(f"event={source.event_id} checksum={source.sha256} facts={len(result.facts)}")
    finally:
        engine.dispose()


if __name__ == "__main__":
    app()
