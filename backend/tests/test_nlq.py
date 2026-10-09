"""Chat, strategy sentences, explanations and voice commands, on their own seeded database with one completed run.

The model is switched off unless a test hands the gateway a canned reply.
"""

import asyncio
import json
import os
import re
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import func, select

from sarthi import validate
from sarthi.agents import demand_intel
from sarthi.api import presenters
from sarthi.api.main import create_app
from sarthi.api.routers import runs
from sarthi.config import BACKEND_ROOT, get_settings
from sarthi.db import init_db, reset_engine, session
from sarthi.llm import get_llm, reset_llm, templates
from sarthi.llm.grounding import grounded
from sarthi.models import Alert, Event, Preference, PurchaseOrder, Run, Sku, StrategyPolicy, local_today
from sarthi.nlq import handlers, router, strategy, voice
from sarthi.nlq.catalogue import resolve_sku
from sarthi.nlq.intents import Intent
from sarthi.orchestrator.runner import active_strategy, run_pipeline, run_summary
from sarthi.seed.generator import seed_database
from tests.conftest import TEST_SEED, TEST_TODAY

I18N = BACKEND_ROOT.parent / "src" / "data" / "i18n.js"
DEVANAGARI = re.compile(r"[ऀ-ॿ]")
REPLY_FIELDS = {"key", "params", "text", "strategy", "refresh", "navigate", "intent", "source"}

# Sentences the built-in chat (`ask()` in src/pages/Intelligence.jsx) answers today, and the branch each takes there.
LEGACY = [
    ("hi", "greeting"), ("Hello there", "greeting"), ("namaste", "greeting"), ("नमस्ते", "greeting"),
    ("prioritize cash flow", "strategy_set"), ("strategy: savings first", "strategy_set"), ("कैश को प्राथमिकता दो", "strategy_set"),
    ("switch to aggressive growth mode", "strategy_set"), ("रणनीति विकास", "strategy_set"),
    ("what is the current strategy?", "strategy_get"), ("which mode are we in", "strategy_get"),
    ("tell me about suppliers", "suppliers"), ("distributor performance", "suppliers"), ("आपूर्तिकर्ता कैसे हैं", "suppliers"),
    ("market basket analysis", "basket"), ("show co-purchase patterns", "basket"), ("बास्केट विश्लेषण", "basket"),
    ("bullwhip smoothing", "bullwhip"), ("बुलव्हिप", "bullwhip"),
    ("monte carlo results", "montecarlo"), ("run the simulation", "montecarlo"), ("सिमुलेशन दिखाओ", "montecarlo"),
    ("which SKUs are critical?", "sku_risk"), ("high risk products", "sku_risk"), ("उत्पाद जोखिम", "sku_risk"),
    ("best products", "sku_sweet"), ("sweet spot items", "sku_sweet"),
    ("how many SKUs do we have", "sku_summary"), ("उत्पाद", "sku_summary"),
    ("stock summary", "stock_summary"), ("total inventory", "stock_summary"), ("स्टॉक कितना है", "stock_summary"),
    ("what are the zones?", "zones"), ("money pit meaning", "zones"), ("जोन क्या हैं", "zones"),
]
NEW = [
    ("which SKUs will stock out in 7 days", "stockout_horizon"),       # "which" contains "hi": not a greeting
    ("what will run out in the next 3 days?", "stockout_horizon"), ("कौन से उत्पाद खत्म होंगे", "stockout_horizon"),
    ("where is the overstock?", "overstock"), ("excess stock", "overstock"), ("ओवरस्टॉक", "overstock"),
    ("why is lays in the chaos zone?", "explain_sku"), ("explain tata salt", "explain_sku"), ("नमक क्यों", "explain_sku"),
    ("reorder 50 units of lays", "reorder"), ("order 120 colgate", "reorder"), ("reorder maggi", "reorder"),
    ("this is a high risk item", "sku_risk"),                           # "this" and "high" contain "hi" too
]


