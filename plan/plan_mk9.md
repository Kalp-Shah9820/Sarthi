# MK9 — HTTP API

**Goal:** expose everything the screens need. One read endpoint returns the whole UI data set in the exact shapes the frontend already uses; a handful of write endpoints perform the actions behind the existing buttons; two SSE endpoints stream agent activity.

Routers live in `src/sarthi/api/routers/` and are included in `create_app()` with prefix `/api`. All field names below are the frontend's existing names (camelCase). Conversion from database rows happens only in `api/presenters.py`.

## 1. Endpoint list

| Method | Path | Used by | Purpose |
|---|---|---|---|
| GET | `/api/health` | — | status + `llm` mode |
| GET | `/api/bootstrap?lang=EN` | app start, every refresh | all screen data (§2) |
| POST | `/api/runs` | Data Hub "Sync" | start a pipeline run → `{runId}` |
| GET | `/api/runs/{id}/stream` | (optional) | SSE of blackboard events |
| POST | `/api/alerts/{id}/approve` | Alerts | body `{feedback?}` → `{txid, status}` |
| POST | `/api/alerts/{id}/dismiss` | Alerts | body `{feedback?}` → `{status}` |
| POST | `/api/orders` | Replenish "Proceed" | body `{skuId, distributor, units, price, esgIndex}` → `{orderId, expectedDelivery}` |
| POST | `/api/transfers` | Replenish transfer modal | body `{skuId, fromId, toId, units}` → `{transferId}` |
| POST | `/api/campaigns` | Replenish "Launch" | body `{type}` → `{status: "live"}` |
| POST | `/api/sandbox/simulate` | What-If sliders | body `{lead, demand, stock, margin}` → `{risk, par, safetyStock, sensitivity}` |
| GET | `/api/sandbox/debate/stream` | What-If debate panel | query `lead, demand, stock, margin, lang`; SSE of debate lines |
| POST | `/api/chat` | Intelligence | mk10 |
| GET / PUT | `/api/strategy` | context | mk10 |
| POST | `/api/voice/intent` | voice button | mk10 |
| POST | `/api/datahub/upload/{type}` | Data Hub cards | multipart `file` → `{records, dropped, errors, time}` |
| GET | `/api/skus/{id}/explain?lang=EN` | (chat uses it) | grounded explanation + counterfactual |

Errors: validation problems return 422 (FastAPI default); unknown ids 404 `{"detail": "..."}`; `IngestError` 400 with the message. No endpoint returns a stack trace.

## 2. `GET /api/bootstrap`

Built by `presenters.bootstrap(lang) -> dict` from the latest non-dry `Run`. If no run has completed yet it returns HTTP 503 `{"detail": "warming up"}`; the frontend then keeps its built-in data and retries.

Top-level keys mirror the exports of `src/data/appData.js`:

| Key | Shape (per item) | Source |
|---|---|---|
| `skuData` | `id, name, cat, stock, vel, margin, lead, age, zone, risk, par, safetyStock, reorderPoint, cogs, shelfLife, velocityTrend, historicalStockouts, holdingCostPct, forecast[12], sales[12]` **plus** `daysStock, esg, co2, stockoutProb, lastReorder, decisionStatus, supplier, tier, recommendedQty` | `Sku` + today's `SkuSnapshot.metrics` |
| `skuMonteCarlo` | `{ [skuId]: { stockoutProb, p95Stock, sigma, bins[20]{range,count,highlight}, daysOfCover } }` | snapshot |
| `mbaRules` | `antecedent[], consequent, confidence, lift, support` | Demand Intel metric event |
| `cannibalization` | `rising, falling, rName, fName, category, correlation` | same |
| `bullwhipData` | `labels[12], raw[12], smoothed[12], reorder[12]` | same |
| `monthLabels`, `forecastMonths` | 12 month abbreviations | `forecast.month_labels` |
| `distributors` | `name, tat, reliability, price, incentive, score, tier, fulfillment, defectRate, capacityLimit, avgTAT, tatHistory[12]`, sorted by `score` descending | Distributor Selector metric event |
| `aisles` | `id, label, x, y, items[], heat, connections[], zone` | `Aisle` + basket pairs; `zone` = zone holding the most SKUs of that aisle (aisles without SKUs keep their seeded zone) |
| `labels` | `{ "sku011": "New SKU name", "chennaiPort": "Chennai Port", … }` | names for ids the static translations do not know |
| `live` | object below | — |

