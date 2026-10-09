# MK6 — SENSE and DECIDE agents

**Goal:** four agents that turn raw data and outside signals into a per-SKU decision record: forecast, stockout risk, zone, Profit-at-Risk, safety stock, reorder point, recommended quantity, and the constraints any action must respect.

Each agent subclasses `Agent` (mk5 §5), reads from the database and blackboard, writes its results to the blackboard, and returns a small partial state dict. Shared state keys are defined in mk8; this file names the keys each agent returns.

## 1. Macro Sentinel — `agents/macro_sentinel.py` (SENSE, key `macroSentinel`)

**Job:** produce the active `RiskSignal` rows and, from them, a lead-time modifier per supplier and a demand multiplier per category.

Steps:

1. **Weather (real).** If `settings.weather_enabled`, for each distinct supplier and location city call Open-Meteo (no key needed) with `httpx.AsyncClient(timeout=6)`:
   ```
   GET https://api.open-meteo.com/v1/forecast
       ?latitude={lat}&longitude={lon}
       &daily=precipitation_sum,wind_speed_10m_max,temperature_2m_max
       &forecast_days=7&timezone=auto
   ```
   Take the 7-day maxima and apply:

   | Condition | Signal | `lead_modifier` | Demand effect |
   |---|---|---|---|
   | wind ≥ 62 km/h or rain ≥ 115 mm/day | WEATHER / HIGH (storm) | 1.5 | Instant Food, Staples × 1.25 |
   | rain ≥ 65 mm/day | WEATHER / MEDIUM (heavy rain) | 1.2 | Instant Food × 1.15 |
   | temperature ≥ 42 °C | WEATHER / MEDIUM (heatwave) | 1.0 | Dairy × 0.95, Snacks × 1.1 |

   Any failure or timeout is caught and logged as an `error` event; the run continues with stored signals. Cache responses for 3 hours in a module-level dict.
2. **Simulated feeds.** Read `data/feeds/logistics.json`, `commodity.json`, `transport.json` (mk2) and keep entries with `active: true`.
3. **Scenario override.** If `state["scenario"]` has `lead_mult` or `demand_mult` (What-If runs), add a synthetic signal `source="scenario"` carrying them.
4. **Persist.** Deactivate previous `source in ("weather","feed","scenario")` signals and insert the new set; uploaded signals (`source="upload"`) stay active. Each signal gets `region_key`, `icon` (`CloudLightning` weather, `Anchor` logistics, `TrendingUp` commodity, `Truck` transport) and `msg_key` when it corresponds to one of the UI's four translated messages (`riskMsgWeather`, `riskMsgLogistics`, `riskMsgCommodity`, `riskMsgTransport`), otherwise an English `msg`.
5. **Fuse.** For supplier `s`: `lead_modifier[s] = min(2.0, Π modifiers of signals whose region is within 300 km of the supplier or of STORE-01)`. For category `c`: `demand_multiplier[c] = clip(Π multipliers, 0.7, 1.6)`. Capping stops several overlapping alerts from compounding into absurd values.
6. **Count impact.** `skus_at_risk` per signal = number of SKUs whose primary supplier is affected or whose category is listed.
7. **Optional LLM use.** If `data/feeds/news.json` exists (list of headline strings), classify each with `llm.json(NewsSignal, CLASSIFY_NEWS, headline, fallback=lambda: NewsSignal(relevant=False))` where `NewsSignal = {relevant: bool, type: enum, severity: enum, region_key: enum of REGIONS keys, lead_modifier: float 1.0–1.5}`; clamp the modifier and add relevant ones. This is the kind of task a 4B model does well: short text in, small enum-heavy schema out.

Blackboard writes: `put("lead_time_risk", "HIGH"|"MEDIUM"|"LOW")` (HIGH if any modifier ≥ 1.3), one `act(...)` per new HIGH signal.

Returns: `{"lead_modifier": {...}, "demand_multiplier": {...}, "signal_count": n}`.

## 2. Demand Intelligence — `agents/demand_intel.py` (SENSE, key `demandIntel`; audit lines use `forecaster`)

