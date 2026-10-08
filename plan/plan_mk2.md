# MK2 — Database and seed data

**Goal:** a SQLite database with every table the system needs, filled with 18 months (540 days) of realistic history for the exact catalogue the UI shows today.

## 1. Engine — `src/sarthi/db.py`

```python
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from sarthi.config import get_settings

_engine = None


def get_engine():
    global _engine
    if _engine is None:
        s = get_settings()
        s.db_file.parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(f"sqlite:///{s.db_file}", connect_args={"check_same_thread": False})

        @event.listens_for(_engine, "connect")
        def _pragmas(conn, _):
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return _engine


def init_db():
    import sarthi.models  # noqa: F401  (registers tables)
    SQLModel.metadata.create_all(get_engine())


def session() -> Session:
    return Session(get_engine())
```

Call `init_db()` in the FastAPI `lifespan` (before `yield`).

## 2. Tables — `src/sarthi/models.py`

All are `SQLModel, table=True`. JSON fields use `Field(default_factory=dict, sa_column=Column(JSON))` (import `Column, JSON` from `sqlalchemy`). Dates are `datetime.date`, timestamps are timezone-naive UTC `datetime`.

**Master data**

| Table | Columns |
|---|---|
| `Sku` | `id` PK (e.g. `SKU001`), `name`, `category`, `cogs` float, `price` float, `shelf_life_days` int, `holding_cost_pct` float (**share of COGS per month**), `moq` int, `aisle_id` str |
| `Location` | `id` PK, `name`, `city`, `kind` (`store` \| `warehouse`), `lat`, `lon`, `capacity` int |
| `Supplier` | `id` PK, `name`, `tier`, `city`, `lat`, `lon`, `email`, `capacity_limit` int, `defect_rate` float, `esg_score` float (0–100), `incentive_text`, `incentive_min_qty` int, `incentive_pct` float, `max_discount_pct` float |
| `SkuSupplier` | PK (`sku_id`, `supplier_id`), `unit_price` float, `is_primary` bool |
| `Aisle` | `id` PK, `label`, `x` int, `y` int, `items` JSON list |

**History**

| Table | Columns |
|---|---|
| `Sale` | `id` PK auto, `day` date (indexed), `sku_id` (indexed), `location_id`, `qty` int, `price` float, `basket_id` str (indexed) |
| `StockDaily` | PK (`day`, `sku_id`, `location_id`), `on_hand` int |
| `Inbound` | `id` PK auto, `sku_id`, `location_id`, `supplier_id`, `qty` int, `ordered_on` date, `expected_on` date, `received_on` date nullable |
| `Delivery` | `id` PK auto, `supplier_id` (indexed), `sku_id`, `ordered_on` date, `expected_days` float, `actual_days` float, `qty_ordered` int, `qty_received` int, `mode` (`air` \| `sea` \| `multimodal`) |
| `PriceHistory` | PK (`sku_id`, `month` str `YYYY-MM`), `unit_price` float |

**Signals and runs**

| Table | Columns |
|---|---|
| `RiskSignal` | `id` PK auto, `type` (`WEATHER` \| `LOGISTICS` \| `COMMODITY` \| `TRANSPORT`), `severity` (`HIGH` \| `MEDIUM` \| `LOW`), `region_key`, `msg`, `msg_key` nullable, `icon`, `lead_modifier` float, `demand_multiplier` float, `categories` JSON list, `skus_at_risk` int, `source`, `active` bool, `created_at` |
| `Run` | `id` PK auto, `trigger`, `dry_run` bool, `scenario` JSON, `strategy` JSON, `status` (`running` \| `done` \| `failed`), `started_at`, `finished_at` nullable |
| `SkuSnapshot` | PK (`run_id`, `as_of` date, `sku_id`), `zone`, `metrics` JSON |
| `Event` | `id` PK auto, `run_id` (indexed), `ts`, `phase`, `agent`, `kind`, `key` nullable, `value` JSON, `text` nullable, `sku_id` nullable |

**Decisions and actions**

| Table | Columns |
|---|---|
| `Alert` | `id` PK auto, `run_id`, `sku_id`, `sku_label`, `zone`, `type`, `risk` int, `confidence` int, `impact_value` float, `msg`, `action`, `impact`, `payload` JSON, `routed` (`auto` \| `review`), `status` (`open` \| `approved` \| `dismissed`), `feedback` nullable, `txid` nullable, `created_at`, `decided_at` nullable |
| `PurchaseOrder` | `id` PK str (`PO-…`), `sku_id`, `supplier_id`, `qty`, `unit_price`, `mode`, `status` (`draft` \| `confirmed`), `source` (`user` \| `alert` \| `auto`), `created_at` |
| `TransferOrder` | `id` PK str (`TRF-…`), `sku_id`, `from_location`, `to_location`, `qty`, `status`, `created_at` |
| `Campaign` | `id` PK auto, `type` (`markdown` \| `bundle` \| `flash`), `target_zone`, `sku_ids` JSON, `discount_pct`, `est_impact_value`, `status` (`proposed` \| `live`), `created_at` |
| `Negotiation` | `id` PK auto, `po_id`, `supplier_id`, `rounds` JSON list, `agreed_price` nullable, `status` |
| `OutboxEmail` | `id` PK auto, `to_addr`, `subject`, `body`, `ref`, `status` (`draft` \| `queued`), `created_at` |

