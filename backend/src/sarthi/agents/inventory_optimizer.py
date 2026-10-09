"""Inventory Optimizer (DECIDE): the per-SKU decision record.

For every SKU: stockout probability from simulation, safety stock and reorder point at a margin-based
service level, Profit-at-Risk, the Ikigai zone, and how much to order. Also keeps seven days of zone
history so the drift time-lapse has real data from the first run.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
from sqlmodel import select

from sarthi.agents.base import Agent
from sarthi.analytics import montecarlo, par, policy, zones
from sarthi.analytics.forecast import uncensor, weekday_mean_forecast
from sarthi.analytics.topsis import ON_TIME_TOLERANCE
from sarthi.db import session
from sarthi.models import Delivery, Inbound, Preference, Sku, SkuSnapshot, SkuSupplier, Supplier
from sarthi.seed.catalog import STORE_ID

HISTORY_DAYS = 7          # days of zone history kept for the drift time-lapse (today included)
BACKFILL_PATHS = 500
MIN_LEAD_SAMPLES = 5
OVERSTOCK_ZONES = ("ghost", "money")


@dataclass
class SkuInputs:
    sku: dict                       # id, name, category, cogs, price, shelf_life_days, holding_cost_pct, moq
    on_hand: float
    receipts: list[tuple[int, float]]   # (days until expected arrival, qty) for stock already on order
    mu: np.ndarray                  # mean demand per day for the coming 30 days
    k: float
    velocity: float
    velocity_trend: float
    lead_samples: np.ndarray        # past actual lead times in days for this SKU's primary supplier
    supplier: dict                  # id, name, capacity_limit
    lead_mult: float = 1.0
    demand_mult: float = 1.0
    age_days: int = 0               # days since the last delivery arrived
    stockout_days_90: int = 0
    last_reorder: str = ""
    safety_pct: float = 0.0         # per-SKU safety stock adjustment from a user preference
    forecast: dict = field(default_factory=dict)   # model, wape (for display)


def decide(inp: SkuInputs, settings, strategy: dict, paths: int, run_seed: int) -> dict:
    """All numbers for one SKU. Pure: same inputs and seed give the same record."""
    sku = inp.sku
    seed = montecarlo.sku_seed(run_seed, sku["id"])
    # Disruption also delays stock that is already on its way (to the nearest whole day, never before tomorrow).
    receipts = [(max(1, round(days * inp.lead_mult)) if days >= 1 else 0, qty) for days, qty in inp.receipts]
    mc = montecarlo.simulate(inp.on_hand, receipts, inp.mu, inp.k, inp.lead_samples, paths, seed,
                             demand_mult=inp.demand_mult, lead_mult=inp.lead_mult)

    unit_margin = sku["price"] - sku["cogs"]
    sl = policy.service_level(unit_margin, sku["cogs"], sku["holding_cost_pct"], sku["shelf_life_days"],
                              settings.review_period_days, settings.goodwill_factor)
    base_safety, _ = policy.reorder_point(mc.ltd, sl)
    multiplier = float(strategy.get("safetyStockMultiplier", 1.0)) * (1 + inp.safety_pct / 100)
    safety, rop = policy.apply_safety_multiplier(mc.ltd, base_safety, multiplier)

    lead_mean = float(np.mean(inp.lead_samples)) * inp.lead_mult
    lead_eff = lead_mean * float(strategy.get("leadTimeBuffer", 1.2))
    velocity = inp.velocity * inp.demand_mult
    doc = policy.days_of_cover(inp.on_hand, velocity)
    arriving_in_time = sum(qty for days, qty in receipts if days <= lead_eff)
    cover = policy.days_of_cover(inp.on_hand + arriving_in_time, velocity)

    shortage = par.par_shortage(mc.expected_lost_units, unit_margin, settings.goodwill_factor)
    overstock = par.par_overstock(inp.on_hand, velocity, sku["cogs"], sku["holding_cost_pct"],
                                  settings.overstock_cover_days, max(sku["shelf_life_days"] - inp.age_days, 0))
    roi = par.carry_roi(unit_margin / sku["price"] if sku["price"] else 0.0, sku["holding_cost_pct"], doc)
    zone_raw = zones.classify({"doc": doc, "cover": cover, "lead_eff": lead_eff, "stockout_prob": mc.stockout_prob,
                               "carry_roi": roi}, settings)

    position = inp.on_hand + sum(qty for _, qty in receipts)
    qty = 0
    if position < rop:
        qty = policy.order_quantity(inp.mu * inp.demand_mult, inp.k, lead_mean, settings.review_period_days, sl, position,
                                    sku["moq"], sku["shelf_life_days"], inp.supplier.get("capacity_limit") or None, seed)

    excess = par.excess_units(inp.on_hand, velocity, settings.overstock_cover_days)
    return {
        "sku_id": sku["id"], "zone_raw": zone_raw, "on_hand": int(inp.on_hand), "inbound": int(sum(q for _, q in receipts)),
        "velocity": round(velocity, 2), "velocity_trend": round(inp.velocity_trend, 3),
        "doc": round(doc, 1), "cover": round(cover, 1), "lead_mean": round(lead_mean, 1), "lead_eff": round(lead_eff, 1),
        "stockout_prob": round(mc.stockout_prob, 4), "expected_lost_units": round(mc.expected_lost_units, 1),
        "service_level": round(sl, 3), "ss": int(safety), "rop": int(rop), "qty": int(qty),
        "par_shortage": round(shortage), "par_overstock": round(overstock), "par": par.profit_at_risk(shortage, overstock),
        "carry_roi": round(roi, 3), "excess_units": int(excess),
        "risk_shortage": round(100 * mc.stockout_prob),
        "risk_overstock": round(100 * min(1.0, excess / inp.on_hand)) if inp.on_hand > 0 else 0,
        "p95_stock": montecarlo.p95_stock(mc.ending), "sigma": round(mc.sigma_daily, 1),
        "mc_bins": montecarlo.histogram(mc.lost_frac),
        "supplier_id": inp.supplier.get("id"), "supplier_name": inp.supplier.get("name", ""),
        "lead_mult": inp.lead_mult, "demand_mult": inp.demand_mult,
        "age": inp.age_days, "historical_stockouts": inp.stockout_days_90, "last_reorder": inp.last_reorder,
        "margin_pct": round(100 * unit_margin / sku["price"]) if sku["price"] else 0,
        "model": inp.forecast.get("model", ""), "wape": inp.forecast.get("wape", 0.0),
    }


def finalise(record: dict, zone: str) -> dict:
    """Attach the final zone and the risk figure that matches it (shortage for most, overstock for ghost/money)."""
    record["zone"] = zone
    record["risk"] = record["risk_overstock"] if zone in OVERSTOCK_ZONES else record["risk_shortage"]
    return record


class Context:
    """Master data and history loaded once per run, with per-day views for the history back-fill."""

    def __init__(self, forecasts: dict, history: dict):
        self.forecasts = forecasts
        self.as_of: date = history["as_of"]
        sales, stock = history["sales"], history["stock"]
        self.days: list[date] = sorted(stock["day"].unique())
        self.day_index = {d: i for i, d in enumerate(self.days)}
        self.on_hand = {sku_id: g.set_index("day")["on_hand"].reindex(self.days).to_numpy(dtype=float)
                        for sku_id, g in stock.groupby("sku_id")}
        self.demand = {sku_id: uncensor(g.set_index("day")["qty"].reindex(self.days, fill_value=0).to_numpy(dtype=float),
                                        self.on_hand.get(sku_id, np.full(len(self.days), np.nan)))
                       for sku_id, g in sales.groupby("sku_id")}
        with session() as s:
            self.skus = [k.model_dump() for k in s.exec(select(Sku).order_by(Sku.id)).all()]
            suppliers = {x.id: x.model_dump() for x in s.exec(select(Supplier).order_by(Supplier.id)).all()}
            links = s.exec(select(SkuSupplier).order_by(SkuSupplier.is_primary.desc())).all()
            deliveries = s.exec(select(Delivery.supplier_id, Delivery.sku_id, Delivery.expected_days, Delivery.actual_days,
                                       Delivery.qty_ordered, Delivery.qty_received, Delivery.ordered_on)).all()
            self.inbounds = s.exec(select(Inbound).where(Inbound.location_id == STORE_ID)).all()
            self.safety_pct = {p.target: p.value or 0.0 for p in s.exec(
                select(Preference).where(Preference.directive == "safety_stock_pct", Preference.active)).all()}
        self.suppliers: dict[str, dict] = suppliers
        self.primary: dict[str, dict] = {}
        self.unit_price: dict[tuple[str, str], float] = {}
        for link in links:                       # primaries first, so the first link seen per SKU wins
            self.primary.setdefault(link.sku_id, suppliers.get(link.supplier_id, {}))
            self.unit_price[(link.sku_id, link.supplier_id)] = link.unit_price
        self.lead_by_pair: dict[tuple[str, str], list[float]] = {}
        self.ratio_by_supplier: dict[str, list[float]] = {}
        # per supplier: deliveries, how many were on time, units ordered / received, lateness ratio by month
        self.delivery_stats: dict[str, dict] = {
            sid: {"n": 0, "on_time": 0, "ordered": 0, "received": 0, "by_month": {}} for sid in suppliers}
        for supplier_id, sku_id, expected, actual, ordered, received, ordered_on in deliveries:
            self.lead_by_pair.setdefault((supplier_id, sku_id), []).append(actual)
            stats = self.delivery_stats.setdefault(
                supplier_id, {"n": 0, "on_time": 0, "ordered": 0, "received": 0, "by_month": {}})
            stats["n"] += 1
            stats["ordered"] += ordered
            stats["received"] += received
            if expected > 0:
                ratio = actual / expected
                self.ratio_by_supplier.setdefault(supplier_id, []).append(ratio)
                stats["on_time"] += ratio <= ON_TIME_TOLERANCE
                stats["by_month"].setdefault(ordered_on.strftime("%Y-%m"), []).append(ratio)

    def lead_samples(self, sku: dict, supplier: dict) -> np.ndarray:
        """Likely lead times in days for this SKU from this supplier.

        Uses that pair's own deliveries when there are enough. Otherwise takes the promised lead time (the
        SKU's, scaled by how fast this supplier is relative to the SKU's usual one) and applies the
        supplier's record of running early or late.
        """
        own = self.lead_by_pair.get((supplier.get("id"), sku["id"]), [])
        if len(own) >= MIN_LEAD_SAMPLES:
            return np.array(own)
        promised = float(sku["lead_time_days"])
        usual = self.primary.get(sku["id"], {})
        if usual and supplier.get("id") != usual.get("id") and usual.get("avg_tat_days") and supplier.get("avg_tat_days"):
            promised *= supplier["avg_tat_days"] / usual["avg_tat_days"]
        ratios = self.ratio_by_supplier.get(supplier.get("id"), [])
        return np.array([promised * r for r in ratios]) if ratios else np.array([promised])

    def inputs(self, sku: dict, day: date, lead_modifier: dict, demand_multiplier: dict) -> SkuInputs | None:
        sku_id = sku["id"]
        idx = self.day_index.get(day)
        f = self.forecasts.get(sku_id)
        if idx is None or f is None or sku_id not in self.on_hand or np.isnan(self.on_hand[sku_id][idx]):
            return None
        supplier = self.primary.get(sku_id, {})
        if day == self.as_of:
            mu, velocity, trend = np.asarray(f.daily, dtype=float), f.velocity, f.velocity_trend
        else:   # an earlier day, seen only with what was known then
            y = self.demand[sku_id][: idx + 1]
            mu = weekday_mean_forecast(y, 30)
            velocity = float(np.mean(y[-14:]))
            prior = float(np.mean(y[-28:-14])) if len(y) >= 28 else velocity
            trend = (velocity - prior) / max(prior, 0.01)
        mine = [i for i in self.inbounds if i.sku_id == sku_id and i.ordered_on <= day]
        open_ = [i for i in mine if i.received_on is None or i.received_on > day]
        arrived = [i.received_on for i in mine if i.received_on is not None and i.received_on <= day]
        held = self.on_hand[sku_id][max(0, idx - 89): idx + 1]
        return SkuInputs(
            sku=sku, on_hand=float(self.on_hand[sku_id][idx]),
            receipts=[(max(1, (i.expected_on - day).days), float(i.qty)) for i in open_],
            mu=mu, k=f.dispersion_k, velocity=velocity, velocity_trend=trend,
            lead_samples=self.lead_samples(sku, supplier), supplier=supplier,
            lead_mult=float(lead_modifier.get(supplier.get("id"), 1.0)),
            demand_mult=float(demand_multiplier.get(sku["category"], 1.0)),
            age_days=(day - max(arrived)).days if arrived else 0,
            stockout_days_90=int(np.sum(held == 0)),
            last_reorder=max((i.ordered_on for i in mine), default=day).isoformat(),
            safety_pct=float(self.safety_pct.get(sku_id, 0.0)),
            forecast={"model": f.model, "wape": round(f.wape, 3)},
        )


def stored_history(first: date, last: date) -> dict[tuple[date, str], SkuSnapshot]:
    """Latest stored snapshot per (day, SKU) in a date range."""
    with session() as s:
        rows = s.exec(select(SkuSnapshot).where(SkuSnapshot.as_of >= first, SkuSnapshot.as_of <= last)
                      .order_by(SkuSnapshot.run_id)).all()
    return {(r.as_of, r.sku_id): r for r in rows}


class InventoryOptimizer(Agent):
    key, phase = "inventoryOptimizer", "decide"

    def _compute(self, state: dict) -> tuple[dict, list[SkuSnapshot]]:
        ctx = Context(state["forecasts"], state["history"])
        lead_modifier = state.get("lead_modifier", {})
        demand_multiplier = state.get("demand_multiplier", {})
        dry_run = bool(state.get("dry_run"))
        run_seed, today = self.settings.seed, ctx.as_of
        snapshots: list[SkuSnapshot] = []

        # previous[(sku)] = (final zone, raw zone) of the day before the one being computed
        stored = stored_history(today - timedelta(days=HISTORY_DAYS - 1), today - timedelta(days=1))
        previous: dict[str, tuple[str, str]] = {}
        if not dry_run:
            for back in range(HISTORY_DAYS - 1, 0, -1):
                day = today - timedelta(days=back)
                for sku in ctx.skus:
                    existing = stored.get((day, sku["id"]))
                    if existing:
                        previous[sku["id"]] = (existing.zone, existing.metrics.get("zone_raw", existing.zone))
                        continue
                    inp = ctx.inputs(sku, day, lead_modifier, demand_multiplier)
                    if inp is None:
                        continue
                    record = decide(inp, self.settings, self.strategy, BACKFILL_PATHS, run_seed)
                    zone = zones.with_hysteresis(record["zone_raw"], *previous.get(sku["id"], (None, None)))
                    previous[sku["id"]] = (zone, record["zone_raw"])
                    record.pop("mc_bins")          # history rows only need the zone and headline numbers
                    snapshots.append(SkuSnapshot(run_id=self.bb.run_id, as_of=day, sku_id=sku["id"], zone=zone,
                                                 metrics=finalise(record, zone)))

        decisions: dict[str, dict] = {}
        for sku in ctx.skus:
            inp = ctx.inputs(sku, today, lead_modifier, demand_multiplier)
            if inp is None:
                continue
            record = decide(inp, self.settings, self.strategy, self.settings.mc_paths, run_seed)
            # A what-if shows the unsmoothed effect; real runs damp one-day flips.
            zone = record["zone_raw"] if dry_run else zones.with_hysteresis(record["zone_raw"], *previous.get(sku["id"], (None, None)))
            decisions[sku["id"]] = finalise(record, zone)
            if not dry_run:
                snapshots.append(SkuSnapshot(run_id=self.bb.run_id, as_of=today, sku_id=sku["id"], zone=zone, metrics=record))
        return decisions, snapshots

    async def work(self, state: dict) -> dict:
        decisions, snapshots = await asyncio.to_thread(self._compute, state)
        if snapshots:
            with session() as s:
                s.add_all(snapshots)
                s.commit()

        total = sum(d["par"] for d in decisions.values())
        threshold = round(self.settings.critical_prob * 100)
        critical = sum(1 for d in decisions.values() if d["risk_shortage"] > threshold)
        self.bb.put("cfoAgent", self.phase, "par_total", f"₹{total / 1000:.1f}K", tone="chaos" if total > 0 else "sweet")
        self.bb.act("monteCarloRiskEngine", self.phase,
                    f"Simulated {self.settings.mc_paths} demand paths for {len(decisions)} SKUs",
                    result=f"{critical} above {threshold}% stockout probability", paths=self.settings.mc_paths, critical=critical)
        for sku_id, d in decisions.items():
            self.bb.metric(self.key, sku_id, key="decision", phase=self.phase,
                           **{k: v for k, v in d.items() if k not in ("mc_bins", "sku_id")})
        return {"decisions": decisions, "as_of": state["history"]["as_of"]}
