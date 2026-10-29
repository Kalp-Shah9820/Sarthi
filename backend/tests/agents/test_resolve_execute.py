"""RESOLVE and EXECUTE agents: supplier choice, negotiation, overstock remedies, alerts, and carrying out
approved actions.

The RESOLVE tests run on the session database. The EXECUTE tests come after them and use a database of
their own (`exec_db`), because executing changes stock and open orders."""

import asyncio
import email
import json
from datetime import timedelta

import numpy as np
import pytest
from sqlmodel import col, func, select

from sarthi import validate
from sarthi.agents.distributor_selector import rank
from sarthi.agents.execution_engine import ExecutionEngine, base36, data_today, execute_alert, execute_payload
from sarthi.agents.inventory_optimizer import Context
from sarthi.agents.negotiation import negotiate
from sarthi.agents.overstock_resolver import (
    LIQUIDATION_FLOOR,
    MARKDOWN_FLOOR,
    OverstockResolver,
    best_markdown,
    discount_option,
    hub_demand_shares,
    plan_hub_moves,
)
from sarthi.agents.proposal import ALERT_TYPES, Proposal
from sarthi.agents.stages import BALANCED, resolve, sense_and_decide, sense_decide_resolve
from sarthi.blackboard.store import Blackboard
from sarthi.config import get_settings
from sarthi.db import session
from sarthi.learning import bandit
from sarthi.llm import get_llm, templates
from sarthi.models import (
    Alert,
    BanditArm,
    Campaign,
    Inbound,
    Negotiation,
    OutboxEmail,
    PurchaseOrder,
    StockDaily,
    TransferOrder,
)
from sarthi.seed import catalog as cat
from tests.conftest import TEST_TODAY, must, private_database

RUN = 7101
CHAOS = [s["id"] for s in cat.SKUS if s["zone"] == "chaos"]
MOQ = {s["id"]: (12 if s["cogs"] < 50 else 6) for s in cat.SKUS}


def count(model, *where) -> int:
    with session() as s:
        return s.exec(select(func.count()).select_from(model).where(*where)).one()


@pytest.fixture(scope="module")
def resolved(analysis):
    """One real run through SENSE, DECIDE and RESOLVE."""
    return asyncio.run(sense_decide_resolve(RUN))


def by_kind(state, kind):
    return [p for p in state["proposals"] if p["kind"] == kind]


# ── Distributor Selector ─────────────────────────────────────────────────────

def test_every_short_sku_gets_a_costed_purchase(resolved):
    assert resolved["errors"] == []
    purchases = {p["sku_id"]: p for p in by_kind(resolved, "purchase")}
    assert sorted(purchases) == sorted(CHAOS)
    for sku_id, p in purchases.items():
        payload = p["payload"]
        assert payload["qty"] > 0 and payload["qty"] % MOQ[sku_id] == 0, sku_id
        assert payload["qty"] <= p["shelf_cap"] and payload["unit_price"] > 0 and payload["eta_days"] >= 1
        assert p["par_rescued"] > 0 and p["cost"] > payload["qty"] * payload["unit_price"] - 0.01      # order value + freight
        assert [m["mode"] for m in p["modes"]] == ["air", "sea", "multimodal"]
        assert set(p["mode_risk"]) == {"air", "sea", "multimodal"}
        assert payload["mode"] in p["mode_risk"] and p["author"] == "distributorSelector"
        assert len(p["alternatives"]) == 2 and all("supplier_id" in a for a in p["alternatives"])
        assert p["id"] == f"purchase:{p['alert_type']}:{sku_id}" and p["status"] == "proposed"


def test_urgent_orders_move_to_the_faster_supplier(resolved):
    for p in by_kind(resolved, "purchase"):
        facts = p["facts"]
        assert p["alert_type"] == "supplier_switch" and p["payload"]["supplier_id"] == "SUP-REL"
        assert (facts["from_supplier"], facts["supplier"]) == ("Metro Cash & Carry", "Reliance Metro WH")
        assert facts["lead_new"] < facts["lead_old"] / 2
        assert p["urgency"] > 0.8
        # the faster supplier needs a smaller order than the slow one would have
        assert p["payload"]["qty"] < resolved["decisions"][p["sku_id"]]["qty"]
        # waiting for the slowest mode leaves far more exposed than the chosen one
        chosen = next(m for m in p["modes"] if m["mode"] == p["payload"]["mode"])
        slowest = max(p["modes"], key=lambda m: m["eta_days"])
        assert chosen["par_shortage"] < slowest["par_shortage"]