**Policy and learning**

| Table | Columns |
|---|---|
| `StrategyPolicy` | `id` PK auto, `mode`, `params` JSON, `source_text`, `expires_on` date nullable, `active` bool, `created_at` |
| `BanditArm` | `key` PK str, `alpha` float, `beta` float |
| `Preference` | `id` PK auto, `scope`, `target`, `directive`, `value` nullable, `note`, `created_at`, `active` bool |
| `Upload` | `id` PK auto, `type`, `filename`, `records` int, `status`, `errors` JSON, `created_at` |
| `LlmCache` | `key` PK str (sha256), `response` str, `created_at` |

## 3. Catalogue — `src/sarthi/seed/catalog.py`

Plain Python constants copied from `src/data/appData.js` and `src/pages/Replenish.jsx` so the UI looks the same on first load.

- `SKUS`: the 10 rows of `skuData` with `id, name, cat, stock, vel, margin, lead, age, zone, cogs, shelfLife, holdingCostPct, sales[12]`. Derive `price = round(cogs / (1 - margin/100), 2)`. `moq`: 12 for `cogs < 50`, else 6.
- `SUPPLIERS`: the 3 `distributors` (`SUP-REL`, `SUP-HUL`, `SUP-MCC`) with `tier`, `price`, `incentive`, `fulfillment`, `defectRate`, `capacityLimit`, `avgTAT`. Add `city` (Mumbai, Pune, Delhi), `esg_score` (82, 74, 61), `max_discount_pct` (4, 5, 7), parsed `incentive_min_qty`/`incentive_pct` (500/2, 300/1.5, 1000/3).
- `LOCATIONS`: `STORE-01` (kind `store`, Mumbai — the store whose stock the SKU screens show) plus the 4 warehouses `WH-MUM`, `WH-DEL`, `WH-BLR`, `WH-CHN` with the capacities and `SKU002/SKU004/SKU007` quantities from `Replenish.jsx`. Coordinates: Mumbai 19.08/72.88, Delhi 28.61/77.21, Bengaluru 12.97/77.59, Chennai 13.08/80.27.
- `AISLES`: the 11 `aisles` rows; `AISLE_OF_CATEGORY` maps each SKU category to an aisle id (Dairy→A, Biscuits→B, Snacks→C, Instant Food→E, Staples→F, Personal Care→G, Detergent→H, Edible Oil→I).
- `REGIONS`: `region_key → {x, y, lat, lon}` for the map, including the four the UI draws (`bayOfBengal` 75/70, `jnptMumbai` 18/65, `delhiNcr` 35/25, `keralaCoast` 32/92).
- `BASKET_AFFINITY`: the 8 `mbaRules` as (antecedents, consequent, confidence) used to generate baskets.
- `ZONE_TARGET`: `sku_id → zone` from the mock, used only by the golden test.

## 4. Generator — `src/sarthi/seed/generator.py`

`seed_database(seed: int, today: date | None = None)` wipes and refills all tables. Use `rng = np.random.default_rng(seed)`. History covers `today-539 … today`.

**Demand per SKU per day**

1. Monthly shape: take the mock `sales[12]` array, scale it so its last element equals `vel`, prepend six months equal to the first value, and linearly interpolate to a daily mean `mu_t`.
2. Weekly seasonality: multiply by `[0.92, 0.90, 0.95, 1.00, 1.08, 1.18, 1.12]` (Mon…Sun), renormalised to mean 1.
3. Draw demand from a negative binomial with dispersion `k = 8` for sweet/chaos SKUs and `k = 3` for ghost/money SKUs: `rng.negative_binomial(k, k / (k + mu_t))`.
4. Cannibalization: for the two `cannibalization` pairs, on days when the "falling" SKU is out of stock add `0.6 × its mean demand` to the "rising" SKU.

**Inventory ledger** (`STORE-01`)

Simulate day by day with a simple legacy policy (order `vel × lead × 2` when on-hand < `vel × lead`), drawing each lead time from the supplier model below. Sales = `min(demand, on_hand)`. Record `StockDaily`, `Inbound`, `Delivery`. After the simulation, **pin the final day** to the UI's picture:

- Set `StockDaily.on_hand` on `today` to the mock `stock`.
- Sweet SKUs (`SKU001, 005, 008, 009`) get one open `Inbound` of `vel × (lead + 7)` units expected on `today + 1`, so they are genuinely healthy.
- Chaos SKUs get no open inbound.
- The stock "age" for ghost/money SKUs: last `received_on` = `today − age`.

