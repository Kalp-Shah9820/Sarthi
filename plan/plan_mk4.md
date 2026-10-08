# MK4 — Analytics core

**Goal:** every number the UI shows is produced by a pure, tested Python function. No agent, no LLM, no HTTP in this milestone. All modules live in `src/sarthi/analytics/` and take plain arrays/frames so they are trivially testable.

Algorithm choices and why:

| Need | Algorithm | Why this one |
|---|---|---|
| Demand forecast | Model selection by rolling-origin cross-validation among AutoETS, AutoTheta, SeasonalNaive, CrostonOptimized | No single model wins across fast, slow and intermittent SKUs; picking per SKU by out-of-sample error is the standard remedy. |
| Sales ≠ demand | Stockout-censoring correction | Days with zero stock record zero sales, which would teach the model that demand fell. |
| Forecast uncertainty | Split conformal intervals from CV residuals | Distribution-free coverage guarantee; no normality assumption. |
| Stockout risk | Monte Carlo with negative-binomial demand and bootstrapped supplier lead times | Retail demand is over-dispersed counts; lead-time error is skewed. Closed-form safety-stock formulas assume normality for both. |
| Service level | Newsvendor critical ratio per SKU | A 4 %-margin staple and a 35 %-margin item should not share one service target. |
| Capital at stake | Profit-at-Risk (shortage side) and carrying-loss (overstock side) | Ranks alerts in rupees, as the UI displays. |
| Supplier choice | TOPSIS against fixed ideal points, Beta-posterior reliability | Multi-criteria with tunable weights; fixed ideals avoid rank reversal when a supplier is added. |
| Transfers | Transportation linear programme | Exact minimum-cost redistribution across warehouses. |
| Budget | 0/1 knapsack (MILP) | Exact choice of which orders to fund under a cash cap. |
| Co-purchase | Exact support/confidence/lift over baskets | Catalogue is small; exact enumeration beats an approximate miner. |

## 1. `forecast.py`

```python
@dataclass
class ForecastResult:
    sku_id: str
    model: str                 # chosen model name
    daily: np.ndarray          # next 30 days, mean demand per day
    wape: float                # CV weighted absolute percentage error (0..1+)
    abs_residuals: np.ndarray  # |y - yhat| from CV, daily
    rel_residuals_14d: np.ndarray  # (sum_y - sum_yhat) / max(sum_yhat, 1) per CV window
    dispersion_k: float
    velocity: float            # mean unconstrained demand, last 14 days
    velocity_trend: float      # (last 14d mean - prior 14d mean) / prior
    monthly_actual: list[float]    # 12 values, mean daily units per 30-day block
    monthly_backcast: list[float]  # 12 values, out-of-sample forecast for the same blocks
```

Steps in `forecast_all(sales_daily: pd.DataFrame, stock_daily: pd.DataFrame, horizon=30) -> dict[str, ForecastResult]` (`sales_daily`: `sku_id, day, qty`, one row per SKU-day with zeros filled):

1. **Uncensor.** For each SKU-day where end-of-day `on_hand == 0` and `qty` is below the trailing 28-day same-weekday mean, replace `qty` with that mean. Keep the original in a `sold` column.
2. Build the long frame `unique_id, ds, y` required by statsforecast.
3. **Cross-validate** (short horizon, drives model choice and residuals):
   ```python
   from statsforecast import StatsForecast
   from statsforecast.models import AutoETS, AutoTheta, SeasonalNaive, CrostonOptimized

   MODELS = [AutoETS(season_length=7), AutoTheta(season_length=7), SeasonalNaive(season_length=7), CrostonOptimized()]
   sf = StatsForecast(models=MODELS, freq="D", n_jobs=1)
   cv = sf.cross_validation(df=df, h=14, n_windows=6, step_size=14)
   if "unique_id" not in cv.columns:
       cv = cv.reset_index()
   ```
   Model columns are named `AutoETS`, `AutoTheta`, `SeasonalNaive`, `CrostonOptimized`. Per SKU pick the model with the lowest `WAPE = Σ|y − ŷ| / max(Σ y, 1)`. Clip all forecasts at 0.
