"""Service level, reorder point, order quantity and Profit-at-Risk."""


import numpy as np
import pytest

from sarthi.analytics import (
    montecarlo,
    par,
    policy,
)

MU = np.full(30, 40.0)
LEADS = np.array([4.0, 5.0, 6.0, 7.0])


def sim(**kw):
    args = {"on_hand": 200, "receipts": [], "mu": MU, "k": 8, "lead_samples": LEADS, "n_paths": 2000, "seed": 11}
    args.update(kw)
    return montecarlo.simulate(**args)


# ── Policy ───────────────────────────────────────────────────────────────────

def test_service_level_follows_margin_and_stays_in_range():
    low = policy.service_level(unit_margin=1, unit_cogs=18, holding_pct_month=0.03, shelf_life_days=730, review_days=7, goodwill=0.2)
    high = policy.service_level(unit_margin=50, unit_cogs=90, holding_pct_month=0.03, shelf_life_days=730, review_days=7, goodwill=0.2)
    perishable = policy.service_level(unit_margin=40, unit_cogs=200, holding_pct_month=0.02, shelf_life_days=15, review_days=7, goodwill=0.2)
    assert 0.80 <= low < high <= 0.995
    assert perishable < policy.service_level(40, 200, 0.02, 365, 7, 0.2)  # short shelf life -> hold less


def test_reorder_point_and_multiplier():
    ltd = sim().ltd
    safety, rop = policy.reorder_point(ltd, 0.95)
    assert safety >= 0 and rop >= ltd.mean()
    assert rop == pytest.approx(np.quantile(ltd, 0.95), abs=1)
    more_safety, more_rop = policy.apply_safety_multiplier(ltd, safety, 1.5)
    less_safety, less_rop = policy.apply_safety_multiplier(ltd, safety, 0.8)
    assert less_safety < safety < more_safety and less_rop < more_rop


def order(**kw):
    args = {"mu": MU, "k": 8, "lead_days": 5, "review_days": 7, "sl": 0.95, "inventory_position": 100, "moq": 12,
            "shelf_life_days": 365, "capacity": None, "seed": 3}
    args.update(kw)
    return policy.order_quantity(**args)


def test_order_quantity_rules():
    qty = order()
    assert qty > 0 and qty % 12 == 0
    assert qty >= 12 * 40 - 100                       # covers mean demand over lead + review, less what is held
    assert order(inventory_position=5000) == 0        # already above the order-up-to level
    assert order(shelf_life_days=4) <= 40 * 4 * 0.5   # never more than half a shelf life
    assert order(capacity=120) <= 120
    assert order(capacity=5) == 0                     # cannot meet one minimum order
    assert order(lead_days=40, review_days=7) > qty   # horizon longer than the forecast is extended, not truncated
    assert order() == order()                         # deterministic for a seed


def test_days_of_cover():
    assert policy.days_of_cover(150, 95) == pytest.approx(1.58, abs=0.01)
    assert policy.days_of_cover(100, 0) == 10000

# ── Profit-at-Risk ───────────────────────────────────────────────────────────

def test_profit_at_risk_sides():
    assert par.par_shortage(expected_lost_units=100, unit_margin=5, goodwill=0.2) == pytest.approx(600)
    assert par.par_overstock(on_hand=200, velocity=10, cogs=50, holding_pct_month=0.05, overstock_cover_days=30,
                             shelf_life_remaining_days=365) == 0
    carrying = par.par_overstock(820, 8, 180, 0.06, 30, 365)
    assert carrying == pytest.approx(580 * 180 * 0.06 * (580 / 240) / 2)
    expiring = par.par_overstock(820, 8, 180, 0.06, 30, 50)
    assert expiring == pytest.approx(carrying + (820 - 400) * 180)
    assert par.profit_at_risk(600.4, 100) == 600
    assert par.carry_roi(0.22, 0.06, 102) > 0 > par.carry_roi(0.04, 0.03, 100)
