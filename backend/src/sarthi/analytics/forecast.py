"""Demand forecasting: per-SKU model selection by rolling-origin cross-validation.

No single model wins across fast, slow and intermittent SKUs, so four candidates are scored on
out-of-sample error and each SKU keeps its best. Sales on stockout days are corrected first, because
zero sales with an empty shelf says nothing about demand.
"""

import warnings
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

MIN_HISTORY = 90          # days needed before statistical models are fitted
CV_HORIZON, CV_WINDOWS = 14, 6
BLOCK_DAYS, BLOCKS = 30, 12
BACKCAST_MIN_TRAIN = 60
EXTERNAL_WEIGHT = 0.3
CANDIDATES = ("AutoETS", "AutoTheta", "SeasonalNaive", "CrostonOptimized")
FALLBACK_MODEL = "weekday_mean"


@dataclass
class ForecastResult:
    sku_id: str
    model: str                      # chosen model name
    daily: np.ndarray               # next `horizon` days, mean demand per day
    wape: float                     # cross-validated error on 14-day totals (the planning horizon), 0..1+
    wape_daily: float               # the same on single days; high for slow movers because daily counts are noisy
    abs_residuals: np.ndarray       # |y - yhat| from cross-validation, daily
    rel_residuals_14d: np.ndarray   # (sum_y - sum_yhat) / max(sum_yhat, 1) per CV window
    dispersion_k: float             # negative-binomial dispersion; large = Poisson-like
    velocity: float                 # mean unconstrained demand, last 14 days
    velocity_trend: float           # (last 14d mean - prior 14d mean) / prior
    monthly_actual: list[float]     # 12 values, mean daily units sold per 30-day block, oldest first
    monthly_backcast: list[float]   # 12 values, out-of-sample forecast for the same blocks
    last_day: date


def uncensor(qty: np.ndarray, on_hand: np.ndarray) -> np.ndarray:
    """Replace sales on empty-shelf days with the recent same-weekday average when that is higher."""
    y = np.asarray(qty, dtype=float).copy()
    for t in np.flatnonzero(np.asarray(on_hand) == 0):
        earlier = [y[t - 7 * j] for j in range(1, 5) if t - 7 * j >= 0]
        if earlier and y[t] < (reference := float(np.mean(earlier))):
            y[t] = reference
    return y


def weekday_mean_forecast(y: np.ndarray, horizon: int) -> np.ndarray:
    """Each future day = mean of the same weekday over the last four weeks (or the overall mean if history is short)."""
    n = len(y)
    if n < 28:
        return np.full(horizon, float(np.mean(y)) if n else 0.0)
    return np.array([np.mean([y[n - 7 * j + (i % 7)] for j in range(1, 5)]) for i in range(horizon)])


def wape(y: np.ndarray, yhat: np.ndarray) -> float:
    """Weighted absolute percentage error: total absolute miss / total actual."""
    return float(np.abs(y - yhat).sum() / max(y.sum(), 1.0))


def _ets():
    """Exponential smoothing with automatic error and trend choice and additive weekly seasonality.

    Restricting the season to additive halves fitting time; on this kind of data the full search
    (which adds multiplicative seasons) scored the same within 0.01 WAPE.
    """
    from statsforecast.models import AutoETS

    return AutoETS(season_length=7, model="ZZA")


def dispersion(y: np.ndarray, yhat: np.ndarray) -> float:
    """Method of moments for var = mu + mu^2 / k."""
    var, mu = float(np.mean((y - yhat) ** 2)), float(np.mean(yhat))
    k = mu * mu / (var - mu) if var > mu else 1e6
    return float(np.clip(k, 0.5, 1e6))


