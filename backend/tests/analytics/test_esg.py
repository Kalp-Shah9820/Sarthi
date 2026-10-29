"""Shipping options: speed, freight and carbon per mode."""


import pytest

from sarthi.analytics import (
    esg,
)

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
