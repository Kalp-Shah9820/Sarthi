"""Builds 18 months of demo history for the catalogue and writes it to the database.

`build_dataset` is pure (same seed and date -> same rows); `seed_database` wipes the database and loads it.

The store ledger is simulated in two parts per SKU:
1. A "legacy" period run by a simple reorder rule, which produces realistic order, delivery and stockout
   history (chaos SKUs depend on a volatile supplier and do run out).
2. A final "pin window" with no new orders, started at whatever stock makes the last day end exactly on
   the stock figure the UI shows. If that start level is above the legacy stock the difference is booked
   as a delivery; if below, it is an unrecorded write-down. The window is at least 14 days so recent
   velocity and the 7-day zone history are computed from coherent data.
"""

import json
import math
import random
from datetime import date, timedelta

import numpy as np
from sqlalchemy import insert
from sqlmodel import Session, SQLModel

from sarthi.config import get_settings
from sarthi.db import get_engine
from sarthi.models import (
    Aisle,
    Delivery,
    Inbound,
    Location,
    PriceHistory,
    Sale,
    Sku,
    SkuSupplier,
    StockDaily,
    StrategyPolicy,
    Supplier,
    local_today,
)
from sarthi.seed import catalog as cat

HISTORY_DAYS = 540
PIN_DAYS = 14
WEEKDAY_SHAPE = np.array([0.92, 0.90, 0.95, 1.00, 1.08, 1.18, 1.12])  # Mon..Sun
WEEKDAY_SHAPE = WEEKDAY_SHAPE / WEEKDAY_SHAPE.mean()
PRIMARY_SHARE = 0.7          # share of legacy orders placed with the SKU's primary supplier
LEGACY_TRIGGER = 1.5         # legacy rule reorders below this many lead-times of demand
LEGACY_ORDER = 2.0           # ... and orders this many lead-times of demand
SHORT_SHIPMENT = 0.8         # a short shipment delivers this share of the order
PRICE_FACTORS = [0.92, 0.95, 0.97, 1.02, 1.00, 0.98]
MODES, MODE_P = ["multimodal", "air", "sea"], [0.7, 0.2, 0.1]


def history_days(today: date) -> list[date]:
    return [today - timedelta(days=HISTORY_DAYS - 1 - i) for i in range(HISTORY_DAYS)]


def daily_mean(sku: dict, days: list[date]) -> np.ndarray:
    """Mean demand per day: the mock's 12-month shape scaled to end at `vel`, preceded by 6 flat months."""
    shape = np.array(sku["sales"], dtype=float) * sku["vel"] / sku["sales"][-1]
    monthly = np.concatenate([np.full(6, shape[0]), shape])
    centres = np.arange(len(monthly)) * 30 + 15
    mu = np.interp(np.arange(len(days)), centres, monthly)
    return mu * WEEKDAY_SHAPE[[d.weekday() for d in days]]


def dispersion(zone: str) -> int:
    return 8 if zone in ("sweet", "chaos") else 3


def expected_lead(sku: dict, supplier: dict, suppliers: dict) -> float:
    """The mock's lead time is for the SKU's primary supplier; others scale by their relative speed."""
    primary = suppliers[cat.primary_supplier(sku["zone"])]
    return sku["lead"] * supplier["avgTAT"] / primary["avgTAT"]


def _pick_supplier(sku: dict, suppliers: dict, rng) -> dict:
    primary = cat.primary_supplier(sku["zone"])
    if rng.random() < PRIMARY_SHARE:
        return suppliers[primary]
    # Only chaos SKUs ever buy from the volatile Bronze supplier (it is their primary).
    others = [s for s in suppliers if s != primary and suppliers[s]["tier"] != "Bronze"]
    return suppliers[others[int(rng.integers(len(others)))]]