4. **Forecast** `sf.forecast(df=df, h=horizon)` and keep each SKU's chosen column as `daily`.
5. **Monthly back-cast** for the 12-point charts: `sf_m = StatsForecast(models=[AutoETS(season_length=7), SeasonalNaive(season_length=7)], freq="D", n_jobs=1)`; `sf_m.cross_validation(df=df, h=30, n_windows=12, step_size=30)`; per SKU use AutoETS unless SeasonalNaive had the lower WAPE; average `y` and `ŷ` per cutoff to get the two 12-value lists (oldest first).
6. **Dispersion**: with daily CV pairs, `var = mean((y − ŷ)²)`, `mu = mean(ŷ)`; `k = mu² / (var − mu)` if `var > mu` else `1e6`; clip to `[0.5, 1e6]`.
7. If an external forecast file exists (mk3), blend `daily = 0.7 × daily + 0.3 × external` for matching dates.

**Fallback:** if statsforecast raises for a SKU (e.g. fewer than 60 days of history), use the trailing same-weekday mean of the last 4 weeks, `model="weekday_mean"`, `wape=0.5`.

`month_labels(today) -> list[str]`: the 12 three-letter month names for the back-cast blocks, oldest first.

## 2. `conformal.py`

```python
def conformal_quantile(abs_residuals: np.ndarray, alpha: float = 0.1) -> float:
    n = len(abs_residuals)
    if n == 0:
        return float("inf")
    level = min(1.0, np.ceil((n + 1) * (1 - alpha)) / n)
    return float(np.quantile(abs_residuals, level, method="higher"))

def band(daily: np.ndarray, q: float) -> tuple[np.ndarray, np.ndarray]:
    return np.clip(daily - q, 0, None), daily + q
```

## 3. `montecarlo.py`

```python
@dataclass
class McResult:
    stockout_prob: float       # 0..1
    expected_lost_units: float
    lost_frac: np.ndarray      # per path, share of lead-time demand unmet (0..1)
    ltd: np.ndarray            # per path, demand during lead time
    ending: np.ndarray         # per path, stock when the order would arrive (can be < 0)
    sigma_daily: float


def simulate(on_hand: float, receipts: list[tuple[int, float]], mu: np.ndarray, k: float,
             lead_samples: np.ndarray, n_paths: int, seed: int,
             demand_mult: float = 1.0, lead_mult: float = 1.0) -> McResult:
    rng = np.random.default_rng(seed)
    H = len(mu)
    m = np.maximum(np.asarray(mu, dtype=float) * demand_mult, 1e-9)
    demand = rng.negative_binomial(k, k / (k + m), size=(n_paths, H))          # mean m, var m + m²/k
    lead = np.clip(np.ceil(rng.choice(lead_samples, size=n_paths) * lead_mult), 1, H).astype(int)
    inflow = np.zeros(H)
    for day, qty in receipts:                                                   # day 0 = today
        if 0 <= day < H:
            inflow[day] += qty
    level = on_hand + np.cumsum(inflow)[None, :] - np.cumsum(demand, axis=1)    # end-of-day stock
    window = np.arange(H)[None, :] < lead[:, None]                              # days before arrival
    stockout = np.where(window, level, np.inf).min(axis=1) < 0
    ltd = (demand * window).sum(axis=1)
    avail = on_hand + (inflow[None, :] * window).sum(axis=1)
    lost = np.maximum(ltd - avail, 0)
    return McResult(
        stockout_prob=float(stockout.mean()),
        expected_lost_units=float(lost.mean()),
        lost_frac=lost / np.maximum(ltd, 1),
        ltd=ltd, ending=avail - ltd,
        sigma_daily=float(demand[:, : min(H, 7)].std()),
    )
```

Rules for callers:

- `mu` is the 30-day forecast; `lead_samples` is the supplier's historical `actual_days` for that SKU (fall back to all of that supplier's deliveries, then to `[lead]`).
- **Seed** = `zlib.crc32(f"{run_seed}:{sku_id}".encode())`. Baseline and what-if runs of the same SKU use the same seed so their difference is caused by the scenario, not by sampling noise.
- `histogram(lost_frac) -> list[dict]`: 20 bins of width 5 %, `{"range": "0–5%", "count": int, "highlight": lower_edge >= 60}` (en dash, as the UI data uses). Counts are scaled to a maximum of 60 to match the chart's visual range.
- `p95_stock = max(0, round(np.quantile(ending, 0.05)))` — the stock level that is exceeded 95 % of the time at arrival (the UI field is named `p95Stock`).

