"""Runs the SENSE and DECIDE agents in order. The full graph (mk8) builds on the same agents."""

import asyncio

from sarthi.agents.compliance_guardian import ComplianceGuardian
from sarthi.agents.demand_intel import DemandIntel
from sarthi.agents.inventory_optimizer import InventoryOptimizer
from sarthi.agents.macro_sentinel import MacroSentinel
from sarthi.blackboard.store import Blackboard
from sarthi.config import get_settings
from sarthi.llm import get_llm
from sarthi.seed.catalog import DEFAULT_STRATEGY

BALANCED = {"mode": "Balanced", **DEFAULT_STRATEGY}


async def sense_and_decide(run_id: int, *, scenario: dict | None = None, strategy: dict | None = None,
                           dry_run: bool = False, settings=None, llm=None) -> dict:
    """SENSE (two agents in parallel) then DECIDE (two agents in sequence). Returns the accumulated state."""
    settings = settings or get_settings()
    args = (Blackboard(run_id), llm or get_llm(), settings, strategy or dict(BALANCED))
    state: dict = {"run_id": run_id, "dry_run": dry_run, "scenario": scenario or {}, "strategy": args[3], "errors": []}

    def merge(update: dict) -> None:
        state["errors"] += update.pop("errors", [])
        state.update(update)

    for update in await asyncio.gather(MacroSentinel(*args).run(state), DemandIntel(*args).run(state)):
        merge(update)
    if "forecasts" not in state:      # Demand Intel failed: nothing downstream can run
        return state
    merge(await InventoryOptimizer(*args).run(state))
    merge(await ComplianceGuardian(*args).run(state))
    return state
