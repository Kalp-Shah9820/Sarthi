"""Monte Carlo stockout simulation: over-dispersed daily demand against uncertain supplier lead time."""

import zlib
from dataclasses import dataclass

import numpy as np

HISTOGRAM_BINS = 20
HISTOGRAM_PEAK = 60       # tallest bar, to match the chart's visual range
HIGHLIGHT_FROM_PCT = 60   # bins from here up are drawn as critical


@dataclass
class McResult:
    stockout_prob: float       # 0..1, chance stock runs out before a new order could arrive
    expected_lost_units: float
    lost_frac: np.ndarray      # per path, share of lead-time demand unmet (0..1)
    ltd: np.ndarray            # per path, demand during the lead time
    ending: np.ndarray         # per path, stock when the order would arrive (can be < 0)
    sigma_daily: float


def sku_seed(run_seed: int, sku_id: str) -> int:
    """Same SKU and run seed -> same random paths, so a what-if differs from baseline only by its scenario."""
    return zlib.crc32(f"{run_seed}:{sku_id}".encode())


def simulate(on_hand: float, receipts: list[tuple[int, float]], mu: np.ndarray, k: float,
             lead_samples: np.ndarray, n_paths: int, seed: int,
             demand_mult: float = 1.0, lead_mult: float = 1.0) -> McResult:
    """`mu`: mean demand per day from today; `receipts`: (day index, qty) already on order, day 0 = today."""
    rng = np.random.default_rng(seed)
    horizon = len(mu)
    m = np.maximum(np.asarray(mu, dtype=float) * demand_mult, 1e-9)
    demand = rng.negative_binomial(k, k / (k + m), size=(n_paths, horizon))        # mean m, var m + m^2/k
    lead = np.clip(np.ceil(rng.choice(np.asarray(lead_samples, dtype=float), size=n_paths) * lead_mult), 1, horizon)
    lead = lead.astype(int)
    inflow = np.zeros(horizon)
    for day, qty in receipts:
        if 0 <= day < horizon:
            inflow[day] += qty
    level = on_hand + np.cumsum(inflow)[None, :] - np.cumsum(demand, axis=1)       # end-of-day stock
    window = np.arange(horizon)[None, :] < lead[:, None]                           # days before arrival
    stockout = np.where(window, level, np.inf).min(axis=1) < 0
    ltd = (demand * window).sum(axis=1)
    avail = on_hand + (inflow[None, :] * window).sum(axis=1)
    lost = np.maximum(ltd - avail, 0)
    return McResult(
        stockout_prob=float(stockout.mean()),
        expected_lost_units=float(lost.mean()),
        lost_frac=lost / np.maximum(ltd, 1),
        ltd=ltd,
        ending=avail - ltd,
        sigma_daily=float(demand[:, : min(horizon, 7)].std()),
    )


def histogram(lost_frac: np.ndarray) -> list[dict]:
    """20 bins of 5 %: how many simulated futures lose that share of lead-time demand."""
    pct = np.clip(np.asarray(lost_frac, dtype=float) * 100, 0, 99.999)
    counts, _ = np.histogram(pct, bins=HISTOGRAM_BINS, range=(0, 100))
    scale = HISTOGRAM_PEAK / counts.max() if counts.max() > 0 else 0
    width = 100 // HISTOGRAM_BINS
    return [
        {"range": f"{i * width}–{(i + 1) * width}%", "count": int(np.ceil(c * scale)),
         "highlight": i * width >= HIGHLIGHT_FROM_PCT}
        for i, c in enumerate(counts)
    ]


def p95_stock(ending: np.ndarray) -> int:
    """Stock level exceeded in 95 % of simulated futures when the next order arrives."""
    return max(0, round(float(np.quantile(ending, 0.05))))
