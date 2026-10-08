# MK10 — Chat, strategy injection, voice, languages

**Goal:** the Intelligence chat answers from live data, a typed sentence can change how the agents behave, the voice button understands commands against the real catalogue, and all eight UI languages keep working with a small local model.

## 1. Design: the model routes, code answers

A 4B local model is reliable at one thing here: mapping a sentence to a small schema. It is not reliable at writing SQL or doing arithmetic. So:

1. A **rule router** tries first (keywords in English and Hindi, fuzzy SKU names). Most questions never reach the model, which keeps answers instant.
2. Otherwise the **LLM extracts an intent** into a fixed schema.
3. A **handler** (plain Python) computes the answer from the database.
4. The reply is a **translation key plus parameters** wherever the frontend already has a translated template, so Tamil, Bengali, Telugu, Marathi, Gujarati and Kannada work without asking the model to write them. Free text from the backend is produced in English or Hindi only.

There is no text-to-SQL and no free-form tool loop: fewer failure modes, no injection surface.

## 2. Intents — `nlq/intents.py`

```python
class Intent(BaseModel):
    name: Literal["greeting", "strategy_set", "strategy_get", "suppliers", "basket", "bullwhip",
                  "montecarlo", "sku_risk", "sku_sweet", "sku_summary", "stock_summary", "zones",
                  "stockout_horizon", "overstock", "explain_sku", "reorder", "unknown"]
    sku: str = ""                 # free text as spoken; resolved later
    days: int = 7
    mode: Literal["", "Cash Flow", "Growth", "Balanced"] = ""
    quantity: int = 0
    percent: float = 0.0
```

**Rule router** — `nlq/router.py::route_rules(text) -> Intent | None`. Mirror the keyword order of the existing `ask()` function in `src/pages/Intelligence.jsx` (including its Hindi keywords), so current behaviour is preserved, and add:

| Pattern | Intent |
|---|---|
| "why", "explain", "क्यों" + a resolvable SKU | `explain_sku` |
| "stock out"/"stockout"/"run out" + optional "N days" | `stockout_horizon` (`days` = N, default 7) |
| "overstock"/"excess"/"ओवरस्टॉक" | `overstock` |
| "reorder"/"order" + number + resolvable SKU | `reorder` |

The existing greeting test (`q.includes("hi")`) matches words such as "which"; in the router, match greetings on whole words only.

**SKU resolution** — `resolve_sku(text) -> str | None`: exact `SKU\d+` id; else `rapidfuzz.process.extractOne(text, choices, scorer=fuzz.partial_ratio, score_cutoff=80)` over SKU names, their first words (brand), categories' flagship items, and a small Hindi alias table (`"नमक" → Tata Salt`, `"मक्खन" → Amul Butter`, `"तेल" → Fortune Oil`, …).

**LLM extraction** — when the rule router returns `None`: `llm.json(Intent, EXTRACT_INTENT, text, fallback=lambda: Intent(name="unknown"))`. The system prompt lists each intent with one example sentence in English and one in Hindi.

## 3. Handlers — `nlq/router.py::answer(text, lang) -> dict`

Response shape:

```jsonc
{ "key": "criticalSkuRiskRes",          // frontend i18n template key, or null
  "params": { "list": "…", "par": "57" }, // values for {placeholders}
  "text": "…",                           // used when key is null (EN or HI)
  "strategy": null,                      // new strategy object when it changed
  "refresh": false,                      // true when the frontend should re-hydrate
  "navigate": null }                     // e.g. "/sku/SKU003" (not used by the chat page today)
```

