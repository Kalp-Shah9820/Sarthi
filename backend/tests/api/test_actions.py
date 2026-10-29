"""The buttons: approve and dismiss, orders, transfers, campaigns, pipeline runs, and their housekeeping."""

import asyncio
from typing import Any, cast

import pytest
from sqlmodel import select

from sarthi.api import presenters
from sarthi.config import get_settings
from sarthi.db import session
from sarthi.learning import bandit
from sarthi.learning.confidence import arm_key
from sarthi.models import Alert, Campaign, Preference, PurchaseOrder, Run, TransferOrder
from tests.api.support import (
    ENDPOINTS,
    boot,
    count,
    open_alert,
    sse,
)
from tests.conftest import must

# ── alerts ───────────────────────────────────────────────────────────────────

def test_approve_executes_once_and_learns_once(client):
    alert = open_alert("transfer")
    arm = arm_key(alert.zone, alert.type)
    alpha, beta = bandit.get_arm(arm)
    r = client.post(f"/api/alerts/{alert.id}/approve")
    assert r.status_code == 200, r.text
    txid = r.json()["txid"]
    assert txid and r.json()["status"] == "approved"
    assert bandit.get_arm(arm) == (alpha + 1, beta)

    again = client.post(f"/api/alerts/{alert.id}/approve", json={"feedback": "good"})
    assert again.json() == {"txid": txid, "status": "approved", "runStarted": False}
    assert bandit.get_arm(arm) == (alpha + 1, beta)                 # exactly one update
    if alert.payload.get("kind") == "transfer":
        assert count(TransferOrder, TransferOrder.id == txid) == 1
    shown = next(a for a in boot(client)["live"]["alerts"] if a["id"] == alert.id)
    assert shown["status"] == "approved" and shown["txid"] == txid
    assert client.post(f"/api/alerts/{alert.id}/dismiss").status_code == 409


def test_dismiss_with_feedback_becomes_a_standing_rule(client):
    alert = open_alert("purchase")
    arm = arm_key(alert.zone, alert.type)
    alpha, beta = bandit.get_arm(arm)
    orders = count(PurchaseOrder)
    r = client.post(f"/api/alerts/{alert.id}/dismiss", json={"feedback": "max 300 units"})
    assert r.status_code == 200 and r.json() == {"status": "dismissed"}
    assert bandit.get_arm(arm) == (alpha, beta + 1)
    with session() as s:
        rule = s.exec(select(Preference).where(Preference.directive == "cap_qty")).first()
        stored = s.get(Alert, alert.id)
        assert stored is not None
    assert rule is not None and rule.value == 300 and rule.target == alert.sku_id
    assert stored.status == "dismissed" and stored.feedback == "max 300 units"
    assert count(PurchaseOrder) == orders                           # nothing was executed
    assert alert.id not in [a["id"] for a in boot(client)["live"]["alerts"]]

    assert client.post(f"/api/alerts/{alert.id}/dismiss").json() == {"status": "dismissed"}
    assert bandit.get_arm(arm) == (alpha, beta + 1)
    assert client.post(f"/api/alerts/{alert.id}/approve").status_code == 409


def test_unknown_alert_is_404(client):
    for verb in ("approve", "dismiss"):
        r = client.post(f"/api/alerts/999999/{verb}")
        assert r.status_code == 404 and "not found" in r.json()["detail"]

# ── orders, transfers, campaigns ─────────────────────────────────────────────

def test_order_is_recorded_and_shown_in_the_audit_trail(client):
    before = count(PurchaseOrder)
    r = client.post("/api/orders", json={"skuId": "SKU001", "distributor": "Reliance Metro WH", "units": 120, "price": 198.5,
                                         "esgIndex": 2})
    assert r.status_code == 200, r.text
    order_id = r.json()["orderId"]
    assert order_id.startswith("PO-") and r.json()["expectedDelivery"].endswith("days")
    with session() as s:
        po = s.get(PurchaseOrder, order_id)
        assert po is not None
    assert count(PurchaseOrder) == before + 1
    assert (po.sku_id, po.supplier_id, po.qty, po.unit_price, po.mode, po.source) == ("SKU001", "SUP-REL", 120, 198.5, "multimodal", "user")
    assert (get_settings().outbox_dir / f"{order_id}.eml").exists()
    top = boot(client)["live"]["auditTrail"][0]
    assert order_id in top["result"] or order_id in top["text"]
    assert top["agentKey"] == "executionEngine"


def test_order_that_breaks_a_rule_is_still_placed_and_the_override_is_audited(client):
    r = client.post("/api/orders", json={"skuId": "SKU003", "distributor": "Reliance", "units": 1, "price": 11.0, "esgIndex": 0})
    assert r.status_code == 200, r.text                     # the short name the charts show resolves too
    with session() as s:
        po = s.get(PurchaseOrder, r.json()["orderId"])
        assert po is not None
    assert po.qty == 1 and po.mode == "air" and po.supplier_id == "SUP-REL"
    trail = boot(client)["live"]["auditTrail"]
    objection = next(x for x in trail if x["agentKey"] == "cfoAgent" and "minimum order" in x["text"])
    assert "manager" in objection["result"]


def test_order_validation(client):
    good = {"skuId": "SKU001", "distributor": "Reliance Metro WH", "units": 10, "price": 100}
    assert client.post("/api/orders", json={**good, "skuId": "NOPE"}).status_code == 404
    assert client.post("/api/orders", json={**good, "distributor": "Nobody Traders"}).status_code == 404
    assert client.post("/api/orders", json={**good, "units": 0}).status_code == 422
    assert client.post("/api/orders", json={**good, "esgIndex": 7}).status_code == 422
    assert client.post("/api/orders", json={"skuId": "SKU001"}).status_code == 422