@pytest.fixture(scope="module")
def client(tmp_path_factory, analysis):
    """A seeded database of its own with one completed run, and a client whose event loop lasts the module."""
    root = tmp_path_factory.mktemp("nlq")
    previous = {k: os.environ[k] for k in ("SARTHI_DB_PATH", "SARTHI_OUTBOX_PATH")}
    os.environ.update(SARTHI_DB_PATH=str(root / "nlq.db"), SARTHI_OUTBOX_PATH=str(root / "outbox"))
    get_settings.cache_clear()
    reset_engine()
    reset_llm()
    presenters.reset()
    init_db()
    seed_database(TEST_SEED, today=TEST_TODAY)
    saved_cache = dict(demand_intel._cache)
    demand_intel._cache[demand_intel._fingerprint(get_settings())] = analysis    # same data, so the same analysis holds
    asyncio.run(run_pipeline("test"))
    with TestClient(create_app()) as test_client:
        yield test_client
    demand_intel._cache.clear()
    demand_intel._cache.update(saved_cache)
    reset_engine()
    os.environ.update(previous)
    get_settings.cache_clear()
    reset_engine()
    reset_llm()
    presenters.reset()


def chat(client, text: str, lang: str = "EN") -> dict:
    r = client.post("/api/chat", json={"text": text, "lang": lang})
    assert r.status_code == 200, r.text
    assert set(r.json()) == REPLY_FIELDS
    return r.json()


def speak(client, text: str, lang: str = "EN") -> dict:
    r = client.post("/api/voice/intent", json={"text": text, "lang": lang})
    assert r.status_code == 200, r.text
    assert set(r.json()) == {"type", "skuId", "path", "lang", "text"}
    return r.json()


def boot(client) -> dict:
    r = client.get("/api/bootstrap")
    assert r.status_code == 200, r.text
    return r.json()


def count(model, *where) -> int:
    with session() as s:
        return s.exec(select(func.count()).select_from(model).where(*where)).one()


def wait_for_runs(client) -> int:
    """Block until the run most recently started through the API has finished. Returns its id."""
    run_id = runs._current[0]
    with client.stream("GET", f"/api/runs/{run_id}/stream") as r:
        for _ in r.iter_lines():
            pass
    with session() as s:
        assert s.get(Run, run_id).status == "done"
    return run_id


def canned(monkeypatch, reply):
    """Make the gateway answer with `reply` (a string, or a function of the prompt) as if the model had."""
    async def fake_chat(system, user, **kwargs):
        return reply(user) if callable(reply) else reply

    monkeypatch.setattr(get_llm(), "_chat", fake_chat)


# ── routing ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("sentence", "expected"), LEGACY + NEW)
def test_rule_router(client, sentence, expected):
    intent = router.route_rules(sentence)
    assert intent is not None and intent.name == expected


def test_rule_router_extracts_details(client):
    assert router.route_rules("which SKUs will stock out in 7 days").days == 7
    assert router.route_rules("what runs out in 2 weeks").days == 14
    assert router.route_rules("anything about to run out?").days == 7
    assert router.route_rules("reorder 50 units of lays") == Intent(name="reorder", sku="SKU003", quantity=50)
    assert router.route_rules("reorder lays classic 26g").quantity == 0                # the pack size is not a quantity
    assert router.route_rules("prioritize cash flow over growth").mode == "Cash Flow"
    assert router.route_rules("strategy reset to normal").mode == "Balanced"
    assert router.route_rules("why is that happening") is None                         # no product named: ask the model
    assert router.route_rules("tell me a joke") is None
    for sentence in ("what is the capital of France?", "a model question", "the hospital", "a despot"):
        assert router.route_rules(sentence) is None, sentence            # "pit", "mode", "spot" inside other words
    assert router.route_rules("suppliers?").name == "suppliers" and router.route_rules("all the zones").name == "zones"
    assert router.route_rules("   ").name == "unknown"


def test_resolve_sku(client):
    expected = {"lays": "SKU003", "tata salt": "SKU004", "नमक": "SKU004", "नमकीन": "SKU010", "zzz": None, "": None,
                "SKU007": "SKU007", "sku 6": "SKU006", "SKU999": None, "Amul Butter 500g": "SKU001", "the toothpaste": "SKU006",
                "colgat": "SKU006", "haldiram namkeen": "SKU010", "मक्खन का क्या हाल है": "SKU001", "तेल": "SKU007",
                "boil the soil": None, "assault": None, "snacks": "SKU003", "surf excel and lays": "SKU002"}
    assert {text: resolve_sku(text) for text in expected} == expected


