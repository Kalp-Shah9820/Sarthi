"""Loads history from the database into the plain frames the analytics functions take.

This is the only analytics module that touches the database; everything else is pure.
"""

from datetime import date, timedelta

import pandas as pd
from sqlmodel import func, select

from sarthi.db import session
from sarthi.models import Sale, Sku, StockDaily
from sarthi.seed.catalog import STORE_ID


def store_frames(location_id: str = STORE_ID, as_of: date | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Daily sales and closing stock for one location, one row per SKU per calendar day.

    Returns (sales_daily[sku_id, day, qty], stock_daily[sku_id, day, on_hand]). Days without sales are
    filled with 0. Days without a stock record carry the last known figure forward; days before the first
    record are NaN ("unknown"), which is deliberately not the same as zero stock.
    """
    with session() as s:
        sale_q = select(Sale.sku_id, Sale.day, func.sum(Sale.qty)).where(Sale.location_id == location_id)
        stock_q = select(StockDaily.sku_id, StockDaily.day, StockDaily.on_hand).where(StockDaily.location_id == location_id)
        if as_of is not None:
            sale_q = sale_q.where(Sale.day <= as_of)
            stock_q = stock_q.where(StockDaily.day <= as_of)
        sales = pd.DataFrame(s.exec(sale_q.group_by(Sale.sku_id, Sale.day)).all(), columns=["sku_id", "day", "qty"])
        stock = pd.DataFrame(s.exec(stock_q).all(), columns=["sku_id", "day", "on_hand"])
        sku_ids = sorted(s.exec(select(Sku.id)).all())

    sales_out, stock_out = [], []
    for sku_id in sku_ids:
        sold = sales[sales["sku_id"] == sku_id].set_index("day")["qty"]
        held = stock[stock["sku_id"] == sku_id].set_index("day")["on_hand"]
        firsts = [x.index.min() for x in (sold, held) if len(x)]
        if not firsts:
            continue
        lasts = [x.index.max() for x in (sold, held) if len(x)]
        end = as_of or max(lasts)
        days = [d.date() for d in pd.date_range(min(firsts), end, freq="D")]
        sales_out.append(pd.DataFrame({"sku_id": sku_id, "day": days, "qty": sold.reindex(days, fill_value=0).to_numpy(dtype=float)}))
        stock_out.append(pd.DataFrame({"sku_id": sku_id, "day": days, "on_hand": held.reindex(days).ffill().to_numpy(dtype=float)}))
    empty_sales = pd.DataFrame(columns=["sku_id", "day", "qty"])
    empty_stock = pd.DataFrame(columns=["sku_id", "day", "on_hand"])
    return (
        pd.concat(sales_out, ignore_index=True) if sales_out else empty_sales,
        pd.concat(stock_out, ignore_index=True) if stock_out else empty_stock,
    )


def basket_frame(days: int = 90, location_id: str = STORE_ID, as_of: date | None = None) -> pd.DataFrame:
    """Basket contents (basket_id, sku_id) for the most recent `days` days."""
    with session() as s:
        end = as_of or s.exec(select(func.max(Sale.day)).where(Sale.location_id == location_id)).one()
        if end is None:
            return pd.DataFrame(columns=["basket_id", "sku_id"])
        rows = s.exec(
            select(Sale.basket_id, Sale.sku_id)
            .where(Sale.location_id == location_id, Sale.day > end - timedelta(days=days), Sale.day <= end)
        ).all()
    return pd.DataFrame(rows, columns=["basket_id", "sku_id"])


def weekly_totals(sales_daily: pd.DataFrame, weeks: int = 12) -> list[float]:
    """Total units per 7-day block, oldest first, ending on the last day in the frame."""
    if sales_daily.empty:
        return []
    per_day = sales_daily.groupby("day")["qty"].sum().sort_index().to_numpy()
    usable = (len(per_day) // 7) * 7
    blocks = per_day[len(per_day) - usable:].reshape(-1, 7).sum(axis=1)
    return [float(x) for x in blocks[-weeks:]]