def test_proposal_facts_fill_their_wording_template(resolved):
    for p in resolved["proposals"]:
        assert p["alert_type"] in ALERT_TYPES
        missing = templates.fields("alert", p["alert_type"]) - set(p["facts"])
        assert not missing, (p["id"], missing)
        assert all(text for text in templates.render("alert", p["alert_type"], "HI", **p["facts"]).values())


def test_negotiated_price_stays_within_both_sides_limits(resolved):
    for p in by_kind(resolved, "purchase"):
        deal = p["payload"]["negotiation"]
        assert 1 <= len(deal["rounds"]) <= 4
        if deal["agreed_price"] is not None:
            assert deal["floor"] <= deal["agreed_price"] <= deal["reserve"] <= deal["list_price"]
            assert p["payload"]["unit_price"] == deal["agreed_price"] and p["facts"]["saving"] >= 0
        else:
            assert p["payload"]["unit_price"] == deal["list_price"]


def test_negotiation_protocol():
    calm = negotiate(100.0, 0.0, 6.0, "Silver", urgency=0.0)
    urgent = negotiate(100.0, 0.0, 6.0, "Silver", urgency=1.0)
    assert (calm.floor, calm.reserve, calm.target) == (94.0, 100.0, 97.0)
    assert calm.floor <= must(calm.agreed_price) <= must(urgent.agreed_price) <= calm.reserve       # urgency never lowers the price
    assert must(urgent.agreed_round) <= must(calm.agreed_round)                                     # an urgent buyer settles sooner
    assert round(must(calm.agreed_price) / 0.05, 6) == round(must(calm.agreed_price) / 0.05)        # prices move in 5-paise steps
    prices = [must(negotiate(100.0, 0.0, 6.0, "Silver", u).agreed_price) for u in (0.0, 0.25, 0.5, 0.75, 1.0)]
    assert prices == sorted(prices)

    capped = negotiate(100.0, 0.02, 6.0, "Gold", 0.5, alternative_price=96.0)
    assert capped.reserve == 96.0 and must(capped.agreed_price) <= 96.0                       # the next-best supplier caps the price
    no_deal = negotiate(100.0, 0.0, 2.0, "Gold", 0.5, alternative_price=90.0)           # supplier cannot go below 98
    assert no_deal.agreed_price is None and no_deal.unit_price == 100.0 and len(no_deal.rounds) == 4
    firm, soft = negotiate(100.0, 0.0, 6.0, "Gold", 0.5), negotiate(100.0, 0.0, 6.0, "Bronze", 0.5)
    assert must(soft.agreed_round) <= must(firm.agreed_round)                                       # a Bronze supplier concedes earlier


def test_one_draft_email_per_purchase_and_reruns_replace_them(resolved):
    purchases = by_kind(resolved, "purchase")
    with session() as s:
        drafts = s.exec(select(OutboxEmail).where(OutboxEmail.status == "draft")).all()
    assert sorted(d.ref for d in drafts) == sorted(p["id"] for p in purchases)
    lays = next(d for d in drafts if d.ref.endswith("SKU003"))
    assert lays.to_addr == "orders@reliance-metro.example" and "Lays Classic 26g" in lays.subject
    assert "Reliance Metro WH" in lays.body and "per unit" in lays.body

    asyncio.run(sense_decide_resolve(RUN + 1))
    assert count(OutboxEmail, OutboxEmail.status == "draft") == len(purchases)


def test_banned_and_preferred_suppliers_are_respected(resolved):
    state = asyncio.run(sense_and_decide(RUN + 2, dry_run=True))
    state["envelope"]["banned_suppliers"] = {"SUP-REL": {"rule": "USER_PREFERENCE", "facts": {}}}
    state = asyncio.run(resolve(state))
    purchases = by_kind(state, "purchase")
    assert len(purchases) == 3
    assert all(p["payload"]["supplier_id"] != "SUP-REL" for p in purchases)
    assert all(a["supplier_id"] != p["payload"]["supplier_id"] for p in purchases for a in p["alternatives"])
    assert count(OutboxEmail, OutboxEmail.status == "draft") == 3          # the dry run drafted nothing new


