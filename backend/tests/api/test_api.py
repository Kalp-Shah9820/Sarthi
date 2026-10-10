"""The HTTP API, end to end, on its own seeded database with one completed run.

The field lists below are copied from `src/data/appData.js` (the frontend's built-in data). If a
presenter drifts from what the screens read, the contract test fails.
"""

import asyncio
import json
import os

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlmodel import func, select

from sarthi.agents import demand_intel
from sarthi.analytics import montecarlo, whatif
from sarthi.api import presenters
from sarthi.api.main import create_app
from sarthi.api.routers import datahub
from sarthi.config import BACKEND_ROOT, get_settings
from sarthi.db import init_db, reset_engine, session
from sarthi.learning import bandit
from sarthi.learning.confidence import arm_key
from sarthi.llm import reset_llm
from sarthi.models import Alert, Campaign, Event, Preference, PurchaseOrder, Run, SkuSnapshot, TransferOrder
from sarthi.orchestrator.runner import run_pipeline
from sarthi.seed.generator import seed_database
from tests.conftest import TEST_SEED, TEST_TODAY

# ── copied from src/data/appData.js ──────────────────────────────────────────
MOCK_SKU_FIELDS = {"id", "name", "cat", "stock", "vel", "margin", "lead", "age", "zone", "risk", "par", "safetyStock",
                   "reorderPoint", "cogs", "shelfLife", "velocityTrend", "historicalStockouts", "holdingCostPct",
                   "forecast", "sales"}
ADDED_SKU_FIELDS = {"daysStock", "esg", "co2", "stockoutProb", "lastReorder", "decisionStatus", "supplier", "tier",
                    "recommendedQty"}       # fields CommandCenter.jsx invents today
MOCK_EXPORTS = {"skuData", "mbaRules", "skuMonteCarlo", "bullwhipData", "cannibalization", "monthLabels",
                "forecastMonths", "distributors", "aisles"}     # zoneInfo and navItems are presentation, not data
MOCK_MONTE_CARLO_FIELDS = {"stockoutProb", "p95Stock", "sigma", "bins", "daysOfCover"}
MOCK_RULE_FIELDS = {"antecedent", "consequent", "confidence", "lift", "support"}
MOCK_CANNIBAL_FIELDS = {"rising", "falling", "rName", "fName", "category", "correlation"}
MOCK_DISTRIBUTOR_FIELDS = {"name", "tat", "reliability", "price", "incentive", "score", "tier", "fulfillment",
                           "defectRate", "capacityLimit", "avgTAT", "tatHistory"}
MOCK_AISLE_FIELDS = {"id", "label", "x", "y", "items", "heat", "connections", "zone"}
# ── from plan_mk9 §2 ─────────────────────────────────────────────────────────
LIVE_FIELDS = {"online", "runId", "llmMode", "strategy", "pipeline", "riskSignals", "mapRisks", "drift", "alerts",
               "auditTrail", "sharedContext", "debate", "esg", "warehouses", "transfers", "campaigns",
               "distributorScores", "replenishment", "coPurchasePairs", "zoneStats", "dataHub"}
ALERT_FIELDS = {"id", "sku", "zone", "risk", "confidence", "msg", "action", "impact", "status", "txid"}
ZONES = {"sweet", "chaos", "ghost", "money"}
ENDPOINTS = {("get", "/api/health"), ("get", "/api/bootstrap"), ("post", "/api/runs"), ("get", "/api/runs/{run_id}/stream"),
             ("post", "/api/alerts/{alert_id}/approve"), ("post", "/api/alerts/{alert_id}/dismiss"), ("post", "/api/orders"),
             ("post", "/api/transfers"), ("post", "/api/campaigns"), ("post", "/api/sandbox/simulate"),
             ("get", "/api/sandbox/debate/stream"), ("post", "/api/datahub/upload/{upload_type}"),
             ("post", "/api/chat"), ("get", "/api/strategy"), ("put", "/api/strategy"), ("post", "/api/voice/intent"),
             ("get", "/api/skus/{sku_id}/explain")}


