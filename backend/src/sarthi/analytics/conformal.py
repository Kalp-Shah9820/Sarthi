"""Split conformal prediction: distribution-free forecast intervals from out-of-sample residuals."""

import numpy as np


def conformal_quantile(abs_residuals: np.ndarray, alpha: float = 0.1) -> float:
    """Half-width q such that |actual - forecast| <= q with probability >= 1 - alpha on new data."""
    n = len(abs_residuals)
    if n == 0:
        return float("inf")
    level = min(1.0, np.ceil((n + 1) * (1 - alpha)) / n)
    return float(np.quantile(abs_residuals, level, method="higher"))


def band(daily: np.ndarray, q: float) -> tuple[np.ndarray, np.ndarray]:
    daily = np.asarray(daily, dtype=float)
    return np.clip(daily - q, 0, None), daily + q
