"""Overstock Resolver (RESOLVE): what to do about stock that is not earning its keep.

For each ghost or money-pit SKU it costs up to three remedies (move it between hubs, mark it down, bundle
it with a product people do want) and proposes one. It also raises the non-order findings: stock about to
expire, demand borrowed from a sold-out substitute, and stock records that contradict sales.

Assumption for transfers: per-hub sales are not uploaded yet, so each hub's demand is taken to be
proportional to its capacity. Hubs holding far more days of cover than the network average are the
sources; hubs holding less are the destinations. Replace `hub_demand_shares` when hub sales exist.
"""

import asyncio

import numpy as np
from sqlmodel import select

from sarthi.agents.base import Agent
from sarthi.agents.proposal import Proposal
from sarthi.analytics import par
from sarthi.analytics.transfer import haversine_km, plan_transfers, unit_cost
from sarthi.db import session
from sarthi.learning import bandit
from sarthi.models import Location, Sku, StockDaily

OVERSTOCK_ZONES = ("ghost", "money")
HORIZON_DAYS = 30
DISCOUNTS = (0.10, 0.15, 0.20, 0.25)
ELASTICITY = {"Staples": 1.2}
DEFAULT_ELASTICITY = 1.8
MARKDOWN_FLOOR = 1.02          # a markdown must still clear cost
LIQUIDATION_FLOOR = 0.75       # a flash sale may go below cost when holding or expiry would cost more
FLASH_DISCOUNT = 0.20
BUNDLE_DISCOUNT = 0.10
BUNDLE_MIN_CONFIDENCE = 0.5
BUNDLE_PARTNER_SHARE = 0.3     # share of the partner's co-purchases a bundle is assumed to convert
EXPIRY_WINDOW_DAYS = 14
MAX_MONTHS = 12


def discount_option(sku: dict, d: dict, cfg, discount: float, floor: float, extra_per_day: float | None = None) -> dict | None:
    """Economics of selling at `discount` for 30 days. `extra_per_day` overrides the price-elasticity uplift.

    par_rescued = carrying and expiry loss no longer incurred (plus any net margin gained);
    cost = margin given away on units that would have sold anyway, net of margin earned on the extra units.
    """
    new_price = sku["price"] * (1 - discount)
    if new_price < sku["cogs"] * floor:
        return None
    velocity, on_hand = d["velocity"], d["on_hand"]
    if extra_per_day is None:
        elasticity = ELASTICITY.get(sku["category"], DEFAULT_ELASTICITY)
        extra_per_day = velocity * ((1 - discount) ** (-elasticity) - 1)
    boosted = velocity + extra_per_day
    base_units = min(on_hand, velocity * HORIZON_DAYS)
    extra_units = max(0.0, min(on_hand, boosted * HORIZON_DAYS) - base_units)
    shelf_left = max(sku["shelf_life_days"] - d["age"], 0)
    before = par.par_overstock(on_hand, velocity, sku["cogs"], sku["holding_cost_pct"], cfg.overstock_cover_days, shelf_left)
    after = par.par_overstock(on_hand, boosted, sku["cogs"], sku["holding_cost_pct"], cfg.overstock_cover_days, shelf_left)
    avoided = max(0.0, before - after)
    margin_effect = (new_price - sku["cogs"]) * extra_units - discount * sku["price"] * base_units
    return {
        "discount_pct": round(discount * 100), "new_price": round(new_price, 2), "extra_units": round(extra_units),
        "par_rescued": round(avoided + max(0.0, margin_effect), 2), "cost": round(max(0.0, -margin_effect), 2),
        "net": round(avoided + margin_effect, 2),
    }


def best_markdown(sku: dict, d: dict, cfg, floor: float = MARKDOWN_FLOOR) -> dict | None:
    options = [o for o in (discount_option(sku, d, cfg, disc, floor) for disc in DISCOUNTS) if o]
    return max(options, key=lambda o: o["net"]) if options else None


def hub_demand_shares(hubs: list[dict]) -> dict[str, float]:
    """Assumed share of demand served by each hub: proportional to capacity."""
    total = sum(h["capacity"] for h in hubs) or 1
    return {h["id"]: h["capacity"] / total for h in hubs}


