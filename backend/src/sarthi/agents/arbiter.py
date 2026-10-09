"""The Arbiter (key `rlhfArbiter`): proposals are challenged, revised and ruled on.

The debate is a protocol, decided by code:
  1. propose  - the authoring agent states its proposal;
  2. object   - three critics check it and speak only if they find something:
                `forecaster` (is the demand real and predictable?), `cfoAgent` / `esgGuardian`
                (does it break a rule?) and `riskAgent` (does it actually remove the risk?);
  3. revise   - the author applies the suggested fixes, at most twice;
  4. rule     - purchases compete for the budget, then every surviving proposal gets a confidence
                score and is either sent for review or (once that kind of action has earned it) executed.
The transcript is what the Agent Debate panel shows.
"""

import asyncio
import copy
import math
from dataclasses import replace

import numpy as np

from sarthi.agents import debate
from sarthi.agents.base import Agent
from sarthi.agents.compliance_guardian import review
from sarthi.agents.inventory_optimizer import Context, decide
from sarthi.analytics import budget
from sarthi.analytics.conformal import conformal_quantile
from sarthi.learning import confidence as conf
from sarthi.llm import prompts
from sarthi.models import local_today

VOICE = {"distributorSelector": "negotiator", "overstockResolver": "overstockResolver"}
W_CASH = {"Growth": 0.02, "Balanced": 0.05, "Cash Flow": 0.12}     # how much a rupee of cash out counts against a rupee rescued
MAX_ROUNDS = 2
WAPE_OBJECTION = 0.35
MODE_GAIN = 0.05               # a faster mode must cut stockout risk by at least this to be worth suggesting
CHEAP_WARNING_SHARE = 0.05     # a warning's fix is applied when it costs less than this share of the value rescued
REPHRASED_LINES = 8
LEAD_GRID = (1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3)
CONFIDENCE_DRAWS = 20


def _round_down(qty: float, moq: int) -> int:
    return int(math.floor(qty / max(1, moq)) * max(1, moq))


