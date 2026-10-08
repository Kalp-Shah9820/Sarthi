# Sarthi

Sarthi is an inventory replenishment advisor for retail chains. It forecasts demand, estimates the risk of running out of stock, sorts every product into one of four "Inventory Ikigai" zones, and recommends what to order, move between warehouses, or mark down. A team of software agents does this work, and a manager approves or rejects their recommendations.

| Zone | Meaning | Typical action |
|---|---|---|
| Sweet Spot | Sells fast, healthy margin, supply is stable | Keep replenishing automatically |
| Chaos Zone | Demand is strong but stock will not last until the next delivery | Reorder urgently, switch or negotiate with suppliers |
| Ghost Zone | Slow-moving stock tying up cash | Transfer to a warehouse that needs it |
| Money Pit | Overstocked and barely profitable | Mark down or run a campaign |

## Current status

The project is being built in twelve milestones, described in [plan/](plan/README.md).

| Part | State |
|---|---|
| Frontend (React) | Complete. All screens work, currently on built-in sample data. |
| Backend milestone 1: project setup | Done. The server starts and answers a health check. |
| Backend milestone 2: database and demo data | Done. 18 months of sales, stock and delivery history for the 10 demo products. |
| Backend milestone 3: file ingestion | Done. The 8 Data Hub file types can be read, checked and stored (from code; the upload button is connected in milestone 9). |
| Backend milestone 4: analytics | Done. Forecasting, stockout simulation, zones, Profit-at-Risk, supplier scoring, basket analysis and the transfer and budget optimisers, as tested calculation code. |
| Backend milestone 5: agent foundations | Done. Shared event log for agents, the local-model connection with safe fallbacks, and the check that stops the model inventing numbers. |
| Backend milestones 6–10: agents, API | Not built yet. |
| Milestone 11: connecting the screens to the backend | Not built yet. |

So today the two halves run side by side but are not connected: the screens show sample numbers, and the backend holds a database of demo history and can analyse it, but no agent runs the analysis yet and nothing is served to the screens.

## What you need installed

