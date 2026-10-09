"""System prompts. Facts are always passed in the user message; see `user_message`."""

import json

PREAMBLE = (
    "You are a component of Sarthi, an inventory planning system.\n"
    "Use ONLY the facts provided. Never state a number that is not in the facts.\n"
    "Write plainly. No greetings, no markdown, no emojis.\n"
)

# The model is never asked to explain raw facts: tested live on 2026-10-09, a 4B model kept the numbers right
# but garbled what they meant (a rupee value became "units", a recommendation was reversed). It is given a
# sentence that is already correct and asked only to reword it.
REPHRASE = (
    "Rewrite the text below so it reads naturally to a store manager. "
    "Keep every number, every name and the meaning exactly the same. "
    "Do not add reasons, advice, causes or any new fact. Do not swap which name does what. "
    "Reply with the rewritten text only, in at most 2 sentences."
)
REPHRASE_EXPLANATION = REPHRASE.replace("in at most 2 sentences", "in at most 4 sentences")
DRAFT_EMAIL = PREAMBLE + (
    "Write a short business email to the supplier named in the facts. First line: 'Subject: ...'. "
    "Then a body of at most 120 words stating the quantity, the offered unit price and the requested delivery time."
)
EXPLAIN_SKU = PREAMBLE + (
    "In at most 4 sentences, explain the product's situation and the recommendation to a store manager."
)
EXTRACT_INTENT = (
    "Classify the user's sentence about a retail inventory system into exactly one intent and extract its details. "
    "Reply with JSON only.\n"
    "Intents, each with an English and a Hindi example:\n"
    "- greeting: 'good morning' / 'नमस्ते'\n"
    "- strategy_set (fill mode: Cash Flow, Growth or Balanced; days if given): 'we need to conserve money this month' / 'पैसे बचाने पर ध्यान दो'\n"
    "- strategy_get: 'how are you set up to decide right now?' / 'अभी क्या नीति चल रही है?'\n"
    "- suppliers: 'who delivers fastest?' / 'कौन सा विक्रेता सबसे भरोसेमंद है?'\n"
    "- basket: 'what do people buy together?' / 'लोग साथ में क्या खरीदते हैं?'\n"
    "- bullwhip: 'are our orders amplifying demand swings?' / 'क्या ऑर्डर में उतार-चढ़ाव बढ़ रहा है?'\n"
    "- montecarlo: 'what do the risk simulations say?' / 'जोखिम के अनुमान क्या कहते हैं?'\n"
    "- sku_risk: 'what is about to cause trouble?' / 'किन चीज़ों पर खतरा है?'\n"
    "- sku_sweet: 'what is doing well?' / 'क्या अच्छा चल रहा है?'\n"
    "- sku_summary: 'give me an overview of the catalogue' / 'सभी सामान का सारांश दो'\n"
    "- stock_summary: 'how many units do we hold?' / 'हमारे पास कुल कितना माल है?'\n"
    "- zones: 'what do the four quadrants mean?' / 'चार श्रेणियों का क्या मतलब है?'\n"
    "- stockout_horizon (fill days): 'what will we be out of by next week?' / 'अगले हफ्ते तक क्या नहीं बचेगा?'\n"
    "- overstock: 'where is money sitting on shelves?' / 'किस सामान में पैसा फँसा है?'\n"
    "- explain_sku (fill sku with the product as the user said it): 'what is going on with the toothpaste?' / 'मक्खन का क्या हाल है?'\n"
    "- reorder (fill sku and quantity): 'get me 200 more packets of chips' / 'नमक की 100 यूनिट और चाहिए'\n"
    "- unknown: anything else, including questions that are not about inventory."
)
COMPILE_STRATEGY = (
    "The user is setting the inventory strategy. Choose the mode that matches their sentence and the number of days "
    "it should last (30 if they give none). Cash Flow = spend less, hold less stock. Growth = never run out, hold more stock. "
    "Balanced = the normal setting. Reply with JSON only."
)
EXTRACT_PREFERENCE = (
    "A store manager approved or dismissed an inventory recommendation and left feedback. "
    "Turn the feedback into one standing rule. Reply with JSON only."
)
CLASSIFY_NEWS = (
    "Decide whether the headline describes a risk to retail supply chains in India, and if so classify it. "
    "Reply with JSON only."
)

LANGUAGE_NAMES = {"EN": "English", "HI": "Hindi"}


def user_message(facts: dict, lang: str = "EN") -> str:
    """Facts as compact JSON, then the reply language."""
    return json.dumps(facts, ensure_ascii=False, separators=(",", ":"), default=str) + f"\nLanguage: {LANGUAGE_NAMES.get(lang, 'English')}"