## 4. `policy.py`

```python
def service_level(unit_margin, unit_cogs, holding_pct_month, shelf_life_days, review_days, goodwill) -> float
```
Newsvendor critical ratio `CR = Cu / (Cu + Co)` with underage cost `Cu = unit_margin × (1 + goodwill)` and overage cost `Co = unit_cogs × holding_pct_month × review_days / 30 + unit_cogs × review_days / max(shelf_life_days, review_days)`. Clip to `[0.80, 0.995]`.

```python
def reorder_point(ltd: np.ndarray, sl: float) -> tuple[int, int]     # (safety_stock, reorder_point)
```
`rop = quantile(ltd, sl)`, `ss = rop − mean(ltd)`; both rounded up, `ss ≥ 0`. Callers then multiply `ss` by the strategy's `safetyStockMultiplier` and recompute `rop = mean(ltd) + ss`.

```python
def order_quantity(mu, k, lead_days, review_days, sl, inventory_position, moq, shelf_life_days, capacity, seed) -> int
```
Order-up-to level `S` = `sl`-quantile of simulated demand over `ceil(lead_days) + review_days` (reuse the negative-binomial draw, 2000 paths). `qty = max(0, S − inventory_position)`, where inventory position = on-hand + open inbound. Then: cap at `mean(mu) × shelf_life_days × 0.5` (never buy more than half a shelf life), cap at `capacity`, round **up** to a multiple of `moq`; return 0 if below one `moq`.

`days_of_cover(on_hand, velocity) = on_hand / max(velocity, 0.01)`.

## 5. `par.py`

- Shortage side: `par_shortage = expected_lost_units × unit_margin × (1 + goodwill)`.
- Overstock side: `excess = max(0, on_hand − velocity × overstock_cover_days)`; `months_to_clear = excess / max(velocity × 30, 1)`; `expiring = max(0, on_hand − velocity × shelf_life_remaining_days)`; `par_overstock = excess × cogs × holding_pct_month × min(months_to_clear, 12) / 2 + expiring × cogs`. (The `/2` is the average inventory held while it sells down.)
- `profit_at_risk = round(max(par_shortage, par_overstock))`.
- `carry_roi = margin_fraction − holding_pct_month × days_of_cover / 30` (margin left after carrying the current cover).

## 6. `zones.py`

```python
def classify(m: dict, cfg) -> str
```
`m` has `velocity, velocity_median, doc, lead_eff, stockout_prob, carry_roi, velocity_trend`. Rules, first match wins:

1. `doc < lead_eff` **or** `stockout_prob ≥ 0.5` → `chaos`.
2. `doc > cfg.overstock_cover_days` and `carry_roi < 0` → `money`.
3. `doc > cfg.overstock_cover_days` → `ghost`.
4. otherwise → `sweet`.

`lead_eff = mean(lead_samples) × lead_modifier × strategy.leadTimeBuffer`.

```python
def with_hysteresis(new_zone: str, history: list[str]) -> str
```
A SKU leaves its previous zone only if `new_zone` differs from `history[-1]` **and** equals the raw classification of the previous day as well (two consecutive days), except moves **into** `chaos`, which apply immediately (risk must not be delayed). This stops SKUs on a threshold from flapping in the drift time-lapse.

## 7. `basket.py`, `cannibal.py`, `bullwhip.py`

**`basket.mine_rules(baskets: pd.DataFrame, names: dict, min_support=0.05, min_confidence=0.4, max_len=2) -> list[dict]`** — `baskets` has `basket_id, sku_id` for the last 90 days. Build a boolean basket×SKU matrix; for every antecedent set of size 1..`max_len` and consequent not in it compute `support = P(A ∪ c)`, `confidence = support / P(A)`, `lift = confidence / P(c)`. Keep rules with `lift > 1.1`, sort by confidence, return the top 8 as `{antecedent: [names], consequent: name, confidence, lift, support}` rounded to 2 decimals. `aisle_pairs(rules, sku→aisle)` aggregates rule confidence to aisle pairs for the store chart (`{from, to, strength}` with strength 0–100) and to each aisle's `connections`; aisle `heat` = that aisle's share of baskets, scaled so the busiest aisle is 0.92.

