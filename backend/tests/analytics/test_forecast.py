"""Forecasting: loading the history, the building blocks, model choice on the seeded data, the bullwhip view."""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from sarthi.analytics import (
    bullwhip,
    data,
    forecast,
)
from sarthi.seed import catalog as cat
from tests.conftest import TEST_TODAY

# ── Loading ──────────────────────────────────────────────────────────────────

def test_store_frames_are_complete(store_data):
    sales, stock = store_data
    assert len(sales) == len(stock) == 10 * 540
    assert sales["qty"].min() >= 0 and stock["on_hand"].min() >= 0
    assert sales["day"].max() == TEST_TODAY
    assert set(sales.columns) == {"sku_id", "day", "qty"} and set(stock.columns) == {"sku_id", "day", "on_hand"}


def test_store_frames_as_of_truncates(seeded_db):
    cutoff = TEST_TODAY - timedelta(days=30)
    sales, stock = data.store_frames(as_of=cutoff)
    assert sales["day"].max() == cutoff and stock["day"].max() == cutoff
    assert len(sales) == 10 * 510


def test_weekly_totals(store_data):
    weeks = data.weekly_totals(store_data[0])
    assert len(weeks) == 12 and all(w > 0 for w in weeks)
    assert data.weekly_totals(pd.DataFrame(columns=["sku_id", "day", "qty"])) == []

# ── Forecast building blocks ─────────────────────────────────────────────────

def test_uncensor_lifts_empty_shelf_days_only():
    qty = np.array([10.0] * 28 + [0.0, 12.0, 4.0])
    on_hand = np.array([50.0] * 28 + [0.0, 30.0, 0.0])
    y = forecast.uncensor(qty, on_hand)
    assert y[28] == 10.0          # empty shelf, sales below normal -> lifted to the same-weekday average
    assert y[29] == 12.0          # in stock -> untouched
    assert y[30] == 10.0          # sold the last 4 and ran out -> lifted
    high = forecast.uncensor(np.array([10.0] * 28 + [25.0]), np.array([50.0] * 28 + [0.0]))
    assert high[28] == 25.0       # sold more than normal before running out -> keep the real figure
    unknown = forecast.uncensor(np.array([10.0] * 28 + [0.0]), np.array([np.nan] * 29))
    assert unknown[28] == 0.0     # unknown stock is not the same as zero stock


def test_weekday_mean_forecast_and_helpers():
    y = np.tile(np.arange(1.0, 8.0), 6)                       # weekly pattern 1..7
    assert forecast.weekday_mean_forecast(y, 9).tolist() == [1, 2, 3, 4, 5, 6, 7, 1, 2]
    assert forecast.weekday_mean_forecast(np.array([4.0, 6.0]), 3).tolist() == [5, 5, 5]
    assert forecast.wape(np.array([10.0, 10.0]), np.array([8.0, 13.0])) == pytest.approx(0.25)
    assert forecast.dispersion(np.array([10.0, 10.0]), np.array([10.0, 10.0])) == 1e6        # no over-dispersion
    noisy = np.random.default_rng(0).negative_binomial(4, 4 / (4 + 30), 5000).astype(float)
    assert forecast.dispersion(noisy, np.full(5000, 30.0)) == pytest.approx(4, rel=0.2)
    assert forecast.block_means(np.arange(60.0)) == [14.5] * 11 + [44.5]


def test_month_labels():
    labels = forecast.month_labels(date(2026, 10, 1))
    assert len(labels) == 12
    assert labels[-1] == "Sep" and labels[0] == "Oct"         # block midpoints, oldest first

# ── Forecast on seeded data ──────────────────────────────────────────────────

def test_every_sku_gets_a_usable_forecast(forecasts):
    results, _ = forecasts
    assert set(results) == {s["id"] for s in cat.SKUS}
    for sku_id, r in results.items():
        assert r.model in forecast.CANDIDATES, sku_id
        assert len(r.daily) == 30 and np.all(r.daily >= 0) and r.daily.sum() > 0, sku_id
        assert r.wape < 0.6, sku_id
        assert 0 < r.wape_daily < 1.0, sku_id
        assert 0.5 <= r.dispersion_k <= 1e6, sku_id
        assert len(r.abs_residuals) == 84 and len(r.rel_residuals_14d) == 6, sku_id
        assert len(r.monthly_actual) == len(r.monthly_backcast) == 12, sku_id
        assert r.last_day == TEST_TODAY, sku_id


def test_forecast_tracks_recent_demand(forecasts):
    results, _ = forecasts
    for sku in cat.SKUS:
        r = results[sku["id"]]
        assert r.daily.mean() == pytest.approx(r.velocity, rel=0.45), sku["id"]
        recent_actual, recent_backcast = np.mean(r.monthly_actual[-3:]), np.mean(r.monthly_backcast[-3:])
        assert recent_backcast == pytest.approx(recent_actual, rel=0.5), sku["id"]


def test_fast_movers_forecast_better_than_slow_movers(forecasts):
    """Daily error is dominated by count noise for slow sellers; error on 14-day totals is what planning uses."""
    results, _ = forecasts
    fast = np.mean([results[s["id"]].wape_daily for s in cat.SKUS if s["vel"] >= 30])
    slow = np.mean([results[s["id"]].wape_daily for s in cat.SKUS if s["vel"] < 15])
    assert fast < slow
    assert all(r.wape <= r.wape_daily for r in results.values())


def test_forecast_time_is_measured(forecasts):
    """Speed is not asserted here: it depends on machine load and power mode (a laptop on battery ran
    this 4-5x slower). The validator's --perf option reports it against the 60-second budget instead."""
    _, seconds = forecasts
    assert seconds > 0


def test_short_history_uses_the_simple_forecast():
    days = [date(2026, 9, 1) + timedelta(days=i) for i in range(40)]
    sales = pd.DataFrame({"sku_id": "NEW", "day": days, "qty": [5.0, 7.0] * 20})
    stock = pd.DataFrame({"sku_id": "NEW", "day": days, "on_hand": [100.0] * 40})
    result = forecast.forecast_all(sales, stock)["NEW"]
    assert result.model == forecast.FALLBACK_MODEL and result.wape == 0.5
    assert len(result.daily) == 30 and result.daily.mean() == pytest.approx(6.0)
    assert len(result.monthly_actual) == 12 and len(result.rel_residuals_14d) == 3


def test_external_forecast_is_blended_at_30_percent():
    days = [date(2026, 9, 1) + timedelta(days=i) for i in range(40)]
    sales = pd.DataFrame({"sku_id": "NEW", "day": days, "qty": [10.0] * 40})
    stock = pd.DataFrame({"sku_id": "NEW", "day": days, "on_hand": [100.0] * 40})
    external = pd.DataFrame({"sku_id": ["NEW", "OTHER"], "forecast_date": [(days[-1] + timedelta(days=1)).isoformat()] * 2,
                             "predicted_demand": [20.0, 99.0]})
    daily = forecast.forecast_all(sales, stock, external=external)["NEW"].daily
    assert daily[0] == pytest.approx(0.7 * 10 + 0.3 * 20)
    assert daily[1] == pytest.approx(10.0)

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
