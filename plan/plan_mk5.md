# MK5 — Blackboard, LLM gateway, grounding

**Goal:** the three pieces of shared infrastructure every agent uses: an append-only event log that doubles as shared memory, a client for the local model that can never block or break a run, and a check that stops the model from inventing numbers.

## 1. Blackboard — `src/sarthi/blackboard/store.py`

Agents do not call each other. They write typed facts to the blackboard and read what others wrote. Every write is one `Event` row, so the same table provides the Shared Context panel (latest value per key), the XAI audit trail (ordered `action` events), the agent debate (ordered `debate` events), and replay for debugging.

Event kinds:

| `kind` | Meaning | Shown in |
|---|---|---|
| `context` | key/value fact, e.g. `lead_time_risk = HIGH` | Shared Context Store |
| `action` | something an agent did, with a result line | XAI Audit Trail |
| `debate` | one line of proposal/objection/ruling | Agent Debate panel |
| `metric` | per-SKU numbers (not shown directly) | snapshots |
| `llm` | model call outcome (`ok`, `fallback`, `ungrounded`) | diagnostics |
| `error` | caught agent failure | diagnostics |

```python
class Blackboard:
    def __init__(self, run_id: int): ...
    def put(self, agent: str, phase: str, key: str, value, *, tone: str = "ghost") -> None
        # kind="context"; value stored as {"v": value, "tone": tone}; tone is a zone colour name
    def act(self, agent: str, phase: str, text: str, result: str = "", sku_id: str | None = None, **facts) -> None
        # kind="action"; value={"result": result, "facts": facts}
    def say(self, agent: str, text: str, *, stance: str, sku_id: str | None = None, **facts) -> None
        # kind="debate"; stance in {"propose","object","revise","rule","execute"}
    def metric(self, agent: str, sku_id: str, **values) -> None
    def error(self, agent: str, exc: Exception) -> None
    def get(self, key: str, default=None)            # latest context value for this run
    def context(self) -> list[dict]                  # latest value per key, newest first
    def events(self, kind: str | None = None, since_id: int = 0) -> list[Event]
```

Implementation notes:

- Each method opens a short `session()`, inserts, commits. SQLite in WAL mode handles the parallel SENSE branch safely.
- An in-process `asyncio.Queue` per run (`_subscribers: dict[int, list[asyncio.Queue]]`) receives a copy of every event so SSE endpoints (mk9) can stream without polling. `subscribe(run_id) -> Queue`, `unsubscribe(run_id, q)`. Publishing uses `loop.call_soon_threadsafe(q.put_nowait, event_dict)` so writes from worker threads are safe.
- `ts` is stored in UTC; presenters format `HH:MM:SS` in local time.

Standard context keys (the UI already has labels for these): `lead_time_risk`, `demand_signal`, `snacks_cannibalization`, `par_total`, `esg_preference`, `supplier_primary`, `ghost_transfer_ready`, `rlhf_weight_update`.

## 2. LLM gateway — `src/sarthi/llm/gateway.py`

One class owns all model traffic. Design rules that follow from running a small model on one GPU:

- **One request at a time** (`asyncio.Semaphore(1)`); a local server gains nothing from parallel calls.
- **Every call has a fallback** supplied by the caller. Timeouts, connection errors, invalid JSON and failed grounding all return the fallback.
- **Cache** identical prompts in `LlmCache`, so repeated runs and demos are instant.
- **Budget**: at most `max_calls_per_run = 12` uncached calls per pipeline run; beyond that, fallbacks are used directly.

