# MK12 — Tests, backtest, runbook

**Goal:** prove the system works, measure whether its decisions beat a naive policy, and document how to run and demo it.

## 1. Test suite layout

```
backend/tests/
├─ conftest.py
├─ test_seed.py                 (mk2)
├─ test_ingest.py               (mk3)
├─ analytics/
│  ├─ test_montecarlo.py  test_policy.py  test_conformal.py  test_zones.py
│  ├─ test_basket.py  test_topsis.py  test_transfer.py  test_budget.py  test_forecast.py
├─ test_blackboard.py  test_llm.py  test_grounding.py      (mk5)
├─ agents/
│  ├─ test_sense_decide.py      (mk6)
│  └─ test_resolve_execute.py   (mk7)
├─ test_pipeline.py             (mk8)
├─ api/
│  ├─ test_bootstrap_contract.py  test_actions.py  test_sandbox.py  test_upload.py   (mk9)
├─ test_nlq.py                  (mk10)
└─ test_backtest.py             (this file)
```

`conftest.py`:

- `settings` fixture (session scope): sets environment before anything imports settings — `SARTHI_DB_PATH` to a file under `tmp_path_factory`, `SARTHI_LLM_ENABLED=false`, `SARTHI_WEATHER_ENABLED=false`, `SARTHI_MC_PATHS=800` — then clears `get_settings.cache_clear()` and resets `sarthi.db._engine = None`.
- `seeded_db` fixture (session scope): `init_db(); seed_database(seed=1, today=date(2026, 10, 1))`. A fixed `today` makes every test deterministic.
- `completed_run` fixture (session scope, depends on `seeded_db`): `asyncio.run(run_pipeline("test"))`, returns the run id. API and NLQ tests depend on it.
- `client` fixture: `TestClient(create_app())`, with the lifespan's startup run disabled via an env flag `SARTHI_SKIP_STARTUP_RUN=true` (add this boolean to `Settings` and check it in `lifespan`).
- Tests that mutate data (approve, transfer, upload) use a function-scoped copy of the database file (`shutil.copy`) so they do not affect one another.

The whole suite must run without LM Studio and without internet.

Marked tests: `@pytest.mark.llm` for the few that need the real model (skipped unless `SARTHI_TEST_LLM=1`). They check that with the model running: intent extraction resolves 8 of 10 paraphrased questions to the right intent; a narrated alert passes `grounded()`; `PreferenceOut` extraction returns a valid directive for 5 sample feedback sentences. These are quality checks, not gates.

## 2. Property checks worth having

Beyond the per-milestone tests, add these invariants in `test_pipeline.py`:

- **No invented numbers:** for every `Alert.msg` and every `debate` event text of a run, `grounded(text, facts)` is true with the facts stored alongside it.
- **Conservation:** after any sequence of transfers, total units per SKU across locations are unchanged.
- **Determinism:** two runs on the same data and strategy, LLM offline, produce identical zones, quantities and confidences.
- **Monotone strategy:** total proposed purchase value under Cash Flow ≤ Balanced ≤ Growth.
- **Safety:** with fresh bandit arms, a run creates zero `PurchaseOrder` rows (nothing auto-executes before autonomy is earned).

## 3. Backtest — `src/sarthi/eval/backtest.py`

Answers "is this better than what a spreadsheet would do?" by replaying the last 120 days of seeded demand under two policies, using the true demand that the generator produced (uncensored), with the same lead-time draws for both.

- **Naive policy:** reorder `velocity × lead × 2` when stock < `velocity × lead`, with `velocity` = trailing 30-day mean. This is the rule the PDF calls "what other teams build".
- **Sarthi policy:** each simulated day, compute velocity and dispersion from history up to that day (weekday-mean forecast for speed), `reorder_point` and `order_quantity` from mk4 with the newsvendor service level, and order when inventory position < reorder point.

Day loop per SKU: receive arrivals → serve demand (`sold = min(demand, on_hand)`) → apply policy → place order with lead time drawn from the supplier's history (seeded identically for both policies).

Metrics per policy, per SKU and in total:

| Metric | Definition |
|---|---|
| Fill rate | `Σ sold / Σ demand` |
| Stockout days | days ending with zero stock |
| Lost margin | `Σ (demand − sold) × unit margin` |
| Average inventory value | mean of `on_hand × cogs` |
| Holding cost | `Σ on_hand × cogs × holding_pct_month / 30` |
| Net | `− lost margin − holding cost` |

CLI:

```python
@app.command()
def backtest(days: int = 120):
    """Compare Sarthi's replenishment policy with a naive reorder rule."""
    from sarthi.eval.backtest import run_backtest, format_report
    typer.echo(format_report(run_backtest(days)))
```

`format_report` prints a per-SKU table and totals. Report the result honestly, including SKUs where the naive rule did better; the point is a measured comparison.

**Calibration report** (same module, `calibration()`): bucket historical SKU-days by predicted stockout probability (0–20 %, 20–40 %, …) using the as-of simulation, and report the observed stockout frequency per bucket. Well-calibrated risk numbers lie near the diagonal. Print the table under the backtest report.

