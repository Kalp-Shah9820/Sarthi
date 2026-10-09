"""Wording for the agent debate.

The debate itself is a protocol with typed messages (propose, object, revise, rule), decided by code.
This module only turns one message into one sentence. Every sentence comes from a template, so it is
correct by construction; the model may reword it but never writes it from scratch.
"""

LINES = {
    "EN": {
        # proposals, keyed by alert type
        "propose:stockout_reorder": "{sku}: order {qty} units from {supplier} at ₹{unit_price}, shipped by {mode}, arriving in {eta}.",
        "propose:supplier_switch": "{sku}: switch from {from_supplier} to {supplier} and order {qty} units at ₹{unit_price}, shipped by {mode}, arriving in {eta}.",
        "propose:transfer": "{sku}: move {units} units from {from_site} to {to_site}, saving ₹{saving_k}K in holding cost.",
        "propose:markdown": "{sku}: mark down {discount}% to clear {excess} excess units.",
        "propose:bundle": "{sku}: bundle with {partner} at {discount}% off; they share {conf_pct}% of baskets.",
        "propose:expiry_risk": "{sku}: {expiring} units will expire in {days_left} days; run a {discount}% flash sale.",
        "propose:cannibalization": "{rising} sold {uplift_pct}% above normal only while {falling} was out of stock; cap the next order of {rising}.",
        "propose:phantom_inventory": "{sku}: stock records and sales disagree on {days} days; count the stock in aisle {aisle}.",
        # objections, keyed by rule
        "object:BORROWED_DEMAND": "Part of the demand for {sku} is borrowed from {falling}, which is out of stock. Order {suggested_qty} units, not {qty}.",
        "object:FORECAST_UNCERTAIN": "Demand for {sku} is hard to predict: the forecast has been off by {wape_pct}%. Order {suggested_qty} units, not {qty}.",
        "object:PHANTOM_STOCK": "The stock record for {sku} is unreliable; a person should check it before this goes ahead.",
        "object:SUPPLIER_ESG_FLOOR": "Supplier {supplier_id} scores {esg_score} on ESG, below the floor of {esg_floor}.",
        "object:USER_PREFERENCE": "This goes against a standing instruction from the manager.",
        "object:MOQ": "{qty} units is below the minimum order of {moq}.",
        "object:SHELF_LIFE_CAP": "{qty} units is more than half a shelf life of demand; the cap is {shelf_cap}.",
        "object:BUDGET_CAP": "The order is worth ₹{order_value} but only ₹{budget_available} of budget is left.",
        "object:AIR_SHARE": "Air freight is not justified here; it would take air's share of orders to {air_share_pct}% against a limit of {max_air_share_pct}%.",
        "object:AIR_UNNEEDED": "Air freight is not needed here: the slower mode leaves a {multimodal_risk_pct}% stockout risk against {air_risk_pct}% by air.",
        "object:AUTO_LIMIT": "The order is worth ₹{order_value}, above the ₹{auto_limit} limit for unattended orders; a person must approve it.",
        "object:CAPACITY": "{units} units will not fit; the destination has room for {free_capacity}.",
        "object:FASTER_MODE": "Stockout risk stays at {risk_pct}% until this arrives. Shipping by {faster_mode} cuts it to {faster_risk_pct}%.",
        "object:RESIDUAL_RISK": "Even the fastest option leaves a {risk_pct}% stockout risk until the order arrives in {eta}.",
        # revisions
        "revise:qty": "Revised to {qty} units; the order is now worth ₹{order_value}.",
        "revise:mode": "Revised to ship by {mode}, arriving in {eta}.",
        "revise:units": "Revised to move {units} units.",
        # rulings
        "rule:approved_review": "Approved with {confidence}% confidence; sent to the manager for review.",
        "rule:approved_auto": "Approved with {confidence}% confidence; executing automatically.",
        "rule:rejected": "Rejected: {reason}.",
    },
    "HI": {
        "propose:stockout_reorder": "{sku}: {supplier} से ₹{unit_price} की दर पर {qty} यूनिट ऑर्डर करें, {mode} से, {eta_days} दिन में पहुँचेगा।",
        "propose:supplier_switch": "{sku}: {from_supplier} की जगह {supplier} से ₹{unit_price} की दर पर {qty} यूनिट ऑर्डर करें, {mode} से, {eta_days} दिन में पहुँचेगा।",
        "propose:transfer": "{sku}: {from_site} से {to_site} को {units} यूनिट भेजें, ₹{saving_k}K होल्डिंग लागत बचेगी।",
        "propose:markdown": "{sku}: {excess} अतिरिक्त यूनिट निकालने के लिए {discount}% छूट दें।",
        "propose:bundle": "{sku}: {partner} के साथ {discount}% छूट पर बंडल करें; ये {conf_pct}% बास्केट में साथ बिकते हैं।",
        "propose:expiry_risk": "{sku}: {expiring} यूनिट {days_left} दिन में एक्सपायर होंगी; {discount}% फ़्लैश सेल चलाएँ।",
        "propose:cannibalization": "{rising} की बिक्री सामान्य से {uplift_pct}% ज़्यादा केवल तब रही जब {falling} स्टॉक में नहीं था; {rising} का अगला ऑर्डर सीमित रखें।",
        "propose:phantom_inventory": "{sku}: {days} दिन स्टॉक रिकॉर्ड और बिक्री मेल नहीं खाते; आइल {aisle} में स्टॉक गिनें।",
        "object:BORROWED_DEMAND": "{sku} की कुछ माँग {falling} से उधार की है, जो स्टॉक में नहीं है। {qty} नहीं, {suggested_qty} यूनिट ऑर्डर करें।",
        "object:FORECAST_UNCERTAIN": "{sku} की माँग का अनुमान कठिन है: पूर्वानुमान {wape_pct}% तक ग़लत रहा है। {qty} नहीं, {suggested_qty} यूनिट ऑर्डर करें।",
        "object:PHANTOM_STOCK": "{sku} का स्टॉक रिकॉर्ड भरोसेमंद नहीं है; आगे बढ़ने से पहले किसी व्यक्ति को जाँचना चाहिए।",
        "object:SUPPLIER_ESG_FLOOR": "आपूर्तिकर्ता {supplier_id} का ESG स्कोर {esg_score} है, जो न्यूनतम {esg_floor} से कम है।",
        "object:USER_PREFERENCE": "यह प्रबंधक के स्थायी निर्देश के विरुद्ध है।",
        "object:MOQ": "{qty} यूनिट न्यूनतम ऑर्डर {moq} से कम है।",
        "object:SHELF_LIFE_CAP": "{qty} यूनिट आधी शेल्फ़ लाइफ़ की माँग से ज़्यादा है; सीमा {shelf_cap} है।",
        "object:BUDGET_CAP": "ऑर्डर ₹{order_value} का है पर बजट में केवल ₹{budget_available} बचा है।",
        "object:AIR_SHARE": "यहाँ हवाई माल ढुलाई उचित नहीं; इससे हवाई ऑर्डर का हिस्सा {air_share_pct}% हो जाएगा, जबकि सीमा {max_air_share_pct}% है।",
        "object:AIR_UNNEEDED": "यहाँ हवाई माल ढुलाई की ज़रूरत नहीं: धीमे विकल्प से स्टॉकआउट जोखिम {multimodal_risk_pct}% रहता है, हवाई से {air_risk_pct}%।",
        "object:AUTO_LIMIT": "ऑर्डर ₹{order_value} का है, जो स्वचालित ऑर्डर की ₹{auto_limit} सीमा से ऊपर है; किसी व्यक्ति की मंज़ूरी चाहिए।",
        "object:CAPACITY": "{units} यूनिट नहीं समाएँगी; गंतव्य पर {free_capacity} की जगह है।",
        "object:FASTER_MODE": "इसके पहुँचने तक स्टॉकआउट जोखिम {risk_pct}% रहेगा। {faster_mode} से भेजने पर यह {faster_risk_pct}% रह जाएगा।",
        "object:RESIDUAL_RISK": "सबसे तेज़ विकल्प से भी ऑर्डर {eta_days} दिन में पहुँचने तक {risk_pct}% स्टॉकआउट जोखिम रहेगा।",
        "revise:qty": "संशोधित: {qty} यूनिट; ऑर्डर अब ₹{order_value} का है।",
        "revise:mode": "संशोधित: {mode} से भेजा जाएगा, {eta_days} दिन में पहुँचेगा।",
        "revise:units": "संशोधित: {units} यूनिट भेजी जाएँगी।",
        "rule:approved_review": "{confidence}% विश्वास के साथ स्वीकृत; समीक्षा के लिए प्रबंधक को भेजा गया।",
        "rule:approved_auto": "{confidence}% विश्वास के साथ स्वीकृत; स्वचालित रूप से निष्पादित हो रहा है।",
        "rule:rejected": "अस्वीकृत: {reason}।",
    },
}

