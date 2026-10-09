"""Distributor Selector (RESOLVE): for every SKU that needs an order, who to buy from, how to ship, at what price.

Suppliers are scored on eight criteria (TOPSIS), with speed weighted more heavily the closer the stockout.
The winning supplier's quantity is recomputed for its own lead time, three shipping modes are costed
against the stockout they would leave, and a price is negotiated. In debates it speaks as `negotiator`.
"""

import asyncio
import math

import numpy as np
from sqlalchemy import delete

from sarthi.agents.base import Agent
from sarthi.agents.debate import days_text
from sarthi.agents.inventory_optimizer import Context, SkuInputs
from sarthi.agents.negotiation import negotiate
from sarthi.agents.proposal import Proposal
from sarthi.analytics import esg, montecarlo, par, policy, topsis
from sarthi.analytics.transfer import haversine_km
from sarthi.db import session
from sarthi.llm import prompts, templates
from sarthi.models import Location, OutboxEmail
from sarthi.seed.catalog import REFERENCE_SUPPLIER_PRICE, STORE_ID

MODE_PATHS = 1000
BASE_MODE_DAYS = esg.MODES["multimodal"][0]   # a supplier's quoted lead time assumes the default mode
MIN_DISTANCE_KM = 50.0
PREFERRED_BONUS = 3.0
HISTORY_MONTHS = 12


def supplier_row(ctx: Context, sku: dict, supplier: dict, qty: float, lead_modifier: dict) -> dict:
    """The eight scoring criteria for one supplier and one SKU, plus what is needed to rank and display it."""
    sid = supplier["id"]
    stats = ctx.delivery_stats.get(sid, {"n": 0, "on_time": 0, "ordered": 0, "received": 0})
    ratios = ctx.ratio_by_supplier.get(sid, [])
    lead = float(np.mean(ctx.lead_samples(sku, supplier))) * float(lead_modifier.get(sid, 1.0))
    prices = [p for (sku_id, _), p in ctx.unit_price.items() if sku_id == sku["id"]]
    unit_price = ctx.unit_price.get((sku["id"], sid))
    earns_incentive = qty >= supplier["incentive_min_qty"] > 0
    return {
        "supplier_id": sid, "name": supplier["name"], "unit_price": unit_price, "lead_days": round(lead, 1),
        "feasible": unit_price is not None and (not supplier["capacity_limit"] or supplier["capacity_limit"] >= qty),
        "price_ratio": unit_price / float(np.median(prices)) if unit_price and prices else None,
        "tat_mean": lead,
        "tat_cv": float(np.std(ratios) / np.mean(ratios)) if len(ratios) >= 2 else None,
        "on_time": topsis.on_time_posterior(int(stats["on_time"]), int(stats["n"])),
        "fill_rate": stats["received"] / stats["ordered"] if stats["ordered"] else None,
        "defect_rate": supplier["defect_rate"], "esg": supplier["esg_score"],
        "incentive": supplier["incentive_pct"] / 100 if earns_incentive else 0.0,
    }


def rank(ctx: Context, sku: dict, qty: float, urgency: float, mode: str, lead_modifier: dict, envelope: dict) -> list[dict]:
    """All suppliers that sell this SKU, best first, each with a 0-100 `score`. Banned ones are flagged, not dropped."""
    rows = [supplier_row(ctx, sku, s, qty, lead_modifier) for s in ctx.suppliers.values()
            if (sku["id"], s["id"]) in ctx.unit_price]
    banned, preferred = envelope.get("banned_suppliers", {}), envelope.get("preferred_suppliers", [])
    for row, score in zip(rows, topsis.score(rows, mode, urgency), strict=True):
        row["score"] = min(100.0, score + PREFERRED_BONUS) if row["supplier_id"] in preferred else score
        row["banned"] = row["supplier_id"] in banned
    return sorted(rows, key=lambda r: (-r["score"], r["supplier_id"]))


