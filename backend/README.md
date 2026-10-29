# Sarthi backend: runbook

How to set up, start, check and demo the backend. The project overview is in the [root README](../README.md); the design is in [plan/](../plan/README.md).

## First-time setup

```powershell
cd "c:\Users\Kalp Shah\Desktop\Sarthi\backend"
uv sync
Copy-Item .env.example .env
uv run sarthi seed
uv run sarthi export-samples
```

`seed` fills the database with 18 months of demo history for 10 products. `export-samples` writes one example upload file per Data Hub card into `samples/`.

## Start (three things)

1. **LM Studio** (optional). Load the model named in `.env` (`qwen/qwen3-4b-2507`; GPU offload at maximum, context 8192), then Developer → Start Server. Without it the system runs in offline mode: every number is the same, and sentences use fixed wording.
2. **Backend.** `uv run sarthi serve` (port 8000). The first start on a seeded database runs the agents once; `curl.exe http://127.0.0.1:8000/api/bootstrap` answers `503 warming up` until that run has finished (up to about 90 seconds while forecasts are fitted).
3. **Frontend.** In the repo root: `npm run dev` (port 5173), then open `http://localhost:5173`.

## Useful commands

| Command | Effect |
|---|---|
| `uv run sarthi seed` | Reset the database to the demo state |
| `uv run sarthi run` | Run the agent pipeline once and print a summary |
| `uv run sarthi run --lead-mult 1.3 --dry` | A what-if run: stores no alerts, executes nothing |
| `uv run sarthi backtest` | Policy comparison and risk calibration tables |
| `uv run sarthi validate` | Check every milestone, a few seconds each |
| `uv run sarthi validate --full --frontend` | The same plus lint, all tests and the frontend build |
| `uv run pytest -q` | Tests only |
| `curl.exe http://127.0.0.1:8000/api/health` | Shows `llm` or `offline` |

## Quality gates

All four succeed before a change is called done. `uv run sarthi validate --full --frontend` runs the first, second and fourth, a short backtest, and the type checker (Pyright, the one inside the editor's Pylance).

```powershell
uv run ruff check src tests
uv run pytest -q
uv run sarthi backtest
cd ..; npm run build
```

The tests need neither LM Studio nor the internet. To also measure the real model (LM Studio running):

```powershell
$env:SARTHI_TEST_LLM = "1"; uv run pytest -m llm -q -s; Remove-Item Env:SARTHI_TEST_LLM
```

That reports how many of 10 reworded questions the model routes correctly, how many rewordings pass the number check, and how many of 6 feedback sentences it reads correctly. These are measurements of the model, not of the code.

## Reading the backtest

`uv run sarthi backtest` replays the last 120 days twice, once under a naive reorder rule ("order twice the lead-time demand when stock falls below one lead time of demand") and once under Sarthi's rule. Both start with the same stock, face the same demand and get the same supplier lead times, and each sees only what it would have known on the day.

- **Fill** is the share of demand that was served. **Out days** are days that ended with an empty shelf.
- **Net** is minus lost margin minus holding cost, so higher is better. It deliberately charges Sarthi for the extra stock it holds.
- The report lists any product where the naive rule did better.
- **Calibration** asks whether a stated risk comes true about that often. Rows with few days are flagged as too thin to read.

The demo history is generated, so the size of the gap says more about the method than about any real store.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `/api/health` says `offline` with LM Studio open | The server is not started, or `SARTHI_LLM_MODEL` differs from the identifier in LM Studio's Developer tab. Copy the identifier exactly. |
| LM Studio runs out of memory or is very slow | Lower the context length to 4096. Do not load the "thinking" variant of the model: it answers slowly or not at all. |
| The screens show the built-in sample numbers | Backend not reachable, or no completed run yet. Check `/api/bootstrap`; a 503 means the first run is still in progress. The screens switch over by themselves within 15 seconds. |
| A click works but the screens do not change | The agents re-run about a second after an order, transfer or approval. If `SARTHI_RERUN_AFTER_ACTION=false` is set, press Sync on the Data Hub. |
| An alert I dismissed does not come back | Intended: a dismissed recommendation is not raised again for `SARTHI_DECISION_MEMORY_DAYS` (7) unless the product changes zone. |
| Alert text is stiff or template-like | The model is offline, or its sentence failed the number check and was discarded (see `Event` rows with `kind="llm"`). This is the safe behaviour. |
| Weather signals missing | No internet, or `SARTHI_WEATHER_ENABLED=false`. The simulated feeds still work. |
| `database is locked` | Two servers are running against one file. Stop the extra `sarthi serve`. |
| Port 8000 already in use | Another backend is running. Find it with `netstat -ano \| findstr :8000` and end that process. |
| `validate` shows `WARN` for `pytest` with "native fault report" | A native library reported a fault while the tests ran. The known cause (SciPy's solver entered from many threads) is fixed; if it appears again, note which test was running and report it. |
| Tests or forecasting are several times slower than usual | Forecast fitting is single-threaded and follows the machine's load: the same fit took 22 s to 129 s on different runs. Close heavy programs or plug the laptop in. The tests keep the fitted analysis on disk, so only the first run after a code change refits. |

## Demo script (about 5 minutes)

1. **Monitor.** "Every number here is computed: a forecast chosen per product by cross-validation, risk from 2,000 simulated demand paths."
2. **Inventory → Lays.** The histogram, days of cover against lead time, why it is in the Chaos Zone. Play the drift time-lapse.
3. **Alerts.** Point at a confidence figure: "This is the share of plausible forecast errors under which the decision stays the same." Approve one; show the transaction id, then the `.eml` file in `backend/outbox/` (for a purchase order).
4. **Intelligence.** Ask "why is lays in the chaos zone?" for a grounded explanation with what would change the decision. Type "prioritize cash flow for 30 days": the sidebar strategy changes, and a few seconds later Inventory shows lower "safe:" figures.
5. **What-If.** Drag lead time up: the risk curve moves, and the debate panel shows the agents objecting to and revising each other's proposals about real products.
6. **Replenish.** Supplier scores, shipping options, a warehouse transfer chosen by the optimiser. Place an order: the receipt opens, and the agents re-run by themselves.
7. **Data Hub.** Upload `samples/sales.csv`, press Sync, numbers refresh.
8. **Trust story.** Stop the LM Studio server and press Sync again: everything still works, in fixed wording. "The model improves the language; it never decides a number. A fresh system executes nothing by itself; it earns that per action type from the manager's approvals."
9. **Backtest.** Show the `uv run sarthi backtest` tables, including the calibration rows that are too thin to trust.