def test_ranking_flags_capacity_and_rewards_preference(resolved):
    ctx = Context(resolved["forecasts"], resolved["history"])
    lays = next(k for k in ctx.skus if k["id"] == "SKU003")
    args = (ctx, lays, 1500, 0.9, "Balanced", resolved["lead_modifier"])
    plain = {r["supplier_id"]: r for r in rank(*args, {})}
    assert plain["SUP-HUL"]["feasible"] is False and plain["SUP-REL"]["feasible"] is True      # HUL caps at 1,200 units
    assert plain["SUP-REL"]["lead_days"] < plain["SUP-HUL"]["lead_days"] < plain["SUP-MCC"]["lead_days"]
    assert all(0 <= r["score"] <= 100 and r["banned"] is False for r in plain.values())
    favoured = {r["supplier_id"]: r for r in rank(*args, {"preferred_suppliers": ["SUP-MCC"], "banned_suppliers": {"SUP-HUL": {}}})}
    assert favoured["SUP-MCC"]["score"] == pytest.approx(plain["SUP-MCC"]["score"] + 3)
    assert favoured["SUP-HUL"]["banned"] is True
    calm = {r["supplier_id"]: r["score"] for r in rank(ctx, lays, 1500, 0.0, "Cash Flow", resolved["lead_modifier"], {})}
    assert plain["SUP-REL"]["score"] - plain["SUP-MCC"]["score"] > calm["SUP-REL"] - calm["SUP-MCC"]   # speed matters less


def test_supplier_table_for_the_replenish_page(resolved):
    table = Blackboard(RUN).metrics(key="distributors")[0]
    rows = table["global"]
    assert [r["id"] for r in rows] == ["SUP-REL", "SUP-HUL", "SUP-MCC"]
    assert [r["score"] for r in rows] == sorted((r["score"] for r in rows), reverse=True)
    fields = {"name", "tat", "reliability", "price", "incentive", "score", "tier", "fulfillment", "defectRate",
              "capacityLimit", "avgTAT", "tatHistory"}
    assert all(fields <= set(r) and len(r["tatHistory"]) == 12 for r in rows)
    assert [r["price"] for r in rows] == [18.5, 17.8, 16.9] and rows[0]["tat"].endswith("days")
    assert set(table["per_sku"]) == set(cat.ZONE_TARGET)
    assert table["per_sku"]["SKU003"][0] == {"name": "Reliance", "tat": 5, "reliability": rows[0]["reliability"],
                                              "score": table["per_sku"]["SKU003"][0]["score"]}


# ── Overstock Resolver ───────────────────────────────────────────────────────

def test_hub_transfers_move_real_surplus_only(resolved):
    moves = Blackboard(RUN).metrics(key="transfers")[0]["items"]
    assert any(m["skuId"] == "SKU002" and m["from"] == "WH-DEL" for m in moves)
    assert not any(m["skuId"] == "SKU004" for m in moves)                # cheap salt is not worth trucking
    hubs = [loc for loc in cat.LOCATIONS if loc["kind"] == "warehouse"]
    share = hub_demand_shares(hubs)
    for sku_id in {m["skuId"] for m in moves}:
        held = {h["id"]: h["stock"][sku_id] for h in hubs}
        total = sum(held.values())
        for hub in hubs:
            shipped = sum(m["units"] for m in moves if m["skuId"] == sku_id and m["from"] == hub["id"])
            received = sum(m["units"] for m in moves if m["skuId"] == sku_id and m["to"] == hub["id"])
            assert shipped <= max(0.0, held[hub["id"]] - share[hub["id"]] * total) + 1e-6
            assert received <= max(0.0, share[hub["id"]] * total - held[hub["id"]]) + 1e-6
    assert all(m["saving"] > 0 and m["units"] >= 10 and m["toCity"] for m in moves)
    assert Blackboard(RUN).get("ghost_transfer_ready") == "TRUE"


def test_hub_move_planner_edge_cases():
    hubs = [{"id": "A", "capacity": 100, "lat": 19.0, "lon": 72.8}, {"id": "B", "capacity": 100, "lat": 28.6, "lon": 77.2}]
    dear = {"cogs": 750.0, "holding_cost_pct": 0.07}
    cheap = {"cogs": 18.0, "holding_cost_pct": 0.03}
    moves = plan_hub_moves(dear, 5.0, {"A": 400, "B": 40}, hubs)
    assert len(moves) == 1 and (moves[0]["from"], moves[0]["to"], moves[0]["units"]) == ("A", "B", 180)
    assert plan_hub_moves(cheap, 5.0, {"A": 400, "B": 40}, hubs) == []          # saving below the cost of the move
    assert plan_hub_moves(dear, 5.0, {"A": 200, "B": 200}, hubs) == []          # already balanced
    assert plan_hub_moves(dear, 0.0, {"A": 400, "B": 40}, hubs) == []           # nothing sells: no basis to rebalance
    assert plan_hub_moves(dear, 5.0, {"A": 400}, hubs[:1]) == []