**`cannibal.detect(sales_daily, stock_daily, categories) -> list[dict]`** — within each category, for each ordered SKU pair compute the Pearson correlation of 7-day-smoothed, detrended (minus 28-day mean) demand over the last 90 days. Report pairs with correlation ≤ −0.4 where, in addition, the "rising" SKU's mean sales on days the other was out of stock exceeds its mean on other days by ≥ 15 % (substitution evidence, when such days exist). Output `{rising, falling, rName, fName, category, correlation}`.

**`bullwhip.series(sales_weekly, alpha=0.3) -> dict`** — last 12 weeks of total demand as `raw`; `smoothed` = simple exponential smoothing; `reorder[i] = smoothed[i]` on weeks where the smoothed order-up-to level crossed the previous reorder by > 8 %, else `None`; `ratio = var(naive orders) / var(raw)` and `ratio_smoothed = var(smoothed) / var(raw)` where naive orders = `raw[t] + (raw[t] − raw[t−1])`. Labels `W1…W12`.

## 8. `topsis.py`

```python
CRITERIA = {            # name: (direction, ideal, anti_ideal)
    "price_ratio": ("cost", 0.90, 1.15),      # unit price / median price for the SKU
    "tat_mean":    ("cost", 1.0, 7.0),        # days
    "tat_cv":      ("cost", 0.05, 0.50),      # std / mean of actual days
    "on_time":     ("benefit", 1.00, 0.60),   # Beta posterior mean of on-time deliveries
    "fill_rate":   ("benefit", 1.00, 0.75),
    "defect_rate": ("cost", 0.0, 0.08),
    "esg":         ("benefit", 100.0, 40.0),
    "incentive":   ("benefit", 0.05, 0.0),    # discount fraction earned at the order qty
}
WEIGHTS = {
    "Balanced":  dict(price_ratio=.20, tat_mean=.15, tat_cv=.15, on_time=.20, fill_rate=.10, defect_rate=.08, esg=.07, incentive=.05),
    "Cash Flow": dict(price_ratio=.35, tat_mean=.08, tat_cv=.10, on_time=.15, fill_rate=.07, defect_rate=.05, esg=.05, incentive=.15),
    "Growth":    dict(price_ratio=.10, tat_mean=.25, tat_cv=.20, on_time=.25, fill_rate=.10, defect_rate=.05, esg=.03, incentive=.02),
}
```

`score(rows: list[dict], mode: str, urgency: float = 0.0) -> list[float]`:

1. Normalise each criterion to `u = clip((x − anti) / (ideal − anti), 0, 1)` (this handles cost and benefit directions uniformly because ideal/anti are given explicitly).
2. `urgency ∈ [0, 1]` (1 = stockout imminent) shifts weight: multiply `tat_mean` and `tat_cv` weights by `1 + urgency`, renormalise.
3. `d_plus = sqrt(Σ w (1 − u)²)`, `d_minus = sqrt(Σ w u²)`, `score = 100 × d_minus / (d_plus + d_minus)`.

`on_time` uses a Beta(1, 1) prior: `(1 + on_time_count) / (2 + deliveries)`, where on-time means `actual_days ≤ expected_days × 1.1`. Suppliers whose `capacity_limit` is below the order quantity are scored but flagged `feasible=False`. Tier labels: `Gold` ≥ 85, `Silver` ≥ 70, else `Bronze`.

## 9. `transfer.py` and `budget.py`

