"""Why a product is where it is, in one short paragraph built from stored facts."""

from sqlalchemy import func
from sqlmodel import select

from sarthi.db import session
from sarthi.llm import prompts, templates
from sarthi.models import Alert, Sku, SkuSnapshot
from sarthi.orchestrator.runner import latest_run_id

ZONE_LABEL = {
    "EN": {"sweet": "Sweet Spot", "chaos": "Chaos Zone", "ghost": "Ghost Zone", "money": "Money Pit"},
    "HI": {"sweet": "स्वीट स्पॉट", "chaos": "केओस ज़ोन", "ghost": "घोस्ट ज़ोन", "money": "मनी पिट"},
}


class NoSuchSku(LookupError):
    pass


class NotReady(Exception):
    """No completed run holds this product yet."""


def text_lang(lang: str) -> str:
    return "HI" if (lang or "").upper() == "HI" else "EN"       # free text is English or Hindi only


def facts_for(sku_id: str, lang: str = "EN") -> tuple[str, dict]:
    """(template kind, flat facts) for one product from the latest run."""
    lang = text_lang(lang)
    run_id = latest_run_id()
    with session() as s:
        sku = s.get(Sku, sku_id)
        if sku is None:
            raise NoSuchSku(f"SKU {sku_id} not found")
        if run_id is None:
            raise NotReady("warming up")
        today = s.exec(select(func.max(SkuSnapshot.as_of)).where(SkuSnapshot.run_id == run_id)).one()
        snap = s.get(SkuSnapshot, (run_id, today, sku_id)) if today else None
        if snap is None:
            raise NotReady("warming up")
        alerts = s.exec(select(Alert).where(Alert.run_id == run_id, Alert.sku_id == sku_id, Alert.status == "open")
                        .order_by(Alert.impact_value.desc())).all()

    m = snap.metrics
    facts = {
        "name": sku.name, "zone": ZONE_LABEL[lang][snap.zone], "on_hand": int(m["on_hand"]),
        "velocity": round(m["velocity"], 1), "trend_pct": round(100 * m.get("velocity_trend", 0.0)),
        "doc": m["doc"], "lead_days": round(m["lead_mean"]), "stockout_prob_pct": int(m.get("risk_shortage", 0)),
        "par": round(m["par"]), "excess_units": int(m.get("excess_units", 0)),
    }
    if m.get("lead_mult", 1.0) > 1:         # outside signals are stretching this product's lead time
        facts["lead_delay_pct"] = round(100 * (m["lead_mult"] - 1))

    order = next((a for a in alerts if a.payload.get("kind") == "purchase" and a.payload.get("source") != "chat"), None)
    if order:
        p = order.payload
        facts.update(qty=int(p["qty"]), supplier=p.get("facts", {}).get("supplier", p["supplier_id"]), mode=p.get("mode", "multimodal"),
                     agreed_price=p.get("unit_price"), confidence=order.confidence, rescued=round(p.get("par_rescued", 0)))
        counter = p.get("counterfactual") or {}
        if counter.get("on_hand") is not None and counter.get("lead_days") is not None:
            facts.update(cf_on_hand=int(counter["on_hand"]), cf_lead=counter["lead_days"])
            return "explain_order_both", facts
        if counter.get("on_hand") is not None:
            facts["cf_on_hand"] = int(counter["on_hand"])
            return "explain_order_stock", facts
        return "explain_order", facts
    if alerts:                              # a transfer, markdown, bundle or check: quote the stored recommendation
        wording = {"action": alerts[0].action}
        if lang == "HI":
            try:
                wording = templates.render("alert", alerts[0].type, "HI", **alerts[0].payload.get("facts", {}))
            except (KeyError, IndexError):
                pass
        facts.update(action=wording["action"], confidence=alerts[0].confidence, alert_facts=alerts[0].payload.get("facts", {}))
        return "explain_action", facts
    return ("explain_excess" if facts["excess_units"] > 0 else "explain_ok"), facts


async def explain(sku_id: str, lang: str, llm) -> dict:
    """{text, facts, source}. The sentence is a template; in English the model may reword it under the usual guards."""
    lang = text_lang(lang)
    kind, facts = facts_for(sku_id, lang)
    sentence = templates.render("chat", kind, lang, **facts)["text"]
    source = "template"
    if lang == "EN":
        names = [v for v in (facts["name"], facts.get("supplier"), facts["zone"]) if v and v in sentence]
        sentence, source = await llm.text(prompts.REPHRASE_EXPLANATION, sentence, facts=facts, fallback=sentence, task="explain",
                                          max_tokens=260, must_contain=names, source=sentence,
                                          max_chars=round(len(sentence) * 1.5) + 40)
    return {"text": sentence, "facts": facts, "source": source}
