from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from reconciler_app.api.deps import get_session
from reconciler_app.api.schemas import ReconcileRequest, ReconcileResponse
from reconciler_app.services.reconciliation import reconcile

router = APIRouter(tags=["reconciliation"])


@router.post("/reconcile", response_model=ReconcileResponse)
def run_reconcile(body: ReconcileRequest, session: Session = Depends(get_session)):
    """Rebuild fact_sku_margins for the merchant and inclusive Bangkok-local date range."""
    if body.date_from and body.date_to and body.date_from > body.date_to:
        raise HTTPException(422, "date_from must be on or before date_to")
    return asdict(reconcile(session, body.merchant_id, body.date_from, body.date_to))
