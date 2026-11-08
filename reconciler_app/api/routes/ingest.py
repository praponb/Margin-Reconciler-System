from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.orm import Session

from reconciler_app.api.deps import get_session
from reconciler_app.api.schemas import BatchOut, IngestFileResult
from reconciler_app.db import repositories as repo
from reconciler_app.services.ingestion import ingest_file

router = APIRouter(tags=["ingestion"])


@router.post("/ingest", response_model=list[IngestFileResult])
async def ingest(
    merchant_id: uuid.UUID = Form(...),
    files: list[UploadFile] = File(...),
    session: Session = Depends(get_session),
):
    """Upload one or more marketplace exports (CSV/XLSX). Errors are reported per file."""
    results = []
    for upload in files:
        data = await upload.read()
        results.append(ingest_file(session, merchant_id, upload.filename or "upload", data).as_dict())
    return results


@router.get("/ingest/batches", response_model=list[BatchOut])
def batches(merchant_id: uuid.UUID = Query(...), limit: int = 50, session: Session = Depends(get_session)):
    return repo.list_batches(session, merchant_id, limit)
