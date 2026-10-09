"""What a sentence can mean. The model fills this schema; it never writes the answer."""

from typing import Literal

from pydantic import BaseModel

IntentName = Literal["greeting", "strategy_set", "strategy_get", "suppliers", "basket", "bullwhip",
                     "montecarlo", "sku_risk", "sku_sweet", "sku_summary", "stock_summary", "zones",
                     "stockout_horizon", "overstock", "explain_sku", "reorder", "unknown"]
NEEDS_SKU = ("explain_sku", "reorder")


class Intent(BaseModel):
    name: IntentName
    sku: str = ""                 # free text as spoken; resolved later
    days: int = 7
    mode: Literal["", "Cash Flow", "Growth", "Balanced"] = ""
    quantity: int = 0
    percent: float = 0.0
