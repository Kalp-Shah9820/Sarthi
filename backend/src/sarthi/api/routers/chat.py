"""The Intelligence chat, strategy, voice commands and per-product explanations."""

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from sarthi.api import presenters
from sarthi.api.routers import runs
from sarthi.llm import get_llm
from sarthi.nlq import explain as explainer
from sarthi.nlq import router as nlq_router
from sarthi.nlq import strategy, voice

router = APIRouter(tags=["chat"])


class Said(BaseModel):
    text: str = Field(min_length=1, max_length=nlq_router.MAX_TEXT)
    lang: str = "EN"


class StrategyIn(BaseModel):
    """`{mode}` is enough; the other fields the UI context sends are accepted and replaced by the mode's preset."""
    model_config = ConfigDict(extra="ignore")
    mode: Literal["Balanced", "Cash Flow", "Growth"]
    horizonDays: int = Field(default=strategy.DEFAULT_HORIZON, ge=1, le=strategy.MAX_HORIZON)


@router.post("/chat")
async def chat(body: Said) -> dict:
    """Answer one question from live data. The reply is a translation key with parameters, or text (English or Hindi)."""
    return await nlq_router.answer(body.text, body.lang, get_llm())


@router.get("/strategy")
def get_strategy() -> dict:
    """The strategy in force: {mode, savingsPriority, safetyStockMultiplier, leadTimeBuffer, lastUpdate}."""
    return presenters.strategy_view()


@router.put("/strategy")
async def put_strategy(body: StrategyIn) -> dict:
    """Switch strategy mode. The change is stored at once and a pipeline run puts it into effect."""
    strategy.apply_policy(strategy.PolicyOut(mode=body.mode, horizon_days=body.horizonDays), "set from the app")
    presenters.bump()
    runs.launch("strategy", queue=True)
    return presenters.strategy_view()


@router.post("/voice/intent")
async def voice_intent(body: Said) -> dict:
    """Turn a spoken sentence into the command object the app already understands."""
    return await voice.interpret(body.text, body.lang, get_llm())


@router.get("/skus/{sku_id}/explain")
async def explain_sku(sku_id: str, lang: str = "EN") -> dict:
    """Why this product is in its zone and what is recommended: {text, facts, source}."""
    try:
        return await explainer.explain(sku_id.upper(), lang, get_llm())
    except explainer.NoSuchSku as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except explainer.NotReady as exc:
        raise HTTPException(status_code=503, detail="warming up") from exc
