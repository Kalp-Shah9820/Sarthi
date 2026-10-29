"""Funding orders within the budget, against brute force."""

from itertools import product

import numpy as np
import pytest

from sarthi.analytics import (
    budget,
)

# ── Budget ───────────────────────────────────────────────────────────────────

def test_budget_matches_brute_force():
    rng = np.random.default_rng(5)
    for _ in range(5):
        values, costs = rng.uniform(1000, 20000, 8), rng.uniform(5000, 40000, 8)
        limit = 70000.0
        chosen = budget.fund_orders(values, costs, limit)
        assert (chosen * costs).sum() <= limit + 1e-6
        best = max(np.dot(pick, values) for pick in product([0, 1], repeat=8) if np.dot(pick, costs) <= limit)
        assert (chosen * values).sum() == pytest.approx(best)


def test_budget_edge_cases():
    assert budget.fund_orders(np.array([]), np.array([]), 1000).tolist() == []
    assert budget.fund_orders(np.array([5.0, 9.0]), np.array([10.0, 10.0]), 0).tolist() == [0, 0]
    assert budget.fund_orders(np.array([5.0, 9.0]), np.array([10.0, 10.0]), 10).tolist() == [0, 1]
    assert budget.fund_orders(np.array([-3.0, 9.0]), np.array([1.0, 10.0]), 100).tolist() == [0, 1]
    assert budget.greedy_by_ratio(np.array([5.0, 9.0, 4.0]), np.array([10.0, 10.0, 2.0]), 12).tolist() == [0, 1, 1]