REJECTION_REASONS = {
    "EN": {
        "BUDGET_CAP": "the budget cannot cover it", "NO_NET_BENEFIT": "it would cost more than it saves",
        "UNRESOLVED": "an objection could not be resolved", "SUPPLIER_ESG_FLOOR": "the supplier is below the ESG floor",
        "USER_PREFERENCE": "it breaks a standing instruction", "MOQ": "it is below the minimum order",
        "CAPACITY": "the destination has no room",
    },
    "HI": {
        "BUDGET_CAP": "बजट इसे पूरा नहीं कर सकता", "NO_NET_BENEFIT": "इसकी लागत बचत से ज़्यादा है",
        "UNRESOLVED": "एक आपत्ति का समाधान नहीं हो सका", "SUPPLIER_ESG_FLOOR": "आपूर्तिकर्ता ESG सीमा से नीचे है",
        "USER_PREFERENCE": "यह स्थायी निर्देश के विरुद्ध है", "MOQ": "यह न्यूनतम ऑर्डर से कम है",
        "CAPACITY": "गंतव्य पर जगह नहीं है",
    },
}


def days_text(days: int) -> str:
    return f"{days} day" if days == 1 else f"{days} days"


def has_line(key: str) -> bool:
    return key in LINES["EN"]


def render(key: str, lang: str, facts: dict) -> str:
    """One debate sentence. Unknown languages fall back to English; a missing fact raises KeyError."""
    table = LINES.get(lang, LINES["EN"])
    return table.get(key, LINES["EN"][key]).format(**facts)


def rejection_reason(rule: str, lang: str) -> str:
    table = REJECTION_REASONS.get(lang, REJECTION_REASONS["EN"])
    return table.get(rule, REJECTION_REASONS["EN"].get(rule, table["UNRESOLVED"]))
