"""Data Hub uploads."""

import asyncio

from fastapi import APIRouter, HTTPException, UploadFile

from sarthi.api import presenters
from sarthi.ingest.loader import MAX_BYTES, ingest
from sarthi.ingest.schemas import UPLOAD_TYPES, IngestError

router = APIRouter(tags=["datahub"])


@router.post("/datahub/upload/{upload_type}")
async def upload(upload_type: str, file: UploadFile) -> dict:
    """Validate and store one file (CSV, Excel or JSON). Returns how many rows were read and any that were dropped."""
    if upload_type not in UPLOAD_TYPES:
        raise HTTPException(status_code=404, detail=f"unknown upload type '{upload_type}'; expected one of {', '.join(UPLOAD_TYPES)}")
    if file.size is not None and file.size > MAX_BYTES:
        raise HTTPException(status_code=413, detail="file is larger than 20 MB")
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="file is larger than 20 MB")
    try:
        result = await asyncio.to_thread(ingest, upload_type, file.filename or "upload", data)
    except IngestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    presenters.bump()
    return result
