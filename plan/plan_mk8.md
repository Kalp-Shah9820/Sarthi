# MK8 — Orchestration, Arbiter, confidence, learning

**Goal:** wire the seven agents into one graph run, add the Arbiter that makes agents genuinely disagree and settle, attach an honest confidence score to every decision, and close the loop with manager feedback.

## 1. Run state — `orchestrator/state.py`

```python
import operator
from typing import Annotated, Any, TypedDict


def merge(a: dict, b: dict) -> dict:
    return {**a, **b}


class RunState(TypedDict, total=False):
    run_id: int
    dry_run: bool
    lang: str
    scenario: dict                 # {"lead_mult": 1.0, "demand_mult": 1.0}
    strategy: dict                 # active policy params + mode
    lead_modifier: dict            # supplier_id -> float     (Macro Sentinel)
    demand_multiplier: dict        # category -> float        (Macro Sentinel)
    signal_count: int
    forecasts: Any                 # dict[str, ForecastResult] (Demand Intel), in-memory only
    forecast_ready: bool
    decisions: Annotated[dict, merge]
    envelope: dict
    proposals: Annotated[list, operator.add]
    rulings: list
    errors: Annotated[list, operator.add]
```

Only `proposals`, `errors` and `decisions` are written by more than one node, hence the reducers. Parallel nodes must not both write a key that has no reducer.

## 2. Graph — `orchestrator/graph.py`

```python
from langgraph.graph import END, START, StateGraph


def build_graph(agents: dict):
    g = StateGraph(RunState)
    for name in ("macro_sentinel", "demand_intel", "inventory_optimizer", "compliance_guardian",
                 "distributor_selector", "overstock_resolver", "arbiter", "execution_engine"):
        g.add_node(name, agents[name].run)

    # SENSE: two agents in parallel
    g.add_edge(START, "macro_sentinel")
    g.add_edge(START, "demand_intel")
    # DECIDE: waits for both SENSE agents
    g.add_edge(["macro_sentinel", "demand_intel"], "inventory_optimizer")
    g.add_edge("inventory_optimizer", "compliance_guardian")
    # RESOLVE: two agents in parallel
    g.add_edge("compliance_guardian", "distributor_selector")
    g.add_edge("compliance_guardian", "overstock_resolver")
    # Arbiter waits for both, then EXECUTE
    g.add_edge(["distributor_selector", "overstock_resolver"], "arbiter")
    g.add_edge("arbiter", "execution_engine")
    g.add_edge("execution_engine", END)
    return g.compile()
```

Both RESOLVE agents always run; each one skips SKUs that are not its concern. Routing by zone therefore happens per SKU inside the agents, which keeps the graph static and the join (`["distributor_selector", "overstock_resolver"]`) always satisfiable.

## 3. Runner — `orchestrator/runner.py`

```python
async def run_pipeline(trigger: str, *, scenario: dict | None = None, dry_run: bool = False, lang: str = "EN") -> int
```

1. `await llm.probe()`.
2. Load the active `StrategyPolicy` (expire it first if `expires_on < today`, reverting to Balanced).
3. Insert `Run(status="running")`; create `Blackboard(run.id)`; instantiate the agents with `(bb, llm, settings, strategy)`.
4. `async for update in graph.astream(initial_state, stream_mode="updates")`: for each node that finished, write `Event(kind="context", key="phase", value={"v": phase_of(node)})` so the UI can show progress.
5. On completion set `status="done"`, `finished_at`; on exception set `failed` and re-raise.
6. Return `run.id`.

A module-level `asyncio.Lock` serialises non-dry runs; a second request while one is running waits and then returns the fresh run id. Dry runs use their own lock so a What-If never blocks a real run.

Helpers: `latest_run_id(dry_run=False) -> int | None`; `phase_of = {"macro_sentinel": "sense", "demand_intel": "sense", "inventory_optimizer": "decide", "compliance_guardian": "decide", "distributor_selector": "resolve", "overstock_resolver": "resolve", "arbiter": "resolve", "execution_engine": "execute"}`.

CLI:

