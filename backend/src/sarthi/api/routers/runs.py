"""Starting a pipeline run and watching it."""

import asyncio
import logging

import anyio
from fastapi import APIRouter, HTTPException

from sarthi.api import presenters
from sarthi.api.sse import stream_run
from sarthi.config import get_settings
from sarthi.db import session
from sarthi.models import Run
from sarthi.orchestrator.runner import execute_run, start_run

router = APIRouter(tags=["runs"])
log = logging.getLogger("sarthi.api")

STREAMED = {"context", "action", "debate", "error"}
_current: tuple[int, asyncio.Task] | None = None      # the latest run started from here, if any
_tasks: set[asyncio.Task] = set()                     # keeps queued runs alive until they finish
_pending: tuple[asyncio.AbstractEventLoop, asyncio.TimerHandle] | None = None    # a follow-up run about to start
RERUN_DELAY_S = 1.0                                   # several clicks in a row share one follow-up run


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


def launch(trigger: str, *, lang: str = "EN", queue: bool = False) -> int:
    """Start a pipeline run in the background and return its id. Call from inside the event loop.

    While a run is in progress the default is to return that run. `queue=True` always starts a new one,
    which waits its turn: used when something the runs depend on (the strategy, a rule) has just changed.
    """
    global _current
    if not queue and _current is not None and not _current[1].done():
        return _current[0]
    run_id = start_run(trigger)
    task = asyncio.create_task(guarded(run_id, lang))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    _current = (run_id, task)
    return run_id


def _fire(trigger: str) -> None:
    global _pending
    _pending = None
    launch(trigger, queue=True)


def launch_soon(trigger: str) -> None:
    """Start a run shortly, unless one is already about to start. Call from inside the event loop."""
    global _pending
    loop = asyncio.get_running_loop()
    if _pending is not None and _pending[0] is loop and not _pending[1].cancelled():
        return
    _pending = (loop, loop.call_later(RERUN_DELAY_S, _fire, trigger))


def rerun_after_action(trigger: str = "action") -> bool:
    """After something that changed stock or orders: run the agents again so every screen reflects it.

    For endpoints that run in a worker thread. Returns whether a run was scheduled.
    """
    if not get_settings().rerun_after_action:
        return False
    try:
        anyio.from_thread.run_sync(launch_soon, trigger)
    except RuntimeError:            # not inside a request (e.g. called from a script): nothing to schedule on
        return False
    return True


@router.post("/runs")
async def create_run() -> dict:
    """Start the agent pipeline. Asking again while it is running returns the run already in progress."""
    return {"runId": launch("sync")}


@router.get("/runs/{run_id}/stream")
async def run_stream(run_id: int):
    """Server-sent events: what the agents share, do and say during this run, ending with `event: done`."""
    with session() as s:
        if s.get(Run, run_id) is None:
            raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return stream_run(run_id, STREAMED, presenters.event_view)
