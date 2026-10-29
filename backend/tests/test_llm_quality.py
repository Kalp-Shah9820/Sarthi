"""Quality of the real local model. Skipped unless SARTHI_TEST_LLM=1 and LM Studio is serving the model.

These are measurements, not gates on the code: the system is built to work when the model is wrong or
absent. They answer "is this model good enough to be worth switching on?".
"""

import os

import pytest

from sarthi.config import get_settings
from sarthi.learning.memory import PreferenceOut, rule_based
from sarthi.llm import prompts
from sarthi.llm.gateway import LlmGateway
from sarthi.llm.grounding import grounded
from sarthi.nlq import router

pytestmark = [pytest.mark.llm,
              pytest.mark.skipif(os.environ.get("SARTHI_TEST_LLM") != "1", reason="set SARTHI_TEST_LLM=1 with LM Studio running")]

# Sentences the keyword rules cannot route (checked below), with the intent a person would mean.
PARAPHRASES = [
    ("where is money sitting on shelves?", "overstock"),
    ("who delivers fastest?", "suppliers"),
    ("what do people usually buy together?", "basket"),
    ("what will we be out of by next week?", "stockout_horizon"),
    ("what is about to cause us trouble?", "sku_risk"),
    ("we need to conserve money this quarter", "strategy_set"),
    ("how is the toothpaste doing?", "explain_sku"),
    ("get me 200 more packets of chips", "reorder"),
    ("किस सामान में पैसा फँसा है", "overstock"),
    ("मक्खन का क्या हाल है", "explain_sku"),
]
FEEDBACK = [     # feedback the keyword patterns do not catch, and the rule a person would read into it
    ("Metro has let us down too often, we are done with them", "avoid_supplier"),
    ("we cannot shelve more than 300 of these", "cap_qty"),
    ("I need to sign off on these personally every time", "no_auto"),
    ("bring them in from Reliance whenever you can", "prefer_supplier"),
    ("fine this time", "other"),
    ("thanks, good call", "other"),
]
SENTENCES = [
    ("Lays Classic 26g has 1.8 days of cover against a lead time of 18 days. Stockout probability is 100%.",
     {"sku": "Lays Classic 26g", "doc": 1.8, "lead": 18, "prob": 100}),
    ("Surf Excel 1kg holds 820 units, 105 days of cover. Moving 96 units to Chennai Port saves ₹1.3K in holding cost.",
     {"sku": "Surf Excel 1kg", "on_hand": 820, "doc": 105, "units": 96, "to_site": "Chennai Port", "saving_k": 1.3}),
    ("Order 456 units of Colgate Strong 200g from Reliance Metro WH at ₹86.5, arriving in 2 days.",
     {"sku": "Colgate Strong 200g", "qty": 456, "supplier": "Reliance Metro WH", "unit_price": 86.5, "eta_days": 2}),
]


@pytest.fixture
async def model(seeded_db, monkeypatch):
    monkeypatch.setenv("SARTHI_LLM_ENABLED", "true")
    get_settings.cache_clear()
    gateway = LlmGateway(get_settings())
    assert await gateway.probe() == "llm", "LM Studio is not serving the configured model"
    yield gateway
    get_settings.cache_clear()


async def test_model_routes_paraphrased_questions(model):
    routed = [q for q, _ in PARAPHRASES if router.route_rules(q) is not None]
    assert not routed, f"routed by keyword rules, so they test nothing: {routed}"
    got = [(q, want, (await router.classify(q, model))[0].name) for q, want in PARAPHRASES]
    right = sum(want == have for _, want, have in got)
    print(f"\nintent extraction: {right}/{len(got)}", [(q, have) for q, want, have in got if want != have])
    assert right >= 8, got


async def test_model_rewording_stays_grounded(model):
    kept = 0
    for sentence, facts in SENTENCES:
        names = [v for v in facts.values() if isinstance(v, str)]
        text, source = await model.text(prompts.REPHRASE, sentence, facts=facts, fallback=sentence, must_contain=names,
                                        source=sentence, max_chars=round(len(sentence) * 1.6) + 20, max_tokens=140)
        assert grounded(text, facts), text                  # true by construction: an ungrounded rewrite is discarded
        assert all(name in text for name in names)
        kept += source == "llm"
    print(f"\nrewordings accepted by the guards: {kept}/{len(SENTENCES)}")
    assert kept >= 1, "the model never produced a rewrite that passed the guards"


async def test_model_turns_feedback_into_valid_rules(model):
    right = 0
    for text, expected in FEEDBACK:
        assert rule_based(text).directive == "other"        # the patterns do not catch it: this is the model's job
        parsed = await model.json(PreferenceOut, prompts.EXTRACT_PREFERENCE, "Decision: dismissed. Feedback: " + text,
                                  fallback=lambda: None, task="preference")
        assert isinstance(parsed, PreferenceOut), f"no valid rule for: {text}"
        right += parsed.directive == expected
        mark = "ok " if parsed.directive == expected else "BAD"
        print(f"\n  {mark} {text!r} -> {parsed.directive} {parsed.scope} {parsed.target!r} {parsed.value}")
    print(f"\nfeedback read correctly: {right}/{len(FEEDBACK)}")
    assert right >= 4