def test_markdowns_never_go_below_cost_and_liquidation_has_its_own_floor(resolved):
    settings, d = get_settings(), resolved["decisions"]
    with session() as s:
        from sarthi.models import Sku
        skus = {k.id: k.model_dump() for k in s.exec(select(Sku)).all()}
    surf = best_markdown(skus["SKU002"], d["SKU002"], settings)
    assert surf is not None
    assert surf["new_price"] >= skus["SKU002"]["cogs"] * MARKDOWN_FLOOR and surf["discount_pct"] in (10, 15, 20, 25)
    assert surf["extra_units"] > 0 and surf["net"] == pytest.approx(surf["par_rescued"] - surf["cost"], abs=0.02)  # each is rounded to paise
    assert best_markdown(skus["SKU004"], d["SKU004"], settings) is None          # a 4 % margin leaves no room
    assert discount_option(skus["SKU007"], d["SKU007"], settings, 0.20, MARKDOWN_FLOOR) is None
    flash = discount_option(skus["SKU007"], d["SKU007"], settings, 0.20, LIQUIDATION_FLOOR)
    assert flash is not None
    assert flash["new_price"] < skus["SKU007"]["cogs"] and flash["new_price"] >= skus["SKU007"]["cogs"] * LIQUIDATION_FLOOR
    deeper = discount_option(skus["SKU002"], d["SKU002"], settings, 0.25, LIQUIDATION_FLOOR)
    assert deeper is not None
    shallow = discount_option(skus["SKU002"], d["SKU002"], settings, 0.10, LIQUIDATION_FLOOR)
    assert shallow is not None
    assert deeper["extra_units"] > shallow["extra_units"]


def test_one_worthwhile_remedy_is_proposed_per_overstocked_sku(resolved):
    remedies = [p for p in resolved["proposals"] if p["author"] == "overstockResolver" and p["kind"] in ("transfer", "campaign")]
    assert len({p["sku_id"] for p in remedies}) == len(remedies)                 # at most one each
    assert {p["sku_id"] for p in remedies} <= {"SKU002", "SKU004", "SKU007"}
    for p in remedies:
        assert p["net"] > 0 and p["remedy"] in ("transfer", "markdown", "bundle")
        assert all(a["remedy"] != p["remedy"] for a in p["alternatives"])
    surf = next(p for p in remedies if p["sku_id"] == "SKU002")
    assert surf["kind"] == "transfer" and surf["payload"]["from"] == "WH-DEL" and surf["dest_free_capacity"] > 0
    assert any(a["remedy"] == "markdown" and a["net"] < 0 for a in surf["alternatives"])   # considered, and rejected


def test_exactly_three_campaign_cards(resolved):
    cards = Blackboard(RUN).metrics(key="campaigns")[0]["items"]
    assert [(c["type"], c["target"]) for c in cards] == [("markdown", "ghost"), ("bundle", "chaos"), ("flash", "money")]
    assert all({"skuIds", "discount", "estImpactValue", "net"} <= set(c) for c in cards)
    assert cards[0]["skuIds"] == ["SKU002"] and cards[2]["discount"] == 20
    bundle = cards[1]
    zones = [resolved["decisions"][s]["zone"] for s in bundle["skuIds"]]
    assert zones[0] != "chaos" and zones[1] == "chaos" and bundle["estImpactValue"] > 0   # promote what is in stock


def test_cannibalization_becomes_an_audit(resolved):
    audits = [p for p in resolved["proposals"] if p["alert_type"] == "cannibalization"]
    snacks = next(p for p in audits if p["sku_id"] == "SKU010")
    assert snacks["kind"] == "audit" and snacks["label"] == "Haldirams → Lays" and snacks["cost"] == 0
    assert snacks["payload"]["task"] == "cap_order" and snacks["payload"]["normal_velocity"] < resolved["decisions"]["SKU010"]["velocity"]
    assert snacks["facts"]["uplift_pct"] > 15 and snacks["par_rescued"] > 0


