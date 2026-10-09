"""Execution: approved alerts become purchase orders, transfers, campaigns and tasks. Uses its own database
because executing changes stock and open orders."""

import email
import json
import os
from datetime import timedelta

import pytest
from sqlmodel import func, select

from sarthi.agents.execution_engine import ExecutionEngine, base36, data_today, execute_alert, execute_payload
from sarthi.agents.stages import BALANCED
from sarthi.blackboard.store import Blackboard
from sarthi.config import get_settings
from sarthi.db import init_db, reset_engine, session
from sarthi.llm import get_llm
from sarthi.models import (
    Alert,
    Campaign,
    Inbound,
    Negotiation,
    OutboxEmail,
    PurchaseOrder,
    StockDaily,
    TransferOrder,
)
from sarthi.seed.generator import seed_database
from tests.conftest import TEST_SEED, TEST_TODAY

RUN = 7200
DEAL = {"list_price": 12.0, "target": 11.52, "reserve": 11.55, "floor": 11.52, "agreed_price": 11.55, "agreed_round": 4,
        "rounds": [{"round": 1, "buyer_offer": 11.52, "supplier_ask": 12.0}]}


@pytest.fixture(scope="module")
def exec_db(tmp_path_factory):
    root = tmp_path_factory.mktemp("execute")
    previous = {k: os.environ[k] for k in ("SARTHI_DB_PATH", "SARTHI_OUTBOX_PATH")}
    os.environ.update(SARTHI_DB_PATH=str(root / "execute.db"), SARTHI_OUTBOX_PATH=str(root / "outbox"))
    get_settings.cache_clear()
    reset_engine()
    init_db()
    seed_database(TEST_SEED, today=TEST_TODAY)
    yield root / "outbox"
    reset_engine()
    os.environ.update(previous)
    get_settings.cache_clear()


def count(model, *where) -> int:
    with session() as s:
        return s.exec(select(func.count()).select_from(model).where(*where)).one()


def make_alert(kind: str, sku_id: str, payload: dict, routed: str = "review") -> int:
    with session() as s:
        alert = Alert(run_id=RUN, sku_id=sku_id, sku_label=sku_id, zone="chaos", type="stockout_reorder", routed=routed,
                      payload={**payload, "kind": kind})
        s.add(alert)
        s.commit()
        s.refresh(alert)
        return alert.id


def stock(sku_id: str, location: str) -> int:
    with session() as s:
        row = s.exec(select(StockDaily).where(StockDaily.sku_id == sku_id, StockDaily.location_id == location)
                     .order_by(StockDaily.day.desc())).first()
        return row.on_hand if row else 0


def test_helpers(exec_db):
    assert base36(0) == "0" and base36(35) == "Z" and base36(36) == "10"
    assert data_today() == TEST_TODAY