```python
def plan_transfers(surplus: dict[str, float], deficit: dict[str, float],
                   cost: dict[tuple[str, str], float], value: dict[str, float]) -> list[dict]
```
Variables `x[i,j] ≥ 0` (units from surplus site `i` to deficit site `j`). Minimise `Σ (cost[i,j] − value[j]) x[i,j]` subject to `Σ_j x[i,j] ≤ surplus[i]` and `Σ_i x[i,j] ≤ deficit[j]`, solved with `scipy.optimize.linprog(c, A_ub=A, b_ub=b, bounds=(0, None), method="highs")`. `value[j]` is the per-unit benefit of filling the deficit (the SKU's landed purchase cost avoided). Because the objective is negative only where a move pays for itself, unprofitable moves stay at zero. Round down to whole units; drop moves under 10 units. `cost[i,j] = 2.0 + 0.004 × haversine_km(i, j)` rupees per unit. Per-site `surplus = max(0, on_hand − 1.5 × site_safety_stock)`, `deficit = max(0, site_safety_stock − on_hand)`, with `site_safety_stock` = the SKU's safety stock split across sites in proportion to capacity.

```python
def fund_orders(values: np.ndarray, costs: np.ndarray, budget: float) -> np.ndarray   # 0/1 per order
```
```python
from scipy.optimize import milp, LinearConstraint, Bounds
res = milp(c=-values, constraints=LinearConstraint(costs.reshape(1, -1), ub=[budget]),
           integrality=np.ones(len(values)), bounds=Bounds(0, 1))
chosen = np.round(res.x).astype(int) if res.success else greedy_by_ratio(values, costs, budget)
```
`values` = Profit-at-Risk rescued by each order, `costs` = order value. Empty input returns an empty array.

## 10. `esg.py` and `anomaly.py`

`esg.options(qty, unit_weight_kg, distance_km, base_freight) -> list[dict]` returns three modes in the UI's fixed order **air, sea, multimodal**:

| Mode | Days | Emission factor (kg CO₂ per tonne-km) | Freight cost factor |
|---|---|---|---|
| air | 1 | 0.60 | 3.0 |
| sea (coastal) | 7 | 0.016 | 1.0 |
| multimodal (rail + road) | 3 | 0.045 | 1.7 |

`co2_kg = qty × unit_weight_kg / 1000 × distance_km × factor`. Factors are planning-grade defaults kept in one dict so they can be replaced. Each option also gets `stockout_prob` from `simulate` with `lead_samples=[days]`. The recommended index maximises `− freight_cost − carbon_price × co2_kg − par_shortage(mode)`, `carbon_price` = ₹2/kg (Balanced), 0.5 (Cash Flow), 1 (Growth). `unit_weight_kg` is parsed from the SKU name (`500g`, `1kg`, `5L`, `250ml`), default 0.5.

`anomaly.phantom_inventory(sales_daily, stock_daily) -> list[dict]` flags a SKU when, in the last 14 days, either (a) units were sold on ≥ 2 days where the ledger said `on_hand == 0`, or (b) `on_hand > 0` for 5 straight days with zero sales while the forecast expected ≥ 5 units/day (stock is recorded but not on the shelf). Output `{sku_id, kind, days, est_units}`.

## Verify

`uv run pytest tests/analytics` with these tests:

- **montecarlo**: `stockout_prob` is non-decreasing in `lead_mult` and non-increasing in `on_hand` (same seed); zero demand → probability 0; identical seeds → identical results; mean of simulated daily demand within 3 % of `mu`.
- **policy**: higher margin → higher service level; `order_quantity` is a multiple of `moq`, is 0 when inventory position exceeds `S`, and never exceeds the shelf-life cap.
- **conformal**: on 2000 synthetic residuals, the band covers ≥ 89 % of fresh draws at `alpha=0.1`.
- **zones**: four hand-built metric dicts map to the four zones; hysteresis keeps a SKU in place for a one-day blip but moves it into chaos immediately.
- **basket**: on a 6-basket toy set, support/confidence/lift equal hand-computed values.
- **topsis**: a supplier at all ideal points scores 100, at all anti-ideal points 0; adding a third supplier does not change the order of the first two.
- **transfer**: shipped units never exceed surplus or deficit; no move when `cost > value`.
- **budget**: result respects the budget; matches brute force on 8 random orders.
- **forecast**: on the seeded database every SKU gets a non-negative 30-day forecast, a model name, and `wape < 0.6`.

## Done when

All analytics tests pass and `forecast_all` on the seeded data finishes in under 60 seconds.