def test_expiring_stock_and_phantom_inventory_are_raised(resolved):
    decisions = {k: dict(v) for k, v in resolved["decisions"].items()}
    decisions["SKU001"]["age"] = 13                      # butter with 2 days of shelf life left
    intel = {"rules": [], "cannibals": [], "phantoms": [{"sku_id": "SKU005", "kind": "idle_stock", "days": 6, "est_units": 280}]}
    agent = OverstockResolver(Blackboard(RUN + 5), get_llm(), get_settings(), dict(BALANCED))
    proposals, _, _ = agent._compute({"decisions": decisions}, intel)
    expiry = next(p for p in proposals if p.alert_type == "expiry_risk")
    assert (expiry.sku_id, expiry.kind, expiry.payload["type"], expiry.payload["discount_pct"]) == ("SKU001", "campaign", "flash", 20)
    assert expiry.facts["days_left"] == 2 and expiry.facts["expiring"] == round(340 - decisions["SKU001"]["velocity"] * 2)
    phantom = next(p for p in proposals if p.alert_type == "phantom_inventory")
    assert (phantom.sku_id, phantom.kind, phantom.payload) == ("SKU005", "audit", {"task": "cycle_count", "aisle": "E", "anomaly": "idle_stock"})
    assert phantom.par_rescued == 280 * 8.0 and not (templates.fields("alert", "phantom_inventory") - set(phantom.facts))


def test_bundle_remedy_uses_a_product_in_demand(resolved):
    decisions = resolved["decisions"]
    with session() as s:
        from sarthi.models import Sku
        skus = {k.id: k.model_dump() for k in s.exec(select(Sku)).all()}
    agent = OverstockResolver(Blackboard(RUN + 6), get_llm(), get_settings(), dict(BALANCED))
    rule = {"antecedent_ids": ["SKU005"], "consequent_id": "SKU002", "confidence": 0.6}
    bundle = agent._bundle(skus["SKU002"], decisions["SKU002"], decisions, skus, [rule])
    assert bundle is not None
    assert bundle["partner_id"] == "SKU005" and bundle["conf_pct"] == 60 and bundle["discount_pct"] == 10
    assert bundle["extra_units"] > 0
    weak = {**rule, "confidence": 0.4}
    both_overstocked = {"antecedent_ids": ["SKU007"], "consequent_id": "SKU002", "confidence": 0.9}
    assert agent._bundle(skus["SKU002"], decisions["SKU002"], decisions, skus, [weak, both_overstocked]) is None


# ── Learning from approvals ──────────────────────────────────────────────────

def test_bandit_arms_learn_from_decisions(db):
    key = "test:arm"
    rng = np.random.default_rng(0)
    try:
        assert bandit.get_arm(key) == bandit.PRIOR and bandit.mean(key) == 0.5
        assert count(BanditArm, BanditArm.key == key) == 0                       # reading does not create the arm
        assert 0 < bandit.sample(key, rng) < 1
        fresh_bound = bandit.lower_bound(key)
        assert bandit.record(key, True) == pytest.approx(0.6 - 0.5)
        for _ in range(7):
            bandit.record(key, True)
        assert bandit.get_arm(key) == (10.0, 2.0) and bandit.lower_bound(key) > 0.6 > fresh_bound
        assert bandit.record(key, False) < 0
        draws = [bandit.sample(key, rng) for _ in range(200)]
        assert np.mean(draws) == pytest.approx(10 / 13, abs=0.05)
    finally:
        with session() as s:
            arm = s.get(BanditArm, key)
            if arm:
                s.delete(arm)
                s.commit()


def test_dismissed_remedies_lose_out_over_time(resolved):
    """After many dismissals of transfers for ghost stock, the resolver stops leading with a transfer."""
    try:
        for _ in range(60):
            bandit.record("ghost:transfer", False)
        state = asyncio.run(sense_decide_resolve(RUN + 7, dry_run=True))
        surf = [p for p in state["proposals"] if p["sku_id"] == "SKU002" and p["author"] == "overstockResolver"]
        # the transfer is still the only remedy with positive value, so it is still offered: learning reweights
        # choices between worthwhile options, it never promotes a loss-making one
        assert [p["remedy"] for p in surf] == ["transfer"]
    finally:
        with session() as s:
            arm = s.get(BanditArm, "ghost:transfer")
            if arm:
                s.delete(arm)
                s.commit()


# ── Alerts from rulings (no execution here; see test_execute.py) ─────────────

def rulings_for(state, routed="review"):
    return [{"proposal": p, "status": "approved", "confidence": 82, "routed": routed, "reasons": [],
             "counterfactual": {"lead_days": 8}} for p in state["proposals"]]