def test_unrouted_sentence_without_the_model_gets_the_default_prompt(client):
    reply = chat(client, "tell me a joke")
    assert (reply["key"], reply["intent"], reply["source"]) == ("intelDefaultPrompt", "unknown", "none")


def test_model_routes_what_the_rules_cannot(client, monkeypatch):
    canned(monkeypatch, '{"name": "overstock"}')
    reply = chat(client, "where is money sitting on shelves?")
    assert (reply["intent"], reply["source"]) == ("overstock", "llm")
    assert reply["key"] is None and "tied up" in reply["text"]

    canned(monkeypatch, '{"name": "explain_sku", "sku": "the toothpaste"}')
    reply = chat(client, "what is going on with the toothpaste?")
    assert reply["intent"] == "explain_sku" and "Colgate Strong 200g" in reply["text"] and reply["navigate"] == "/sku/SKU006"

    canned(monkeypatch, '{"name": "reorder", "sku": "unicorn dust", "quantity": 40}')      # a product that does not exist
    alerts = count(Alert)
    assert chat(client, "get me forty of the sparkly stuff")["key"] == "intelDefaultPrompt"
    assert count(Alert) == alerts

    canned(monkeypatch, "I think you want overstock")                                       # not JSON, twice
    assert chat(client, "hmm what about the thing")["key"] == "intelDefaultPrompt"


# ── answers ──────────────────────────────────────────────────────────────────

def test_answers_with_frontend_templates_fill_every_placeholder(client):
    source = I18N.read_text(encoding="utf-8")
    english = source[: source.index("  HI:")] if "  HI:" in source else source
    questions = {"hi": "intelGreetings", "critical skus": "criticalSkuRiskRes", "best products": "sweetSpotRes",
                 "how many skus": "skuZoneSummary", "stock summary": "inventoryStockSummary",
                 "tell me about suppliers": "supplierTrack", "market basket analysis": "basketRules",
                 "what are the zones": "zoneIkigaiDesc", "current strategy?": "currentStrategyWeights",
                 "tell me a joke": "intelDefaultPrompt"}
    for question, key in questions.items():
        reply = chat(client, question)
        assert reply["key"] == key and reply["text"] == "", question
        template = re.search(rf'\b{key}: "((?:[^"\\]|\\.)*)"', english)
        assert template, f"{key} is not in the frontend's translations"
        assert set(re.findall(r"\{(\w+)\}", template.group(1))) <= set(reply["params"]), key
    assert set(questions.values()) | {"strategyUpdatedCash", "strategyUpdatedGrowth"} == set(validate.CHAT_KEYS)


def test_critical_skus_come_from_the_run(client):
    reply = chat(client, "critical skus")
    rows = {row["name"]: row for row in boot(client)["skuData"]}
    listed = re.findall(r"([^,(]+?) \((\d+)%\)", reply["params"]["list"])
    assert listed and all(int(pct) > 60 for _, pct in listed)
    assert all(rows[name.strip()]["zone"] == "chaos" for name, _ in listed)
    assert float(reply["params"]["par"]) > 0


def test_summaries_agree_with_bootstrap(client):
    rows = boot(client)["skuData"]
    summary = chat(client, "how many products")["params"]
    assert summary["count"] == len(rows) == summary["sweet"] + summary["chaos"] + summary["ghost"] + summary["money"]
    stock = chat(client, "inventory total")["params"]
    assert stock["total"] == sum(r["stock"] for r in rows) and 0 <= stock["ghostPct"] <= 100
    sweet = chat(client, "best items")["params"]
    assert sweet["count"] == sum(r["zone"] == "sweet" for r in rows)


