import asyncio
import logging
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from sarthi.api.routers import actions, alerts, chat, datahub, read, runs, sandbox
from sarthi.config import get_settings
from sarthi.db import init_db
from sarthi.llm import get_llm

log = logging.getLogger("sarthi.api")
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
LOOPBACK = {"localhost", "127.0.0.1", "::1"}


class SameOriginWrites:
    """Refuse requests that change something when a page on another site sent them.

    CORS only keeps another site from reading the answer; a plain form post (an approval, an upload) still
    arrives and is carried out. So a write is refused when the browser says it came from elsewhere: an
    `Origin` that is neither configured nor on this computer, or `Sec-Fetch-Site: cross-site`. Requests
    without these headers (curl, the CLI, tests) are not from a browser page and pass.
    """

    def __init__(self, app, allowed: list[str]):
        self.app, self.allowed = app, {o.rstrip("/") for o in allowed}

    def permitted(self, origin: str | None, fetch_site: str | None) -> bool:
        if origin is not None:
            if origin.rstrip("/") in self.allowed:
                return True
            parts = urlsplit(origin)
            return parts.scheme in ("http", "https") and parts.hostname in LOOPBACK       # "null" fails here too
        return fetch_site != "cross-site"

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] not in SAFE_METHODS:
            headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]}
            if not self.permitted(headers.get("origin"), headers.get("sec-fetch-site")):
                response = JSONResponse(status_code=403, content={"detail": "cross-site request refused"})
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    app.state.llm = get_llm()
    await app.state.llm.probe()
    # First start on a seeded database: run the pipeline once so the screens have something to show.
    from sarthi.orchestrator.runner import has_data, latest_run_id, run_pipeline

    _close_interrupted_runs()
    app.state.startup_run = None
    if not get_settings().skip_startup_run and has_data() and latest_run_id() is None:
        app.state.startup_run = asyncio.create_task(_startup_run(run_pipeline))
    yield
    if app.state.startup_run is not None and not app.state.startup_run.done():
        app.state.startup_run.cancel()


def _close_interrupted_runs() -> None:
    """Runs still marked "running" were cut off when the server last stopped; record them as interrupted."""
    from sqlmodel import select

    from sarthi.db import session
    from sarthi.models import Run, utcnow

    with session() as s:
        for run in s.exec(select(Run).where(Run.status == "running")).all():
            run.status, run.finished_at = "interrupted", utcnow()
            s.add(run)
        s.commit()


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
    app.add_middleware(SameOriginWrites, allowed=s.cors_list)
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_list, allow_methods=["*"], allow_headers=["*"])

    @app.get("/api/health")
    async def health():
        llm = getattr(app.state, "llm", None)
        return {"status": "ok", "llm": await llm.probe() if llm else "unknown"}

    for module in (read, runs, alerts, actions, sandbox, datahub, chat):
        app.include_router(module.router, prefix="/api")

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        """Anything unforeseen is logged here and answered without internals."""
        log.error("%s %s failed", request.method, request.url.path, exc_info=exc)
        return JSONResponse(status_code=500, content={"detail": "internal error"})

    return app


app = create_app()
