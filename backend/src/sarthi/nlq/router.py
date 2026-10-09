"""From a sentence to an answer: keyword rules first, the model only when they find nothing.

The legacy part of `route_rules` follows the keyword order of `ask()` in `src/pages/Intelligence.jsx`
(including its Hindi keywords) so questions that worked against the built-in data route the same way.
"""

import re

from sarthi.llm import get_llm, prompts
from sarthi.nlq import handlers
from sarthi.nlq.catalogue import resolve_sku, tokens
from sarthi.nlq.intents import NEEDS_SKU, Intent
from sarthi.nlq.strategy import mode_from_words

GREETINGS = {"hi", "hello", "hey", "namaste", "नमस्ते", "नमस्कार"}     # whole words: "which" is not a greeting
STANDALONE_NUMBER = re.compile(r"(?<![\w.])(\d{1,6})(?![\w.%])")
DAYS = re.compile(r"(\d+)\s*(day|days|दिन)")
WEEKS = re.compile(r"(\d+)\s*(week|weeks|हफ्ते|सप्ताह)")
MAX_TEXT = 500


def _has(q: str, *words: str) -> bool:
    return any(w in q for w in words)


def _starts(q: str, words: set[str], *keys: str) -> bool:
    """True when a word of the sentence starts with a key ("suppliers" for "supplier"); phrases match anywhere.

    The built-in chat matched substrings, so "capital" hit "pit" and "which" hit "hi".
    """
    return any(key in q if " " in key else any(w.startswith(key) for w in words) for key in keys)


def _days(q: str) -> int:
    if m := DAYS.search(q):
        return max(1, min(90, int(m.group(1))))
    if m := WEEKS.search(q):
        return max(1, min(90, 7 * int(m.group(1))))
    return 14 if _has(q, "fortnight") else 7


def route_rules(text: str) -> Intent | None:
    """The intent, when keywords settle it. None means "ask the model"."""
    q = text.lower().strip()
    if not q:
        return Intent(name="unknown")
    words = set(tokens(q))
    sku = resolve_sku(q)
    numbers = [int(n) for n in STANDALONE_NUMBER.findall(q)]

    # ── questions the built-in chat could not answer ─────────────────────────
    if sku and _has(q, "why", "explain", "क्यों", "what is going on", "what's going on", "what is happening", "समझाओ", "समझाएं"):
        return Intent(name="explain_sku", sku=sku)
    if sku and (_has(q, "reorder", "रीऑर्डर") or (numbers and (words & {"order", "buy", "ऑर्डर", "मंगाओ", "मंगाएं", "खरीदो"}))):
        return Intent(name="reorder", sku=sku, quantity=numbers[0] if numbers else 0)
    if _has(q, "stock out", "stockout", "stock-out", "run out", "running out", "runs out", "स्टॉकआउट", "खत्म"):
        return Intent(name="stockout_horizon", days=_days(q))
    if _has(q, "overstock", "over stock", "excess", "surplus", "ओवरस्टॉक", "अतिरिक्त"):
        return Intent(name="overstock")

    # ── the built-in chat's branches, in its order ───────────────────────────
    if words & GREETINGS:
        return Intent(name="greeting")
    if _starts(q, words, "prioritize", "prioritise", "strategy", "प्राथमिकता", "रणनीति") or words & {"mode", "modes"}:     # not "model"
        mode = mode_from_words(q)
        return Intent(name="strategy_set", mode=mode) if mode else Intent(name="strategy_get")
    if _starts(q, words, "supplier", "distributor", "आपूर्तिकर्ता", "वितरक", "सप्लायर"):
        return Intent(name="suppliers")
    if _starts(q, words, "basket", "mba", "co-purchase", "बास्केट", "सह-खरीद"):
        return Intent(name="basket")
    if _starts(q, words, "bullwhip", "smooth", "बुलव्हिप"):
        return Intent(name="bullwhip")
    if _starts(q, words, "monte carlo", "simulation", "सिमुलेशन", "मोंटे कार्लो"):
        return Intent(name="montecarlo")
    if _starts(q, words, "sku", "product", "item", "उत्पाद"):
        if _starts(q, words, "risk", "high", "critical", "जोखिम", "गंभीर"):
            return Intent(name="sku_risk")
        if _starts(q, words, "best", "sweet", "अच्छा"):
            return Intent(name="sku_sweet")
        return Intent(name="sku_summary")
    if _starts(q, words, "stock", "inventory", "स्टॉक", "इन्वेंट्री"):
        return Intent(name="stock_summary")
    if _starts(q, words, "zone", "spot", "pit", "chaos", "ghost", "जोन", "ज़ोन"):
        return Intent(name="zones")
    return None


async def classify(text: str, llm=None) -> tuple[Intent, str]:
    """(intent, 'rules' | 'llm' | 'none'). The intent's `sku` is a real SKU id or empty."""
    text = text[:MAX_TEXT]
    intent = route_rules(text)
    if intent is not None:
        return intent, "rules"
    llm = llm or get_llm()
    guess = await llm.json(Intent, prompts.EXTRACT_INTENT, text, fallback=lambda: None, task="intent", max_tokens=120)
    if guess is None:
        return Intent(name="unknown"), "none"
    guess.sku = resolve_sku(guess.sku) or resolve_sku(text) or ""       # the model names a product; code decides which one
    if guess.name in NEEDS_SKU and not guess.sku:
        return Intent(name="unknown"), "llm"
    return guess, "llm"


async def answer(text: str, lang: str = "EN", llm=None) -> dict:
    """The chat reply: {key, params, text, strategy, refresh, navigate, intent, source}."""
    intent, source = await classify(text, llm)
    reply = await handlers.handle(intent, text[:MAX_TEXT], lang, llm or get_llm())
    return {**reply, "intent": intent.name, "source": source}