@pytest.fixture(scope="module")
def client(tmp_path_factory, analysis):
    """A seeded database of its own with one completed run, and a client whose event loop lasts the module."""
    root = tmp_path_factory.mktemp("api")
    previous = {k: os.environ[k] for k in ("SARTHI_DB_PATH", "SARTHI_OUTBOX_PATH")}
    os.environ.update(SARTHI_DB_PATH=str(root / "api.db"), SARTHI_OUTBOX_PATH=str(root / "outbox"))
    get_settings.cache_clear()
    reset_engine()
    reset_llm()
    presenters.reset()
    init_db()
    seed_database(TEST_SEED, today=TEST_TODAY)
    saved_cache = dict(demand_intel._cache)
    demand_intel._cache[demand_intel._fingerprint(get_settings())] = analysis    # same data, so the same analysis holds
    asyncio.run(run_pipeline("test"))
    with TestClient(create_app()) as test_client:
        yield test_client
    demand_intel._cache.clear()
    demand_intel._cache.update(saved_cache)
    reset_engine()
    os.environ.update(previous)
    get_settings.cache_clear()
    reset_engine()
    reset_llm()
    presenters.reset()


def boot(client, lang="EN") -> dict:
    r = client.get("/api/bootstrap", params={"lang": lang})
    assert r.status_code == 200, r.text
    return r.json()


def count(model, *where) -> int:
    with session() as s:
        return s.exec(select(func.count()).select_from(model).where(*where)).one()


def open_alert(kind: str | None = None, exclude: tuple[int, ...] = ()) -> Alert:
    """An open alert of the latest run, preferring one whose action is of `kind`."""
    with session() as s:
        run_id = s.exec(select(func.max(Run.id)).where(Run.dry_run.is_(False), Run.status == "done")).one()
        alerts = [a for a in s.exec(select(Alert).where(Alert.run_id == run_id, Alert.status == "open")).all()
                  if a.id not in exclude]
    assert alerts, "the seeded run should leave open alerts"
    return next((a for a in alerts if a.payload.get("kind") == kind), alerts[0])


def sse(client, url: str, **params) -> tuple[list[dict], bool]:
    """Read a server-sent-event stream to its end: (data messages, whether `event: done` arrived)."""
    messages, done = [], False
    with client.stream("GET", url, params=params) as r:
        assert r.status_code == 200, r.read()
        assert r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if line.startswith("event: done"):
                done = True
            elif line.startswith("data:") and not done:
                messages.append(json.loads(line[5:]))
    return messages, done


# ── bootstrap ────────────────────────────────────────────────────────────────

def test_docs_list_every_endpoint(client):
    paths = client.get("/openapi.json").json()["paths"]
    listed = {(method, path) for path, methods in paths.items() for method in methods}
    assert ENDPOINTS <= listed, ENDPOINTS - listed
    assert client.get("/docs").status_code == 200


