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
DRAFT_EMAIL = PREAMBLE + (
    "Write a short business email to the supplier named in the facts. First line: 'Subject: ...'. "
    "Then a body of at most 120 words stating the quantity, the offered unit price and the requested delivery time."
)
EXPLAIN_SKU = PREAMBLE + (
    "In at most 4 sentences, explain the product's situation and the recommendation to a store manager."
)
EXTRACT_INTENT = (
    "Classify the user's sentence about a retail inventory system into exactly one intent and extract its details. "
    "Reply with JSON only."
)
COMPILE_STRATEGY = (
    "The user is setting the inventory strategy. Choose the mode that matches their sentence and the number of days "
    "it should last. Reply with JSON only."
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
