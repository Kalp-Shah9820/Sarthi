"""Is Sarthi's replenishment policy better than what a spreadsheet would do?

`run_backtest` replays the last N days of demand under two policies that start from the same stock,
face the same demand and get the same sequence of supplier lead times. Each day a policy sees only what
it would have known that day: its own sales and stock so far. `calibration` checks the other claim the
screens make, that a "40 % stockout risk" comes true about 40 % of the time.

Nothing here writes to the database.
"""

import math
import zlib
from dataclasses import dataclass, field
from datetime import date, timedelta
from types import SimpleNamespace

import numpy as np
from sqlmodel import select

from sarthi.analytics import data, montecarlo, policy
from sarthi.analytics.forecast import dispersion, uncensor, weekday_mean_forecast
from sarthi.config import get_settings
from sarthi.db import session
from sarthi.models import Delivery, Inbound, Sku, SkuSupplier
from sarthi.seed import generator

MIN_HISTORY_DAYS = 56          # history a policy needs before the replay starts
LOOKBACK_DAYS = 112            # how far back the Sarthi policy looks each day
REPLAY_PATHS = 400             # simulated futures per SKU-day in the replay
CALIBRATION_PATHS = 300
CALIBRATION_STEP = 2           # every second day: neighbouring days are nearly the same question
BUCKETS = ((0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0001))
NAIVE_WINDOW, NAIVE_FACTOR = 30, 2.0
POLICIES = ("naive", "sarthi")


@dataclass
class Series:
    """One product's history and the facts both policies replay against."""
    sku: Sku
    days: list[date]
    demand: np.ndarray            # true demand per day where known, else estimated from sales
    sold: np.ndarray              # what actually sold (limited by the stock that was there)
    on_hand: np.ndarray           # actual closing stock
    lead_pool: np.ndarray         # this product's past delivery times, in days
    lead_draws: np.ndarray        # the k-th order placed in the replay takes lead_draws[k] days, for either policy
    opening: int = 0
    in_transit: list[tuple[int, int]] = field(default_factory=list)    # (replay day it arrives, qty), ordered before the replay


def _seed(run_seed: int, sku_id: str, salt: str) -> int:
    return zlib.crc32(f"{run_seed}:{sku_id}:{salt}".encode())


def load_series(days: int, truth_seed: int | None = None) -> tuple[list[Series], str]:
    """Histories for the replay and where the demand figures come from ("generator" or "estimated").

    `truth_seed`: the seed the database was generated with, when it is not the configured one.
    """
    settings = get_settings()
    sales, stock = data.store_frames()
    with session() as s:
        skus = {k.id: k for k in s.exec(select(Sku).order_by(Sku.id)).all()}
        primary = {x.sku_id: x.supplier_id for x in s.exec(select(SkuSupplier).where(SkuSupplier.is_primary)).all()}
        deliveries = s.exec(select(Delivery)).all()
        inbounds = s.exec(select(Inbound)).all()
    if sales.empty or stock.empty:
        raise ValueError("the database has no sales history; run `uv run sarthi seed` first")

    built: list[Series] = []
    for sku_id, sku in skus.items():
        sold_frame = sales[sales["sku_id"] == sku_id].sort_values("day")
        held = stock[stock["sku_id"] == sku_id].sort_values("day")
        if len(sold_frame) < days + MIN_HISTORY_DAYS or held["on_hand"].isna().any():
            continue                        # too little history, or a stretch with no stock record
        calendar = list(sold_frame["day"])
        start_day = calendar[-days]
        mine = [d for d in deliveries if d.sku_id == sku_id and d.ordered_on < start_day]
        own = [d.actual_days for d in mine if d.supplier_id == primary.get(sku_id)]
        pool = np.array(own if len(own) >= 5 else [d.actual_days for d in mine] or [sku.lead_time_days], dtype=float)
        rng = np.random.default_rng(_seed(settings.seed, sku_id, "lead"))
        transit = [(max(0, ((i.received_on or i.expected_on) - start_day).days), int(i.qty)) for i in inbounds
                   if i.sku_id == sku_id and i.ordered_on < start_day and (i.received_on is None or i.received_on >= start_day)]
        on_hand = held["on_hand"].to_numpy(dtype=float)
        built.append(Series(
            sku=sku, days=calendar, demand=np.array([]), sold=sold_frame["qty"].to_numpy(dtype=float), on_hand=on_hand,
            lead_pool=pool, lead_draws=np.maximum(1, np.ceil(rng.choice(pool, size=days))).astype(int),
            opening=int(on_hand[-days - 1]), in_transit=[(a, q) for a, q in transit if a < days]))
    if not built:
        raise ValueError(f"no product has {days + MIN_HISTORY_DAYS} days of history; try fewer --days")

    # The seed generator knows the demand that was really there on empty-shelf days. Use it when the database
    # still holds exactly the generated sales; otherwise estimate demand from sales, as the agents do.
    truth = generator.true_demand(settings.seed if truth_seed is None else truth_seed, built[0].days[-1])
    exact = all(s.sku.id in truth["sold"] and len(truth["sold"][s.sku.id]) == len(s.sold)
                and np.array_equal(truth["sold"][s.sku.id], s.sold) for s in built)
    for s in built:
        s.demand = np.asarray(truth["demand"][s.sku.id], dtype=float) if exact else uncensor(s.sold, s.on_hand)
    return built, "generator" if exact else "estimated"