Field rules for `skuData`: `vel`, `stock`, `safetyStock`, `reorderPoint`, `par`, `risk` are integers; `margin` is `round(100 × (price − cogs) / price)`; `lead` is the rounded mean supplier lead time; `sales` = `monthly_actual` and `forecast` = `monthly_backcast`, rounded; `daysStock = round(doc)`; `esg` = primary supplier's ESG score; `co2` = kg CO₂ per unit for the recommended mode, 2 decimals, as a string; `stockoutProb = risk`; `decisionStatus` = `"critical"` if the SKU has an open alert and `risk > 60`, `"pending"` if it has an open alert, else `"auto"`; `supplier`/`tier` = primary supplier name and tier; `recommendedQty` = the purchase proposal's quantity, or `round(vel × lead × 1.3)` when none exists.

`live`:

```jsonc
{
  "online": true,
  "runId": 12,
  "llmMode": "llm",                      // or "offline"
  "strategy": { "mode": "Balanced", "savingsPriority": 0.5, "safetyStockMultiplier": 1.0,
                "leadTimeBuffer": 1.2, "lastUpdate": "14:32:01" },
  "pipeline": { "signals": 4, "phase": "execute" },
  "riskSignals": [ { "id": 1, "type": "WEATHER", "severity": "HIGH", "skus": 3,
                     "icon": "CloudLightning", "actionKey": "viewImpact",
                     "msgKey": "riskMsgWeather" /* or "msg": "…" */ } ],
  "mapRisks": [ { "id": "bayOfBengal", "type": "cyclone", "x": 75, "y": 70,
                  "severity": "high", "icon": "CloudLightning" } ],
  "drift": { "days": ["1","2","3","4","5","6","7"],
             "data": [ { "id": "SKU001", "zones": ["sweet", "…7 values"] } ] },
  "alerts": [ { "id": 31, "sku": "Lays Classic 26g", "zone": "chaos", "risk": 97, "confidence": 91,
                "msg": "…", "action": "…", "impact": "…", "status": "open", "txid": null } ],
  "auditTrail": [ { "ts": "14:32:01", "agentKey": "forecaster", "text": "…", "result": "…" } ],
  "sharedContext": [ { "key": "lead_time_risk", "value": "HIGH", "agentKey": "macroSentinel",
                       "tone": "chaos", "updated": "14:32:01" } ],
  "debate": [ { "agentKey": "negotiator", "tone": "sweet", "msg": "…" } ],
  "esg": { "recommendedIndex": 2,
           "options": [ { "tat": "1 day", "cost": "₹₹₹", "costVal": 3, "co2Pct": 100, "co2Key": "esgHigh" },
                        { "tat": "7 days", "cost": "₹", "costVal": 1, "co2Pct": 3, "co2Key": "esgLow" },
                        { "tat": "3 days", "cost": "₹₹", "costVal": 2, "co2Pct": 8, "co2Key": "esgLow" } ],
           "bySku": { "SKU003": { "recommendedIndex": 0, "options": [ /* same shape */ ] } } },
  "warehouses": [ { "id": "WH-MUM", "name": "Mumbai Central", "city": "Mumbai",
                    "stock": { "SKU002": 180 }, "capacity": 2000, "utilization": 0.72 } ],
  "transfers": [ { "fromId": "WH-DEL", "toId": "WH-MUM", "toCity": "Mumbai", "skuId": "SKU002", "units": 200 } ],
  "campaigns": [ { "type": "markdown", "target": "ghost", "discount": "15%", "estImpact": "₹12.4K", "status": "proposed" } ],
  "distributorScores": { "SKU003": [ { "name": "Reliance", "tat": 1, "reliability": 96, "score": 93 } ] },
  "replenishment": { "SKU003": { "units": 1488, "price": 11.6,
                                 "priceHistory": [ { "month": "May", "price": 11.04 } ] } },
  "coPurchasePairs": [ { "from": "a", "to": "b", "strength": 84 } ],
  "zoneStats": { "sweet": { "avgRisk": "2.4%", "confidence": "96.0%" } },
  "dataHub": { "metrics": { "totalIngested": "214,507", "freshness": "100.0%", "joinQuality": "100.0%", "alertsGenerated": "9" },
               "uploads": { "sku": { "progress": 100, "status": "done", "records": 10, "time": "14:20:11" } } }
}
```