def test_transfer_moves_stock_between_warehouses(client):
    def stock() -> dict:
        return {w["id"]: w["stock"] for w in boot(client)["live"]["warehouses"]}

    before = stock()
    source = max(before, key=lambda wh: max(before[wh].values(), default=0))
    sku_id = max(before[source], key=before[source].get)
    target = next(wh for wh in before if wh != source)
    r = client.post("/api/transfers", json={"skuId": sku_id, "fromId": source, "toId": target, "units": 50})
    assert r.status_code == 200, r.text
    assert r.json()["transferId"].startswith("TRF-") and r.json()["units"] == 50
    after = stock()
    assert after[source][sku_id] == before[source][sku_id] - 50
    assert after[target].get(sku_id, 0) == before[target].get(sku_id, 0) + 50

    rest = after[source][sku_id]
    clamped = client.post("/api/transfers", json={"skuId": sku_id, "fromId": source, "toId": target, "units": 999_999})
    assert clamped.json()["units"] == rest                  # never more than the source holds
    assert sku_id not in stock()[source]

    assert client.post("/api/transfers", json={"skuId": sku_id, "fromId": source, "toId": source, "units": 5}).status_code == 400
    assert client.post("/api/transfers", json={"skuId": sku_id, "fromId": "WH-XXX", "toId": target, "units": 5}).status_code == 404
    assert client.post("/api/transfers", json={"skuId": "NOPE", "fromId": source, "toId": target, "units": 5}).status_code == 404


def test_campaign_goes_live(client):
    proposed = boot(client)["live"]["campaigns"]
    assert proposed, "the seeded run should cost at least one campaign"
    kind = proposed[0]["type"]
    r = client.post("/api/campaigns", json={"type": kind})
    assert r.status_code == 200 and r.json() == {"status": "live"}
    assert count(Campaign, Campaign.type == kind, Campaign.status == "live") == 1
    assert next(c for c in boot(client)["live"]["campaigns"] if c["type"] == kind)["status"] == "live"
    assert client.post("/api/campaigns", json={"type": "giveaway"}).status_code == 404

# ── runs ─────────────────────────────────────────────────────────────────────

def test_run_can_be_started_watched_and_then_shown(client):
    before = boot(client)["live"]["runId"]
    r = client.post("/api/runs")
    assert r.status_code == 200
    run_id = r.json()["runId"]
    assert run_id > before
    events, done = sse(client, f"/api/runs/{run_id}/stream")
    assert done
    kinds = {e["kind"] for e in events}
    assert {"context", "action", "debate"} <= kinds and "metric" not in kinds
    assert [e["value"]["v"] for e in events if e["key"] == "phase"] == ["sense", "decide", "resolve", "execute"]
    assert all({"id", "kind", "agentKey", "phase", "key", "text", "value", "skuId", "ts"} == set(e) for e in events)
    with session() as s:
        assert must(s.get(Run, run_id)).status == "done"
    assert boot(client)["live"]["runId"] == run_id

    replay, done = sse(client, f"/api/runs/{run_id}/stream")                    # a finished run replays in full
    assert done and [e["id"] for e in replay] == [e["id"] for e in events]
    assert client.get("/api/runs/999999/stream").status_code == 404

# ── housekeeping ─────────────────────────────────────────────────────────────

def test_a_replaced_what_if_is_recorded_as_cancelled(client):
    from sarthi.orchestrator.runner import execute_run, start_run

    async def start_then_cancel() -> int:
        run_id = start_run("sandbox", scenario={"lead_mult": 2.0}, dry_run=True)
        task = asyncio.create_task(execute_run(run_id))
        await asyncio.sleep(0)              # let it begin
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return run_id

    run_id = asyncio.run(start_then_cancel())
    with session() as s:
        run = s.get(Run, run_id)
        assert run is not None
    assert run.status == "cancelled" and run.finished_at is not None
    assert boot(client)["live"]["runId"] != run_id


def test_validator_http_api_check(client, monkeypatch):
    from sarthi import validate

    result = validate.check_http_api()
    assert result.status == validate.PASS, result.detail
    assert f"{len(validate.HTTP_PATHS)} endpoints" in result.detail
    assert {path for _, path in ENDPOINTS} == set(validate.HTTP_PATHS)
    monkeypatch.setattr(presenters, "latest_run_id", lambda *a, **k: None)
    assert validate.check_http_api().status == validate.WARN        # no run yet is a warning, not a breakage


def test_model_may_classify_feedback_but_never_author_it(client):
    """Found in the live check: for "good call" the model stored an invented sentence and an invented number."""
    from sarthi.learning.memory import PreferenceOut, remember_feedback

    class InventiveModel:
        def __init__(self, reply: PreferenceOut):
            self.reply = reply

        async def json(self, schema, system, user, *, fallback, **kw):
            return self.reply

    alert = open_alert()

    async def learn(text: str, reply: PreferenceOut) -> Preference:
        return must(await remember_feedback(alert, "approved", text, cast(Any, InventiveModel(reply))))

    invented = asyncio.run(learn("good call", PreferenceOut(scope="global", directive="other", value=96.0,
                                                           note="Moving stock is approved whenever a hub holds more than a port.")))
    assert (invented.directive, invented.note) == ("other", "good call")
    capped = asyncio.run(learn("that is far too many for us", PreferenceOut(scope="sku", directive="cap_qty", value=250.0)))
    assert (capped.directive, capped.value, capped.note) == ("other", None, "that is far too many for us")
    kept = asyncio.run(learn("we never want over 250 of these", PreferenceOut(scope="sku", directive="cap_qty", value=250.0, note="x")))
    assert (kept.directive, kept.value, kept.target, kept.note) == ("cap_qty", 250.0, alert.sku_id, "we never want over 250 of these")
