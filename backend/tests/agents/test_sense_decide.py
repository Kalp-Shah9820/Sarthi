"""SENSE and DECIDE agents on the seeded database (model offline, weather off unless a test turns it on)."""

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace

import numpy as np
import pytest
from sqlmodel import func, select

from sarthi import validate
from sarthi.agents import compliance_guardian as cg
from sarthi.agents import demand_intel, macro_sentinel
from sarthi.agents.compliance_guardian import build_envelope, review
from sarthi.agents.inventory_optimizer import SkuInputs, decide
from sarthi.agents.macro_sentinel import MacroSentinel, fuse, load_feeds, nearest_region, weather_signal
from sarthi.agents.stages import BALANCED, sense_and_decide
from sarthi.blackboard.store import Blackboard
from sarthi.config import get_settings
from sarthi.db import session
from sarthi.models import Preference, PurchaseOrder, RiskSignal, SkuSnapshot, Supplier
from sarthi.seed import catalog as cat
from tests.conftest import TEST_TODAY

RUN = 7001
CHAOS = [s["id"] for s in cat.SKUS if s["zone"] == "chaos"]
SWEET = [s["id"] for s in cat.SKUS if s["zone"] == "sweet"]
OVERSTOCK = [s["id"] for s in cat.SKUS if s["zone"] in ("ghost", "money")]


def run(run_id, **kw):
    return asyncio.run(sense_and_decide(run_id, **kw))


def count(model, *where) -> int:
    with session() as s:
        return s.exec(select(func.count()).select_from(model).where(*where)).one()


@pytest.fixture(scope="module")
def baseline(analysis):
    """One real (non-dry) run; later tests compare against it."""
    return run(RUN)


# ── The golden test ──────────────────────────────────────────────────────────

def test_every_sku_lands_in_its_intended_zone(baseline):
    assert baseline["errors"] == []
    zones = {sku_id: d["zone"] for sku_id, d in baseline["decisions"].items()}
    assert zones == cat.ZONE_TARGET


def test_risk_and_value_match_the_zone(baseline):
    d = baseline["decisions"]
    for sku_id in CHAOS:
        assert d[sku_id]["stockout_prob"] > 0.6 and d[sku_id]["par"] > 0, sku_id
        assert d[sku_id]["cover"] < d[sku_id]["lead_eff"], sku_id
        assert d[sku_id]["risk"] == d[sku_id]["risk_shortage"], sku_id
    for sku_id in SWEET:
        assert d[sku_id]["stockout_prob"] < 0.15 and d[sku_id]["par"] < 500, sku_id
        assert d[sku_id]["inbound"] > 0 and d[sku_id]["cover"] > d[sku_id]["doc"], sku_id   # the delivery on its way counts
    for sku_id in OVERSTOCK:
        assert d[sku_id]["doc"] > 30 and d[sku_id]["par"] > 0, sku_id
        assert d[sku_id]["risk"] == d[sku_id]["risk_overstock"] > 50, sku_id
    assert d["SKU002"]["carry_roi"] > 0 > d["SKU004"]["carry_roi"]        # ghost still earns its keep; money pit does not


def test_only_short_skus_get_an_order_and_it_respects_the_pack_size(baseline):
    d = baseline["decisions"]
    moq = {s["id"]: (12 if s["cogs"] < 50 else 6) for s in cat.SKUS}
    for sku_id in CHAOS:
        assert d[sku_id]["qty"] > 0 and d[sku_id]["qty"] % moq[sku_id] == 0, sku_id
        assert d[sku_id]["on_hand"] + d[sku_id]["inbound"] < d[sku_id]["rop"], sku_id
        assert d[sku_id]["supplier_id"] == "SUP-MCC"
    for sku_id in SWEET + OVERSTOCK:
        assert d[sku_id]["qty"] == 0, sku_id
    assert all(0.80 <= x["service_level"] <= 0.995 and x["ss"] >= 0 and x["rop"] >= x["ss"] for x in d.values())
    assert d["SKU006"]["service_level"] > d["SKU004"]["service_level"]    # 35 % margin is protected harder than 4 %


