"""Decision confidence and routing.

Confidence here has a definition: how likely the same decision would be made under the forecast
uncertainty we can measure, combined with how trustworthy the inputs are. It is not a number the model
reports about itself.
"""

from dataclasses import replace

import numpy as np

from sarthi.learning import bandit

WEIGHTS = {"stability": 0.5, "reliability": 0.3, "quality": 0.2}
CONFIDENCE_RANGE = (35, 99)
STABILITY_PATHS = 500
QTY_TOLERANCE = 0.25           # a redrawn order within 25 % of the proposed one counts as "the same decision"
WAPE_AT_ZERO = 0.6             # forecast error at which reliability reaches zero
EARNED_AUTONOMY = 0.60         # we must be 90 % sure the manager approves this kind of action this often
PHANTOM_CONFIDENCE = 80
MIN_SALES_DAYS, MIN_DELIVERIES, FRESH_DAYS = 180, 15, 2


def data_quality(sales_days: int, supplier_deliveries: int, stock_age_days: int, phantom_flag: bool) -> float:
    """Share of four basic checks the inputs pass."""
    checks = [sales_days >= MIN_SALES_DAYS, supplier_deliveries >= MIN_DELIVERIES, stock_age_days <= FRESH_DAYS, not phantom_flag]
    return sum(checks) / len(checks)


def purchase_stability(inp, baseline: dict, decide, settings, strategy: dict, errors: np.ndarray, n_draws: int, seed: int) -> float:
    """Share of plausible forecast errors under which an order of about the same size is still needed."""
    if baseline["qty"] <= 0 or len(errors) == 0:
        return 0.0
    rng = np.random.default_rng(seed)
    same = 0
    for e in rng.choice(errors, size=n_draws):
        shifted = replace(inp, demand_mult=inp.demand_mult * max(0.1, 1 + float(e)))
        redo = decide(shifted, settings, strategy, STABILITY_PATHS, settings.seed)
        same += redo["qty"] > 0 and abs(redo["qty"] - baseline["qty"]) <= QTY_TOLERANCE * baseline["qty"]
    return same / n_draws


def overstock_stability(decision: dict, cover_days: float, errors: np.ndarray, n_draws: int, seed: int) -> float:
    """Share of plausible forecast errors under which the SKU is still overstocked."""
    if len(errors) == 0:
        return 0.0
    rng = np.random.default_rng(seed)
    draws = rng.choice(errors, size=n_draws)
    return float(np.mean([decision["doc"] / max(0.1, 1 + float(e)) > cover_days for e in draws]))


def combine(stability: float, wape: float, quality: float) -> int:
    reliability = 1 - min(1.0, wape / WAPE_AT_ZERO)
    score = WEIGHTS["stability"] * stability + WEIGHTS["reliability"] * reliability + WEIGHTS["quality"] * quality
    return int(np.clip(round(100 * score), *CONFIDENCE_RANGE))


def audit_confidence(alert_type: str, correlation: float | None = None) -> int:
    """Findings that are not forecasts: strength of the evidence instead."""
    if alert_type == "cannibalization" and correlation is not None:
        return int(np.clip(round(100 * min(1.0, abs(correlation) + 0.2)), *CONFIDENCE_RANGE))
    return PHANTOM_CONFIDENCE


def arm_key(zone: str, alert_type: str) -> str:
    return f"{zone}:{alert_type}"


def route(confidence: int, cost: float, warnings: list[str], no_auto: bool, arm: str, settings) -> tuple[str, list[str]]:
    """'auto' only if every condition holds; otherwise 'review' with the reasons it needs a person."""
    held_back = []
    if confidence < settings.auto_confidence:
        held_back.append("confidence below the automatic threshold")
    if cost > settings.auto_max_order_value:
        held_back.append("value above the unattended limit")
    if warnings:
        held_back.append("an unresolved warning")
    if no_auto:
        held_back.append("the manager asked to be consulted")
    if bandit.lower_bound(arm) < EARNED_AUTONOMY:
        held_back.append("this kind of action has not been approved often enough yet")
    return ("review" if held_back else "auto"), held_back