def _simulate_sku(sku: dict, days: list[date], mu: np.ndarray, demand: np.ndarray, suppliers: dict, rng) -> dict:
    n = len(days)
    last = n - 1
    overstock = sku["zone"] in ("ghost", "money")
    # first day of the pin window; overstocked SKUs received their last (too large) delivery `age` days ago
    ws = last - sku["age"] if overstock else n - PIN_DAYS

    on_hand = np.zeros(n, dtype=int)
    sold = np.zeros(n, dtype=int)
    orders: list[dict] = []
    arrivals: dict[int, list[dict]] = {}
    open_order = False
    next_supplier = _pick_supplier(sku, suppliers, rng)
    stock = math.ceil(mu[0] * sku["lead"] * LEGACY_ORDER)

    for t in range(ws):
        for order in arrivals.pop(t, []):
            stock += order["qty_received"]
            open_order = False
            next_supplier = _pick_supplier(sku, suppliers, rng)
        s = min(int(demand[t]), stock)
        stock -= s
        sold[t], on_hand[t] = s, stock

        lead = expected_lead(sku, next_supplier, suppliers)
        if not open_order and stock < mu[t] * lead * LEGACY_TRIGGER:
            actual = lead * float(rng.lognormal(0.0, next_supplier["lead_sigma"]))
            qty = max(sku["moq"], math.ceil(mu[t] * lead * LEGACY_ORDER))
            short = rng.random() > next_supplier["fulfillment"]
            received = round(qty * SHORT_SHIPMENT) if short else round(qty * (1 - next_supplier["defectRate"]))
            order = {
                "supplier_id": next_supplier["id"], "ordered": t, "arrive": t + max(1, round(actual)),
                "expected_days": round(lead, 2), "actual_days": round(actual, 2),
                "qty": qty, "qty_received": received, "mode": MODES[int(rng.choice(len(MODES), p=MODE_P))],
            }
            orders.append(order)
            arrivals.setdefault(order["arrive"], []).append(order)
            open_order = True

    # Orders that would land inside the pin window are treated as never placed.
    orders = [o for o in orders if o["arrive"] < ws]

    # Pin window: start at the level that makes today's closing stock equal the UI's figure.
    start = sku["stock"] + int(demand[ws:].sum())
    gap = start - stock
    if gap > 0:
        primary = suppliers[cat.primary_supplier(sku["zone"])]
        orders.append({
            "supplier_id": primary["id"], "ordered": ws - sku["lead"], "arrive": ws,
            "expected_days": float(sku["lead"]), "actual_days": float(sku["lead"]),
            "qty": gap, "qty_received": gap, "mode": "multimodal",
        })
    stock = start
    for t in range(ws, n):
        stock -= int(demand[t])
        sold[t], on_hand[t] = int(demand[t]), stock

    def day_of(idx: int) -> date:
        return days[0] + timedelta(days=idx)

    inbounds, deliveries = [], []
    for o in orders:
        ordered_on = day_of(o["ordered"])
        inbounds.append({
            "sku_id": sku["id"], "location_id": cat.STORE_ID, "supplier_id": o["supplier_id"], "qty": o["qty"],
            "ordered_on": ordered_on, "expected_on": ordered_on + timedelta(days=max(1, round(o["expected_days"]))),
            "received_on": day_of(o["arrive"]),
        })
        deliveries.append({
            "supplier_id": o["supplier_id"], "sku_id": sku["id"], "ordered_on": ordered_on,
            "expected_days": o["expected_days"], "actual_days": o["actual_days"],
            "qty_ordered": o["qty"], "qty_received": o["qty_received"], "mode": o["mode"],
        })

    if sku["zone"] == "sweet":  # healthy SKUs have a replenishment already on its way
        today = days[-1]
        inbounds.append({
            "sku_id": sku["id"], "location_id": cat.STORE_ID, "supplier_id": cat.primary_supplier("sweet"),
            "qty": sku["vel"] * (sku["lead"] + 7), "ordered_on": today - timedelta(days=max(sku["lead"] - 1, 0)),
            "expected_on": today + timedelta(days=1), "received_on": None,
        })
    return {"on_hand": on_hand, "sold": sold, "inbounds": inbounds, "deliveries": deliveries}


