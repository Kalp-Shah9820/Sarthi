"""One function per intent. Each computes its answer from the latest run; none of them asks the model for a number.

A reply is a translation key plus parameters when the frontend already has a fully parameterised
template for it (so all eight UI languages work), and otherwise text in English or Hindi.
"""

import math
import statistics
from dataclasses import dataclass

from sqlalchemy import func
from sqlmodel import select

from sarthi.api import presenters
from sarthi.config import get_settings
from sarthi.db import session
from sarthi.learning import confidence as conf
from sarthi.llm import templates
from sarthi.models import Alert, Sku, SkuSnapshot, SkuSupplier, Supplier
from sarthi.nlq import explain as explainer
from sarthi.nlq import strategy
from sarthi.nlq.intents import Intent

CRITICAL_PCT = 60
OVERSTOCK_ZONES = ("ghost", "money")
STRATEGY_KEYS = {"Cash Flow": "strategyUpdatedCash", "Growth": "strategyUpdatedGrowth"}
# These two frontend templates end in a fixed claim; the key is used only while the data bears it out.
SUPPLIER_CLAIM = ("Reliance Metro", "Metro Cash & Carry")          # "…leads on speed, … on capacity"
CANNIBAL_CLAIM = {"Snacks", "Personal Care"}                       # "…alerts active in Snacks and Personal Care"


@dataclass
class View:
    """What the latest run produced, as the handlers need it."""
    run_id: int
    boot: dict
    metrics: dict[str, dict]        # SKU id -> today's snapshot metrics
    skus: dict[str, Sku]

    @property
    def rows(self) -> list[dict]:
        return self.boot["skuData"]


def load() -> View:
    boot = presenters.bootstrap("EN")           # raises presenters.NotReady before the first run
    run_id = boot["live"]["runId"]
    with session() as s:
        today = s.exec(select(func.max(SkuSnapshot.as_of)).where(SkuSnapshot.run_id == run_id)).one()
        snaps = s.exec(select(SkuSnapshot).where(SkuSnapshot.run_id == run_id, SkuSnapshot.as_of == today)).all()
        skus = {k.id: k for k in s.exec(select(Sku)).all()}
    return View(run_id, boot, {x.sku_id: x.metrics for x in snaps}, skus)


def reply(key: str | None = None, params: dict | None = None, text: str = "", *, strategy_obj: dict | None = None,
          refresh: bool = False, navigate: str | None = None) -> dict:
    return {"key": key, "params": params or {}, "text": text, "strategy": strategy_obj, "refresh": refresh,
            "navigate": navigate}


def say(kind: str, lang: str, **facts) -> str:
    return templates.render("chat", kind, explainer.text_lang(lang), **facts)["text"]


def _k(value: float) -> str:
    return f"{value / 1000:.1f}"


def _critical(view: View) -> list[tuple[str, int, float]]:
    """(name, stockout %, shortage profit at risk) for products above the critical line, worst first."""
    rows = [(row["name"], int(view.metrics[row["id"]].get("risk_shortage", 0)), float(view.metrics[row["id"]].get("par_shortage", 0)))
            for row in view.rows if row["id"] in view.metrics]
    return sorted((r for r in rows if r[1] > CRITICAL_PCT), key=lambda r: -r[1])


# ── read-only answers ────────────────────────────────────────────────────────

def sku_risk(view: View, lang: str) -> dict:
    critical = _critical(view)
    if not critical:
        return reply(text=say("critical_none", lang, threshold=CRITICAL_PCT))
    return reply("criticalSkuRiskRes", {"list": ", ".join(f"{name} ({pct}%)" for name, pct, _ in critical),
                                        "par": f"{sum(par for *_, par in critical) / 1000:.0f}"})


def sku_sweet(view: View, lang: str) -> dict:
    names = [row["name"] for row in view.rows if row["zone"] == "sweet"]
    if not names:
        return reply(text=say("sweet_none", lang))
    return reply("sweetSpotRes", {"list": ", ".join(names), "count": len(names)})


def sku_summary(view: View, lang: str) -> dict:
    zones = [row["zone"] for row in view.rows]
    return reply("skuZoneSummary", {"count": len(zones), **{z: zones.count(z) for z in ("sweet", "chaos", "ghost", "money")}})


def stock_summary(view: View, lang: str) -> dict:
    total = sum(row["stock"] for row in view.rows)
    ghost = [row for row in view.rows if row["zone"] == "ghost"]
    capital = sum(row["stock"] * row["cogs"] for row in view.rows)
    frozen = sum(row["stock"] * row["cogs"] for row in ghost)
    return reply("inventoryStockSummary", {
        "total": total, "count": len(view.rows), "ghostStock": sum(row["stock"] for row in ghost),
        "ghostPct": round(100 * frozen / capital) if capital else 0,      # the template says "% of capital frozen"
        "par": f"{sum(row['par'] for row in view.rows) / 1000:.0f}"})


