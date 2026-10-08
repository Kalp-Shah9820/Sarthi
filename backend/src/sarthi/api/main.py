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
    # mk8 adds the first pipeline run
    yield


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
