"""The whole pipeline through the graph: orchestration, debate, confidence, routing and learning.

Uses its own database: a run with earned autonomy executes a transfer, which changes stock.
"""

import asyncio
import os
from datetime import timedelta
from types import SimpleNamespace

import numpy as np
import pytest
from sqlmodel import func, select

from sarthi import validate
from sarthi.agents import debate, demand_intel
from sarthi.agents.arbiter import Arbiter
from sarthi.blackboard.store import Blackboard
from sarthi.config import get_settings
from sarthi.db import init_db, reset_engine, session
from sarthi.learning import bandit
from sarthi.learning import confidence as conf
from sarthi.learning.memory import PreferenceOut, learn_from_decision, rule_based, to_preference
from sarthi.llm import get_llm, reset_llm, templates
from sarthi.llm.grounding import grounded
from sarthi.models import (
    Alert,
    BanditArm,
    Event,
    Preference,
    PurchaseOrder,
    Run,
    SkuSnapshot,
    StrategyPolicy,
    TransferOrder,
)
from sarthi.orchestrator.graph import AGENT_CLASSES, build_agents, build_graph
from sarthi.orchestrator.runner import active_strategy, latest_run_id, run_pipeline, run_summary
from sarthi.orchestrator.state import PHASE_OF
from sarthi.seed import catalog as cat
from sarthi.seed.generator import seed_database
from tests.conftest import TEST_SEED, TEST_TODAY


def run(trigger="test", **kw) -> tuple[int, dict]:
    run_id = asyncio.run(run_pipeline(trigger, **kw))
    return run_id, run_summary(run_id)


def count(model, *where) -> int:
    with session() as s:
        return s.exec(select(func.count()).select_from(model).where(*where)).one()


def alerts_of(run_id: int) -> list[Alert]:
    with session() as s:
        return list(s.exec(select(Alert).where(Alert.run_id == run_id)).all())


def debate_of(run_id: int) -> list[Event]:
    return Blackboard(run_id).events(kind="debate")


@pytest.fixture(scope="module")
def pipeline_db(tmp_path_factory, analysis):
    """An identical seeded database of its own, with the session's forecast analysis reused for it."""
    root = tmp_path_factory.mktemp("pipeline")
    previous = {k: os.environ[k] for k in ("SARTHI_DB_PATH", "SARTHI_OUTBOX_PATH")}
    os.environ.update(SARTHI_DB_PATH=str(root / "pipeline.db"), SARTHI_OUTBOX_PATH=str(root / "outbox"))
    get_settings.cache_clear()
    reset_engine()
    reset_llm()
    init_db()
    seed_database(TEST_SEED, today=TEST_TODAY)
    saved_cache = dict(demand_intel._cache)
    demand_intel._cache[demand_intel._fingerprint(get_settings())] = analysis    # same data, so the same analysis holds
    yield root
    demand_intel._cache.clear()
    demand_intel._cache.update(saved_cache)
    reset_engine()
    os.environ.update(previous)
    get_settings.cache_clear()
    reset_llm()


@pytest.fixture(scope="module")
def first(pipeline_db):
    """The first real run on a fresh system."""
    return run("first")


# ── Orchestration ────────────────────────────────────────────────────────────

def test_graph_has_every_agent_in_a_phase():
    assert set(AGENT_CLASSES) == set(PHASE_OF) and len(AGENT_CLASSES) == 8
    agents = build_agents(Blackboard(-5), SimpleNamespace(), get_settings(), {"mode": "Balanced"})
    nodes = set(build_graph(agents).get_graph().nodes)
    assert set(AGENT_CLASSES) <= nodes


def test_a_run_completes_through_all_four_phases(first):
    run_id, summary = first
    assert summary["status"] == "done" and summary["errors"] == []
    bb = Blackboard(run_id)
    assert bb.events(kind="error") == []
    assert [e.value["v"] for e in bb.events(kind="context") if e.key == "phase"] == ["sense", "decide", "resolve", "execute"]
    with session() as s:
        record = s.get(Run, run_id)
    assert (record.status, record.trigger, record.dry_run) == ("done", "first", False) and record.finished_at is not None
    assert record.strategy["mode"] == "Balanced" and latest_run_id() == run_id
    assert {k: d["zone"] for k, d in summary["decisions"].items()} == cat.ZONE_TARGET
    assert count(SkuSnapshot, SkuSnapshot.run_id == run_id) == 70            # 7 days x 10 SKUs on the first run