def test_bootstrap_matches_the_frontend_contract(client):
    b = boot(client)
    assert set(b) == MOCK_EXPORTS | {"labels", "live"}
    assert len(b["skuData"]) == 10
    for row in b["skuData"]:
        assert set(row) == MOCK_SKU_FIELDS | ADDED_SKU_FIELDS, row["id"]
        assert len(row["forecast"]) == 12 and len(row["sales"]) == 12
        assert all(isinstance(v, int) for v in row["forecast"] + row["sales"])
        assert all(isinstance(row[k], int) for k in ("vel", "stock", "safetyStock", "reorderPoint", "par", "risk", "margin",
                                                     "lead", "daysStock", "recommendedQty"))
        assert row["zone"] in ZONES and 0 <= row["risk"] <= 100 and 0 <= row["stockoutProb"] <= row["risk"]
        assert row["stockoutProb"] == row["risk"] or row["zone"] in ("ghost", "money")      # they differ only for overstock
        assert row["decisionStatus"] in ("critical", "pending", "auto")
        assert isinstance(row["co2"], str) and float(row["co2"]) >= 0
        assert row["supplier"] and row["tier"] in ("Gold", "Silver", "Bronze")

    assert set(b["skuMonteCarlo"]) == {row["id"] for row in b["skuData"]}
    for mc in b["skuMonteCarlo"].values():
        assert set(mc) == MOCK_MONTE_CARLO_FIELDS
        assert len(mc["bins"]) == 20 and all(set(x) == {"range", "count", "highlight"} for x in mc["bins"])

    assert b["mbaRules"] and all(set(r) == MOCK_RULE_FIELDS and isinstance(r["antecedent"], list) for r in b["mbaRules"])
    assert all(set(c) == MOCK_CANNIBAL_FIELDS for c in b["cannibalization"])
    assert set(b["bullwhipData"]) == {"labels", "raw", "smoothed", "reorder"}
    assert all(len(v) == 12 for v in b["bullwhipData"].values())
    assert len(b["monthLabels"]) == 12 and b["forecastMonths"] == b["monthLabels"]
    assert len(b["distributors"]) == 3 and all(set(d) == MOCK_DISTRIBUTOR_FIELDS for d in b["distributors"])
    assert [d["score"] for d in b["distributors"]] == sorted((d["score"] for d in b["distributors"]), reverse=True)
    assert all(len(d["tatHistory"]) == 12 for d in b["distributors"])
    assert len(b["aisles"]) == 11 and all(set(a) == MOCK_AISLE_FIELDS and a["zone"] in ZONES for a in b["aisles"])
    assert b["labels"]["sku001"] == "Amul Butter 500g" and b["labels"]["chennaiPort"] == "Chennai Port"


def test_bootstrap_live_section(client):
    live = boot(client)["live"]
    assert set(live) == LIVE_FIELDS
    assert live["online"] is True and live["llmMode"] == "offline" and isinstance(live["runId"], int)
    assert set(live["strategy"]) == {"mode", "savingsPriority", "safetyStockMultiplier", "leadTimeBuffer", "lastUpdate"}
    assert live["pipeline"]["phase"] == "execute" and live["pipeline"]["signals"] == len(live["riskSignals"])
    for sig in live["riskSignals"]:
        assert {"id", "type", "severity", "skus", "icon", "actionKey", "msg"} <= set(sig)
    for pin in live["mapRisks"]:
        assert pin["type"] in ("cyclone", "port", "heatwave", "strike") and pin["severity"] == pin["severity"].lower()
        assert 0 <= pin["x"] <= 100 and 0 <= pin["y"] <= 100

    assert live["drift"]["days"] == ["1", "2", "3", "4", "5", "6", "7"]
    assert len(live["drift"]["data"]) == 10
    assert all(len(d["zones"]) == 7 and set(d["zones"]) <= ZONES for d in live["drift"]["data"])
    today = {row["id"]: row["zone"] for row in boot(client)["skuData"]}
    assert all(d["zones"][-1] == today[d["id"]] for d in live["drift"]["data"])     # the last day is today

    assert live["alerts"] and all(set(a) == ALERT_FIELDS for a in live["alerts"])
    assert 0 < len(live["auditTrail"]) <= 30 and all(set(x) == {"ts", "agentKey", "text", "result"} for x in live["auditTrail"])
    assert live["sharedContext"] and all(c["key"] != "phase" and c["tone"] in ZONES for c in live["sharedContext"])
    assert live["debate"] and all(set(x) == {"agentKey", "tone", "msg"} and x["tone"] in ZONES for x in live["debate"])

    esg = live["esg"]
    assert len(esg["options"]) == 3 and esg["recommendedIndex"] in (0, 1, 2)
    assert [o["tat"] for o in esg["options"]] == ["1 day", "7 days", "3 days"]         # air, sea, multimodal
    assert [o["cost"] for o in esg["options"]] == ["₹₹₹", "₹", "₹₹"]
    assert esg["options"][0]["co2Pct"] == 100 and esg["options"][0]["co2Key"] == "esgHigh"
    assert set(esg["bySku"]) == set(today) and all(len(v["options"]) == 3 for v in esg["bySku"].values())

    assert len(live["warehouses"]) == 4
    for wh in live["warehouses"]:
        assert set(wh) == {"id", "name", "city", "stock", "capacity", "utilization"}
        assert wh["utilization"] == round(sum(wh["stock"].values()) / wh["capacity"], 2)
    assert all(set(t) == {"fromId", "toId", "toCity", "skuId", "units"} for t in live["transfers"])
    assert {c["type"] for c in live["campaigns"]} <= {"markdown", "bundle", "flash"}
    assert all(c["discount"].endswith("%") and c["estImpact"].startswith("₹") for c in live["campaigns"])
    assert set(live["distributorScores"]) == set(today)
    assert all(isinstance(x["tat"], int) and " " not in x["name"] for rows in live["distributorScores"].values() for x in rows)
    for sku_id, plan in live["replenishment"].items():
        assert plan["units"] > 0 and plan["price"] > 0, sku_id
        assert plan["priceHistory"] and all(set(p) == {"month", "price"} for p in plan["priceHistory"])
    assert len(live["coPurchasePairs"]) <= 7
    assert all(p["from"] == p["from"].lower() and 0 <= p["strength"] <= 100 for p in live["coPurchasePairs"])
    assert set(live["zoneStats"]) == ZONES
    assert set(live["dataHub"]["metrics"]) == {"totalIngested", "freshness", "joinQuality", "alertsGenerated"}