def plan_hub_moves(sku: dict, velocity: float, stock_by_hub: dict[str, float], hubs: list[dict]) -> list[dict]:
    """Moves that even out days of cover across hubs, where carrying cost saved exceeds the cost of moving."""
    held = {h["id"]: stock_by_hub.get(h["id"], 0.0) for h in hubs}
    total = sum(held.values())
    if total <= 0 or velocity <= 0 or len(hubs) < 2:
        return []
    shares = hub_demand_shares(hubs)
    monthly = sku["cogs"] * sku["holding_cost_pct"]                    # carrying cost of one unit for one month
    average = min(MAX_MONTHS, total / velocity / 30)
    months = {h: min(MAX_MONTHS, held[h] / max(velocity * shares[h], 1e-9) / 30) for h in held}
    surplus = {h: held[h] - shares[h] * total for h in held if held[h] > shares[h] * total}
    deficit = {h: shares[h] * total - held[h] for h in held if held[h] < shares[h] * total}
    where = {h["id"]: h for h in hubs}
    cost = {(i, j): unit_cost(haversine_km(where[i]["lat"], where[i]["lon"], where[j]["lat"], where[j]["lon"]))
            for i in surplus for j in deficit}
    # A unit leaving an over-covered hub and arriving at an under-covered one sells that much sooner.
    return plan_transfers(surplus, deficit, cost,
                          value={j: monthly * (average - months[j]) / 2 for j in deficit},
                          source_value={i: monthly * (months[i] - average) / 2 for i in surplus})


