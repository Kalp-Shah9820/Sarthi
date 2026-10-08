import asyncio
from datetime import UTC, datetime

import pytest

from sarthi.agents.base import Agent
from sarthi.blackboard import store
from sarthi.blackboard.store import Blackboard


@pytest.fixture
def bb(db):
    """A blackboard on its own run id, cleaned up afterwards."""
    board = Blackboard(run_id=9001)
    store.delete_run_events(9001)
    yield board
    store.delete_run_events(9001)


def test_put_keeps_only_the_latest_value_per_key(bb):
    bb.put("macroSentinel", "sense", "lead_time_risk", "LOW")
    bb.put("forecaster", "sense", "demand_signal", "STABLE", tone="sweet")
    bb.put("macroSentinel", "sense", "lead_time_risk", "HIGH", tone="chaos")
    assert bb.get("lead_time_risk") == "HIGH"
    assert bb.get("missing", "default") == "default"
    context = bb.context()
    assert [(c["key"], c["value"], c["tone"], c["agent"]) for c in context] == [
        ("lead_time_risk", "HIGH", "chaos", "macroSentinel"),   # newest first
        ("demand_signal", "STABLE", "sweet", "forecaster"),
    ]


def test_actions_keep_their_order_and_facts(bb):
    bb.act("forecaster", "sense", "Forecast 10 SKUs", result="WAPE 12%", sku_id="SKU003", wape=0.12)
    bb.act("negotiator", "resolve", "Ranked 3 suppliers", result="Reliance · 93")
    bb.put("cfoAgent", "decide", "par_total", "₹57K")
    actions = bb.events(kind="action")
    assert [e.text for e in actions] == ["Forecast 10 SKUs", "Ranked 3 suppliers"]
    assert actions[0].value == {"result": "WAPE 12%", "facts": {"wape": 0.12}}
    assert (actions[0].sku_id, actions[0].phase, actions[0].agent) == ("SKU003", "sense", "forecaster")
    assert len(bb.events()) == 3
    assert [e.text for e in bb.events(kind="action", since_id=actions[0].id)] == ["Ranked 3 suppliers"]


def test_debate_metric_and_error_events(bb):
    bb.say("negotiator", "Order 1,488 units from Reliance.", stance="propose", sku_id="SKU003", qty=1488)
    bb.say("cfoAgent", "Over budget.", stance="object", rule="BUDGET_CAP")
    with pytest.raises(ValueError, match="unknown stance"):
        bb.say("cfoAgent", "Hmm.", stance="shrug")
    bb.metric("inventoryOptimizer", "SKU003", risk=97, zone="chaos")
    bb.metric("demandIntel", key="demand_intel", rules=8)
    bb.error("macroSentinel", TimeoutError("weather service did not answer"))

    debate = bb.events(kind="debate")
    assert [(e.agent, e.value["stance"]) for e in debate] == [("negotiator", "propose"), ("cfoAgent", "object")]
    assert debate[1].value["facts"] == {"rule": "BUDGET_CAP"}
    assert bb.metrics(sku_id="SKU003") == [{"risk": 97, "zone": "chaos"}]
    assert bb.metrics(key="demand_intel") == [{"rules": 8}]
    assert bb.events(kind="error")[0].text == "TimeoutError: weather service did not answer"


def test_runs_do_not_see_each_other(bb, db):
    other = Blackboard(run_id=9002)
    try:
        other.put("x", "sense", "lead_time_risk", "LOW")
        bb.put("x", "sense", "lead_time_risk", "HIGH")
        assert (bb.get("lead_time_risk"), other.get("lead_time_risk")) == ("HIGH", "LOW")
    finally:
        store.delete_run_events(9002)


async def test_subscriber_receives_each_event_once_including_from_threads(bb):
    queue = store.subscribe(bb.run_id)
    try:
        bb.put("a", "sense", "k1", 1)                              # same thread as the loop
        await asyncio.to_thread(bb.act, "b", "decide", "worked")   # worker thread, as analytics will do
        bb.end()
        received = [await asyncio.wait_for(queue.get(), timeout=2) for _ in range(3)]
        assert [e["kind"] for e in received] == ["context", "action", store.END]
        assert received[0]["key"] == "k1" and received[1]["text"] == "worked"
        assert queue.empty()
    finally:
        store.unsubscribe(bb.run_id, queue)
    assert bb.run_id not in store._subscribers
    bb.put("a", "sense", "k2", 2)                                  # no listeners: must not raise


async def test_subscribers_only_get_their_own_run(bb):
    mine, theirs = store.subscribe(bb.run_id), store.subscribe(9002)
    try:
        bb.put("a", "sense", "k", 1)
        assert (await asyncio.wait_for(mine.get(), timeout=2))["key"] == "k"
        await asyncio.sleep(0.05)
        assert theirs.empty()
    finally:
        store.unsubscribe(bb.run_id, mine)
        store.unsubscribe(9002, theirs)


def test_local_time_treats_stored_times_as_utc():
    aware = datetime(2026, 10, 8, 12, 0, 0, tzinfo=UTC)
    assert store.local_time(aware) == store.local_time(aware.replace(tzinfo=None))
    assert len(store.local_time(aware)) == 8


# ── Agent base class ─────────────────────────────────────────────────────────

class Working(Agent):
    key, phase = "forecaster", "sense"

    async def work(self, state):
        self.bb.put(self.key, self.phase, "demand_signal", "STABLE")
        return {"forecast_ready": True}


class Broken(Agent):
    key, phase = "macroSentinel", "sense"

    async def work(self, state):
        raise RuntimeError("feed unavailable")


async def test_agent_returns_its_update(bb):
    agent = Working(bb, llm=None, settings=None, strategy={"mode": "Balanced"})
    assert await agent.run({}) == {"forecast_ready": True}
    assert bb.get("demand_signal") == "STABLE"


async def test_a_failing_agent_is_contained_and_logged(bb):
    result = await Broken(bb, llm=None, settings=None, strategy={}).run({})
    assert result == {"errors": ["macroSentinel: RuntimeError: feed unavailable"]}
    error = bb.events(kind="error")[0]
    assert (error.agent, error.phase, error.text) == ("macroSentinel", "sense", "RuntimeError: feed unavailable")


async def test_base_agent_must_be_subclassed(bb):
    result = await Agent(bb, llm=None, settings=None, strategy={}).run({})
    assert "NotImplementedError" in result["errors"][0]
