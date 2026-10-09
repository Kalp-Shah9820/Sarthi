import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from sarthi.api.routers import actions, alerts, datahub, read, runs, sandbox
from sarthi.config import get_settings
from sarthi.db import init_db
from sarthi.llm import get_llm

log = logging.getLogger("sarthi.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    app.state.llm = get_llm()
    await app.state.llm.probe()
    # First start on a seeded database: run the pipeline once so the screens have something to show.
    from sarthi.orchestrator.runner import has_data, latest_run_id, run_pipeline

    app.state.startup_run = None
    if not get_settings().skip_startup_run and has_data() and latest_run_id() is None:
        app.state.startup_run = asyncio.create_task(_startup_run(run_pipeline))
    yield
    if app.state.startup_run is not None and not app.state.startup_run.done():
        app.state.startup_run.cancel()


async def _startup_run(run_pipeline) -> None:
    try:
        await run_pipeline("startup")
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("startup run failed")


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="Sarthi", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_list, allow_methods=["*"], allow_headers=["*"])

    @app.get("/api/health")
    async def health():
        llm = getattr(app.state, "llm", None)
        return {"status": "ok", "llm": await llm.probe() if llm else "unknown"}

    for module in (read, runs, alerts, actions, sandbox, datahub):
        app.include_router(module.router, prefix="/api")

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        """Anything unforeseen is logged here and answered without internals."""
        log.error("%s %s failed", request.method, request.url.path, exc_info=exc)
        return JSONResponse(status_code=500, content={"detail": "internal error"})

    return app


app = create_app()
