"""Pure-function tests: simulation, policy, risk value, zones, supplier scoring, optimisers, shipping, anomalies."""

from datetime import date, timedelta
from itertools import product
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from sarthi.analytics import (
    anomaly,
    budget,
    bullwhip,
    conformal,
    esg,
    montecarlo,
    par,
    policy,
    topsis,
    transfer,
    zones,
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


# ── Conformal ────────────────────────────────────────────────────────────────

def test_conformal_band_covers_fresh_data():
    rng = np.random.default_rng(0)
    q = conformal.conformal_quantile(np.abs(rng.normal(size=2000)), alpha=0.1)
    assert np.mean(np.abs(rng.normal(size=20000)) <= q) >= 0.89
    assert conformal.conformal_quantile(np.array([])) == float("inf")
    lo, hi = conformal.band(np.array([1.0, 10.0]), 3.0)
    assert lo.tolist() == [0.0, 7.0] and hi.tolist() == [4.0, 13.0]


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


# ── Zones ────────────────────────────────────────────────────────────────────

CFG = SimpleNamespace(overstock_cover_days=30)


@pytest.mark.parametrize(("metrics", "zone"), [
    ({"doc": 8.0, "lead_eff": 2.4, "stockout_prob": 0.02, "carry_roi": 0.17}, "sweet"),
    ({"doc": 1.6, "lead_eff": 14.4, "stockout_prob": 0.97, "carry_roi": 0.30}, "chaos"),
    ({"doc": 9.0, "lead_eff": 6.0, "stockout_prob": 0.55, "carry_roi": 0.30}, "chaos"),   # risk alone is enough
    ({"doc": 102.0, "lead_eff": 8.4, "stockout_prob": 0.0, "carry_roi": 0.016}, "ghost"),
    ({"doc": 100.0, "lead_eff": 3.6, "stockout_prob": 0.0, "carry_roi": -0.06}, "money"),
])
def test_zone_classification(metrics, zone):
    assert zones.classify(metrics, CFG) == zone


def test_hysteresis_holds_for_a_blip_but_not_for_risk():
    assert zones.with_hysteresis("ghost", None, None) == "ghost"                # first day
    assert zones.with_hysteresis("ghost", "sweet", "sweet") == "sweet"          # one-day blip: stay
    assert zones.with_hysteresis("ghost", "sweet", "ghost") == "ghost"          # second day running: move
    assert zones.with_hysteresis("chaos", "sweet", "sweet") == "chaos"          # into chaos immediately
    assert zones.with_hysteresis("sweet", "chaos", "chaos") == "chaos"          # out of chaos needs two days
    assert zones.with_hysteresis("sweet", "sweet", "ghost") == "sweet"


# ── Supplier scoring ─────────────────────────────────────────────────────────

IDEAL = {name: ideal for name, (ideal, _) in topsis.CRITERIA.items()}
WORST = {name: anti for name, (_, anti) in topsis.CRITERIA.items()}
GOLD = {"price_ratio": 1.02, "tat_mean": 1.4, "tat_cv": 0.10, "on_time": 0.95, "fill_rate": 0.96, "defect_rate": 0.012, "esg": 82, "incentive": 0.02}
BRONZE = {"price_ratio": 0.93, "tat_mean": 4.1, "tat_cv": 0.38, "on_time": 0.70, "fill_rate": 0.82, "defect_rate": 0.058, "esg": 61, "incentive": 0.0}
SILVER = {"price_ratio": 0.98, "tat_mean": 2.6, "tat_cv": 0.22, "on_time": 0.85, "fill_rate": 0.89, "defect_rate": 0.034, "esg": 74, "incentive": 0.015}


def test_topsis_anchors_and_out_of_range_values():
    assert topsis.score([IDEAL, WORST], "Balanced") == [100.0, 0.0]
    beyond = {**IDEAL, "tat_mean": 0.2, "price_ratio": 0.5}
    assert topsis.score([beyond], "Balanced") == [100.0]


def test_topsis_scores_do_not_depend_on_who_else_is_listed():
    two = topsis.score([GOLD, BRONZE], "Balanced")
    three = topsis.score([GOLD, BRONZE, SILVER], "Balanced")
    assert three[:2] == two
    assert two[0] > three[2] > two[1]


def test_topsis_mode_and_urgency_shift_the_ranking():
    for mode in topsis.WEIGHTS:
        assert sum(topsis.weights_for(mode, 0.7).values()) == pytest.approx(1.0)
    gap_calm = np.subtract(*topsis.score([GOLD, BRONZE], "Balanced", urgency=0.0))
    gap_urgent = np.subtract(*topsis.score([GOLD, BRONZE], "Balanced", urgency=1.0))
    assert gap_urgent > gap_calm                                     # speed matters more when a stockout is near
    cash = topsis.score([GOLD, BRONZE], "Cash Flow")
    growth = topsis.score([GOLD, BRONZE], "Growth")
    assert cash[0] - cash[1] < growth[0] - growth[1]                 # the cheap supplier looks better under Cash Flow


def test_topsis_helpers():
    assert topsis.on_time_posterior(0, 0) == 0.5
    assert topsis.on_time_posterior(38, 40) == pytest.approx(39 / 42)
    assert [topsis.tier(s) for s in (91, 85, 84.9, 70, 55)] == ["Gold", "Gold", "Silver", "Silver", "Bronze"]
    partial = topsis.score([{"price_ratio": 1.0}], "Balanced")[0]
    assert 30 < partial < 70                                         # unknown criteria count as neutral


# ── Transfers ────────────────────────────────────────────────────────────────

def test_transfer_fills_deficit_from_surplus():
    moves = transfer.plan_transfers({"DEL": 100}, {"MUM": 60}, {("DEL", "MUM"): 5.0}, {"MUM": 20.0})
    assert moves == [{"from": "DEL", "to": "MUM", "units": 60, "unit_cost": 5.0, "saving": 900.0}]


def test_transfer_respects_limits_and_prefers_the_cheaper_source():
    surplus, deficit = {"DEL": 80, "BLR": 500}, {"MUM": 120, "CHN": 40}
    cost = {("DEL", "MUM"): 6.0, ("DEL", "CHN"): 9.0, ("BLR", "MUM"): 4.0, ("BLR", "CHN"): 3.0}
    moves = transfer.plan_transfers(surplus, deficit, cost, {"MUM": 50.0, "CHN": 50.0})
    shipped_from, shipped_to = {}, {}
    for m in moves:
        shipped_from[m["from"]] = shipped_from.get(m["from"], 0) + m["units"]
        shipped_to[m["to"]] = shipped_to.get(m["to"], 0) + m["units"]
    assert all(shipped_from[s] <= surplus[s] for s in shipped_from)
    assert shipped_to == {"MUM": 120, "CHN": 40}
    assert set(shipped_from) == {"BLR"}  # cheaper to both destinations and has enough


def test_transfer_skips_moves_that_do_not_pay_or_are_tiny():
    assert transfer.plan_transfers({"DEL": 100}, {"MUM": 60}, {("DEL", "MUM"): 25.0}, {"MUM": 20.0}) == []
    assert transfer.plan_transfers({"DEL": 100}, {"MUM": 6}, {("DEL", "MUM"): 5.0}, {"MUM": 20.0}) == []
    assert transfer.plan_transfers({}, {"MUM": 60}, {}, {"MUM": 20.0}) == []
    assert transfer.plan_transfers({"MUM": 100}, {"MUM": 60}, {}, {"MUM": 20.0}) == []


def test_distance_cost_and_site_needs():
    km = transfer.haversine_km(19.08, 72.88, 28.61, 77.21)  # Mumbai -> Delhi
    assert 1100 < km < 1200
    assert transfer.unit_cost(1000) == pytest.approx(6.0)
    surplus, deficit = transfer.site_needs({"A": 640, "B": 20, "C": 110}, {"A": 100, "B": 100, "C": 100}, safety_stock=300)
    assert surplus == {"A": 490} and deficit == {"B": 80} and "C" not in surplus | deficit


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


# ── Shipping options ─────────────────────────────────────────────────────────

def test_unit_weight_parsing():
    weights = [esg.parse_unit_weight(n) for n in ("Amul Butter 500g", "Tata Salt 1kg", "Fortune Oil 5L", "Dettol Liquid 250ml", "Mystery")]
    assert weights == [0.5, 1.0, 5.0, 0.25, 0.5]


def test_shipping_options_order_and_recommendation():
    freight = esg.base_freight(qty=1000, unit_weight_kg=0.5, distance_km=1150)
    opts = esg.options(1000, 0.5, 1150, freight)
    assert [o["mode"] for o in opts] == ["air", "sea", "multimodal"]
    assert [o["days"] for o in opts] == [1, 7, 3]
    assert opts[0]["co2_kg"] > opts[2]["co2_kg"] > opts[1]["co2_kg"]
    assert opts[0]["co2_kg"] == pytest.approx(0.5 * 1150 * 0.60)
    assert opts[0]["freight_cost"] > opts[2]["freight_cost"] > opts[1]["freight_cost"]
    assert esg.recommend(opts) == 1                                   # nothing at risk: cheapest and cleanest wins

    urgent = esg.options(1000, 0.5, 1150, freight, risk=lambda days: (min(1.0, days / 7), 4000.0 * days))
    assert esg.recommend(urgent, "Balanced") == 0                     # waiting costs far more than air freight
    middling = esg.options(1000, 0.5, 1150, freight, risk=lambda days: (0.9, 30000.0) if days > 3 else (0.0, 0.0))
    assert esg.recommend(middling, "Balanced") == 2                   # 3 days is fast enough; air adds cost and carbon


# ── Bullwhip ─────────────────────────────────────────────────────────────────

def test_bullwhip_series():
    flat = bullwhip.series([100.0] * 12)
    assert flat["reorder"] == [None] * 12 and flat["ratio"] == 1.0
    noisy = bullwhip.series([100, 140, 90, 150, 95, 145, 100, 150, 90, 140, 95, 150, 100, 140])
    assert len(noisy["raw"]) == 12 and noisy["labels"][0] == "W1" and noisy["labels"][-1] == "W12"
    assert noisy["ratio"] > 1 > noisy["ratio_smoothed"]               # chasing demand amplifies; smoothing dampens
    step = bullwhip.series([100.0] * 6 + [140.0] * 6)
    assert step["reorder"][6] is not None and step["reorder"][:6] == [None] * 6
    assert bullwhip.series([])["raw"] == []


# ── Phantom inventory ────────────────────────────────────────────────────────

def frames(qty, on_hand, sku="X"):
    days = [date(2026, 9, 1) + timedelta(days=i) for i in range(len(qty))]
    return (pd.DataFrame({"sku_id": sku, "day": days, "qty": qty}),
            pd.DataFrame({"sku_id": sku, "day": days, "on_hand": on_hand}))


def test_phantom_inventory_sold_while_empty():
    sales, stock = frames([5] * 10 + [0, 3, 4, 0], [50] * 10 + [0, 0, 0, 0])
    assert anomaly.phantom_inventory(sales, stock) == [{"sku_id": "X", "kind": "sold_while_empty", "days": 2, "est_units": 7}]
    deliveries = {("X", sales["day"].iloc[11]), ("X", sales["day"].iloc[12])}
    assert anomaly.phantom_inventory(sales, stock, receipt_days=deliveries) == []


def test_phantom_inventory_idle_stock_needs_expected_demand():
    sales, stock = frames([20] * 8 + [0] * 6, [300] * 14)
    assert anomaly.phantom_inventory(sales, stock, expected={"X": 20.0}) == [
        {"sku_id": "X", "kind": "idle_stock", "days": 6, "est_units": 300}
    ]
    assert anomaly.phantom_inventory(sales, stock, expected={"X": 1.0}) == []   # a slow mover can sit idle


def test_an_ordinary_stockout_is_not_an_anomaly():
    sales, stock = frames([20] * 12 + [15, 0], [100] * 12 + [0, 0])  # sold the last units, then nothing
    assert anomaly.phantom_inventory(sales, stock, expected={"X": 20.0}) == []