Presenter rules:

- `alerts`: open and approved alerts of the latest run, ordered by `impact_value` descending. `msg`/`action`/`impact` are rendered in `lang` (`EN` or `HI`; other languages receive English).
- `auditTrail`: last 12 `action` events, newest first. `sharedContext`: `Blackboard.context()` excluding the internal `phase` key. `debate`: `debate` events of the latest non-dry run. `tone` is one of `sweet, chaos, ghost, money` (the UI maps it to a colour): `forecaster`/`esgGuardian` → ghost, `riskAgent` → chaos, `cfoAgent` → money, `negotiator`/`executionEngine`/`overstockResolver` → sweet, `rlhfArbiter` → money.
- `esg.options` keep the fixed order air, sea, multimodal. `co2Pct` is relative to the highest-emitting mode (= 100). `co2Key` is `esgHigh` above 70, `esgMedium` above 40, else `esgLow`. `cost` is `₹`, `₹₹`, `₹₹₹` by freight-cost rank. The top-level `options` are for a reference order (median SKU, 500 km).
- `mapRisks.severity` is lower-case; `type` is `cyclone`, `port`, `heatwave` or `strike`; `x`/`y` come from `REGIONS`.
- `drift.data[*].zones` are the last 7 `as_of` days from `SkuSnapshot`, oldest first.
- `warehouses` lists `kind="warehouse"` locations; `stock` contains only SKUs held there; `utilization = Σ on_hand / capacity`, 2 decimals.
- `distributorScores[sku][*].name` is the first word of the supplier name (the chart's axis label), `tat` an integer.
- `zoneStats[zone].avgRisk` = mean stockout probability of that zone's SKUs; `confidence` = mean alert confidence in that zone (or `"—"` if none). Both as percentage strings with one decimal.
- `coPurchasePairs.from/to` are lower-case aisle ids (the UI looks up translated aisle names by those keys); top 7 by strength.
- `dataHub.uploads` contains the latest successful `Upload` per type.

The response is cached in memory per `(run_id, lang, write_counter)`; any write endpoint increments `write_counter`.

## 3. Write endpoints

**`POST /api/alerts/{id}/approve`** — `execution_engine.execute(alert, source="alert")`; set `status="approved"`, `decided_at`, `feedback`; `bandit.record(arm, True)`; if feedback text is present run preference extraction (mk8 §6) as a background task. Returns `{txid, status: "approved"}`. Approving an already approved alert returns the same `txid`.

**`POST /api/alerts/{id}/dismiss`** — set `status="dismissed"`; `bandit.record(arm, False)`; preference extraction as above. Returns `{status: "dismissed"}`.

**`POST /api/orders`** — the user's own order from the Replenish modal. Resolve `distributor` (name) to a supplier; `mode` from `esgIndex` (0 air, 1 sea, 2 multimodal; default the SKU's recommended mode). Run `compliance_guardian.review` on it. The order is always recorded (`source="user"`): this modal has no error state in the current UI, so a rejection would look like a dead button, and a manager's explicit order is a legitimate override. Any `block` or `warn` verdicts are written to the audit trail as `cfoAgent`/`esgGuardian` lines so the override is visible and auditable. Returns `{orderId, expectedDelivery: "1–2 days"}`.