def suppliers(view: View, lang: str) -> dict:
    ranked = view.boot["distributors"]
    if not ranked:
        return reply(text=say("suppliers_none", lang))
    listing = "; ".join(f"{d['name']} ({d['tier']}, {d['fulfillment'] * 100:.0f}% reliability, {d['avgTAT']}d avg TAT)" for d in ranked)
    fastest = min(ranked, key=lambda d: d["avgTAT"])["name"]
    largest = max(ranked, key=lambda d: d["capacityLimit"])["name"]
    if fastest.startswith(SUPPLIER_CLAIM[0]) and largest.startswith(SUPPLIER_CLAIM[1]):
        return reply("supplierTrack", {"count": len(ranked), "list": listing})
    return reply(text=say("suppliers", lang, count=len(ranked), list=listing, fastest=fastest, largest=largest))


def basket(view: View, lang: str) -> dict:
    rules, cannibals = view.boot["mbaRules"], view.boot["cannibalization"]
    if not rules:
        return reply(text=say("basket_none", lang))
    top = max(rules, key=lambda r: (r["confidence"], r["lift"]))
    params = {"count": len(rules), "rule": f"{{{', '.join(top['antecedent'])}}} leads to {top['consequent']}",
              "conf": round(100 * top["confidence"]), "lift": top["lift"], "cannibalCount": len(cannibals)}
    if {c["category"] for c in cannibals} == CANNIBAL_CLAIM:
        return reply("basketRules", params)
    return reply(text=say("basket", lang, **params))


def zones(view: View, lang: str) -> dict:
    return reply("zoneIkigaiDesc")


def bullwhip(view: View, lang: str) -> dict:
    data = view.boot["bullwhipData"]
    raw, smoothed = data.get("raw", []), data.get("smoothed", [])
    if len(raw) < 2 or not statistics.mean(raw):
        return reply(text=say("bullwhip_none", lang))

    def swing(series: list[float]) -> int:
        return round(100 * statistics.pstdev(series) / statistics.mean(series))

    return reply(text=say("bullwhip", lang, weeks=len(raw), raw_pct=swing(raw), smooth_pct=swing(smoothed),
                          triggers=sum(v is not None for v in data.get("reorder", []))))


def montecarlo(view: View, lang: str) -> dict:
    critical = _critical(view)
    paths = get_settings().mc_paths
    if not critical:
        return reply(text=say("montecarlo_none", lang, paths=paths, threshold=CRITICAL_PCT))
    return reply(text=say("montecarlo", lang, paths=paths, count=len(critical), threshold=CRITICAL_PCT,
                          list=", ".join(f"{name} ({pct}%)" for name, pct, _ in critical)))


def stockout_horizon(view: View, lang: str, days: int) -> dict:
    """Products whose stock, counting what is already on order, will not last `days`."""
    short = sorted(((m.get("cover", m["doc"]), view.skus[sku_id].name) for sku_id, m in view.metrics.items()
                    if sku_id in view.skus and m.get("velocity", 0) > 0 and m.get("cover", m["doc"]) <= days))
    if not short:
        return reply(text=say("stockout_none", lang, days=days))
    unit = "दिन" if explainer.text_lang(lang) == "HI" else "days"
    return reply(text=say("stockout", lang, days=days, count=len(short),
                          list=", ".join(f"{name} ({cover:.1f} {unit})" for cover, name in short)))


def overstock(view: View, lang: str) -> dict:
    lang = explainer.text_lang(lang)
    rows = [row for row in view.rows if row["zone"] in OVERSTOCK_ZONES]
    if not rows:
        return reply(text=say("overstock_none", lang))
    rows.sort(key=lambda row: -row["stock"] * row["cogs"])
    item = "{name} ({zone}, {doc} दिन का स्टॉक, ₹{k}K फँसा)" if lang == "HI" else "{name} ({zone}, {doc} days of cover, ₹{k}K tied up)"
    listing = "; ".join(item.format(name=row["name"], zone=explainer.ZONE_LABEL[lang][row["zone"]],
                                    doc=row["daysStock"], k=_k(row["stock"] * row["cogs"])) for row in rows)
    return reply(text=say("overstock", lang, count=len(rows), list=listing,
                          total_k=_k(sum(row["stock"] * row["cogs"] for row in rows))))


# ── answers that change something ────────────────────────────────────────────