# ── the two policies ─────────────────────────────────────────────────────────

def naive_policy(series: Series, sold: list[float], stock: list[float], on_hand: float, pending: list, settings) -> int:
    """Reorder twice the lead-time demand when stock falls below one lead time of demand (one order at a time)."""
    velocity = float(np.mean(sold[-NAIVE_WINDOW:]))
    lead = float(series.sku.lead_time_days)
    if pending or on_hand >= velocity * lead:
        return 0
    moq = max(1, series.sku.moq)
    return math.ceil(velocity * lead * NAIVE_FACTOR / moq) * moq


def sarthi_policy(series: Series, sold: list[float], stock: list[float], on_hand: float, pending: list, settings) -> int:
    """The agents' rule: forecast, simulate lead-time demand, order up to the newsvendor service level."""
    sku = series.sku
    y = uncensor(np.array(sold[-LOOKBACK_DAYS:]), np.array(stock[-LOOKBACK_DAYS:]))
    mu = weekday_mean_forecast(y, 30)
    recent = y[-MIN_HISTORY_DAYS:]
    weekday_means = np.array([recent[i % 7::7].mean() for i in range(7)])
    k = dispersion(recent, weekday_means[np.arange(len(recent)) % 7])
    seed = _seed(settings.seed, sku.id, f"replay{len(sold)}")
    today = len(sold) - len(series.sold) + len(series.lead_draws)            # replay day index
    receipts = [(max(1, expected - today), float(qty)) for _, qty, expected in pending]
    mc = montecarlo.simulate(on_hand, montecarlo.arrival_index(receipts), mu, k, series.lead_pool, REPLAY_PATHS, seed)
    level = policy.service_level(sku.price - sku.cogs, sku.cogs, sku.holding_cost_pct, sku.shelf_life_days,
                                 settings.review_period_days, settings.goodwill_factor)
    _, reorder_point = policy.reorder_point(mc.ltd, level)
    position = on_hand + sum(qty for _, qty, _ in pending)
    if position >= reorder_point:
        return 0
    return policy.order_quantity(mu, k, float(np.mean(series.lead_pool)), settings.review_period_days, level, position,
                                 sku.moq, sku.shelf_life_days, None, seed)


RULES = {"naive": naive_policy, "sarthi": sarthi_policy}