def test_decision_record_carries_display_fields(baseline):
    lays = baseline["decisions"]["SKU003"]
    assert (lays["on_hand"], lays["age"], lays["supplier_name"]) == (150, 1, "Metro Cash & Carry")
    assert len(lays["mc_bins"]) == 20 and lays["p95_stock"] == 0 and lays["sigma"] > 0
    assert lays["model"] and 0 < lays["wape"] < 0.6
    assert lays["lead_mult"] == 1.25 and lays["last_reorder"] <= TEST_TODAY.isoformat()


# ── History for the drift time-lapse ─────────────────────────────────────────

def test_seven_days_of_zone_history_are_stored(baseline):
    with session() as s:
        everything = s.exec(select(SkuSnapshot).order_by(SkuSnapshot.run_id)).all()
    latest = {(r.as_of, r.sku_id): r for r in everything}          # the newest stored row per day and SKU
    rows = list(latest.values())
    days = sorted({r.as_of for r in rows})
    assert days == [TEST_TODAY - timedelta(days=6 - i) for i in range(7)]
    assert all(sum(1 for r in rows if r.as_of == d) == 10 for d in days)
    today = [r for r in everything if r.as_of == TEST_TODAY and r.run_id == RUN]
    assert len(today) == 10
    assert {r.sku_id: r.zone for r in today} == cat.ZONE_TARGET
    assert all(len(r.metrics["mc_bins"]) == 20 and "zone_raw" in r.metrics for r in today)
    earlier = [r for r in rows if r.as_of < TEST_TODAY]
    assert all("mc_bins" not in r.metrics and "zone_raw" in r.metrics for r in earlier)
    # the seeded picture is steady: no SKU changes zone during the week
    assert all(len({r.zone for r in rows if r.sku_id == sku_id}) == 1 for sku_id in cat.ZONE_TARGET)


def test_a_second_run_reuses_history_and_the_forecast(baseline):
    before = count(SkuSnapshot, SkuSnapshot.as_of < TEST_TODAY)
    again = run(RUN + 1)
    assert count(SkuSnapshot, SkuSnapshot.as_of < TEST_TODAY) == before         # earlier days are not recomputed
    assert count(SkuSnapshot, SkuSnapshot.run_id == RUN + 1) == 10              # only today's rows
    strip = lambda d: {k: v for k, v in d.items() if k != "mc_bins"}
    assert {k: strip(v) for k, v in again["decisions"].items()} == {k: strip(v) for k, v in baseline["decisions"].items()}
    forecaster = [e for e in Blackboard(RUN + 1).events(kind="action") if e.agent == "forecaster"]
    assert "cached" in forecaster[0].value["result"]


# ── What-if scenarios and strategy ───────────────────────────────────────────

def test_longer_lead_times_never_lower_risk_and_raise_value_at_risk(baseline):
    snapshots, signals = count(SkuSnapshot), count(RiskSignal)
    whatif = run(RUN + 10, scenario={"lead_mult": 1.3}, dry_run=True)
    base, scen = baseline["decisions"], whatif["decisions"]
    assert all(scen[k]["stockout_prob"] >= base[k]["stockout_prob"] for k in base)
    assert sum(d["par"] for d in scen.values()) > sum(d["par"] for d in base.values())
    assert all(scen[k]["lead_eff"] > base[k]["lead_eff"] for k in base)
    assert whatif["lead_modifier"]["SUP-MCC"] == pytest.approx(1.25 * 1.3)
    assert (count(SkuSnapshot), count(RiskSignal)) == (snapshots, signals)      # a what-if leaves no trace