def test_alerts_are_raised_with_confidence_and_wording(first):
    run_id, summary = first
    alerts = alerts_of(run_id)
    assert len(alerts) == len(summary["alerts"]) == summary["approved"] >= 5
    assert {a.zone for a in alerts} >= {"chaos", "ghost", "money"}
    for a in alerts:
        assert 35 <= a.confidence <= 99 and a.msg and a.action and a.impact
        assert a.status == "open" and a.routed == "review" and a.txid is None
    assert len({a.confidence for a in alerts}) >= 4                          # confidence discriminates between alerts
    assert (count(PurchaseOrder), count(TransferOrder)) == (0, 0)            # a fresh system executes nothing by itself


def test_approved_purchases_explain_what_would_change_the_decision(first):
    purchases = [a for a in alerts_of(first[0]) if a.payload["kind"] == "purchase"]
    assert len(purchases) == 3
    for a in purchases:
        cf = a.payload["counterfactual"]
        assert cf and ("lead_days" in cf or "on_hand" in cf)
        assert cf["on_hand"] > first[1]["decisions"][a.sku_id]["qty"] * 0        # a positive stock level
        assert cf["on_hand"] > 150 or a.sku_id != "SKU003"                       # Lays would need far more than its 150


# ── The debate ───────────────────────────────────────────────────────────────

def test_debate_follows_the_protocol(first):
    lines = debate_of(first[0])
    stances = [e.value["stance"] for e in lines]
    assert {"propose", "object", "revise", "rule"} <= set(stances)
    assert stances.count("propose") == stances.count("rule") == first[1]["proposals"]
    for e in lines:
        facts = e.value["facts"]
        assert e.text and debate.has_line(facts["key"]) and e.phase == "resolve"
        if e.value["stance"] == "object":
            assert facts["rule"] and facts["severity"] in ("block", "warn")
            assert e.agent in ("forecaster", "cfoAgent", "esgGuardian", "riskAgent")
        if e.value["stance"] == "rule":
            assert e.agent == "rlhfArbiter"
    # every proposal is stated before anyone objects to it or rules on it
    for sku_id in {e.sku_id for e in lines}:
        own = [e.value["stance"] for e in lines if e.sku_id == sku_id]
        assert own[0] == "propose" and own[-1] == "rule"


def test_borrowed_demand_objection_shrinks_the_order(first):
    run_id = first[0]
    lines = [e for e in debate_of(run_id) if e.sku_id == "SKU010"]
    objection = next(e for e in lines if e.value["facts"].get("rule") == "BORROWED_DEMAND")
    revision = next(e for e in lines if e.value["stance"] == "revise")
    assert objection.agent == "forecaster" and revision.agent == "negotiator"
    facts = objection.value["facts"]
    assert facts["suggested_qty"] < facts["qty"] and revision.value["facts"]["qty"] == facts["suggested_qty"]
    alert = next(a for a in alerts_of(run_id) if a.sku_id == "SKU010" and a.payload["kind"] == "purchase")
    assert alert.payload["qty"] == facts["suggested_qty"] and str(facts["suggested_qty"]) in alert.action


def test_debate_and_alert_sentences_contain_only_known_numbers(first):
    for e in debate_of(first[0]):
        assert grounded(e.text, e.value["facts"]), e.text
    for a in alerts_of(first[0]):
        assert grounded(a.msg, a.payload["facts"]), a.msg


def test_every_debate_line_exists_in_both_languages():
    assert set(debate.LINES["EN"]) == set(debate.LINES["HI"])
    assert debate.render("revise:qty", "HI", {"qty": 270, "order_value": 15566}).startswith("संशोधित")
    assert debate.render("revise:qty", "TA", {"qty": 270, "order_value": 15566}).startswith("Revised")     # falls back to English
    assert debate.days_text(1) == "1 day" and debate.days_text(3) == "3 days"
    assert debate.rejection_reason("BUDGET_CAP", "EN") == "the budget cannot cover it"
    assert debate.rejection_reason("SOMETHING_NEW", "EN") == "an objection could not be resolved"


def test_hindi_run_debates_in_hindi(first):
    run_id, _ = run("hindi", dry_run=True, lang="HI")
    text = " ".join(e.text for e in debate_of(run_id))
    assert "स्वीकृत" in text and "Approved" not in text


# ── What-if, budget, failure ─────────────────────────────────────────────────