async def test_approved_proposals_become_alerts(resolved):
    before = (count(PurchaseOrder), count(TransferOrder))
    state = {**resolved, "rulings": rulings_for(resolved) + [{"proposal": resolved["proposals"][0], "status": "rejected"}]}
    result = await ExecutionEngine(Blackboard(RUN), get_llm(), get_settings(), dict(BALANCED)).run(state)
    assert len(result["alerts"]) == len(resolved["proposals"]) and result["executed"] == []
    with session() as s:
        alerts = s.exec(select(Alert).where(Alert.run_id == RUN)).all()
    assert (count(PurchaseOrder), count(TransferOrder)) == before             # raising an alert executes nothing
    for alert in alerts:
        assert alert.status == "open" and alert.routed == "review" and alert.txid is None and alert.confidence == 82
        assert alert.msg and alert.action and alert.impact and alert.payload["kind"] in ("purchase", "transfer", "campaign", "audit")
        assert alert.zone == resolved["decisions"][alert.sku_id]["zone"]
    lays = next(a for a in alerts if a.sku_id == "SKU003")
    assert (lays.type, lays.zone, lays.sku_label, lays.risk) == ("supplier_switch", "chaos", "Lays Classic 26g", 100)
    assert lays.action.startswith("Switch to Reliance Metro WH:") and lays.impact.endswith("K profit at risk")
    assert lays.payload["proposal_id"] == "purchase:supplier_switch:SKU003" and lays.payload["counterfactual"] == {"lead_days": 8}
    assert lays.impact_value == lays.payload["par_rescued"] > 0
    assert next(a for a in alerts if a.type == "cannibalization" and a.sku_id == "SKU010").sku_label == "Haldirams → Lays"


async def test_hindi_alerts_use_the_hindi_templates(resolved):
    state = {**resolved, "lang": "HI", "rulings": rulings_for(resolved)[:1]}
    result = await ExecutionEngine(Blackboard(RUN + 8), get_llm(), get_settings(), dict(BALANCED)).run(state)
    with session() as s:
        alert = s.get(Alert, result["alerts"][0])
        assert alert is not None
    assert "स्विच करें" in alert.action and "स्टॉकआउट" in alert.msg


async def test_a_dry_run_raises_no_alerts(resolved):
    before = count(Alert)
    state = {**resolved, "dry_run": True, "rulings": rulings_for(resolved, routed="auto")}
    result = await ExecutionEngine(Blackboard(RUN + 9), get_llm(), get_settings(), dict(BALANCED)).run(state)
    assert result == {"alerts": [], "executed": []} and count(Alert) == before


def test_proposal_helper():
    p = Proposal(kind="purchase", alert_type="stockout_reorder", sku_id="X", author="a", payload={"qty": 6}, cost=100.0,
                 par_rescued=250.0, extra={"moq": 6})
    data = p.as_dict()
    assert (p.id, p.net, data["net"], data["moq"], data["extra"]) == ("purchase:stockout_reorder:X", 150.0, 150.0, 6, {"moq": 6})


def test_validator_resolve_check(seeded_db):
    result = validate.check_resolve()
    assert result.status == validate.PASS, result.detail

# ── EXECUTE (own database) ───────────────────────────────────────────────────

EXEC_RUN = 7200
DEAL = {"list_price": 12.0, "target": 11.52, "reserve": 11.55, "floor": 11.52, "agreed_price": 11.55, "agreed_round": 4,
        "rounds": [{"round": 1, "buyer_offer": 11.52, "supplier_ask": 12.0}]}


@pytest.fixture(scope="module")
def exec_db(tmp_path_factory, template_db):
    """Executing changes stock and open orders, so these tests get a database of their own."""
    with private_database(tmp_path_factory, template_db, "execute") as root:
        yield root / "outbox"


def make_alert(kind: str, sku_id: str, payload: dict, routed: str = "review") -> int:
    with session() as s:
        alert = Alert(run_id=EXEC_RUN, sku_id=sku_id, sku_label=sku_id, zone="chaos", type="stockout_reorder", routed=routed,
                      payload={**payload, "kind": kind})
        s.add(alert)
        s.commit()
        s.refresh(alert)
        return must(alert.id)


def stock(sku_id: str, location: str) -> int:
    with session() as s:
        row = s.exec(select(StockDaily).where(StockDaily.sku_id == sku_id, StockDaily.location_id == location)
                     .order_by(col(StockDaily.day).desc())).first()
        return row.on_hand if row else 0


def test_helpers(exec_db):
    assert base36(0) == "0" and base36(35) == "Z" and base36(36) == "10"
    assert data_today() == TEST_TODAY