def test_a_severe_disruption_pushes_healthy_skus_into_chaos(baseline):
    whatif = run(RUN + 11, scenario={"lead_mult": 3.0, "demand_mult": 1.5}, dry_run=True)
    moved = [k for k in SWEET if whatif["decisions"][k]["zone"] == "chaos"]
    assert moved, "tripling lead times with 50 % more demand should endanger at least one healthy SKU"
    assert all(whatif["decisions"][k]["zone"] == "chaos" for k in CHAOS)
    assert all(whatif["decisions"][k]["qty"] >= baseline["decisions"][k]["qty"] for k in CHAOS)


def test_strategy_changes_safety_stock(baseline):
    balanced = baseline["decisions"]
    cash = run(RUN + 12, strategy={"mode": "Cash Flow", "savingsPriority": 0.9, "safetyStockMultiplier": 0.8, "leadTimeBuffer": 1.2}, dry_run=True)
    growth = run(RUN + 13, strategy={"mode": "Growth", "savingsPriority": 0.2, "safetyStockMultiplier": 1.5, "leadTimeBuffer": 1.5}, dry_run=True)
    for k in balanced:
        assert cash["decisions"][k]["ss"] <= balanced[k]["ss"] <= growth["decisions"][k]["ss"], k
    assert sum(d["ss"] for d in cash["decisions"].values()) < sum(d["ss"] for d in balanced.values())
    assert cash["envelope"]["budget_remaining"] < baseline["envelope"]["budget_remaining"] < growth["envelope"]["budget_remaining"]
    assert growth["decisions"]["SKU003"]["lead_eff"] > balanced["SKU003"]["lead_eff"]   # larger lead-time buffer


# ── What the agents publish ──────────────────────────────────────────────────

def test_blackboard_shows_the_shared_picture(baseline):
    bb = Blackboard(RUN)
    context = {c["key"]: c for c in bb.context()}
    assert {"lead_time_risk", "demand_signal", "par_total", "esg_preference"} <= set(context)
    assert context["lead_time_risk"]["value"] == "MEDIUM"                       # port congestion: +25 % lead time
    assert context["esg_preference"]["value"] == "RAIL_BALANCED"
    assert context["par_total"]["value"].startswith("₹") and context["par_total"]["agent"] == "cfoAgent"
    agents = {e.agent for e in bb.events(kind="action")}
    assert {"forecaster", "monteCarloRiskEngine", "cfoAgent", "macroSentinel"} <= agents
    assert bb.events(kind="error") == []
    assert len(bb.metrics(key="decision")) == 10 and len(bb.metrics(key="forecast")) == 10
    assert bb.metrics(key="decision", sku_id="SKU003")[0]["zone"] == "chaos"


def test_demand_intelligence_publishes_structure(baseline):
    intel = Blackboard(RUN).metrics(key="demand_intel")[0]
    assert 1 <= len(intel["rules"]) <= 8 and len(intel["month_labels"]) == 12
    assert ("SKU010", "SKU003") in {(c["rising"], c["falling"]) for c in intel["cannibals"]}
    assert len(intel["bullwhip"]["raw"]) == 12 and intel["heat"]["C"] == 0.92
    assert intel["phantoms"] == []                                              # the seeded ledger is consistent
    forecast = Blackboard(RUN).metrics(key="forecast", sku_id="SKU005")[0]
    assert len(forecast["daily"]) == 30 and len(forecast["monthly_backcast"]) == 12


def test_demand_intelligence_failure_stops_the_stage_cleanly(analysis, monkeypatch):
    monkeypatch.setattr(demand_intel, "_cache", {})

    def boom(settings):
        raise RuntimeError("history unreadable")

    monkeypatch.setattr(demand_intel, "analyse", boom)
    state = run(RUN + 20, dry_run=True)
    assert state["errors"] == ["demandIntel: RuntimeError: history unreadable"]
    assert "decisions" not in state and "lead_modifier" in state                # the other SENSE agent still finished


