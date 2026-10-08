"""Profit-at-Risk: rupees exposed by running out (shortage) or by holding too much (overstock)."""


def par_shortage(expected_lost_units: float, unit_margin: float, goodwill: float) -> float:
    return expected_lost_units * unit_margin * (1 + goodwill)


def excess_units(on_hand: float, velocity: float, overstock_cover_days: float) -> float:
    return max(0.0, on_hand - velocity * overstock_cover_days)


def par_overstock(on_hand: float, velocity: float, cogs: float, holding_pct_month: float,
                  overstock_cover_days: float, shelf_life_remaining_days: float) -> float:
    """Carrying cost of the excess while it sells down, plus stock that will expire unsold."""
    excess = excess_units(on_hand, velocity, overstock_cover_days)
    months_to_clear = excess / max(velocity * 30, 1)
    expiring = max(0.0, on_hand - velocity * shelf_life_remaining_days)
    # /2: on average half the excess is still on hand while it sells down
    return excess * cogs * holding_pct_month * min(months_to_clear, 12) / 2 + expiring * cogs


def profit_at_risk(shortage: float, overstock: float) -> int:
    return round(max(shortage, overstock))


def carry_roi(margin_fraction: float, holding_pct_month: float, days_of_cover: float) -> float:
    """Margin left after paying to carry the current cover; negative means the stock loses money sitting."""
    return margin_fraction - holding_pct_month * days_of_cover / 30
