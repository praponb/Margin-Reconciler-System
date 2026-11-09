from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from reconciler_app.api.deps import get_session
from reconciler_app.services.pipeline import pipeline_status

router = APIRouter(tags=["pipeline"])


@router.get("/pipeline/status")
def status(
    merchant_id: uuid.UUID = Query(...),
    history: int = Query(50, ge=1, le=500),
    session: Session = Depends(get_session),
):
    """Per-stage counts, payout/SKU coverage, reconcile freshness and recent run history."""
    return pipeline_status(session, merchant_id, history)