# ── Macro Sentinel ───────────────────────────────────────────────────────────

SUPPLIERS = [{"id": "REL", "lat": 19.08, "lon": 72.88}, {"id": "MCC", "lat": 28.61, "lon": 77.21}]
STORE = {"lat": 19.08, "lon": 72.88}
SKUS = [{"id": "A", "category": "Snacks", "primary_supplier": "REL"}, {"id": "B", "category": "Dairy", "primary_supplier": "MCC"},
        {"id": "C", "category": "Edible Oil", "primary_supplier": "MCC"}]


def sig(**kw):
    base = {"type": "LOGISTICS", "severity": "HIGH", "region_key": "", "msg": "", "msg_key": None, "icon": "Anchor",
            "lead_modifier": 1.0, "demand_multiplier": 1.0, "categories": [], "source": "feed"}
    return {**base, **kw}


def test_fuse_applies_signals_by_distance_and_category():
    near_store = sig(region_key="jnptMumbai", lead_modifier=1.25)
    near_delhi = sig(region_key="delhiNcr", lead_modifier=1.2)
    far_away = sig(region_key="keralaCoast", lead_modifier=1.3)
    oil = sig(type="COMMODITY", demand_multiplier=0.97, categories=["Edible Oil"])
    lead, demand = fuse([near_store, near_delhi, far_away, oil], SUPPLIERS, STORE, SKUS)
    assert lead == {"REL": 1.25, "MCC": 1.5}             # the store's region slows everyone; Delhi only its own supplier
    assert demand == {"Dairy": 1.0, "Edible Oil": 0.97, "Snacks": 1.0}
    assert [s["skus_at_risk"] for s in (near_store, near_delhi, far_away, oil)] == [3, 2, 0, 1]


def test_fuse_caps_compounding_signals():
    storms = [sig(region_key="jnptMumbai", lead_modifier=1.5, demand_multiplier=1.4, categories=["Snacks"]) for _ in range(3)]
    lead, demand = fuse(storms, SUPPLIERS, STORE, SKUS)
    assert lead == {"REL": 2.0, "MCC": 2.0} and demand["Snacks"] == 1.6
    lead, demand = fuse([sig(demand_multiplier=0.5)], SUPPLIERS, STORE, SKUS)   # no category named: applies to all
    assert set(demand.values()) == {0.7} and lead == {"REL": 1.0, "MCC": 1.0}


def test_weather_thresholds_and_region_labels():
    calm = weather_signal("Mumbai", 19.08, 72.88, {"rain_mm": 20, "wind_kmh": 25, "temp_c": 33})
    storm = weather_signal("Mumbai", 19.08, 72.88, {"rain_mm": 40, "wind_kmh": 80, "temp_c": 30})
    rain = weather_signal("Pune", 18.52, 73.86, {"rain_mm": 70, "wind_kmh": 20, "temp_c": 28})
    heat = weather_signal("Delhi", 28.61, 77.21, {"rain_mm": 0, "wind_kmh": 10, "temp_c": 44})
    assert calm is None
    assert (storm["severity"], storm["lead_modifier"], storm["region_key"]) == ("HIGH", 1.5, "jnptMumbai")
    assert "80 km/h" in storm["msg"] and storm["msg_key"] is None
    assert (rain["severity"], rain["lead_modifier"], rain["region_key"]) == ("MEDIUM", 1.2, "puneHub")
    assert (heat["lead_modifier"], heat["categories"], heat["region_key"]) == (1.0, ["Snacks"], "delhiNcr")
    assert nearest_region(23.0, 80.0) == ""              # central India: no named region nearby


def test_feeds_are_read_from_the_seeded_files(seeded_db):
    feeds = load_feeds(get_settings().feeds_dir)
    assert {f["type"] for f in feeds} == {"LOGISTICS", "COMMODITY", "TRANSPORT"}
    assert all(f["source"] == "feed" and f["msg_key"] for f in feeds)


