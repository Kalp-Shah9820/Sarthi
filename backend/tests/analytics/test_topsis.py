"""Supplier scoring with fixed ideal and anti-ideal points."""


import pytest

from sarthi.analytics import (
    topsis,
)

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
    gold_calm, bronze_calm = topsis.score([GOLD, BRONZE], "Balanced", urgency=0.0)
    gold_urgent, bronze_urgent = topsis.score([GOLD, BRONZE], "Balanced", urgency=1.0)
    gap_calm, gap_urgent = gold_calm - bronze_calm, gold_urgent - bronze_urgent
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
