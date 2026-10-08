"""Shipping options for a replenishment: speed, freight cost and carbon, in the UI's fixed order."""

import re
from collections.abc import Callable

# Planning-grade defaults; replace with measured factors when available.
MODES = {  # mode: (transit days, kg CO2 per tonne-km, freight cost factor vs. the cheapest mode)
    "air": (1, 0.60, 3.0),
    "sea": (7, 0.016, 1.0),
    "multimodal": (3, 0.045, 1.7),
}
MODE_ORDER = ("air", "sea", "multimodal")  # the order the ESG cards are drawn in
CARBON_PRICE = {"Balanced": 2.0, "Cash Flow": 0.5, "Growth": 1.0}  # rupees per kg CO2 in the trade-off
FREIGHT_RATE = 2.5         # rupees per tonne-km for the cheapest mode
DEFAULT_UNIT_WEIGHT = 0.5  # kg


def parse_unit_weight(name: str) -> float:
    """Weight in kg from a product name such as 'Amul Butter 500g', 'Tata Salt 1kg', 'Fortune Oil 5L'."""
    match = re.search(r"(\d+(?:\.\d+)?)\s*(kg|g|ml|l)\b", name.lower())
    if not match:
        return DEFAULT_UNIT_WEIGHT
    value, unit = float(match.group(1)), match.group(2)
    return value if unit in ("kg", "l") else value / 1000


def base_freight(qty: float, unit_weight_kg: float, distance_km: float) -> float:
    """Freight cost of the shipment by the cheapest mode."""
    return qty * unit_weight_kg / 1000 * distance_km * FREIGHT_RATE


def options(qty: float, unit_weight_kg: float, distance_km: float, freight: float,
            risk: Callable[[int], tuple[float, float]] | None = None) -> list[dict]:
    """One dict per mode. `risk(days)` returns (stockout probability, shortage Profit-at-Risk) for that transit time."""
    tonnes = qty * unit_weight_kg / 1000
    out = []
    for mode in MODE_ORDER:
        days, factor, cost_factor = MODES[mode]
        stockout_prob, par = risk(days) if risk else (0.0, 0.0)
        out.append({
            "mode": mode, "days": days, "freight_cost": round(freight * cost_factor, 2),
            "co2_kg": round(tonnes * distance_km * factor, 2),
            "stockout_prob": round(stockout_prob, 4), "par_shortage": round(par, 2),
        })
    return out


def recommend(opts: list[dict], mode: str = "Balanced") -> int:
    """Index of the option with the lowest total of freight, priced carbon and stockout exposure."""
    price = CARBON_PRICE.get(mode, CARBON_PRICE["Balanced"])
    totals = [o["freight_cost"] + price * o["co2_kg"] + o["par_shortage"] for o in opts]
    return totals.index(min(totals))