def sentinel(run_id, **settings_overrides):
    settings = get_settings().model_copy(update=settings_overrides)
    return MacroSentinel(Blackboard(run_id), SimpleNamespace(), settings, dict(BALANCED))


async def test_weather_outage_is_logged_and_the_run_continues(baseline, monkeypatch):
    async def down(lat, lon):
        raise TimeoutError("weather service did not answer")

    monkeypatch.setattr(macro_sentinel, "fetch_weather", down)
    state = await sentinel(RUN + 30, weather_enabled=True).run({"dry_run": True})
    assert state["lead_modifier"]["SUP-REL"] == 1.25      # feeds still applied
    errors = Blackboard(RUN + 30).events(kind="error")
    assert len(errors) == 1 and "TimeoutError" in errors[0].text       # one failure, not one per city


async def test_storm_forecast_becomes_a_stored_high_signal(baseline, monkeypatch):
    async def stormy(lat, lon):
        return {"rain_mm": 130, "wind_kmh": 70, "temp_c": 29}

    monkeypatch.setattr(macro_sentinel, "fetch_weather", stormy)
    try:
        state = await sentinel(RUN + 31, weather_enabled=True).run({})
        assert state["lead_modifier"] == {"SUP-REL": 2.0, "SUP-HUL": 2.0, "SUP-MCC": 2.0}   # capped
        assert state["demand_multiplier"]["Instant Food"] == 1.6 and state["demand_multiplier"]["Dairy"] == 1.0
        assert count(RiskSignal, RiskSignal.source == "weather") == 5                       # one per city
        bb = Blackboard(RUN + 31)
        assert bb.get("lead_time_risk") == "HIGH"
        assert sum("Storm forecast" in e.text for e in bb.events(kind="action")) == 5
    finally:
        await sentinel(RUN + 32).run({})                  # weather off again: rebuild the stored signals
    assert count(RiskSignal, RiskSignal.source == "weather") == 0 and count(RiskSignal, RiskSignal.source == "feed") == 3


async def test_uploaded_signals_are_used_and_survive_runs(baseline):
    with session() as s:
        s.add(RiskSignal(type="LOGISTICS", severity="MEDIUM", region_key="jnptMumbai", msg="Customs backlog",
                         icon="Anchor", lead_modifier=1.2, source="upload", active=True))
        s.commit()
    try:
        state = await sentinel(RUN + 33).run({})
        assert state["lead_modifier"]["SUP-REL"] == pytest.approx(1.5)        # 1.25 feed x 1.2 upload
        assert state["signal_count"] == 4
        with session() as s:
            uploaded = s.exec(select(RiskSignal).where(RiskSignal.source == "upload")).one()
        assert uploaded.skus_at_risk == 10
    finally:
        with session() as s:
            for row in s.exec(select(RiskSignal).where(RiskSignal.source == "upload")).all():
                s.delete(row)
            s.commit()


async def test_news_headlines_are_classified_by_the_model(baseline):
    class FakeLlm:
        async def json(self, schema, system, user, *, fallback, run_id=None, task="json", max_tokens=400):
            if "strike" in user:
                return schema(relevant=True, type="TRANSPORT", severity="HIGH", region_key="jnptMumbai", lead_modifier=9.0)
            return fallback()

    path = get_settings().feeds_dir / "news.json"
    path.write_text(json.dumps(["Truckers strike near Mumbai port", "Cricket team wins series"]), encoding="utf-8")
    try:
        agent = MacroSentinel(Blackboard(RUN + 34), FakeLlm(), get_settings(), dict(BALANCED))
        state = await agent.run({"dry_run": True})
        assert state["signal_count"] == 4                                      # 3 feeds + 1 relevant headline
        assert state["lead_modifier"]["SUP-REL"] == pytest.approx(1.25 * 1.5)  # the model's 9.0 is clamped to 1.5
    finally:
        path.unlink()