def test_purchase_creates_order_inbound_and_outbox_files_once(exec_db):
    with session() as s:        # the draft the Distributor Selector would have left for this proposal
        s.add(OutboxEmail(to_addr="orders@reliance-metro.example", subject="Order request: 984 units of Lays Classic 26g",
                          body="Dear Reliance Metro WH team,\n\nPlease supply 984 units.", ref="purchase:supplier_switch:SKU003"))
        s.commit()
    alert_id = make_alert("purchase", "SKU003", {
        "supplier_id": "SUP-REL", "qty": 984, "unit_price": 11.55, "mode": "air", "eta_days": 3,
        "negotiation": DEAL, "proposal_id": "purchase:supplier_switch:SKU003"})
    before = (count(PurchaseOrder), count(Inbound), count(Negotiation), count(OutboxEmail))

    result = execute_alert(alert_id)
    po_id = result["txid"]
    assert result["kind"] == "purchase" and result["repeat"] is False and po_id.startswith("PO-")
    assert (count(PurchaseOrder), count(Inbound), count(Negotiation), count(OutboxEmail)) == (before[0] + 1, before[1] + 1, before[2] + 1, before[3])
    with session() as s:
        po = s.get(PurchaseOrder, po_id)
        inbound = s.exec(select(Inbound).where(Inbound.sku_id == "SKU003", Inbound.received_on.is_(None))).one()
        mail = s.exec(select(OutboxEmail).where(OutboxEmail.ref == po_id)).one()
        alert = s.get(Alert, alert_id)
        deal = s.exec(select(Negotiation).where(Negotiation.po_id == po_id)).one()
    assert (po.qty, po.unit_price, po.mode, po.status, po.source, po.supplier_id) == (984, 11.55, "air", "confirmed", "alert", "SUP-REL")
    assert (inbound.qty, inbound.ordered_on, inbound.expected_on) == (984, TEST_TODAY, TEST_TODAY + timedelta(days=3))
    assert mail.status == "queued" and (deal.agreed_price, deal.status) == (11.55, "agreed")
    assert (alert.status, alert.txid) == ("approved", po_id) and alert.decided_at is not None

    files = sorted(p.name for p in exec_db.iterdir())
    assert files == [f"{po_id}.eml", f"{po_id}.json"]
    message = email.message_from_bytes((exec_db / f"{po_id}.eml").read_bytes())
    assert message["To"] == "orders@reliance-metro.example" and message["From"] == "sarthi@localhost"
    assert po_id in message["Subject"] and "984 units" in message.get_payload()
    summary = json.loads((exec_db / f"{po_id}.json").read_text(encoding="utf-8"))
    assert (summary["total"], summary["sku"], summary["expected_on"]) == (round(984 * 11.55, 2), "Lays Classic 26g", "2026-10-04")

    again = execute_alert(alert_id)                       # a double-click on Approve
    assert again == {"txid": po_id, "kind": "purchase", "repeat": True}
    assert (count(PurchaseOrder), count(Inbound)) == (before[0] + 1, before[1] + 1)
    assert len(list(exec_db.iterdir())) == 2


def test_manual_order_without_a_draft_still_writes_an_email(exec_db):
    result = execute_payload("purchase", {"supplier_id": "SUP-HUL", "qty": 60, "unit_price": 86.6, "eta_days": 4},
                             sku_id="SKU006", run_id=RUN, source="user")
    with session() as s:
        mail = s.exec(select(OutboxEmail).where(OutboxEmail.ref == result["txid"])).one()
        po = s.get(PurchaseOrder, result["txid"])
    assert mail.status == "queued" and mail.to_addr == "supply@hul-regional.example" and "Colgate Strong 200g" in mail.subject
    assert (po.source, po.mode) == ("user", "multimodal") and count(Negotiation, Negotiation.po_id == result["txid"]) == 0
    assert (exec_db / f"{result['txid']}.eml").exists()


def test_transfer_moves_stock_between_hubs(exec_db):
    before = (stock("SKU002", "WH-DEL"), stock("SKU002", "WH-CHN"))
    result = execute_alert(make_alert("transfer", "SKU002", {"sku_id": "SKU002", "from": "WH-DEL", "to": "WH-CHN", "units": 96}))
    assert result["txid"].startswith("TRF-") and len(result["txid"]) == 9
    assert (stock("SKU002", "WH-DEL"), stock("SKU002", "WH-CHN")) == (before[0] - 96, before[1] + 96)
    with session() as s:
        order = s.get(TransferOrder, result["txid"])
    assert (order.qty, order.from_location, order.to_location, order.status) == (96, "WH-DEL", "WH-CHN", "confirmed")


def test_transfer_never_moves_more_than_the_source_holds(exec_db):
    held = stock("SKU007", "WH-MUM")
    total = held + stock("SKU007", "WH-BLR")
    result = execute_payload("transfer", {"from": "WH-MUM", "to": "WH-BLR", "units": held + 500}, sku_id="SKU007", run_id=RUN)
    with session() as s:
        assert s.get(TransferOrder, result["txid"]).qty == held
    assert stock("SKU007", "WH-MUM") == 0 and stock("SKU007", "WH-MUM") + stock("SKU007", "WH-BLR") == total


