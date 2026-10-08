"""Reads an uploaded file, validates it, and writes it to the database."""

import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
from sqlalchemy import delete, insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import Session, func, select

from sarthi.config import get_settings
from sarthi.db import get_engine, session
from sarthi.ingest.schemas import UPLOAD_TYPES, VALIDATORS, Context, IngestError, normalise_headers
from sarthi.models import (
    Alert,
    Delivery,
    Inbound,
    Location,
    RiskSignal,
    Run,
    Sale,
    Sku,
    SkuSupplier,
    StockDaily,
    Supplier,
    Upload,
    local_today,
)
from sarthi.seed import catalog as cat

MAX_BYTES = 20 * 1024 * 1024
MAX_ROWS = 2_000_000
CHUNK = 2000
EXTENSIONS = (".csv", ".xlsx", ".xls", ".json", ".xml")
RISK_ICONS = {"WEATHER": "CloudLightning", "LOGISTICS": "Anchor", "COMMODITY": "TrendingUp", "TRANSPORT": "Truck"}
# ESG score from carbon intensity (kg CO2e per tonne delivered): 0 kg -> 100, 2,500 kg or more -> 0.
ESG_KG_PER_POINT = 25.0


def esg_score_from_carbon(carbon_kg: float) -> float:
    return round(min(100.0, max(0.0, 100.0 - carbon_kg / ESG_KG_PER_POINT)), 1)


def _read_json(data: bytes) -> pd.DataFrame:
    payload = json.loads(data)
    if isinstance(payload, dict) and isinstance(payload.get("features"), list):  # GeoJSON
        records = []
        for feature in payload["features"]:
            record = dict(feature.get("properties") or {})
            coords = (feature.get("geometry") or {}).get("coordinates") or []
            if len(coords) >= 2:
                record.setdefault("long", coords[0])
                record.setdefault("lat", coords[1])
            records.append(record)
        return pd.DataFrame(records)
    if isinstance(payload, dict):
        lists = [v for v in payload.values() if isinstance(v, list)]
        payload = lists[0] if len(lists) == 1 else [payload]
    if not isinstance(payload, list):
        raise IngestError("The JSON file must contain a list of records.")
    return pd.json_normalize(payload, max_level=0)


def read_table(filename: str, data: bytes) -> pd.DataFrame:
    """Parse an uploaded file into a frame with the file's own headers."""
    ext = Path(filename).suffix.lower()
    if ext not in EXTENSIONS:
        raise IngestError(f"Unsupported file type '{ext or filename}'. Use one of: {', '.join(EXTENSIONS)}.")
    if len(data) > MAX_BYTES:
        raise IngestError(f"The file is {len(data) / 1e6:.1f} MB; the limit is {MAX_BYTES // (1024 * 1024)} MB.")
    if not data.strip():
        raise IngestError("The file is empty.")
    try:
        if ext == ".csv":
            df = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False, encoding="utf-8-sig")
        elif ext == ".xlsx":
            df = pd.read_excel(io.BytesIO(data), engine="openpyxl", dtype=str)
        elif ext == ".xls":
            df = pd.read_excel(io.BytesIO(data), engine="xlrd", dtype=str)
        elif ext == ".json":
            df = _read_json(data)
        else:
            df = pd.read_xml(io.BytesIO(data))
    except IngestError:
        raise
    except Exception as exc:  # any parser failure becomes one readable message
        raise IngestError(f"Could not read {filename} as {ext[1:].upper()}: {exc}") from exc
    if len(df) > MAX_ROWS:
        raise IngestError(f"The file has {len(df):,} rows; the limit is {MAX_ROWS:,}.")
    if df.empty:
        raise IngestError("The file contains no data rows.")
    return df


def _context(s: Session) -> Context:
    return Context(
        sku_ids=set(s.exec(select(Sku.id)).all()),
        supplier_ids=set(s.exec(select(Supplier.id)).all()),
        location_ids=set(s.exec(select(Location.id)).all()),
    )


