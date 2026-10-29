"""Basket rules and cannibalization, on toy inputs and on the seeded database."""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from sqlmodel import select

from sarthi import validate
from sarthi.analytics import basket, cannibal, data
from sarthi.db import session
from sarthi.models import Sku
from sarthi.seed import catalog as cat


@pytest.fixture(scope="module")
def sku_info(seeded_db):
    with session() as s:
        skus = s.exec(select(Sku)).all()
    return {
        "names": {k.id: k.name for k in skus},
        "categories": {k.id: k.category for k in skus},
        "aisles": {k.id: k.aisle_id for k in skus},
    }

# ── Basket analysis ──────────────────────────────────────────────────────────

TOY = pd.DataFrame(
    [("b1", "A"), ("b1", "B"), ("b2", "A"), ("b2", "B"), ("b3", "A"), ("b3", "B"), ("b4", "A"), ("b5", "C"), ("b6", "C")],
    columns=["basket_id", "sku_id"],
)


def test_rule_metrics_match_hand_calculation():
    rules = {(tuple(r["antecedent_ids"]), r["consequent_id"]): r for r in basket.mine_rules(TOY, {"A": "Apple", "B": "Bread"})}
    a_to_b, b_to_a = rules[(("A",), "B")], rules[(("B",), "A")]
    # 6 baskets; A in 4, B in 3, both in 3
    assert (a_to_b["support"], a_to_b["confidence"], a_to_b["lift"]) == (0.5, 0.75, 1.5)
    assert (b_to_a["support"], b_to_a["confidence"], b_to_a["lift"]) == (0.5, 1.0, 1.5)
    assert a_to_b["antecedent"] == ["Apple"] and a_to_b["consequent"] == "Bread"
    assert all("C" not in k[0] and k[1] != "C" for k in rules)       # C is bought alone: no rule
    assert basket.mine_rules(TOY.iloc[:0], {}) == []


def test_aisle_views():
    rules = basket.mine_rules(TOY, {})
    pairs = basket.aisle_pairs(rules, {"A": "X", "B": "Y", "C": "Z"})
    assert pairs == [{"from": "y", "to": "x", "strength": 100}, {"from": "x", "to": "y", "strength": 75}]
    assert basket.aisle_connections(pairs) == {"X": ["Y"], "Y": ["X"]}
    assert basket.aisle_pairs(rules, {"A": "X", "B": "X"}) == []      # same aisle is not a connection
    heat = basket.aisle_heat(TOY, {"A": "X", "B": "Y", "C": "Z"})
    assert heat == {"X": 0.92, "Y": 0.69, "Z": 0.46}


def test_seeded_baskets_recover_the_catalogue_rules(seeded_db, sku_info):
    rules = basket.mine_rules(data.basket_frame(), sku_info["names"])
    assert 1 <= len(rules) <= 8
    assert rules == sorted(rules, key=lambda r: -r["confidence"])
    found = {(tuple(r["antecedent_ids"]), r["consequent_id"]): r["confidence"] for r in rules}
    assert found[(("SKU001",), "SKU008")] == pytest.approx(0.72, abs=0.1)   # Amul Butter -> Parle-G, as seeded
    assert all(r["lift"] > 1.1 and 0 < r["support"] <= 1 for r in rules)
    pairs = basket.aisle_pairs(rules, sku_info["aisles"])
    assert {"from": "a", "to": "b"}.items() <= next(p for p in pairs if p["from"] == "a").items()
    heat = basket.aisle_heat(data.basket_frame(), sku_info["aisles"])
    assert max(heat.values()) == 0.92 and max(heat, key=lambda aisle: heat[aisle]) == "C"    # snacks is the busiest aisle

# ── Cannibalization ──────────────────────────────────────────────────────────

def synthetic_pair(uplift: float):
    rng = np.random.default_rng(2)
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(200)]
    empty = np.zeros(200, dtype=bool)
    empty[[40, 41, 42, 90, 91, 130, 131, 132, 170, 171]] = True
    rising = rng.poisson(50, 200) + np.where(empty, uplift * 50, 0)
    falling = np.where(empty, 0, rng.poisson(80, 200))
    sales = pd.concat([pd.DataFrame({"sku_id": "R", "day": days, "qty": rising.astype(float)}),
                       pd.DataFrame({"sku_id": "F", "day": days, "qty": falling.astype(float)})])
    stock = pd.concat([pd.DataFrame({"sku_id": "R", "day": days, "on_hand": 500.0}),
                       pd.DataFrame({"sku_id": "F", "day": days, "on_hand": np.where(empty, 0.0, 300.0)})])
    return sales, stock


def test_cannibalization_detected_only_when_sales_really_shift():
    cats = {"R": "Snacks", "F": "Snacks"}
    found = cannibal.detect(*synthetic_pair(uplift=0.8), cats, {"R": "Rising", "F": "Falling"})
    assert len(found) == 1
    assert (found[0]["rising"], found[0]["falling"], found[0]["rName"]) == ("R", "F", "Rising")
    assert found[0]["correlation"] < -0.3 and found[0]["uplift"] == pytest.approx(0.8, abs=0.15)
    assert found[0]["event_days"] == 10
    assert cannibal.detect(*synthetic_pair(uplift=0.0), cats) == []                       # no shift -> nothing
    assert cannibal.detect(*synthetic_pair(uplift=0.8), {"R": "Snacks", "F": "Dairy"}) == []  # different categories


def test_seeded_cannibalization_pair_is_found(store_data, sku_info):
    sales, stock = store_data
    found = cannibal.detect(sales, stock, sku_info["categories"], sku_info["names"])
    pairs = {(f["rising"], f["falling"]) for f in found}
    assert ("SKU010", "SKU003") in pairs          # Haldirams gains when Lays is out, as the generator built it
    assert ("SKU003", "SKU010") not in pairs      # and not the other way round
    assert pairs <= set(cat.CANNIBALIZATION)      # nothing spurious
    assert all(f["correlation"] < 0 and f["uplift"] >= 0.15 for f in found)


def test_validator_analytics_check(seeded_db):
    result = validate.check_analytics()
    assert result.status == validate.PASS, result.detail
