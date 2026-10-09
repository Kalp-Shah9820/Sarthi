"""Starting a pipeline run and watching it."""

import asyncio
import logging

from fastapi import APIRouter, HTTPException

from sarthi.api import presenters
from sarthi.api.sse import stream_run
from sarthi.db import session
from sarthi.models import Run
from sarthi.orchestrator.runner import execute_run, start_run

router = APIRouter(tags=["runs"])
log = logging.getLogger("sarthi.api")

STREAMED = {"context", "action", "debate", "error"}
_current: tuple[int, asyncio.Task] | None = None      # the sync run in progress, if any


async def guarded(run_id: int, lang: str = "EN") -> None:
    """Run in the background; a failure is recorded on the run and logged, never raised into the event loop."""
    try:
        await execute_run(run_id, lang=lang)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("run %s failed", run_id)
    finally:
        presenters.bump()


@router.post("/runs")
async def create_run() -> dict:
    """Start the agent pipeline. Asking again while it is running returns the run already in progress."""
    global _current
    if _current is not None and not _current[1].done():
        return {"runId": _current[0]}
    run_id = await asyncio.to_thread(start_run, "sync")
    _current = (run_id, asyncio.create_task(guarded(run_id)))
    return {"runId": run_id}


@router.get("/runs/{run_id}/stream")
async def run_stream(run_id: int):
    """Server-sent events: what the agents share, do and say during this run, ending with `event: done`."""
    with session() as s:
        if s.get(Run, run_id) is None:
            raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return stream_run(run_id, STREAMED, presenters.event_view)
