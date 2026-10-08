"""Stock redistribution between sites: a transportation linear programme."""

import math

import numpy as np
from scipy.optimize import linprog

MIN_MOVE_UNITS = 10
COST_FIXED = 2.0        # rupees per unit, handling
COST_PER_KM = 0.004     # rupees per unit per km
SURPLUS_FACTOR = 1.5    # a site has surplus above this many times its safety stock


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def unit_cost(distance_km: float) -> float:
    return COST_FIXED + COST_PER_KM * distance_km


def site_needs(on_hand: dict[str, float], capacity: dict[str, float], safety_stock: float) -> tuple[dict, dict]:
    """Split a SKU's safety stock across sites by capacity; return (surplus, deficit) units per site."""
    total_capacity = sum(capacity.get(site, 0) for site in on_hand) or 1
    surplus, deficit = {}, {}
    for site, qty in on_hand.items():
        site_safety = safety_stock * capacity.get(site, 0) / total_capacity
        if qty > SURPLUS_FACTOR * site_safety:
            surplus[site] = qty - SURPLUS_FACTOR * site_safety
        elif qty < site_safety:
            deficit[site] = site_safety - qty
    return surplus, deficit


def plan_transfers(surplus: dict[str, float], deficit: dict[str, float],
                   cost: dict[tuple[str, str], float], value: dict[str, float]) -> list[dict]:
    """Cheapest set of moves from surplus sites to deficit sites; a move happens only if it pays for itself.

    `value[j]` is the per-unit benefit of filling site j's deficit (purchase cost avoided).
    """
    sources = [s for s, q in surplus.items() if q > 0]
    sinks = [d for d, q in deficit.items() if q > 0]
    pairs = [(i, j) for i in sources for j in sinks if i != j]
    if not pairs:
        return []
    c = np.array([cost[(i, j)] - value[j] for i, j in pairs])
    rows, limits = [], []
    for site in sources:
        rows.append([1.0 if i == site else 0.0 for i, _ in pairs])
        limits.append(surplus[site])
    for site in sinks:
        rows.append([1.0 if j == site else 0.0 for _, j in pairs])
        limits.append(deficit[site])
    res = linprog(c, A_ub=np.array(rows), b_ub=np.array(limits), bounds=(0, None), method="highs")
    if not res.success:
        return []
    moves = []
    for (i, j), x in zip(pairs, res.x, strict=True):
        units = math.floor(x + 1e-6)
        if units >= MIN_MOVE_UNITS:
            moves.append({"from": i, "to": j, "units": units, "unit_cost": round(cost[(i, j)], 2),
                          "saving": round(units * (value[j] - cost[(i, j)]), 2)})
    return sorted(moves, key=lambda m: -m["saving"])
