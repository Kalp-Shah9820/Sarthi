"""The agent graph: SENSE (parallel) -> DECIDE -> RESOLVE (parallel) -> Arbiter -> EXECUTE."""

from langgraph.graph import END, START, StateGraph

from sarthi.agents.arbiter import Arbiter
from sarthi.agents.compliance_guardian import ComplianceGuardian
from sarthi.agents.demand_intel import DemandIntel
from sarthi.agents.distributor_selector import DistributorSelector
from sarthi.agents.execution_engine import ExecutionEngine
from sarthi.agents.inventory_optimizer import InventoryOptimizer
from sarthi.agents.macro_sentinel import MacroSentinel
from sarthi.agents.overstock_resolver import OverstockResolver
from sarthi.orchestrator.state import PHASE_OF, RunState

AGENT_CLASSES = {
    "macro_sentinel": MacroSentinel, "demand_intel": DemandIntel,
    "inventory_optimizer": InventoryOptimizer, "compliance_guardian": ComplianceGuardian,
    "distributor_selector": DistributorSelector, "overstock_resolver": OverstockResolver,
    "arbiter": Arbiter, "execution_engine": ExecutionEngine,
}
assert set(AGENT_CLASSES) == set(PHASE_OF)


def build_agents(bb, llm, settings, strategy: dict) -> dict:
    return {name: cls(bb, llm, settings, strategy) for name, cls in AGENT_CLASSES.items()}


def build_graph(agents: dict):
    g = StateGraph(RunState)
    for name in AGENT_CLASSES:
        g.add_node(name, agents[name].run)

    # SENSE: two agents in parallel
    g.add_edge(START, "macro_sentinel")
    g.add_edge(START, "demand_intel")
    # DECIDE: waits for both SENSE agents
    g.add_edge(["macro_sentinel", "demand_intel"], "inventory_optimizer")
    g.add_edge("inventory_optimizer", "compliance_guardian")
    # RESOLVE: two agents in parallel. Both always run and each skips SKUs that are not its concern,
    # so the join below is always satisfiable and the graph stays static.
    g.add_edge("compliance_guardian", "distributor_selector")
    g.add_edge("compliance_guardian", "overstock_resolver")
    # The Arbiter waits for both, then EXECUTE
    g.add_edge(["distributor_selector", "overstock_resolver"], "arbiter")
    g.add_edge("arbiter", "execution_engine")
    g.add_edge("execution_engine", END)
    return g.compile()