**`POST /api/transfers`**, **`POST /api/campaigns`** — call `execute` with a payload built from the body (`campaigns` looks up the stored candidate of that `type`). Transfers of more units than the source holds are clamped to the available quantity.

**`POST /api/runs`** — `asyncio.create_task(run_pipeline("sync"))`, returns `{runId}` of the run being started (the runner inserts the `Run` row before its first await, so the id is known). **`POST /api/datahub/upload/{type}`** — reads the file (reject > 20 MB with 413), calls `ingest`.

## 4. Sandbox

**`POST /api/sandbox/simulate`** — pure computation, no database writes:

- `mu = [demand] × 30`; `k` = median dispersion of the catalogue; `lead_samples` = 200 draws from a gamma distribution with mean `lead` and coefficient of variation 0.25 (fixed seed).
- `risk = round(100 × simulate(stock, [], mu, k, lead_samples, 2000, seed=7).stockout_prob)`.
- `par = round(expected_lost_units × (margin / 100) × ref_price)`, `ref_price` = catalogue median price.
- `safetyStock` from `reorder_point` at service level 0.95.
- `sensitivity = [{ "lt": "1d", "risk": … }, … 15 points]` re-running with mean lead 1…15 and the same seed.

Returns in a few milliseconds, so the page can call it on every slider change (debounced).