def test_a_what_if_debates_but_leaves_no_alerts_or_orders(first):
    before = (count(Alert), count(PurchaseOrder), count(TransferOrder), count(SkuSnapshot))
    run_id, summary = run("what-if", scenario={"lead_mult": 1.3}, dry_run=True)
    assert summary["status"] == "done" and summary["alerts"] == [] and summary["executed"] == []
    assert (count(Alert), count(PurchaseOrder), count(TransferOrder), count(SkuSnapshot)) == before
    assert len(debate_of(run_id)) >= summary["proposals"] * 2
    assert latest_run_id() == first[0] and latest_run_id(dry_run=True) == run_id      # it does not replace the real run
    base = first[1]["decisions"]
    assert all(summary["decisions"][k]["stockout_prob"] >= base[k]["stockout_prob"] for k in base)


def test_a_tiny_budget_rejects_purchases(first, monkeypatch):
    monkeypatch.setenv("SARTHI_MONTHLY_BUDGET", "1000")
    get_settings.cache_clear()
    try:
        run_id, summary = run("tight", dry_run=True)
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
    rejected = dict(summary["rejected"])
    assert any("BUDGET_CAP" in reasons for reasons in rejected.values())
    lines = debate_of(run_id)
    assert any(e.value["facts"].get("rule") == "BUDGET_CAP" and e.agent == "cfoAgent" for e in lines)
    assert any(e.value["facts"].get("status") == "rejected" and "budget" in e.text for e in lines)
    assert summary["approved"] < first[1]["approved"]


def test_a_failing_agent_marks_the_run_failed_without_crashing(first, monkeypatch):
    def boom(settings):
        raise RuntimeError("history unreadable")

    monkeypatch.setattr(demand_intel, "_cache", {})
    monkeypatch.setattr(demand_intel, "analyse", boom)
    run_id, summary = run("broken", dry_run=True)
    assert summary["status"] == "failed" and any("history unreadable" in e for e in summary["errors"])
    with session() as s:
        assert s.get(Run, run_id).status == "failed"
    assert latest_run_id(dry_run=True) != run_id                 # a failed run is never treated as the latest result


# ── Confidence and routing ───────────────────────────────────────────────────

def test_confidence_building_blocks():
    assert conf.data_quality(540, 40, 0, False) == 1.0 and conf.data_quality(60, 3, 9, True) == 0.0
    assert conf.combine(1.0, 0.0, 1.0) == 99 and conf.combine(0.0, 0.9, 0.0) == 35       # clipped to 35..99
    assert conf.combine(1.0, 0.12, 1.0) > conf.combine(0.6, 0.12, 1.0) > conf.combine(0.6, 0.40, 0.5)
    assert conf.audit_confidence("cannibalization", -0.48) == 68 and conf.audit_confidence("phantom_inventory") == 80
    errors = np.array([-0.2, 0.0, 0.1, 0.3])
    assert conf.overstock_stability({"doc": 95.0}, 30, errors, 20, seed=1) == 1.0          # far above the threshold: stable
    assert 0 < conf.overstock_stability({"doc": 33.0}, 30, errors, 200, seed=1) < 1        # near it: depends on the error
    assert conf.overstock_stability({"doc": 95.0}, 30, np.array([]), 20, seed=1) == 0.0


def test_routing_needs_every_condition(pipeline_db):
    settings = get_settings()
    try:
        assert conf.route(95, 1000, [], False, "t:fresh", settings)[0] == "review"         # no track record yet
        for _ in range(8):
            bandit.record("t:trusted", True)
        assert conf.route(95, 1000, [], False, "t:trusted", settings) == ("auto", [])
        assert conf.route(84, 1000, [], False, "t:trusted", settings)[0] == "review"
        assert conf.route(95, 60_000, [], False, "t:trusted", settings)[0] == "review"
        assert conf.route(95, 1000, ["RESIDUAL_RISK"], False, "t:trusted", settings)[0] == "review"
        route, reasons = conf.route(95, 1000, [], True, "t:trusted", settings)
        assert route == "review" and reasons == ["the manager asked to be consulted"]
        bandit.record("t:trusted", False)
        bandit.record("t:trusted", False)
        assert conf.route(95, 1000, [], False, "t:trusted", settings)[0] == "review"       # two dismissals lose it again
    finally:
        with session() as s:
            for arm in s.exec(select(BanditArm).where(BanditArm.key.startswith("t:"))).all():
                s.delete(arm)
            s.commit()


