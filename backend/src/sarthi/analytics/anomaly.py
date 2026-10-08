"""Phantom inventory: the stock ledger and the shelf disagree."""

import pandas as pd

WINDOW_DAYS = 14
SOLD_WHILE_EMPTY_DAYS = 2
IDLE_RUN_DAYS = 5
MIN_EXPECTED_PER_DAY = 5.0


def _longest_run(flags: list[bool]) -> int:
    best = run = 0
    for flag in flags:
        run = run + 1 if flag else 0
        best = max(best, run)
    return best


def phantom_inventory(sales_daily: pd.DataFrame, stock_daily: pd.DataFrame,
                      expected: dict[str, float] | None = None,
                      receipt_days: set[tuple[str, object]] | None = None) -> list[dict]:
    """Flag SKUs whose last 14 days show one of two contradictions.

    - `sold_while_empty`: units sold on 2+ days that both opened and closed at zero recorded stock, with no
      delivery recorded that day. The ledger says there was nothing to sell, so the count is wrong.
    - `idle_stock`: recorded stock for 5 straight days with zero sales while demand of 5+/day was expected.
      The units are probably not on the shelf.

    `expected`: sku_id -> forecast units per day. `receipt_days`: (sku_id, day) pairs with a delivery.
    """
    expected = expected or {}
    receipt_days = receipt_days or set()
    out = []
    for sku_id, sales in sales_daily.groupby("sku_id", sort=True):
        sales = sales.sort_values("day")
        stock = stock_daily[stock_daily["sku_id"] == sku_id].set_index("day")["on_hand"].reindex(sales["day"])
        frame = pd.DataFrame({
            "day": sales["day"].to_numpy(), "qty": sales["qty"].to_numpy(),
            "close": stock.to_numpy(), "open": stock.shift(1).to_numpy(),
        }).tail(WINDOW_DAYS)

        empty_sale = frame[(frame["open"] == 0) & (frame["close"] == 0) & (frame["qty"] > 0)]
        empty_sale = empty_sale[[(sku_id, d) not in receipt_days for d in empty_sale["day"]]]
        if len(empty_sale) >= SOLD_WHILE_EMPTY_DAYS:
            out.append({"sku_id": sku_id, "kind": "sold_while_empty", "days": len(empty_sale),
                        "est_units": int(empty_sale["qty"].sum())})
            continue

        idle = ((frame["close"] > 0) & (frame["qty"] == 0)).to_list()
        run = _longest_run(idle)
        if run >= IDLE_RUN_DAYS and expected.get(sku_id, 0.0) >= MIN_EXPECTED_PER_DAY:
            out.append({"sku_id": sku_id, "kind": "idle_stock", "days": run,
                        "est_units": int(frame["close"].iloc[-1])})
    return out
