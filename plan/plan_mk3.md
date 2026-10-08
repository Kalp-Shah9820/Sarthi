# MK3 — Data Hub ingestion

**Goal:** the 8 upload cards on the Data Hub page accept real files, validate them, and write to the database. "Sync Intelligence" then runs the pipeline (wired in mk8/mk9).

## 1. Upload types

The card ids come from `uploadTypes` in `src/pages/DataHub.jsx`. The file picker accepts `.csv, .xlsx, .xls, .json, .xml`.

| Card id | Target tables | Required columns | Optional columns |
|---|---|---|---|
| `sku` | `Sku` | `sku_id, name, category, unit_cost` | `price, lead_time, shelf_life_days, holding_cost_pct, moq` |
| `sales` | `Sale` | `date, sku_id, qty` | `transaction_id, price, location_id` |
| `stock` | `StockDaily`, `Inbound` | `sku_id, warehouse_id, current_stock` | `transit, date` |
| `vendor` | `Supplier`, `SkuSupplier` | `supplier_id, name, tier` | `lead_time, quality_score, city, email, sku_id, unit_price, capacity_limit` |
| `forecast` | kept as `Upload` side-data | `sku_id, forecast_date, predicted_demand` | — |
| `risk` | `RiskSignal` | `type, severity` | `signal_id, region, message, lead_modifier, demand_multiplier, skus_at_risk` |
| `locations` | `Location` | `location_id, city` | `name, lat, long, capacity, kind` |
| `esg` | `Supplier.esg_score` | `supplier_id, carbon_kg` | `water_l, waste_kg` |

## 2. Reading files — `src/sarthi/ingest/loader.py`

```python
def read_table(filename: str, data: bytes) -> pd.DataFrame
```

- Dispatch on the lower-cased extension: `.csv` → `pd.read_csv(io.BytesIO(data))`; `.xlsx` → `pd.read_excel(..., engine="openpyxl")`; `.xls` → `pd.read_excel(..., engine="xlrd")`; `.json` → `pd.json_normalize(json.loads(data))` (accept a top-level list, or a dict with one list value, or GeoJSON `features` flattened from `properties` plus `geometry.coordinates` → `long, lat`); `.xml` → `pd.read_xml(io.BytesIO(data))`.
- Any other extension, a file over 20 MB, or more than 2,000,000 rows raises `IngestError` with a readable message.
- Normalise headers: strip, lower-case, replace spaces and hyphens with `_`. Then apply an alias map so the names printed on the cards work, e.g. `unit_cost|cogs|cost → unit_cost`, `qty|quantity|quantity_sold|units → qty`, `current_stock|stock|on_hand → current_stock`, `long|lng|longitude → long`, `lead_time|lead_time_days → lead_time`, `warehouse_id|location_id|warehouse → warehouse_id` (for `stock`), `sku|sku_id → sku_id`.

## 3. Validation — `src/sarthi/ingest/schemas.py`

One function per type, `validate_<type>(df) -> tuple[pd.DataFrame, list[str]]`, returning the cleaned frame and a list of row-level problems (capped at 50 messages). Rules:

- Missing required column → hard failure (`IngestError`) naming the column.
- Coerce types with `pd.to_numeric(errors="coerce")` and `pd.to_datetime(errors="coerce")`; rows that fail are dropped and counted.
- Range checks: `qty ≥ 0`, `unit_cost > 0`, `current_stock ≥ 0`, `−90 ≤ lat ≤ 90`, `−180 ≤ long ≤ 180`, `severity ∈ {HIGH, MEDIUM, LOW}` (upper-cased), `type ∈ {WEATHER, LOGISTICS, COMMODITY, TRANSPORT}`.
- Referential checks: `sales`, `stock`, `forecast` rows whose `sku_id` is not in `Sku` are dropped and counted (so `sku` should be uploaded first; the message says so).
- Duplicates: drop exact duplicate rows; for `sku`, `vendor`, `locations` keep the last row per primary key.

