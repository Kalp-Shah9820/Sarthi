"""The What-If sliders: stockout risk for a hypothetical product.

Same model as the agents' Monte Carlo (`montecarlo.simulate`): negative-binomial daily demand and sampled
lead times. With nothing on order, stock only falls, so a path runs out exactly when demand over the lead
time exceeds the stock. That lets one set of simulated futures answer every lead time on the sensitivity
chart, which keeps the endpoint fast enough to call on each slider move.
"""

import numpy as np

from sarthi.analytics import policy

HORIZON_DAYS = 30
LEAD_CV = 0.25             # lead times vary by about a quarter around their mean
LEAD_DRAWS = 200
LEAD_SEED, DEMAND_SEED = 11, 7      # fixed, so moving one slider changes only what that slider means
PATHS = 2000
SERVICE_LEVEL = 0.95
SENSITIVITY_DAYS = 15


def lead_samples(lead: float) -> np.ndarray:
    """Plausible lead times in days around a mean of `lead`."""
    shape = 1 / LEAD_CV**2
    return np.random.default_rng(LEAD_SEED).gamma(shape, 1 / shape, LEAD_DRAWS) * max(lead, 0.1)


def simulate(lead: float, demand: float, stock: float, margin: float, k: float, ref_price: float) -> dict:
    """{risk %, par ₹, safetyStock units, sensitivity: risk at lead times of 1..15 days}."""
    rng = np.random.default_rng(DEMAND_SEED)
    mean = max(float(demand), 1e-9)
    cumulative = np.cumsum(rng.negative_binomial(k, k / (k + mean), size=(PATHS, HORIZON_DAYS)), axis=1)
    picks = rng.choice(LEAD_DRAWS, size=PATHS)          # which lead-time draw each future gets
    rows = np.arange(PATHS)

    def lead_time_demand(mean_lead: float) -> np.ndarray:
        days = np.clip(np.ceil(lead_samples(mean_lead)[picks]), 1, HORIZON_DAYS).astype(int)
        return cumulative[rows, days - 1]

    ltd = lead_time_demand(lead)
    safety, _ = policy.reorder_point(ltd, SERVICE_LEVEL)
    return {
        "risk": round(100 * float((ltd > stock).mean())),
        "par": round(float(np.maximum(ltd - stock, 0).mean()) * (margin / 100) * ref_price),
        "safetyStock": safety,
        "sensitivity": [{"lt": f"{days}d", "risk": round(100 * float((lead_time_demand(days) > stock).mean()))}
                        for days in range(1, SENSITIVITY_DAYS + 1)],
    }
