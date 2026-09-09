import typer
import uvicorn

app = typer.Typer(no_args_is_help=True)


@app.command()
def serve() -> None:
    """Run the local API."""
    uvicorn.run("secrecon.api.app:create_app", factory=True, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    app()
