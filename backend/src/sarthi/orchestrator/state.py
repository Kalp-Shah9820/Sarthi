"""The state that flows through one pipeline run."""

import operator
from typing import Annotated, Any, TypedDict


def merge(a: dict, b: dict) -> dict:
    return {**a, **b}


class RunState(TypedDict, total=False):
    run_id: int
    dry_run: bool
    lang: str
    scenario: dict                               # {"lead_mult": 1.0, "demand_mult": 1.0}
    strategy: dict                               # mode + the active policy's parameters
    lead_modifier: dict                          # supplier_id -> float      (Macro Sentinel)
    demand_multiplier: dict                      # category -> float         (Macro Sentinel)
    signal_count: int
    forecasts: Any                               # dict[str, ForecastResult] (Demand Intel); in memory only
    forecast_ready: bool
    history: Any                                 # sales / stock frames and the data's last day
    as_of: Any
    decisions: Annotated[dict, merge]            # (Inventory Optimizer)
    envelope: dict                               # (Compliance Guardian)
    proposals: Annotated[list, operator.add]     # (both RESOLVE agents, in parallel)
    rulings: list                                # (Arbiter)
    alerts: list                                 # (Execution Engine)
    executed: list
    errors: Annotated[list, operator.add]        # any agent may report a failure


PHASE_OF = {
    "macro_sentinel": "sense", "demand_intel": "sense",
    "inventory_optimizer": "decide", "compliance_guardian": "decide",
    "distributor_selector": "resolve", "overstock_resolver": "resolve", "arbiter": "resolve",
    "execution_engine": "execute",
}
