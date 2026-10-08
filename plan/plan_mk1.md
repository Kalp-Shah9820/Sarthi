# MK1 — Project setup

**Goal:** a uv-managed Python package that starts a FastAPI server, reads configuration, and can reach (or cleanly detect the absence of) a local model in LM Studio.

## 1. Create the project

```powershell
cd "c:\Users\Kalp Shah\Desktop\Sarthi"
New-Item -ItemType Directory -Force backend\src\sarthi, backend\tests, backend\data\feeds, backend\samples, backend\outbox
cd backend
uv python pin 3.12
```

Create `backend/pyproject.toml`:

```toml
[project]
name = "sarthi"
version = "0.1.0"
description = "Sarthi agentic inventory backend"
requires-python = ">=3.12,<3.13"
dependencies = []

[project.scripts]
sarthi = "sarthi.cli:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/sarthi"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"

[tool.ruff]
line-length = 110
```

Create an empty `backend/src/sarthi/__init__.py`, then add dependencies (uv resolves and locks current versions):

```powershell
uv add fastapi "uvicorn[standard]" sqlmodel pydantic-settings typer
uv add numpy pandas scipy statsforecast
uv add openai httpx langgraph sse-starlette rapidfuzz
uv add python-multipart openpyxl xlrd lxml
uv add --dev pytest pytest-asyncio ruff
```

What each is for:

| Package | Use |
|---|---|
| fastapi, uvicorn, sse-starlette, python-multipart | HTTP API, streaming, file upload |
| sqlmodel | SQLite tables and queries |
| pydantic-settings, typer | `.env` config, CLI |
| numpy, pandas, scipy | simulation, data frames, LP/MILP solvers, Beta quantiles |
| statsforecast | AutoETS, AutoTheta, SeasonalNaive, Croston forecasting |
| openai | client for LM Studio's OpenAI-compatible endpoint |
| httpx | Open-Meteo weather calls, LM Studio health check |
| langgraph | agent graph with parallel branches |
| rapidfuzz | fuzzy matching of SKU names in chat/voice |
| openpyxl, xlrd, lxml | `.xlsx`, `.xls`, `.xml` uploads |

Append to the repo root `.gitignore`:

```
backend/.venv
backend/data/sarthi.db*
backend/outbox/
backend/.env
__pycache__/
```

## 2. Configuration

`backend/.env.example` (copy to `.env`):

```
SARTHI_DB_PATH=data/sarthi.db
SARTHI_LLM_BASE_URL=http://localhost:1234/v1
SARTHI_LLM_MODEL=qwen/qwen3-4b-2507
SARTHI_LLM_ENABLED=true
SARTHI_LLM_TIMEOUT_S=45
# Extra tokens allowed per call for hidden reasoning. 0 for normal instruct models.
# For a reasoning model (e.g. qwen/qwen3-4b-thinking-2507) set 2000 and SARTHI_LLM_TIMEOUT_S=120.
SARTHI_LLM_REASONING_HEADROOM=0
SARTHI_WEATHER_ENABLED=true
SARTHI_MC_PATHS=2000
SARTHI_SEED=20261005
SARTHI_CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
```

`backend/src/sarthi/config.py`:

```python
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SARTHI_", env_file=BACKEND_ROOT / ".env", extra="ignore")

    db_path: str = "data/sarthi.db"
    llm_base_url: str = "http://localhost:1234/v1"
    llm_model: str = "qwen/qwen3-4b-2507"
    llm_enabled: bool = True
    llm_timeout_s: float = 45.0
    llm_reasoning_headroom: int = 0
    weather_enabled: bool = True
    mc_paths: int = 2000
    seed: int = 20261005
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # decision constants (overridable by the active strategy policy)
    review_period_days: int = 7
    stockout_alert_prob: float = 0.15      # PDF: alert when P(stockout) > 15 %
    critical_prob: float = 0.60            # UI: "critical" above 60 %
    overstock_cover_days: int = 30         # PDF: Ghost when days of cover > 30
    auto_confidence: float = 85.0          # UI: green confidence bar from 85 %
    auto_max_order_value: float = 50_000.0
    goodwill_factor: float = 0.20          # lost-customer penalty as a share of lost margin
    monthly_budget: float = 500_000.0

    @property
    def db_file(self) -> Path:
        p = Path(self.db_path)
        return p if p.is_absolute() else BACKEND_ROOT / p

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

## 3. Minimal server and CLI

`backend/src/sarthi/api/__init__.py` and `backend/src/sarthi/api/routers/__init__.py`: empty.

`backend/src/sarthi/api/main.py`:

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from sarthi.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    # mk2 adds init_db(); mk5 adds the LLM probe; mk8 adds the first pipeline run
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
```