def test_campaign_goes_live_and_is_updated_not_duplicated(exec_db):
    payload = {"type": "markdown", "sku_ids": ["SKU002"], "discount_pct": 10, "target_zone": "ghost", "est_impact_value": 1612.3}
    first = execute_alert(make_alert("campaign", "SKU002", payload))
    second = execute_payload("campaign", {**payload, "discount_pct": 15}, sku_id="SKU002", run_id=RUN)
    assert first["txid"] == second["txid"] and first["txid"].startswith("CMP-")
    with session() as s:
        campaigns = s.exec(select(Campaign).where(Campaign.type == "markdown")).all()
    assert len(campaigns) == 1
    assert (campaigns[0].status, campaigns[0].discount_pct, campaigns[0].sku_ids) == ("live", 15.0, ["SKU002"])


def test_audit_becomes_a_logged_task_without_touching_stock(exec_db):
    before = (count(StockDaily), count(Inbound))
    result = execute_alert(make_alert("audit", "SKU005", {"task": "cycle_count", "aisle": "E"}))
    assert result["txid"].startswith("TASK-") and (count(StockDaily), count(Inbound)) == before
    actions = [e for e in Blackboard(RUN).events(kind="action") if e.agent == "executionEngine"]
    assert actions[-1].text == "Count stock in aisle E for SKU005" and actions[-1].value["result"] == result["txid"]
    assert len(actions) >= 5                              # every execution above was logged for the audit trail


def test_bad_requests_are_rejected(exec_db):
    with pytest.raises(LookupError):
        execute_alert(999_999)
    with pytest.raises(ValueError, match="cannot execute"):
        execute_payload("teleport", {}, sku_id="SKU001", run_id=RUN)


async def test_auto_routed_rulings_execute_immediately(exec_db):
    proposal = {
        "id": "transfer:transfer:SKU004", "kind": "transfer", "alert_type": "transfer", "sku_id": "SKU004", "cost": 100.0,
        "par_rescued": 400.0, "payload": {"sku_id": "SKU004", "from": "WH-DEL", "to": "WH-CHN", "units": 50},
        "facts": {"sku": "Tata Salt 1kg", "units": 50, "from_site": "Delhi NCR Hub", "to_site": "Chennai Port", "saving_k": 0.3},
    }
    review = {**proposal, "id": "transfer:transfer:SKU002", "sku_id": "SKU002", "payload": {**proposal["payload"], "sku_id": "SKU002"}}
    state = {"decisions": {"SKU004": {"zone": "money", "risk": 74}, "SKU002": {"zone": "ghost", "risk": 69}},
             "rulings": [{"proposal": proposal, "status": "approved", "confidence": 93, "routed": "auto"},
                         {"proposal": review, "status": "approved", "confidence": 71, "routed": "review"}]}
    before = stock("SKU004", "WH-DEL")
    bb = Blackboard(RUN + 1)
    result = await ExecutionEngine(bb, get_llm(), get_settings(), dict(BALANCED)).run(state)
    assert len(result["alerts"]) == 2 and len(result["executed"]) == 1 and result["executed"][0].startswith("TRF-")
    with session() as s:
        auto, held = (s.get(Alert, i) for i in result["alerts"])
    assert (auto.status, auto.routed, auto.txid, auto.zone, auto.risk) == ("approved", "auto", result["executed"][0], "money", 74)
    assert (held.status, held.txid, held.confidence) == ("open", None, 71)
    assert stock("SKU004", "WH-DEL") == before - 50
    assert [e.value["stance"] for e in bb.events(kind="debate")] == ["execute"]
    summary = next(e for e in bb.events(kind="action") if e.text.startswith("Raised"))
    assert summary.value["result"] == "1 executed automatically"
