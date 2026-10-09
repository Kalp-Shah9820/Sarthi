"""Demand Intelligence (SENSE): forecasts every SKU and mines demand structure from the history."""

import asyncio

import pandas as pd
from sqlmodel import func, select

from sarthi.agents.base import Agent
from sarthi.analytics import anomaly, basket, bullwhip, cannibal, data
from sarthi.analytics.forecast import forecast_all, month_labels
from sarthi.db import session
from sarthi.models import Inbound, Sale, Sku
from sarthi.seed.catalog import STORE_ID

TREND_BAND = 0.05
# Forecasting is the slowest step and depends only on history, so what-if and strategy re-runs reuse it.
# Key: (last sale row, last stock day, external forecast timestamp).
_cache: dict[tuple, dict] = {}


def clear_cache() -> None:
    _cache.clear()


def _fingerprint(settings) -> tuple:
    with session() as s:
        last_sale = s.exec(select(func.max(Sale.id))).one()
        sale_days = s.exec(select(func.max(Sale.day)).where(Sale.location_id == STORE_ID)).one()
    external = settings.external_forecast_file
    return (str(settings.db_file), last_sale, sale_days, external.stat().st_mtime if external.exists() else None)


def analyse(settings) -> dict:
    """Everything derived from history alone. Pure computation plus database reads; safe to run in a thread."""
    sales, stock = data.store_frames()
    with session() as s:
        skus = s.exec(select(Sku)).all()
        receipt_days = set(s.exec(select(Inbound.sku_id, Inbound.received_on).where(Inbound.received_on.is_not(None))).all())
    names = {k.id: k.name for k in skus}
    categories = {k.id: k.category for k in skus}
    aisles = {k.id: k.aisle_id for k in skus}

    external_file = settings.external_forecast_file
    external = pd.read_csv(external_file) if external_file.exists() else None
    forecasts = forecast_all(sales, stock, external=external)

    baskets = data.basket_frame()
    rules = basket.mine_rules(baskets, names)
    pairs = basket.aisle_pairs(rules, aisles)
    as_of = sales["day"].max() if not sales.empty else None
    return {
        "forecasts": forecasts, "sales": sales, "stock": stock, "as_of": as_of,
        "rules": rules, "pairs": pairs, "connections": basket.aisle_connections(pairs),
        "heat": basket.aisle_heat(baskets, aisles),
        "cannibals": cannibal.detect(sales, stock, categories, names),
        "bullwhip": bullwhip.series(data.weekly_totals(sales)),
        "phantoms": anomaly.phantom_inventory(sales, stock, {k: f.velocity for k, f in forecasts.items()}, receipt_days),
        "month_labels": month_labels(as_of) if as_of else [],
    }


class DemandIntel(Agent):
    key, phase = "demandIntel", "sense"

    async def work(self, state: dict) -> dict:
        fingerprint = _fingerprint(self.settings)
        result = _cache.get(fingerprint)
        cached = result is not None
        if not cached:
            result = await asyncio.to_thread(analyse, self.settings)
            _cache.clear()                 # only the current data set is worth keeping
            _cache[fingerprint] = result
        forecasts = result["forecasts"]

        self.bb.metric(self.key, key="demand_intel", phase=self.phase, rules=result["rules"], pairs=result["pairs"],
                       connections=result["connections"], heat=result["heat"], cannibals=result["cannibals"],
                       bullwhip=result["bullwhip"], phantoms=result["phantoms"], month_labels=result["month_labels"])
        for sku_id, f in forecasts.items():
            self.bb.metric(self.key, sku_id, key="forecast", phase=self.phase, velocity=round(f.velocity, 2),
                           velocity_trend=round(f.velocity_trend, 3), model=f.model, wape=round(f.wape, 3),
                           wape_daily=round(f.wape_daily, 3), k=round(f.dispersion_k, 2),
                           daily=[round(float(x), 2) for x in f.daily],
                           monthly_actual=[round(x, 1) for x in f.monthly_actual],
                           monthly_backcast=[round(x, 1) for x in f.monthly_backcast])

        if forecasts:
            now = sum(f.velocity for f in forecasts.values())
            before = sum(f.velocity / (1 + f.velocity_trend) if f.velocity_trend > -0.99 else f.velocity for f in forecasts.values())
            change = now / before - 1 if before > 0 else 0.0
            signal, tone = ("RISING", "sweet") if change > TREND_BAND else ("FALLING", "chaos") if change < -TREND_BAND else ("STABLE", "sweet")
            self.bb.put("forecaster", self.phase, "demand_signal", signal, tone=tone)

            top = max(forecasts.values(), key=lambda f: f.velocity_trend)
            self.bb.act("forecaster", self.phase, f"Forecast {len(forecasts)} SKUs; strongest growth on {top.sku_id}",
                        result=f"{top.model} · WAPE {top.wape * 100:.0f}%" + (" · cached" if cached else ""),
                        sku_id=top.sku_id, trend_pct=round(top.velocity_trend * 100), wape_pct=round(top.wape * 100))
        if result["cannibals"]:
            strongest = result["cannibals"][0]
            self.bb.put(self.key, self.phase, "snacks_cannibalization", strongest["correlation"], tone="money")

        return {"forecast_ready": True, "forecasts": forecasts,
                "history": {"sales": result["sales"], "stock": result["stock"], "as_of": result["as_of"]}}