def test_text_answers_are_computed_and_bilingual(client):
    for question in ("bullwhip smoothing", "monte carlo results", "which SKUs will stock out in 7 days", "where is the overstock?"):
        en, hi, ta = chat(client, question), chat(client, question, "HI"), chat(client, question, "TA")
        assert en["key"] is None and en["text"] and not DEVANAGARI.search(en["text"]), question
        assert DEVANAGARI.search(hi["text"]), question
        assert ta["text"] == en["text"]                                    # other languages get English text
        assert sorted(re.findall(r"\d+", en["text"])) == sorted(re.findall(r"\d+", hi["text"])), question    # same numbers
    assert str(get_settings().mc_paths) in chat(client, "monte carlo results")["text"]
    week = chat(client, "which SKUs will stock out in 7 days")["text"]
    month = chat(client, "which SKUs will stock out in 60 days")["text"]
    assert int(re.match(r"(\d+)", month).group(1)) >= int(re.match(r"(\d+)", week).group(1))


def test_fixed_claims_in_frontend_templates_are_only_used_when_true():
    def view(distributors=(), cannibals=()):
        boot_data = {"skuData": [], "distributors": list(distributors), "cannibalization": list(cannibals),
                     "mbaRules": [{"antecedent": ["A"], "consequent": "B", "confidence": 0.5, "lift": 2.0, "support": 0.1}]}
        return handlers.View(1, boot_data, {}, {})

    def dist(name, tat, capacity):
        return {"name": name, "tier": "Gold", "fulfillment": 0.9, "avgTAT": tat, "capacityLimit": capacity}

    as_claimed = [dist("Reliance Metro WH", 1.4, 1800), dist("Metro Cash & Carry", 4.1, 2000)]
    assert handlers.suppliers(view(as_claimed), "EN")["key"] == "supplierTrack"
    changed = handlers.suppliers(view([dist("Reliance Metro WH", 5.0, 1800), dist("Metro Cash & Carry", 4.1, 2000)]), "EN")
    assert changed["key"] is None and "Metro Cash & Carry is the fastest" in changed["text"]
    assert handlers.suppliers(view(), "EN")["key"] is None

    snacks = [{"category": "Snacks"}, {"category": "Personal Care"}]
    assert handlers.basket(view(cannibals=snacks), "EN")["key"] == "basketRules"
    other = handlers.basket(view(cannibals=[{"category": "Dairy"}]), "EN")
    assert other["key"] is None and "1 cannibalization alerts are active" in other["text"]


def test_chat_wording_is_complete_in_both_languages():
    for kind in templates.CHAT:
        assert templates.fields("chat", kind, "EN") == templates.fields("chat", kind, "HI"), kind
        assert DEVANAGARI.search(templates.CHAT[kind]["HI"]["text"]) and not DEVANAGARI.search(templates.CHAT[kind]["EN"]["text"])


def test_warming_up_before_the_first_run(client, monkeypatch):
    monkeypatch.setattr(presenters, "latest_run_id", lambda *a, **k: None)
    reply = chat(client, "critical skus")
    assert reply["key"] is None and "not finished" in reply["text"]
    assert chat(client, "hi")["key"] == "intelGreetings"            # needs no data


def test_chat_rejects_empty_and_oversized_text(client):
    assert client.post("/api/chat", json={"text": ""}).status_code == 422
    assert client.post("/api/chat", json={"text": "x" * 501}).status_code == 422
    assert client.post("/api/chat", json={}).status_code == 422


# ── explain ──────────────────────────────────────────────────────────────────

def test_explain_is_grounded_for_every_product(client):
    kinds = set()
    for row in boot(client)["skuData"]:
        r = client.get(f"/api/skus/{row['id']}/explain")
        assert r.status_code == 200, r.text
        body = r.json()
        assert set(body) == {"text", "facts", "source"} and body["source"] == "template"
        assert row["name"] in body["text"] and grounded(body["text"], body["facts"]), row["id"]
        assert body["facts"]["on_hand"] == row["stock"]
        kinds.add("order" if "qty" in body["facts"] else "action" if "action" in body["facts"] else "none")
        hindi = client.get(f"/api/skus/{row['id']}/explain", params={"lang": "HI"}).json()
        assert DEVANAGARI.search(hindi["text"]) and row["name"] in hindi["text"] and grounded(hindi["text"], hindi["facts"])
    assert kinds == {"order", "action", "none"}                     # the seeded run exercises every kind of explanation
    assert client.get("/api/skus/sku003/explain").status_code == 200       # the id is not case-sensitive
    assert client.get("/api/skus/NOPE/explain").status_code == 404


