"""Replenishment policy: per-SKU service level, safety stock, reorder point and order quantity."""

import math

import numpy as np

SERVICE_LEVEL_RANGE = (0.80, 0.995)
ORDER_PATHS = 2000
SHELF_LIFE_SHARE = 0.5  # never buy more than half a shelf life of demand


def service_level(unit_margin: float, unit_cogs: float, holding_pct_month: float, shelf_life_days: float,
                  review_days: float, goodwill: float) -> float:
    """Newsvendor critical ratio: cost of being one unit short vs. one unit over, per review period."""
    underage = unit_margin * (1 + goodwill)
    overage = unit_cogs * holding_pct_month * review_days / 30 + unit_cogs * review_days / max(shelf_life_days, review_days)
    ratio = underage / (underage + overage) if underage + overage > 0 else SERVICE_LEVEL_RANGE[0]
    return float(np.clip(ratio, *SERVICE_LEVEL_RANGE))


def reorder_point(ltd: np.ndarray, sl: float) -> tuple[int, int]:
    """(safety stock, reorder point) from simulated lead-time demand at service level `sl`."""
    rop = float(np.quantile(ltd, sl))
    safety = max(0.0, rop - float(np.mean(ltd)))
    return math.ceil(safety), math.ceil(rop)


def apply_safety_multiplier(ltd: np.ndarray, safety_stock: float, multiplier: float) -> tuple[int, int]:
    """Scale safety stock by the strategy (and any per-SKU adjustment) and rebuild the reorder point."""
    safety = math.ceil(safety_stock * multiplier)
    return safety, math.ceil(float(np.mean(ltd)) + safety)


def order_quantity(mu: np.ndarray, k: float, lead_days: float, review_days: int, sl: float,
                   inventory_position: float, moq: int, shelf_life_days: float, capacity: float | None,
                   seed: int) -> int:
    """Order up to the `sl` quantile of demand over lead time + review period, within shelf-life and capacity."""
    mu = np.asarray(mu, dtype=float)
    span = math.ceil(lead_days) + review_days
    if span > len(mu):
        mu = np.concatenate([mu, np.full(span - len(mu), mu[-1])])
    m = np.maximum(mu[:span], 1e-9)
    rng = np.random.default_rng(seed)
    total = rng.negative_binomial(k, k / (k + m), size=(ORDER_PATHS, span)).sum(axis=1)
    up_to = float(np.quantile(total, sl))
    qty = up_to - inventory_position
    if qty <= 0:
        return 0
    moq = max(1, int(moq))
    ceiling = float(np.mean(mu)) * shelf_life_days * SHELF_LIFE_SHARE
    if capacity is not None:
        ceiling = min(ceiling, capacity)
    rounded = math.ceil(qty / moq) * moq
    if rounded > ceiling:
        rounded = math.floor(ceiling / moq) * moq
    return int(rounded) if rounded >= moq else 0


def days_of_cover(on_hand: float, velocity: float) -> float:
    return on_hand / max(velocity, 0.01)
