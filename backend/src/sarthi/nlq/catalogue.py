"""Finding the product a sentence is about.

Matching is on whole words, not substrings: "oil" must not match "boil", and "नमक" (salt) must not
match "नमकीन" (namkeen). Longer and more specific names win over brand words, brand words over
generic nouns, and those over a category's flagship product.
"""

import re

from rapidfuzz import fuzz
from sqlmodel import select

from sarthi.db import session
from sarthi.models import Sku

SPLIT = re.compile(r"[\s,.;:!?()\"'/]+")        # not \w: Devanagari vowel signs are not word characters
SIZE = re.compile(r"^\d+(\.\d+)?(g|kg|ml|l)$")
SKU_ID = re.compile(r"\bsku[\s-]?(\d{1,4})\b", re.IGNORECASE)
FUZZY_MIN_LEN, FUZZY_SCORE = 5, 85              # spoken or mistyped brand names: "colgat", "haldiram"

# Everyday words for the demo products, English and Hindi -> a fragment of the product name.
# An entry is ignored when no product in the catalogue contains its fragment.
NOUNS = {
    "butter": "butter", "मक्खन": "butter", "अमूल": "amul",
    "detergent": "surf", "डिटर्जेंट": "surf", "सर्फ": "surf", "excel": "surf excel",
    "chips": "lays", "चिप्स": "lays", "लेज़": "lays", "लेज": "lays",
    "salt": "salt", "नमक": "salt", "टाटा": "tata",
    "noodles": "maggi", "नूडल्स": "maggi", "मैगी": "maggi",
    "toothpaste": "colgate", "टूथपेस्ट": "colgate", "कोलगेट": "colgate",
    "oil": "oil", "तेल": "oil", "फॉर्च्यून": "fortune",
    "biscuit": "parle", "biscuits": "parle", "बिस्कुट": "parle", "parle": "parle", "पारले": "parle",
    "handwash": "dettol", "डेटॉल": "dettol",
    "namkeen": "namkeen", "नमकीन": "namkeen", "haldiram": "haldiram", "हल्दीराम": "haldiram",
}
TIERS = ("name", "short", "brand", "noun", "category")      # most specific first


def tokens(text: str) -> list[str]:
    return [t for t in SPLIT.split(text.lower()) if t]


def aliases() -> dict[str, tuple[str, int]]:
    """Every way a product may be referred to: phrase -> (SKU id, tier index)."""
    with session() as s:
        skus = s.exec(select(Sku).order_by(Sku.id)).all()
    found: dict[str, tuple[str, int]] = {}

    def add(phrase: str, sku_id: str, tier: str) -> None:
        if phrase:
            found.setdefault(phrase, (sku_id, TIERS.index(tier)))

    for sku in skus:
        words = tokens(sku.name)
        short = [w for w in words if not SIZE.match(w)]
        add(" ".join(words), sku.id, "name")
        add(" ".join(short), sku.id, "short")
        add(short[0] if short else "", sku.id, "brand")
    for word, fragment in NOUNS.items():
        match = next((k for k in skus if fragment in k.name.lower()), None)
        if match:
            add(word, match.id, "noun")
    for sku in skus:                                    # a category means its first product
        add(" ".join(tokens(sku.category)), sku.id, "category")
    return found


def resolve_sku(text: str) -> str | None:
    """The SKU id a sentence refers to, or None."""
    if not text or not text.strip():
        return None
    table = aliases()
    ids = {sku_id for sku_id, _ in table.values()}
    if (m := SKU_ID.search(text)) and (wanted := f"SKU{int(m.group(1)):03d}") in ids:
        return wanted

    words = tokens(text)
    padded = f" {' '.join(words)} "
    best: tuple[int, int, str] | None = None            # (tier, position, sku id); smallest wins
    for phrase, (sku_id, tier) in table.items():
        at = padded.find(f" {phrase} ")
        if at >= 0 and (best is None or (tier, at) < best[:2]):
            best = (tier, at, sku_id)
    if best:
        return best[2]

    single = {p: v for p, v in table.items() if " " not in p and len(p) >= FUZZY_MIN_LEN and v[1] <= TIERS.index("noun")}
    scored = [(fuzz.ratio(word, phrase), -tier, sku_id) for word in words if len(word) >= FUZZY_MIN_LEN
              for phrase, (sku_id, tier) in single.items()]
    score, _, sku_id = max(scored, default=(0, 0, None))
    return sku_id if score >= FUZZY_SCORE else None