def global_rows(ctx: Context, mode: str) -> list[dict]:
    """One row per supplier for the Replenish page: an overall score that does not depend on any one SKU."""
    months = sorted({m for stats in ctx.delivery_stats.values() for m in stats["by_month"]})[-HISTORY_MONTHS:]
    out = []
    for supplier in ctx.suppliers.values():
        sid = supplier["id"]
        stats, ratios = ctx.delivery_stats[sid], ctx.ratio_by_supplier.get(sid, [1.0])
        tat = supplier["avg_tat_days"] * float(np.mean(ratios))
        spread = supplier["avg_tat_days"] * float(np.std(ratios))
        row = {
            "price_ratio": 1.0, "tat_mean": tat, "tat_cv": float(np.std(ratios) / np.mean(ratios)) if len(ratios) >= 2 else None,
            "on_time": topsis.on_time_posterior(int(stats["on_time"]), int(stats["n"])),
            "fill_rate": stats["received"] / stats["ordered"] if stats["ordered"] else None,
            "defect_rate": supplier["defect_rate"], "esg": supplier["esg_score"], "incentive": supplier["incentive_pct"] / 100,
        }
        relative = [p / sku["cogs"] for sku in ctx.skus if (p := ctx.unit_price.get((sku["id"], sid))) and sku["cogs"]]
        low, high = max(1, math.floor(tat - spread)), max(1, math.ceil(tat + spread))
        out.append({
            "id": sid, "name": supplier["name"], "tat": f"{low}–{high} days" if high > low else f"{low} day{'s' if low > 1 else ''}",
            "reliability": round(row["on_time"] * 100),
            "price": round(REFERENCE_SUPPLIER_PRICE * float(np.mean(relative)), 2) if relative else 0.0,
            "incentive": supplier["incentive_text"], "score": round(topsis.score([row], mode)[0]), "tier": supplier["tier"],
            "fulfillment": round(row["fill_rate"], 2) if row["fill_rate"] is not None else 0.0,
            "defectRate": supplier["defect_rate"], "capacityLimit": supplier["capacity_limit"], "avgTAT": round(tat, 1),
            "tatHistory": [round(supplier["avg_tat_days"] * float(np.mean(stats["by_month"][m])), 1) if m in stats["by_month"]
                           else round(tat, 1) for m in months],
        })
    return sorted(out, key=lambda r: -r["score"])