def test_bootstrap_alerts_ordered_by_impact_and_agree_with_sku_status(client):
    b = boot(client)
    with session() as s:
        impact = {a.id: a.impact_value for a in s.exec(select(Alert)).all()}
        flagged = {a.sku_id for a in s.exec(select(Alert).where(Alert.run_id == b["live"]["runId"], Alert.status == "open")).all()}
    values = [impact[a["id"]] for a in b["live"]["alerts"]]
    assert values == sorted(values, reverse=True)
    for row in b["skuData"]:
        assert (row["decisionStatus"] != "auto") == (row["id"] in flagged), row["id"]


def test_bootstrap_languages(client):
    en, hi, ta = boot(client), boot(client, "HI"), boot(client, "TA")
    assert en["live"]["alerts"][0]["msg"] != hi["live"]["alerts"][0]["msg"]
    assert any("ऀ" <= ch <= "ॿ" for ch in hi["live"]["alerts"][0]["msg"])      # Devanagari
    assert any("ऀ" <= ch <= "ॿ" for ch in hi["live"]["debate"][0]["msg"])
    assert ta["live"]["alerts"] == en["live"]["alerts"]                                    # other languages get English
    assert hi["skuData"] == en["skuData"]                                                  # numbers do not depend on language


def test_bootstrap_is_cached_until_something_changes(client, monkeypatch):
    boot(client)
    calls = []
    real = presenters._build
    monkeypatch.setattr(presenters, "_build", lambda run_id, lang: calls.append(lang) or real(run_id, lang))
    boot(client)
    assert calls == []                      # served from memory
    presenters.bump()
    boot(client)
    assert calls == ["EN"]


def test_bootstrap_warming_up_before_the_first_run(client, monkeypatch):
    monkeypatch.setattr(presenters, "latest_run_id", lambda *a, **k: None)
    r = client.get("/api/bootstrap")
    assert r.status_code == 503 and r.json() == {"detail": "warming up"}


def test_unexpected_errors_do_not_leak_details(client, monkeypatch):
    def boom(lang):
        raise RuntimeError("secret internals")

    monkeypatch.setattr(presenters, "bootstrap", boom)
    with TestClient(client.app, raise_server_exceptions=False) as quiet:
        r = quiet.get("/api/bootstrap")
    assert r.status_code == 500 and r.json() == {"detail": "internal error"}
    assert "secret" not in r.text and "Traceback" not in r.text


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


# ── sandbox ──────────────────────────────────────────────────────────────────

def simulate(client, **change) -> dict:
    r = client.post("/api/sandbox/simulate", json={"lead": 5, "demand": 50, "stock": 300, "margin": 20, **change})
    assert r.status_code == 200, r.text
    return r.json()