```python
@app.command()
def run(lead_mult: float = 1.0, demand_mult: float = 1.0, dry: bool = False):
    """Run the agent pipeline once."""
    import asyncio
    from sarthi.db import init_db
    from sarthi.orchestrator.runner import run_pipeline
    init_db()
    rid = asyncio.run(run_pipeline("cli", scenario={"lead_mult": lead_mult, "demand_mult": demand_mult}, dry_run=dry))
    typer.echo(f"run {rid} done")
```

In the FastAPI `lifespan`, after `init_db()` and the LLM probe: if the database has SKUs but no completed run, start `asyncio.create_task(run_pipeline("startup"))`.

## 4. Arbiter and debate — `agents/arbiter.py`, `agents/debate.py` (key `rlhfArbiter`)

The debate is a real protocol with typed messages; it is not generated prose. Its transcript is what the What-If page's Agent Debate panel shows.

For each proposal, in descending `par_rescued`:

1. **Propose.** The author states it: `bb.say(author_voice, stance="propose", **facts)`.
2. **Challenge.** Three critics evaluate it with code and speak only if they have a finding:
   - `forecaster`: is the demand behind it reliable? Objects if the SKU's `wape > 0.35`, if a cannibalization pair shows the demand is borrowed, or if a phantom-inventory flag exists. Suggestion: reduce quantity to the lower conformal bound of demand.
   - `cfoAgent` and `esgGuardian`: `compliance_guardian.review(proposal, envelope, committed_value)` verdicts (mk6 §4), each mapped to its `voice`.
   - `riskAgent`: recomputes stockout probability if the proposal is adopted; objects if it remains above `settings.stockout_alert_prob`, suggesting a faster mode or supplier.
   Each objection is `bb.say(voice, stance="object", rule=..., **message_facts)`.
3. **Revise.** The author applies `suggestion`s from `block` verdicts (and from `warn` verdicts that cost less than 5 % of `par_rescued`), recomputes `cost`, `par_rescued`, `co2_kg`, and says so (`stance="revise"`). At most two challenge/revise rounds; a proposal still blocked after two is `rejected`.
4. **Rule.** After all proposals are processed, fund purchases under the budget with `budget.fund_orders(values=utility, costs=cost, budget=envelope.budget_remaining)`, where
   `utility = par_rescued − w_cash × cost − carbon_price × co2_kg`, `w_cash` = 0.02 (Growth), 0.05 (Balanced), 0.12 (Cash Flow).
   Unfunded purchases become `rejected` with rule `BUDGET_CAP`. For each surviving proposal compute confidence and routing (§5) and `bb.say("rlhfArbiter", stance="rule", ...)`.

`debate.py` renders a debate event into one sentence. Deterministic templates exist for every `(stance, rule)` pair in English and Hindi. When the LLM is available and budget allows, lines are rephrased with `llm.text(NARRATE_DEBATE, facts=..., fallback=template_line)`; grounding guarantees the numbers survive. Only the first 8 lines of a run are sent to the LLM; the rest use templates.

Returns `{"rulings": [{proposal, status, confidence, routed, reasons: [rule names], counterfactual}]}`.

**Counterfactual explanation.** For each approved purchase, find the smallest change that would flip the decision, by re-running `simulate` (same seed) on a grid: lead-time multiplier from 1.0 down to 0.3 in steps of 0.1, and on-hand from current upward in steps of 10 % of the reorder point. Report the first value at which `inventory position ≥ reorder point` (no order needed), e.g. `{"lead_days": 8, "on_hand": 310}`. Stored in `payload.facts` and used by the explain intent (mk10): "No order would be needed if lead time were 8 days instead of 12, or if stock were 310 instead of 150."

## 5. Confidence — `learning/confidence.py`

Confidence must mean something: here it is **the probability that the decision would be the same under the uncertainty we can measure**, combined with how trustworthy the inputs are.

```python
def decision_confidence(proposal, fc: ForecastResult, sim_args: dict, data_quality: float, n_draws: int = 20) -> int
```

1. **Stability (weight 0.50).** Draw `n_draws` relative forecast errors `e` from `fc.rel_residuals_14d` (the out-of-sample 14-day errors from cross-validation, i.e. the conformal calibration set). For each, re-run `simulate` with `demand_mult × (1 + e)` at 500 paths (same seed) and re-apply the decision rule (order vs. no order; for overstock, `doc > overstock_cover_days` vs. not). `stability` = share of draws that produce the same action.
2. **Forecast reliability (weight 0.30).** `1 − min(1, fc.wape / 0.6)`.
3. **Data quality (weight 0.20).** Mean of: has ≥ 180 days of sales (0/1), supplier has ≥ 15 deliveries (0/1), stock record updated within 2 days (0/1), no phantom-inventory flag (0/1).