def test_explain_accepts_a_faithful_rewrite_and_rejects_an_unfaithful_one(client, monkeypatch):
    template = client.get("/api/skus/SKU003/explain").json()["text"]

    canned(monkeypatch, lambda prompt: prompt.replace("Recommended:", "The recommendation is"))
    reworded = client.get("/api/skus/SKU003/explain").json()
    assert reworded["source"] == "llm" and "The recommendation is" in reworded["text"]

    canned(monkeypatch, lambda prompt: prompt.replace("Recommended:", "Recommended: 9999 units, or"))    # a number from nowhere
    assert client.get("/api/skus/SKU003/explain").json() == {**reworded, "text": template, "source": "template"}

    canned(monkeypatch, lambda prompt: prompt.replace("Lays Classic 26g", "This product"))               # drops the name
    assert client.get("/api/skus/SKU003/explain").json()["source"] == "template"

    canned(monkeypatch, lambda prompt: prompt)
    assert client.get("/api/skus/SKU003/explain", params={"lang": "HI"}).json()["source"] == "template"   # Hindi is never reworded


def test_chat_explains_a_named_product(client):
    reply = chat(client, "why is lays in the chaos zone?")
    assert reply["intent"] == "explain_sku" and reply["key"] is None and reply["navigate"] == "/sku/SKU003"
    assert "Lays Classic 26g" in reply["text"] and "Chaos Zone" in reply["text"]


# ── reorder ──────────────────────────────────────────────────────────────────

def test_reorder_creates_one_alert_for_approval_and_no_order(client):
    orders = count(PurchaseOrder)
    recommended = next(r["recommendedQty"] for r in boot(client)["skuData"] if r["id"] == "SKU003")
    reply = chat(client, "reorder 50 units of lays")
    assert reply["intent"] == "reorder" and reply["refresh"] is True and "Alerts page" in reply["text"]
    with session() as s:
        made = [a for a in s.exec(select(Alert).where(Alert.sku_id == "SKU003", Alert.status == "open")).all()
                if a.payload.get("source") == "chat"]
        moq = s.get(Sku, "SKU003").moq
    assert len(made) == 1 and count(PurchaseOrder) == orders
    alert = made[0]
    assert alert.type == "stockout_reorder" and alert.routed == "review"
    assert alert.payload["qty"] >= 50 and alert.payload["qty"] % moq == 0 and alert.payload["qty"] - 50 < moq
    assert grounded(reply["text"], alert.payload["facts"] | {"moq": moq, "total": round(alert.payload["cost"])})
    shown = boot(client)
    assert alert.id in [a["id"] for a in shown["live"]["alerts"]]
    # the manager's request does not replace what the agents recommend
    assert next(r["recommendedQty"] for r in shown["skuData"] if r["id"] == "SKU003") == recommended
    assert f"{recommended} units" in client.get("/api/skus/SKU003/explain").json()["text"]

    chat(client, "reorder 80 units of lays")                        # asking again replaces the request
    with session() as s:
        again = [a for a in s.exec(select(Alert).where(Alert.sku_id == "SKU003", Alert.status == "open")).all()
                 if a.payload.get("source") == "chat"]
    assert [a.id for a in again] == [alert.id] and again[0].payload["qty"] >= 80

    approved = client.post(f"/api/alerts/{alert.id}/approve")       # the normal approval path places it
    assert approved.status_code == 200, approved.text
    with session() as s:
        po = s.get(PurchaseOrder, approved.json()["txid"])
    assert (po.sku_id, po.qty, po.source) == ("SKU003", again[0].payload["qty"], "alert")


def test_reorder_hindi_and_default_quantity(client):
    reply = chat(client, "नमक की 100 यूनिट ऑर्डर करो", "HI")
    assert reply["intent"] == "reorder" and DEVANAGARI.search(reply["text"]) and "Tata Salt 1kg" in reply["text"]
    recommended = next(r["recommendedQty"] for r in boot(client)["skuData"] if r["id"] == "SKU005")
    chat(client, "reorder maggi")
    with session() as s:
        made = next(a for a in s.exec(select(Alert).where(Alert.sku_id == "SKU005", Alert.status == "open")).all()
                    if a.payload.get("source") == "chat")
    assert recommended <= made.payload["qty"] < recommended + 50


