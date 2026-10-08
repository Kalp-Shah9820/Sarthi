"""Writes one example upload file per Data Hub card, built from the current database.

Headers use the capitalised names printed on the cards (SKU_ID, Unit_Cost, ...), so the files also
exercise header normalisation. Uploading them back changes nothing: they describe the data they came from.
"""

import json
from datetime import timedelta
from pathlib import Path

import pandas as pd
from sqlmodel import func, select

from sarthi.db import session
from sarthi.ingest.loader import ESG_KG_PER_POINT
from sarthi.models import Inbound, Location, Sale, Sku, SkuSupplier, StockDaily, Supplier

SALES_DAYS = 90
FORECAST_DAYS = 14

# Two extra signals that do not duplicate the built-in feeds and sit away from the demo's supply routes.
SAMPLE_RISKS = [
    {"Signal_ID": "SIG-1001", "Type": "LOGISTICS", "Severity": "MEDIUM", "Region": "chennaiPort",
     "Message": "Berth delays at Chennai Port are adding about a day to container clearance.",
     "Lead_Modifier": 1.1, "Demand_Multiplier": 1.0, "SKUs_at_Risk": ["SKU002", "SKU004", "SKU007"]},
    {"Signal_ID": "SIG-1002", "Type": "COMMODITY", "Severity": "LOW", "Region": "",
     "Message": "Packaging material prices are easing; no action needed.",
     "Lead_Modifier": 1.0, "Demand_Multiplier": 1.0, "SKUs_at_Risk": []},
]


def export_samples(out_dir: Path) -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with session() as s:
        skus = s.exec(select(Sku).order_by(Sku.id)).all()
        suppliers = s.exec(select(Supplier).order_by(Supplier.id)).all()
        links = s.exec(select(SkuSupplier).order_by(SkuSupplier.supplier_id, SkuSupplier.sku_id)).all()
        locations = s.exec(select(Location).order_by(Location.id)).all()
        last_day = s.exec(select(func.max(Sale.day))).one()
        if not skus or last_day is None:
            raise RuntimeError("The database is empty. Run `uv run sarthi seed` first.")
        sales = s.exec(
            select(Sale).where(Sale.day > last_day - timedelta(days=SALES_DAYS)).order_by(Sale.day, Sale.id)
        ).all()
        stock_day = s.exec(select(func.max(StockDaily.day))).one()
        stock = s.exec(select(StockDaily).where(StockDaily.day == stock_day).order_by(StockDaily.location_id, StockDaily.sku_id)).all()
        transit = {
            (sku_id, loc): qty for sku_id, loc, qty in s.exec(
                select(Inbound.sku_id, Inbound.location_id, func.sum(Inbound.qty))
                .where(Inbound.received_on.is_(None)).group_by(Inbound.sku_id, Inbound.location_id)
            ).all()
        }
        recent = dict(s.exec(
            select(Sale.sku_id, func.sum(Sale.qty)).where(Sale.day > last_day - timedelta(days=14)).group_by(Sale.sku_id)
        ).all())

    files = {
        "sku.csv": pd.DataFrame([
            {"SKU_ID": k.id, "Name": k.name, "Category": k.category, "Unit_Cost": k.cogs, "Price": k.price,
             "Lead_Time": k.lead_time_days, "Shelf_Life_Days": k.shelf_life_days,
             "Holding_Cost_Pct": k.holding_cost_pct, "MOQ": k.moq} for k in skus
        ]),
        "sales.csv": pd.DataFrame([
            {"Transaction_ID": r.basket_id, "Date": r.day.isoformat(), "SKU_ID": r.sku_id, "Qty": r.qty,
             "Price": r.price, "Location_ID": r.location_id} for r in sales
        ]),
        "vendor.xlsx": pd.DataFrame([
            {"Supplier_ID": sup.id, "Name": sup.name, "Tier": sup.tier, "Lead_Time": sup.avg_tat_days,
             "Quality_Score": round(100 - sup.defect_rate * 1000, 1), "City": sup.city, "Email": sup.email,
             "Capacity_Limit": sup.capacity_limit, "SKU_ID": link.sku_id, "Unit_Price": link.unit_price}
            for sup in suppliers for link in links if link.supplier_id == sup.id
        ]),
        "forecast.csv": pd.DataFrame([
            {"SKU_ID": k.id, "Forecast_Date": (last_day + timedelta(days=d)).isoformat(),
             "Predicted_Demand": round(recent.get(k.id, 0) / 14, 1)}
            for k in skus for d in range(1, FORECAST_DAYS + 1)
        ]),
        "locations.csv": pd.DataFrame([
            {"Location_ID": loc.id, "Name": loc.name, "City": loc.city, "Lat": loc.lat, "Long": loc.lon,
             "Capacity": loc.capacity, "Kind": loc.kind} for loc in locations
        ]),
        "esg.xlsx": pd.DataFrame([
            {"Supplier_ID": sup.id, "Carbon_kg": round((100 - sup.esg_score) * ESG_KG_PER_POINT, 1),
             "Water_L": round((100 - sup.esg_score) * 40), "Waste_kg": round((100 - sup.esg_score) * 1.5, 1)}
            for sup in suppliers
        ]),
    }
    written = []
    for name, frame in files.items():
        path = out_dir / name
        if name.endswith(".csv"):
            frame.to_csv(path, index=False)
        else:
            frame.to_excel(path, index=False, engine="openpyxl")
        written.append(path)

    json_files = {
        "stock.json": [
            {"SKU_ID": r.sku_id, "Warehouse_ID": r.location_id, "Current_Stock": r.on_hand,
             "Transit": int(transit.get((r.sku_id, r.location_id), 0)), "Date": r.day.isoformat()} for r in stock
        ],
        "risk.json": SAMPLE_RISKS,
    }
    for name, payload in json_files.items():
        path = out_dir / name
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        written.append(path)
    return sorted(written)