def replay(series: Series, name: str, days: int, settings) -> dict:
    """Run one policy over the replay window. Day loop: receive, serve demand, decide, order."""
    rule = RULES[name]
    start = len(series.sold) - days
    sold_seen, stock_seen = list(series.sold[:start]), list(series.on_hand[:start])
    mean_lead = float(np.mean(series.lead_pool))
    on_hand = series.opening
    pending = [(arrive, qty, arrive) for arrive, qty in series.in_transit]      # (arrival day, qty, day it is expected)
    received = sold_total = lost = stockout_days = orders = ordered_units = 0
    value_days = holding = 0.0
    sku = series.sku
    for t in range(days):
        arriving = sum(qty for arrive, qty, _ in pending if arrive == t)
        pending = [p for p in pending if p[0] != t]
        on_hand += arriving
        received += arriving
        demand = int(series.demand[start + t])
        served = min(demand, on_hand)
        on_hand -= served
        sold_total += served
        lost += demand - served
        stockout_days += on_hand == 0
        value_days += on_hand * sku.cogs
        holding += on_hand * sku.cogs * sku.holding_cost_pct / 30
        sold_seen.append(served)
        stock_seen.append(on_hand)
        qty = int(rule(series, sold_seen, stock_seen, on_hand, pending, settings))
        if qty > 0:
            lead = int(series.lead_draws[min(orders, days - 1)])
            pending.append((t + lead, qty, t + max(1, round(mean_lead))))
            orders += 1
            ordered_units += qty
    demand_total = float(series.demand[start:].sum())
    lost_margin = lost * (sku.price - sku.cogs)
    return {
        "fill_rate": sold_total / demand_total if demand_total else 1.0, "stockout_days": int(stockout_days),
        "lost_units": int(lost), "lost_margin": round(lost_margin, 2), "avg_inventory_value": round(value_days / days, 2),
        "holding_cost": round(holding, 2), "net": round(-lost_margin - holding, 2), "orders": orders,
        "ordered_units": int(ordered_units), "demand": int(demand_total), "sold": int(sold_total),
        "opening": int(series.opening), "received": int(received), "closing": int(on_hand),
        "on_order_at_end": int(sum(qty for _, qty, _ in pending)),
    }


def _total(rows: list[dict]) -> dict:
    out: dict[str, float] = {k: round(sum(r[k] for r in rows), 2) for k in ("stockout_days", "lost_units", "lost_margin", "avg_inventory_value",
                                                           "holding_cost", "net", "orders", "ordered_units", "demand", "sold")}
    out["fill_rate"] = out["sold"] / out["demand"] if out["demand"] else 1.0
    return out


def run_backtest(days: int = 120, truth_seed: int | None = None) -> dict:
    """Replay both policies. Returns {days, start, end, demand_source, skus: [{id, name, naive, sarthi}], totals}."""
    if days < 14:
        raise ValueError("a backtest needs at least 14 days")
    settings = get_settings()
    series, source = load_series(days, truth_seed)
    rows = [{"id": s.sku.id, "name": s.sku.name, **{name: replay(s, name, days, settings) for name in POLICIES}} for s in series]
    return {"days": days, "start": series[0].days[-days], "end": series[0].days[-1], "demand_source": source, "skus": rows,
            "totals": {name: _total([r[name] for r in rows]) for name in POLICIES}}


# ── calibration ──────────────────────────────────────────────────────────────

def calibration(days: int = 120) -> dict:
    """Is the risk simulation borne out by what happened next?

    For each product and (every second) day of the window: the chance, simulated from what was known that
    day, that the shelf runs empty within the product's shortest lead time, and whether it did. Within
    that many days nothing ordered later can arrive, so both sides describe the same event. (The figure
    on the screens also allows for late deliveries; that part cannot be checked against days on which no
    order was actually placed.)
    """
    from sarthi.agents.inventory_optimizer import Context

    settings = get_settings()
    sales, stock = data.store_frames()
    if sales.empty:
        raise ValueError("the database has no sales history; run `uv run sarthi seed` first")
    as_of = max(stock["day"])
    stand_ins = {}
    for sku_id, frame in sales.groupby("sku_id"):          # dispersion from before the window only: no hindsight
        y = frame.sort_values("day")["qty"].to_numpy(dtype=float)[: -days][-MIN_HISTORY_DAYS:]
        k = dispersion(y, np.full(len(y), y.mean())) if len(y) >= 14 else 5.0
        stand_ins[sku_id] = SimpleNamespace(dispersion_k=k, model="weekday", wape=0.0)
    ctx = Context(stand_ins, {"sales": sales, "stock": stock, "as_of": as_of})

    points: list[tuple[float, bool]] = []
    for sku in ctx.skus:
        held = ctx.on_hand.get(sku["id"])
        if held is None:
            continue
        for back in range(days, 0, -CALIBRATION_STEP):
            day = as_of - timedelta(days=back)
            idx = ctx.day_index.get(day)
            inp = ctx.inputs(sku, day, {}, {}) if idx is not None and idx >= MIN_HISTORY_DAYS else None
            if inp is None or idx is None:
                continue
            horizon = max(1, math.floor(float(np.min(inp.lead_samples))))
            if idx + horizon >= len(held):
                continue                    # what happened next is not on record yet
            mc = montecarlo.simulate(inp.on_hand, montecarlo.arrival_index(inp.receipts), inp.mu, inp.k, np.array([float(horizon)]),
                                     CALIBRATION_PATHS, _seed(settings.seed, sku["id"], f"cal{idx}"))
            points.append((float(mc.stockout_prob), bool((held[idx + 1: idx + 1 + horizon] == 0).any())))

    buckets = []
    for low, high in BUCKETS:
        inside = [(p, hit) for p, hit in points if low <= p < high]
        buckets.append({"range": f"{round(low * 100)}–{min(100, round(high * 100))}%", "n": len(inside),
                        "predicted": float(np.mean([p for p, _ in inside])) if inside else None,
                        "observed": float(np.mean([hit for _, hit in inside])) if inside else None})
    filled = [b for b in buckets if b["n"]]
    gap = sum(b["n"] * abs(float(b["predicted"]) - float(b["observed"])) for b in filled) / max(1, sum(b["n"] for b in filled))
    return {"days": days, "points": len(points), "buckets": buckets, "mean_gap": gap}


