import sys

import typer
import uvicorn

# Windows consoles default to a legacy code page that cannot print the rupee sign or Hindi text.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

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


@app.command("export-samples")
def export_samples_cmd():
    """Write one example upload file per Data Hub card into backend/samples."""
    from sarthi.config import BACKEND_ROOT
    from sarthi.db import init_db
    from sarthi.seed.samples import export_samples

    init_db()
    paths = export_samples(BACKEND_ROOT / "samples")
    typer.echo("samples written: " + ", ".join(p.name for p in paths))


@app.command()
def validate(full: bool = False, frontend: bool = False, perf: bool = False):
    """Check that every milestone built so far still works. --full adds ruff and pytest; --perf times forecasting."""
    from sarthi.validate import report, run

    ok = report(run(full=full, frontend=frontend, perf=perf))
    raise typer.Exit(0 if ok else 1)