# ── Inventory Optimizer building blocks ──────────────────────────────────────

def inputs(**kw):
    sku = {"id": "T1", "name": "Test 1kg", "category": "Snacks", "cogs": 60.0, "price": 80.0, "shelf_life_days": 365,
           "holding_cost_pct": 0.03, "moq": 6, "lead_time_days": 5.0}
    base = {"sku": sku, "on_hand": 200.0, "receipts": [], "mu": np.full(30, 40.0), "k": 8.0, "velocity": 40.0,
            "velocity_trend": 0.0, "lead_samples": np.array([4.0, 5.0, 6.0]),
            "supplier": {"id": "S", "name": "Supplier", "capacity_limit": 5000}}
    return SkuInputs(**{**base, **kw})


def test_decide_is_deterministic_and_reacts_to_its_inputs():
    settings = get_settings()
    base = decide(inputs(), settings, BALANCED, 1000, run_seed=1)
    assert base == decide(inputs(), settings, BALANCED, 1000, run_seed=1)
    assert base["zone_raw"] == "chaos" and base["qty"] > 0 and base["doc"] == 5.0     # 5 days of cover vs. ~6 needed
    stocked = decide(inputs(on_hand=600.0), settings, BALANCED, 1000, run_seed=1)
    assert stocked["zone_raw"] == "sweet" and stocked["qty"] == 0
    covered = decide(inputs(receipts=[(1, 500.0)]), settings, BALANCED, 1000, run_seed=1)
    assert covered["zone_raw"] == "sweet" and covered["cover"] > covered["doc"] and covered["inbound"] == 500
    delayed = decide(inputs(receipts=[(1, 500.0)], lead_mult=3.0), settings, BALANCED, 1000, run_seed=1)
    assert delayed["stockout_prob"] > covered["stockout_prob"]                        # the incoming delivery is late too
    padded = decide(inputs(safety_pct=50.0), settings, BALANCED, 1000, run_seed=1)
    assert padded["ss"] > base["ss"] and padded["rop"] > base["rop"]
    glut = decide(inputs(on_hand=4000.0, velocity=10.0, mu=np.full(30, 10.0)), settings, BALANCED, 1000, run_seed=1)
    assert glut["zone_raw"] == "money" and glut["risk_overstock"] > 90 and glut["par_overstock"] > 0


def test_expiring_stock_shows_up_as_value_at_risk():
    settings = get_settings()
    butter = {"id": "B1", "name": "Butter 500g", "category": "Dairy", "cogs": 200.0, "price": 244.0, "shelf_life_days": 15,
              "holding_cost_pct": 0.02, "moq": 6, "lead_time_days": 2.0}
    fresh = decide(inputs(sku=butter, on_hand=340.0, age_days=3), settings, BALANCED, 500, run_seed=1)
    stale = decide(inputs(sku=butter, on_hand=340.0, age_days=13), settings, BALANCED, 500, run_seed=1)
    assert fresh["par_overstock"] == 0
    assert stale["par_overstock"] == pytest.approx((340 - 40 * 2) * 200)              # 2 days left: 260 units will expire


# ── Compliance Guardian ──────────────────────────────────────────────────────

ENVELOPE = {"budget_remaining": 100_000, "banned_suppliers": {}, "max_qty": {}, "max_air_share": 0.2, "auto_max_order_value": 50_000}


def purchase(**kw):
    payload = {"supplier_id": "SUP-REL", "qty": 600, "unit_price": 50.0, "mode": "multimodal"}
    payload.update({k: kw.pop(k) for k in list(kw) if k in payload})
    return {"kind": "purchase", "sku_id": "SKU003", "payload": payload, "moq": 12, **kw}


def rules(verdicts):
    return [(v.rule, v.severity) for v in verdicts]