class DistributorSelector(Agent):
    key, phase = "distributorSelector", "resolve"

    def _order(self, ctx: Context, sku: dict, inp: SkuInputs, decision: dict, candidate: dict) -> dict | None:
        """Quantity, shipping options and stockout exposure if this supplier is used. None if it cannot supply."""
        seed = montecarlo.sku_seed(self.settings.seed, sku["id"])
        supplier = ctx.suppliers[candidate["supplier_id"]]
        mu = inp.mu * inp.demand_mult
        position = decision["on_hand"] + decision["inbound"]
        shelf_cap = float(np.mean(mu)) * sku["shelf_life_days"] * policy.SHELF_LIFE_SHARE
        qty = policy.order_quantity(mu, inp.k, candidate["lead_days"], self.settings.review_period_days,
                                    decision["service_level"], position, sku["moq"], sku["shelf_life_days"],
                                    supplier["capacity_limit"] or None, seed)
        if qty <= 0:
            return None
        unit_margin = sku["price"] - sku["cogs"]
        receipts = [(max(1, round(days * inp.lead_mult)), q) for days, q in inp.receipts]

        def exposure(mode_days: int) -> tuple[float, float]:
            """Stockout risk and lost margin until an order shipped this way would arrive."""
            eta = max(1, round(candidate["lead_days"] + mode_days - BASE_MODE_DAYS))
            mc = montecarlo.simulate(inp.on_hand, [*receipts, (eta, float(qty))], inp.mu, inp.k, np.array([float(eta)]),
                                     MODE_PATHS, seed, demand_mult=inp.demand_mult)
            return mc.stockout_prob, par.par_shortage(mc.expected_lost_units, unit_margin, self.settings.goodwill_factor)

        store = self._store
        km = max(MIN_DISTANCE_KM, haversine_km(supplier["lat"], supplier["lon"], store["lat"], store["lon"]))
        weight = esg.parse_unit_weight(sku["name"])
        modes = esg.options(qty, weight, km, esg.base_freight(qty, weight, km), risk=exposure)
        for option in modes:
            option["eta_days"] = max(1, round(candidate["lead_days"] + option["days"] - BASE_MODE_DAYS))
        return {"qty": int(qty), "modes": modes, "chosen": esg.recommend(modes, self.strategy.get("mode", "Balanced")),
                "shelf_cap": int(shelf_cap), "distance_km": round(km)}

    def _compute(self, state: dict) -> tuple[list[Proposal], dict]:
        ctx = Context(state["forecasts"], state["history"])
        decisions, envelope = state["decisions"], state.get("envelope", {})
        lead_modifier, demand_multiplier = state.get("lead_modifier", {}), state.get("demand_multiplier", {})
        mode = self.strategy.get("mode", "Balanced")
        with session() as s:
            store = s.get(Location, STORE_ID)
            self._store = {"lat": store.lat, "lon": store.lon} if store else {"lat": 0.0, "lon": 0.0}

        proposals: list[Proposal] = []
        per_sku: dict[str, list[dict]] = {}
        for sku in ctx.skus:
            d = decisions.get(sku["id"])
            inp = ctx.inputs(sku, ctx.as_of, lead_modifier, demand_multiplier)
            if d is None or inp is None:
                continue
            urgency = min(1.0, max(0.0, 1 - d["cover"] / max(d["lead_eff"], 0.1)))
            hint = d["qty"] or round(d["velocity"] * d["lead_mean"] * 1.3)
            ranked = rank(ctx, sku, hint, urgency, mode, lead_modifier, envelope)
            per_sku[sku["id"]] = [{"name": r["name"].split(" ")[0], "tat": max(1, round(r["lead_days"])),
                                   "reliability": round(r["on_time"] * 100), "score": round(r["score"])} for r in ranked]
            if d["qty"] <= 0:
                continue

            chosen = order = None
            for candidate in (r for r in ranked if not r["banned"]):      # best first; skip any that cannot supply
                order = self._order(ctx, sku, inp, d, candidate)
                if order:
                    chosen = candidate
                    break
            if chosen is None:
                continue

            usual = ctx.primary.get(sku["id"], {})
            option = order["modes"][order["chosen"]]
            others = [r for r in ranked if r["supplier_id"] != chosen["supplier_id"] and not r["banned"] and r["unit_price"]]
            supplier = ctx.suppliers[chosen["supplier_id"]]
            earns = order["qty"] >= supplier["incentive_min_qty"] > 0
            deal = negotiate(chosen["unit_price"], supplier["incentive_pct"] / 100 if earns else 0.0,
                             supplier["max_discount_pct"], supplier["tier"], urgency,
                             alternative_price=others[0]["unit_price"] if others else None)
            switched = chosen["supplier_id"] != usual.get("id")
            rescued = max(0.0, d["par_shortage"] - option["par_shortage"])
            facts = {
                "sku": sku["name"], "supplier": chosen["name"], "from_supplier": usual.get("name", ""),
                "doc": d["doc"], "lead": d["lead_mean"], "lead_old": d["lead_mean"], "lead_new": chosen["lead_days"],
                "prob": d["risk_shortage"], "qty": order["qty"], "par_k": round(d["par_shortage"] / 1000, 1),
                "rescued_k": round(rescued / 1000, 1), "score": round(chosen["score"]),
                "mode": option["mode"], "eta_days": option["eta_days"], "eta": days_text(option["eta_days"]),
                "co2_kg": option["co2_kg"],
                "freight": option["freight_cost"], "list_price": deal.list_price, "agreed_price": deal.agreed_price,
                "unit_price": deal.unit_price, "offer": deal.unit_price,   # the email asks for the price we expect to pay
                "saving": round((deal.list_price - deal.unit_price) * order["qty"], 2), "rounds": len(deal.rounds),
            }
            proposals.append(Proposal(
                kind="purchase", alert_type="supplier_switch" if switched else "stockout_reorder", sku_id=sku["id"],
                author=self.key,
                payload={"supplier_id": chosen["supplier_id"], "qty": order["qty"], "unit_price": deal.unit_price,
                         "mode": option["mode"], "eta_days": option["eta_days"], "negotiation": deal.as_dict()},
                cost=round(order["qty"] * deal.unit_price + option["freight_cost"], 2), par_rescued=round(rescued, 2),
                co2_kg=option["co2_kg"],
                alternatives=[{k: r[k] for k in ("supplier_id", "name", "score", "feasible", "unit_price", "lead_days")}
                              for r in ranked if r["supplier_id"] != chosen["supplier_id"]],
                facts=facts,
                extra={"moq": sku["moq"], "shelf_cap": order["shelf_cap"], "modes": order["modes"],
                       "mode_risk": {o["mode"]: o["stockout_prob"] for o in order["modes"]},
                       "urgency": round(urgency, 2), "zone": d["zone"], "supplier_email": supplier["email"],
                       "par_now": round(d["par_shortage"], 2)},
            ))
        return proposals, {"global": global_rows(ctx, mode), "per_sku": per_sku}

    async def _draft_emails(self, proposals: list[Proposal], lang: str) -> None:
        """One opening email per purchase, saved as a draft. Replaces drafts left by earlier runs."""
        with session() as s:
            s.connection().execute(delete(OutboxEmail).where(OutboxEmail.status == "draft"))
            s.commit()
        for p in proposals:
            f = p.facts
            email_facts = {"supplier": f["supplier"], "sku": f["sku"], "qty": f["qty"], "offer": f["offer"],
                           "eta_days": f["eta_days"], "list_price": f["list_price"]}
            template = templates.render("email", "opening", lang, **email_facts)
            fallback = f"Subject: {template['subject']}\n\n{template['body']}"
            text = fallback
            if lang == "EN":       # Hindi and other languages always use the template
                text, _ = await self.llm.text(prompts.DRAFT_EMAIL, prompts.user_message(email_facts, lang),
                                              facts=email_facts, fallback=fallback, run_id=self.bb.run_id, task="email",
                                              must_contain=[f["sku"], "Subject:"], max_chars=900)
            subject, _, body = text.partition("\n")
            subject = subject.removeprefix("Subject:").strip() or template["subject"]
            with session() as s:
                s.add(OutboxEmail(to_addr=p.extra.get("supplier_email") or "unknown@localhost", subject=subject,
                                  body=body.strip() or template["body"], ref=p.id, status="draft"))
                s.commit()

    async def work(self, state: dict) -> dict:
        proposals, distributors = await asyncio.to_thread(self._compute, state)
        if not state.get("dry_run"):
            await self._draft_emails(proposals, state.get("lang", "EN"))

        self.bb.metric(self.key, key="distributors", phase=self.phase, **distributors)
        if distributors["global"]:
            self.bb.put("negotiator", self.phase, "supplier_primary", distributors["global"][0]["name"].split(" ")[0], tone="ghost")
        for p in proposals:
            f = p.facts
            price = f"agreed ₹{f['agreed_price']}" if f["agreed_price"] is not None else f"list ₹{f['list_price']}"
            self.bb.act("negotiator", self.phase, f"Ranked {len(p.alternatives) + 1} suppliers for {f['sku']}",
                        result=f"{f['supplier']} · score {f['score']} · {price}", sku_id=p.sku_id,
                        score=f["score"], qty=f["qty"], unit_price=f["unit_price"])
        return {"proposals": [p.as_dict() for p in proposals]}
