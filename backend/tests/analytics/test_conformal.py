"""Split-conformal forecast bands."""


import numpy as np

from sarthi.analytics import (
    conformal,
)

# ── Conformal ────────────────────────────────────────────────────────────────

def test_conformal_band_covers_fresh_data():
    rng = np.random.default_rng(0)
    q = conformal.conformal_quantile(np.abs(rng.normal(size=2000)), alpha=0.1)
    assert np.mean(np.abs(rng.normal(size=20000)) <= q) >= 0.89
    assert conformal.conformal_quantile(np.array([])) == float("inf")
    lo, hi = conformal.band(np.array([1.0, 10.0]), 3.0)
    assert lo.tolist() == [0.0, 7.0] and hi.tolist() == [4.0, 13.0]
