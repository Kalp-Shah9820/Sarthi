# Sarthi backend — build plan

Twelve milestones. Do them in order; each ends with a **Verify** block that must pass before the next starts.

| File | Milestone | Output |
|---|---|---|
| `plan_mk1.md` | Project setup (uv, LM Studio, layout) | `backend/` boots, `/api/health` answers |
| `plan_mk2.md` | Database and seed data | SQLite with 18 months of history for the 10 UI SKUs |
| `plan_mk3.md` | Data Hub ingestion | 8 upload types parsed, validated, upserted |
| `plan_mk4.md` | Analytics core | Forecast, Monte Carlo, policy, zones, PaR, basket, TOPSIS, LPs |
| `plan_mk5.md` | Blackboard, LLM gateway, grounding | Event log + local-model client with offline fallback |
| `plan_mk6.md` | SENSE and DECIDE agents | Macro Sentinel, Demand Intel, Inventory Optimizer, Compliance Guardian |
| `plan_mk7.md` | RESOLVE and EXECUTE agents | Distributor Selector, Overstock Resolver, Execution Engine, negotiation |
| `plan_mk8.md` | Orchestration, Arbiter, learning | LangGraph run, debate, confidence routing, feedback bandit |
| `plan_mk9.md` | HTTP API | `/api/bootstrap` + action endpoints + SSE streams |
| `plan_mk10.md` | Chat, strategy, voice, languages | Intent router, policy compiler, voice intents |
| `plan_mk11.md` | Frontend wiring | Every screen reads/writes the backend; no visual change |
| `plan_mk12.md` | Tests, backtest, runbook | Test suite green, demo script |

## Fixed decisions

1. **No paid LLM.** The only model endpoint is LM Studio's OpenAI-compatible server on `http://localhost:1234/v1`. Model choice for a 6 GB GPU is in `plan_mk1.md` §4.
2. **The system must work with LM Studio closed.** Every LLM call has a deterministic fallback (templates or rules). The LLM improves wording and language understanding; it never decides a number.
3. **Numbers come from code.** Forecasts, probabilities, quantities, prices and rupee values are computed by `sarthi.analytics`. Any LLM sentence is checked against those values (`plan_mk5.md` §4) and replaced by a template if it contains a number the tools did not produce.
4. **Agent roster follows the UI**, not the PDF's variants. The seven agents and four phases are the ones drawn in `src/components/PipelineVisualizer.jsx`: SENSE (Macro Sentinel, Demand Intel) → DECIDE (Inventory Optimizer, Compliance Guardian) → RESOLVE (Distributor Selector, Overstock Resolver) → EXECUTE (Execution Engine), plus an Arbiter.
5. **No UI or feature change.** Frontend edits are limited to where data comes from. Every edited expression keeps the current hardcoded value as its fallback, so the app renders exactly as today when the backend is down.
6. **Nothing leaves the machine except weather lookups** (Open-Meteo, free, no key). Emails and purchase orders are written to the database and `backend/outbox/`; they are never sent.
7. **Real numbers replace mock numbers.** Seed data reproduces the UI's 10 SKUs, 3 distributors and 4 warehouses, and is tuned so every SKU lands in the same Ikigai zone as the mock. Individual values (risk %, PaR, safety stock) will differ from the mock because they are now computed.

## Conventions

- All commands run in PowerShell from `c:\Users\Kalp Shah\Desktop\Sarthi\backend` unless stated.
- Python 3.12, managed by uv. Never call `pip` or `python` directly; use `uv add` and `uv run`.
- Package name `sarthi`, source in `backend/src/sarthi/`.
- API prefix `/api`, port 8000. Vite dev server (port 5173) proxies `/api` to it.
- JSON sent to the frontend uses the frontend's existing camelCase field names. Python code uses snake_case; conversion happens only in `sarthi/api/presenters.py`.
- Agent identifiers in events use the frontend's i18n keys: `macroSentinel`, `demandIntel`, `forecaster`, `inventoryOptimizer`, `monteCarloRiskEngine`, `riskAgent`, `complianceGuardian`, `cfoAgent`, `esgGuardian`, `distributorSelector`, `negotiator`, `overstockResolver`, `rlhfArbiter`, `executionEngine`.
- Zone identifiers: `sweet`, `chaos`, `ghost`, `money`.
- **Regression gate:** after every milestone run `uv run sarthi validate --full --frontend`. It imports every `sarthi` module, checks config, the health endpoint and LM Studio, then runs ruff, pytest and the Vite build. Each milestone appends its own check function to `CHECKS` in `src/sarthi/validate.py` (built in mk1).

## Final layout

```
Sarthi/
├─ plan/                      this folder
├─ backend/
│  ├─ pyproject.toml  .python-version  .env  .env.example
│  ├─ data/        sarthi.db, feeds/*.json (simulated external feeds)
│  ├─ samples/     the 8 example upload files
│  ├─ outbox/      generated .eml and PO .json files
│  ├─ src/sarthi/
│  │  ├─ config.py  db.py  models.py  cli.py
│  │  ├─ seed/         catalog.py  generator.py  samples.py
│  │  ├─ ingest/       schemas.py  loader.py
│  │  ├─ analytics/    forecast.py conformal.py montecarlo.py policy.py par.py zones.py
│  │  │                basket.py cannibal.py bullwhip.py topsis.py transfer.py budget.py
│  │  │                esg.py anomaly.py
│  │  ├─ blackboard/   store.py
│  │  ├─ llm/          gateway.py  grounding.py  prompts.py  templates.py
│  │  ├─ agents/       base.py macro_sentinel.py demand_intel.py inventory_optimizer.py
│  │  │                compliance_guardian.py distributor_selector.py overstock_resolver.py
│  │  │                execution_engine.py arbiter.py negotiation.py debate.py
│  │  ├─ orchestrator/ state.py  graph.py  runner.py
│  │  ├─ learning/     bandit.py  memory.py  confidence.py
│  │  ├─ nlq/          intents.py  router.py  strategy.py  voice.py
│  │  ├─ api/          main.py  presenters.py  sse.py  routers/*.py
│  │  └─ eval/         backtest.py
│  └─ tests/
└─ src/ …                     existing frontend (+ src/api/ added in mk11)
```