`backend/src/sarthi/cli.py`:

```python
import typer
import uvicorn

app = typer.Typer(no_args_is_help=True)


@app.command()
def serve(port: int = 8000, reload: bool = True):
    """Start the API server."""
    uvicorn.run("sarthi.api.main:app", host="127.0.0.1", port=port, reload=reload)


@app.command()
def version():
    typer.echo("sarthi 0.1.0")
```

(`version` exists so Typer keeps sub-command mode; later milestones add `seed`, `run`, `export-samples`, `backtest`.)

## 4. Local model in LM Studio (6 GB GPU)

The LLM's jobs here are narrow: classify a sentence into an intent, fill a small JSON schema, and phrase a few sentences from given facts. A small instruct model with reliable JSON output is the right tool; a bigger one would not fit with usable context.

| Role | Model | File size (Q4_K_M) | Why |
|---|---|---|---|
| **Default** | `qwen/qwen3-4b-2507` (Qwen3 4B Instruct, non-thinking) | about 2.5 GB | Fits entirely on the GPU with an 8k–16k context, fast, strong at JSON-schema output and tool-style extraction, usable Hindi. |
| Higher quality, slower | `qwen2.5-7b-instruct` | about 4.7 GB | Better prose for negotiation emails. Fits only with context ≤ 8192; expect roughly half the speed. |

Use the default unless email quality disappoints.

**Do not use a reasoning ("thinking") model as the main model.** Measured on this machine on 2026-10-07 against the LM Studio server, using the two call shapes this backend makes (a JSON-schema extraction capped at 400 tokens and a two-sentence narration capped at 220 tokens):

| Model as loaded | With the plan's token caps | With a 2,500-token cap |
|---|---|---|
| `qwen/qwen3-4b-thinking-2507` | Empty reply both times: every token went to hidden reasoning (`finish_reason: length`) | Correct, valid output, but about 1,100 reasoning tokens and roughly 30 seconds per call |
| `google/gemma-4-e2b` | Also reasons before answering; the narration call returned empty | not tested |

At 30 seconds per call a pipeline run with 12 model calls takes about 6 minutes and every chat answer takes half a minute. The non-thinking `qwen/qwen3-4b-2507` is the same size (2.5 GB) and answers these calls directly. The thinking variant of the 2507 release cannot have its reasoning switched off by a prompt flag, so the fix is to load the other model, not to configure this one.

If you still want to run a reasoning model, it works with `SARTHI_LLM_REASONING_HEADROOM=2000` and `SARTHI_LLM_TIMEOUT_S=120` in `.env`; expect the speeds above.

Setup:

1. In LM Studio, search and download `qwen/qwen3-4b-2507` (Discover tab), choosing the `Q4_K_M` quantisation.
2. Load it with **GPU offload: max** and **Context length: 8192**. Eject other chat models so only one occupies the GPU.
3. Developer tab → server **Status: Running** (port 1234). The page shows "Reachable at `http://192.168.0.102:1234`"; that is the same server on the LAN address. Keep `SARTHI_LLM_BASE_URL=http://localhost:1234/v1`: both addresses answer (verified), and `localhost` does not change when the router hands out a new IP.
4. The identifier shown next to the loaded model (for example `qwen/qwen3-4b-2507`) must equal `SARTHI_LLM_MODEL` in `.env`. Copy it with the copy button beside the name.
5. The "Parallel 4" setting has no effect here: the gateway sends one request at a time.

Other Indian languages (Tamil, Bengali, Telugu, Marathi, Gujarati, Kannada) are not left to a 4B model: `plan_mk10.md` answers through the frontend's existing translated templates, so those languages work without the LLM.

## Verify

```powershell
uv run sarthi serve --no-reload
```

In a second terminal:

```powershell
curl.exe http://127.0.0.1:8000/api/health
# {"status":"ok","llm":"unknown"}

curl.exe http://localhost:1234/v1/models
# JSON list containing the model id  (connection refused is acceptable: offline mode)
```

`uv run ruff check src` reports no errors.

## Done when

- `uv.lock` exists and `uv run sarthi version` prints `sarthi 0.1.0`.
- `/api/health` returns 200.
- `.env` holds the exact LM Studio model identifier (or `SARTHI_LLM_ENABLED=false`).
