"""Turns stored results into the shapes the screens already use.

This is the only place where database rows and agent output are renamed to the frontend's field names.
Nothing here computes a decision: every number was produced by an agent during the run being shown.
"""

import statistics
import threading
from datetime import date

from sqlalchemy import func
from sqlmodel import select

from sarthi.agents import debate as debate_lines
from sarthi.agents.distributor_selector import MIN_DISTANCE_KM
from sarthi.analytics import esg
from sarthi.analytics.transfer import haversine_km
from sarthi.blackboard.store import Blackboard, local_time
from sarthi.config import get_settings
from sarthi.db import session
from sarthi.ingest.loader import data_health
from sarthi.llm import get_llm, templates
from sarthi.models import (
    Aisle,
    Alert,
    Campaign,
    Location,
    PriceHistory,
    RiskSignal,
    Run,
    Sku,
    SkuSnapshot,
    SkuSupplier,
    StockDaily,
    StrategyPolicy,
    Supplier,
    Upload,
)
from sarthi.orchestrator.runner import active_strategy, latest_run_id
from sarthi.seed.catalog import REGIONS, STORE_ID

ZONES = ("sweet", "chaos", "ghost", "money")
TONE = {"forecaster": "ghost", "esgGuardian": "ghost", "riskAgent": "chaos", "cfoAgent": "money",
        "negotiator": "sweet", "executionEngine": "sweet", "overstockResolver": "sweet", "rlhfArbiter": "money"}
MAP_TYPE = {"LOGISTICS": "port", "TRANSPORT": "strike"}
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
DRIFT_DAYS = 7
AUDIT_ROWS = 12
PRICE_MONTHS = 6
TOP_PAIRS = 7
REFERENCE_KM, REFERENCE_QTY = 500.0, 500      # the order behind the general shipping cards
DEFAULT_K, DEFAULT_PRICE = 5.0, 50.0

_lock = threading.Lock()
_cache: dict[tuple, dict] = {}
_stats: dict[tuple, tuple[float, float]] = {}
_writes = 0


class NotReady(Exception):
    """No run has completed yet, so there is nothing to show."""


def bump() -> None:
    """Call after anything that changes what the screens show; the next bootstrap is rebuilt."""
    global _writes
    with _lock:
        _writes += 1


def reset() -> None:
    """Forget everything cached (used when the database is swapped, e.g. by tests)."""
    with _lock:
        _cache.clear()
        _stats.clear()


def tone_of(agent: str) -> str:
    return TONE.get(agent, "ghost")


def debate_line(event: dict, lang: str = "EN") -> dict:
    """One debate event as the What-If and Alerts panels draw it. Hindi is re-rendered from the stored facts."""
    text = event.get("text") or ""
    facts = (event.get("value") or {}).get("facts", {})
    if lang == "HI" and facts.get("key"):
        try:
            text = debate_lines.render(facts["key"], "HI", facts)
        except (KeyError, IndexError):
            pass                    # a line without a template keeps the wording it was stored with
    return {"agentKey": event.get("agent", ""), "tone": tone_of(event.get("agent", "")), "msg": text}


def event_view(event: dict) -> dict:
    """A blackboard event for the run stream."""
    return {"id": event.get("id"), "kind": event.get("kind"), "agentKey": event.get("agent"), "phase": event.get("phase"),
            "key": event.get("key"), "text": event.get("text"), "value": event.get("value"),
            "skuId": event.get("sku_id"), "ts": local_time(event["ts"]) if event.get("ts") else None}


def catalogue_stats() -> tuple[float, float]:
    """(typical demand dispersion, median shelf price) for the What-If sliders, which describe no particular SKU."""
    run_id = latest_run_id()
    key = (str(get_settings().db_file), run_id)
    if key not in _stats:
        ks = [m["k"] for m in Blackboard(run_id).metrics(key="forecast") if m.get("k")] if run_id is not None else []
        with session() as s:
            prices = list(s.exec(select(Sku.price)).all())
        _stats.clear()
        _stats[key] = (statistics.median(ks) if ks else DEFAULT_K, statistics.median(prices) if prices else DEFAULT_PRICE)
    return _stats[key]