class Arbiter(Agent):
    key, phase = "rlhfArbiter", "resolve"

    # ── critics ──────────────────────────────────────────────────────────────

    def _forecaster(self, p: dict, forecasts: dict, cannibals: dict, phantoms: set, decisions: dict) -> list[dict]:
        out = []
        if p["kind"] != "purchase":
            return out
        qty, moq, sku_id = p["payload"]["qty"], p.get("moq", 1), p["sku_id"]
        pair = cannibals.get(sku_id)
        if pair and "BORROWED_DEMAND" not in p["_applied"]:
            falling_risk = decisions.get(pair["falling"], {}).get("stockout_prob", 0.0)
            suggested = _round_down(qty / (1 + pair["uplift"] * falling_risk), moq)
            if 0 < suggested < qty:
                out.append({"rule": "BORROWED_DEMAND", "voice": "forecaster", "severity": "block",
                            "facts": {"sku": p["facts"]["sku"], "falling": pair["fName"], "qty": qty, "suggested_qty": suggested},
                            "suggestion": {"qty": suggested}})
        f = forecasts.get(sku_id)
        if f is not None and f.wape > WAPE_OBJECTION and "FORECAST_UNCERTAIN" not in p["_applied"]:
            shortfall = min(0.5, conformal_quantile(np.abs(f.rel_residuals_14d), alpha=0.2))
            suggested = _round_down(qty * (1 - shortfall), moq)
            if 0 < suggested < qty:
                out.append({"rule": "FORECAST_UNCERTAIN", "voice": "forecaster", "severity": "warn", "cost": 0.0,
                            "facts": {"sku": p["facts"]["sku"], "wape_pct": round(f.wape * 100), "qty": qty, "suggested_qty": suggested},
                            "suggestion": {"qty": suggested}})
        if sku_id in phantoms:
            out.append({"rule": "PHANTOM_STOCK", "voice": "forecaster", "severity": "warn",
                        "facts": {"sku": p["facts"]["sku"]}, "suggestion": None})
        return out

    def _risk_agent(self, p: dict) -> list[dict]:
        if p["kind"] != "purchase" or not p.get("mode_risk"):
            return []
        mode = p["payload"]["mode"]
        risk = p["mode_risk"][mode]
        if risk <= self.settings.stockout_alert_prob:
            return []
        fastest = min(p["modes"], key=lambda m: (m["stockout_prob"], m["freight_cost"]))
        if fastest["mode"] != mode and risk - fastest["stockout_prob"] >= MODE_GAIN and "FASTER_MODE" not in p["_applied"]:
            current = next(m for m in p["modes"] if m["mode"] == mode)
            return [{"rule": "FASTER_MODE", "voice": "riskAgent", "severity": "warn",
                     "cost": max(0.0, fastest["freight_cost"] - current["freight_cost"]),
                     "facts": {"risk_pct": round(risk * 100), "faster_mode": fastest["mode"],
                               "faster_risk_pct": round(fastest["stockout_prob"] * 100)},
                     "suggestion": {"mode": fastest["mode"]}}]
        return [{"rule": "RESIDUAL_RISK", "voice": "riskAgent", "severity": "warn",
                 "facts": {"risk_pct": round(risk * 100), "eta_days": p["payload"]["eta_days"],
                           "eta": debate.days_text(p["payload"]["eta_days"])}, "suggestion": None}]

    def _compliance(self, p: dict, envelope: dict, tally: dict) -> list[dict]:
        checked = {**p, "air_orders": tally["air"], "total_orders": tally["purchases"]}
        out = []
        for v in review(checked, envelope, committed_value=0.0):
            if v.rule in p["_applied"]:
                continue
            cost = 0.0
            if v.rule in ("AIR_SHARE", "AIR_UNNEEDED") and p.get("modes"):   # giving up air costs the slower mode's extra exposure
                by_mode = {m["mode"]: m for m in p["modes"]}
                cost = max(0.0, by_mode["multimodal"]["par_shortage"] - by_mode["air"]["par_shortage"])
            out.append({"rule": v.rule, "voice": v.voice, "severity": v.severity, "cost": cost,
                        "facts": dict(v.message_facts), "suggestion": v.suggestion})
        return out

    # ── revision ─────────────────────────────────────────────────────────────

    def _revise(self, p: dict, objection: dict) -> tuple[str, dict] | None:
        """Apply one suggestion. Returns (debate line key, facts) or None when it cannot be applied here."""
        suggestion, payload, facts = objection["suggestion"], p["payload"], p["facts"]
        if "qty" in suggestion and p["kind"] == "purchase":
            new_qty, old_qty = int(suggestion["qty"]), payload["qty"]
            if new_qty <= 0:
                return None
            scale = new_qty / old_qty
            freight = facts.get("freight", 0.0) * scale
            payload["qty"] = facts["qty"] = new_qty
            facts["freight"], p["co2_kg"] = round(freight, 2), round(p["co2_kg"] * scale, 2)
            p["cost"] = round(new_qty * payload["unit_price"] + freight, 2)
            facts["saving"] = round((facts.get("list_price", payload["unit_price"]) - payload["unit_price"]) * new_qty, 2)
            return "revise:qty", {"qty": new_qty, "order_value": round(new_qty * payload["unit_price"])}
        if "mode" in suggestion and p.get("modes"):
            option = next((m for m in p["modes"] if m["mode"] == suggestion["mode"]), None)
            if option is None:
                return None
            scale = payload["qty"] / max(1, p.get("modes_qty", payload["qty"]))
            payload["mode"], payload["eta_days"] = option["mode"], option["eta_days"]
            facts.update(mode=option["mode"], eta_days=option["eta_days"], eta=debate.days_text(option["eta_days"]),
                         freight=round(option["freight_cost"] * scale, 2))
            p["co2_kg"] = round(option["co2_kg"] * scale, 2)
            p["cost"] = round(payload["qty"] * payload["unit_price"] + facts["freight"], 2)
            p["par_rescued"] = round(max(0.0, p.get("par_now", p["par_rescued"]) - option["par_shortage"]), 2)
            return "revise:mode", {"mode": option["mode"], "eta_days": option["eta_days"], "eta": facts["eta"]}
        if "units" in suggestion and p["kind"] == "transfer":
            new_units, old_units = int(suggestion["units"]), payload["units"]
            if new_units <= 0:
                return None
            scale = new_units / old_units
            payload["units"] = facts["units"] = new_units
            p["cost"], p["par_rescued"] = round(p["cost"] * scale, 2), round(p["par_rescued"] * scale, 2)
            facts["saving_k"] = round((p["par_rescued"] - p["cost"]) / 1000, 1)
            return "revise:units", {"units": new_units}
        return None       # e.g. a different supplier: that needs the Distributor Selector, not a patch here

    # ── confidence and counterfactual ────────────────────────────────────────

    def _confidence(self, p: dict, state: dict, ctx: Context | None, phantoms: set) -> int:
        if p["kind"] == "audit":
            return conf.audit_confidence(p["alert_type"], p["facts"].get("correlation"))
        decision = state["decisions"].get(p["sku_id"], {})
        f = state["forecasts"].get(p["sku_id"]) if state.get("forecasts") else None
        if f is None or ctx is None or not decision:
            return conf.CONFIDENCE_RANGE[0]
        sku = next((k for k in ctx.skus if k["id"] == p["sku_id"]), None)
        supplier_id = p["payload"].get("supplier_id") or decision.get("supplier_id")
        quality = conf.data_quality(
            sales_days=len(ctx.days), supplier_deliveries=ctx.delivery_stats.get(supplier_id, {}).get("n", 0),
            stock_age_days=(local_today() - ctx.as_of).days, phantom_flag=p["sku_id"] in phantoms)
        seed = self.settings.seed + sum(map(ord, p["sku_id"]))
        if p["kind"] == "purchase" and sku is not None:
            inp = ctx.inputs(sku, ctx.as_of, state.get("lead_modifier", {}), state.get("demand_multiplier", {}))
            stability = conf.purchase_stability(inp, decision, decide, self.settings, self.strategy, f.rel_residuals_14d,
                                                CONFIDENCE_DRAWS, seed) if inp else 0.0
        else:
            stability = conf.overstock_stability(decision, self.settings.overstock_cover_days, f.rel_residuals_14d,
                                                 CONFIDENCE_DRAWS, seed)
        return conf.combine(stability, f.wape, quality)

    def _counterfactual(self, p: dict, state: dict, ctx: Context | None) -> dict | None:
        """The smallest change that would make this order unnecessary."""
        decision = state["decisions"].get(p["sku_id"])
        if p["kind"] != "purchase" or ctx is None or not decision:
            return None
        sku = next((k for k in ctx.skus if k["id"] == p["sku_id"]), None)
        inp = ctx.inputs(sku, ctx.as_of, state.get("lead_modifier", {}), state.get("demand_multiplier", {})) if sku else None
        if inp is None:
            return None
        position = decision["on_hand"] + decision["inbound"]
        out = {"on_hand": max(decision["on_hand"], decision["rop"] - decision["inbound"])}   # the reorder point does not depend on stock
        for factor in LEAD_GRID:
            redo = decide(replace(inp, lead_mult=inp.lead_mult * factor), self.settings, self.strategy, 500, self.settings.seed)
            if position >= redo["rop"]:
                out["lead_days"] = redo["lead_mean"]
                break
        return out

    # ── the debate ───────────────────────────────────────────────────────────

    def _deliberate(self, state: dict, intel: dict) -> tuple[list[dict], list[dict]]:
        decisions, envelope = state.get("decisions", {}), state.get("envelope", {})
        forecasts = state.get("forecasts") or {}
        ctx = Context(forecasts, state["history"]) if forecasts and state.get("history") else None
        cannibals = {c["rising"]: c for c in intel.get("cannibals", [])}
        phantoms = {a["sku_id"] for a in intel.get("phantoms", [])}
        mode = self.strategy.get("mode", "Balanced")
        lines: list[dict] = []
        tally = {"air": 0, "purchases": 0}

        def say(agent: str, key: str, facts: dict, stance: str, sku_id: str, **extra) -> None:
            lines.append({"agent": agent, "key": key, "facts": facts, "stance": stance, "sku_id": sku_id, "extra": extra})

        working = []
        for original in sorted(state.get("proposals", []), key=lambda x: -x["par_rescued"]):
            p = copy.deepcopy(original)
            p["_applied"], p["modes_qty"] = set(), p["payload"].get("qty", 0)
            say(VOICE.get(p["author"], p["author"]), f"propose:{p['alert_type']}", p["facts"], "propose", p["sku_id"])
            said: set[str] = set()
            rejected_for, warnings = None, []
            for round_no in range(MAX_ROUNDS + 1):
                objections = (self._forecaster(p, forecasts, cannibals, phantoms, decisions)
                              + self._compliance(p, envelope, tally) + self._risk_agent(p))
                for o in objections:
                    if o["rule"] not in said:
                        said.add(o["rule"])
                        say(o["voice"], f"object:{o['rule']}", o["facts"], "object", p["sku_id"], rule=o["rule"], severity=o["severity"])
                blocks = [o for o in objections if o["severity"] == "block"]
                fixable = blocks + [o for o in objections if o["severity"] == "warn" and o["suggestion"]
                                    and o.get("cost", 0.0) <= CHEAP_WARNING_SHARE * max(p["par_rescued"], 0.0)]
                warnings = [o["rule"] for o in objections if o["severity"] == "warn" and o not in fixable]
                if not fixable:
                    break
                if round_no == MAX_ROUNDS:
                    rejected_for = blocks[0]["rule"] if blocks else None
                    warnings = [o["rule"] for o in objections if o["severity"] == "warn"]
                    break
                for o in fixable:
                    change = self._revise(p, o) if o["suggestion"] else None
                    p["_applied"].add(o["rule"])
                    if change is None:
                        if o["severity"] == "block":
                            rejected_for = o["rule"]
                        continue
                    p["status"] = "revised"
                    say(VOICE.get(p["author"], p["author"]), change[0], change[1], "revise", p["sku_id"])
                if rejected_for:
                    break
            if p["kind"] == "purchase" and not rejected_for:
                tally["purchases"] += 1
                tally["air"] += p["payload"]["mode"] == "air"
            working.append({"p": p, "rejected_for": rejected_for, "warnings": warnings})

        # Purchases compete for the budget on what they rescue, net of cash tied up and carbon.
        buying = [w for w in working if w["p"]["kind"] == "purchase" and not w["rejected_for"]]
        if buying:
            utility = np.array([w["p"]["par_rescued"] - W_CASH.get(mode, 0.05) * w["p"]["cost"]
                                - envelope.get("carbon_price", 0.0) * w["p"]["co2_kg"] for w in buying])
            costs = np.array([w["p"]["cost"] for w in buying])
            funded = budget.fund_orders(utility, costs, envelope.get("budget_remaining", float("inf")))
            for w, chosen in zip(buying, funded, strict=True):
                if not chosen:
                    w["rejected_for"] = "BUDGET_CAP"
        for w in working:
            p = w["p"]
            if not w["rejected_for"] and p["kind"] in ("transfer", "campaign") and p["par_rescued"] - p["cost"] <= 0:
                w["rejected_for"] = "NO_NET_BENEFIT"

        rulings = []
        lang = state.get("lang", "EN")
        for w in working:
            p = w["p"]
            p.pop("_applied", None)
            p.pop("modes_qty", None)
            zone = decisions.get(p["sku_id"], {}).get("zone", p.get("zone", "sweet"))
            if w["rejected_for"]:
                p["status"] = "rejected"
                say(self.key, "rule:rejected", {"reason": debate.rejection_reason(w["rejected_for"], lang)}, "rule", p["sku_id"],
                    rule=w["rejected_for"], status="rejected")
                rulings.append({"proposal": p, "status": "rejected", "confidence": 0, "routed": "review",
                                "reasons": [w["rejected_for"]], "warnings": w["warnings"], "counterfactual": None})
                continue
            p["status"] = "approved"
            score = self._confidence(p, state, ctx, phantoms)
            no_auto = p["sku_id"] in envelope.get("no_auto", []) or "global" in envelope.get("no_auto", [])
            routed, held_back = conf.route(score, p["cost"], w["warnings"], no_auto, conf.arm_key(zone, p["alert_type"]), self.settings)
            say(self.key, f"rule:approved_{routed}", {"confidence": score}, "rule", p["sku_id"], status="approved", routed=routed)
            rulings.append({"proposal": p, "status": "approved", "confidence": score, "routed": routed, "reasons": held_back,
                            "warnings": w["warnings"], "counterfactual": self._counterfactual(p, state, ctx)})
        return rulings, lines

    async def work(self, state: dict) -> dict:
        found = self.bb.metrics(key="demand_intel")
        rulings, lines = await asyncio.to_thread(self._deliberate, state, found[-1] if found else {})
        lang = state.get("lang", "EN")
        # Rewording is opt-in, English only, and never for a what-if (which must answer at once).
        reword = self.settings.llm_reword_debate and lang == "EN" and not state.get("dry_run")
        for i, line in enumerate(lines):
            text = debate.render(line["key"], lang, line["facts"])
            if reword and i < REPHRASED_LINES:
                names = [v for v in line["facts"].values() if isinstance(v, str) and v and v in text]
                text, _ = await self.llm.text(prompts.REPHRASE, text, facts=line["facts"], fallback=text, run_id=self.bb.run_id,
                                              task="debate", max_tokens=120, must_contain=names, source=text,
                                              max_chars=round(len(text) * 1.6) + 20)
            self.bb.say(line["agent"], text, stance=line["stance"], phase=self.phase, sku_id=line["sku_id"],
                        key=line["key"], **line["extra"], **{k: v for k, v in line["facts"].items() if k not in line["extra"]})
        approved = [r for r in rulings if r["status"] == "approved"]
        self.bb.act(self.key, self.phase, f"Ruled on {len(rulings)} proposals",
                    result=f"{len(approved)} approved, {len(rulings) - len(approved)} rejected", approved=len(approved))
        return {"rulings": rulings}