| Tool | Version used | Needed for |
|---|---|---|
| [Node.js](https://nodejs.org) | 22 | Frontend |
| [uv](https://docs.astral.sh/uv/) | 0.12 | Backend (it installs Python 3.12 itself) |
| [LM Studio](https://lmstudio.ai) | any recent | Optional local language model |

No paid AI service is used. The only language model is one that runs on your own machine through LM Studio, and the system is designed to keep working when it is switched off.

## Folder layout

```
Sarthi/
├─ src/, main.jsx, index.html   frontend (React + Vite)
├─ backend/                     backend (Python, FastAPI)
│  ├─ src/sarthi/               application code
│  ├─ tests/                    tests
│  ├─ .env                      your local settings (not committed)
│  └─ pyproject.toml            Python dependencies
└─ plan/                        the twelve build plans
```

The frontend is run from the **repo root**. The backend is run from the **`backend` folder**. Running `uv` in the root fails with "No pyproject.toml found".

## First-time setup

```powershell
# frontend
cd "C:\Users\Kalp Shah\Desktop\Sarthi"
npm install

# backend
cd backend
uv sync
Copy-Item .env.example .env    # skip if .env already exists
uv run sarthi seed              # create the database and fill it with demo history (about 4 seconds)
uv run sarthi export-samples    # write 8 example upload files into backend/samples
```

`sarthi seed` wipes the database each time, so run it again whenever you want to return to the demo state.

## Running it

Use two terminals.

**Terminal 1 — backend**

```powershell
cd "C:\Users\Kalp Shah\Desktop\Sarthi\backend"
uv run sarthi serve
```

It listens on `http://127.0.0.1:8000`. Check it with:

```powershell
curl.exe http://127.0.0.1:8000/api/health
# {"status":"ok","llm":"llm"}       "llm" = LM Studio is serving the model, "offline" = it is not
```

Interactive API documentation is at `http://127.0.0.1:8000/docs`.

**Terminal 2 — frontend**

```powershell
cd "C:\Users\Kalp Shah\Desktop\Sarthi"
npm run dev
```

Open `http://localhost:5173`.

## Local language model (optional)

The model is used only for wording and for understanding typed or spoken questions. It never produces a number; all figures come from code.

1. In LM Studio, download **`qwen/qwen3-4b-2507`** with the `Q4_K_M` quantisation (about 2.5 GB, fits a 6 GB GPU).
2. Load it with GPU offload at maximum and context length 8192.
3. In the Developer tab, set the server status to **Running** (port 1234).

Do not use a "thinking" variant such as `qwen3-4b-thinking-2507`. In testing on this machine it took about 30 seconds per reply and returned empty answers at normal length limits.

If LM Studio shows a different model identifier, put that exact text in `backend/.env` as `SARTHI_LLM_MODEL`.

## Settings

Settings live in `backend/.env`. The ones you are most likely to change:

| Setting | Default | Meaning |
|---|---|---|
| `SARTHI_LLM_MODEL` | `qwen/qwen3-4b-2507` | Model identifier as shown in LM Studio |
| `SARTHI_LLM_BASE_URL` | `http://localhost:1234/v1` | LM Studio server address |
| `SARTHI_LLM_ENABLED` | `true` | Set `false` to run without any model |
| `SARTHI_WEATHER_ENABLED` | `true` | Allow free weather lookups (the only outside call) |
| `SARTHI_DB_PATH` | `data/sarthi.db` | Database file location |

## Checking that everything works

Run from the `backend` folder:

```powershell
uv run sarthi validate --full --frontend
```

This checks that all dependencies and modules load, the settings are valid, the health endpoint answers, LM Studio is reachable, the code passes linting, the tests pass, and the frontend builds. A healthy run ends with `0 failed`. A `WARN` on the `llm` line only means LM Studio is closed.

Individual checks:

| Command (from `backend`) | What it does |
|---|---|
| `uv run pytest -q` | Runs the tests |
| `uv run ruff check src tests` | Lints the code |
| `uv run sarthi validate` | Quick checks only, a few seconds |
| `uv run sarthi validate --perf` | Also times the forecasting step against its 60-second budget |
| `uv run sarthi seed` | Resets the database to the demo state |
| `uv run sarthi export-samples` | Writes the 8 example upload files into `backend/samples` |
| `uv run sarthi version` | Prints the backend version |

## Troubleshooting

| Problem | Fix |
|---|---|
| `error: No pyproject.toml found` | You are in the repo root. Run `cd backend` first. |
| `validate` shows `WARN` for `llm` | LM Studio's server is not running, or the model identifier in `.env` does not match the loaded model. The backend still works. |
| `validate` shows `WARN` for `database` | The database is empty. Run `uv run sarthi seed`. |
| `validate` shows `FAIL ... schema is out of date` | The code gained new database columns. Run `uv run sarthi seed` to rebuild (this erases current data). |
| `validate` shows `WARN` for `ingest` | The sample files are missing. Run `uv run sarthi export-samples`. |
| Tests or forecasting are several times slower than usual | The laptop is on battery, which throttles the processor. Plug it in. |
| VS Code underlines `import sarthi...` as unresolved | Select the interpreter `backend\.venv\Scripts\python.exe` (Ctrl+Shift+P, "Python: Select Interpreter"). The code runs fine either way. |
| Port 8000 already in use | Another backend is running. Stop it, or start with `uv run sarthi serve --port 8001`. |
| Screens show the same numbers regardless of the backend | Expected for now: the screens are connected to the backend in milestone 11. |

## How the finished system will work

Seven agents run in four phases, matching the pipeline bar at the top of each screen:

1. **Sense** — one agent watches outside signals (weather, port delays, strikes); another forecasts demand and finds products bought together.
2. **Decide** — one agent simulates thousands of possible futures per product to estimate stockout risk and assign a zone; another sets the rules any action must respect (budget, supplier and environmental limits).
3. **Resolve** — one agent ranks suppliers and negotiates price; another finds profitable warehouse transfers and markdowns.
4. **Execute** — approved actions become purchase orders, transfers or campaigns. An arbiter settles disagreements between agents and decides which actions need a human.

Nothing runs unattended at first: an action type earns automatic execution only after a manager has approved it repeatedly. The full design is in [plan/README.md](plan/README.md).