def month_labels(today: date) -> list[str]:
    """Three-letter month of each 30-day block's midpoint, oldest first."""
    return [(today - timedelta(days=BLOCK_DAYS * (BLOCKS - 1 - i) + BLOCK_DAYS // 2)).strftime("%b") for i in range(BLOCKS)]


def block_means(values: np.ndarray) -> list[float]:
    """Mean per 30-day block for the last 12 blocks; short histories are padded with their first block."""
    values = np.asarray(values, dtype=float)
    usable = min(len(values) // BLOCK_DAYS, BLOCKS)
    if usable == 0:
        return [float(np.mean(values)) if len(values) else 0.0] * BLOCKS
    means = values[len(values) - usable * BLOCK_DAYS:].reshape(usable, BLOCK_DAYS).mean(axis=1)
    return [float(means[0])] * (BLOCKS - usable) + [float(m) for m in means]


def _trend(y: np.ndarray) -> tuple[float, float]:
    last = float(np.mean(y[-14:])) if len(y) else 0.0
    prior = float(np.mean(y[-28:-14])) if len(y) >= 28 else last
    return last, (last - prior) / max(prior, 0.01)


def _fallback(sku_id: str, y: np.ndarray, sold: np.ndarray, horizon: int, last_day: date) -> ForecastResult:
    daily = weekday_mean_forecast(y, horizon)
    recent = y[-28:] if len(y) else np.zeros(1)
    fitted = np.full(len(recent), float(np.mean(recent)))
    velocity, trend = _trend(y)
    actual = block_means(sold)
    return ForecastResult(
        sku_id=sku_id, model=FALLBACK_MODEL, daily=np.clip(daily, 0, None), wape=0.5, wape_daily=0.5,
        abs_residuals=np.abs(recent - fitted), rel_residuals_14d=np.array([-0.5, 0.0, 0.5]),
        dispersion_k=dispersion(recent, fitted), velocity=velocity, velocity_trend=trend,
        monthly_actual=actual, monthly_backcast=list(actual), last_day=last_day,
    )


def _statistical(series: dict[str, dict], horizon: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame | None]:
    """Run cross-validation, the forecast and the monthly back-cast for all eligible SKUs at once."""
    from statsforecast import StatsForecast
    from statsforecast.models import AutoTheta, CrostonOptimized, SeasonalNaive

    df = pd.concat(
        [pd.DataFrame({"unique_id": sku_id, "ds": pd.to_datetime(s["days"]), "y": s["y"]}) for sku_id, s in series.items()],
        ignore_index=True,
    )
    models = [_ets(), AutoTheta(season_length=7), SeasonalNaive(season_length=7), CrostonOptimized()]
    sf = StatsForecast(models=models, freq="D", n_jobs=1)
    cv = sf.cross_validation(df=df, h=CV_HORIZON, n_windows=CV_WINDOWS, step_size=CV_HORIZON)
    fc = sf.forecast(df=df, h=horizon)

    shortest = min(len(s["y"]) for s in series.values())
    windows = min(BLOCKS, (shortest - BACKCAST_MIN_TRAIN) // BLOCK_DAYS)
    back = None
    if windows >= 1:
        # Refitted at every block start, so each block is a genuine out-of-sample forecast. A lighter model
        # (no trend search) keeps 12 refits per SKU fast; `refit=False` was tried and produced flat,
        # badly-off back-casts because the level is not updated between blocks.
        from statsforecast.models import AutoETS

        sf_m = StatsForecast(models=[AutoETS(season_length=7, model="ZNA")], freq="D", n_jobs=1)
        back = sf_m.cross_validation(df=df, h=BLOCK_DAYS, n_windows=windows, step_size=BLOCK_DAYS)

    def tidy(frame: pd.DataFrame | None) -> pd.DataFrame | None:
        if frame is not None and "unique_id" not in frame.columns:
            frame = frame.reset_index()
        return frame

    return tidy(cv), tidy(fc), tidy(back)


def forecast_all(sales_daily: pd.DataFrame, stock_daily: pd.DataFrame, horizon: int = 30,
                 external: pd.DataFrame | None = None) -> dict[str, ForecastResult]:
    """Forecast every SKU.

    `sales_daily`: sku_id, day, qty (one row per SKU-day, zeros filled). `stock_daily`: sku_id, day, on_hand.
    `external`: optional sku_id, forecast_date, predicted_demand to blend in at 30 % weight.
    """
    series: dict[str, dict] = {}
    for sku_id, g in sales_daily.sort_values("day").groupby("sku_id", sort=True):
        held = stock_daily[stock_daily["sku_id"] == sku_id].set_index("day")["on_hand"].reindex(g["day"]).to_numpy(dtype=float)
        sold = g["qty"].to_numpy(dtype=float)
        series[sku_id] = {"days": list(g["day"]), "sold": sold, "y": uncensor(sold, held)}

    eligible = {k: v for k, v in series.items() if len(v["y"]) >= MIN_HISTORY}
    cv = fc = back = None
    if eligible:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                cv, fc, back = _statistical(eligible, horizon)
        except Exception:  # noqa: BLE001  any library failure degrades every SKU to the simple forecast
            cv = fc = back = None

    results: dict[str, ForecastResult] = {}
    for sku_id, s in series.items():
        y, sold, last_day = s["y"], s["sold"], s["days"][-1]
        if cv is None or sku_id not in eligible:
            results[sku_id] = _fallback(sku_id, y, sold, horizon, last_day)
            continue
        cv_s = cv[cv["unique_id"] == sku_id]
        actual = cv_s["y"].to_numpy(dtype=float)
        scores = {m: wape(actual, np.clip(cv_s[m].to_numpy(dtype=float), 0, None)) for m in CANDIDATES}
        best = min(scores, key=scores.get)
        fitted = np.clip(cv_s[best].to_numpy(dtype=float), 0, None)
        per_window = pd.DataFrame({"cutoff": cv_s["cutoff"].to_numpy(), "y": actual, "f": fitted}).groupby("cutoff").sum()
        daily = np.clip(fc[fc["unique_id"] == sku_id][best].to_numpy(dtype=float), 0, None)

        monthly_actual = block_means(sold)
        monthly_backcast = list(monthly_actual)
        if back is not None:
            b = back[back["unique_id"] == sku_id]
            means = (
                pd.DataFrame({"cutoff": b["cutoff"].to_numpy(), "f": np.clip(b["AutoETS"].to_numpy(dtype=float), 0, None)})
                .groupby("cutoff")["f"].mean().sort_index().to_list()
            )
            monthly_backcast = monthly_backcast[: BLOCKS - len(means)] + [float(m) for m in means]

        velocity, trend = _trend(y)
        results[sku_id] = ForecastResult(
            sku_id=sku_id, model=best, daily=daily,
            wape=float(np.abs(per_window["y"] - per_window["f"]).sum() / max(per_window["y"].sum(), 1.0)),
            wape_daily=scores[best],
            abs_residuals=np.abs(actual - fitted),
            rel_residuals_14d=((per_window["y"] - per_window["f"]) / np.maximum(per_window["f"], 1.0)).to_numpy(),
            dispersion_k=dispersion(actual, fitted), velocity=velocity, velocity_trend=trend,
            monthly_actual=monthly_actual, monthly_backcast=monthly_backcast, last_day=last_day,
        )

    if external is not None and not external.empty:
        _blend_external(results, external)
    return results


def _blend_external(results: dict[str, ForecastResult], external: pd.DataFrame) -> None:
    """Mix an uploaded third-party forecast into the matching future days."""
    ext = external.copy()
    ext["forecast_date"] = pd.to_datetime(ext["forecast_date"]).dt.date
    for sku_id, group in ext.groupby("sku_id"):
        result = results.get(str(sku_id))
        if result is None:
            continue
        by_day = dict(zip(group["forecast_date"], group["predicted_demand"].astype(float), strict=True))
        for i in range(len(result.daily)):
            value = by_day.get(result.last_day + timedelta(days=i + 1))
            if value is not None:
                result.daily[i] = (1 - EXTERNAL_WEIGHT) * result.daily[i] + EXTERNAL_WEIGHT * max(0.0, value)