def test_earned_autonomy_executes_automatically(first):
    for _ in range(8):
        bandit.record("money:transfer", True)
    try:
        run_id, summary = run("trusted")
        auto = [a for a in alerts_of(run_id) if a.routed == "auto"]
        assert len(auto) == 1 and len(summary["executed"]) == 1
        a = auto[0]
        assert (a.zone, a.type, a.status) == ("money", "transfer", "approved") and a.confidence >= 85
        assert a.txid == summary["executed"][0] and a.txid.startswith("TRF-")
        assert count(TransferOrder) == 1 and count(PurchaseOrder) == 0
        assert all(x.status == "open" for x in alerts_of(run_id) if x.routed == "review")
        assert any(e.value["stance"] == "execute" for e in debate_of(run_id))
        assert any("executing automatically" in e.text for e in debate_of(run_id))
    finally:
        with session() as s:
            s.delete(s.get(BanditArm, "money:transfer"))
            s.commit()


# ── Learning from the manager ────────────────────────────────────────────────

def test_feedback_phrases_are_understood_without_the_model():
    assert rule_based("avoid Metro Cash & Carry").model_dump(include={"directive", "target"}) == {"directive": "avoid_supplier", "target": "metro cash & carry"}
    assert rule_based("Don't use HUL again").directive == "avoid_supplier"
    assert rule_based("always use Reliance").directive == "prefer_supplier"
    cap = rule_based("max 300 units please")
    assert (cap.directive, cap.value) == ("cap_qty", 300.0)
    cover = rule_based("keep at least 10 days of this")
    assert (cover.directive, cover.value) == ("min_cover_days", 10.0)
    assert rule_based("always ask me first").directive == "no_auto"
    assert rule_based("looks fine, thanks").model_dump(include={"directive", "note"}) == {"directive": "other", "note": "looks fine, thanks"}


def test_preferences_attach_to_real_suppliers_and_skus(pipeline_db):
    avoid = to_preference(PreferenceOut(scope="supplier", target="metro cash and carry", directive="avoid_supplier", note="n"), "SKU003")
    assert (avoid.directive, avoid.target, avoid.scope) == ("avoid_supplier", "SUP-MCC", "supplier")
    by_id = to_preference(PreferenceOut(target="sup-hul", directive="prefer_supplier"), "SKU003")
    assert by_id.target == "SUP-HUL"
    cap = to_preference(PreferenceOut(directive="cap_qty", value=300), "SKU003")
    assert (cap.directive, cap.target, cap.value) == ("cap_qty", "SKU003", 300)             # defaults to the alert's product
    named = to_preference(PreferenceOut(target="maggi", directive="cap_qty", value=50), "SKU003")
    assert named.target == "SKU005"
    unknown = to_preference(PreferenceOut(target="Acme Traders", directive="avoid_supplier", note="avoid Acme"), "SKU003")
    assert (unknown.directive, unknown.target) == ("other", "")                              # no such supplier: kept as a note
    assert to_preference(PreferenceOut(directive="cap_qty"), "SKU003").directive == "other"  # a cap with no number


async def test_dismissal_updates_the_approval_rate_and_is_logged(first):
    alert = next(a for a in alerts_of(first[0]) if a.type == "cannibalization")
    key = conf.arm_key(alert.zone, alert.type)
    try:
        result = await learn_from_decision(alert.id, approved=False, feedback=None, llm=get_llm())
        assert result == {"delta": pytest.approx(0.4 - 0.5), "preference": None, "target": None}
        assert bandit.get_arm(key) == (2.0, 3.0)
        with session() as s:
            assert s.get(Alert, alert.id).status == "dismissed"
        bb = Blackboard(latest_run_id())
        assert bb.get("rlhf_weight_update") == "-0.10"
        assert any(e.agent == "rlhfArbiter" and "dismissed" in e.text for e in bb.events(kind="action"))
        with pytest.raises(LookupError):
            await learn_from_decision(999_999, True, None, get_llm())
    finally:
        with session() as s:
            arm = s.get(BanditArm, key)
            if arm:
                s.delete(arm)
            s.commit()


