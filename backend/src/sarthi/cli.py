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


@app.command()
def run(lead_mult: float = 1.0, demand_mult: float = 1.0, dry: bool = False, lang: str = "EN"):
    """Run the agent pipeline once. --dry makes it a what-if that stores no alerts and executes nothing."""
    import asyncio

    from sarthi.db import init_db
    from sarthi.orchestrator.runner import run_pipeline, run_summary

    init_db()
    scenario = {"lead_mult": lead_mult, "demand_mult": demand_mult}
    run_id = asyncio.run(run_pipeline("cli", scenario=scenario, dry_run=dry, lang=lang.upper()))
    summary = run_summary(run_id)
    zones: dict[str, int] = {}
    for d in summary["decisions"].values():
        zones[d["zone"]] = zones.get(d["zone"], 0) + 1
    typer.echo(f"run {run_id} {summary['status']}: {len(summary['decisions'])} SKUs "
               f"({', '.join(f'{n} {z}' for z, n in sorted(zones.items()))}); {summary['proposals']} proposals, "
               f"{summary['approved']} approved, {len(summary['alerts'])} alerts, {len(summary['executed'])} executed")
    for error in summary["errors"]:
        typer.echo(f"  error: {error}")
    raise typer.Exit(0 if summary["status"] == "done" else 1)


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