def _baskets(days: list[date], sold: dict[str, np.ndarray], prices: dict[str, float], seed: int) -> list[dict]:
    """Group each day's sold units into baskets that follow the catalogue's co-purchase rules."""
    py_rng = random.Random(seed)
    ids = list(sold)
    rules = [(frozenset(a), c, p) for a, c, p in cat.BASKET_AFFINITY]
    rows: list[dict] = []
    for t, day in enumerate(days):
        remaining = {sku_id: int(sold[sku_id][t]) for sku_id in ids}
        units = [sku_id for sku_id in ids for _ in range(remaining[sku_id])]
        py_rng.shuffle(units)  # walking a shuffled list picks first items in proportion to units sold
        tag, count = day.strftime("%y%m%d"), 0
        for first in units:
            if remaining[first] == 0:  # already used as someone else's add-on
                continue
            remaining[first] -= 1
            basket = {first}
            tried: set[int] = set()
            changed = True
            while changed:
                changed = False
                for i, (antecedents, consequent, confidence) in enumerate(rules):
                    if i in tried or consequent in basket or not antecedents <= basket:
                        continue
                    tried.add(i)
                    if remaining[consequent] > 0 and py_rng.random() < confidence:
                        remaining[consequent] -= 1
                        basket.add(consequent)
                        changed = True
            count += 1
            basket_id = f"{tag}-{count}"
            rows.extend(
                {"day": day, "sku_id": s, "location_id": cat.STORE_ID, "qty": 1, "price": prices[s],
                 "basket_id": basket_id}
                for s in sorted(basket)
            )
    return rows


def _last_months(today: date, n: int) -> list[str]:
    year, month, out = today.year, today.month, []
    for _ in range(n):
        out.append(f"{year}-{month:02d}")
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return out[::-1]


