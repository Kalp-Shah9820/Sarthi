from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from sarthi.config import get_settings
from sarthi.db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # mk5 adds the LLM probe; mk8 adds the first pipeline run
    yield


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="Sarthi", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_list, allow_methods=["*"], allow_headers=["*"])

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "llm": getattr(app.state, "llm_mode", "unknown")}

    return app


app = create_app()
