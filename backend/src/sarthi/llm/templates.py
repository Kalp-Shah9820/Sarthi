"""Deterministic wording for everything the system says.

Templates are the source of truth. The model may rephrase an English `msg`; `action` and `impact`
sit on buttons and totals and are always templates. Hindi always uses these templates (a 4B local
model keeps Hindi numbers right but phrases them unnaturally).
"""

import string

ALERT = {
    "stockout_reorder": {
        "EN": {"msg": "{doc} days of cover against a {lead}-day lead time. Stockout probability {prob}%.",
               "action": "Approve PO: {qty} units via {supplier}",
               "impact": "₹{par_k}K profit at risk"},
        "HI": {"msg": "{lead} दिन के लीड टाइम के मुकाबले केवल {doc} दिन का स्टॉक बचा है। स्टॉकआउट की संभावना {prob}% है।",
               "action": "PO स्वीकृत करें: {supplier} से {qty} यूनिट",
               "impact": "₹{par_k}K मुनाफ़ा जोखिम में"},
    },
    "supplier_switch": {
        "EN": {"msg": "{from_supplier} needs about {lead_old} days; {supplier} can deliver in about {lead_new}. "
                      "Stockout probability is {prob}%.",
               "action": "Switch to {supplier}: {qty} units",
               "impact": "₹{par_k}K profit at risk"},
        "HI": {"msg": "{from_supplier} को लगभग {lead_old} दिन लगते हैं; {supplier} लगभग {lead_new} दिन में डिलीवर कर सकता है। "
                      "स्टॉकआउट की संभावना {prob}% है।",
               "action": "{supplier} पर स्विच करें: {qty} यूनिट",
               "impact": "₹{par_k}K मुनाफ़ा जोखिम में"},
    },
    "transfer": {
        "EN": {"msg": "{from_site} holds far more days of this product than {to_site}. "
                      "Moving {units} units lets it sell sooner and cuts the cost of holding it.",
               "action": "Transfer {units} units: {from_site} → {to_site}",
               "impact": "₹{saving_k}K holding cost saved"},
        "HI": {"msg": "{from_site} के पास इस उत्पाद का स्टॉक {to_site} की तुलना में कहीं ज़्यादा दिनों का है। "
                      "{units} यूनिट भेजने से यह जल्दी बिकेगा और होल्डिंग लागत घटेगी।",
               "action": "{units} यूनिट ट्रांसफ़र करें: {from_site} → {to_site}",
               "impact": "₹{saving_k}K होल्डिंग लागत की बचत"},
    },
    "markdown": {
        "EN": {"msg": "{doc} days of cover, {excess} units above what will sell in a month. Holding it is costing money.",
               "action": "Launch {discount}% markdown",
               "impact": "₹{par_k}K carrying cost at risk"},
        "HI": {"msg": "{doc} दिन का स्टॉक है, एक महीने की बिक्री से {excess} यूनिट ज़्यादा। इसे रखना महँगा पड़ रहा है।",
               "action": "{discount}% छूट शुरू करें",
               "impact": "₹{par_k}K होल्डिंग लागत जोखिम में"},
    },
    "bundle": {
        "EN": {"msg": "{partner} buyers also take {sku} in {conf_pct}% of baskets. A bundle would move about "
                      "{extra} extra units this month.",
               "action": "Launch {discount}% bundle with {partner}",
               "impact": "₹{par_k}K carrying cost at risk"},
        "HI": {"msg": "{partner} ख़रीदने वाले {conf_pct}% बास्केट में {sku} भी लेते हैं। बंडल से इस महीने लगभग "
                      "{extra} अतिरिक्त यूनिट बिकेंगी।",
               "action": "{partner} के साथ {discount}% बंडल शुरू करें",
               "impact": "₹{par_k}K होल्डिंग लागत जोखिम में"},
    },
    "expiry_risk": {
        "EN": {"msg": "{expiring} units will pass their shelf life in about {days_left} days at the current sales rate.",
               "action": "Launch {discount}% flash sale",
               "impact": "₹{par_k}K write-off at risk"},
        "HI": {"msg": "मौजूदा बिक्री दर पर {expiring} यूनिट लगभग {days_left} दिन में एक्सपायर हो जाएँगी।",
               "action": "{discount}% फ़्लैश सेल शुरू करें",
               "impact": "₹{par_k}K राइट-ऑफ़ जोखिम में"},
    },
    "cannibalization": {
        "EN": {"msg": "{rising} sold {uplift_pct}% above normal on the {event_days} days {falling} was out of stock. "
                      "That demand is borrowed.",
               "action": "Cap {rising} order at normal demand",
               "impact": "₹{par_k}K over-order avoided"},
        "HI": {"msg": "जिन {event_days} दिनों में {falling} स्टॉक में नहीं था, {rising} की बिक्री सामान्य से {uplift_pct}% ज़्यादा रही। "
                      "यह माँग उधार की है।",
               "action": "{rising} का ऑर्डर सामान्य माँग तक सीमित रखें",
               "impact": "₹{par_k}K अतिरिक्त ऑर्डर से बचाव"},
    },
    "phantom_inventory": {
        "EN": {"msg": "The stock record and sales disagree on {days} of the last 14 days. The count is probably wrong.",
               "action": "Count stock in aisle {aisle}",
               "impact": "{est_units} units unverified"},
        "HI": {"msg": "पिछले 14 दिनों में से {days} दिन स्टॉक रिकॉर्ड और बिक्री मेल नहीं खाते। गिनती शायद ग़लत है।",
               "action": "आइल {aisle} में स्टॉक गिनें",
               "impact": "{est_units} यूनिट अपुष्ट"},
    },
}

EMAIL = {
    "opening": {
        "EN": {"subject": "Order request: {qty} units of {sku}",
               "body": "Dear {supplier} team,\n\nWe would like to order {qty} units of {sku} at ₹{offer} per unit, "
                       "for delivery within {eta_days} days. Your list price is ₹{list_price}.\n\n"
                       "Please confirm availability and price.\n\nRegards,\nSarthi Procurement"},
        "HI": {"subject": "ऑर्डर अनुरोध: {sku} की {qty} यूनिट",
               "body": "प्रिय {supplier} टीम,\n\nहम {sku} की {qty} यूनिट ₹{offer} प्रति यूनिट की दर से, {eta_days} दिन के भीतर "
                       "डिलीवरी के लिए ऑर्डर करना चाहते हैं। आपकी सूची कीमत ₹{list_price} है।\n\n"
                       "कृपया उपलब्धता और कीमत की पुष्टि करें।\n\nसादर,\nसारथी प्रोक्योरमेंट"},
    },
}

TASKS = {"alert": ALERT, "email": EMAIL}


def fields(task: str, kind: str, lang: str = "EN") -> set[str]:
    """Names of the facts a template needs."""
    entry = TASKS[task][kind]
    parts = entry.get(lang, entry["EN"])
    return {name for text in parts.values() for _, name, _, _ in string.Formatter().parse(text) if name}


def render(task: str, kind: str, lang: str = "EN", **facts) -> dict[str, str]:
    """Fill a template. Unknown languages fall back to English; a missing fact raises KeyError."""
    entry = TASKS[task][kind]
    parts = entry.get(lang, entry["EN"])
    return {name: text.format(**facts) for name, text in parts.items()}
