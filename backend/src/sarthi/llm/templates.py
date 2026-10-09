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

# Chat answers that have no fully parameterised template in the frontend. English and Hindi only.
_EXPLAIN_EN = ("{name} is in the {zone}: {on_hand} units cover {doc} days against a lead time of {lead_days} days, "
               "so stockout probability is {stockout_prob_pct}%.")
_EXPLAIN_HI = ("{name} {zone} में है: {on_hand} यूनिट {doc} दिन चलेंगी जबकि लीड टाइम {lead_days} दिन का है, "
               "इसलिए स्टॉकआउट की संभावना {stockout_prob_pct}% है।")
_ORDER_EN = _EXPLAIN_EN + " Recommended: {qty} units from {supplier} by {mode} ({confidence}% confidence), protecting ₹{rescued} of profit."
_ORDER_HI = _EXPLAIN_HI + " सुझाव: {supplier} से {mode} द्वारा {qty} यूनिट ({confidence}% विश्वास), जिससे ₹{rescued} का मुनाफ़ा बचेगा।"


def _both(en: str, hi: str) -> dict:
    return {"EN": {"text": en}, "HI": {"text": hi}}


CHAT = {
    "warming": _both("The first analysis run has not finished yet. Ask again in a minute.",
                     "पहला विश्लेषण अभी पूरा नहीं हुआ है। एक मिनट बाद फिर पूछें।"),
    "need_sku": _both("Which product do you mean? Name it, for example 'why is Lays in the Chaos Zone?'.",
                      "आप किस उत्पाद की बात कर रहे हैं? उसका नाम बताएँ, जैसे 'Lays केओस ज़ोन में क्यों है?'।"),
    "critical_none": _both("No SKU is above {threshold}% stockout risk right now.",
                           "अभी कोई SKU {threshold}% से ज़्यादा स्टॉकआउट जोखिम पर नहीं है।"),
    "sweet_none": _both("No SKU is in the Sweet Spot right now.", "अभी कोई SKU स्वीट स्पॉट में नहीं है।"),
    "suppliers_none": _both("No supplier has been ranked yet.", "अभी किसी आपूर्तिकर्ता की रैंकिंग नहीं हुई है।"),
    "suppliers": _both("I track {count} distributors: {list}. {fastest} is the fastest; {largest} has the most capacity.",
                       "मैं {count} वितरकों पर नज़र रखता हूँ: {list}। सबसे तेज़ {fastest} है; सबसे ज़्यादा क्षमता {largest} की है।"),
    "basket_none": _both("No association rule is strong enough to report yet.", "अभी कोई बास्केट नियम बताने लायक मज़बूत नहीं है।"),
    "basket": _both("I have {count} active association rules. Strongest: {rule} ({conf}% confidence, {lift}x lift). "
                    "{cannibalCount} cannibalization alerts are active.",
                    "मेरे पास {count} सक्रिय बास्केट नियम हैं। सबसे मज़बूत: {rule} ({conf}% विश्वास, {lift}x लिफ़्ट)। "
                    "{cannibalCount} कैनिबलाइज़ेशन अलर्ट सक्रिय हैं।"),
    "bullwhip_none": _both("There is not enough sales history to measure the bullwhip effect yet.",
                           "बुलव्हिप प्रभाव मापने के लिए अभी पर्याप्त बिक्री इतिहास नहीं है।"),
    "bullwhip": _both("Over the last {weeks} weeks, weekly demand swung by ±{raw_pct}%. The smoothed signal the agents order "
                      "against swings by ±{smooth_pct}%. {triggers} reorder triggers fired in that time. "
                      "The Dashboard has the full chart.",
                      "पिछले {weeks} हफ़्तों में साप्ताहिक माँग ±{raw_pct}% घटी-बढ़ी। एजेंट जिस स्मूद सिग्नल पर ऑर्डर करते हैं वह "
                      "±{smooth_pct}% घटता-बढ़ता है। इस दौरान {triggers} रीऑर्डर ट्रिगर चले। पूरा चार्ट डैशबोर्ड पर है।"),
    "montecarlo": _both("{paths} simulated demand paths per SKU per run. {count} SKUs above the {threshold}% stockout "
                        "threshold: {list}. Open any SKU for its full histogram.",
                        "हर रन में प्रति SKU {paths} सिम्युलेटेड माँग पथ। {threshold}% स्टॉकआउट सीमा से ऊपर {count} SKU: {list}। "
                        "पूरा हिस्टोग्राम देखने के लिए कोई भी SKU खोलें।"),
    "montecarlo_none": _both("{paths} simulated demand paths per SKU per run. No SKU is above the {threshold}% stockout threshold.",
                             "हर रन में प्रति SKU {paths} सिम्युलेटेड माँग पथ। कोई SKU {threshold}% स्टॉकआउट सीमा से ऊपर नहीं है।"),
    "stockout": _both("{count} SKUs will run out within {days} days, counting stock already on order: {list}.",
                      "ऑर्डर पर चल रहे स्टॉक को गिनकर भी {count} SKU {days} दिन के भीतर खत्म हो जाएँगे: {list}।"),
    "stockout_none": _both("No SKU is set to run out within {days} days, counting stock already on order.",
                           "ऑर्डर पर चल रहे स्टॉक को गिनकर, {days} दिन के भीतर कोई SKU खत्म नहीं होगा।"),
    "overstock": _both("{count} SKUs are overstocked, with ₹{total_k}K tied up: {list}.",
                       "{count} SKU में ज़रूरत से ज़्यादा स्टॉक है, कुल ₹{total_k}K फँसा है: {list}।"),
    "overstock_none": _both("No SKU is overstocked right now.", "अभी किसी SKU में ज़रूरत से ज़्यादा स्टॉक नहीं है।"),
    "reorder": _both("Reorder request created: {qty} units of {sku} from {supplier} at ₹{unit_price} each (₹{total} in total), "
                     "about {eta_days} days to arrive. Nothing is ordered until you approve it on the Alerts page.",
                     "रीऑर्डर अनुरोध बना दिया गया: {supplier} से {sku} की {qty} यूनिट, ₹{unit_price} प्रति यूनिट (कुल ₹{total}), "
                     "लगभग {eta_days} दिन में पहुँचेगा। जब तक आप अलर्ट पेज पर स्वीकृत नहीं करते, कोई ऑर्डर नहीं जाएगा।"),
    "reorder_rounded": _both("Reorder request created: {qty} units of {sku} from {supplier} at ₹{unit_price} each (₹{total} in total), "
                             "about {eta_days} days to arrive. You asked for {asked}; it is rounded up to the pack size of {moq}. "
                             "Nothing is ordered until you approve it on the Alerts page.",
                             "रीऑर्डर अनुरोध बना दिया गया: {supplier} से {sku} की {qty} यूनिट, ₹{unit_price} प्रति यूनिट (कुल ₹{total}), "
                             "लगभग {eta_days} दिन में पहुँचेगा। आपने {asked} माँगी थीं; इसे {moq} के पैक साइज़ तक बढ़ाया गया है। "
                             "जब तक आप अलर्ट पेज पर स्वीकृत नहीं करते, कोई ऑर्डर नहीं जाएगा।"),
    "reorder_no_supplier": _both("{sku} has no supplier on record, so I cannot prepare an order for it.",
                                 "{sku} के लिए कोई आपूर्तिकर्ता दर्ज नहीं है, इसलिए मैं इसका ऑर्डर तैयार नहीं कर सकता।"),
    "strategy_balanced": _both("STRATEGY UPDATED: Sarthi is back to Balanced. Safety stock multiplier {safetyStockMultiplier}x, "
                               "lead-time buffer {leadTimeBuffer}x.",
                               "रणनीति अपडेट: सारथी फिर से Balanced मोड में है। सुरक्षा स्टॉक गुणक {safetyStockMultiplier}x, "
                               "लीड टाइम बफ़र {leadTimeBuffer}x।"),
    "explain_order": _both(_ORDER_EN, _ORDER_HI),
    "explain_order_stock": _both(_ORDER_EN + " No order would be needed if stock were {cf_on_hand} units.",
                                 _ORDER_HI + " अगर स्टॉक {cf_on_hand} यूनिट होता तो ऑर्डर की ज़रूरत नहीं पड़ती।"),
    "explain_order_both": _both(_ORDER_EN + " No order would be needed if lead time were {cf_lead} days or stock were {cf_on_hand} units.",
                                _ORDER_HI + " अगर लीड टाइम {cf_lead} दिन होता या स्टॉक {cf_on_hand} यूनिट होता तो ऑर्डर की ज़रूरत नहीं पड़ती।"),
    "explain_action": _both(_EXPLAIN_EN + " Open recommendation ({confidence}% confidence): {action}.",
                            _EXPLAIN_HI + " खुला सुझाव ({confidence}% विश्वास): {action}।"),
    "explain_excess": _both(_EXPLAIN_EN + " That is {excess_units} units more than a month of sales, and holding them puts ₹{par} at risk. "
                                          "No order is needed.",
                            _EXPLAIN_HI + " यह एक महीने की बिक्री से {excess_units} यूनिट ज़्यादा है, और इसे रखने से ₹{par} जोखिम में है। "
                                          "ऑर्डर की ज़रूरत नहीं है।"),
    "explain_ok": _both(_EXPLAIN_EN + " No action is needed.", _EXPLAIN_HI + " किसी कार्रवाई की ज़रूरत नहीं है।"),
}

TASKS = {"alert": ALERT, "email": EMAIL, "chat": CHAT}


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
