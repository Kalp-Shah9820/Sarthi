"""Cannibalization: one product's sales rise because a similar product ran out.

Detection is a substitution-uplift test, not a raw correlation. Products in one category share weekday
and growth patterns, so their sales correlate positively even when one is cannibalising the other. The
question that matters for ordering is narrower: on days the "falling" product had an empty shelf, did
the "rising" product sell clearly more than its own recent normal?
"""

from itertools import permutations

import numpy as np
import pandas as pd

WINDOW_DAYS = 365
BASELINE_DAYS = 28
MIN_BASELINE_DAYS = 10
MIN_EVENT_DAYS = 5
MIN_UPLIFT = 0.15
MIN_T = 3.0


def _test(rising_qty: pd.Series, rising_stock: pd.Series, falling_stock: pd.Series) -> dict | None:
    """Compare the rising SKU's sales (relative to its own trailing normal) on event days vs. other days."""
    event = falling_stock == 0
    in_stock = rising_stock > 0                       # the rising SKU's own stockouts would hide the effect
    normal = rising_qty.where(in_stock & ~event)      # baseline built only from ordinary days
    baseline = normal.shift(1).rolling(BASELINE_DAYS, min_periods=MIN_BASELINE_DAYS).mean()
    ratio = (rising_qty / baseline.where(baseline > 0)).where(in_stock)
    on_event, otherwise = ratio[event].dropna(), ratio[~event].dropna()
    if len(on_event) < MIN_EVENT_DAYS or len(otherwise) < BASELINE_DAYS:
        return None
    spread = np.sqrt(on_event.var() / len(on_event) + otherwise.var() / len(otherwise))
    if not spread > 0:
        return None
    uplift = float(on_event.mean() / otherwise.mean() - 1)
    t = float((on_event.mean() - otherwise.mean()) / spread)
    # correlation between the falling SKU being available (1) or not (0) and the rising SKU's relative sales
    available = np.r_[np.zeros(len(on_event)), np.ones(len(otherwise))]
    correlation = float(np.corrcoef(available, np.r_[on_event.to_numpy(), otherwise.to_numpy()])[0, 1])
    return {"uplift": uplift, "t": t, "correlation": correlation, "event_days": len(on_event)}


def detect(sales_daily: pd.DataFrame, stock_daily: pd.DataFrame, categories: dict[str, str],
           names: dict[str, str] | None = None) -> list[dict]:
    """Pairs within a category where the rising SKU gains when the falling SKU is out of stock.

    Output per pair: rising, falling, rName, fName, category, correlation (negative: the rising SKU sells
    more when the falling one is unavailable), uplift (share above normal), event_days.
    """
    if sales_daily.empty:
        return []
    names = names or {}
    qty = sales_daily.pivot(index="day", columns="sku_id", values="qty").sort_index().tail(WINDOW_DAYS)
    stock = stock_daily.pivot(index="day", columns="sku_id", values="on_hand").reindex(qty.index)
    by_category: dict[str, list[str]] = {}
    for sku_id in qty.columns:
        by_category.setdefault(categories.get(sku_id, ""), []).append(sku_id)

    found = []
    for category, members in by_category.items():
        if not category or len(members) < 2:
            continue
        for rising, falling in permutations(sorted(members), 2):
            result = _test(qty[rising].astype(float), stock[rising], stock[falling])
            if result and result["uplift"] >= MIN_UPLIFT and result["t"] >= MIN_T:
                found.append({
                    "rising": rising, "falling": falling,
                    "rName": names.get(rising, rising), "fName": names.get(falling, falling),
                    "category": category, "correlation": round(result["correlation"], 2),
                    "uplift": round(result["uplift"], 2), "event_days": result["event_days"],
                })
    return sorted(found, key=lambda r: r["correlation"])
