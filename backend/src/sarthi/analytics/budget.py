"""Which orders to fund under a cash limit: an exact 0/1 knapsack."""

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp


def greedy_by_ratio(values: np.ndarray, costs: np.ndarray, budget: float) -> np.ndarray:
    chosen = np.zeros(len(values), dtype=int)
    remaining = budget
    for i in np.argsort(-(values / np.maximum(costs, 1e-9))):
        if values[i] > 0 and costs[i] <= remaining:
            chosen[i] = 1
            remaining -= costs[i]
    return chosen


def fund_orders(values: np.ndarray, costs: np.ndarray, budget: float) -> np.ndarray:
    """Return 0/1 per order maximising total value with total cost <= budget."""
    values = np.asarray(values, dtype=float)
    costs = np.asarray(costs, dtype=float)
    if len(values) == 0:
        return np.zeros(0, dtype=int)
    if budget <= 0:
        return np.zeros(len(values), dtype=int)
    res = milp(c=-values, constraints=LinearConstraint(costs.reshape(1, -1), ub=[budget]),
               integrality=np.ones(len(values)), bounds=Bounds(0, 1))
    if not res.success or res.x is None:
        return greedy_by_ratio(values, costs, budget)
    chosen = np.round(res.x).astype(int)
    chosen[values <= 0] = 0  # never fund an order that rescues nothing
    return chosen
