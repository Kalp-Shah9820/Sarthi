"""Voice commands: the same triggers the frontend component uses, evaluated here against the real catalogue.

The reply is exactly the command object `handleVoiceCommand` in `App.jsx` already understands.
"""

from sarthi.llm import get_llm
from sarthi.nlq import router, strategy
from sarthi.nlq.catalogue import resolve_sku

# The frontend's own trigger words (i18n keys increase, safetyStock, show, zone, risk, switchTo, warRoom, …), EN + HI.
INCREASE = ("increase", "raise", "बढ़ाओ", "बढ़ाएं", "बढ़ा")
SAFETY_STOCK = ("safety stock", "सुरक्षा स्टॉक", "सेफ्टी स्टॉक")
SHOW = ("show", "open", "दिखाओ", "दिखाएं")
ZONE_OR_INVENTORY = ("zone", "inventory", "क्षेत्र", "जोन", "ज़ोन", "इन्वेंट्री")
RISK = ("risk", "जोखिम")
SWITCH_TO = ("switch to", "change to", "बदलो", "बदलें")
SANDBOX = ("war room", "scenario", "disruption", "what if", "what-if", "sandbox", "वॉर रूम", "परिदृश्य", "व्यवधान")
LANGUAGES = {       # code -> names it may be called by
    "EN": ("english", "अंग्रेजी", "अंग्रेज़ी", "इंग्लिश"), "HI": ("hindi", "हिंदी", "हिन्दी"),
    "TA": ("tamil", "தமிழ்", "तमिल"), "BN": ("bengali", "bangla", "বাংলা", "बंगाली"),
    "TE": ("telugu", "తెలుగు", "तेलुगु"), "MR": ("marathi", "मराठी"),
    "GJ": ("gujarati", "ગુજરાતી", "गुजराती"), "KN": ("kannada", "ಕನ್ನಡ", "कन्नड़"),
}
PAGES = (           # first match wins; paths are the frontend's routes
    ("datahub", ("data hub", "datahub", "डेटा हब")), ("command", ("command", "कमांड")),
    ("alerts", ("alert", "अलर्ट")), ("replenish", ("replenish", "पुनःपूर्ति")),
    ("dashboard", ("dashboard", "monitor", "डैशबोर्ड")), ("inventory", ("inventory", "इन्वेंट्री")),
    ("store", ("store", "स्टोर")), ("chat", ("intelligence", "chat", "चैट")),
)


def _has(q: str, words: tuple[str, ...]) -> bool:
    return any(w in q for w in words)


def command(kind: str, text: str, *, sku_id: str | None = None, path: str | None = None, lang: str | None = None) -> dict:
    return {"type": kind, "skuId": sku_id, "path": path, "lang": lang, "text": text}


def rules(text: str) -> dict | None:
    """The command, when trigger words settle it. PROCURE is returned without its side effect (see `interpret`)."""
    q = text.lower().strip()
    sku_id = resolve_sku(q)
    if _has(q, INCREASE) and _has(q, SAFETY_STOCK) and sku_id:
        return command("PROCURE", text, sku_id=sku_id)
    if _has(q, SHOW) and _has(q, ZONE_OR_INVENTORY) and sku_id:
        return command("NAVIGATE_SKU", text, sku_id=sku_id)
    if _has(q, RISK) and sku_id:
        return command("SKU_DETAIL", text, sku_id=sku_id)
    if _has(q, SWITCH_TO):
        code = next((code for code, names in LANGUAGES.items() if _has(q, names)), None)
        if code:
            return command("SET_LANG", text, lang=code)
    if _has(q, SANDBOX):
        return command("NAVIGATE", text, path="sandbox")
    for path, names in PAGES:
        if _has(q, names):
            return command("NAVIGATE", text, path=path)
    return None


async def interpret(text: str, lang: str = "EN", llm=None) -> dict:
    """Understand one spoken sentence. "Increase safety stock" is stored and takes effect in a new run."""
    text = text[:router.MAX_TEXT]
    found = rules(text)
    if found is None:
        intent, _ = await router.classify(text, llm or get_llm())
        if intent.name in ("explain_sku", "sku_risk") and intent.sku:
            return command("SKU_DETAIL", text, sku_id=intent.sku)
        return command("UNKNOWN", text)
    if found["type"] == "PROCURE":
        from sarthi.api import presenters
        from sarthi.api.routers import runs

        strategy.adjust_safety_stock(found["skuId"], strategy.percent_from_words(text), text)
        presenters.bump()
        runs.launch("voice", queue=True)
    return found