async def test_feedback_becomes_a_rule_the_next_run_obeys(first):
    lays = next(a for a in alerts_of(first[0]) if a.sku_id == "SKU003")
    assert lays.payload["supplier_id"] == "SUP-REL"
    try:
        result = await learn_from_decision(lays.id, approved=False, feedback="avoid Reliance Metro", llm=get_llm())
        assert (result["preference"], result["target"]) == ("avoid_supplier", "SUP-REL")
        with session() as s:
            stored = s.exec(select(Preference).where(Preference.directive == "avoid_supplier")).one()
            assert (stored.target, stored.active) == ("SUP-REL", True) and s.get(Alert, lays.id).feedback == "avoid Reliance Metro"
        await learn_from_decision(lays.id, approved=False, feedback="max 300 units", llm=get_llm())

        run_id = await run_pipeline("after-feedback", dry_run=True)
        summary = run_summary(run_id)
        assert summary["status"] == "done"
        proposals = [e for e in debate_of(run_id) if e.value["stance"] == "propose" and "supplier" in e.value["facts"]]
        assert proposals and all(e.value["facts"]["supplier"] != "Reliance Metro WH" for e in proposals)
        capped = [e for e in debate_of(run_id) if e.sku_id == "SKU003" and e.value["facts"].get("rule") == "USER_PREFERENCE"]
        assert capped and capped[0].agent == "cfoAgent"
        revised = [e for e in debate_of(run_id) if e.sku_id == "SKU003" and e.value["stance"] == "revise"]
        assert revised and revised[-1].value["facts"]["qty"] == 300
    finally:
        with session() as s:
            for row in s.exec(select(Preference)).all():
                s.delete(row)
            for arm in s.exec(select(BanditArm)).all():
                s.delete(arm)
            s.commit()


# ── Strategy record ──────────────────────────────────────────────────────────

def test_expired_strategy_reverts_to_balanced(pipeline_db):
    with session() as s:
        for p in s.exec(select(StrategyPolicy).where(StrategyPolicy.active)).all():
            p.active = False
            s.add(p)
        s.add(StrategyPolicy(mode="Cash Flow", params={"safetyStockMultiplier": 0.8}, active=True,
                             expires_on=TEST_TODAY + timedelta(days=36500)))
        s.commit()
    try:
        assert active_strategy() == {"mode": "Cash Flow", "savingsPriority": 0.5, "safetyStockMultiplier": 0.8, "leadTimeBuffer": 1.2}
        with session() as s:
            policy = s.exec(select(StrategyPolicy).where(StrategyPolicy.active)).one()
            policy.expires_on = TEST_TODAY - timedelta(days=1)          # long past
            s.add(policy)
            s.commit()
        assert active_strategy()["mode"] == "Balanced"
        assert count(StrategyPolicy, StrategyPolicy.active) == 1
    finally:
        with session() as s:
            for p in s.exec(select(StrategyPolicy)).all():
                s.delete(p)
            s.add(StrategyPolicy(mode="Balanced", params=dict(cat.DEFAULT_STRATEGY), source_text="seed", active=True))
            s.commit()


async def test_debate_rewording_is_opt_in(first):
    class Recorder:
        def __init__(self):
            self.calls = 0

        async def text(self, system, user, *, fallback, **kw):
            self.calls += 1
            assert kw["source"] == user and kw["must_contain"] is not None      # always reworded under the strict guard
            return fallback, "template"

    proposal = {"id": "audit:phantom_inventory:SKU005", "kind": "audit", "alert_type": "phantom_inventory", "sku_id": "SKU005",
                "author": "overstockResolver", "payload": {"task": "cycle_count", "aisle": "E"}, "cost": 0.0, "par_rescued": 100.0,
                "facts": {"sku": "Maggi 70g", "days": 6, "aisle": "E", "est_units": 280}}
    state = {"proposals": [proposal], "decisions": {}, "envelope": {}}
    for flag, expected in ((False, 0), (True, 2)):                              # one proposal: a propose line and a ruling
        llm = Recorder()
        settings = get_settings().model_copy(update={"llm_reword_debate": flag})
        result = await Arbiter(Blackboard(-7), llm, settings, {"mode": "Balanced"}).run(dict(state))
        assert result["rulings"][0]["status"] == "approved" and result["rulings"][0]["confidence"] == 80
        assert llm.calls == expected


def test_arbiter_handles_an_empty_run(pipeline_db):
    bb = Blackboard(-6)
    arbiter = Arbiter(bb, get_llm(), get_settings(), {"mode": "Balanced"})
    assert asyncio.run(arbiter.run({"proposals": [], "decisions": {}, "envelope": {}})) == {"rulings": []}


def test_alert_templates_cover_every_debated_proposal_type():
    from sarthi.agents.proposal import ALERT_TYPES

    assert all(debate.has_line(f"propose:{t}") and t in templates.ALERT for t in ALERT_TYPES)


def test_validator_orchestration_check(first):
    result = validate.check_orchestration()
    assert result.status == validate.PASS, result.detail
    assert "last run" in result.detail