def test_purchase_creates_order_inbound_and_outbox_files_once(exec_db):
    with session() as s:        # the draft the Distributor Selector would have left for this proposal
        s.add(OutboxEmail(to_addr="orders@reliance-metro.example", subject="Order request: 984 units of Lays Classic 26g",
                          body="Dear Reliance Metro WH team,\n\nPlease supply 984 units.", ref="purchase:supplier_switch:SKU003"))
        s.commit()
    alert_id = make_alert("purchase", "SKU003", {
        "supplier_id": "SUP-REL", "qty": 984, "unit_price": 11.55, "mode": "air", "eta_days": 3,
        "negotiation": DEAL, "proposal_id": "purchase:supplier_switch:SKU003"})
    before = (count(PurchaseOrder), count(Inbound), count(Negotiation), count(OutboxEmail))

    result = execute_alert(alert_id)
    po_id = result["txid"]
    assert result["kind"] == "purchase" and result["repeat"] is False and po_id.startswith("PO-")
    assert (count(PurchaseOrder), count(Inbound), count(Negotiation), count(OutboxEmail)) == (before[0] + 1, before[1] + 1, before[2] + 1, before[3])
    with session() as s:
        po = s.get(PurchaseOrder, po_id)
        assert po is not None
        inbound = s.exec(select(Inbound).where(Inbound.sku_id == "SKU003", col(Inbound.received_on).is_(None))).one()
        mail = s.exec(select(OutboxEmail).where(OutboxEmail.ref == po_id)).one()
        alert = s.get(Alert, alert_id)
        assert alert is not None
        deal = s.exec(select(Negotiation).where(Negotiation.po_id == po_id)).one()
    assert (po.qty, po.unit_price, po.mode, po.status, po.source, po.supplier_id) == (984, 11.55, "air", "confirmed", "alert", "SUP-REL")
    assert (inbound.qty, inbound.ordered_on, inbound.expected_on) == (984, TEST_TODAY, TEST_TODAY + timedelta(days=3))
    assert mail.status == "queued" and (deal.agreed_price, deal.status) == (11.55, "agreed")
    assert (alert.status, alert.txid) == ("approved", po_id) and alert.decided_at is not None

    files = sorted(p.name for p in exec_db.iterdir())
    assert files == [f"{po_id}.eml", f"{po_id}.json"]
    message = email.message_from_bytes((exec_db / f"{po_id}.eml").read_bytes())
    assert message["To"] == "orders@reliance-metro.example" and message["From"] == "sarthi@localhost"
    assert po_id in message["Subject"] and "984 units" in message.get_payload()
    summary = json.loads((exec_db / f"{po_id}.json").read_text(encoding="utf-8"))
    assert (summary["total"], summary["sku"], summary["expected_on"]) == (round(984 * 11.55, 2), "Lays Classic 26g", "2026-10-04")

    again = execute_alert(alert_id)                       # a double-click on Approve
    assert again == {"txid": po_id, "kind": "purchase", "repeat": True}
    assert (count(PurchaseOrder), count(Inbound)) == (before[0] + 1, before[1] + 1)
    assert len(list(exec_db.iterdir())) == 2


def test_manual_order_without_a_draft_still_writes_an_email(exec_db):
    result = execute_payload("purchase", {"supplier_id": "SUP-HUL", "qty": 60, "unit_price": 86.6, "eta_days": 4},
                             sku_id="SKU006", run_id=EXEC_RUN, source="user")
    with session() as s:
        mail = s.exec(select(OutboxEmail).where(OutboxEmail.ref == result["txid"])).one()
        po = s.get(PurchaseOrder, result["txid"])
        assert po is not None
    assert mail.status == "queued" and mail.to_addr == "supply@hul-regional.example" and "Colgate Strong 200g" in mail.subject
    assert (po.source, po.mode) == ("user", "multimodal") and count(Negotiation, Negotiation.po_id == result["txid"]) == 0
    assert (exec_db / f"{result['txid']}.eml").exists()


def test_transfer_moves_stock_between_hubs(exec_db):
    before = (stock("SKU002", "WH-DEL"), stock("SKU002", "WH-CHN"))
    result = execute_alert(make_alert("transfer", "SKU002", {"sku_id": "SKU002", "from": "WH-DEL", "to": "WH-CHN", "units": 96}))
    assert result["txid"].startswith("TRF-") and len(result["txid"]) == 9
    assert (stock("SKU002", "WH-DEL"), stock("SKU002", "WH-CHN")) == (before[0] - 96, before[1] + 96)
    with session() as s:
        order = s.get(TransferOrder, result["txid"])
        assert order is not None
    assert (order.qty, order.from_location, order.to_location, order.status) == (96, "WH-DEL", "WH-CHN", "confirmed")


