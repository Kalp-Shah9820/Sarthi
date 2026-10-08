"""Supplier scoring: TOPSIS against fixed ideal points.

Using fixed ideal / anti-ideal values (not the best and worst supplier present) makes scores absolute:
adding or removing a supplier cannot reorder the others.
"""

import math

CRITERIA = {            # name: (ideal, anti_ideal)
    "price_ratio": (0.90, 1.15),      # unit price / median price for the SKU
    "tat_mean": (1.0, 7.0),           # days
    "tat_cv": (0.05, 0.50),           # std / mean of actual days
    "on_time": (1.00, 0.60),          # Beta posterior mean of on-time deliveries
    "fill_rate": (1.00, 0.75),
    "defect_rate": (0.0, 0.08),
    "esg": (100.0, 40.0),
    "incentive": (0.05, 0.0),         # discount fraction earned at the order quantity
}
WEIGHTS = {
    "Balanced": {"price_ratio": .20, "tat_mean": .15, "tat_cv": .15, "on_time": .20, "fill_rate": .10,
                 "defect_rate": .08, "esg": .07, "incentive": .05},
    "Cash Flow": {"price_ratio": .35, "tat_mean": .08, "tat_cv": .10, "on_time": .15, "fill_rate": .07,
                  "defect_rate": .05, "esg": .05, "incentive": .15},
    "Growth": {"price_ratio": .10, "tat_mean": .25, "tat_cv": .20, "on_time": .25, "fill_rate": .10,
               "defect_rate": .05, "esg": .03, "incentive": .02},
}
SPEED_CRITERIA = ("tat_mean", "tat_cv")
ON_TIME_TOLERANCE = 1.1  # a delivery is on time if it took at most 10 % longer than promised


def on_time_posterior(on_time_count: int, deliveries: int) -> float:
    """Beta(1, 1) prior: a supplier with no history scores 0.5, not 0 or 1."""
    return (1 + on_time_count) / (2 + deliveries)


def utility(criterion: str, value: float) -> float:
    ideal, anti = CRITERIA[criterion]
    return min(1.0, max(0.0, (value - anti) / (ideal - anti)))


def weights_for(mode: str, urgency: float = 0.0) -> dict[str, float]:
    """`urgency` in [0, 1] (1 = stockout imminent) shifts weight toward speed and reliability of timing."""
    raw = dict(WEIGHTS.get(mode, WEIGHTS["Balanced"]))
    urgency = min(1.0, max(0.0, urgency))
    for name in SPEED_CRITERIA:
        raw[name] *= 1 + urgency
    total = sum(raw.values())
    return {name: w / total for name, w in raw.items()}


def score(rows: list[dict], mode: str, urgency: float = 0.0) -> list[float]:
    """Score each supplier 0-100. A criterion missing from a row counts as neutral (0.5)."""
    w = weights_for(mode, urgency)
    out = []
    for row in rows:
        u = {name: utility(name, row[name]) if row.get(name) is not None else 0.5 for name in CRITERIA}
        d_plus = math.sqrt(sum(w[n] * (1 - u[n]) ** 2 for n in CRITERIA))
        d_minus = math.sqrt(sum(w[n] * u[n] ** 2 for n in CRITERIA))
        out.append(round(100 * d_minus / (d_plus + d_minus), 1) if d_plus + d_minus > 0 else 50.0)
    return out


def tier(score_value: float) -> str:
    if score_value >= 85:
        return "Gold"
    return "Silver" if score_value >= 70 else "Bronze"