```python
import asyncio, hashlib, json, re
import httpx
from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

THINK = re.compile(r"<think>.*?</think>", re.S)


class LlmGateway:
    def __init__(self, settings):
        self.s = settings
        self.mode = "offline"
        self.client = AsyncOpenAI(base_url=settings.llm_base_url, api_key="lm-studio",
                                  timeout=settings.llm_timeout_s, max_retries=0)
        self._sem = asyncio.Semaphore(1)
        self._calls: dict[int, int] = {}

    async def probe(self) -> str:
        """Set self.mode to 'llm' if LM Studio answers and the configured model is listed."""
        if not self.s.llm_enabled:
            self.mode = "offline"; return self.mode
        try:
            async with httpx.AsyncClient(timeout=3) as c:
                r = await c.get(self.s.llm_base_url.rstrip("/") + "/models")
            ids = [m["id"] for m in r.json().get("data", [])]
            self.mode = "llm" if self.s.llm_model in ids else "offline"
        except Exception:
            self.mode = "offline"
        return self.mode

    async def _chat(self, system: str, user: str, *, max_tokens: int, response_format=None) -> str | None:
        key = hashlib.sha256(json.dumps([self.s.llm_model, system, user, response_format], sort_keys=True,
                                        default=str).encode()).hexdigest()
        if (hit := cache_get(key)) is not None:
            return hit
        if self.mode != "llm":
            return None
        kwargs = {"response_format": response_format} if response_format else {}
        try:
            async with self._sem:
                r = await self.client.chat.completions.create(
                    model=self.s.llm_model, temperature=0.2,
                    max_tokens=max_tokens + self.s.llm_reasoning_headroom,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}], **kwargs)
            text = THINK.sub("", r.choices[0].message.content or "").strip()
        except Exception:
            return None
        if not text:          # reasoning model used the whole budget before answering: treat as a failure
            return None
        cache_put(key, text)
        return text

    async def json(self, schema: type[BaseModel], system: str, user: str, *, fallback, run_id: int | None = None,
                   max_tokens: int = 400) -> BaseModel:
        """Structured output. Returns fallback() on any failure."""
        if not self._within_budget(run_id):
            return fallback()
        fmt = {"type": "json_schema",
               "json_schema": {"name": schema.__name__, "strict": True, "schema": schema.model_json_schema()}}
        for attempt in range(2):
            raw = await self._chat(system, user, max_tokens=max_tokens, response_format=fmt)
            if raw is None:
                break
            try:
                return schema.model_validate_json(raw)
            except ValidationError as e:
                user = f"{user}\n\nYour previous reply was invalid: {str(e)[:300]}\nReply with valid JSON only."
        return fallback()

    async def text(self, system: str, user: str, *, facts: dict, fallback: str, run_id: int | None = None,
                   max_tokens: int = 220) -> tuple[str, str]:
        """Free text grounded in `facts`. Returns (text, source) with source in {'llm','template'}."""
        if not self._within_budget(run_id):
            return fallback, "template"
        raw = await self._chat(system, user, max_tokens=max_tokens)
        if raw and grounded(raw, facts):
            return raw, "llm"
        return fallback, "template"
```

`cache_get` / `cache_put` read and write `LlmCache`. `_within_budget(run_id)` increments `self._calls[run_id]` and returns `False` past `max_calls_per_run` (always `True` when `run_id is None`, i.e. chat requests).

Notes:

- LM Studio accepts `response_format` of type `json_schema` and constrains decoding to it. Pydantic validation plus one repair attempt covers the remaining failure modes.
- LM Studio returns a reasoning model's hidden reasoning in a separate `reasoning_content` field and leaves `content` empty until the reasoning ends. The gateway reads only `content`, never caches an empty reply, and adds `llm_reasoning_headroom` to every token cap (0 by default; see mk1 §4 for the measured cost of reasoning models).
- Keep schemas flat and small (enums, numbers, short strings). Small models degrade quickly on nested or optional-heavy schemas.
- Create one gateway in the FastAPI `lifespan`: `app.state.llm = LlmGateway(get_settings()); app.state.llm_mode = await app.state.llm.probe()`. Expose `get_llm()` in `sarthi/llm/__init__.py` returning the singleton for non-HTTP callers (CLI, tests).
- Re-probe on every pipeline run start, so opening or closing LM Studio takes effect without restarting the server.