`confidence = round(100 × (0.5 × stability + 0.3 × reliability + 0.2 × quality))`, clipped to `[35, 99]`. `audit` proposals use `round(100 × min(1, |correlation| + 0.2))` for cannibalization and a fixed 80 for phantom inventory.

**Routing** — `route(proposal, confidence, envelope, arm) -> "auto" | "review"`. Auto only if **all** hold:

- `confidence ≥ settings.auto_confidence` (85);
- `cost ≤ settings.auto_max_order_value`;
- no `warn` verdict remained and no `no_auto` preference covers the SKU;
- the arm has **earned autonomy**: `scipy.stats.beta.ppf(0.10, arm.alpha, arm.beta) ≥ 0.60` (we are 90 % sure the manager approves this kind of action at least 60 % of the time). With the Beta(2, 2) prior this is false, so a fresh system auto-executes nothing; it takes roughly 6 straight approvals of an action type before that type can run unattended.

## 6. Learning from feedback — `learning/bandit.py`, `learning/memory.py`

**Bandit.** Arm key = `f"{zone}:{alert_type}"` (and `f"{zone}:{remedy}"` for overstock remedies, mk7 §2). `get_arm(key)` creates Beta(2, 2) on first use.

```python
def record(key: str, approved: bool) -> float      # returns the change in posterior mean
def sample(key: str, rng) -> float                 # Thompson draw
def lower_bound(key: str, q: float = 0.10) -> float
```

Approve → `alpha += 1`; dismiss → `beta += 1`. After each update write `bb.put("rlhf_weight_update", f"{delta:+.2f}", tone="money")` and an `act("rlhfArbiter", ...)` audit line on the latest run. This is a Bayesian bandit over the manager's revealed preferences; the UI's "RLHF" label refers to this loop.

**Preference memory.** When an approve/dismiss arrives with feedback text:

```python
class PreferenceOut(BaseModel):
    scope: Literal["sku", "category", "zone", "supplier", "global"]
    target: str                      # id or name; "" for global
    directive: Literal["avoid_supplier", "prefer_supplier", "cap_qty", "min_cover_days", "safety_stock_pct", "no_auto", "other"]
    value: float | None
    note: str
```

`llm.json(PreferenceOut, EXTRACT_PREFERENCE, f"Alert: {alert.msg}\nDecision: {status}\nFeedback: {text}", fallback=rule_based)`. The `rule_based` fallback handles common phrasings with regex and fuzzy matching against supplier and SKU names ("don't use X" / "avoid X" → `avoid_supplier`; "max N units" → `cap_qty`; "always ask me" → `no_auto`; "keep N days" → `min_cover_days`), else `directive="other"` with the raw text as `note`. `target` is resolved to a real id with `rapidfuzz.process.extractOne(..., score_cutoff=70)`; unresolved targets downgrade the directive to `other`. Typed directives are enforced by the Compliance Guardian on the next run; `other` notes are appended to narration prompts only.

## Verify

`uv run pytest tests/test_pipeline.py` (seeded, LLM offline, weather off):

- `run_pipeline("test")` finishes with `status="done"` and no `error` events.
- `phase` context events appear in the order sense → decide → resolve → execute.
- At least 5 open alerts exist, covering at least three zones; every alert has `35 ≤ confidence ≤ 99`, non-empty `msg`, `action`, `impact`.
- With default arms, no alert is `routed="auto"`. After calling `record(arm, True)` eight times for one arm and re-running, a qualifying alert of that arm with confidence ≥ 85 is `auto` and already `approved` with a `txid`.
- Debate events exist with stances `propose`, `object`, `rule`; every `object` event carries a `rule` name.
- Setting `monthly_budget` to ₹1,000 rejects at least one purchase with reason `BUDGET_CAP`.
- A dry run with `lead_mult=1.3` produces debate events but no new alerts or orders.
- Feedback "avoid Metro Cash & Carry" produces a `Preference(directive="avoid_supplier", target="SUP-MCC")`, and the next run proposes no order to that supplier.
- Each approved purchase has a `counterfactual` with at least one of `lead_days` / `on_hand`.

