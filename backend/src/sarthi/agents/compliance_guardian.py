"""Compliance Guardian (DECIDE): the rules any action must respect, and a review of proposals against them.

Rules are data and are evaluated in code. Every verdict names the rule that fired and carries the numbers
behind it, which is what makes a decision auditable. In debates it speaks as `cfoAgent` (money rules)
or `esgGuardian` (supplier and carbon rules).
"""

import math
from dataclasses import asdict, dataclass

from sqlmodel import select

from sarthi.agents.base import Agent
from sarthi.analytics.esg import CARBON_PRICE
from sarthi.db import session
from sarthi.models import Preference, PurchaseOrder, Supplier, local_today

BUDGET_FACTOR = {"Balanced": 1.0, "Cash Flow": 0.6, "Growth": 1.3}
MAX_AIR_SHARE = {"Balanced": 0.2, "Cash Flow": 0.1, "Growth": 0.4}
ESG_LABEL = {"Balanced": "RAIL_BALANCED", "Growth": "AIR_PRIORITY", "Cash Flow": "SEA_SAVER"}
ESG_FLOOR = 50.0
AIR_RISK_MARGIN = 0.05     # air is not justified when a slower mode is within 5 points of stockout risk


@dataclass
class Verdict:
    ok: bool
    severity: str               # "block" | "warn"
    rule: str                   # SUPPLIER_ESG_FLOOR, USER_PREFERENCE, MOQ, SHELF_LIFE_CAP, BUDGET_CAP, AIR_SHARE, AUTO_LIMIT, CAPACITY
    voice: str                  # "cfoAgent" | "esgGuardian"
    message_facts: dict         # the numbers behind the verdict
    suggestion: dict | None     # e.g. {"qty": 480}, {"mode": "multimodal"}, {"supplier_id": "SUP-HUL"}

    def as_dict(self) -> dict:
        return asdict(self)


def _block(rule: str, voice: str, facts: dict, suggestion: dict | None) -> Verdict:
    return Verdict(False, "block", rule, voice, facts, suggestion)


def _warn(rule: str, voice: str, facts: dict, suggestion: dict | None) -> Verdict:
    return Verdict(True, "warn", rule, voice, facts, suggestion)


def _round_down(qty: float, moq: int) -> int:
    return int(math.floor(qty / moq) * moq) if moq > 0 else int(qty)


def review(proposal: dict, envelope: dict, committed_value: float = 0.0) -> list[Verdict]:
    """Check one proposal. Returns the rules it trips (empty list = compliant).

    `proposal` needs `kind` and `payload`; a purchase payload has supplier_id, qty, unit_price, mode.
    Optional context: `sku_id`, `moq`, `shelf_cap` (units), `alternatives` (ranked suppliers with
    supplier_id), `mode_risk` ({mode: stockout probability}), `air_orders` / `total_orders` so far,
    and for transfers `dest_free_capacity`.
    """
    kind, payload = proposal.get("kind"), proposal.get("payload", {})
    verdicts: list[Verdict] = []

    if kind == "transfer":
        free = proposal.get("dest_free_capacity")
        units = payload.get("units", 0)
        if free is not None and units > free:
            verdicts.append(_block("CAPACITY", "cfoAgent", {"units": units, "free_capacity": int(free)},
                                   {"units": max(0, int(free))}))
        return verdicts
    if kind != "purchase":
        return verdicts

    sku_id, supplier_id = proposal.get("sku_id"), payload.get("supplier_id")
    qty, unit_price = int(payload.get("qty", 0)), float(payload.get("unit_price", 0.0))
    moq = max(1, int(proposal.get("moq", 1)))

    banned = envelope.get("banned_suppliers", {})
    if supplier_id in banned:
        allowed = [a["supplier_id"] for a in proposal.get("alternatives", [])
                   if a["supplier_id"] not in banned and a.get("feasible", True) and a["supplier_id"] != supplier_id]
        reason = banned[supplier_id]
        verdicts.append(_block(reason["rule"], "esgGuardian", {"supplier_id": supplier_id, **reason.get("facts", {})},
                               {"supplier_id": allowed[0]} if allowed else None))

    if qty < moq:
        verdicts.append(_block("MOQ", "cfoAgent", {"qty": qty, "moq": moq}, {"qty": moq}))
    shelf_cap = proposal.get("shelf_cap")
    if shelf_cap is not None and qty > shelf_cap:
        verdicts.append(_block("SHELF_LIFE_CAP", "cfoAgent", {"qty": qty, "shelf_cap": int(shelf_cap)},
                               {"qty": _round_down(shelf_cap, moq)}))
    user_cap = envelope.get("max_qty", {}).get(sku_id)
    if user_cap is not None and qty > user_cap:
        verdicts.append(_block("USER_PREFERENCE", "cfoAgent", {"qty": qty, "max_qty": int(user_cap)},
                               {"qty": _round_down(user_cap, moq)}))

    value = qty * unit_price
    available = envelope.get("budget_remaining", float("inf")) - committed_value
    if value > available:
        affordable = _round_down(max(available, 0) / unit_price, moq) if unit_price > 0 else 0
        verdicts.append(_block("BUDGET_CAP", "cfoAgent", {"order_value": round(value), "budget_available": round(max(available, 0))},
                               {"qty": affordable}))

    if payload.get("mode") == "air":
        risk = proposal.get("mode_risk", {})
        total = proposal.get("total_orders", 0)
        share_after = (proposal.get("air_orders", 0) + 1) / (total + 1)
        over_share = total > 0 and share_after > envelope.get("max_air_share", 1.0)
        unneeded = "air" in risk and "multimodal" in risk and risk["multimodal"] - risk["air"] <= AIR_RISK_MARGIN
        if over_share or unneeded:
            facts = {"air_share_pct": round(share_after * 100), "max_air_share_pct": round(envelope.get("max_air_share", 1.0) * 100)}
            if unneeded:
                facts.update(air_risk_pct=round(risk["air"] * 100), multimodal_risk_pct=round(risk["multimodal"] * 100))
            verdicts.append(_warn("AIR_SHARE", "esgGuardian", facts, {"mode": "multimodal"}))

    limit = envelope.get("auto_max_order_value")
    if limit is not None and value > limit:
        verdicts.append(_warn("AUTO_LIMIT", "cfoAgent", {"order_value": round(value), "auto_limit": round(limit)}, None))
    return verdicts