## 4. Writing — `ingest/loader.py`

```python
def ingest(upload_type: str, filename: str, data: bytes) -> dict
# returns {"records": int, "dropped": int, "errors": [...], "time": "HH:MM:SS"}
```

Per type, inside one transaction:

- `sku`: upsert. `price` defaults to `unit_cost × 1.25`; `holding_cost_pct` to 0.03; `shelf_life_days` to 365; `moq` to 6; `aisle_id` from `AISLE_OF_CATEGORY` (unknown category → `F`).
- `sales`: append. `location_id` defaults to `STORE-01`; `basket_id` = `transaction_id` if present, else `"{date}-{row}"`. Skip rows whose (`transaction_id`, `sku_id`) already exist to make re-uploads idempotent.
- `stock`: upsert `StockDaily` for `date` (default today). A positive `transit` creates an `Inbound` expected in `lead_time` days (default 3) from the SKU's primary supplier.
- `vendor`: upsert `Supplier`; if `sku_id` and `unit_price` are present upsert `SkuSupplier`. `quality_score` (0–100) sets `defect_rate = (100 − quality_score) / 1000`.
- `forecast`: write the cleaned rows to `data/external_forecast.csv`, replacing any previous file. Demand Intel (mk6) blends it at 30 % weight when present.
- `risk`: insert `RiskSignal` rows with `source="upload"`, `active=True`; `region` is matched to `REGIONS` keys case-insensitively, otherwise stored as given.
- `locations`: upsert `Location` (`kind` default `warehouse`).
- `esg`: per supplier, `esg_score = clip(100 − 100 × carbon_kg / max_carbon_kg_in_file × 0.6, 0, 100)`, i.e. the highest emitter in the file scores 40 and a zero-emitter scores 100.

Always insert an `Upload` row (`status` `done` or `failed`, with errors).

## 5. Data-health metrics

`ingest/loader.py::data_health() -> dict` feeds the four tiles at the top of the page:

| Tile (i18n key) | Definition |
|---|---|
| `totalIngested` | total rows across `Sale`, `StockDaily`, `Delivery`, `Sku`, `Supplier`, `Location`, formatted with thousands separators |
| `freshness` | share of SKUs that have a `Sale` or `StockDaily` row within the last 2 days, as `"98.2%"` |
| `joinQuality` | share of `Sale` rows whose `sku_id` and `location_id` both resolve, as a percentage |
| `alertsGenerated` | count of `Alert` rows with `status="open"` in the latest run |

## 6. Sample files — `src/sarthi/seed/samples.py`

`export_samples(out_dir)` writes one valid file per card from the current database, in the format each card advertises: `sku.csv`, `sales.csv` (last 90 days), `stock.json`, `vendor.xlsx`, `forecast.csv`, `risk.json`, `locations.csv`, `esg.xlsx`. CLI:

```python
@app.command("export-samples")
def export_samples_cmd():
    from sarthi.seed.samples import export_samples
    from sarthi.config import BACKEND_ROOT
    export_samples(BACKEND_ROOT / "samples")
    typer.echo("samples written")
```

These are the files to pick in the browser during a demo.

## Verify

```powershell
uv run sarthi export-samples
uv run pytest tests/test_ingest.py
```

`tests/test_ingest.py`:

- Each of the 8 sample files ingests with `dropped == 0` and `records > 0`.
- Re-ingesting `sales.csv` adds 0 new rows.
- A CSV missing `sku_id` raises `IngestError` mentioning `sku_id`.
- A `sales` file with one unknown SKU and one negative `qty` reports `dropped == 2` and two error messages.
- A `.txt` file raises `IngestError`.
- Headers `SKU_ID, Unit_Cost, Lead_Time` (as printed on the card) are accepted.

## Done when

All ingest tests pass and `backend/samples/` holds 8 files.