**Job:** forecast every SKU and mine demand structure.

Steps (all analytics via `asyncio.to_thread`):

1. Load `sales_daily` and `stock_daily` for `STORE-01` from the database into frames (zero-filled per SKU-day).
2. `forecasts = forecast_all(...)` (mk4 §1). Cache the result in a module-level dict keyed by `(max Sale.id, today)`: scenario and strategy re-runs do not change history, so they reuse it and finish in about a second.
3. `rules = basket.mine_rules(...)`; `pairs = basket.aisle_pairs(...)`.
4. `cannibals = cannibal.detect(...)`.
5. `bull = bullwhip.series(...)`.
6. `phantoms = anomaly.phantom_inventory(...)`.
7. Store results for the presenters in one `Event(kind="metric", key="demand_intel", value={rules, pairs, cannibals, bull, phantoms})` and one `metric` event per SKU with `velocity, velocity_trend, model, wape, k, daily (list), monthly_actual, monthly_backcast`.

Blackboard writes: `put("demand_signal", "RISING"|"STABLE"|"FALLING")` from the catalogue-wide trend (±5 % band); for the strongest cannibalization pair `put("snacks_cannibalization", correlation)` (key name kept because the UI has a label for it; the value is whichever category is strongest); `act("forecaster", ...)` for the SKU with the largest positive trend, with `result="{model} · WAPE {wape}%"`.

Returns: `{"forecast_ready": True}`. The forecasts themselves are passed in memory through `state["forecasts"]` (a dict of `ForecastResult`), which mk8 marks as a non-serialised state field.

## 3. Inventory Optimizer — `agents/inventory_optimizer.py` (DECIDE, key `inventoryOptimizer`; audit lines use `monteCarloRiskEngine`)

**Job:** the core per-SKU decision record.

For each SKU:

1. Inputs: `on_hand` (today, `STORE-01`), `receipts` from open `Inbound` rows (`(expected_on − today).days, qty`), `fc = state["forecasts"][sku]`, primary supplier, `lead_samples` from `Delivery`, `lead_mult = lead_modifier[supplier] × scenario.lead_mult`, `demand_mult = demand_multiplier[category] × scenario.demand_mult`.
2. `mc = simulate(on_hand, receipts, fc.daily, fc.dispersion_k, lead_samples, settings.mc_paths, seed, demand_mult, lead_mult)`.
3. `sl = service_level(...)`; `ss, rop = reorder_point(mc.ltd, sl)`; apply `strategy.safetyStockMultiplier` to `ss`.
4. `lead_eff = mean(lead_samples) × lead_mult × strategy.leadTimeBuffer`; `doc = days_of_cover(on_hand, fc.velocity × demand_mult)`.
5. `par = profit_at_risk(...)`, `carry_roi` (mk4 §5).
6. `zone_raw = classify(...)`; `zone = with_hysteresis(zone_raw, last zones from SkuSnapshot)`.
7. `qty = order_quantity(...)` if `on_hand + open inbound < rop`, else 0.
8. Derived display fields: `risk = round(100 × mc.stockout_prob)`; for ghost/money SKUs, where shortage risk is near zero, `risk` instead reports the overstock severity `round(100 × min(1, excess_value / max(stock_value, 1)))` so the UI's risk column stays meaningful for every zone; `age` = days since last `Inbound.received_on`; `historicalStockouts` = number of zero-stock days in the last 90; `lastReorder` = latest `Inbound.ordered_on`.

Write one `SkuSnapshot(run_id, as_of=today, sku_id, zone, metrics={…all of the above…, mc_bins, p95_stock, sigma})`.

**Drift history.** If fewer than 7 distinct `as_of` days exist for the latest non-dry runs, back-fill them: for `d` in `today−6 … today−1`, recompute steps 1–6 with data truncated at `d` (stock from `StockDaily` on `d`, forecast velocity from the trailing 14 days up to `d`, no LLM, 500 paths) and write snapshots with `as_of=d`. This runs once after seeding and gives the Ikigai drift time-lapse real history immediately.

