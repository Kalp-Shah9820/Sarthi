"""The What-If page: instant risk numbers for the sliders, and the agents debating the scenario."""

import asyncio
from typing import Annotated

import numpy as np
from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete
from sqlmodel import select

from sarthi.analytics import whatif
from sarthi.api import presenters
from sarthi.api.routers.runs import guarded
from sarthi.api.sse import stream_run
from sarthi.db import session
from sarthi.models import Event, Run
from sarthi.orchestrator.runner import start_run

router = APIRouter(tags=["sandbox"])

DEFAULT_LEAD, DEFAULT_DEMAND = 5.0, 50.0      # the sliders' starting positions, i.e. "no change"
MULT_RANGE = (0.5, 3.0)
TRIGGER = "sandbox"

Lead = Annotated[float, Field(ge=1, le=60)]
Demand = Annotated[float, Field(ge=1, le=10_000)]
Stock = Annotated[float, Field(ge=0, le=1_000_000)]
Margin = Annotated[float, Field(ge=0, le=100)]

_task: asyncio.Task | None = None       # the what-if debate in progress; a new request replaces it


class Scenario(BaseModel):
    lead: Lead
    demand: Demand
    stock: Stock
    margin: Margin


@router.post("/sandbox/simulate")
def simulate(body: Scenario) -> dict:
    """Stockout risk, profit at risk and safety stock for the slider values. Computes only; stores nothing."""
    k, ref_price = presenters.catalogue_stats()
    return whatif.simulate(body.lead, body.demand, body.stock, body.margin, k, ref_price)


def _forget_finished() -> None:
    """What-if runs are throwaway: clear the events of finished ones so they do not pile up.

    The run rows stay. They are tiny, and deleting them would let the database hand the same run id
    to a later run.
    """
    with session() as s:
        finished = select(Run.id).where(Run.trigger == TRIGGER, Run.dry_run, Run.status != "running")
        s.connection().execute(delete(Event).where(Event.run_id.in_(finished)))
        s.commit()


@router.get("/sandbox/debate/stream")
async def debate_stream(lead: Annotated[float, Query(ge=1, le=60)] = DEFAULT_LEAD,
                        demand: Annotated[float, Query(ge=1, le=10_000)] = DEFAULT_DEMAND,
                        stock: Annotated[float, Query(ge=0)] = 300, margin: Annotated[float, Query(ge=0, le=100)] = 20,
                        lang: str = "EN"):
    """Server-sent events: the agents debate the scenario on a dry run (nothing is stored or executed).

    Each message is `{agentKey, tone, msg}`; the stream ends with `event: done`. The lead-time and demand
    sliders scale the whole store's lead times and demand relative to their starting positions.
    """
    global _task
    if _task is not None and not _task.done():
        _task.cancel()
    await asyncio.to_thread(_forget_finished)
    scenario = {"lead_mult": float(np.clip(lead / DEFAULT_LEAD, *MULT_RANGE)),
                "demand_mult": float(np.clip(demand / DEFAULT_DEMAND, *MULT_RANGE))}
    lang = lang.upper() if lang.upper() in ("EN", "HI") else "EN"
    run_id = await asyncio.to_thread(lambda: start_run(TRIGGER, scenario=scenario, dry_run=True))

    def stop() -> None:                 # the listener left: no one is waiting for the rest
        if task is not None and not task.done():
            task.cancel()

    task = None
    response = stream_run(run_id, {"debate"}, lambda event: presenters.debate_line(event, lang), on_close=stop)
    task = _task = asyncio.create_task(guarded(run_id, lang))       # started after subscribing, so no line is missed
    return response
