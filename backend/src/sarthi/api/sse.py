"""Server-sent events: a run's blackboard, streamed as it is written."""

import asyncio
import json
from collections.abc import Callable

from sse_starlette.sse import EventSourceResponse

from sarthi.blackboard import store
from sarthi.db import session
from sarthi.models import Run


def _finished(run_id: int) -> bool:
    with session() as s:
        run = s.get(Run, run_id)
        return run is None or run.status != "running"


def stream_run(run_id: int, kinds: set[str], render: Callable[[dict], dict],
               on_close: Callable[[], None] | None = None) -> EventSourceResponse:
    """Stream the events of `run_id` whose kind is in `kinds`, then `event: done`.

    The subscription is made here, before the caller starts the run, and events already stored are
    replayed first, so a listener sees the whole run wherever it joins. `on_close` runs when the stream
    ends for any reason, including the client going away.
    """
    queue = store.subscribe(run_id)

    def line(event: dict) -> dict:
        return {"data": json.dumps(render(event), ensure_ascii=False, default=str)}

    async def events():
        try:
            last = 0
            for stored in await asyncio.to_thread(store.Blackboard(run_id).events):
                last = stored.id
                if stored.kind in kinds:
                    yield line(store.as_dict(stored))
            if await asyncio.to_thread(_finished, run_id):      # nothing more will be published
                for stored in await asyncio.to_thread(store.Blackboard(run_id).events, None, last):
                    if stored.kind in kinds:
                        yield line(store.as_dict(stored))
                yield {"event": "done", "data": "{}"}
                return
            while True:
                event = await queue.get()
                if event.get("kind") == store.END:
                    yield {"event": "done", "data": "{}"}
                    return
                if event["kind"] in kinds and (event.get("id") or 0) > last:
                    yield line(event)
        finally:
            store.unsubscribe(run_id, queue)
            if on_close:
                on_close()

    return EventSourceResponse(events())
