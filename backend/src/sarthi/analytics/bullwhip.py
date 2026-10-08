"""Bullwhip effect: how much reorder signals amplify swings in demand, raw vs. smoothed."""

import numpy as np

WEEKS = 12
REORDER_SHIFT = 0.03  # a new reorder signal fires when the smoothed level has moved this much since the last one


def series(sales_weekly: list[float], alpha: float = 0.3) -> dict:
    """Last 12 weeks of demand, its exponentially smoothed version, and the weeks a reorder would trigger.

    `ratio` > 1 means naive ordering (chasing each week's change) swings more than demand itself;
    `ratio_smoothed` < 1 means the smoothed signal swings less.
    """
    raw = [float(x) for x in sales_weekly[-WEEKS:]]
    if not raw:
        return {"labels": [], "raw": [], "smoothed": [], "reorder": [], "ratio": 1.0, "ratio_smoothed": 1.0}
    smoothed = [raw[0]]
    for value in raw[1:]:
        smoothed.append(alpha * value + (1 - alpha) * smoothed[-1])

    reorder: list[int | None] = [None] * len(raw)
    anchor = smoothed[0]
    for i in range(1, len(raw)):
        if anchor > 0 and abs(smoothed[i] - anchor) / anchor > REORDER_SHIFT:
            reorder[i] = round(smoothed[i])
            anchor = smoothed[i]

    variance = float(np.var(raw))
    naive_orders = [raw[0]] + [raw[t] + (raw[t] - raw[t - 1]) for t in range(1, len(raw))]
    ratio = float(np.var(naive_orders)) / variance if variance > 0 else 1.0
    ratio_smoothed = float(np.var(smoothed)) / variance if variance > 0 else 1.0
    return {
        "labels": [f"W{i + 1}" for i in range(len(raw))],
        "raw": [round(x) for x in raw],
        "smoothed": [round(x) for x in smoothed],
        "reorder": reorder,
        "ratio": round(ratio, 2),
        "ratio_smoothed": round(ratio_smoothed, 2),
    }
