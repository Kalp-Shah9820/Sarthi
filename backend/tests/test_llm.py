import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlmodel import select

from sarthi import validate
from sarthi.api.main import create_app
from sarthi.blackboard.store import Blackboard, delete_run_events
from sarthi.config import Settings, get_settings
from sarthi.db import session
from sarthi.llm import get_llm, prompts, reset_llm, templates
from sarthi.llm.gateway import LlmGateway, cache_get
from sarthi.models import LlmCache

RUN = 9100
FACTS = {"name": "Lays Classic 26g", "prob": 97, "qty": 1488}
GOOD = "Lays Classic 26g has a 97% stockout risk; order 1,488 units."


class Intent(BaseModel):
    name: str
    days: int = 7


def reply(content):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class FakeCompletions:
    """Stands in for the OpenAI client: returns queued replies and records what it was asked."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return reply(item)


@pytest.fixture
def gateway(db):
    """A gateway that believes the model is up, with the network replaced by a fake."""
    with session() as s:
        for row in s.exec(select(LlmCache)).all():
            s.delete(row)
        s.commit()
    delete_run_events(RUN)
    gw = LlmGateway(Settings(_env_file=None, llm_enabled=True, llm_max_calls_per_run=3))
    gw.mode = "llm"

    def use(*replies):
        fake = FakeCompletions(*replies)
        gw.client = SimpleNamespace(chat=SimpleNamespace(completions=fake))
        return fake

    gw.use = use
    yield gw
    delete_run_events(RUN)


def llm_events():
    return [(e.key, e.value["result"]) for e in Blackboard(RUN).events(kind="llm")]


# ── Offline behaviour ────────────────────────────────────────────────────────

async def test_disabled_model_uses_fallbacks_without_any_call(db):
    gw = LlmGateway(Settings(_env_file=None, llm_enabled=False))
    fake = FakeCompletions()
    gw.client = SimpleNamespace(chat=SimpleNamespace(completions=fake))
    assert await gw.probe() == "offline"
    assert await gw.json(Intent, "sys", "user", fallback=lambda: Intent(name="unknown")) == Intent(name="unknown")
    assert await gw.text("sys", "user", facts=FACTS, fallback="template text") == ("template text", "template")
    assert fake.requests == []


async def test_probe_reports_offline_when_nothing_listens(db):
    gw = LlmGateway(Settings(_env_file=None, llm_enabled=True, llm_base_url="http://127.0.0.1:9/v1"))
    assert await gw.probe() == "offline"


# ── Structured output ────────────────────────────────────────────────────────

async def test_json_returns_the_parsed_model_and_sends_the_schema(gateway):
    fake = gateway.use('{"name": "sku_risk", "days": 14}')
    result = await gateway.json(Intent, "sys", "which skus are at risk", fallback=lambda: Intent(name="unknown"), run_id=RUN, task="intent")
    assert result == Intent(name="sku_risk", days=14)
    request = fake.requests[0]
    assert request["response_format"]["type"] == "json_schema"
    assert request["response_format"]["json_schema"]["name"] == "Intent"
    assert request["messages"][0] == {"role": "system", "content": "sys"}
    assert request["max_tokens"] == 400 and request["temperature"] == 0.2
    assert llm_events() == [("intent", "ok")]


async def test_json_repairs_one_invalid_reply(gateway):
    fake = gateway.use("not json at all", '{"name": "zones"}')
    result = await gateway.json(Intent, "sys", "user", fallback=lambda: Intent(name="unknown"))
    assert result == Intent(name="zones")
    assert "previous reply was invalid" in fake.requests[1]["messages"][1]["content"]


async def test_json_falls_back_after_two_invalid_replies_or_an_error(gateway):
    gateway.use("nope", '{"wrong": 1}')
    assert (await gateway.json(Intent, "sys", "a", fallback=lambda: Intent(name="unknown"), run_id=RUN, task="intent")).name == "unknown"
    gateway.use(TimeoutError("model too slow"))
    assert (await gateway.json(Intent, "sys", "b", fallback=lambda: Intent(name="unknown"))).name == "unknown"
    assert llm_events() == [("intent", "fallback")]


# ── Grounded text ────────────────────────────────────────────────────────────

async def test_text_accepts_a_grounded_sentence(gateway):
    gateway.use(GOOD)
    assert await gateway.text("sys", "user", facts=FACTS, fallback="template", run_id=RUN, task="alert") == (GOOD, "llm")
    assert llm_events() == [("alert", "ok")]


async def test_text_rejects_an_invented_number_and_logs_it(gateway):
    gateway.use("Lays Classic 26g has a 97% stockout risk; order 2,000 units.")
    assert await gateway.text("sys", "user", facts=FACTS, fallback="template", run_id=RUN, task="alert") == ("template", "template")
    assert llm_events() == [("alert", "ungrounded")]


async def test_text_rejects_a_rewrite_that_drops_a_name_or_balloons(gateway):
    gateway.use("The snack has a 97% stockout risk; order 1,488 units.", GOOD + " " + "It is very important. " * 10)
    kept_name = await gateway.text("sys", "a", facts=FACTS, fallback="template", run_id=RUN, task="alert", must_contain=["Lays Classic 26g"])
    too_long = await gateway.text("sys", "b", facts=FACTS, fallback="template", run_id=RUN, task="alert", max_chars=120)
    assert kept_name == too_long == ("template", "template")
    assert llm_events() == [("alert", "off_template"), ("alert", "off_template")]
    gateway.use(GOOD)
    assert (await gateway.text("sys", "c", facts=FACTS, fallback="template", must_contain=["lays classic 26g"], max_chars=120))[1] == "llm"


async def test_text_rejects_a_rewrite_that_changes_how_often_a_name_appears(gateway):
    """Seen live: 'cap its next order' reworded so that the action applied to the other product."""
    facts = {"rising": "Haldirams Namkeen 400g", "falling": "Lays Classic 26g", "uplift_pct": 136}
    source = "Haldirams Namkeen 400g sold 136% above normal only while Lays Classic 26g was out of stock; cap the next order of Haldirams Namkeen 400g."
    swapped = "Haldirams Namkeen 400g sold 136% above normal while Lays Classic 26g was out of stock; cap the next order for Lays Classic 26g."
    faithful = "While Lays Classic 26g was out of stock, Haldirams Namkeen 400g sold 136% above normal, so the next order of Haldirams Namkeen 400g should be capped."
    names = [facts["rising"], facts["falling"]]
    gateway.use(swapped, faithful)
    assert await gateway.text("sys", "a", facts=facts, fallback=source, must_contain=names, source=source, run_id=RUN, task="debate") == (source, "template")
    assert await gateway.text("sys", "b", facts=facts, fallback=source, must_contain=names, source=source) == (faithful, "llm")
    assert llm_events() == [("debate", "off_template")]


async def test_empty_and_reasoning_only_replies_are_failures_and_never_cached(gateway):
    fake = gateway.use("", "<think>let me think about this for a long time</think>", None)
    for _ in range(3):
        assert await gateway.text("sys", "user", facts=FACTS, fallback="template") == ("template", "template")
    assert len(fake.requests) == 3          # nothing was cached, so each attempt reached the model
    with session() as s:
        assert s.exec(select(LlmCache)).all() == []


async def test_thinking_block_is_stripped_from_a_real_answer(gateway):
    gateway.use(f"<think>the user wants a short sentence</think>\n{GOOD}")
    assert await gateway.text("sys", "user", facts=FACTS, fallback="template") == (GOOD, "llm")


async def test_reasoning_headroom_is_added_to_the_token_limit(db):
    gw = LlmGateway(Settings(_env_file=None, llm_enabled=True, llm_reasoning_headroom=2000))
    gw.mode = "llm"
    fake = FakeCompletions(GOOD)
    gw.client = SimpleNamespace(chat=SimpleNamespace(completions=fake))
    await gw.text("sys", "headroom check", facts=FACTS, fallback="template", max_tokens=220)
    assert fake.requests[0]["max_tokens"] == 2220


# ── Cache and budget ─────────────────────────────────────────────────────────

async def test_identical_prompt_is_served_from_the_cache_even_offline(gateway):
    fake = gateway.use(GOOD)
    first = await gateway.text("sys", "same prompt", facts=FACTS, fallback="template")
    second = await gateway.text("sys", "same prompt", facts=FACTS, fallback="template")
    assert first == second == (GOOD, "llm")
    assert len(fake.requests) == 1
    gateway.mode = "offline"                 # LM Studio closed: the demo still shows the cached wording
    assert await gateway.text("sys", "same prompt", facts=FACTS, fallback="template") == (GOOD, "llm")
    assert await gateway.text("sys", "new prompt", facts=FACTS, fallback="template") == ("template", "template")


async def test_cache_is_per_model(gateway):
    gateway.use(GOOD)
    await gateway.text("sys", "prompt", facts=FACTS, fallback="template")
    other = LlmGateway(Settings(_env_file=None, llm_enabled=True, llm_model="some/other-model"))
    assert await other.text("sys", "prompt", facts=FACTS, fallback="template") == ("template", "template")


async def test_run_budget_limits_uncached_calls_but_not_chat(gateway):
    fake = gateway.use(*[GOOD] * 6)
    sources = [(await gateway.text("sys", f"prompt {i}", facts=FACTS, fallback="template", run_id=RUN))[1] for i in range(5)]
    assert sources == ["llm", "llm", "llm", "template", "template"]      # budget of 3 for this run
    assert gateway.calls(RUN) == 3 and len(fake.requests) == 3
    assert (await gateway.text("sys", "prompt 0", facts=FACTS, fallback="template", run_id=RUN))[1] == "llm"  # cached: free
    assert (await gateway.text("sys", "chat question", facts=FACTS, fallback="template"))[1] == "llm"         # no run: no budget
    assert gateway.calls(RUN) == 3


async def test_raw_reply_bypasses_the_cache(gateway):
    fake = gateway.use("first", "second")
    assert await gateway.raw_reply("sys", "diag") == "first"
    assert await gateway.raw_reply("sys", "diag") == "second"
    assert len(fake.requests) == 2 and cache_get("anything") is None


# ── Singleton, server, prompts, templates ────────────────────────────────────

def test_get_llm_is_a_singleton_until_reset(db):
    first = get_llm()
    assert get_llm() is first
    reset_llm()
    assert get_llm() is not first


def test_health_reports_the_model_mode(db):
    with TestClient(create_app()) as client:
        assert client.get("/api/health").json() == {"status": "ok", "llm": "offline"}


def test_user_message_carries_facts_and_language():
    message = prompts.user_message({"name": "नमक", "qty": 5}, "HI")
    assert message == '{"name":"नमक","qty":5}\nLanguage: Hindi'
    assert prompts.user_message({}, "TA").endswith("Language: English")
    for prompt in (prompts.DRAFT_EMAIL, prompts.EXPLAIN_SKU):
        assert prompt.startswith(prompts.PREAMBLE)
    assert "meaning exactly the same" in prompts.REPHRASE


@pytest.mark.parametrize("kind", list(templates.ALERT))
def test_alert_templates_are_complete_in_both_languages(kind):
    assert templates.fields("alert", kind, "EN") == templates.fields("alert", kind, "HI")
    facts = {name: 7 for name in templates.fields("alert", kind)}
    for lang in ("EN", "HI"):
        rendered = templates.render("alert", kind, lang, **facts)
        assert set(rendered) == {"msg", "action", "impact"}
        assert all(text and "{" not in text for text in rendered.values())
    assert templates.render("alert", kind, "TA", **facts) == templates.render("alert", kind, "EN", **facts)


def test_template_wording_and_missing_facts():
    rendered = templates.render("alert", "stockout_reorder", "EN", doc=1.6, lead=12, prob=97, qty=1488,
                                supplier="Reliance Metro WH", par_k=18.5)
    assert rendered == {
        "msg": "1.6 days of cover against a 12-day lead time. Stockout probability 97%.",
        "action": "Approve PO: 1488 units via Reliance Metro WH",
        "impact": "₹18.5K profit at risk",
    }
    with pytest.raises(KeyError):
        templates.render("alert", "stockout_reorder", "EN", doc=1.6)
    email = templates.render("email", "opening", "EN", supplier="HUL", sku="Maggi 70g", qty=600, offer=7.4, eta_days=3, list_price=7.7)
    assert email["subject"] == "Order request: 600 units of Maggi 70g" and "₹7.4 per unit" in email["body"]


def test_template_sentences_pass_their_own_grounding_check():
    """A template filled with facts must itself be 'grounded', or the check and the templates disagree."""
    from sarthi.llm.grounding import grounded

    facts = {"doc": 1.6, "lead": 12, "prob": 97, "qty": 1488, "supplier": "Reliance Metro WH", "par_k": 18.5}
    assert all(grounded(text, facts) for text in templates.render("alert", "stockout_reorder", "EN", **facts).values())


def test_validator_agent_core_check(db):
    result = validate.check_agent_core()
    assert result.status in (validate.PASS, validate.WARN), result.detail   # WARN = model offline, as in tests
    assert "blackboard, templates and grounding ok" in result.detail


# ── The real model (skipped unless SARTHI_TEST_LLM=1) ────────────────────────

live = pytest.mark.skipif(os.environ.get("SARTHI_TEST_LLM") != "1", reason="set SARTHI_TEST_LLM=1 with LM Studio running")


@pytest.fixture
def live_gateway(db, monkeypatch):
    monkeypatch.setenv("SARTHI_LLM_ENABLED", "true")
    get_settings.cache_clear()
    yield LlmGateway(get_settings())
    get_settings.cache_clear()


@live
@pytest.mark.llm
async def test_live_model_extracts_json_and_writes_grounded_text(live_gateway):
    assert await live_gateway.probe() == "llm", "LM Studio is not serving the configured model"

    class Sentiment(BaseModel):
        positive: bool

    parsed = await live_gateway.json(Sentiment, "Decide whether the sentence is positive. Reply with JSON only.",
                                     "Deliveries arrived early and complete.", fallback=lambda: Sentiment(positive=False))
    assert parsed.positive is True

    facts = {"doc": 1.6, "lead": 12, "prob": 97, "qty": 1488, "supplier": "Reliance Metro WH", "par_k": 18.5}
    sentence = templates.render("alert", "stockout_reorder", "EN", **facts)["msg"]
    text, source = await live_gateway.text(prompts.REPHRASE, sentence, facts=facts, fallback="TEMPLATE", max_chars=200)
    assert text != "" and source in ("llm", "template")       # either is acceptable; a wrong number never is
    if source == "llm":
        from sarthi.llm.grounding import grounded

        assert grounded(text, facts)