Blackboard writes: `put("par_total", "₹{total/1000:.1f}K", tone="chaos")`; `act("monteCarloRiskEngine", "Simulated {paths} demand paths for {n} SKUs", result="{k} above {threshold}% stockout probability")`; a `metric` event per SKU.

Returns: `{"decisions": {sku_id: {zone, risk, stockout_prob, par, qty, doc, lead_eff, supplier_id, on_hand, ss, rop}}}`.

## 4. Compliance Guardian — `agents/compliance_guardian.py` (DECIDE, key `complianceGuardian`; speaks as `cfoAgent` and `esgGuardian` in debates)

**Job:** publish the rule envelope for this run and expose a `review()` function the Arbiter calls on each proposal. Rules are data, evaluated in code, and every verdict cites the rule that fired, which is what makes the system auditable.

`envelope` built in `work()`:

| Field | Source |
|---|---|
| `budget_remaining` | `settings.monthly_budget × budget_factor[mode]` (Balanced 1.0, Cash Flow 0.6, Growth 1.3) minus confirmed `PurchaseOrder` value this calendar month |
| `banned_suppliers` | suppliers with `esg_score < 50`, plus `Preference(directive="avoid_supplier")` |
| `preferred_suppliers` | `Preference(directive="prefer_supplier")` |
| `max_qty` per SKU | `Preference(directive="cap_qty")` |
| `min_cover_days` per SKU/category | `Preference(directive="min_cover_days")` |
| `no_auto` scopes | `Preference(directive="no_auto")` |
| `max_air_share` | 0.2 (Balanced), 0.1 (Cash Flow), 0.4 (Growth) — share of orders that may ship by air |
| `carbon_price` | as in mk4 §10 |

```python
@dataclass
class Verdict:
    ok: bool
    severity: str          # "block" | "warn" | "pass"
    rule: str              # e.g. "BUDGET_CAP", "SUPPLIER_ESG_FLOOR", "MOQ", "SHELF_LIFE_CAP", "AIR_SHARE", "USER_PREFERENCE"
    voice: str             # "cfoAgent" or "esgGuardian"
    message_facts: dict    # numbers behind the verdict
    suggestion: dict | None  # e.g. {"qty": 480} or {"mode": "multimodal"} or {"supplier_id": "SUP-HUL"}

def review(proposal: dict, envelope: dict, committed_value: float) -> list[Verdict]
```

Checks, in order: supplier banned (block, suggest next-ranked supplier); quantity below MOQ or above shelf-life cap or user cap (block, suggest clamped quantity); order value over remaining budget (block, suggest the affordable quantity rounded down to MOQ, or zero); air mode beyond `max_air_share` or when multimodal has stockout probability within 5 points of air (warn, suggest multimodal); order value above `auto_max_order_value` (warn → forces human review); transfers that would push the destination above capacity (block, suggest capacity-limited quantity).

Blackboard writes: `put("esg_preference", "RAIL_BALANCED"|"AIR_PRIORITY"|"SEA_SAVER")` by mode (Balanced / Growth / Cash Flow); `act("cfoAgent", "Budget envelope set", result="₹{remaining}K of ₹{total}K available")`.

Returns: `{"envelope": envelope}`.

## Verify

`uv run pytest tests/agents/test_sense_decide.py` (uses the seeded database, LLM offline, weather disabled):

- **Golden zones:** after Macro Sentinel → Demand Intel → Inventory Optimizer, every SKU's zone equals `ZONE_TARGET` from the catalogue (the mock's zones). If a SKU misses, adjust seed parameters in mk2 (stock, inbound, lead-time sigma), not the classifier.
- Chaos SKUs have `stockout_prob > 0.6`; sweet SKUs have `stockout_prob < 0.15`; ghost/money SKUs have `doc > 30`.
- Sweet SKUs have `par == 0` or negligible (< ₹500); chaos SKUs have `par > 0`.
- 7 distinct `as_of` days of snapshots exist after the first run.
- Scenario `{"lead_mult": 1.3}` does not lower any SKU's `stockout_prob` and raises the total PaR.
- Strategy `Cash Flow` (multiplier 0.8) lowers every safety stock versus `Balanced`.
- `review()` blocks a proposal to a banned supplier and suggests another; blocks an order over budget and suggests a smaller quantity.
- With weather enabled but the network call monkeypatched to raise, the run completes and an `error` event exists.

