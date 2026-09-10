from pathlib import Path
from typing import Literal

import typer
import uvicorn

from secrecon.commands.jobs import app as jobs_app
from secrecon.commands.jobs import dispatch, worker
from secrecon.config import Settings
from secrecon.db.projections import process_source
from secrecon.db.session import make_engine
from secrecon.domain.types import cik_text
from secrecon.storage.archive import Archive

app = typer.Typer(no_args_is_help=True)
app.add_typer(jobs_app, name="jobs")
app.command()(worker)
app.command()(dispatch)


@app.command()
def serve() -> None:
    """Run the local API."""
    uvicorn.run("secrecon.api.app:create_app", factory=True, host="0.0.0.0", port=8000)


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