# ── pieces ───────────────────────────────────────────────────────────────────

def _money_k(value: float) -> str:
    return f"₹{value / 1000:.1f}K"


def _shipping_cards(options: list[dict]) -> list[dict]:
    """The three shipping cards (air, sea, multimodal) from costed options."""
    highest = max((o["co2_kg"] for o in options), default=0) or 1
    by_cost = sorted(range(len(options)), key=lambda i: options[i]["freight_cost"])
    cards = []
    for i, option in enumerate(options):
        rank = by_cost.index(i) + 1
        pct = max(1, round(100 * option["co2_kg"] / highest))
        cards.append({"tat": debate_lines.days_text(option["days"]), "cost": "₹" * rank, "costVal": rank, "co2Pct": pct,
                      "co2Key": "esgHigh" if pct > 70 else "esgMedium" if pct > 40 else "esgLow"})
    return cards


def _costed(name: str, qty: float, km: float) -> list[dict]:
    weight = esg.parse_unit_weight(name)
    return esg.options(max(qty, 1), weight, km, esg.base_freight(max(qty, 1), weight, km))


def _drift(s, sku_ids: list[str]) -> dict:
    """Zone of each SKU on the last seven days on record, oldest first."""
    rows = s.exec(select(SkuSnapshot.as_of, SkuSnapshot.sku_id, SkuSnapshot.zone).order_by(SkuSnapshot.run_id)).all()
    latest: dict[tuple[date, str], str] = {(day, sku_id): zone for day, sku_id, zone in rows}   # later runs win
    days = sorted({day for day, _ in latest})[-DRIFT_DAYS:]
    data = []
    for sku_id in sku_ids:
        zones = [latest[(day, sku_id)] for day in days if (day, sku_id) in latest]
        if not zones:
            continue
        zones = [zones[0]] * (DRIFT_DAYS - len(zones)) + zones     # a product newer than a week starts where it began
        data.append({"id": sku_id, "zones": zones})
    return {"days": [str(i) for i in range(1, DRIFT_DAYS + 1)], "data": data}


def _warehouses(s) -> list[dict]:
    sites = s.exec(select(Location).where(Location.kind == "warehouse").order_by(Location.id)).all()
    last = (select(StockDaily.sku_id, StockDaily.location_id, func.max(StockDaily.day).label("day"))
            .where(StockDaily.location_id.in_([x.id for x in sites]))
            .group_by(StockDaily.sku_id, StockDaily.location_id).subquery())
    rows = s.exec(select(StockDaily.location_id, StockDaily.sku_id, StockDaily.on_hand).join(
        last, (StockDaily.sku_id == last.c.sku_id) & (StockDaily.location_id == last.c.location_id)
        & (StockDaily.day == last.c.day))).all()
    held: dict[str, dict[str, int]] = {}
    for location_id, sku_id, on_hand in rows:
        if on_hand > 0:
            held.setdefault(location_id, {})[sku_id] = int(on_hand)
    return [{"id": x.id, "name": x.name, "city": x.city, "stock": dict(sorted(held.get(x.id, {}).items())),
             "capacity": x.capacity,
             "utilization": round(sum(held.get(x.id, {}).values()) / x.capacity, 2) if x.capacity else 0.0}
            for x in sites]


def _alert_view(alert: Alert, lang: str) -> dict:
    msg, action, impact = alert.msg, alert.action, alert.impact
    if lang == "HI":
        try:
            wording = templates.render("alert", alert.type, "HI", **alert.payload.get("facts", {}))
            msg, action, impact = wording["msg"], wording["action"], wording["impact"]
        except (KeyError, IndexError):
            pass
    return {"id": alert.id, "sku": alert.sku_label, "zone": alert.zone, "risk": alert.risk, "confidence": alert.confidence,
            "msg": msg, "action": action, "impact": impact, "status": alert.status, "txid": alert.txid}