class OverstockResolver(Agent):
    key, phase = "overstockResolver", "resolve"

    def _load(self) -> tuple[dict, list[dict], dict, dict]:
        with session() as s:
            skus = {k.id: k.model_dump() for k in s.exec(select(Sku)).all()}
            locations = [x.model_dump() for x in s.exec(select(Location)).all()]
            rows = s.exec(select(StockDaily).order_by(StockDaily.day)).all()
        latest: dict[tuple[str, str], float] = {}
        for r in rows:                                   # ordered by day, so the last write per pair is the newest
            latest[(r.sku_id, r.location_id)] = r.on_hand
        hubs = [x for x in locations if x["kind"] == "warehouse"]
        stock_by_sku: dict[str, dict[str, float]] = {}
        load: dict[str, float] = {}
        for (sku_id, loc), qty in latest.items():
            stock_by_sku.setdefault(sku_id, {})[loc] = qty
            load[loc] = load.get(loc, 0.0) + qty
        return skus, hubs, stock_by_sku, load

    def _bundle(self, sku: dict, d: dict, decisions: dict, skus: dict, rules: list[dict]) -> dict | None:
        """A bundle with a product in demand, if the baskets show the two are bought together."""
        best = None
        for rule in rules:
            items = [*rule["antecedent_ids"], rule["consequent_id"]]
            if sku["id"] not in items or rule["confidence"] < BUNDLE_MIN_CONFIDENCE:
                continue
            partners = [i for i in items if i != sku["id"] and decisions.get(i, {}).get("zone") in ("sweet", "chaos")]
            if not partners:
                continue
            partner = max(partners, key=lambda i: decisions[i]["velocity"])
            extra = rule["confidence"] * decisions[partner]["velocity"] * BUNDLE_PARTNER_SHARE
            option = discount_option(sku, d, self.settings, BUNDLE_DISCOUNT, MARKDOWN_FLOOR, extra_per_day=extra)
            if option and (best is None or option["net"] > best["net"]):
                best = {**option, "partner_id": partner, "partner": skus[partner]["name"],
                        "conf_pct": round(rule["confidence"] * 100)}
        return best

    def _compute(self, state: dict, intel: dict) -> tuple[list[Proposal], list[dict], list[dict]]:
        decisions = state["decisions"]
        skus, hubs, stock_by_sku, load = self._load()
        hub_by_id = {h["id"]: h for h in hubs}
        rules = intel.get("rules", [])
        rng = np.random.default_rng(self.settings.seed + max(self.bb.run_id, 0))
        proposals: list[Proposal] = []
        transfers: list[dict] = []
        markdowns: list[tuple[dict, dict]] = []      # (sku, option) per zone, for the campaign cards
        flashes: list[tuple[dict, dict]] = []

        for sku_id, d in decisions.items():
            sku = skus.get(sku_id)
            if sku is None:
                continue
            zone = d["zone"]

            # Stock that will pass its shelf life: raised for any zone.
            days_left = max(sku["shelf_life_days"] - d["age"], 0)
            expiring = max(0.0, d["on_hand"] - d["velocity"] * days_left)
            if expiring > 0 and days_left <= EXPIRY_WINDOW_DAYS:
                option = discount_option(sku, d, self.settings, FLASH_DISCOUNT, LIQUIDATION_FLOOR)
                if option:
                    proposals.append(Proposal(
                        kind="campaign", alert_type="expiry_risk", sku_id=sku_id, author=self.key,
                        payload={"type": "flash", "sku_ids": [sku_id], "discount_pct": option["discount_pct"],
                                 "target_zone": zone, "est_impact_value": option["par_rescued"]},
                        cost=option["cost"], par_rescued=option["par_rescued"],
                        facts={"sku": sku["name"], "expiring": round(expiring), "days_left": days_left,
                               "discount": option["discount_pct"], "par_k": round(expiring * sku["cogs"] / 1000, 1)},
                        extra={"zone": zone},
                    ))

            if zone not in OVERSTOCK_ZONES:
                continue

            candidates: list[tuple[str, Proposal]] = []
            moves = plan_hub_moves(sku, d["velocity"], {h: q for h, q in stock_by_sku.get(sku_id, {}).items() if h in hub_by_id}, hubs)
            for move in moves:
                transfers.append({"skuId": sku_id, "from": move["from"], "to": move["to"],
                                  "toCity": hub_by_id[move["to"]]["city"], "units": move["units"], "saving": move["saving"]})
            if moves:
                move = moves[0]                       # the most valuable move for this SKU
                moving_cost = round(move["units"] * move["unit_cost"], 2)
                destination = hub_by_id[move["to"]]
                candidates.append(("transfer", Proposal(
                    kind="transfer", alert_type="transfer", sku_id=sku_id, author=self.key,
                    payload={"sku_id": sku_id, "from": move["from"], "to": move["to"], "units": move["units"]},
                    cost=moving_cost, par_rescued=round(move["saving"] + moving_cost, 2),
                    facts={"sku": sku["name"], "units": move["units"], "from_site": hub_by_id[move["from"]]["name"],
                           "to_site": destination["name"], "saving_k": round(move["saving"] / 1000, 1), "doc": d["doc"]},
                    extra={"zone": zone, "dest_free_capacity": max(0.0, destination["capacity"] - load.get(move["to"], 0.0))},
                )))

            markdown = best_markdown(sku, d, self.settings)
            if markdown:
                markdowns.append((sku, {**markdown, "zone": zone}))
                candidates.append(("markdown", Proposal(
                    kind="campaign", alert_type="markdown", sku_id=sku_id, author=self.key,
                    payload={"type": "markdown", "sku_ids": [sku_id], "discount_pct": markdown["discount_pct"],
                             "target_zone": zone, "est_impact_value": markdown["par_rescued"]},
                    cost=markdown["cost"], par_rescued=markdown["par_rescued"],
                    facts={"sku": sku["name"], "doc": d["doc"], "excess": d["excess_units"],
                           "discount": markdown["discount_pct"], "par_k": round(d["par_overstock"] / 1000, 1)},
                    extra={"zone": zone},
                )))
            liquidation = discount_option(sku, d, self.settings, FLASH_DISCOUNT, LIQUIDATION_FLOOR)
            if liquidation:
                flashes.append((sku, {**liquidation, "zone": zone}))

            bundle = self._bundle(sku, d, decisions, skus, rules)
            if bundle:
                candidates.append(("bundle", Proposal(
                    kind="campaign", alert_type="bundle", sku_id=sku_id, author=self.key,
                    payload={"type": "bundle", "sku_ids": [sku_id, bundle["partner_id"]], "discount_pct": bundle["discount_pct"],
                             "target_zone": zone, "est_impact_value": bundle["par_rescued"]},
                    cost=bundle["cost"], par_rescued=bundle["par_rescued"],
                    facts={"sku": sku["name"], "partner": bundle["partner"], "conf_pct": bundle["conf_pct"],
                           "extra": bundle["extra_units"], "discount": bundle["discount_pct"],
                           "par_k": round(d["par_overstock"] / 1000, 1)},
                    extra={"zone": zone},
                )))

            # Choose one remedy: value weighted by how often the manager has approved that kind of action.
            worthwhile = [(name, p) for name, p in candidates if p.net > 0]
            if worthwhile:
                scored = [(p.net * bandit.sample(f"{zone}:{name}", rng), name, p) for name, p in worthwhile]
                _, chosen_name, chosen = max(scored, key=lambda x: x[0])
                chosen.extra["remedy"] = chosen_name
                chosen.alternatives = [{"remedy": name, "net": round(p.net, 2), "par_rescued": p.par_rescued, "cost": p.cost}
                                       for name, p in candidates if p is not chosen]
                proposals.append(chosen)

        proposals += self._audits(decisions, skus, intel)
        return proposals, transfers, self._campaign_cards(decisions, skus, rules, markdowns, flashes)

    def _audits(self, decisions: dict, skus: dict, intel: dict) -> list[Proposal]:
        """Findings that need a person, not an order."""
        out = []
        for pair in intel.get("cannibals", []):
            rising, falling = decisions.get(pair["rising"]), decisions.get(pair["falling"])
            if not rising or not falling or pair["rising"] not in skus:
                continue
            sku = skus[pair["rising"]]
            # Units that would be over-ordered if the borrowed demand were treated as real, over one review period.
            borrowed = pair["uplift"] * rising["velocity"] * self.settings.review_period_days * falling["stockout_prob"]
            out.append(Proposal(
                kind="audit", alert_type="cannibalization", sku_id=pair["rising"], author=self.key,
                payload={"task": "cap_order", "rising": pair["rising"], "falling": pair["falling"],
                         "normal_velocity": round(rising["velocity"] / (1 + pair["uplift"] * falling["stockout_prob"]), 1)},
                cost=0.0, par_rescued=round(borrowed * sku["cogs"], 2),
                facts={"rising": pair["rName"], "falling": pair["fName"], "uplift_pct": round(pair["uplift"] * 100),
                       "event_days": pair["event_days"], "par_k": round(borrowed * sku["cogs"] / 1000, 1),
                       "correlation": pair["correlation"]},
                extra={"zone": rising["zone"], "label": f"{pair['rName'].split(' ')[0]} → {pair['fName'].split(' ')[0]}"},
            ))
        for anomaly in intel.get("phantoms", []):
            sku = skus.get(anomaly["sku_id"])
            d = decisions.get(anomaly["sku_id"])
            if not sku or not d:
                continue
            out.append(Proposal(
                kind="audit", alert_type="phantom_inventory", sku_id=sku["id"], author=self.key,
                payload={"task": "cycle_count", "aisle": sku["aisle_id"], "anomaly": anomaly["kind"]},
                cost=0.0, par_rescued=round(anomaly["est_units"] * sku["cogs"], 2),
                facts={"sku": sku["name"], "days": anomaly["days"], "aisle": sku["aisle_id"], "est_units": anomaly["est_units"]},
                extra={"zone": d["zone"]},
            ))
        return out

    def _campaign_cards(self, decisions: dict, skus: dict, rules: list[dict], markdowns: list, flashes: list) -> list[dict]:
        """Exactly one candidate per campaign type, for the three cards on the Replenish page."""
        def card(kind: str, target: str, pool: list) -> dict:
            in_zone = [x for x in pool if x[1]["zone"] == target] or pool
            if not in_zone:
                return {"type": kind, "target": target, "skuIds": [], "discount": 0, "estImpactValue": 0.0, "net": 0.0}
            sku, option = max(in_zone, key=lambda x: x[1]["net"])
            return {"type": kind, "target": target, "skuIds": [sku["id"]], "discount": option["discount_pct"],
                    "estImpactValue": option["par_rescued"], "net": option["net"]}

        # Bundle card: steer buyers of a product that is running out toward a companion that is in stock.
        bundle = {"type": "bundle", "target": "chaos", "skuIds": [], "discount": round(BUNDLE_DISCOUNT * 100),
                  "estImpactValue": 0.0, "net": 0.0}
        for rule in rules:
            items = [*rule["antecedent_ids"], rule["consequent_id"]]
            short = [i for i in items if decisions.get(i, {}).get("zone") == "chaos"]
            stocked = [i for i in items if i in skus and decisions.get(i, {}).get("zone") not in ("chaos", None)]
            if not short or not stocked or rule["confidence"] < BUNDLE_MIN_CONFIDENCE:
                continue
            promoted = skus[stocked[0]]
            units = rule["confidence"] * decisions[short[0]]["velocity"] * BUNDLE_PARTNER_SHARE * HORIZON_DAYS
            value = units * (promoted["price"] * (1 - BUNDLE_DISCOUNT) - promoted["cogs"])
            if value > bundle["estImpactValue"]:
                bundle.update(skuIds=[stocked[0], short[0]], estImpactValue=round(value, 2), net=round(value, 2))
        return [card("markdown", "ghost", markdowns), bundle, card("flash", "money", flashes)]

    async def work(self, state: dict) -> dict:
        found = self.bb.metrics(key="demand_intel")
        intel = found[-1] if found else {}
        proposals, transfers, campaigns = await asyncio.to_thread(self._compute, state, intel)

        self.bb.metric(self.key, key="campaigns", phase=self.phase, items=campaigns)
        self.bb.metric(self.key, key="transfers", phase=self.phase, items=transfers)
        self.bb.put(self.key, self.phase, "ghost_transfer_ready", "TRUE" if transfers else "FALSE",
                    tone="sweet" if transfers else "ghost")
        if transfers:
            units = sum(t["units"] for t in transfers)
            saving = sum(t["saving"] for t in transfers)
            self.bb.act(self.key, self.phase, f"Found {len(transfers)} profitable transfers",
                        result=f"{units} units · ₹{saving / 1000:.1f}K saved", moves=len(transfers), units=units)
        return {"proposals": [p.as_dict() for p in proposals]}
