"""`/api/bootstrap` against the frontend's data contract, and the validator checks of that contract."""


from fastapi.testclient import TestClient
from sqlmodel import select

from sarthi.api import presenters
from sarthi.db import session
from sarthi.models import Alert
from tests.api.support import (
    ADDED_SKU_FIELDS,
    ALERT_FIELDS,
    ENDPOINTS,
    LIVE_FIELDS,
    MOCK_AISLE_FIELDS,
    MOCK_CANNIBAL_FIELDS,
    MOCK_DISTRIBUTOR_FIELDS,
    MOCK_EXPORTS,
    MOCK_MONTE_CARLO_FIELDS,
    MOCK_RULE_FIELDS,
    MOCK_SKU_FIELDS,
    ZONES,
    boot,
)

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
