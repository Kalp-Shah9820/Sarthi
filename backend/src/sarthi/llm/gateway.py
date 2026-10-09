"""The only door to the language model.

Rules that follow from running a small model on one local GPU:
- one request at a time (a local server gains nothing from parallel calls);
- every call has a caller-supplied fallback, so a timeout, a closed LM Studio, invalid JSON or an
  invented number never blocks or breaks a run;
- identical prompts are answered from a cache, so repeated runs and demos are instant;
- a pipeline run may make only a limited number of uncached calls.
"""

import asyncio
import hashlib
import json
import re
from collections.abc import Callable

import httpx
from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError
from sqlmodel import select

from sarthi.blackboard.store import write_event
from sarthi.db import session
from sarthi.llm.grounding import grounded
from sarthi.models import LlmCache

THINK = re.compile(r"<think>.*?</think>", re.DOTALL)
TEMPERATURE = 0.2


def cache_get(key: str) -> str | None:
    with session() as s:
        row = s.get(LlmCache, key)
        return row.response if row else None


def cache_put(key: str, response: str) -> None:
    with session() as s:
        s.merge(LlmCache(key=key, response=response))
        s.commit()


def cache_size() -> int:
    with session() as s:
        return len(s.exec(select(LlmCache.key)).all())


class LlmGateway:
    def __init__(self, settings):
        self.s = settings
        self.mode = "offline"   # "llm" once LM Studio has answered and lists the configured model
        self.client = AsyncOpenAI(base_url=settings.llm_base_url, api_key="lm-studio",
                                  timeout=settings.llm_timeout_s, max_retries=0)
        self._sem = asyncio.Semaphore(1)
        self._calls: dict[int, int] = {}

    async def probe(self) -> str:
        """Set and return the mode: 'llm' if LM Studio answers and lists the configured model, else 'offline'."""
        if not self.s.llm_enabled:
            self.mode = "offline"
            return self.mode
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                response = await client.get(self.s.llm_base_url.rstrip("/") + "/models")
            ids = [m["id"] for m in response.json().get("data", [])]
            self.mode = "llm" if self.s.llm_model in ids else "offline"
        except Exception:  # noqa: BLE001  unreachable, refused, bad JSON: all mean "work without the model"
            self.mode = "offline"
        return self.mode

    def calls(self, run_id: int) -> int:
        """Uncached model calls made so far for this run."""
        return self._calls.get(run_id, 0)

    def _spend(self, run_id: int | None) -> bool:
        """Count one uncached call against the run's budget; False when the budget is used up."""
        if run_id is None:      # chat requests are not part of a pipeline run
            return True
        if self._calls.get(run_id, 0) >= self.s.llm_max_calls_per_run:
            return False
        self._calls[run_id] = self._calls.get(run_id, 0) + 1
        return True

    def _log(self, run_id: int | None, outcome: str, task: str) -> None:
        if run_id is not None:
            write_event(run_id, kind="llm", agent="llmGateway", key=task, value={"result": outcome, "model": self.s.llm_model})

    async def _chat(self, system: str, user: str, *, max_tokens: int, response_format: dict | None = None,
                    run_id: int | None = None, use_cache: bool = True) -> str | None:
        """One completion, or None on any failure. Cached answers are served even when the model is offline."""
        key = hashlib.sha256(
            json.dumps([self.s.llm_model, system, user, response_format], sort_keys=True, default=str).encode()
        ).hexdigest()
        if use_cache and (hit := cache_get(key)) is not None:
            return hit
        if self.mode != "llm" or not self._spend(run_id):
            return None
        extra = {"response_format": response_format} if response_format else {}
        try:
            async with self._sem:
                response = await self.client.chat.completions.create(
                    model=self.s.llm_model, temperature=TEMPERATURE,
                    max_tokens=max_tokens + self.s.llm_reasoning_headroom,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}], **extra)
            text = THINK.sub("", response.choices[0].message.content or "").strip()
        except Exception:  # noqa: BLE001  timeout, connection reset, malformed reply: the caller's fallback applies
            return None
        if not text:            # a reasoning model spent the whole budget before answering
            return None
        if use_cache:
            cache_put(key, text)
        return text

    async def raw_reply(self, system: str, user: str, *, max_tokens: int = 120) -> str | None:
        """One uncached, unchecked completion. For diagnostics (the validator); agents use `json` and `text`."""
        return await self._chat(system, user, max_tokens=max_tokens, use_cache=False)

    async def json(self, schema: type[BaseModel], system: str, user: str, *, fallback: Callable[[], BaseModel],
                   run_id: int | None = None, max_tokens: int = 400, task: str = "json") -> BaseModel:
        """Structured output validated against `schema`. Returns `fallback()` on any failure."""
        fmt = {"type": "json_schema",
               "json_schema": {"name": schema.__name__, "strict": True, "schema": schema.model_json_schema()}}
        prompt = user
        for _ in range(2):      # one repair attempt
            raw = await self._chat(system, prompt, max_tokens=max_tokens, response_format=fmt, run_id=run_id)
            if raw is None:
                break
            try:
                parsed = schema.model_validate_json(raw)
            except ValidationError as exc:
                prompt = f"{user}\n\nYour previous reply was invalid: {str(exc)[:300]}\nReply with valid JSON only."
                continue
            self._log(run_id, "ok", task)
            return parsed
        self._log(run_id, "fallback", task)
        return fallback()

    async def text(self, system: str, user: str, *, facts: dict, fallback: str, run_id: int | None = None,
                   max_tokens: int = 220, task: str = "text", must_contain: list[str] | None = None,
                   max_chars: int | None = None) -> tuple[str, str]:
        """Free text that may only use numbers from `facts`. Returns (text, 'llm' | 'template').

        `must_contain`: names that have to survive in the reply (a rewrite that drops a supplier or product
        name is rejected). `max_chars`: upper bound on length (a rewrite that balloons has added something).
        """
        raw = await self._chat(system, user, max_tokens=max_tokens, run_id=run_id)
        if raw is None:
            self._log(run_id, "fallback", task)
            return fallback, "template"
        if not grounded(raw, facts):
            self._log(run_id, "ungrounded", task)
            return fallback, "template"
        lowered = raw.lower()
        if any(name.lower() not in lowered for name in must_contain or []) or (max_chars is not None and len(raw) > max_chars):
            self._log(run_id, "off_template", task)
            return fallback, "template"
        self._log(run_id, "ok", task)
        return raw, "llm"