# ── report ───────────────────────────────────────────────────────────────────

def _money(value: float) -> str:
    return f"₹{value:,.0f}"


def format_report(result: dict, risk: dict | None = None) -> str:
    """The backtest as a per-product table with totals, and the calibration table under it."""
    lines = [f"Backtest: {result['days']} days, {result['start']} to {result['end']}, {len(result['skus'])} products.",
             "Same opening stock, same demand and the same supplier lead times for both policies.",
             "Demand: " + ("the generator's true demand, including what was wanted on empty-shelf days." if result["demand_source"] == "generator"
                           else "estimated from sales (the data has changed since it was generated)."),
             "",
             f"{'Product':24} {'Policy':7} {'Fill':>6} {'Out days':>8} {'Lost margin':>12} {'Avg stock':>11} {'Holding':>9} {'Net':>11}"]
    for row in result["skus"]:
        for name in POLICIES:
            m = row[name]
            lines.append(f"{(row['name'] if name == 'naive' else '')[:24]:24} {name:7} {m['fill_rate']:>6.1%} {m['stockout_days']:>8} "
                         f"{_money(m['lost_margin']):>12} {_money(m['avg_inventory_value']):>11} {_money(m['holding_cost']):>9} {_money(m['net']):>11}")
    lines.append("-" * 95)
    for name in POLICIES:
        m = result["totals"][name]
        lines.append(f"{'TOTAL':24} {name:7} {m['fill_rate']:>6.1%} {int(m['stockout_days']):>8} {_money(m['lost_margin']):>12} "
                     f"{_money(m['avg_inventory_value']):>11} {_money(m['holding_cost']):>9} {_money(m['net']):>11}")
    naive, sarthi = result["totals"]["naive"], result["totals"]["sarthi"]
    diff = sarthi["net"] - naive["net"]
    verdict = f"Sarthi is {_money(abs(diff))} {'better' if diff >= 0 else 'worse'} than the naive rule over {result['days']} days."
    lines += ["", "Net = minus lost margin minus holding cost (higher is better). " + verdict]
    worse = [r["name"] for r in result["skus"] if r["sarthi"]["net"] < r["naive"]["net"]]
    lines.append("Products where the naive rule did better: " + (", ".join(worse) if worse else "none") + ".")

    if risk is not None:
        lines += ["", f"Calibration: {risk['points']} product-days. For each, the simulated chance (from what was known that day)",
                  "that the shelf runs empty within the product's shortest lead time, and whether it did.",
                  "", f"{'Predicted risk':15} {'Days':>6} {'Mean predicted':>15} {'Came true':>10}"]
        for b in risk["buckets"]:
            shown = (f"{b['predicted']:>15.1%} {b['observed']:>10.1%}" if b["n"] else f"{'—':>15} {'—':>10}")
            lines.append(f"{b['range']:15} {b['n']:>6} {shown}")
        lines.append(f"Average gap between predicted and observed: {risk['mean_gap']:.1%} (0% would be perfectly calibrated).")
        thin = [b["range"] for b in risk["buckets"] if 0 < b["n"] < 20]
        if thin:
            lines.append("Rows with fewer than 20 days (" + ", ".join(thin) + ") are too thin to read much into.")
    return "\n".join(lines)
