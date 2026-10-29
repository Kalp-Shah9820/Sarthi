"""The policy replay and the risk calibration, on the seeded history."""

import time

import numpy as np
import pytest
from typer.testing import CliRunner

from sarthi.analytics import montecarlo
from sarthi.cli import app as cli_app
from sarthi.eval import backtest as bt
from tests.conftest import TEST_SEED

DAYS = 120
runner = CliRunner()


@pytest.fixture(scope="module")
def result(seeded_db):
    started = time.perf_counter()
    out = bt.run_backtest(DAYS, truth_seed=TEST_SEED)
    out["seconds"] = time.perf_counter() - started
    return out


def test_backtest_runs_quickly_on_every_product(result):
    assert result["seconds"] < 30
    assert result["days"] == DAYS and len(result["skus"]) == 10
    assert (result["end"] - result["start"]).days == DAYS - 1
    assert result["demand_source"] == "generator"          # the seeded sales are untouched, so true demand is known


def test_metrics_are_sane_and_units_are_conserved(result):
    for row in result["skus"]:
        for name in bt.POLICIES:
            m = row[name]
            assert 0 < m["fill_rate"] <= 1, (row["id"], name)
            assert m["opening"] + m["received"] - m["sold"] == m["closing"], (row["id"], name)      # nothing appears or vanishes
            assert m["sold"] + m["lost_units"] == m["demand"]
            assert 0 <= m["stockout_days"] <= DAYS and m["closing"] >= 0 and m["holding_cost"] >= 0
            assert m["net"] == pytest.approx(-m["lost_margin"] - m["holding_cost"], abs=0.02)
            assert m["orders"] >= 0 and m["ordered_units"] >= 0 and m["on_order_at_end"] >= 0
        assert row["naive"]["demand"] == row["sarthi"]["demand"] and row["naive"]["opening"] == row["sarthi"]["opening"]
    for name in bt.POLICIES:
        total = result["totals"][name]
        assert total["demand"] == sum(r[name]["demand"] for r in result["skus"])
        assert total["fill_rate"] == pytest.approx(total["sold"] / total["demand"])


def test_both_policies_face_the_same_demand_and_lead_times(seeded_db):
    series, source = bt.load_series(60, truth_seed=TEST_SEED)
    again, _ = bt.load_series(60, truth_seed=TEST_SEED)
    assert source == "generator"
    for a, b in zip(series, again, strict=True):
        assert np.array_equal(a.lead_draws, b.lead_draws) and np.array_equal(a.demand, b.demand)
        assert (a.lead_draws >= 1).all() and len(a.lead_draws) == 60
        assert (a.demand[-60:] >= a.sold[-60:]).all()       # true demand is at least what was sold
    assert any((s.demand > s.sold).any() for s in series)   # and exceeds it on empty-shelf days


def test_replay_is_deterministic(result):
    again = bt.run_backtest(DAYS, truth_seed=TEST_SEED)
    assert again["skus"] == result["skus"]


def test_sarthi_beats_the_naive_rule_on_the_seeded_history(result):
    """A measured outcome on fixed data, kept as a guard: a change that makes the policy worse should be noticed."""
    naive, sarthi = result["totals"]["naive"], result["totals"]["sarthi"]
    assert sarthi["fill_rate"] > naive["fill_rate"]
    assert sarthi["net"] > naive["net"]
    assert sarthi["stockout_days"] < naive["stockout_days"]


def test_without_the_generator_demand_is_estimated_from_sales(seeded_db):
    out = bt.run_backtest(30, truth_seed=TEST_SEED + 1)     # a seed that did not produce this database
    assert out["demand_source"] == "estimated" and len(out["skus"]) == 10
    assert all(0 < r[p]["fill_rate"] <= 1 for r in out["skus"] for p in bt.POLICIES)


def test_backtest_rejects_windows_it_cannot_replay(seeded_db):
    with pytest.raises(ValueError, match="at least 14 days"):
        bt.run_backtest(5)
    with pytest.raises(ValueError, match="days of history"):
        bt.run_backtest(5000)


def test_calibration_buckets(seeded_db):
    risk = bt.calibration(DAYS)
    assert risk["points"] > 300 and sum(b["n"] for b in risk["buckets"]) == risk["points"]
    assert [b["range"] for b in risk["buckets"]] == ["0–20%", "20–40%", "40–60%", "60–80%", "80–100%"]
    for b in risk["buckets"]:
        if b["n"]:
            assert 0 <= b["observed"] <= 1 and 0 <= b["predicted"] <= 1
    low = risk["buckets"][0]
    assert low["n"] > 200 and low["observed"] < 0.10        # days called safe were, in fact, almost always safe
    assert 0 <= risk["mean_gap"] < 0.15


def test_report_shows_both_tables(result, seeded_db):
    text = bt.format_report(result, bt.calibration(DAYS))
    assert "Backtest: 120 days" in text and "TOTAL" in text and "Calibration:" in text and "Came true" in text
    assert text.count("naive") >= 11 and text.count("sarthi") >= 11
    assert "Products where the naive rule did better:" in text
    assert "Calibration" not in bt.format_report(result)


def test_backtest_command(seeded_db):
    out = runner.invoke(cli_app, ["backtest", "--days", "30", "--no-calibration"])
    assert out.exit_code == 0, out.output
    assert "Backtest: 30 days" in out.output and "TOTAL" in out.output
    bad = runner.invoke(cli_app, ["backtest", "--days", "3"])
    assert bad.exit_code == 1 and "Cannot run the backtest" in bad.output


def test_a_delivery_expected_tomorrow_is_on_the_shelf_tomorrow():
    assert montecarlo.arrival_index([(1, 50.0), (3, 20.0), (0, 5.0)]) == [(0, 50.0), (2, 20.0), (0, 5.0)]
    mu = np.full(30, 20.0)
    lead = np.array([3.0])
    late = montecarlo.simulate(0, [(1, 500.0)], mu, 8.0, lead, 400, 1)                      # arrives a day after it is needed
    on_time = montecarlo.simulate(0, montecarlo.arrival_index([(1, 500.0)]), mu, 8.0, lead, 400, 1)
    assert late.stockout_prob > 0.95 and on_time.stockout_prob < 0.05
