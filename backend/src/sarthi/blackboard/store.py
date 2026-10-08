"""The blackboard: an append-only event log that is also the agents' shared memory.

Agents never call each other. They write typed facts here and read what others wrote. Because every
write is one `Event` row, the same table feeds the Shared Context panel (latest value per key), the
audit trail (`action` events in order), the agent debate (`debate` events in order) and live streams.
"""

import asyncio
from datetime import UTC, datetime

from sqlalchemy import delete
from sqlmodel import select

from sarthi.db import session
from sarthi.models import Event

KINDS = ("context", "action", "debate", "metric", "llm", "error")
STANCES = ("propose", "object", "revise", "rule", "execute")
END = "_end"  # synthetic event kind sent to subscribers when a run finishes

# run_id -> [(event loop, queue)]; live listeners (SSE endpoints) for that run
_subscribers: dict[int, list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]]] = {}


def subscribe(run_id: int) -> asyncio.Queue:
    """Receive a copy of every event written for `run_id` from now on. Call from inside the event loop."""
    queue: asyncio.Queue = asyncio.Queue()
    _subscribers.setdefault(run_id, []).append((asyncio.get_running_loop(), queue))
    return queue


def unsubscribe(run_id: int, queue: asyncio.Queue) -> None:
    listeners = [(loop, q) for loop, q in _subscribers.get(run_id, []) if q is not queue]
    if listeners:
        _subscribers[run_id] = listeners
    else:
        _subscribers.pop(run_id, None)


def publish(run_id: int, event: dict) -> None:
    """Hand an event to live listeners. Safe to call from worker threads."""
    for loop, queue in list(_subscribers.get(run_id, [])):
        if not loop.is_closed():
            loop.call_soon_threadsafe(queue.put_nowait, event)


def end_run(run_id: int) -> None:
    """Tell listeners the run is over so their streams can close."""
    publish(run_id, {"kind": END, "run_id": run_id})


def as_dict(event: Event) -> dict:
    return {
        "id": event.id, "run_id": event.run_id, "ts": event.ts, "phase": event.phase, "agent": event.agent,
        "kind": event.kind, "key": event.key, "value": event.value, "text": event.text, "sku_id": event.sku_id,
    }


def write_event(run_id: int, *, kind: str, agent: str = "", phase: str = "", key: str | None = None,
                value: dict | None = None, text: str | None = None, sku_id: str | None = None) -> dict:
    """Insert one event and notify listeners. Returns the stored event as a dict."""
    event = Event(run_id=run_id, kind=kind, agent=agent, phase=phase, key=key, value=value or {}, text=text, sku_id=sku_id)
    with session() as s:
        s.add(event)
        s.commit()
        s.refresh(event)
        stored = as_dict(event)
    publish(run_id, stored)
    return stored


def delete_run_events(run_id: int) -> None:
    with session() as s:
        s.connection().execute(delete(Event).where(Event.run_id == run_id))
        s.commit()


class Blackboard:
    """One run's view of the event log."""

    def __init__(self, run_id: int):
        self.run_id = run_id

    # ── writing ──────────────────────────────────────────────────────────────

    def put(self, agent: str, phase: str, key: str, value, *, tone: str = "ghost") -> None:
        """Publish a shared fact, e.g. lead_time_risk = HIGH. `tone` is a zone colour name for the UI."""
        write_event(self.run_id, kind="context", agent=agent, phase=phase, key=key, value={"v": value, "tone": tone})

    def act(self, agent: str, phase: str, text: str, result: str = "", sku_id: str | None = None, **facts) -> None:
        """Record something an agent did, with a one-line result. Shown in the audit trail."""
        write_event(self.run_id, kind="action", agent=agent, phase=phase, text=text, sku_id=sku_id,
                    value={"result": result, "facts": facts})

    def say(self, agent: str, text: str, *, stance: str, phase: str = "resolve", sku_id: str | None = None,
            **facts) -> None:
        """One line of the agent debate. `stance` is propose, object, revise, rule or execute."""
        if stance not in STANCES:
            raise ValueError(f"unknown stance '{stance}'; expected one of {STANCES}")
        write_event(self.run_id, kind="debate", agent=agent, phase=phase, text=text, sku_id=sku_id,
                    value={"stance": stance, "facts": facts})

    def metric(self, agent: str, sku_id: str | None = None, *, key: str | None = None, phase: str = "", **values) -> None:
        """Numbers for later steps and presenters; not shown directly."""
        write_event(self.run_id, kind="metric", agent=agent, phase=phase, key=key, sku_id=sku_id, value=values)

    def error(self, agent: str, exc: Exception, phase: str = "") -> None:
        write_event(self.run_id, kind="error", agent=agent, phase=phase, text=f"{type(exc).__name__}: {exc}")

    # ── reading ──────────────────────────────────────────────────────────────

    def events(self, kind: str | None = None, since_id: int = 0) -> list[Event]:
        with session() as s:
            query = select(Event).where(Event.run_id == self.run_id, Event.id > since_id)
            if kind is not None:
                query = query.where(Event.kind == kind)
            return list(s.exec(query.order_by(Event.id)).all())

    def get(self, key: str, default=None):
        """Latest shared fact for `key` in this run."""
        with session() as s:
            event = s.exec(
                select(Event).where(Event.run_id == self.run_id, Event.kind == "context", Event.key == key)
                .order_by(Event.id.desc())
            ).first()
        return event.value.get("v", default) if event else default

    def context(self) -> list[dict]:
        """Latest value per key, newest first."""
        latest: dict[str, Event] = {}
        for event in self.events(kind="context"):
            latest[event.key] = event
        ordered = sorted(latest.values(), key=lambda e: e.id, reverse=True)
        return [
            {"key": e.key, "value": e.value.get("v"), "tone": e.value.get("tone", "ghost"), "agent": e.agent, "ts": e.ts}
            for e in ordered
        ]

    def metrics(self, key: str | None = None, sku_id: str | None = None) -> list[dict]:
        """Values of metric events, oldest first, optionally filtered."""
        return [
            e.value for e in self.events(kind="metric")
            if (key is None or e.key == key) and (sku_id is None or e.sku_id == sku_id)
        ]

    def end(self) -> None:
        end_run(self.run_id)


def local_time(ts: datetime) -> str:
    """HH:MM:SS in the machine's timezone, for display. Stored timestamps are UTC."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone().strftime("%H:%M:%S")
