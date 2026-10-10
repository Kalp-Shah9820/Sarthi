"""Closing the loop: an action re-runs the agents, and the manager's decisions survive the new run.

Own database, with the follow-up run switched on (the rest of the suite keeps it off so that tests can
look at the state straight after an action).
"""

import asyncio
import os
import time

import pytest
from fastapi.testclient import TestClient
from sqlmodel import func, select

from sarthi.agents import demand_intel
from sarthi.agents.execution_engine import already_decided, recent_decisions
from sarthi.api import presenters
from sarthi.api.main import create_app
from sarthi.api.routers import runs
from sarthi.config import get_settings
from sarthi.db import init_db, reset_engine, session
from sarthi.llm import reset_llm
from sarthi.models import Alert, Event, Inbound, PurchaseOrder, Run, utcnow
from sarthi.orchestrator.runner import run_pipeline
from sarthi.seed.generator import seed_database
from tests.conftest import TEST_SEED, TEST_TODAY

ENV = ("SARTHI_DB_PATH", "SARTHI_OUTBOX_PATH", "SARTHI_RERUN_AFTER_ACTION")


@pytest.fixture(scope="module")
def client(tmp_path_factory, analysis):
    root = tmp_path_factory.mktemp("loop")
    previous = {k: os.environ[k] for k in ENV}
    os.environ.update(SARTHI_DB_PATH=str(root / "loop.db"), SARTHI_OUTBOX_PATH=str(root / "outbox"), SARTHI_RERUN_AFTER_ACTION="true")
    get_settings.cache_clear()
    reset_engine()
    reset_llm()
    presenters.reset()
    init_db()
    seed_database(TEST_SEED, today=TEST_TODAY)
    saved_cache = dict(demand_intel._cache)
    demand_intel._cache[demand_intel._fingerprint(get_settings())] = analysis
    asyncio.run(run_pipeline("test"))
    delay, runs.RERUN_DELAY_S = runs.RERUN_DELAY_S, 0.3
    with TestClient(create_app()) as test_client:
        yield test_client
    runs.RERUN_DELAY_S = delay
    demand_intel._cache.clear()
    demand_intel._cache.update(saved_cache)
    reset_engine()
    os.environ.update(previous)
    get_settings.cache_clear()
    reset_engine()
    reset_llm()
    presenters.reset()


def boot(client) -> dict:
    r = client.get("/api/bootstrap")
    assert r.status_code == 200, r.text
    return r.json()