`test_backtest.py`: the backtest runs on the seeded data in under 30 seconds; both policies have fill rate in `(0, 1]`; total units are conserved (`opening + received − sold = closing`) for each SKU and policy.

## 4. Quality gates

```powershell
uv run ruff check src tests
uv run pytest -q
uv run sarthi backtest
cd ..; npm run build
```

All four succeed before the project is called complete.

## 5. Runbook — `backend/README.md`

Write this file with the following content.

**First-time setup**

```powershell
cd "c:\Users\Kalp Shah\Desktop\Sarthi\backend"
uv sync
Copy-Item .env.example .env
uv run sarthi seed
uv run sarthi export-samples
```

**Start (three things)**

1. LM Studio → load the model from `.env` (GPU offload max, context 8192) → Developer → Start Server. Optional: without it the system runs in offline mode.
2. `uv run sarthi serve` (backend, port 8000). The first start runs the pipeline once; wait for `run 1 done` in the log (up to about 90 seconds while forecasts are fitted).
3. In the repo root: `npm run dev` (frontend, port 5173).

**Useful commands**

| Command | Effect |
|---|---|
| `uv run sarthi seed` | reset the database to the demo state |
| `uv run sarthi run` | run the agent pipeline once |
| `uv run sarthi run --lead-mult 1.3 --dry` | what-if run, no side effects |
| `uv run sarthi backtest` | policy comparison and calibration tables |
| `uv run pytest -q` | tests |
| `curl.exe http://127.0.0.1:8000/api/health` | shows `llm` or `offline` |

**Troubleshooting**

| Symptom | Cause and fix |
|---|---|
| `/api/health` says `offline` with LM Studio open | The server is not started, or `SARTHI_LLM_MODEL` differs from the identifier in LM Studio's Developer tab. Copy the identifier exactly. |
| LM Studio runs out of VRAM or is very slow | Lower context length to 4096, or use the 4B model instead of the 7B one. |
| Frontend shows the old mock numbers | Backend not reachable or no completed run yet. Check `/api/bootstrap`; a 503 means the first run is still in progress. |
| Alert text is stiff/template-like | Model offline, or its sentence failed the grounding check (see `Event` rows with `kind="llm"`). This is the safe behaviour. |
| Weather signals missing | No internet, or `SARTHI_WEATHER_ENABLED=false`. Simulated feeds still work. |
| `database is locked` | Two servers running against one file. Stop the extra `sarthi serve`. |

## 6. Demo script (about 5 minutes)

1. **Monitor.** "Every number here is computed: forecasts chosen per SKU by cross-validation, risk from 2,000 simulated demand paths."
2. **Inventory → Lays.** Histogram, days of cover against lead time, why it is in Chaos. Play the drift time-lapse.
3. **Alerts.** Point at a confidence figure: "This is the share of plausible forecast errors under which the decision stays the same." Approve one; show the TXID and the generated `.eml` in `backend/outbox/`.
4. **Intelligence.** Ask "why Lays?" → grounded explanation with the counterfactual. Type "prioritize cash flow for 30 days" → sidebar strategy changes; open Inventory to show reduced safety stocks.
5. **What-If.** Drag lead time up: risk curve moves, and the debate panel shows the agents objecting to and revising each other's proposals about real SKUs.
6. **Replenish.** Supplier scores, ESG shipping options, a warehouse transfer chosen by the optimiser.
7. **Data Hub.** Upload `sales.csv`, press Sync, numbers refresh.
8. **Trust story.** Close LM Studio, press Sync again: everything still works, in template wording. "The model improves language; it never decides a number. A fresh system auto-executes nothing; it earns autonomy per action type from the manager's approvals."
9. **Backtest.** Show the `sarthi backtest` table.

## Done when

- `uv run pytest -q` is green with LM Studio closed and the network disabled.
- `uv run sarthi backtest` prints both tables.
- `backend/README.md` exists and its first-time setup works on a clean clone.
- The mk11 screen checklist passes both online and offline.

## Implementation notes (as built, 2026-10-10)

Code: `eval/backtest.py`, `sarthi backtest`, `tests/test_backtest.py` (11 tests), `tests/test_llm_quality.py` (3 live-model checks), five invariant tests at the end of `tests/test_pipeline.py`, `backend/README.md`. Deviations from the text above:

- **Test layout.** Now as in section 1 (see the follow-up below).
- **Backtest demand.** The generator's true demand is used when the database still holds exactly the generated sales (`generator.true_demand`); after uploads, demand is estimated from sales as the agents do, and the report says which. Stock in transit at the start of the window is given to both policies. The naive rule orders only when no order is outstanding (without that it re-orders every day while waiting).
- **Each policy sees only its own past**: its own sales and stock in the replay, not the history that really happened. Substitution between products is not modelled (demand is the same for both policies).
- **Calibration** scores the chance of an empty shelf within the product's *shortest* lead time. Within that window nothing ordered later can arrive, so prediction and outcome describe the same event; a longer window let orders placed the next day "rescue" the shelf. The figure on the screens also allows for late deliveries, which cannot be scored against days on which no order was placed.
- **Engine fix found by the calibration.** The risk simulation counted a delivery expected in d days as arriving on day d+1, so a product with a delivery due the next morning showed a near-certain stockout. `montecarlo.arrival_index` puts it on day d. All earlier tests, including the fixed zone expectations of mk6, still pass.
- **Measured on the demo data (120 days, 10 products):** fill rate 97.6 % against 86.8 % for the naive rule, 20 empty-shelf days against 155, average stock ₹187K against ₹146K, net ₹76.7K better; no product where the naive rule did better. Calibration: 577 of 590 product-days fall in the 0-20 % row (predicted 0.3 %, came true 2.1 %); the other rows hold 2 to 6 days each and are reported as too thin to read.
- **Monotone strategy.** Now strict (see the follow-up below).
- **Live-model checks** (`SARTHI_TEST_LLM=1`): 10/10 reworded questions routed, 3/3 rewordings accepted by the number guard, 6/6 feedback sentences read correctly. The first measurement of feedback was 1 of 5: the prompt gave the model no definitions. `EXTRACT_PREFERENCE` now defines each directive with an example, more phrasings are caught without the model, and a supplier rule finds its supplier in the manager's words when the model names none.
- **Validator**: `check_backtest` replays the last 45 days and checks that the books balance. `uv run sarthi validate --full --frontend` therefore covers all four quality gates.
- **Not done here:** the mk11 screen checklist was re-run in headless Chrome during mk11 and its follow-up, not again in this milestone; the frontend is unchanged since.

## Follow-up (2026-10-18): the four open issues

**1. The intermittent "Windows fatal exception: access violation" is fixed.** A diagnostic (a Windows vectored
exception handler that records the faulting module) caught it inside SciPy's HiGHS solver,
`scipy/optimize/_highspy/_core.pyd`: a write to address 0x77, during a pipeline run. HiGHS keeps scheduler state
per operating-system thread, and the agents called it (budget MILP, transfer LP) from short-lived worker threads.
A stress test of 400 solves, each on a fresh thread, reproduced it: native fault reports in one run, and the
process crashed outright (segmentation fault) in two more. `analytics/solver.py` now runs every HiGHS call on one
long-lived thread. The same stress test through the project's own functions: five runs, 0 fault reports, every
solve on the one solver thread. No fault report in any test run since.

**2. The tests follow the layout of section 1.** `analytics/test_core.py` and `test_demand.py` were split into
`test_montecarlo, test_policy, test_conformal, test_zones, test_topsis, test_transfer, test_budget, test_forecast,
test_basket`, plus `test_esg` (shipping options) and `test_anomaly` (phantom inventory), which the list does not
name. `agents/test_resolve.py` and `test_execute.py` are now `agents/test_resolve_execute.py`; the EXECUTE tests come
last because they switch to a database of their own. `api/test_api.py` is now `test_bootstrap_contract, test_actions,
test_sandbox, test_upload`, with the shared client in `tests/api/conftest.py` and the field lists in
`tests/api/support.py`. The same 406 tests, nothing dropped (the count was checked before and after).

**3. The strategy now changes how much is ordered, not only when.** The safety multiplier (Cash Flow 0.8, Growth
1.5) also scales the part of the order-up-to level above expected demand, exactly as it scales safety stock in the
reorder point (`policy.order_quantity(..., safety_multiplier=)`). Proposed purchase value is now strictly
Cash Flow < Balanced < Growth, and the invariant test asserts it strictly. Balanced (1.0) is unchanged, so every
earlier result on the default strategy is the same.

**4. Test time.** The variation (2 to 6 minutes) came from fitting the forecasts, which is single-threaded CPU work
whose speed follows the machine's load: the same fit took 22 s to 129 s on different runs, even a second fit in the
same process. Windows power throttling was tested and is not the cause. What changed:
- The seeded database is built once per session (`template_db`) and copied for every test module that needs its
  own (`private_database`); it was re-seeded seven times.
- The forecast analysis of the seeded data is kept on disk between sessions, keyed by a hash of the code and the
  library versions it depends on (`.pytest_cache/sarthi`; `SARTHI_TEST_FRESH_FORECAST=1` forces a new fit).
- Fitting in parallel processes (`SARTHI_FORECAST_JOBS`) was tried: identical forecasts, no speed-up on this
  machine (22 s either way), so the default stays at one process.
- Result: 248 s before, 102-108 s after on a run that refits, less when the analysis is cached.

**Type checking.** Pyright (the checker inside the editor's Pylance) reported 695 errors with the repository's
`pyrightconfig.json`; now 0 in `src` and `tests`. pandas type stubs were added as a dev dependency (they removed
about 230 false reports); SQLModel columns in queries are wrapped in `col()`; values that may be missing are checked
explicitly (a clear error in the code, `must()` or an `assert` in the tests); the model gateway's `json()` is generic,
so it returns the schema it was given. `uv run sarthi validate --full` now includes a `types` check.