Manual: `uv run sarthi run` prints `run N done`; with LM Studio open, `Event` rows of kind `llm` show `ok` results and the alert messages read as natural sentences.

## Done when

Pipeline tests pass; a warm full run takes under 10 seconds offline and under 60 seconds with the local model.

## Implementation notes (as built, 2026-10-09)

Verified by `tests/test_pipeline.py` (24 tests) plus a live-model run on the real database. Deviations from the text above:

- **Debate rewording by the model is off by default** (`SARTHI_LLM_REWORD_DEBATE=false`). In the first live run two reworded lines changed which product an action applied to ("cap its next order" became "cap the next order for <the other product>"). The template sentences are used as they are. Alert explanations are still reworded, under a stricter guard.
- **Stricter rewrite guard**: `gateway.text(..., source=...)` rejects a rewrite unless every name appears exactly as often as in the sentence being reworded. Templates were also made unambiguous (no pronouns for which product an action applies to).
- **Model-call budget is 20 per run** (was 12): 3 emails + 7 alert explanations (+ 8 debate lines if rewording is switched on).
- **A run with any agent error is recorded as `failed`**, and `latest_run_id()` only returns `done` runs, so a broken run never replaces the last good result.
- **`start_run()` and `execute_run()`** are split out of `run_pipeline()` so a caller (mk9's SSE endpoint) can subscribe to a run's events before it starts. `run_summary(run_id)` returns what a run in this process produced.
- **State** also declares `history`, `as_of`, `alerts`, `executed`. The phase events are written once per phase (`sense`, `decide`, `resolve`, `execute`).
- **Critics.** `forecaster`: `BORROWED_DEMAND` (must revise: the order is cut by the share of demand borrowed from a sold-out substitute), `FORECAST_UNCERTAIN` (14-day error above 35 %), `PHANTOM_STOCK`. `riskAgent`: `FASTER_MODE` when a faster mode cuts stockout risk by 5 points or more, otherwise `RESIDUAL_RISK`. `cfoAgent` / `esgGuardian`: the Compliance Guardian's verdicts.
- **Compliance changes.** The air-share limit applies only from the fifth order (a share over two orders means nothing). "Air is not needed" is its own rule, `AIR_UNNEEDED`, and when the shipping options are costed it fires only if the slower mode is no worse in total (freight + priced carbon + stockout exposure).
- **Revisions**: quantity, shipping mode and transfer units are revised in place. A different supplier cannot be patched in here, so a proposal blocked on its supplier is rejected.
- **Ruling.** Purchases compete for the budget on `par_rescued - w_cash x cost - carbon_price x co2`. Transfers and campaigns need positive net value (`NO_NET_BENEFIT` otherwise). Audits are always approved.
- **Confidence stability for a purchase** = share of 20 plausible forecast errors under which an order within 25 % of the proposed size is still needed (stricter than "an order is still needed", which was always 100 % for at-risk SKUs and gave no spread). Seeded data gives 47-98 % across the seven alerts.
- **Data freshness is measured**: the stock record counts as fresh only if the data's last day is within 2 days of today.
- **Counterfactual**: `on_hand` is exact (the reorder point less stock on order). `lead_days` is reported only if some lead time on the grid removes the need to order; on the seeded data none does.
- **Learning.** `learning/memory.py`: `rule_based()` handles common feedback phrasings without the model; `to_preference()` attaches a rule to a real supplier or SKU (defaulting to the alert's SKU), or keeps it as a note; `learn_from_decision()` is the entry point for mk9's approve / dismiss endpoints. `safety_stock_pct` is honoured by the Inventory Optimizer. `min_cover_days` is stored and reaches the envelope but nothing enforces it yet.
- **Earned autonomy** needs about 6 straight approvals of one action type in one zone; two dismissals lose it again.
- **Server startup** runs the pipeline once when there is data but no completed run (`SARTHI_SKIP_STARTUP_RUN=true` turns this off; tests set it).
- **Measured** (mains power): full pipeline 26 s cold and 1.6 s warm inside one process; a first run with the live model 54 s including start-up. Each `sarthi run` is a new process, so it refits the forecasts (about 30 s); inside the server repeat runs are warm.