def test_simulate_shape_and_direction(client):
    base = simulate(client)
    assert set(base) == {"risk", "par", "safetyStock", "sensitivity"}
    assert [p["lt"] for p in base["sensitivity"]] == [f"{d}d" for d in range(1, 16)]
    curve = [p["risk"] for p in base["sensitivity"]]
    assert curve == sorted(curve) and curve[0] < curve[-1]              # longer lead time, more risk
    assert base["sensitivity"][4]["risk"] == base["risk"]               # the 5-day point is the headline number

    by_lead = [simulate(client, lead=lead)["risk"] for lead in (2, 5, 9, 14)]
    by_stock = [simulate(client, stock=stock)["risk"] for stock in (100, 300, 600, 1200)]
    assert by_lead == sorted(by_lead) and by_lead[0] < by_lead[-1]
    assert by_stock == sorted(by_stock, reverse=True) and by_stock[0] > by_stock[-1]
    assert simulate(client, margin=40)["par"] == pytest.approx(2 * base["par"], abs=2)
    assert simulate(client, stock=1500, lead=1) == {**simulate(client, stock=1500, lead=1), "risk": 0, "par": 0}


def test_simulate_agrees_with_the_agents_monte_carlo():
    k = 5.0
    for lead, demand, stock in ((5, 50, 300), (9, 80, 400), (3, 20, 50)):
        fast = whatif.simulate(lead, demand, stock, 20, k, 50.0)
        slow = montecarlo.simulate(stock, [], np.full(whatif.HORIZON_DAYS, float(demand)), k, whatif.lead_samples(lead),
                                   whatif.PATHS, whatif.DEMAND_SEED)
        assert fast["risk"] == round(100 * slow.stockout_prob)
        assert fast["par"] == round(slow.expected_lost_units * 0.20 * 50.0)


def test_simulate_validation_and_no_writes(client):
    runs = count(Run)
    assert client.post("/api/sandbox/simulate", json={"lead": 5, "demand": 50, "stock": 300}).status_code == 422
    assert client.post("/api/sandbox/simulate", json={"lead": -1, "demand": 50, "stock": 300, "margin": 20}).status_code == 422
    simulate(client)
    assert count(Run) == runs


def test_sandbox_debate_stream_is_a_dry_run(client):
    counts = (count(Alert), count(PurchaseOrder), count(TransferOrder), count(SkuSnapshot))
    shown = boot(client)["live"]["runId"]
    lines, done = sse(client, "/api/sandbox/debate/stream", lead=12, demand=80, stock=200, margin=20, lang="EN")
    assert done and lines
    assert all(set(x) == {"agentKey", "tone", "msg"} and x["tone"] in ZONES and x["msg"] for x in lines)
    assert {"rlhfArbiter"} <= {x["agentKey"] for x in lines}                    # the debate reaches a ruling
    assert (count(Alert), count(PurchaseOrder), count(TransferOrder), count(SkuSnapshot)) == counts
    assert boot(client)["live"]["runId"] == shown                               # the screens still show the real run
    with session() as s:
        run = s.exec(select(Run).where(Run.trigger == "sandbox")).one()
    assert run.dry_run and run.status == "done"
    assert run.scenario == {"lead_mult": 2.4, "demand_mult": 1.6}
    assert count(Event, Event.run_id == run.id) > 0

    hindi, done = sse(client, "/api/sandbox/debate/stream", lead=5, demand=50, stock=300, margin=20, lang="HI")
    assert done and any("ऀ" <= ch <= "ॿ" for ch in hindi[0]["msg"])
    with session() as s:
        latest = s.exec(select(Run).where(Run.trigger == "sandbox").order_by(Run.id.desc())).first()
    assert latest.id > run.id and latest.scenario == {"lead_mult": 1.0, "demand_mult": 1.0}
    assert count(Event, Event.run_id == run.id) == 0                            # earlier what-ifs are cleared away
    assert count(Event, Event.run_id == latest.id) > 0


def test_sandbox_stream_rejects_bad_sliders(client):
    assert client.get("/api/sandbox/debate/stream", params={"lead": 0}).status_code == 422


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
        assert s.get(Run, run_id).status == "done"
    assert boot(client)["live"]["runId"] == run_id

    replay, done = sse(client, f"/api/runs/{run_id}/stream")                    # a finished run replays in full
    assert done and [e["id"] for e in replay] == [e["id"] for e in events]
    assert client.get("/api/runs/999999/stream").status_code == 404