def test_transfer_never_moves_more_than_the_source_holds(exec_db):
    held = stock("SKU007", "WH-MUM")
    total = held + stock("SKU007", "WH-BLR")
    result = execute_payload("transfer", {"from": "WH-MUM", "to": "WH-BLR", "units": held + 500}, sku_id="SKU007", run_id=EXEC_RUN)
    with session() as s:
        assert must(s.get(TransferOrder, result["txid"])).qty == held
    assert stock("SKU007", "WH-MUM") == 0 and stock("SKU007", "WH-MUM") + stock("SKU007", "WH-BLR") == total


def test_campaign_goes_live_and_is_updated_not_duplicated(exec_db):
    payload = {"type": "markdown", "sku_ids": ["SKU002"], "discount_pct": 10, "target_zone": "ghost", "est_impact_value": 1612.3}
    first = execute_alert(make_alert("campaign", "SKU002", payload))
    second = execute_payload("campaign", {**payload, "discount_pct": 15}, sku_id="SKU002", run_id=EXEC_RUN)
    assert first["txid"] == second["txid"] and first["txid"].startswith("CMP-")
    with session() as s:
        campaigns = s.exec(select(Campaign).where(Campaign.type == "markdown")).all()
    assert len(campaigns) == 1
    assert (campaigns[0].status, campaigns[0].discount_pct, campaigns[0].sku_ids) == ("live", 15.0, ["SKU002"])


def test_audit_becomes_a_logged_task_without_touching_stock(exec_db):
    before = (count(StockDaily), count(Inbound))
    result = execute_alert(make_alert("audit", "SKU005", {"task": "cycle_count", "aisle": "E"}))
    assert result["txid"].startswith("TASK-") and (count(StockDaily), count(Inbound)) == before
    actions = [e for e in Blackboard(EXEC_RUN).events(kind="action") if e.agent == "executionEngine"]
    assert actions[-1].text == "Count stock in aisle E for SKU005" and actions[-1].value["result"] == result["txid"]
    assert len(actions) >= 5                              # every execution above was logged for the audit trail


def test_bad_requests_are_rejected(exec_db):
    with pytest.raises(LookupError):
        execute_alert(999_999)
    with pytest.raises(ValueError, match="cannot execute"):
        execute_payload("teleport", {}, sku_id="SKU001", run_id=EXEC_RUN)


async def test_auto_routed_rulings_execute_immediately(exec_db):
    proposal = {
        "id": "transfer:transfer:SKU004", "kind": "transfer", "alert_type": "transfer", "sku_id": "SKU004", "cost": 100.0,
        "par_rescued": 400.0, "payload": {"sku_id": "SKU004", "from": "WH-DEL", "to": "WH-CHN", "units": 50},
        "facts": {"sku": "Tata Salt 1kg", "units": 50, "from_site": "Delhi NCR Hub", "to_site": "Chennai Port", "saving_k": 0.3},
    }
    review = {**proposal, "id": "transfer:transfer:SKU002", "sku_id": "SKU002", "payload": {**proposal["payload"], "sku_id": "SKU002"}}
    state = {"decisions": {"SKU004": {"zone": "money", "risk": 74}, "SKU002": {"zone": "ghost", "risk": 69}},
             "rulings": [{"proposal": proposal, "status": "approved", "confidence": 93, "routed": "auto"},
                         {"proposal": review, "status": "approved", "confidence": 71, "routed": "review"}]}
    before = stock("SKU004", "WH-DEL")
    bb = Blackboard(EXEC_RUN + 1)
    result = await ExecutionEngine(bb, get_llm(), get_settings(), dict(BALANCED)).run(state)
    assert len(result["alerts"]) == 2 and len(result["executed"]) == 1 and result["executed"][0].startswith("TRF-")
    with session() as s:
        auto, held = (must(s.get(Alert, i)) for i in result["alerts"])
    assert (auto.status, auto.routed, auto.txid, auto.zone, auto.risk) == ("approved", "auto", result["executed"][0], "money", 74)
    assert (held.status, held.txid, held.confidence) == ("open", None, 71)
    assert stock("SKU004", "WH-DEL") == before - 50
    assert [e.value["stance"] for e in bb.events(kind="debate")] == ["execute"]
    summary = next(e for e in bb.events(kind="action") if must(e.text).startswith("Raised"))
    assert summary.value["result"] == "1 executed automatically"
