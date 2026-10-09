import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from sarthi.config import get_settings
from sarthi.db import init_db
from sarthi.llm import get_llm


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    app.state.llm = get_llm()
    await app.state.llm.probe()
    # First start on a seeded database: run the pipeline once so the screens have something to show.
    from sarthi.orchestrator.runner import has_data, latest_run_id, run_pipeline

    app.state.startup_run = None
    if not get_settings().skip_startup_run and has_data() and latest_run_id() is None:
        app.state.startup_run = asyncio.create_task(run_pipeline("startup"))
    yield
    if app.state.startup_run is not None and not app.state.startup_run.done():
        app.state.startup_run.cancel()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="Sarthi", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_list, allow_methods=["*"], allow_headers=["*"])

    @app.get("/api/health")
    async def health():
        llm = getattr(app.state, "llm", None)
        return {"status": "ok", "llm": await llm.probe() if llm else "unknown"}

    return app


app = create_app()