## 3. Prompts and templates

`llm/prompts.py` holds the system prompts as constants. Shared preamble:

```
You are a component of Sarthi, an inventory planning system.
Use ONLY the facts provided. Never state a number that is not in the facts.
Write plainly. No greetings, no markdown, no emojis.
```

Task prompts (each one sentence of instruction after the preamble): `NARRATE_ALERT` (≤ 2 sentences explaining why the action is recommended), `NARRATE_DEBATE` (one sentence in the voice of the named agent), `DRAFT_EMAIL` (subject line then ≤ 120-word body), `EXPLAIN_SKU` (≤ 4 sentences), `EXTRACT_INTENT`, `COMPILE_STRATEGY`, `EXTRACT_PREFERENCE`, `CLASSIFY_NEWS`. Facts are passed in the user message as a compact JSON object followed by `Language: English` or `Language: Hindi`.

`llm/templates.py` holds the deterministic fallbacks as Python format strings in English and Hindi, keyed by the same task names and alert types, e.g.:

```python
ALERT = {
  "stockout_reorder": {
    "EN": {"msg": "{doc} days of cover against a {lead}-day lead time. Stockout probability {prob}%.",
           "action": "Approve PO: {qty} units via {supplier}",
           "impact": "₹{par_k}K profit at risk"},
    "HI": {...},
  },
  "transfer": {...}, "markdown": {...}, "supplier_switch": {...},
  "cannibalization": {...}, "phantom_inventory": {...}, "expiry_risk": {...},
}
def render(task: str, kind: str, lang: str, **facts) -> dict[str, str]   # falls back to EN
```

Templates are the source of truth; the model only rephrases `msg`, and only when the language is English. Hindi text always uses the Hindi templates: tested on 2026-10-07, `qwen/qwen3-4b-2507` kept the numbers right in Hindi but produced unnatural phrasing. Callers enforce this by passing `fallback` straight through (skipping `llm.text`) when `lang != "EN"`. `action` and `impact` are **always** templates, because they sit on buttons and totals.

## 4. Grounding check — `src/sarthi/llm/grounding.py`

```python
NUM = re.compile(r"(?<![A-Za-z])[-+]?\d[\d,]*\.?\d*")

def _flatten(facts) -> list[float]            # every int/float in nested dicts/lists; numeric strings too
def _variants(x: float) -> set[float]:
    # the value itself, rounded to 0/1/2 dp, as a percentage (x*100) if 0<x<=1,
    # in thousands (x/1000) and lakhs (x/100000) if x>=1000
def grounded(text: str, facts: dict, tol: float = 0.011) -> bool
```

Algorithm:

1. Mask entity strings that legitimately contain digits: every string value in `facts` (SKU names such as "Amul Butter 500g", ids such as "SKU003", "PO-…") is removed from `text` before scanning.
2. Extract all numbers with `NUM` (strip thousands separators).
3. Each extracted number must match some variant of some fact within relative tolerance `tol`, or be one of the always-allowed small integers `{0, 1, 2, 3, 4}` when they are not followed by `%`, `₹`, "days" or "units" (ordinals and counts like "two suppliers" written as digits).
4. Return `False` on the first unmatched number. Also return `False` if the text is empty or longer than 900 characters.

On failure the gateway writes an `llm` event with result `ungrounded` and uses the template. This check is intentionally strict: a rejected good sentence costs nothing, an accepted wrong number costs trust.

## 5. Agent base class — `src/sarthi/agents/base.py`

```python
class Agent:
    key: str            # i18n key, e.g. "macroSentinel"
    phase: str          # "sense" | "decide" | "resolve" | "execute"

    def __init__(self, bb: Blackboard, llm: LlmGateway, settings, strategy: dict): ...

    async def run(self, state: dict) -> dict:
        try:
            return await self.work(state)
        except Exception as exc:                 # one agent failing must not kill the run
            self.bb.error(self.key, exc)
            return {"errors": [f"{self.key}: {exc}"]}

    async def work(self, state: dict) -> dict: raise NotImplementedError
```

