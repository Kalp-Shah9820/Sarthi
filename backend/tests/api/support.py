"""Shared by the API tests: the frontend's field lists (copied from `src/data/appData.js`, so a presenter
that drifts from what the screens read fails loudly) and small request helpers."""

import json

from sqlmodel import col, func, select

from sarthi.db import session
from sarthi.models import Alert, Run

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
        run_id = s.exec(select(func.max(col(Run.id))).where(col(Run.dry_run).is_(False), col(Run.status) == "done")).one()
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