| Intent | Reply | Computation |
|---|---|---|
| `greeting` | key `intelGreetings` | — |
| `strategy_set` | key `strategyUpdatedCash` / `strategyUpdatedGrowth`, or text for Balanced; `strategy` set; `refresh: true` | §4 |
| `strategy_get` | key `currentStrategyWeights`, params `mode, savings, safety` | active policy |
| `suppliers` | key `supplierTrack`, params `count, list` | ranked suppliers: "name (tier, on-time %, avg TAT d)" |
| `basket` | key `basketRules`, params `count, rule, conf, lift, cannibalCount` | mined rules |
| `sku_risk` | key `criticalSkuRiskRes`, params `list, par` | SKUs with `risk > 60` |
| `sku_sweet` | key `sweetSpotRes`, params `list, count` | sweet SKUs |
| `sku_summary` | key `skuZoneSummary`, params `count, sweet, chaos, ghost, money` | zone counts |
| `stock_summary` | key `inventoryStockSummary`, params `total, count, ghostStock, ghostPct, par` | stock totals |
| `zones` | key `zoneIkigaiDesc` | — |
| `bullwhip` | text | `bullwhip.series` ratios: raw vs. smoothed variance, number of reorder triggers |
| `montecarlo` | text | path count from settings, SKUs above 60 % |
| `stockout_horizon` | text | SKUs with `doc ≤ days` and no inbound arriving in time, each with days left |
| `overstock` | text | ghost + money SKUs with days of cover and capital tied up |
| `explain_sku` | text | §5 |
| `reorder` | text; `refresh: true` | creates an `Alert` (type `stockout_reorder`, `routed="review"`, payload from the Distributor Selector's stored ranking, `qty` = spoken quantity rounded up to MOQ) so the order appears on the Alerts page for one-click approval. It does not place the order directly. |
| `unknown` | key `intelDefaultPrompt` | — |

Rule for key vs. text: use a `key` only when the frontend template is fully parameterised. `bullwhipDampening` and `mcSimResults` contain hardcoded numbers in the template itself, so those intents return computed `text` instead.

Text replies are built from English/Hindi templates in `llm/templates.py`. Only `explain_sku` is passed through `llm.text` for better prose.

## 4. Strategy injection — `nlq/strategy.py`

"Prioritize cash flow over growth for the next 30 days" becomes a stored, expiring policy that changes real thresholds.

```python
PRESETS = {   # identical to the values the UI sets today
    "Balanced":  {"savingsPriority": 0.5, "safetyStockMultiplier": 1.0, "leadTimeBuffer": 1.2},
    "Cash Flow": {"savingsPriority": 0.9, "safetyStockMultiplier": 0.8, "leadTimeBuffer": 1.2},
    "Growth":    {"savingsPriority": 0.2, "safetyStockMultiplier": 1.5, "leadTimeBuffer": 1.5},
}

class PolicyOut(BaseModel):
    mode: Literal["Balanced", "Cash Flow", "Growth"]
    horizon_days: int = 30
```

`compile_policy(text) -> PolicyOut`: rules first ("cash", "saving", "कैश", "बचत" → Cash Flow; "growth", "aggressive", "विकास" → Growth; "balanced", "normal", "reset" → Balanced; `(\d+)\s*(day|days|दिन)` → horizon), LLM (`COMPILE_STRATEGY`) only when the rules find no mode. `horizon_days` is clamped to `[1, 180]`.

`apply_policy(policy, source_text) -> dict`: deactivate the current `StrategyPolicy`, insert the new one with `expires_on = today + horizon_days` (Balanced never expires), write `bb.act("rlhfArbiter", "Strategy set to {mode}", result="expires {date}")`, start `run_pipeline("strategy")` as a background task, and return the strategy object `{mode, savingsPriority, safetyStockMultiplier, leadTimeBuffer, lastUpdate}`.

What each mode actually changes (all already wired in earlier milestones):

| Lever | Cash Flow | Growth |
|---|---|---|
| Safety stock multiplier (mk6 §3) | × 0.8 | × 1.5 |
| Lead-time buffer in zone test (mk4 §6) | 1.2 | 1.5 |
| Budget envelope (mk6 §4) | 60 % | 130 % |
| Supplier weights (mk4 §8) | price and incentives | speed and reliability |
| Cash weight in Arbiter utility (mk8 §4) | 0.12 | 0.02 |
| Air-freight allowance, carbon price | lower | higher |

Endpoints: `GET /api/strategy` returns the current object; `PUT /api/strategy` accepts `{mode}` or the full object the UI context sends, maps it to the nearest preset by `mode`, and applies it.

Scoped voice adjustments ("increase safety stock by 20 % for Lays") are stored as `Preference(scope="sku", target=<id>, directive="safety_stock_pct", value=20)`. The Inventory Optimizer applies it as a per-SKU multiplier `(1 + value/100)` on safety stock, on top of the strategy multiplier.

## 5. Explain — `GET /api/skus/{id}/explain`

Builds a flat facts dict from the latest snapshot, ruling and negotiation: `name, zone, on_hand, velocity, trend_pct, doc, lead_days, stockout_prob_pct, par, qty, supplier, mode, agreed_price, confidence, signal (if any), counterfactual lead_days / on_hand`. The template sentence (English shown; Hindi analogous):

> "{name} is in the {zone}: {on_hand} units cover {doc} days against a {lead_days}-day lead time, so stockout probability is {stockout_prob_pct}%. Recommended: {qty} units from {supplier} by {mode} ({confidence}% confidence), protecting ₹{par} of profit. No order would be needed if lead time were {cf_lead} days or stock were {cf_on_hand} units."

`llm.text(EXPLAIN_SKU, facts=facts, fallback=template_sentence)` may rephrase it; the grounding check enforces the numbers. Returns `{text, facts, source}`.

## 6. Voice — `nlq/voice.py`, `POST /api/voice/intent`

Body `{text, lang}`. Returns exactly the command object the frontend's `handleVoiceCommand` already understands:

```jsonc
{ "type": "PROCURE" | "NAVIGATE_SKU" | "SKU_DETAIL" | "SET_LANG" | "NAVIGATE" | "UNKNOWN",
  "skuId": "SKU003", "path": "sandbox", "lang": "HI", "text": "…" }
```

Rules (same triggers as the current component, evaluated server-side with the real catalogue):

| Utterance contains | Result |
|---|---|
| "increase" + "safety stock" + SKU | store the scoped adjustment (§4, percent parsed from the text, default 20), start a run, return `PROCURE` |
| "show" + ("zone" or "inventory") + SKU | `NAVIGATE_SKU` |
| "risk" + SKU | `SKU_DETAIL` |
| "switch to" + language name | `SET_LANG` with the matching code among `EN, HI, TA, BN, TE, MR, GJ, KN` |
| "war room", "scenario", "disruption", "what if" | `NAVIGATE`, `path: "sandbox"` |
| page names ("alerts", "inventory", "dashboard", "replenish", "store", "data hub", "command") | `NAVIGATE` with that path |
| otherwise | LLM intent extraction; `explain_sku`/`sku_risk` with a SKU → `SKU_DETAIL`; else `UNKNOWN` |

Trigger words are checked in English and Hindi (reuse the frontend's own words: `increase/बढ़ाएं`, `safety stock`, `show`, `risk`, `switch to`). SKU names are resolved with `resolve_sku`, which fixes the current component's alias table (it maps "lays" to `SKU001`, which is Amul Butter).

## 7. Language handling summary

| Content | EN | HI | TA, BN, TE, MR, GJ, KN |
|---|---|---|---|
| Static UI text | frontend | frontend | frontend (falls back to EN per key) |
| Chat answers with a template key | frontend template | frontend template | frontend template (EN fallback per key) |
| Alert `msg`/`action`/`impact`, debate lines, text chat answers | backend template or model | backend template or model | English |
| Voice recognition | browser (`en-IN`) | browser (`hi-IN`) | browser, as today |

This is a stated limit: dynamic agent text is English or Hindi. Extending it means adding template strings for another language in `llm/templates.py`; no code change.

## Verify

`uv run pytest tests/test_nlq.py` (LLM offline unless noted):

- Each example in the existing `ask()` keyword list routes to the same branch as today (table-driven test with ~20 sentences, English and Hindi).
- "which SKUs will stock out in 7 days" → `stockout_horizon`, not `greeting`.
- `resolve_sku("lays")` → `SKU003`; `resolve_sku("tata salt")` → `SKU004`; `resolve_sku("नमक")` → `SKU004`; `resolve_sku("zzz")` → `None`.
- `compile_policy("Prioritize cash flow over growth for the next 30 days")` → `Cash Flow`, 30; `apply_policy` lowers the next run's safety stocks and sets `expires_on`.
- An expired policy reverts to Balanced on the next run.
- `/api/chat` with "critical skus" returns key `criticalSkuRiskRes` with a non-empty `list`.
- `/api/chat` with "reorder 50 units of lays" creates one open alert for `SKU003` and no `PurchaseOrder`.
- `/api/skus/SKU003/explain` returns text containing the SKU name and passing `grounded(text, facts)`.
- `/api/voice/intent` "show risk for colgate" → `SKU_DETAIL`, `SKU006`; "switch to hindi" → `SET_LANG`, `HI`; "open war room" → `NAVIGATE`, `sandbox`.
- With `_chat` mocked to return `{"name":"overstock"}` for an unrouted sentence, the overstock handler runs.

Manual, with LM Studio open: ask "what is going on with the toothpaste?" in the chat; the model should route it to `explain_sku` for Colgate.

## Done when

NLQ tests pass, and switching strategy from the chat visibly changes safety stocks in the next `/api/bootstrap`.