def test_compliant_proposals_pass():
    assert review(purchase(), ENVELOPE) == []
    assert review({"kind": "campaign", "payload": {}}, ENVELOPE) == []
    assert review({"kind": "transfer", "payload": {"units": 100}, "dest_free_capacity": 500}, ENVELOPE) == []


def test_banned_supplier_is_blocked_with_an_alternative():
    envelope = {**ENVELOPE, "banned_suppliers": {"SUP-MCC": {"rule": "SUPPLIER_ESG_FLOOR", "facts": {"esg_score": 41.0, "esg_floor": 50.0}}}}
    alternatives = [{"supplier_id": "SUP-MCC"}, {"supplier_id": "SUP-REL"}, {"supplier_id": "SUP-HUL"}]
    verdict = review(purchase(supplier_id="SUP-MCC", alternatives=alternatives), envelope)[0]
    assert (verdict.rule, verdict.severity, verdict.voice, verdict.ok) == ("SUPPLIER_ESG_FLOOR", "block", "esgGuardian", False)
    assert verdict.suggestion == {"supplier_id": "SUP-REL"} and verdict.message_facts["esg_score"] == 41.0
    assert review(purchase(supplier_id="SUP-MCC"), envelope)[0].suggestion is None     # nobody else to suggest
    assert verdict.as_dict()["rule"] == "SUPPLIER_ESG_FLOOR"


def test_quantity_rules_suggest_a_clamped_quantity():
    assert review(purchase(qty=5), ENVELOPE)[0].suggestion == {"qty": 12}
    over_shelf = review(purchase(qty=600, shelf_cap=500), ENVELOPE)[0]
    assert (over_shelf.rule, over_shelf.suggestion) == ("SHELF_LIFE_CAP", {"qty": 492})          # rounded down to packs of 12
    capped = review(purchase(qty=600), {**ENVELOPE, "max_qty": {"SKU003": 300}})[0]
    assert (capped.rule, capped.voice, capped.suggestion) == ("USER_PREFERENCE", "cfoAgent", {"qty": 300})


def test_budget_rule_accounts_for_what_is_already_committed():
    assert rules(review(purchase(qty=2400, unit_price=50.0), ENVELOPE)) == [("BUDGET_CAP", "block"), ("AUTO_LIMIT", "warn")]
    tight = review(purchase(qty=600, unit_price=50.0), ENVELOPE, committed_value=80_000)[0]
    assert (tight.rule, tight.suggestion) == ("BUDGET_CAP", {"qty": 396})                        # ₹20,000 left / ₹50, in 12s
    assert tight.message_facts == {"order_value": 30000, "budget_available": 20000}
    assert review(purchase(), ENVELOPE, committed_value=100_000)[0].suggestion == {"qty": 0}


def test_air_freight_needs_a_reason():
    needed = purchase(mode="air", mode_risk={"air": 0.05, "multimodal": 0.60})
    unneeded = purchase(mode="air", mode_risk={"air": 0.05, "multimodal": 0.08})
    over_share = purchase(mode="air", mode_risk={"air": 0.05, "multimodal": 0.60}, air_orders=2, total_orders=4)
    few_orders = purchase(mode="air", mode_risk={"air": 0.05, "multimodal": 0.60}, air_orders=1, total_orders=1)
    assert review(few_orders, ENVELOPE) == []           # a share limit means nothing over two orders
    assert review(needed, ENVELOPE) == []
    verdict = review(unneeded, ENVELOPE)[0]
    assert (verdict.rule, verdict.severity, verdict.voice, verdict.suggestion) == ("AIR_UNNEEDED", "warn", "esgGuardian", {"mode": "multimodal"})
    assert verdict.ok is True and verdict.message_facts == {"air_risk_pct": 5, "multimodal_risk_pct": 8}
    # with every option costed, the slower mode must be no worse overall, not merely close on probability
    air = {"mode": "air", "freight_cost": 30.0, "co2_kg": 2.0, "par_shortage": 700.0, "stockout_prob": 0.98}
    slow = {"mode": "multimodal", "freight_cost": 17.0, "co2_kg": 0.2, "par_shortage": 1650.0, "stockout_prob": 1.0}
    envelope = {**ENVELOPE, "carbon_price": 2.0}
    assert review(purchase(mode="air", modes=[air, slow]), envelope) == []                 # air saves far more than it costs
    assert rules(review(purchase(mode="air", modes=[air, {**slow, "par_shortage": 705.0}]), envelope)) == [("AIR_UNNEEDED", "warn")]
    assert rules(review(over_share, ENVELOPE)) == [("AIR_SHARE", "warn")]


