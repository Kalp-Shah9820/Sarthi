"""Strategy injection: a typed sentence becomes a stored, expiring policy that changes real thresholds."""

import re
from datetime import timedelta
from typing import Literal

from pydantic import BaseModel
from sqlmodel import select

from sarthi.blackboard.store import Blackboard
from sarthi.db import session
from sarthi.llm import get_llm, prompts
from sarthi.models import Preference, StrategyPolicy, local_today
from sarthi.orchestrator.runner import latest_run_id

PRESETS = {   # identical to the values the UI sets today
    "Balanced": {"savingsPriority": 0.5, "safetyStockMultiplier": 1.0, "leadTimeBuffer": 1.2},
    "Cash Flow": {"savingsPriority": 0.9, "safetyStockMultiplier": 0.8, "leadTimeBuffer": 1.2},
    "Growth": {"savingsPriority": 0.2, "safetyStockMultiplier": 1.5, "leadTimeBuffer": 1.5},
}
MODE_WORDS = (      # checked in this order: "cash flow over growth" is Cash Flow
    ("Cash Flow", ("cash", "saving", "कैश", "बचत", "नकदी")),
    ("Growth", ("growth", "aggressive", "विकास", "ग्रोथ")),
    ("Balanced", ("balanced", "balance", "normal", "reset", "default", "संतुलित", "सामान्य")),
)
HORIZON = re.compile(r"(\d+)\s*(day|days|दिन|week|weeks|हफ्ते|सप्ताह|month|months|महीने)")
UNIT_DAYS = {"day": 1, "days": 1, "दिन": 1, "week": 7, "weeks": 7, "हफ्ते": 7, "सप्ताह": 7, "month": 30, "months": 30, "महीने": 30}
DEFAULT_HORIZON, MAX_HORIZON = 30, 180
PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*(%|percent|per cent|प्रतिशत)")
DEFAULT_SAFETY_PCT, MAX_SAFETY_PCT = 20.0, 100.0


class PolicyOut(BaseModel):
    mode: Literal["Balanced", "Cash Flow", "Growth"]
    horizon_days: int = DEFAULT_HORIZON


def mode_from_words(text: str) -> str | None:
    low = text.lower()
    return next((mode for mode, words in MODE_WORDS if any(w in low for w in words)), None)


def horizon_from_words(text: str) -> int | None:
    m = HORIZON.search(text.lower())
    return int(m.group(1)) * UNIT_DAYS[m.group(2)] if m else None


def _clamp(days: int) -> int:
    return max(1, min(MAX_HORIZON, int(days)))


async def compile_policy(text: str, llm=None, hint: str = "") -> PolicyOut | None:
    """The mode and duration a sentence asks for. Rules first; the model only when no mode word is found.

    `hint` is a mode already extracted from the sentence. Returns None when no mode can be told.
    """
    horizon = horizon_from_words(text)
    mode = mode_from_words(text) or (hint if hint in PRESETS else None)
    if mode is None:
        llm = llm or get_llm()
        guess = await llm.json(PolicyOut, prompts.COMPILE_STRATEGY, text, fallback=lambda: None, task="strategy",
                               max_tokens=80)
        if guess is None:
            return None
        mode, horizon = guess.mode, horizon or guess.horizon_days       # a duration the manager typed wins
    return PolicyOut(mode=mode, horizon_days=_clamp(horizon or DEFAULT_HORIZON))


def apply_policy(policy: PolicyOut, source_text: str = "") -> dict:
    """Make `policy` the strategy in force. Returns {mode, savingsPriority, safetyStockMultiplier, leadTimeBuffer, expiresOn}.

    The caller starts the pipeline run that puts it into effect.
    """
    expires = None if policy.mode == "Balanced" else local_today() + timedelta(days=_clamp(policy.horizon_days))
    with session() as s:
        for old in s.exec(select(StrategyPolicy).where(StrategyPolicy.active)).all():
            old.active = False
            s.add(old)
        s.add(StrategyPolicy(mode=policy.mode, params=dict(PRESETS[policy.mode]), source_text=source_text[:300],
                             expires_on=expires, active=True))
        s.commit()
    run_id = latest_run_id()
    if run_id is not None:
        Blackboard(run_id).act("rlhfArbiter", "execute", f"Strategy set to {policy.mode}",
                               result=f"expires {expires.isoformat()}" if expires else "no expiry", mode=policy.mode)
    return {"mode": policy.mode, **PRESETS[policy.mode], "expiresOn": expires.isoformat() if expires else None}


def percent_from_words(text: str) -> float:
    m = PERCENT.search(text.lower())
    return max(1.0, min(MAX_SAFETY_PCT, float(m.group(1)))) if m else DEFAULT_SAFETY_PCT


def adjust_safety_stock(sku_id: str, percent: float, source_text: str = "") -> float:
    """Raise one product's safety stock by `percent` from the next run on. Replaces an earlier adjustment."""
    percent = max(1.0, min(MAX_SAFETY_PCT, float(percent)))
    with session() as s:
        for old in s.exec(select(Preference).where(Preference.directive == "safety_stock_pct", Preference.target == sku_id,
                                                   Preference.active)).all():
            old.active = False
            s.add(old)
        s.add(Preference(scope="sku", target=sku_id, directive="safety_stock_pct", value=percent, note=source_text[:300]))
        s.commit()
    return percent