def build_dataset(seed: int, today: date) -> dict:
    """Return every seed row, keyed by table name. Pure: no database or file access."""
    rng = np.random.default_rng(seed)
    days = history_days(today)
    suppliers = {s["id"]: s for s in cat.SUPPLIERS}

    skus = []
    for s in cat.SKUS:
        skus.append({
            **s,
            "price": round(s["cogs"] / (1 - s["margin"] / 100), 2),
            "moq": 12 if s["cogs"] < 50 else 6,
        })
    by_id = {s["id"]: s for s in skus}

    mu = {s["id"]: daily_mean(s, days) for s in skus}
    demand = {}
    for s in skus:  # fixed order keeps the random stream reproducible
        k = dispersion(s["zone"])
        demand[s["id"]] = rng.negative_binomial(k, k / (k + mu[s["id"]]))

    # Falling SKUs first: their stockout days push demand onto the rising SKU of each pair.
    falling = [f for _, f in cat.CANNIBALIZATION]
    order = falling + [s["id"] for s in skus if s["id"] not in falling]
    ledgers: dict[str, dict] = {}
    for sku_id in order:
        for rising, fall in cat.CANNIBALIZATION:
            if rising == sku_id:
                out = ledgers[fall]["on_hand"] == 0
                demand[sku_id] = demand[sku_id] + np.where(out, np.rint(cat.CANNIBAL_SHARE * mu[fall]), 0).astype(int)
        ledgers[sku_id] = _simulate_sku(by_id[sku_id], days, mu[sku_id], demand[sku_id], suppliers, rng)

    stock_daily, inbounds, deliveries = [], [], []
    for s in skus:  # catalogue order, independent of simulation order
        led = ledgers[s["id"]]
        stock_daily.extend(
            {"day": d, "sku_id": s["id"], "location_id": cat.STORE_ID, "on_hand": int(q)}
            for d, q in zip(days, led["on_hand"], strict=True)
        )
        inbounds.extend(led["inbounds"])
        deliveries.extend(led["deliveries"])
    for loc in cat.LOCATIONS:
        stock_daily.extend(
            {"day": today, "sku_id": sku_id, "location_id": loc["id"], "on_hand": qty}
            for sku_id, qty in loc["stock"].items()
        )

    sales = _baskets(days, {s["id"]: ledgers[s["id"]]["sold"] for s in skus}, {s["id"]: s["price"] for s in skus}, seed)

    months = _last_months(today, len(PRICE_FACTORS))
    return {
        "sku": [
            {"id": s["id"], "name": s["name"], "category": s["cat"], "cogs": float(s["cogs"]), "price": s["price"],
             "shelf_life_days": s["shelfLife"], "holding_cost_pct": s["holdingCostPct"], "moq": s["moq"],
             "aisle_id": cat.AISLE_OF_CATEGORY.get(s["cat"], cat.DEFAULT_AISLE), "lead_time_days": float(s["lead"])}
            for s in skus
        ],
        "location": [{k: v for k, v in loc.items() if k != "stock"} for loc in cat.LOCATIONS],
        "supplier": [
            {"id": s["id"], "name": s["name"], "tier": s["tier"], "city": s["city"], "lat": s["lat"], "lon": s["lon"],
             "email": s["email"], "capacity_limit": s["capacityLimit"], "defect_rate": s["defectRate"],
             "esg_score": float(s["esg_score"]), "incentive_text": s["incentive"],
             "incentive_min_qty": s["incentive_min_qty"], "incentive_pct": s["incentive_pct"],
             "max_discount_pct": s["max_discount_pct"], "avg_tat_days": s["avgTAT"]}
            for s in cat.SUPPLIERS
        ],
        "sku_supplier": [
            {"sku_id": s["id"], "supplier_id": sup["id"],
             "unit_price": round(s["cogs"] * sup["price"] / cat.REFERENCE_SUPPLIER_PRICE, 2),
             "is_primary": sup["id"] == cat.primary_supplier(s["zone"])}
            for s in skus for sup in cat.SUPPLIERS
        ],
        "aisle": [dict(a) for a in cat.AISLES],
        "price_history": [
            {"sku_id": s["id"], "month": m, "unit_price": round(s["cogs"] * f, 2)}
            for s in skus for m, f in zip(months, PRICE_FACTORS, strict=True)
        ],
        "sale": sales,
        "stock_daily": stock_daily,
        "inbound": inbounds,
        "delivery": deliveries,
    }


def write_feeds() -> None:
    feeds_dir = get_settings().feeds_dir
    feeds_dir.mkdir(parents=True, exist_ok=True)
    for name, entries in cat.FEEDS.items():
        (feeds_dir / f"{name}.json").write_text(json.dumps(entries, indent=2), encoding="utf-8")


def seed_database(seed: int, today: date | None = None) -> dict[str, int]:
    """Wipe every table and load the demo dataset. Returns row counts per table."""
    data = build_dataset(seed, today or local_today())
    engine = get_engine()
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)

    small = {"sku": Sku, "location": Location, "supplier": Supplier, "sku_supplier": SkuSupplier,
             "aisle": Aisle, "price_history": PriceHistory}
    bulk = {"sale": Sale, "stock_daily": StockDaily, "inbound": Inbound, "delivery": Delivery}
    with Session(engine) as s:
        for name, model in small.items():
            s.add_all(model(**row) for row in data[name])
        s.add(StrategyPolicy(mode="Balanced", params=dict(cat.DEFAULT_STRATEGY), source_text="seed", active=True))
        s.commit()
    with engine.begin() as conn:
        for name, model in bulk.items():
            conn.execute(insert(model.__table__), data[name])

    write_feeds()
    return {name: len(rows) for name, rows in data.items()}