**Supplier lead times** — `actual_days = expected_days × lognormal(mean=0, sigma)`, with `sigma` 0.10 (Gold), 0.22 (Silver), 0.38 (Bronze). Primary supplier: chaos SKUs → `SUP-MCC` (Bronze, volatile), others → `SUP-REL`. `expected_days` = mock `lead`. `qty_received = round(qty × (1 − defect))` with a 1-in-(1/(1−fulfillment)) chance of a short shipment at 80 %. Generate at least 40 deliveries per supplier so reliability statistics are stable. Shipment `mode`: 70 % multimodal, 20 % air, 10 % sea.

**Baskets** — for each store sale unit assign a `basket_id`. Build baskets per day: draw a basket's first item in proportion to demand; for each `BASKET_AFFINITY` rule whose antecedents are in the basket add the consequent with probability `confidence` (if units remain that day); otherwise close the basket. This makes the mined rules recover roughly the mock's confidences.

**Warehouses** — write only the final-day `StockDaily` rows for the 4 warehouses from the catalogue.

**Other tables** — `PriceHistory`: last 6 months at `cogs × [0.92, 0.95, 0.97, 1.02, 1.00, 0.98]`. `SkuSupplier`: every SKU × every supplier at `cogs × (supplier.price / 18.50)`. `StrategyPolicy`: one active row, mode `Balanced`, params `{savingsPriority: 0.5, safetyStockMultiplier: 1.0, leadTimeBuffer: 1.2}`. `BanditArm`: none (created on demand with prior 2/2).

**Feeds** — write `backend/data/feeds/logistics.json`, `commodity.json`, `transport.json`, each a list of `{region_key, severity, msg, msg_key, lead_modifier, demand_multiplier, categories, active}`. Seed them with the three non-weather signals the dashboard shows (`riskMsgLogistics` at `jnptMumbai`, `riskMsgCommodity` for Edible Oil, `riskMsgTransport` at `keralaCoast`).

## 5. CLI

Add to `cli.py`:

```python
@app.command()
def seed(reset: bool = True):
    """Create the database and fill it with demo history."""
    from sarthi.config import get_settings
    from sarthi.db import init_db
    from sarthi.seed.generator import seed_database
    init_db()
    seed_database(get_settings().seed)
    typer.echo("seeded")
```

## Verify

```powershell
uv run sarthi seed
uv run python -c "from sarthi.db import session; from sarthi.models import *; from sqlmodel import select, func; s=session(); print(s.exec(select(func.count()).select_from(Sku)).one(), s.exec(select(func.count()).select_from(Sale)).one(), s.exec(select(func.count()).select_from(Delivery)).one())"
```

Expected: `10`, a sale-row count in the hundreds of thousands, and ≥ 120 deliveries.

`tests/test_seed.py`:

- 10 SKUs, 3 suppliers, 5 locations, 11 aisles.
- For every SKU, `StockDaily` on `today` at `STORE-01` equals the mock `stock`.
- Mean daily sales over the last 14 days is within ±25 % of the mock `vel` for SKUs that were in stock.
- Seeding twice with the same seed yields identical `Sale` totals (determinism).

## Done when

`uv run pytest tests/test_seed.py` passes and `data/sarthi.db` exists.

## Implementation notes (as built, 2026-10-08)

Deviations from the text above, made while building and verified by `tests/test_seed.py`:

- **Timestamps are timezone-aware UTC**, not naive. The installed SQLModel (0.0.48) rejects naive datetimes. `models.utcnow()` returns `datetime.now(UTC)`; `models.local_today()` gives the local business date.
- **Legacy reorder rule** is "reorder below 1.5 lead-times of demand, order 2 lead-times" (not "below 1 lead-time"). The stricter rule stocked out on about half of all cycles for every SKU, which would have corrupted the history of healthy SKUs.
- **Supplier lead time** for a SKU scales with the supplier's speed: `sku.lead x supplier.avgTAT / primary.avgTAT`. The mock's lead time therefore holds for the primary supplier, and switching a chaos SKU from the Bronze to the Gold supplier is genuinely faster. 70 % of legacy orders go to the primary; the rest to a non-Bronze alternative, so only chaos SKUs ever buy from the Bronze supplier.
- **Pin window.** The last 14 days (or `age` days for ghost/money SKUs) have no new orders and start at the stock level that makes today's closing stock equal the mock's figure exactly. Orders that would have landed inside the window are dropped. A positive difference at the window start is booked as a delivery; a negative one is an unrecorded write-down.
- **`Aisle`** also stores `heat`, `connections`, `zone` (the mock's values) as fallbacks for the store screen.
- **Feeds** are written to `<database folder>/feeds`, exposed as `settings.feeds_dir`, so tests do not write into the real data folder.
- **Velocity test tolerance** is "within 25 % or within 3 standard errors". With 14 days of negative-binomial demand, slow movers legitimately vary by more than 25 %.
- **Row counts** with the default seed: 167,171 sales, 501 deliveries (305 Gold, 119 Silver, 77 Bronze), 5,412 stock rows. Seeding takes about 4 seconds.
- `sarthi seed` takes no `--reset` flag; it always wipes and reloads.
