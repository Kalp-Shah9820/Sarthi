"""The one read endpoint: every screen's data in one response."""

from fastapi import APIRouter, HTTPException

from sarthi.api import presenters

router = APIRouter(tags=["read"])


@router.get("/bootstrap")
def bootstrap(lang: str = "EN") -> dict:
    """All screen data from the latest completed run, in the shapes the frontend already uses."""
    try:
        return presenters.bootstrap(lang)
    except presenters.NotReady as exc:
        raise HTTPException(status_code=503, detail="warming up") from exc
