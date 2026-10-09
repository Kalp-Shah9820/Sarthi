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


async def resolve(state: dict, *, settings=None, llm=None) -> dict:
    """RESOLVE: both agents in parallel, each proposing actions for the SKUs that concern it."""
    from sarthi.agents.distributor_selector import DistributorSelector
    from sarthi.agents.overstock_resolver import OverstockResolver

    settings = settings or get_settings()
    args = (Blackboard(state["run_id"]), llm or get_llm(), settings, state["strategy"])
    state.setdefault("proposals", [])
    for update in await asyncio.gather(DistributorSelector(*args).run(state), OverstockResolver(*args).run(state)):
        state["errors"] += update.pop("errors", [])
        state["proposals"] += update.pop("proposals", [])
        state.update(update)
    return state


async def sense_decide_resolve(run_id: int, **kw) -> dict:
    """SENSE, DECIDE and RESOLVE. Stops after DECIDE if it produced no decisions."""
    state = await sense_and_decide(run_id, **kw)
    if "decisions" not in state:
        return state
    return await resolve(state, settings=kw.get("settings"), llm=kw.get("llm"))