## Done when

The golden-zone test passes and a full SENSE+DECIDE pass over the seeded data takes under 90 seconds cold and under 5 seconds with the forecast cache warm.

## Implementation notes (as built, 2026-10-09)

Verified by `tests/agents/test_sense_decide.py` (30 tests). Deviations from the text above:

- **Golden zones pass** on the test seed and on the real database's seed. Across six seeds, five classify all 10 SKUs correctly with a steady 7-day history; the sixth is an extreme draw where Surf Excel sold 35 % below normal and is (reasonably) classed as a money pit.
- **Seed changes made to get there** (the classifier's rules were not tuned):
  - Surf Excel's holding cost is seeded at 4.5 %/month instead of the mock's 6 %. At 6 % it sits within 1.5 points of the ghost/money boundary and ordinary sales noise flipped it.
  - Sweet and chaos SKUs now receive their latest delivery `age` days ago inside the pin window (previously all were 13 days old, which made 15-day-shelf-life butter look nearly expired and showed as value at risk).
- **`cover` vs. `doc`.** Days of cover for the shortage test counts stock already on order that arrives within the lead time (`cover`); the overstock test uses stock on hand (`doc`). Without this a healthy SKU with four days on the shelf and a delivery due tomorrow was classed as chaos. `zones.classify` reads `cover` when present.
- **Disruption delays stock already on its way**: open inbound arrives after `round(days x lead modifier)` days. This is what moves healthy SKUs into chaos under a severe what-if.
- **The scenario is applied once**, by Macro Sentinel, on top of the fused signal modifiers (signals capped at 2.0, scenario clamped to 0.5-3.0). Inventory Optimizer does not multiply it in again.
- **Dry runs leave no trace**: no risk signals, no snapshots. They also skip hysteresis so a what-if shows the unsmoothed effect.
- **"Today" is the last day in the data** (`as_of`), not the wall clock, so stale demo data still analyses consistently.
- **History back-fill** reuses any stored day and only computes missing ones; earlier days are computed with what was known then (weekday-mean forecast, 500 paths) and stored without the histogram.
- **Snapshots store `zone_raw`** next to the final zone, as the hysteresis rule needs yesterday's unsmoothed value.
- **Risk signals are replaced each run** (weather, feed, news), not accumulated; uploaded signals are kept and get their `skus_at_risk` refreshed.
- **Weather**: one failure stops further city lookups for that run (one error event, not five). Heatwave affects Snacks demand only (one multiplier per signal). Dynamic weather signals carry English `msg` and no `msg_key`.
- **Lead-time samples** fall back to the supplier's lateness pattern applied to `Sku.lead_time_days` when a SKU has fewer than 5 deliveries from its primary supplier.
- **`review(proposal, envelope, committed_value)`** reads optional context from the proposal dict (`moq`, `shelf_cap`, `alternatives`, `mode_risk`, `air_orders`, `total_orders`, `dest_free_capacity`); mk7 supplies it. Extra rules `AUTO_LIMIT` and `CAPACITY` are named explicitly. A compliant proposal returns an empty list.
- **`agents/stages.py::sense_and_decide()`** runs the four agents in order (SENSE in parallel). mk8's graph replaces it as the entry point; tests and the validator use it meanwhile.
- **Demand Intel cache** is keyed by (database file, last sale id, last sale day, external forecast timestamp). Measured on mains power: 26 s cold, 0.2 s warm.
- **Console encoding**: the CLI switches stdout to UTF-8 so the rupee sign and Hindi print on Windows.
- **Validator**: `agents` check added (quick, uses the simple forecast); `--perf` now times a cold and warm SENSE + DECIDE pass against the 90 s budget.