def build_envelope(settings, strategy: dict) -> dict:
    """This run's rule set, from settings, the strategy mode, supplier ESG scores and stored user preferences."""
    mode = strategy.get("mode", "Balanced")
    month_start = local_today().replace(day=1)
    with session() as s:
        suppliers = s.exec(select(Supplier)).all()
        preferences = s.exec(select(Preference).where(Preference.active)).all()
        orders = s.exec(select(PurchaseOrder).where(PurchaseOrder.status == "confirmed")).all()
    spent = sum(o.qty * o.unit_price for o in orders if o.created_at.date() >= month_start)
    total = settings.monthly_budget * BUDGET_FACTOR.get(mode, 1.0)

    banned = {x.id: {"rule": "SUPPLIER_ESG_FLOOR", "facts": {"esg_score": x.esg_score, "esg_floor": ESG_FLOOR}}
              for x in suppliers if x.esg_score < ESG_FLOOR}
    envelope = {
        "mode": mode, "budget_total": round(total), "budget_spent": round(spent),
        "budget_remaining": round(max(total - spent, 0)),
        "banned_suppliers": banned, "preferred_suppliers": [], "max_qty": {}, "min_cover_days": {}, "no_auto": [],
        "max_air_share": MAX_AIR_SHARE.get(mode, 0.2), "carbon_price": CARBON_PRICE.get(mode, 2.0),
        "auto_max_order_value": settings.auto_max_order_value, "notes": [],
    }
    for p in preferences:
        if p.directive == "avoid_supplier":
            banned[p.target] = {"rule": "USER_PREFERENCE", "facts": {}}
        elif p.directive == "prefer_supplier":
            envelope["preferred_suppliers"].append(p.target)
        elif p.directive == "cap_qty" and p.value is not None:
            envelope["max_qty"][p.target] = p.value
        elif p.directive == "min_cover_days" and p.value is not None:
            envelope["min_cover_days"][p.target] = p.value
        elif p.directive == "no_auto":
            envelope["no_auto"].append(p.target or p.scope)
        elif p.directive == "other" and p.note:
            envelope["notes"].append(p.note)
    return envelope


class ComplianceGuardian(Agent):
    key, phase = "complianceGuardian", "decide"

    async def work(self, state: dict) -> dict:
        envelope = build_envelope(self.settings, self.strategy)
        mode = envelope["mode"]
        self.bb.put("esgGuardian", self.phase, "esg_preference", ESG_LABEL.get(mode, "RAIL_BALANCED"), tone="sweet")
        self.bb.act("cfoAgent", self.phase, "Budget envelope set",
                    result=f"₹{envelope['budget_remaining'] / 1000:.0f}K of ₹{envelope['budget_total'] / 1000:.0f}K available",
                    budget_remaining=envelope["budget_remaining"], budget_total=envelope["budget_total"])
        if envelope["banned_suppliers"]:
            self.bb.act("esgGuardian", self.phase, f"{len(envelope['banned_suppliers'])} supplier(s) excluded",
                        result=", ".join(sorted(envelope["banned_suppliers"])))
        self.bb.metric(self.key, key="envelope", phase=self.phase, **envelope)
        return {"envelope": envelope}
