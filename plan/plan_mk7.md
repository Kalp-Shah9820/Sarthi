# MK7 — RESOLVE and EXECUTE agents

**Goal:** turn decision records into concrete, costed proposals (which supplier, which shipping mode, which transfer, which campaign), negotiate price, and execute approved actions as database records and outbox files.

A **proposal** is the unit that flows from RESOLVE to the Arbiter (mk8) to EXECUTE:

```python
@dataclass
class Proposal:
    id: str                    # f"{kind}:{sku_id}"
    kind: str                  # "purchase" | "transfer" | "campaign" | "audit"
    alert_type: str            # stockout_reorder | supplier_switch | transfer | markdown | cannibalization | phantom_inventory | expiry_risk
    sku_id: str
    author: str                # agent key that proposed it
    payload: dict              # everything needed to execute (below)
    cost: float                # cash out (order value, transfer cost, discount given)
    par_rescued: float         # rupees of Profit-at-Risk this removes
    co2_kg: float
    alternatives: list[dict]   # other payloads considered, with their cost/benefit
    facts: dict                # flat numbers for templates, narration and grounding
    status: str = "proposed"   # proposed | revised | approved | rejected
```

## 1. Distributor Selector — `agents/distributor_selector.py` (RESOLVE, key `distributorSelector`; speaks as `negotiator`)

For every SKU with `decisions[sku].qty > 0`:

1. **Supplier metrics** from `Delivery` (last 12 months): `tat_mean`, `tat_cv`, `on_time` (Beta posterior), `fill_rate = Σ received / Σ ordered`, plus `defect_rate`, `esg`, `price_ratio = unit_price / median unit price for this SKU`, `incentive` = `incentive_pct/100` if `qty ≥ incentive_min_qty` else 0.
2. `urgency = clip(1 − doc / max(lead_eff, 0.1), 0, 1)`.
3. `scores = topsis.score(rows, strategy.mode, urgency)`; drop banned suppliers from the envelope; add +3 to preferred suppliers; mark infeasible capacity.
4. **Shipping mode:** `esg.options(...)` for the top supplier → pick the recommended index (mk4 §10).
5. **Negotiate** price with the top supplier (§3).
6. Build the proposal: `payload = {supplier_id, qty, unit_price (negotiated or list), mode, eta_days}`; `cost = qty × unit_price + freight`; `par_rescued = par_shortage(now) − par_shortage(after order with that mode's lead time)` using `simulate` with the order added as a receipt at `eta_days`; `alternatives` = the other suppliers and modes with their scores.
7. `alert_type = "supplier_switch"` if the chosen supplier differs from the SKU's primary, else `"stockout_reorder"`.

Also compute, for the Replenish page, **global** supplier rows (score with `urgency=0`, `price_ratio=1`) and store `Event(kind="metric", key="distributors", value={global: [...], per_sku: {...}})`. Global rows carry the UI's fields: `name, tat ("1–2 days" from floor/ceil of mean ± std), reliability (on_time × 100), price, incentive, score, tier, fulfillment, defectRate, capacityLimit, avgTAT, tatHistory (12 monthly means)`.

Blackboard: `put("supplier_primary", top supplier's short name)`; `act("negotiator", "Ranked {n} suppliers for {sku}", result="{supplier} · score {score}")` for each chaos SKU.

## 2. Overstock Resolver — `agents/overstock_resolver.py` (RESOLVE, key `overstockResolver`)

For ghost and money SKUs, three candidate remedies are generated and one is chosen:

1. **Transfer.** Build `surplus`/`deficit` across all locations (mk4 §9) and solve `plan_transfers`. Each resulting move is a candidate with `cost = units × transfer_cost`, `par_rescued = units × cogs × holding_pct_month × months_to_clear / 2` (carrying loss removed at the source) plus avoided purchase cost at the destination.
2. **Markdown.** Constant-elasticity estimate: `uplift = (1 − d)^(−ε) − 1` with elasticity `ε = 1.8` (staples 1.2). Choose discount `d ∈ {10, 15, 20, 25} %` maximising `margin_after × extra_units − carrying loss remaining`, never pricing below `cogs × 1.02`. `cost` = margin given away; `par_rescued` = carrying loss and expiry write-off avoided.
3. **Bundle.** If a mined rule links the overstocked SKU with a sweet/chaos SKU (confidence ≥ 0.5), propose a bundle at 10 % off the overstocked item only; uplift = `confidence × partner velocity × 0.3` units/day.

**Choosing among them** uses Thompson sampling over learned approval rates (mk8 §5): for each candidate, `score = (par_rescued − cost) × sample(Beta(arm))` with arm key `f"{zone}:{remedy}"`; the highest score becomes the proposal, the others go into `alternatives`. With no feedback yet the Beta(2, 2) prior makes this mostly value-driven with a little exploration; as the manager approves or dismisses, the system leans toward remedies they accept.

Additional proposals from this agent:

- `expiry_risk` (kind `campaign`, flash sale 20 %) when `expiring > 0` within 14 days.
- `cannibalization` (kind `audit`) for each detected pair: payload advises capping the rising SKU's order at its pre-substitution velocity; `par_rescued` = over-order value avoided.
- `phantom_inventory` (kind `audit`) for each anomaly: payload is a cycle-count task for that SKU's aisle.

Store campaign candidates for the Replenish page in `Event(kind="metric", key="campaigns", value=[{type, target, skuIds, discount, estImpactValue}])` — exactly one per type (`markdown`→ghost, `bundle`→chaos, `flash`→money), the best of each. Store transfer suggestions in `Event(kind="metric", key="transfers", value=[{skuId, from, to, units, saving}])`.

Blackboard: `put("ghost_transfer_ready", "TRUE"|"FALSE", tone="sweet")`; `act("overstockResolver", "Found {n} profitable transfers", result="{units} units · ₹{saving}K saved")`.

Both RESOLVE agents return `{"proposals": [ ... ]}` (lists are concatenated by the state reducer).

## 3. Negotiation — `agents/negotiation.py`

Price is settled by a deterministic alternating-offers protocol; the LLM only writes the emails.

- **Buyer limits.** `target = list_price × (1 − incentive − 0.5 × max_discount)`. `reserve = min(list_price, landed price of the second-ranked supplier)` (the buyer's best alternative).
- **Supplier simulator** (stands in for the real counterparty). `floor = list_price × (1 − max_discount_pct/100)`, opening ask `= list_price`.
- **Concession curves** (time-dependent tactics): at round `t` of `T = 4`,
  `buyer_offer = target + (reserve − target) × (t/T)^(1/β_b)`, `supplier_ask = list − (list − floor) × (t/T)^(1/β_s)`.
  `β_b = 0.5 + 2 × urgency` (patient when there is cover, concedes fast when a stockout is close); `β_s` = 0.6 (Gold), 1.0 (Silver), 1.6 (Bronze).
- **Agreement** when `buyer_offer ≥ supplier_ask`: price = midpoint, rounded to ₹0.05. If no agreement after `T` rounds, `agreed_price = None` and the proposal uses list price.
- Record every round `{round, buyer_offer, supplier_ask}` in `Negotiation.rounds`.

**Emails.** For the opening round only (the one a human would actually send), draft with `llm.text(DRAFT_EMAIL, facts={sku, qty, offer, list_price, eta_days, supplier}, fallback=templates.render("email", "opening", lang, ...))` and store as `OutboxEmail(status="draft", ref=proposal.id)`. Later rounds are simulation and get template text only, which keeps the per-run LLM budget small.

`facts` added to the proposal: `list_price, agreed_price, saving = (list − agreed) × qty, rounds`.

## 4. Execution Engine — `agents/execution_engine.py` (EXECUTE, key `executionEngine`)

Two entry points.

**(a) In the pipeline** — `work(state)` receives the Arbiter's `rulings` (mk8) and:

1. Creates one `Alert` per approved proposal: `zone` = SKU zone, `risk` = decision risk, `confidence` and `routed` from the ruling, `msg` (LLM-narrated or template), `action`/`impact` (templates), `impact_value = par_rescued`, `payload = proposal.payload + {kind, alert_type, facts}`, `sku_label` = SKU name (for cannibalization: `"{rising} → {falling}"`, matching the UI's existing alert).
2. For `routed == "auto"` and `dry_run == False`: execute immediately via `execute(alert, source="auto")` and set `status="approved"`.
3. Everything else stays `open` for the human.
4. Dry runs (What-If) create no alerts and execute nothing; they only produce debate events.

**(b) From the API** — `execute(alert_or_payload, source) -> dict`:

| Kind | Effect |
|---|---|
| `purchase` | Insert `PurchaseOrder(id="PO-" + base36(time_ms), status="confirmed")`; insert `Inbound(expected_on = today + eta_days)`; mark the opening `OutboxEmail` `queued`; write `outbox/{po_id}.eml` and `outbox/{po_id}.json` |
| `transfer` | Insert `TransferOrder(id="TRF-" + 5 base36 chars)`; decrement source and increment destination in today's `StockDaily` |
| `campaign` | Insert or update `Campaign(status="live")` |
| `audit` | Insert an `Event(kind="action")` task line; no stock change |

Returns `{"txid": id, "kind": kind}`. Every execution writes `bb.act("executionEngine", ...)` so it appears in the audit trail, and `bb.say("executionEngine", ..., stance="execute")` in pipeline runs.

`.eml` files are plain RFC 822 text built with `email.message.EmailMessage` (From `sarthi@localhost`, To supplier email). Nothing is transmitted.

**Idempotency.** `execute` first checks whether the alert already has a `txid`; if so it returns it unchanged. A double-click on Approve cannot create two purchase orders.

## Verify

`uv run pytest tests/agents/test_resolve_execute.py` (seeded data, LLM offline):

- Every chaos SKU yields a `purchase` proposal with `qty > 0`, a feasible supplier, `par_rescued > 0`, and three shipping alternatives.
- The proposal's supplier is never in `envelope.banned_suppliers`.
- Negotiation: `floor ≤ agreed_price ≤ reserve` whenever a price is agreed; higher urgency never lowers the agreed price; exactly one draft email per purchase proposal.
- With the seeded warehouses, at least one transfer of `SKU002` out of `WH-DEL` is proposed (Delhi holds 640 against much lower stock elsewhere), and no transfer exceeds the source surplus.
- Markdown never prices below `cogs × 1.02`.
- Exactly three campaign candidates are stored, one per type.
- `execute` on a purchase alert creates one `PurchaseOrder`, one `Inbound`, and two outbox files; calling it again returns the same `txid` and creates nothing.
- `execute` on a transfer moves stock: source decreases and destination increases by the same units.
- A dry run leaves `Alert`, `PurchaseOrder`, `TransferOrder` counts unchanged.

## Done when

Tests pass and `backend/outbox/` contains a readable `.eml` after the purchase test.

## Implementation notes (as built, 2026-10-09)

Verified by `tests/agents/test_resolve.py` (24 tests) and `tests/agents/test_execute.py` (9 tests). Deviations from the text above:

- **Model wording is a rephrase, not an explanation.** With the live model, "explain these facts" produced sentences whose numbers passed the grounding check but whose meaning was wrong (a rupee value called "units", a cap-the-order finding described as "increase stock"). Alerts now send the already-correct template sentence with the `REPHRASE` prompt. Two extra guards in `gateway.text`: `must_contain` (every name in the template must survive) and `max_chars`. Outcome `off_template` is logged when they fail. mk8's debate lines must use the same approach (`NARRATE_ALERT` / `NARRATE_DEBATE` no longer exist).
- **Proposal** gained `extra` (review context: `moq`, `shelf_cap`, `modes`, `mode_risk`, `urgency`, `zone`, `remedy`, `dest_free_capacity`, `label`) and `net`. `as_dict()` lifts `extra` to the top level so `compliance_guardian.review` can read it. Ids are `kind:alert_type:sku_id`. Alert type `bundle` was added, with templates.
- **The order quantity is recomputed for the chosen supplier's lead time.** A faster supplier needs a smaller order (Lays: 984 units from Reliance vs. 1,788 from the usual supplier).
- **Shipping mode shifts the supplier's lead time** relative to the default mode (multimodal): air is 2 days sooner, sea 4 days later, never below 1 day.
- **`par_rescued` for a purchase** = shortage value if nothing changes (waiting for the usual supplier) minus shortage value until this order lands.
- **`cost` for a purchase is the order value plus freight**, so `net` is not a profit figure for purchases; the Arbiter (mk8) weighs cost with `w_cash`. `net` is meaningful for transfers and campaigns.
- **Hub transfers even out days of cover.** Per-hub sales do not exist yet, so each hub's demand is assumed proportional to its capacity (`hub_demand_shares`). The value of a move is the carrying cost saved because the unit sells sooner. The plan's safety-stock rule found no deficits at all, because every hub is overstocked on the ghost/money SKUs. `plan_transfers` gained `source_value`. The store is not part of the hub network.
- **Markdown must clear cost x 1.02; a flash sale may go down to cost x 0.75** when holding or expiry would cost more. Tata Salt (4 % margin) therefore gets no markdown, and with a transfer not worth its freight it gets **no proposal at all**: there is no profitable action, which is the honest answer.
- **Remedy choice** only considers remedies with positive net value; Thompson sampling reweights between those. Learning never promotes a loss-making remedy.
- **Campaign cards** always number three. The bundle card promotes an in-stock companion of a product that is running out. Cards may show a negative `net` (e.g. the markdown for Surf Excel loses more margin than it saves).
- **Cannibalization value** = uplift x velocity x review period x cost x the falling SKU's stockout probability.
- **Negotiation**: the buyer's reserve is capped by the next-best supplier's price; the opening email quotes the price we expect to pay. On the seeded data deals close in round 4 at 3-4 % below list.
- **Execution ids**: `PO-<base36 time>`, `TRF-<5 chars>`, `CMP-<id>`, `TASK-<5 chars>`. Dates use the data's last day (`data_today()`), not the wall clock. Transfers are clamped to what the source holds. One campaign row per type.
- **Settings**: `outbox_path` (`SARTHI_OUTBOX_PATH`, default `backend/outbox`).
- **`learning/bandit.py`** is implemented here in full (`get_arm`, `mean`, `sample`, `lower_bound`, `record`); mk8 only needs to call it.
- **`agents/stages.py`** gained `resolve()` and `sense_decide_resolve()`.
- **Supplier scores are on a strict absolute scale**: seeded suppliers score 73 / 51 / 37 overall (the mock shows 94 / 88 / 79). Displayed tiers come from the supplier record, not from the score.
- **Measured**: SENSE to RESOLVE 25 s cold, 0.4 s warm; with the live model and nothing cached, a run that also raises 7 alerts took 48 s.
