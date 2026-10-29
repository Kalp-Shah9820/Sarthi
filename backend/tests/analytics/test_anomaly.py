"""Phantom inventory: stock records that disagree with sales."""

from datetime import date, timedelta

import pandas as pd

from sarthi.analytics import (
    anomaly,
)

# ── Phantom inventory ────────────────────────────────────────────────────────

def frames(qty, on_hand, sku="X"):
    days = [date(2026, 9, 1) + timedelta(days=i) for i in range(len(qty))]
    return (pd.DataFrame({"sku_id": sku, "day": days, "qty": qty}),
            pd.DataFrame({"sku_id": sku, "day": days, "on_hand": on_hand}))


def test_phantom_inventory_sold_while_empty():
    sales, stock = frames([5] * 10 + [0, 3, 4, 0], [50] * 10 + [0, 0, 0, 0])
    assert anomaly.phantom_inventory(sales, stock) == [{"sku_id": "X", "kind": "sold_while_empty", "days": 2, "est_units": 7}]
    deliveries = {("X", sales["day"].iloc[11]), ("X", sales["day"].iloc[12])}
    assert anomaly.phantom_inventory(sales, stock, receipt_days=deliveries) == []


def test_phantom_inventory_idle_stock_needs_expected_demand():
    sales, stock = frames([20] * 8 + [0] * 6, [300] * 14)
    assert anomaly.phantom_inventory(sales, stock, expected={"X": 20.0}) == [
        {"sku_id": "X", "kind": "idle_stock", "days": 6, "est_units": 300}
    ]
    assert anomaly.phantom_inventory(sales, stock, expected={"X": 1.0}) == []   # a slow mover can sit idle


def test_an_ordinary_stockout_is_not_an_anomaly():
    sales, stock = frames([20] * 12 + [15, 0], [100] * 12 + [0, 0])  # sold the last units, then nothing
    assert anomaly.phantom_inventory(sales, stock, expected={"X": 20.0}) == []