# ── strategy ─────────────────────────────────────────────────────────────────

def test_compile_policy():
    def compiled(text, llm=None):
        return asyncio.run(strategy.compile_policy(text, llm))

    assert compiled("Prioritize cash flow over growth for the next 30 days") == strategy.PolicyOut(mode="Cash Flow", horizon_days=30)
    assert compiled("go for aggressive growth for 2 weeks") == strategy.PolicyOut(mode="Growth", horizon_days=14)
    assert compiled("बचत को प्राथमिकता दो 10 दिन") == strategy.PolicyOut(mode="Cash Flow", horizon_days=10)
    assert compiled("reset to normal") == strategy.PolicyOut(mode="Balanced", horizon_days=30)
    assert compiled("growth for 999 days").horizon_days == 180              # clamped
    assert compiled("growth for 0 days").horizon_days == 30              # not a duration: the default applies

    class Model:
        def __init__(self, reply):
            self.reply = reply

        async def json(self, schema, system, user, *, fallback, **kw):
            return self.reply if self.reply is not None else fallback()

    assert compiled("we are bleeding money, tighten up", Model(None)) is None           # no mode word, no model
    guessed = compiled("we are bleeding money, tighten up for 10 days", Model(strategy.PolicyOut(mode="Cash Flow", horizon_days=90)))
    assert guessed == strategy.PolicyOut(mode="Cash Flow", horizon_days=10)             # the duration typed wins


def test_presets_match_the_values_the_ui_sets():
    assert strategy.PRESETS["Cash Flow"] == {"savingsPriority": 0.9, "safetyStockMultiplier": 0.8, "leadTimeBuffer": 1.2}
    assert strategy.PRESETS["Growth"] == {"savingsPriority": 0.2, "safetyStockMultiplier": 1.5, "leadTimeBuffer": 1.5}
    assert strategy.PRESETS["Balanced"] == {"savingsPriority": 0.5, "safetyStockMultiplier": 1.0, "leadTimeBuffer": 1.2}


def test_strategy_from_chat_changes_safety_stock_in_the_next_bootstrap(client):
    before = {r["id"]: r["safetyStock"] for r in boot(client)["skuData"]}
    assert client.get("/api/strategy").json()["mode"] == "Balanced"

    reply = chat(client, "Prioritize cash flow over growth for the next 30 days")
    assert reply["key"] == "strategyUpdatedCash" and reply["refresh"] is True
    assert {k: reply["strategy"][k] for k in ("mode", "savingsPriority", "safetyStockMultiplier", "leadTimeBuffer")} == \
        {"mode": "Cash Flow", **strategy.PRESETS["Cash Flow"]}
    with session() as s:
        active = s.exec(select(StrategyPolicy).where(StrategyPolicy.active)).all()
    assert len(active) == 1 and active[0].mode == "Cash Flow"
    assert active[0].expires_on == local_today() + timedelta(days=30) and "cash flow" in active[0].source_text.lower()

    run_id = wait_for_runs(client)
    after = boot(client)
    assert after["live"]["runId"] == run_id and after["live"]["strategy"]["mode"] == "Cash Flow"
    cash = {r["id"]: r["safetyStock"] for r in after["skuData"]}
    assert all(cash[k] <= before[k] for k in before) and sum(cash.values()) < 0.9 * sum(before.values())
    with session() as s:
        noted = s.exec(select(Event).where(Event.kind == "action", Event.text == "Strategy set to Cash Flow")).all()
    assert len(noted) == 1 and noted[0].agent == "rlhfArbiter" and noted[0].value["result"].startswith("expires ")
    assert chat(client, "what is the current strategy?")["params"] == {"mode": "Cash Flow", "savings": 0.9, "safety": 0.8}

    growth = client.put("/api/strategy", json={"mode": "Growth", "savingsPriority": 0.2, "safetyStockMultiplier": 1.5,
                                               "leadTimeBuffer": 1.5, "lastUpdate": "10:00:00"})      # the object the UI holds
    assert growth.status_code == 200 and growth.json()["mode"] == "Growth" and growth.json()["safetyStockMultiplier"] == 1.5
    wait_for_runs(client)
    grown = {r["id"]: r["safetyStock"] for r in boot(client)["skuData"]}
    assert sum(grown.values()) > 1.3 * sum(before.values())

    assert client.put("/api/strategy", json={"mode": "Reckless"}).status_code == 422
    back = chat(client, "strategy: reset to balanced")
    assert back["key"] is None and "Balanced" in back["text"] and back["strategy"]["mode"] == "Balanced"
    wait_for_runs(client)
    assert {r["id"]: r["safetyStock"] for r in boot(client)["skuData"]} == before
    with session() as s:
        assert s.exec(select(StrategyPolicy).where(StrategyPolicy.active)).one().expires_on is None


