import typer
import uvicorn

app = typer.Typer(no_args_is_help=True)


@app.command()
def serve(port: int = 8000, reload: bool = True):
    """Start the API server."""
    uvicorn.run("sarthi.api.main:app", host="127.0.0.1", port=port, reload=reload)


@app.command()
def version():
    """Print the backend version."""
    typer.echo("sarthi 0.1.0")


@app.command()
def seed():
    """Wipe the database and fill it with 18 months of demo history."""
    from sarthi.config import get_settings
    from sarthi.db import init_db
    from sarthi.seed.generator import seed_database

    init_db()
    counts = seed_database(get_settings().seed)
    typer.echo("seeded: " + ", ".join(f"{k}={v}" for k, v in counts.items()))


@app.command()
def validate(full: bool = False, frontend: bool = False):
    """Check that every milestone built so far still works. --full adds ruff and pytest."""
    from sarthi.validate import report, run

    ok = report(run(full=full, frontend=frontend))
    raise typer.Exit(0 if ok else 1)