**`GET /api/sandbox/debate/stream`** — starts `run_pipeline("sandbox", dry_run=True, scenario={"lead_mult": clip(lead / 5, 0.5, 3), "demand_mult": clip(demand / 50, 0.5, 3)}, lang=lang)` as a task (5 and 50 are the sliders' default positions, i.e. "no change"), subscribes to that run's blackboard queue, and emits each `debate` event as `data: {"agentKey", "tone", "msg"}`. When the run finishes it sends `event: done`. If the client disconnects, the subscription is removed. A new request cancels the previous sandbox task for that client (keep one module-level task handle).

## 5. SSE helper — `api/sse.py`

```python
from sse_starlette.sse import EventSourceResponse

def stream_events(run_id: int, kinds: set[str], render) -> EventSourceResponse:
    async def gen():
        q = blackboard.subscribe(run_id)
        try:
            while True:
                ev = await q.get()
                if ev.get("kind") == "_end":
                    yield {"event": "done", "data": "{}"}
                    return
                if ev["kind"] in kinds:
                    yield {"data": json.dumps(render(ev), ensure_ascii=False)}
        finally:
            blackboard.unsubscribe(run_id, q)
    return EventSourceResponse(gen())
```

The runner publishes a synthetic `{"kind": "_end"}` to the run's subscribers when the run completes or fails. Subscribe **before** starting the run task so no early event is missed.

## Verify

`uv run pytest tests/api` using `fastapi.testclient.TestClient` on a seeded database with one completed run:

- **Contract test:** `/api/bootstrap` contains every top-level key above, and every `skuData` item has exactly the mock's 20 fields plus the 9 added ones. The list of expected keys is copied from `appData.js` into the test so any drift fails loudly.
- `skuMonteCarlo[id].bins` has 20 items with `range`, `count`, `highlight`; `forecast` and `sales` have 12 numbers; `drift.data[*].zones` has 7 valid zone ids.
- Approve an alert → 200 with a `txid`; approve again → same `txid`; `BanditArm.alpha` increased by exactly 1.
- Dismiss with feedback "max 300 units" → a `Preference(directive="cap_qty", value=300)` exists.
- `POST /api/orders` creates a `PurchaseOrder` and the next bootstrap shows the order in `auditTrail`.
- `POST /api/transfers` moves stock between the two warehouses in the next bootstrap.
- `/api/sandbox/simulate`: risk rises as `lead` rises and falls as `stock` rises; `sensitivity` has 15 points.
- Upload of `samples/sku.csv` returns `records: 10`; a `.txt` upload returns 400.

Manual:

```powershell
uv run sarthi serve
curl.exe "http://127.0.0.1:8000/api/bootstrap" | Select-Object -First 1
curl.exe -N "http://127.0.0.1:8000/api/sandbox/debate/stream?lead=12&demand=80&stock=200&margin=20&lang=EN"
```

The second command prints debate lines one by one and ends with `event: done`.

## Done when

API tests pass and the interactive docs at `http://127.0.0.1:8000/docs` list every endpoint in §1.

## Implementation notes (as built, 2026-10-09)

Verified by `tests/api/test_api.py` (26 tests) and by a live server with the model loaded. Code: `api/presenters.py`, `api/sse.py`, `api/routers/` (`read`, `runs`, `alerts`, `actions`, `sandbox`, `datahub`), `analytics/whatif.py`. Deviations from the text above:

- **Not in this milestone:** `/api/chat`, `/api/strategy`, `/api/voice/intent` and `/api/skus/{id}/explain` belong to mk10 and are not registered yet. The other 12 endpoints are.
- **`skuData[*].co2`** is the kg of CO2 for the recommended order by the recommended shipping mode, not per unit. Per unit it rounds to `0.00` for every product (a 26 g packet carried 150 km emits well under a gram).
- **`riskSignals[*]`** always carry `msg`, and `msgKey` as well when the signal has a translation key.
- **Shipping cards (`esg`)** are costed in the presenter from the same `esg.options` the agents use. For a product with a purchase alert the recommended card is the mode on that alert; for a product that needs no order it is the cheapest and cleanest mode (sea), since nothing is urgent.
- **`drift`** uses the latest stored snapshot per day across runs (each run stores only the days it has not seen), padded at the front if fewer than 7 days exist.
- **Bootstrap cache** key is `(database, run id, language, write counter)`. A run made by another process (`sarthi run`) changes the run id, so it is picked up too.
- **Approve / dismiss.** The click, the execution and the approval-rate update happen before the response; only reading the feedback text runs afterwards. A lock makes a double click execute once. Approving a dismissed alert, or dismissing an approved one, returns 409. `memory.note_decision()` was split out of `learn_from_decision()` for this.
- **Feedback learning is stricter** (found in the live check: for "good call" the model stored a sentence and a number the manager never wrote). The stored note is now always the manager's own words, and a number the manager did not write demotes the rule to a plain note.
- **`POST /api/orders`**: the distributor may be given by id, full name or the short name on the charts; an unknown one is 404. The unattended-order limit is not applied (a person is placing the order). `expectedDelivery` is `"N–N+1 days"` from the supplier's lead time and the chosen mode. Rule breaches are written to the audit trail with the result "recorded on the manager's instruction".
- **`POST /api/transfers`** also returns `units` actually moved; same source and destination is 400.
- **`POST /api/runs`** returns the run already in progress if asked again while one is running.
- **`GET /api/runs/{id}/stream`** replays what is already stored before going live, so it can be opened at any time, including after the run has finished. It carries `context`, `action`, `debate` and `error` events.
- **What-if numbers** (`analytics/whatif.py`) use one set of simulated futures for all 16 lead times instead of calling `montecarlo.simulate` 16 times (about 130 ms). With nothing on order the two are identical, which a test asserts. Measured 8-19 ms per request over HTTP.
- **What-if debate.** A new request cancels the previous one, and so does the listener leaving; a cancelled run is recorded with status `cancelled`. Earlier what-if runs have their events deleted, but their `Run` rows are kept: deleting them let SQLite give the same run id to a later run. Debate lines arrive together near the end of the run, because the Arbiter writes the debate once it has ruled. The first what-if after a server start takes as long as a first run (forecasts are fitted once per process); later ones take 1-2 s.
- **Errors**: anything unforeseen is logged and answered as `500 {"detail": "internal error"}`.
- **Validator**: `check_http_api` (read-only) checks that every endpoint is registered and that the bootstrap has the frontend's keys.