def test_apply_policy_and_expiry(client):
    applied = strategy.apply_policy(strategy.PolicyOut(mode="Growth", horizon_days=14), "test")
    assert applied["expiresOn"] == (local_today() + timedelta(days=14)).isoformat()
    assert active_strategy()["mode"] == "Growth"
    with session() as s:
        policy = s.exec(select(StrategyPolicy).where(StrategyPolicy.active)).one()
        policy.expires_on = local_today() - timedelta(days=1)       # as if the 14 days had passed
        s.add(policy)
        s.commit()
    assert active_strategy() == {"mode": "Balanced", **strategy.PRESETS["Balanced"]}
    run_id = asyncio.run(run_pipeline("test"))
    assert run_summary(run_id)["status"] == "done"
    with session() as s:
        assert s.get(Run, run_id).strategy["mode"] == "Balanced"
    presenters.bump()


# ── voice ────────────────────────────────────────────────────────────────────

VOICE = [
    ("show risk for colgate", "SKU_DETAIL", {"skuId": "SKU006"}), ("what is the risk on lays", "SKU_DETAIL", {"skuId": "SKU003"}),
    ("नमक का जोखिम", "SKU_DETAIL", {"skuId": "SKU004"}),
    ("show zone for tata salt", "NAVIGATE_SKU", {"skuId": "SKU004"}), ("show inventory for lays", "NAVIGATE_SKU", {"skuId": "SKU003"}),
    ("मक्खन की इन्वेंट्री दिखाओ", "NAVIGATE_SKU", {"skuId": "SKU001"}),
    ("switch to hindi", "SET_LANG", {"lang": "HI"}), ("switch to english", "SET_LANG", {"lang": "EN"}),
    ("switch to tamil", "SET_LANG", {"lang": "TA"}), ("switch to bengali", "SET_LANG", {"lang": "BN"}),
    ("switch to telugu", "SET_LANG", {"lang": "TE"}), ("switch to marathi", "SET_LANG", {"lang": "MR"}),
    ("switch to gujarati", "SET_LANG", {"lang": "GJ"}), ("switch to kannada", "SET_LANG", {"lang": "KN"}),
    ("हिंदी में बदलो", "SET_LANG", {"lang": "HI"}),
    ("open war room", "NAVIGATE", {"path": "sandbox"}), ("run a disruption scenario", "NAVIGATE", {"path": "sandbox"}),
    ("what if demand doubles", "NAVIGATE", {"path": "sandbox"}), ("वॉर रूम खोलो", "NAVIGATE", {"path": "sandbox"}),
    ("go to alerts", "NAVIGATE", {"path": "alerts"}), ("open inventory", "NAVIGATE", {"path": "inventory"}),
    ("take me to the dashboard", "NAVIGATE", {"path": "dashboard"}), ("replenish page", "NAVIGATE", {"path": "replenish"}),
    ("open the store view", "NAVIGATE", {"path": "store"}), ("data hub", "NAVIGATE", {"path": "datahub"}),
    ("command center", "NAVIGATE", {"path": "command"}),
    ("sing me a song", "UNKNOWN", {}), ("switch to klingon", "UNKNOWN", {}),
]


@pytest.mark.parametrize(("sentence", "kind", "fields"), VOICE)
def test_voice_commands(client, sentence, kind, fields):
    command = speak(client, sentence)
    assert command["type"] == kind and command["text"] == sentence
    assert {k: command[k] for k in fields} == fields
    assert all(command[k] is None for k in ("skuId", "path", "lang") if k not in fields)


