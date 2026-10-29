"""The stockout simulation: direction of every input, determinism, histogram."""


import numpy as np
import pytest

from sarthi.analytics import (
    montecarlo,
)

MU = np.full(30, 40.0)
LEADS = np.array([4.0, 5.0, 6.0, 7.0])


def sim(**kw):
    args = {"on_hand": 200, "receipts": [], "mu": MU, "k": 8, "lead_samples": LEADS, "n_paths": 2000, "seed": 11}
    args.update(kw)
    return montecarlo.simulate(**args)

# ── Monte Carlo ──────────────────────────────────────────────────────────────

def test_risk_rises_with_lead_time_and_falls_with_stock():
    by_lead = [sim(lead_mult=m).stockout_prob for m in (0.5, 1.0, 1.5, 2.0)]
    by_stock = [sim(on_hand=q).stockout_prob for q in (50, 150, 250, 400)]
    assert by_lead == sorted(by_lead) and by_lead[0] < by_lead[-1]
    assert by_stock == sorted(by_stock, reverse=True) and by_stock[0] > by_stock[-1]


def test_risk_rises_with_demand_and_falls_with_incoming_stock():
    assert sim(demand_mult=1.5).stockout_prob > sim().stockout_prob
    assert sim(receipts=[(1, 300)]).stockout_prob < sim().stockout_prob
    assert sim(receipts=[(40, 300)]).stockout_prob == sim().stockout_prob  # arrives beyond the horizon


def test_zero_demand_never_stocks_out():
    result = sim(mu=np.zeros(30), on_hand=0)
    assert result.stockout_prob == 0.0
    assert result.expected_lost_units == 0.0


def test_same_seed_same_result_different_seed_differs():
    a, b, c = sim(), sim(), sim(seed=12)
    assert a.stockout_prob == b.stockout_prob and np.array_equal(a.ltd, b.ltd)
    assert not np.array_equal(a.ltd, c.ltd)


def test_simulated_demand_matches_its_mean():
    result = sim(lead_samples=np.array([30.0]), n_paths=4000)
    assert result.ltd.mean() / 30 == pytest.approx(40.0, rel=0.03)
    assert result.sigma_daily == pytest.approx(np.sqrt(40 + 40 * 40 / 8), rel=0.1)  # var = mu + mu^2/k


def test_lost_units_are_consistent():
    result = sim(on_hand=100)
    assert np.all((result.lost_frac >= 0) & (result.lost_frac <= 1))
    assert result.expected_lost_units == pytest.approx(np.maximum(result.ltd - 100, 0).mean())
    assert np.array_equal(result.ending, 100 - result.ltd)


def test_histogram_shape_and_labels():
    bins = montecarlo.histogram(sim(on_hand=100).lost_frac)
    assert len(bins) == 20
    assert bins[0]["range"] == "0–5%" and bins[-1]["range"] == "95–100%"
    assert [b["highlight"] for b in bins] == [i >= 12 for i in range(20)]
    assert max(b["count"] for b in bins) == 60
    assert all(b["count"] == 0 for b in montecarlo.histogram(np.array([])))


def test_p95_stock_and_seed_helper():
    assert montecarlo.p95_stock(np.array([-50.0, -10.0])) == 0
    assert montecarlo.p95_stock(np.arange(100, 201, dtype=float)) == 105
    assert montecarlo.sku_seed(1, "SKU001") == montecarlo.sku_seed(1, "SKU001")
    assert montecarlo.sku_seed(1, "SKU001") != montecarlo.sku_seed(1, "SKU002")