def reorder(view: View, lang: str, sku_id: str, quantity: int) -> dict:
    """Put a reorder request on the Alerts page for one-click approval. It does not place the order."""
    sku, m = view.skus.get(sku_id), view.metrics.get(sku_id)
    if sku is None or m is None:
        return reply(text=say("need_sku", lang))
    row = next(r for r in view.rows if r["id"] == sku_id)
    asked = quantity if quantity > 0 else row["recommendedQty"]
    moq = max(1, sku.moq)
    qty = max(moq, math.ceil(asked / moq) * moq)

    ranked = view.boot["live"]["distributorScores"].get(sku_id, [])      # the Distributor Selector's stored ranking
    with session() as s:
        all_suppliers = s.exec(select(Supplier)).all()
        links = {x.supplier_id: x for x in s.exec(select(SkuSupplier).where(SkuSupplier.sku_id == sku_id)).all()}
    supplier = next((x for r in ranked for x in all_suppliers if x.name.split(" ")[0] == r["name"] and x.id in links), None)
    eta = next((r["tat"] for r in ranked if supplier and supplier.name.split(" ")[0] == r["name"]), None)
    if supplier is None:        # no ranking stored for this product: its usual supplier
        usual = next((x for x in links.values() if x.is_primary), next(iter(links.values()), None))
        supplier = next((x for x in all_suppliers if usual and x.id == usual.supplier_id), None)
    if supplier is None:
        return reply(text=say("reorder_no_supplier", lang, sku=sku.name))
    unit_price = round(links[supplier.id].unit_price, 2)
    eta = int(eta or max(1, round(m.get("lead_mean", sku.lead_time_days))))

    facts = {"sku": sku.name, "supplier": supplier.name, "doc": m["doc"], "lead": round(m["lead_mean"], 1),
             "prob": int(m.get("risk_shortage", 0)), "qty": qty, "par_k": round(m.get("par_shortage", 0) / 1000, 1),
             "unit_price": unit_price, "eta_days": eta, "mode": "multimodal", "asked": asked}
    wording = templates.render("alert", "stockout_reorder", "EN", **facts)
    payload = {"supplier_id": supplier.id, "qty": qty, "unit_price": unit_price, "mode": "multimodal", "eta_days": eta,
               "kind": "purchase", "alert_type": "stockout_reorder", "proposal_id": f"chat:reorder:{sku_id}",
               "facts": facts, "cost": round(qty * unit_price, 2), "par_rescued": float(m.get("par_shortage", 0)),
               "counterfactual": None, "source": "chat"}
    low, high = conf.CONFIDENCE_RANGE
    with session() as s:
        earlier = [a for a in s.exec(select(Alert).where(Alert.run_id == view.run_id, Alert.sku_id == sku_id,
                                                         Alert.status == "open")).all() if a.payload.get("source") == "chat"]
        alert = earlier[0] if earlier else Alert(run_id=view.run_id, sku_id=sku_id, sku_label=sku.name, zone=row["zone"],
                                                 type="stockout_reorder", routed="review")
        alert.risk = int(m.get("risk", 0))
        # A manager's request has no decision to be confident in; this is how far the demand forecast can be trusted.
        alert.confidence = int(min(high, max(low, round(100 * (1 - m.get("wape", 0.5))))))
        alert.impact_value = payload["par_rescued"]
        alert.msg, alert.action, alert.impact, alert.payload = wording["msg"], wording["action"], wording["impact"], payload
        s.add(alert)
        s.commit()
    presenters.bump()
    kind = "reorder_rounded" if qty != asked else "reorder"
    return reply(text=say(kind, lang, **facts, moq=moq, total=round(qty * unit_price)), refresh=True)


async def strategy_set(lang: str, text: str, llm, hint: str = "") -> dict:
    from sarthi.api.routers import runs  # the run it starts lives with the other background runs

    policy = await strategy.compile_policy(text, llm, hint)
    if policy is None:
        return strategy_get(lang)
    applied = strategy.apply_policy(policy, text)
    presenters.bump()
    runs.launch("strategy", queue=True)
    view = presenters.strategy_view()
    if policy.mode in STRATEGY_KEYS:
        return reply(STRATEGY_KEYS[policy.mode], strategy_obj=view, refresh=True)
    return reply(text=say("strategy_balanced", lang, **applied), strategy_obj=view, refresh=True)


def strategy_get(lang: str) -> dict:
    current = presenters.strategy_view()
    return reply("currentStrategyWeights", {"mode": current["mode"], "savings": current["savingsPriority"],
                                            "safety": current["safetyStockMultiplier"]})


READ_ONLY = {"sku_risk": sku_risk, "sku_sweet": sku_sweet, "sku_summary": sku_summary, "stock_summary": stock_summary,
             "suppliers": suppliers, "basket": basket, "zones": zones, "bullwhip": bullwhip, "montecarlo": montecarlo,
             "overstock": overstock}


async def handle(intent: Intent, text: str, lang: str, llm) -> dict:
    """Run the handler for `intent`. Before the first run has finished, data questions get a "warming up" reply."""
    name = intent.name
    if name == "greeting":
        return reply("intelGreetings")
    if name == "unknown":
        return reply("intelDefaultPrompt")
    if name == "strategy_get":
        return strategy_get(lang)
    if name == "strategy_set":
        return await strategy_set(lang, text, llm, intent.mode)
    try:
        if name == "explain_sku":
            if not intent.sku:
                return reply(text=say("need_sku", lang))
            result = await explainer.explain(intent.sku, lang, llm)
            return reply(text=result["text"], navigate=f"/sku/{intent.sku}")
        view = load()
    except (presenters.NotReady, explainer.NotReady):
        return reply(text=say("warming", lang))
    except explainer.NoSuchSku:
        return reply(text=say("need_sku", lang))
    if name == "stockout_horizon":
        return stockout_horizon(view, lang, intent.days)
    if name == "reorder":
        return reorder(view, lang, intent.sku, intent.quantity)
    return READ_ONLY[name](view, lang)