def wait_for_new_run(client, after: int, seconds: float = 60) -> dict:
    """The bootstrap once a run newer than `after` has finished."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        b = boot(client)
        if b["live"]["runId"] > after:
            return b
        time.sleep(0.2)
    raise AssertionError(f"no run after #{after} finished within {seconds}s")


def settle(client) -> dict:
    """Wait until no follow-up run is pending or in progress, then return the bootstrap."""
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        with session() as s:
            busy = s.exec(select(func.count()).select_from(Run).where(Run.status == "running")).one()
        if not busy and runs._pending is None:
            return boot(client)
        time.sleep(0.2)
    raise AssertionError("runs did not settle")


def alerts_of(run_id: int, status: str = "open") -> list[Alert]:
    with session() as s:
        return list(s.exec(select(Alert).where(Alert.run_id == run_id, Alert.status == status)).all())


def run_count() -> int:
    with session() as s:
        return s.exec(select(func.count()).select_from(Run).where(Run.dry_run.is_(False))).one()


def test_an_order_re_runs_the_agents_and_the_screens_reflect_it(client):
    before = settle(client)
    row = next(r for r in before["skuData"] if r["decisionStatus"] == "critical" and r["zone"] == "chaos")
    qty = row["recommendedQty"]
    r = client.post("/api/orders", json={"skuId": row["id"], "distributor": before["distributors"][0]["name"], "units": qty,
                                         "price": before["live"]["replenishment"][row["id"]]["price"]})
    assert r.status_code == 200 and r.json()["runStarted"] is True
    after = wait_for_new_run(client, before["live"]["runId"])
    with session() as s:
        run = s.get(Run, after["live"]["runId"])
        inbound = s.exec(select(func.sum(Inbound.qty)).where(Inbound.sku_id == row["id"], Inbound.received_on.is_(None))).one()
    assert run.trigger == "action" and inbound >= qty
    # the agents now count the stock on order: they no longer ask for the same quantity again
    again = [a for a in alerts_of(run.id) if a.sku_id == row["id"] and a.payload.get("kind") == "purchase"]
    assert all(a.payload["qty"] < qty for a in again), [a.payload["qty"] for a in again]
    # and the order is still in the audit trail, although a newer run has finished since
    assert any(r.json()["orderId"] in x["result"] for x in after["live"]["auditTrail"])
    assert len(after["live"]["auditTrail"]) > 12


def test_quick_actions_share_one_follow_up_run(client):
    start = settle(client)
    runs_before = run_count()
    warehouses = start["live"]["warehouses"]
    source = max(warehouses, key=lambda w: sum(w["stock"].values()))
    target = next(w for w in warehouses if w["id"] != source["id"])
    sku_id = next(iter(source["stock"]))
    for _ in range(3):
        r = client.post("/api/transfers", json={"skuId": sku_id, "fromId": source["id"], "toId": target["id"], "units": 5})
        assert r.json()["runStarted"] is True
    wait_for_new_run(client, start["live"]["runId"])
    settle(client)
    assert run_count() == runs_before + 1


def test_an_approval_stays_on_the_alert_list_after_the_follow_up_run(client):
    start = settle(client)
    alert = next(a for a in alerts_of(start["live"]["runId"]) if a.payload.get("kind") == "transfer")
    r = client.post(f"/api/alerts/{alert.id}/approve")
    assert r.status_code == 200 and r.json()["runStarted"] is True
    after = wait_for_new_run(client, start["live"]["runId"])
    shown = next((a for a in after["live"]["alerts"] if a["id"] == alert.id), None)
    assert shown is not None and shown["status"] == "approved" and shown["txid"] == r.json()["txid"]
    assert client.post(f"/api/alerts/{alert.id}/approve").json() == {"txid": r.json()["txid"], "status": "approved", "runStarted": False}


def test_a_dismissed_recommendation_is_not_raised_again(client):
    start = settle(client)
    alert = next(a for a in alerts_of(start["live"]["runId"]) if a.type == "cannibalization")
    proposal_id = alert.payload["proposal_id"]
    runs_before = run_count()
    assert client.post(f"/api/alerts/{alert.id}/dismiss").json() == {"status": "dismissed"}
    time.sleep(1.0)
    assert run_count() == runs_before and runs._pending is None      # a dismissal changes nothing the agents would see: no run
    run_id = client.post("/api/runs").json()["runId"]
    after = wait_for_new_run(client, start["live"]["runId"])
    assert after["live"]["runId"] == run_id
    assert proposal_id not in [a.payload.get("proposal_id") for a in alerts_of(run_id)]
    others = {a.payload.get("proposal_id") for a in alerts_of(start["live"]["runId"])} - {proposal_id}
    assert others & {a.payload.get("proposal_id") for a in alerts_of(run_id)}      # the rest are still raised
    with session() as s:
        note = s.exec(select(Event).where(Event.run_id == run_id, Event.kind == "action", Event.agent == "executionEngine")
                      .order_by(Event.id.desc())).first()
    assert "already decided and not repeated" in note.value["result"]


def test_an_approved_check_is_not_repeated_but_an_expired_decision_is_forgotten(client):
    start = settle(client)
    alert = next(a for a in alerts_of(start["live"]["runId"]) if a.payload.get("kind") == "audit")
    proposal = {"id": alert.payload["proposal_id"], "kind": "audit"}
    r = client.post(f"/api/alerts/{alert.id}/approve")
    assert r.json()["runStarted"] is False                  # a check or a cap moves no stock
    memory = recent_decisions(get_settings().decision_memory_days)
    assert already_decided(proposal, alert.zone, memory) == "approved"
    assert already_decided({"id": "purchase:stockout_reorder:SKU999", "kind": "purchase"}, "chaos", memory) is None

    with session() as s:                                    # as if it had been decided long ago
        stored = s.get(Alert, alert.id)
        stored.decided_at = utcnow().replace(year=utcnow().year - 1)
        s.add(stored)
        s.commit()
    assert already_decided(proposal, alert.zone, recent_decisions(get_settings().decision_memory_days)) is None


def test_decision_memory_rules():
    class Prior:
        def __init__(self, status, zone):
            self.status, self.zone = status, zone

    def decided(kind, prior, zone="chaos"):
        return already_decided({"id": "x", "kind": kind}, zone, {"x": prior})

    assert decided("purchase", Prior("dismissed", "chaos")) == "dismissed"
    assert decided("purchase", Prior("dismissed", "sweet")) is None       # the product's situation changed: ask again
    assert decided("purchase", Prior("approved", "chaos")) is None        # the order changed the stock: a new question
    assert decided("transfer", Prior("approved", "ghost"), "ghost") is None
    assert decided("campaign", Prior("approved", "ghost"), "ghost") == "approved"
    assert decided("audit", Prior("approved", "chaos")) == "approved"


def test_a_chat_request_stays_on_the_list_across_runs(client):
    start = settle(client)
    reply = client.post("/api/chat", json={"text": "reorder 40 units of maggi", "lang": "EN"}).json()
    assert reply["intent"] == "reorder"
    with session() as s:
        made = next(a for a in s.exec(select(Alert).where(Alert.status == "open")).all() if a.payload.get("source") == "chat")
    client.post("/api/runs")
    after = wait_for_new_run(client, start["live"]["runId"])
    assert made.id in [a["id"] for a in after["live"]["alerts"]]
    with session() as s:
        assert s.get(Alert, made.id).run_id == after["live"]["runId"]
    orders = None
    with session() as s:
        orders = s.exec(select(func.count()).select_from(PurchaseOrder)).one()
    approved = client.post(f"/api/alerts/{made.id}/approve")
    assert approved.status_code == 200 and approved.json()["runStarted"] is True
    with session() as s:
        assert s.exec(select(func.count()).select_from(PurchaseOrder)).one() == orders + 1
    settle(client)


def test_follow_up_run_can_be_switched_off(client, monkeypatch):
    settle(client)
    monkeypatch.setenv("SARTHI_RERUN_AFTER_ACTION", "false")
    get_settings.cache_clear()
    try:
        runs_before = run_count()
        r = client.post("/api/orders", json={"skuId": "SKU001", "distributor": "Reliance", "units": 12, "price": 190})
        assert r.status_code == 200 and r.json()["runStarted"] is False
        time.sleep(1.0)
        assert run_count() == runs_before and runs._pending is None
    finally:
        monkeypatch.setenv("SARTHI_RERUN_AFTER_ACTION", "true")
        get_settings.cache_clear()