# ── data hub ─────────────────────────────────────────────────────────────────

def test_upload_sample_and_reject_bad_files(client, monkeypatch):
    sample = (BACKEND_ROOT / "samples" / "sku.csv").read_bytes()
    r = client.post("/api/datahub/upload/sku", files={"file": ("sku.csv", sample, "text/csv")})
    assert r.status_code == 200, r.text
    assert r.json()["records"] == 10 and r.json()["dropped"] == 0 and set(r.json()) >= {"records", "dropped", "errors", "time"}
    shown = boot(client)["live"]["dataHub"]["uploads"]["sku"]
    assert shown["records"] == 10 and shown["status"] == "done" and shown["progress"] == 100

    bad = client.post("/api/datahub/upload/sku", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert bad.status_code == 400 and bad.json()["detail"]
    assert client.post("/api/datahub/upload/payroll", files={"file": ("sku.csv", sample, "text/csv")}).status_code == 404
    assert client.post("/api/datahub/upload/sku").status_code == 422           # no file
    monkeypatch.setattr(datahub, "MAX_BYTES", 100)
    assert client.post("/api/datahub/upload/sku", files={"file": ("sku.csv", sample, "text/csv")}).status_code == 413


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
        return await remember_feedback(alert, "approved", text, InventiveModel(reply))

    invented = asyncio.run(learn("good call", PreferenceOut(scope="global", directive="other", value=96.0,
                                                           note="Moving stock is approved whenever a hub holds more than a port.")))
    assert (invented.directive, invented.note) == ("other", "good call")
    capped = asyncio.run(learn("that is far too many for us", PreferenceOut(scope="sku", directive="cap_qty", value=250.0)))
    assert (capped.directive, capped.value, capped.note) == ("other", None, "that is far too many for us")
    kept = asyncio.run(learn("we never want over 250 of these", PreferenceOut(scope="sku", directive="cap_qty", value=250.0, note="x")))
    assert (kept.directive, kept.value, kept.target, kept.note) == ("cap_qty", 250.0, alert.sku_id, "we never want over 250 of these")


# ── the frontend's side of the contract ──────────────────────────────────────

def test_validator_frontend_wiring_check(client, tmp_path):
    from sarthi import validate

    result = validate.check_frontend_wiring()
    assert result.status == validate.PASS, result.detail

    def frontend(page: str):
        """A minimal frontend tree whose one page is `page`."""
        root = tmp_path / str(len(list(tmp_path.iterdir())))
        (root / "src" / "api").mkdir(parents=True)
        (root / "src" / "data").mkdir()
        (root / "src" / "pages").mkdir()
        (root / "vite.config.js").write_text("export default { server: { proxy: { '/api': {} } } }", encoding="utf-8")
        (root / "src" / "api" / "client.js").write_text("export const api = {}", encoding="utf-8")
        (root / "src" / "api" / "hydrate.js").write_text("const x = b.skuData && b.live;", encoding="utf-8")
        (root / "src" / "data" / "appData.js").write_text("const live = { online: false };", encoding="utf-8")
        (root / "src" / "pages" / "Page.jsx").write_text(page, encoding="utf-8")
        return validate.check_frontend_wiring(root)

    good = frontend("api.post(`/alerts/${a.id}/approve`, {}); api.stream(`/sandbox/debate/stream?lead=${lead}`); const n = live.alerts ?? [];")
    assert good.status == validate.PASS, good.detail
    gone = frontend('api.post("/payments", {});')
    assert gone.status == validate.FAIL and "POST /api/payments" in gone.detail
    wrong_method = frontend('api.put("/orders", {});')
    assert wrong_method.status == validate.FAIL and "PUT /api/orders" in wrong_method.detail
    unknown_field = frontend('api.get("/health"); const x = live.weatherRadar;')
    assert unknown_field.status == validate.FAIL and "live.weatherRadar" in unknown_field.detail
    assert validate.check_frontend_wiring(tmp_path / "nowhere").status == validate.WARN
