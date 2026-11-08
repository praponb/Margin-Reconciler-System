from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import ValidationError
from sqlalchemy.orm import Session

from reconciler_app.api.deps import get_session
from reconciler_app.api.schemas import MappingIn, MappingOut, ProductIn, ProductOut, UpsertResult
from reconciler_app.core.canonical_mapping import MappingError
from reconciler_app.core.parser import parse_simple_table
from reconciler_app.db import repositories as repo

router = APIRouter(tags=["master data"])


def _validated(model, rows: list[dict]) -> list[dict]:
    try:
        return [model.model_validate(r).model_dump() for r in rows]
    except ValidationError as exc:
        raise HTTPException(422, exc.errors(include_url=False, include_context=False)) from exc


def _read_table(data: bytes, spec: str, filename: str | None) -> list[dict]:
    try:
        return parse_simple_table(data, spec, filename).to_dicts()
    except MappingError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/products", response_model=list[ProductOut])
def get_products(merchant_id: uuid.UUID = Query(...), session: Session = Depends(get_session)):
    return repo.list_products(session, merchant_id)


@router.post("/products", response_model=UpsertResult)
def post_products(
    products: list[ProductIn], merchant_id: uuid.UUID = Query(...), session: Session = Depends(get_session)
):
    n = repo.upsert_products(session, merchant_id, [p.model_dump() for p in products])
    session.commit()
    return {"upserted": n}


@router.post("/products/upload", response_model=UpsertResult)
async def upload_products(
    merchant_id: uuid.UUID = Query(...), file: UploadFile = File(...), session: Session = Depends(get_session)
):
    """Bulk COGS upload. Columns: SKU, Product Name, COGS, Packaging (Thai headers accepted)."""
    rows = _read_table(await file.read(), "PRODUCT_MASTER", file.filename)
    rows = [{k: (v if v not in ("", None) else None) for k, v in r.items()} for r in rows]
    for r in rows:
        r["packaging_cost"] = r.get("packaging_cost") or 0.0
    n = repo.upsert_products(session, merchant_id, _validated(ProductIn, rows))
    session.commit()
    return {"upserted": n}


@router.delete("/products/{internal_sku}", response_model=UpsertResult)
def remove_product(
    internal_sku: str, merchant_id: uuid.UUID = Query(...), session: Session = Depends(get_session)
):
    n = repo.delete_product(session, merchant_id, internal_sku)
    session.commit()
    if not n:
        raise HTTPException(404, f"Product {internal_sku} not found")
    return {"upserted": 0}


@router.get("/sku-mappings", response_model=list[MappingOut])
def get_mappings(merchant_id: uuid.UUID = Query(...), session: Session = Depends(get_session)):
    return repo.list_mappings(session, merchant_id)


def _save_mappings(session: Session, merchant_id: uuid.UUID, rows: list[dict]) -> dict:
    try:
        n = repo.upsert_mappings(session, merchant_id, rows)
    except ValueError as exc:
        session.rollback()
        raise HTTPException(422, str(exc)) from exc
    session.commit()
    return {"upserted": n}


@router.post("/sku-mappings", response_model=UpsertResult)
def post_mappings(
    mappings: list[MappingIn], merchant_id: uuid.UUID = Query(...), session: Session = Depends(get_session)
):
    return _save_mappings(session, merchant_id, [m.model_dump() for m in mappings])


@router.post("/sku-mappings/upload", response_model=UpsertResult)
async def upload_mappings(
    merchant_id: uuid.UUID = Query(...), file: UploadFile = File(...), session: Session = Depends(get_session)
):
    rows = _read_table(await file.read(), "SKU_MAPPING", file.filename)
    return _save_mappings(session, merchant_id, _validated(MappingIn, rows))