def check_file(upload_type: str, filename: str, data: bytes) -> tuple[pd.DataFrame, list[str], int]:
    """Read and validate without writing. Returns (clean frame, problems, rows dropped)."""
    if upload_type not in UPLOAD_TYPES:
        raise IngestError(f"Unknown upload type '{upload_type}'. Expected one of: {', '.join(UPLOAD_TYPES)}.")
    raw = normalise_headers(read_table(filename, data), upload_type)
    with session() as s:
        ctx = _context(s)
    clean, problems = VALIDATORS[upload_type](raw, ctx)
    return clean, problems, len(raw) - len(clean)


def _given(value) -> bool:
    """True when an optional cell actually holds a value."""
    return value is not None and not (isinstance(value, float) and pd.isna(value)) and value != ""


# ── writers (each receives an open session; the caller commits) ──────────────

def _write_sku(s: Session, df: pd.DataFrame) -> int:
    for row in df.to_dict("records"):
        sku = s.get(Sku, row["sku_id"])
        new = sku is None
        if new:
            sku = Sku(id=row["sku_id"], name=row["name"] or row["sku_id"], category=row["category"],
                      cogs=float(row["unit_cost"]), price=round(float(row["unit_cost"]) * 1.25, 2))
        sku.name = row["name"] or sku.name
        sku.category = row["category"] or sku.category
        sku.cogs = float(row["unit_cost"])
        sku.aisle_id = cat.AISLE_OF_CATEGORY.get(sku.category, cat.DEFAULT_AISLE)
        if _given(row["price"]):
            sku.price = float(row["price"])
        elif new:
            sku.price = round(sku.cogs * 1.25, 2)
        if _given(row["lead_time"]):
            sku.lead_time_days = float(row["lead_time"])
        if _given(row["shelf_life_days"]):
            sku.shelf_life_days = int(row["shelf_life_days"])
        if _given(row["holding_cost_pct"]):
            sku.holding_cost_pct = float(row["holding_cost_pct"])
        if _given(row["moq"]):
            sku.moq = max(1, int(row["moq"]))
        s.add(sku)
    return len(df)