CPU-heavy analytics inside `work` are run with `await asyncio.to_thread(fn, ...)` so the event loop (and SSE streams) stay responsive.

## Verify

`uv run pytest tests/test_blackboard.py tests/test_llm.py tests/test_grounding.py`:

- **blackboard**: `put` twice with the same key → `context()` returns one entry with the newer value; `events(kind="action")` preserves order; a subscriber queue receives each event once.
- **gateway (offline)**: with `llm_enabled=False`, `json(...)` returns the fallback object and `text(...)` returns `(fallback, "template")` without any network call.
- **gateway (mocked)**: monkeypatch `_chat` to return invalid JSON then valid JSON → `json` returns the parsed model on the second attempt; to return `None` → fallback; a second identical call is served from `LlmCache`.
- **grounding**: with facts `{"prob": 0.84, "qty": 1482, "par": 18500, "name": "Lays Classic 26g"}` the sentences "84% stockout risk on Lays Classic 26g; order 1,482 units to protect ₹18.5K" → `True`; "order 1,500 units" → `False`; "92% risk" → `False`.

Live check, with LM Studio running:

```powershell
uv run python -c "import asyncio; from sarthi.config import get_settings; from sarthi.llm.gateway import LlmGateway; g=LlmGateway(get_settings()); print(asyncio.run(g.probe()))"
# llm
```

## Done when

Tests pass, and the live probe prints `llm` with LM Studio open and `offline` with it closed.

## Implementation notes (as built, 2026-10-08)

Deviations from the text above, verified by `tests/test_blackboard.py`, `tests/test_llm.py`, `tests/test_grounding.py` (55 tests, plus one live-model test):

- **Budget counts only real model calls.** A cached answer is free and is served even when LM Studio is closed, so a demo keeps its wording offline. `llm_max_calls_per_run` (default 12) is a setting.
- **Gateway methods take `task=`** (a short label) and write an `llm` event per call with result `ok`, `fallback` or `ungrounded` when a `run_id` is given.
- **`raw_reply()`** makes one uncached, unchecked call for diagnostics; the validator uses it for its live check.
- **`/api/health` re-probes on every call**, so opening or closing LM Studio shows up immediately as `"llm"` or `"offline"`.
- **Blackboard extras:** `metric(agent, sku_id=None, key=None, **values)`, `metrics(key=, sku_id=)`, `end()`, and module functions `subscribe`, `unsubscribe`, `publish`, `end_run`, `write_event`, `delete_run_events`, `local_time`. `say()` rejects unknown stances.
- **Grounding** also treats `K`, `lakh`, `crore`, `percent` after a number and a currency sign before it as units, so "costs Rs 4" must be a fact while "4 suppliers" need not be. The tolerance is 1.1 %: 250 for a true 247.3 passes, 255 does not. Numbers written as words ("two") are not checked.
- **Templates** cover the 7 alert types and the opening supplier email, in English and Hindi. Debate-line templates are written with the Arbiter in mk8, where the rule names are defined. `templates.fields()` lists the facts a template needs.
- **Timestamps:** `local_time()` treats a stored time without timezone as UTC.
- **Forecast speed check moved out of pytest.** `forecast_all` took 22-32 s on mains power and 74-230 s on battery (the CPU is throttled). The unit test no longer asserts a wall-clock limit; `uv run sarthi validate --perf` reports the time against the 60 s budget as a warning.
- **Live-model test** runs only with `SARTHI_TEST_LLM=1`; otherwise it is skipped (1 skipped in the normal suite).

- **Update 2026-10-09 (mk7):** `NARRATE_ALERT` and `NARRATE_DEBATE` were replaced by `REPHRASE`. The model rewords a correct template sentence instead of explaining raw facts; see the mk7 implementation notes for why. `gateway.text` gained `must_contain` and `max_chars`.
