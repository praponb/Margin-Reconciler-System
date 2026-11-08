from __future__ import annotations

from fastapi import APIRouter, HTTPException

from reconciler_app.api.schemas import PricingRequest, PricingResponse
from reconciler_app.core.config import get_settings
from reconciler_app.core.pricing import price_breakdown

router = APIRouter(tags=["pricing"])


@router.post("/pricing/recommend", response_model=PricingResponse)
def recommend(body: PricingRequest):
    params = body.model_dump()
    if params["vat_rate"] is None:
        params["vat_rate"] = get_settings().vat_rate
    try:
        return price_breakdown(**params).as_dict()
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