def test_voice_falls_back_to_the_model_for_a_product_question(client, monkeypatch):
    canned(monkeypatch, '{"name": "explain_sku", "sku": "toothpaste"}')
    assert speak(client, "how is the toothpaste doing")["skuId"] == "SKU006"
    canned(monkeypatch, '{"name": "suppliers"}')
    assert speak(client, "who brings our goods")["type"] == "UNKNOWN"


def test_voice_safety_stock_adjustment_takes_effect_in_a_new_run(client):
    before = next(r["safetyStock"] for r in boot(client)["skuData"] if r["id"] == "SKU003")
    others = {r["id"]: r["safetyStock"] for r in boot(client)["skuData"] if r["id"] != "SKU003"}
    command = speak(client, "increase safety stock by 30 percent for lays")
    assert (command["type"], command["skuId"]) == ("PROCURE", "SKU003")
    with session() as s:
        rule = s.exec(select(Preference).where(Preference.directive == "safety_stock_pct", Preference.active)).one()
    assert (rule.scope, rule.target, rule.value) == ("sku", "SKU003", 30.0)
    wait_for_runs(client)
    rows = {r["id"]: r["safetyStock"] for r in boot(client)["skuData"]}
    assert rows["SKU003"] == pytest.approx(before * 1.3, abs=2)
    assert {k: v for k, v in rows.items() if k != "SKU003"} == others                  # only the named product changes

    speak(client, "लेज़ का सुरक्षा स्टॉक बढ़ाओ")                                        # no percentage: 20, replacing the 30
    with session() as s:
        rule = s.exec(select(Preference).where(Preference.directive == "safety_stock_pct", Preference.active)).one()
    assert rule.value == 20.0
    wait_for_runs(client)
    assert next(r["safetyStock"] for r in boot(client)["skuData"] if r["id"] == "SKU003") == pytest.approx(before * 1.2, abs=2)


def test_voice_rules_alone_do_nothing(client):
    """`rules` only reads; the adjustment is stored by `interpret`."""
    stored = count(Preference)
    assert voice.rules("increase safety stock for colgate")["type"] == "PROCURE"
    assert voice.rules("mumble") is None
    assert count(Preference) == stored


# ── the validator ────────────────────────────────────────────────────────────

def test_validator_nlq_check(client, monkeypatch):
    result = validate.check_nlq()
    assert result.status == validate.PASS, result.detail
    assert "model offline" in result.detail
    monkeypatch.setattr(validate, "NLQ_SENTENCES", (*validate.NLQ_SENTENCES, ("hello", "overstock")))
    broken = validate.check_nlq()
    assert broken.status == validate.FAIL and "'hello' routed to greeting" in broken.detail


def test_validator_reports_native_faults_instead_of_hiding_them(tmp_path):
    def tool(script: str):
        path = tmp_path / "tool.py"
        path.write_text(script, encoding="utf-8")
        return validate._run_tool("pytest", ["python", str(path)], tmp_path)

    clean = tool("print('===== 5 passed, 1 skipped in 2.00s =====')")
    assert (clean.status, clean.detail) == (validate.PASS, "5 passed, 1 skipped in 2.00s")
    noisy = tool("import sys\nprint('===== 5 passed in 2.00s =====')\n"
                 "print('Windows fatal exception: access violation', file=sys.stderr)\nprint('  File x.py, line 1 in keys', file=sys.stderr)")
    assert noisy.status == validate.WARN and noisy.detail.startswith("5 passed in 2.00s") and "1 native fault report" in noisy.detail
    failed = tool("import sys\nprint('===== 1 failed, 4 passed in 2.00s =====')\nsys.exit(1)")
    assert (failed.status, failed.detail) == (validate.FAIL, "1 failed, 4 passed in 2.00s")


def test_reply_json_is_plain(client):
    json.dumps(chat(client, "critical skus"), ensure_ascii=False)


def test_runs_cut_off_by_a_server_stop_are_closed_at_startup(client):
    from sarthi.orchestrator.runner import start_run

    stale = start_run("sync")                       # a run row that nothing will ever finish
    with TestClient(create_app()):                   # the next server start
        pass
    with session() as s:
        run = s.get(Run, stale)
    assert run.status == "interrupted" and run.finished_at is not None