def test_large_orders_need_a_human_and_transfers_respect_capacity():
    big = review(purchase(qty=1200, unit_price=50.0), ENVELOPE)
    assert rules(big) == [("AUTO_LIMIT", "warn")] and big[0].suggestion is None
    full = review({"kind": "transfer", "payload": {"units": 400}, "dest_free_capacity": 150}, ENVELOPE)[0]
    assert (full.rule, full.suggestion) == ("CAPACITY", {"units": 150})


def test_envelope_follows_strategy_spend_and_preferences(seeded_db):
    settings = get_settings()
    balanced = build_envelope(settings, {"mode": "Balanced"})
    assert (balanced["budget_total"], balanced["budget_remaining"]) == (500_000, 500_000)
    assert (balanced["max_air_share"], balanced["carbon_price"], balanced["banned_suppliers"]) == (0.2, 2.0, {})
    assert build_envelope(settings, {"mode": "Cash Flow"})["budget_total"] == 300_000
    assert build_envelope(settings, {"mode": "Growth"})["max_air_share"] == 0.4

    with session() as s:
        s.add(PurchaseOrder(id="PO-TEST1", sku_id="SKU003", supplier_id="SUP-REL", qty=1000, unit_price=12.0, status="confirmed"))
        s.add(PurchaseOrder(id="PO-TEST2", sku_id="SKU003", supplier_id="SUP-REL", qty=9999, unit_price=12.0, status="draft"))
        s.add_all([
            Preference(scope="supplier", target="SUP-HUL", directive="avoid_supplier"),
            Preference(scope="supplier", target="SUP-REL", directive="prefer_supplier"),
            Preference(scope="sku", target="SKU003", directive="cap_qty", value=300),
            Preference(scope="sku", target="SKU005", directive="no_auto"),
            Preference(scope="global", target="", directive="other", note="Prefer weekday deliveries"),
            Preference(scope="sku", target="SKU001", directive="cap_qty", value=10, active=False),
        ])
        mcc = s.get(Supplier, "SUP-MCC")
        mcc.esg_score = 41.0
        s.add(mcc)
        s.commit()
    try:
        env = build_envelope(settings, {"mode": "Balanced"})
        assert (env["budget_spent"], env["budget_remaining"]) == (12_000, 488_000)    # drafts do not count
        assert env["banned_suppliers"]["SUP-MCC"]["rule"] == "SUPPLIER_ESG_FLOOR"
        assert env["banned_suppliers"]["SUP-HUL"]["rule"] == "USER_PREFERENCE"
        assert env["preferred_suppliers"] == ["SUP-REL"] and env["max_qty"] == {"SKU003": 300}
        assert env["no_auto"] == ["SKU005"] and env["notes"] == ["Prefer weekday deliveries"]
    finally:
        with session() as s:
            for model in (PurchaseOrder, Preference):
                for row in s.exec(select(model)).all():
                    s.delete(row)
            mcc = s.get(Supplier, "SUP-MCC")
            mcc.esg_score = 61.0
            s.add(mcc)
            s.commit()
    assert cg.ESG_LABEL["Cash Flow"] == "SEA_SAVER"


def test_validator_agents_check(seeded_db):
    result = validate.check_agents()
    assert result.status == validate.PASS, result.detail
