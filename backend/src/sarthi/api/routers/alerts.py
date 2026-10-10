"""The manager's decision on an alert: approve (carry it out) or dismiss, with optional feedback."""

import threading

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from sarthi.agents.execution_engine import execute_alert
from sarthi.api import presenters
from sarthi.api.routers import runs
from sarthi.db import session
from sarthi.learning import memory
from sarthi.llm import get_llm
from sarthi.models import Alert

router = APIRouter(tags=["alerts"])
_deciding = threading.Lock()      # one decision at a time, so a double click cannot execute an alert twice


class Decision(BaseModel):
    feedback: str | None = Field(default=None, max_length=500)


async def _learn_from_feedback(alert: Alert, decision: str, feedback: str) -> None:
    """Turn feedback text into a standing rule. Runs after the response: it may need the model."""
    await memory.remember_feedback(alert, decision, feedback, get_llm())


def _load(alert_id: int) -> Alert:
    with session() as s:
        alert = s.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail=f"alert {alert_id} not found")
    return alert


@router.post("/alerts/{alert_id}/approve")
def approve(alert_id: int, background: BackgroundTasks, body: Decision | None = None) -> dict:
    """Carry out the alert's action. Approving it again returns the same transaction id and changes nothing."""
    feedback = (body.feedback or "").strip() if body else ""
    with _deciding:
        alert = _load(alert_id)
        if alert.status == "dismissed":
            raise HTTPException(status_code=409, detail="alert was already dismissed")
        if alert.txid:
            return {"txid": alert.txid, "status": "approved", "runStarted": False}
        result = execute_alert(alert_id, source="alert")
        alert, _ = memory.note_decision(alert_id, True, feedback or None)
        presenters.bump()
    if feedback:
        background.add_task(_learn_from_feedback, alert, "approved", feedback)
    # stock or orders changed: let the agents look again (a count or a cap changes nothing they would see)
    started = result["kind"] in ("purchase", "transfer") and runs.rerun_after_action()
    return {"txid": result["txid"], "status": "approved", "runStarted": bool(started)}


@router.post("/alerts/{alert_id}/dismiss")
def dismiss(alert_id: int, background: BackgroundTasks, body: Decision | None = None) -> dict:
    """Reject the alert. Nothing is executed; the system learns from the refusal and any feedback."""
    feedback = (body.feedback or "").strip() if body else ""
    with _deciding:
        alert = _load(alert_id)
        if alert.status == "approved":
            raise HTTPException(status_code=409, detail="alert was already approved and carried out")
        if alert.status == "dismissed":
            return {"status": "dismissed"}
        alert, _ = memory.note_decision(alert_id, False, feedback or None)
        presenters.bump()
    if feedback:
        background.add_task(_learn_from_feedback, alert, "dismissed", feedback)
    return {"status": "dismissed"}