def _write_sales(s: Session, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    df = df.copy()
    df["location_id"] = df["location_id"].where(df["location_id"] != "", cat.STORE_ID)
    fallback = df["date"].astype(str) + "-" + (df.index + 1).astype(str)
    df["basket_id"] = df["transaction_id"].where(df["transaction_id"] != "", fallback)
    prices = dict(s.exec(select(Sku.id, Sku.price)).all())
    df["price"] = df["price"].where(df["price"].notna(), df["sku_id"].map(prices))

    existing = set(
        s.exec(select(Sale.basket_id, Sale.sku_id).where(Sale.day >= df["date"].min(), Sale.day <= df["date"].max())).all()
    )
    keys = list(zip(df["basket_id"], df["sku_id"], strict=True))
    fresh = df[[key not in existing for key in keys]].drop_duplicates(subset=["basket_id", "sku_id"])
    rows = [
        {"day": r.date, "sku_id": r.sku_id, "location_id": r.location_id, "qty": int(r.qty),
         "price": float(r.price), "basket_id": r.basket_id}
        for r in fresh.itertuples()
    ]
    for start in range(0, len(rows), CHUNK * 10):
        s.connection().execute(insert(Sale.__table__), rows[start:start + CHUNK * 10])
    return len(rows)


def _primary_supplier(s: Session, sku_id: str) -> str | None:
    link = s.exec(select(SkuSupplier).where(SkuSupplier.sku_id == sku_id).order_by(SkuSupplier.is_primary.desc())).first()
    if link:
        return link.supplier_id
    return s.exec(select(Supplier.id)).first()


def _write_stock(s: Session, df: pd.DataFrame, problems: list[str]) -> int:
    rows = [
        {"day": r.date, "sku_id": r.sku_id, "location_id": r.warehouse_id, "on_hand": int(r.current_stock)}
        for r in df.itertuples()
    ]
    for start in range(0, len(rows), CHUNK):
        stmt = sqlite_insert(StockDaily.__table__).values(rows[start:start + CHUNK])
        s.connection().execute(stmt.on_conflict_do_update(
            index_elements=["day", "sku_id", "location_id"], set_={"on_hand": stmt.excluded.on_hand}
        ))
    # `transit` is the total currently on its way: replace open inbound only when the figure changed.
    for r in df[df["date"] == df["date"].max()].itertuples():
        open_rows = s.exec(
            select(Inbound).where(Inbound.sku_id == r.sku_id, Inbound.location_id == r.warehouse_id,
                                  Inbound.received_on.is_(None))
        ).all()
        if sum(i.qty for i in open_rows) == r.transit:
            continue
        for i in open_rows:
            s.delete(i)
        if r.transit > 0:
            supplier_id = _primary_supplier(s, r.sku_id)
            if supplier_id is None:
                problems.append(f"{r.sku_id}: {r.transit} units in transit ignored because no supplier exists yet")
                continue
            s.add(Inbound(sku_id=r.sku_id, location_id=r.warehouse_id, supplier_id=supplier_id, qty=int(r.transit),
                          ordered_on=r.date, expected_on=r.date + timedelta(days=max(1, round(r.lead_time)))))
    return len(rows)


def _write_vendor(s: Session, df: pd.DataFrame) -> int:
    for row in df.to_dict("records"):
        sup = s.get(Supplier, row["supplier_id"]) or Supplier(id=row["supplier_id"], name=row["name"] or row["supplier_id"])
        sup.name = row["name"] or sup.name
        sup.tier = row["tier"] or sup.tier
        if row.get("city"):
            sup.city = row["city"]
        if row.get("email"):
            sup.email = row["email"]
        if _given(row["lead_time"]):
            sup.avg_tat_days = float(row["lead_time"])
        if _given(row["quality_score"]):
            sup.defect_rate = round((100 - float(row["quality_score"])) / 1000, 4)
        if _given(row["capacity_limit"]):
            sup.capacity_limit = int(row["capacity_limit"])
        s.add(sup)
        if row["sku_id"] and _given(row["unit_price"]):
            link = s.get(SkuSupplier, (row["sku_id"], row["supplier_id"]))
            if link is None:
                has_primary = s.exec(
                    select(SkuSupplier).where(SkuSupplier.sku_id == row["sku_id"], SkuSupplier.is_primary)
                ).first()
                link = SkuSupplier(sku_id=row["sku_id"], supplier_id=row["supplier_id"],
                                   unit_price=float(row["unit_price"]), is_primary=has_primary is None)
            link.unit_price = float(row["unit_price"])
            s.add(link)
        s.flush()
    return len(df)


def _write_forecast(s: Session, df: pd.DataFrame) -> int:
    path = get_settings().external_forecast_file
    path.parent.mkdir(parents=True, exist_ok=True)
    df[["sku_id", "forecast_date", "predicted_demand"]].to_csv(path, index=False)
    return len(df)


def _write_risk(s: Session, df: pd.DataFrame) -> int:
    """The file is the current set of uploaded signals: it replaces earlier uploads, never feeds or weather."""
    s.connection().execute(delete(RiskSignal).where(RiskSignal.source == "upload"))
    regions = {key.lower(): key for key in cat.REGIONS}
    for r in df.itertuples():
        s.add(RiskSignal(
            type=r.type, severity=r.severity, region_key=regions.get(r.region.lower(), r.region), msg=r.message,
            icon=RISK_ICONS[r.type], lead_modifier=float(r.lead_modifier), demand_multiplier=float(r.demand_multiplier),
            skus_at_risk=int(r.skus_at_risk), source="upload", active=True,
        ))
    return len(df)


def _write_locations(s: Session, df: pd.DataFrame) -> int:
    for row in df.to_dict("records"):
        loc = s.get(Location, row["location_id"])
        if loc is None:
            loc = Location(id=row["location_id"], name=row.get("name") or row["location_id"], city=row["city"])
        loc.city = row["city"] or loc.city
        if row.get("name"):
            loc.name = row["name"]
        loc.kind = row["kind"]
        if _given(row["lat"]):
            loc.lat = float(row["lat"])
        if _given(row["long"]):
            loc.lon = float(row["long"])
        if "capacity" in row and row["capacity"] > 0:
            loc.capacity = int(row["capacity"])
        s.add(loc)
    return len(df)


def _write_esg(s: Session, df: pd.DataFrame) -> int:
    for r in df.itertuples():
        sup = s.get(Supplier, r.supplier_id)
        sup.esg_score = esg_score_from_carbon(float(r.carbon_kg))
        s.add(sup)
    return len(df)


def _record_upload(upload_type: str, filename: str, records: int, status: str, errors: list[str]) -> None:
    with session() as s:
        s.add(Upload(type=upload_type, filename=filename, records=records, status=status, errors=errors))
        s.commit()


def ingest(upload_type: str, filename: str, data: bytes) -> dict:
    """Validate and store one uploaded file.

    Returns {"records": valid rows read, "inserted": rows written, "dropped": rows rejected,
             "errors": [...], "time": "HH:MM:SS"}. Raises IngestError when the file cannot be used.
    """
    try:
        clean, problems, dropped = check_file(upload_type, filename, data)
        with Session(get_engine()) as s:
            if upload_type == "sku":
                written = _write_sku(s, clean)
            elif upload_type == "sales":
                written = _write_sales(s, clean)
            elif upload_type == "stock":
                written = _write_stock(s, clean, problems)
            elif upload_type == "vendor":
                written = _write_vendor(s, clean)
            elif upload_type == "forecast":
                written = _write_forecast(s, clean)
            elif upload_type == "risk":
                written = _write_risk(s, clean)
            elif upload_type == "locations":
                written = _write_locations(s, clean)
            else:
                written = _write_esg(s, clean)
            s.commit()  # one transaction per file: any failure above leaves the database untouched
    except IngestError as exc:
        _record_upload(upload_type, filename, 0, "failed", [str(exc)])
        raise
    except Exception as exc:  # database or data errors are reported, not leaked as stack traces
        _record_upload(upload_type, filename, 0, "failed", [f"{type(exc).__name__}: {exc}"])
        raise IngestError(f"Could not store {filename}: {exc}") from exc

    _record_upload(upload_type, filename, len(clean), "done", problems)
    return {
        "records": len(clean), "inserted": written, "dropped": dropped, "errors": problems,
        "time": datetime.now(UTC).astimezone().strftime("%H:%M:%S"),
    }


def data_health() -> dict[str, str]:
    """The four tiles at the top of the Data Hub page."""
    with session() as s:
        def count(model) -> int:
            return s.exec(select(func.count()).select_from(model)).one()

        total = sum(count(m) for m in (Sale, StockDaily, Delivery, Sku, Supplier, Location))
        skus = count(Sku)
        since = local_today() - timedelta(days=2)
        fresh = set(s.exec(select(Sale.sku_id).where(Sale.day >= since).distinct()).all())
        fresh |= set(s.exec(select(StockDaily.sku_id).where(StockDaily.day >= since).distinct()).all())
        sales = count(Sale)
        joined = s.exec(
            select(func.count()).select_from(Sale)
            .where(Sale.sku_id.in_(select(Sku.id)), Sale.location_id.in_(select(Location.id)))
        ).one()
        latest_run = s.exec(select(func.max(Run.id)).where(Run.dry_run.is_(False))).one()
        alerts = 0
        if latest_run is not None:
            alerts = s.exec(
                select(func.count()).select_from(Alert).where(Alert.run_id == latest_run, Alert.status == "open")
            ).one()
    return {
        "totalIngested": f"{total:,}",
        "freshness": f"{100 * len(fresh) / skus:.1f}%" if skus else "0.0%",
        "joinQuality": f"{100 * joined / sales:.1f}%" if sales else "100.0%",
        "alertsGenerated": str(alerts),
    }