def _risk_views(signals: list[RiskSignal]) -> tuple[list[dict], list[dict]]:
    cards, pins = [], []
    for sig in signals:
        card = {"id": sig.id, "type": sig.type, "severity": sig.severity, "skus": sig.skus_at_risk, "icon": sig.icon,
                "actionKey": "reroute" if sig.type == "TRANSPORT" else "viewImpact", "msg": sig.msg}
        if sig.msg_key:
            card["msgKey"] = sig.msg_key
        cards.append(card)
        region = REGIONS.get(sig.region_key)
        if region:
            kind = MAP_TYPE.get(sig.type) or ("heatwave" if "heatwave" in sig.msg.lower() else "cyclone")
            pins.append({"id": sig.region_key, "type": kind, "x": region["x"], "y": region["y"],
                         "severity": sig.severity.lower(), "icon": sig.icon})
    return cards, pins


# ── the whole payload ────────────────────────────────────────────────────────

def _build(run_id: int, lang: str) -> dict:
    bb = Blackboard(run_id)
    metric: dict[str, dict] = {}
    forecast: dict[str, dict] = {}
    for event in bb.events(kind="metric"):
        if event.key == "forecast" and event.sku_id:
            forecast[event.sku_id] = event.value
        elif event.key and not event.sku_id:
            metric[event.key] = event.value
    intel, ranked = metric.get("demand_intel", {}), metric.get("distributors", {})

    with session() as s:
        run = s.get(Run, run_id)
        skus = s.exec(select(Sku).order_by(Sku.id)).all()
        suppliers = {x.id: x for x in s.exec(select(Supplier)).all()}
        links = s.exec(select(SkuSupplier)).all()
        store = s.get(Location, STORE_ID)
        today = s.exec(select(func.max(SkuSnapshot.as_of)).where(SkuSnapshot.run_id == run_id)).one()
        snaps = {x.sku_id: x for x in s.exec(
            select(SkuSnapshot).where(SkuSnapshot.run_id == run_id, SkuSnapshot.as_of == today)).all()}
        alerts = s.exec(select(Alert).where(Alert.run_id == run_id, Alert.status.in_(("open", "approved")))
                        .order_by(Alert.impact_value.desc(), Alert.id)).all()
        signals = s.exec(select(RiskSignal).where(RiskSignal.active).order_by(RiskSignal.id)).all()
        aisles = s.exec(select(Aisle).order_by(Aisle.id)).all()
        prices = s.exec(select(PriceHistory).order_by(PriceHistory.month)).all()
        live_campaigns = {c.type for c in s.exec(select(Campaign).where(Campaign.status == "live")).all()}
        uploads = s.exec(select(Upload).where(Upload.status == "done").order_by(Upload.id)).all()
        policy = s.exec(select(StrategyPolicy).where(StrategyPolicy.active).order_by(StrategyPolicy.id.desc())).first()
        drift = _drift(s, [k.id for k in skus])
        warehouses = _warehouses(s)

    list_price = {(x.sku_id, x.supplier_id): x.unit_price for x in links}
    primary = {x.sku_id: x.supplier_id for x in links if x.is_primary}
    open_alert = {a.sku_id for a in alerts if a.status == "open"}
    purchase = {}
    for a in alerts:                                    # highest impact first, so the first purchase per SKU wins
        if a.payload.get("kind") == "purchase":
            purchase.setdefault(a.sku_id, a.payload)
    history: dict[str, list[dict]] = {}
    for row in prices:
        history.setdefault(row.sku_id, []).append(
            {"month": MONTHS[int(row.month[5:7]) - 1], "price": round(row.unit_price, 2)})
    mode = (run.strategy or {}).get("mode", "Balanced")

    sku_data, monte_carlo, esg_by_sku, replenishment = [], {}, {}, {}
    for sku in skus:
        snap = snaps.get(sku.id)
        if snap is None:
            continue                                    # added after this run; it appears after the next one
        m, f, order = snap.metrics, forecast.get(sku.id, {}), purchase.get(sku.id)
        supplier = suppliers.get((order or {}).get("supplier_id") or m.get("supplier_id") or primary.get(sku.id))
        usual = suppliers.get(m.get("supplier_id") or primary.get(sku.id))
        vel, lead, risk = round(m["velocity"]), round(m["lead_mean"]), int(m["risk"])
        qty = int(order["qty"]) if order else round(vel * lead * 1.3)

        km = REFERENCE_KM
        if supplier and store:
            km = max(MIN_DISTANCE_KM, haversine_km(supplier.lat, supplier.lon, store.lat, store.lon))
        options = _costed(sku.name, qty, km)
        chosen = esg.MODE_ORDER.index(order["mode"]) if order and order.get("mode") in esg.MODE_ORDER else esg.recommend(options, mode)
        esg_by_sku[sku.id] = {"recommendedIndex": chosen, "options": _shipping_cards(options)}
        unit_price = (order or {}).get("unit_price") or list_price.get((sku.id, supplier.id if supplier else ""), sku.cogs)
        replenishment[sku.id] = {"units": qty, "price": round(float(unit_price), 2),
                                 "priceHistory": history.get(sku.id, [])[-PRICE_MONTHS:]}

        sku_data.append({
            "id": sku.id, "name": sku.name, "cat": sku.category, "stock": int(m["on_hand"]), "vel": vel,
            "margin": round(100 * (sku.price - sku.cogs) / sku.price) if sku.price else 0, "lead": lead,
            "age": int(m.get("age", 0)), "zone": snap.zone, "risk": risk, "par": round(m["par"]),
            "safetyStock": int(m["ss"]), "reorderPoint": int(m["rop"]), "cogs": sku.cogs,
            "shelfLife": sku.shelf_life_days, "velocityTrend": round(m.get("velocity_trend", 0.0), 2),
            "historicalStockouts": int(m.get("historical_stockouts", 0)), "holdingCostPct": sku.holding_cost_pct,
            "forecast": [round(v) for v in f.get("monthly_backcast", [])],
            "sales": [round(v) for v in f.get("monthly_actual", [])],
            "daysStock": round(m["doc"]), "esg": round(usual.esg_score) if usual else 0,
            "co2": f"{options[chosen]['co2_kg']:.2f}", "stockoutProb": risk,
            "lastReorder": m.get("last_reorder", ""),
            "decisionStatus": ("critical" if risk > 60 else "pending") if sku.id in open_alert else "auto",
            "supplier": usual.name if usual else "", "tier": usual.tier if usual else "", "recommendedQty": qty,
        })
        monte_carlo[sku.id] = {"stockoutProb": risk, "p95Stock": int(m.get("p95_stock", 0)), "sigma": m.get("sigma", 0),
                               "bins": m.get("mc_bins", []), "daysOfCover": f"{m['doc']:.1f}"}

    # aisles: live heat and links from this run's baskets; the zone is where most of the aisle's products sit
    heat, connections = intel.get("heat", {}), intel.get("connections", {})
    zone_by_sku = {row["id"]: row["zone"] for row in sku_data}
    aisle_zones: dict[str, list[str]] = {}
    for sku in skus:
        if sku.id in zone_by_sku:
            aisle_zones.setdefault(sku.aisle_id, []).append(zone_by_sku[sku.id])
    aisle_rows = [{"id": a.id, "label": a.label, "x": a.x, "y": a.y, "items": a.items, "heat": heat.get(a.id, a.heat),
                   "connections": connections.get(a.id, a.connections),
                   "zone": max(ZONES, key=aisle_zones[a.id].count) if a.id in aisle_zones else a.zone} for a in aisles]

    zone_stats = {}
    for zone in ZONES:
        risks = [snaps[row["id"]].metrics["stockout_prob"] * 100 for row in sku_data if row["zone"] == zone]
        confidences = [a.confidence for a in alerts if a.zone == zone]
        zone_stats[zone] = {"avgRisk": f"{statistics.mean(risks):.1f}%" if risks else "—",
                            "confidence": f"{statistics.mean(confidences):.1f}%" if confidences else "—"}

    reference = sorted(skus, key=lambda k: esg.parse_unit_weight(k.name))[len(skus) // 2] if skus else None
    general = _costed(reference.name if reference else "", REFERENCE_QTY, REFERENCE_KM)
    cards, pins = _risk_views(signals)
    context = bb.context()
    phase = next((c["value"] for c in context if c["key"] == "phase"), "")
    latest_upload = {u.type: u for u in uploads}        # ordered by id, so the newest of each type wins
    strategy = active_strategy()
    bullwhip = intel.get("bullwhip", {})
    months = intel.get("month_labels", [])

    return {
        "skuData": sku_data,
        "skuMonteCarlo": monte_carlo,
        "mbaRules": [{k: r[k] for k in ("antecedent", "consequent", "confidence", "lift", "support")} for r in intel.get("rules", [])],
        "cannibalization": [{k: c[k] for k in ("rising", "falling", "rName", "fName", "category", "correlation")}
                            for c in intel.get("cannibals", [])],
        "bullwhipData": {k: bullwhip.get(k, []) for k in ("labels", "raw", "smoothed", "reorder")},
        "monthLabels": months,
        "forecastMonths": months,
        "distributors": sorted(({k: v for k, v in row.items() if k != "id"} for row in ranked.get("global", [])),
                               key=lambda row: row["score"], reverse=True),
        "aisles": aisle_rows,
        "labels": {**{k.id.lower(): k.name for k in skus}, **{key: region["label"] for key, region in REGIONS.items()}},
        "live": {
            "online": True,
            "runId": run_id,
            "llmMode": "offline",
            "strategy": {**strategy, "lastUpdate": local_time(policy.created_at if policy else run.started_at)},
            "pipeline": {"signals": len(signals), "phase": phase},
            "riskSignals": cards,
            "mapRisks": pins,
            "drift": drift,
            "alerts": [_alert_view(a, lang) for a in alerts],
            "auditTrail": [{"ts": local_time(e.ts), "agentKey": e.agent, "text": e.text or "", "result": e.value.get("result", "")}
                           for e in reversed(bb.events(kind="action")[-AUDIT_ROWS:])],
            "sharedContext": [{"key": c["key"], "value": c["value"], "agentKey": c["agent"], "tone": c["tone"],
                               "updated": local_time(c["ts"])} for c in context if c["key"] != "phase"],
            "debate": [debate_line({"agent": e.agent, "text": e.text, "value": e.value}, lang) for e in bb.events(kind="debate")],
            "esg": {"recommendedIndex": esg.recommend(general, mode), "options": _shipping_cards(general), "bySku": esg_by_sku},
            "warehouses": warehouses,
            "transfers": [{"fromId": t["from"], "toId": t["to"], "toCity": t["toCity"], "skuId": t["skuId"], "units": t["units"]}
                          for t in metric.get("transfers", {}).get("items", [])],
            "campaigns": [{"type": c["type"], "target": c["target"], "discount": f"{c['discount']:g}%",
                           "estImpact": _money_k(c["estImpactValue"]),
                           "status": "live" if c["type"] in live_campaigns else "proposed"}
                          for c in metric.get("campaigns", {}).get("items", [])],
            "distributorScores": ranked.get("per_sku", {}),
            "replenishment": replenishment,
            "coPurchasePairs": sorted(intel.get("pairs", []), key=lambda p: p["strength"], reverse=True)[:TOP_PAIRS],
            "zoneStats": zone_stats,
            "dataHub": {"metrics": data_health(),
                        "uploads": {u.type: {"progress": 100, "status": "done", "records": u.records,
                                             "time": local_time(u.created_at)} for u in latest_upload.values()}},
        },
    }


def bootstrap(lang: str = "EN") -> dict:
    """Everything the screens show, from the latest completed run. Raises NotReady before the first run finishes."""
    run_id = latest_run_id()
    if run_id is None:
        raise NotReady("warming up")
    lang = lang.upper() if lang.upper() in ("EN", "HI") else "EN"      # other languages receive English
    with _lock:
        key = (str(get_settings().db_file), run_id, lang, _writes)
        cached = _cache.get(key)
    if cached is None:
        cached = _build(run_id, lang)
        with _lock:
            for old in [k for k in _cache if k[:2] != key[:2] or k[3] != key[3]]:
                del _cache[old]
            _cache[key] = cached
    return {**cached, "live": {**cached["live"], "llmMode": get_llm().mode}}
