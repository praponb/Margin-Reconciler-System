"""Persistence helpers: upserts, slice replacement, and frame loading for the engine."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import polars as pl
from sqlalchemy import and_, delete, select, tuple_
from sqlalchemy.orm import Session

from reconciler_app.core.config import get_settings
from reconciler_app.core.engine import (
    ITEM_SCHEMA,
    MAPPING_SCHEMA,
    MART_COLUMNS,
    ORDER_SCHEMA,
    PRODUCT_SCHEMA,
    SETTLEMENT_SCHEMA,
)
from reconciler_app.db.models import (
    DimChannelSkuMapping,
    DimProduct,
    FactOrder,
    FactOrderItem,
    FactSettlement,
    FactSkuMargin,
    IngestionBatch,
    PipelineRun,
)

CHUNK = 500


# --- conversions --------------------------------------------------------------


def dec(value: float | int | None, places: int = 4) -> Decimal:
    if value is None or value != value:
        value = 0.0
    return Decimal(str(round(float(value), places)))


def as_utc(value: datetime) -> datetime:
    """Timestamps come back naive from SQLite; they were stored as UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def to_db_ts(value: datetime) -> datetime:
    return as_utc(value)


def local_day_bounds(date_from: date | None, date_to: date | None) -> tuple[datetime | None, datetime | None]:
    """Inclusive local-date range -> [start, end) in UTC."""
    tz = ZoneInfo(get_settings().local_timezone)
    start = datetime.combine(date_from, time.min, tz).astimezone(UTC) if date_from else None
    end = datetime.combine(date_to + timedelta(days=1), time.min, tz).astimezone(UTC) if date_to else None
    return start, end


def _chunks(seq: Sequence, size: int = CHUNK) -> Iterable[Sequence]:
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def _uuid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


# --- ingestion ledger -----------------------------------------------------------


def find_batch(session: Session, merchant_id: uuid.UUID, file_sha256: str) -> IngestionBatch | None:
    return session.scalar(
        select(IngestionBatch).where(
            IngestionBatch.merchant_id == merchant_id, IngestionBatch.file_sha256 == file_sha256
        )
    )


def record_batch(session: Session, **fields) -> IngestionBatch:
    batch = IngestionBatch(**fields)
    session.add(batch)
    session.flush()
    return batch


def list_batches(session: Session, merchant_id: uuid.UUID, limit: int = 50) -> list[IngestionBatch]:
    return list(
        session.scalars(
            select(IngestionBatch)
            .where(IngestionBatch.merchant_id == merchant_id)
            .order_by(IngestionBatch.ingested_at.desc())
            .limit(limit)
        )
    )


# --- master data -------------------------------------------------------------------


def upsert_products(session: Session, merchant_id: uuid.UUID, rows: list[dict]) -> int:
    skus = [r["internal_sku"] for r in rows]
    existing = {
        p.internal_sku: p
        for chunk in _chunks(skus)
        for p in session.scalars(
            select(DimProduct).where(
                DimProduct.merchant_id == merchant_id, DimProduct.internal_sku.in_(chunk)
            )
        )
    }
    for r in rows:
        product = existing.get(r["internal_sku"])
        if product is None:
            product = DimProduct(merchant_id=merchant_id, internal_sku=r["internal_sku"])
            session.add(product)
            existing[r["internal_sku"]] = product
        product.product_name = r["product_name"]
        product.cogs_amount = dec(r["cogs_amount"])
        product.packaging_cost = dec(r.get("packaging_cost") or 0.0)
    session.flush()
    return len(rows)


def list_products(session: Session, merchant_id: uuid.UUID) -> list[DimProduct]:
    return list(
        session.scalars(
            select(DimProduct).where(DimProduct.merchant_id == merchant_id).order_by(DimProduct.internal_sku)
        )
    )


def delete_product(session: Session, merchant_id: uuid.UUID, internal_sku: str) -> int:
    result = session.execute(
        delete(DimProduct).where(
            DimProduct.merchant_id == merchant_id, DimProduct.internal_sku == internal_sku
        )
    )
    return result.rowcount or 0


def upsert_mappings(session: Session, merchant_id: uuid.UUID, rows: list[dict]) -> int:
    known = {p.internal_sku for p in list_products(session, merchant_id)}
    missing = sorted({r["internal_sku"] for r in rows} - known)
    if missing:
        raise ValueError(f"Unknown internal SKU(s): {', '.join(missing)}. Add them to products first.")
    keys = [(r["channel"].upper(), r["channel_sku"]) for r in rows]
    existing = {
        (m.channel, m.channel_sku): m
        for m in session.scalars(
            select(DimChannelSkuMapping).where(DimChannelSkuMapping.merchant_id == merchant_id)
        )
    }
    for (channel, channel_sku), r in zip(keys, rows, strict=True):
        mapping = existing.get((channel, channel_sku))
        if mapping is None:
            mapping = DimChannelSkuMapping(merchant_id=merchant_id, channel=channel, channel_sku=channel_sku)
            session.add(mapping)
            existing[(channel, channel_sku)] = mapping
        mapping.internal_sku = r["internal_sku"]
    session.flush()
    return len(rows)


def list_mappings(session: Session, merchant_id: uuid.UUID) -> list[DimChannelSkuMapping]:
    return list(
        session.scalars(
            select(DimChannelSkuMapping)
            .where(DimChannelSkuMapping.merchant_id == merchant_id)
            .order_by(DimChannelSkuMapping.channel, DimChannelSkuMapping.channel_sku)
        )
    )


# --- transactional facts -------------------------------------------------------------


def replace_orders(
    session: Session, merchant_id: uuid.UUID, channel: str, orders: pl.DataFrame, items: pl.DataFrame
) -> int:
    """Delete-then-insert the orders (and their items) present in this batch."""
    order_ids = orders["channel_order_id"].to_list()
    for chunk in _chunks(order_ids):
        existing = select(FactOrder.order_id).where(
            FactOrder.merchant_id == merchant_id,
            FactOrder.channel == channel,
            FactOrder.channel_order_id.in_(chunk),
        )
        session.execute(delete(FactOrderItem).where(FactOrderItem.order_id.in_(existing)))
        session.execute(
            delete(FactOrder).where(
                FactOrder.merchant_id == merchant_id,
                FactOrder.channel == channel,
                FactOrder.channel_order_id.in_(chunk),
            )
        )

    id_by_order: dict[str, uuid.UUID] = {}
    order_rows = []
    for o in orders.iter_rows(named=True):
        oid = uuid.uuid4()
        id_by_order[o["channel_order_id"]] = oid
        order_rows.append(
            {
                "order_id": oid,
                "merchant_id": merchant_id,
                "channel": channel,
                "channel_order_id": o["channel_order_id"],
                "order_date": to_db_ts(o["order_date"]),
                "order_status": (o.get("order_status") or "")[:50],
                "buyer_total_paid": dec(o.get("buyer_total_paid")),
            }
        )
    item_rows = [
        {
            "order_item_id": uuid.uuid4(),
            "order_id": id_by_order[i["channel_order_id"]],
            "channel_sku": i["channel_sku"],
            "internal_sku": i.get("internal_sku"),
            "quantity": int(i["quantity"]),
            "unit_original_price": dec(i["unit_original_price"]),
            "seller_discount": dec(i["seller_discount"]),
            "subtotal": dec(i["subtotal"]),
        }
        for i in items.iter_rows(named=True)
        if i["channel_order_id"] in id_by_order
    ]
    if order_rows:
        session.execute(FactOrder.__table__.insert(), order_rows)
    if item_rows:
        session.execute(FactOrderItem.__table__.insert(), item_rows)
    return len(item_rows)


def replace_settlements(
    session: Session, merchant_id: uuid.UUID, channel: str, settlements: pl.DataFrame
) -> int:
    """Delete-then-insert settlement slices keyed by (order, settlement_date)."""
    keys = [(r["channel_order_id"], r["settlement_date"]) for r in settlements.iter_rows(named=True)]
    for chunk in _chunks(keys):
        session.execute(
            delete(FactSettlement).where(
                FactSettlement.merchant_id == merchant_id,
                FactSettlement.channel == channel,
                tuple_(FactSettlement.channel_order_id, FactSettlement.settlement_date).in_(chunk),
            )
        )
    money_cols = [c for c in SETTLEMENT_SCHEMA if SETTLEMENT_SCHEMA[c] == pl.Float64]
    rows = [
        {
            "settlement_id": uuid.uuid4(),
            "merchant_id": merchant_id,
            "channel": channel,
            "channel_order_id": r["channel_order_id"],
            "settlement_date": r["settlement_date"],
            **{c: dec(r.get(c)) for c in money_cols},
        }
        for r in settlements.iter_rows(named=True)
    ]
    if rows:
        session.execute(FactSettlement.__table__.insert(), rows)
    return len(rows)


# --- engine inputs ---------------------------------------------------------------------


def _frame(rows: list[dict], schema: dict) -> pl.DataFrame:
    if not rows:
        return pl.DataFrame(schema=schema)
    return pl.DataFrame(rows, schema=schema, orient="row")


def load_engine_inputs(
    session: Session, merchant_id: uuid.UUID, start: datetime | None, end: datetime | None
) -> dict[str, pl.DataFrame]:
    mid = str(merchant_id)
    conds = [FactOrder.merchant_id == merchant_id]
    if start:
        conds.append(FactOrder.order_date >= start)
    if end:
        conds.append(FactOrder.order_date < end)
    where = and_(*conds)

    orders = [
        {
            "merchant_id": mid,
            "channel": o.channel,
            "channel_order_id": o.channel_order_id,
            "order_date": as_utc(o.order_date),
            "order_status": o.order_status,
        }
        for o in session.scalars(select(FactOrder).where(where))
    ]
    items = [
        {
            "merchant_id": mid,
            "channel": ch,
            "channel_order_id": coid,
            "channel_sku": it.channel_sku,
            "internal_sku": it.internal_sku,
            "quantity": it.quantity,
            "unit_original_price": float(it.unit_original_price),
            "seller_discount": float(it.seller_discount),
            "subtotal": float(it.subtotal),
        }
        for it, ch, coid in session.execute(
            select(FactOrderItem, FactOrder.channel, FactOrder.channel_order_id)
            .join(FactOrder, FactOrder.order_id == FactOrderItem.order_id)
            .where(where)
        )
    ]
    settlement_money = [c for c in SETTLEMENT_SCHEMA if SETTLEMENT_SCHEMA[c] == pl.Float64]
    settlements = [
        {
            "merchant_id": mid,
            "channel": s.channel,
            "channel_order_id": s.channel_order_id,
            "settlement_date": s.settlement_date,
            **{c: float(getattr(s, c)) for c in settlement_money},
        }
        for s in session.scalars(
            select(FactSettlement)
            .join(
                FactOrder,
                and_(
                    FactOrder.merchant_id == FactSettlement.merchant_id,
                    FactOrder.channel == FactSettlement.channel,
                    FactOrder.channel_order_id == FactSettlement.channel_order_id,
                ),
            )
            .where(where)
        )
    ]
    products = [
        {
            "merchant_id": mid,
            "internal_sku": p.internal_sku,
            "cogs_amount": float(p.cogs_amount),
            "packaging_cost": float(p.packaging_cost),
        }
        for p in list_products(session, merchant_id)
    ]
    mappings = [
        {
            "merchant_id": mid,
            "channel": m.channel,
            "channel_sku": m.channel_sku,
            "internal_sku": m.internal_sku,
        }
        for m in list_mappings(session, merchant_id)
    ]
    return {
        "orders": _frame(orders, ORDER_SCHEMA),
        "items": _frame(items, ITEM_SCHEMA),
        "settlements": _frame(settlements, SETTLEMENT_SCHEMA),
        "products": _frame(products, PRODUCT_SCHEMA),
        "mappings": _frame(mappings, MAPPING_SCHEMA),
    }


# --- analytical mart -----------------------------------------------------------------------

MART_MONEY = (
    "gross_revenue",
    "total_cogs",
    "total_packaging",
    "allocated_commission",
    "allocated_service_fee",
    "allocated_transaction",
    "allocated_shipping_gap",
    "net_profit",
)


def replace_margins(
    session: Session, merchant_id: uuid.UUID, start: datetime | None, end: datetime | None, df: pl.DataFrame
) -> int:
    conds = [FactSkuMargin.merchant_id == merchant_id]
    if start:
        conds.append(FactSkuMargin.order_date >= start)
    if end:
        conds.append(FactSkuMargin.order_date < end)
    session.execute(delete(FactSkuMargin).where(*conds))
    rows = []
    for r in df.select(MART_COLUMNS).iter_rows(named=True):
        row = {
            **r,
            "reconcile_id": _uuid(r["reconcile_id"]),
            "merchant_id": merchant_id,
            "order_date": to_db_ts(r["order_date"]),
            "quantity": int(r["quantity"]),
            "net_margin_pct": dec(r["net_margin_pct"], 2),
        }
        for c in MART_MONEY:
            row[c] = dec(r[c])
        rows.append(row)
    for chunk in _chunks(rows):
        session.execute(FactSkuMargin.__table__.insert(), list(chunk))
    return len(rows)


# --- pipeline run log ------------------------------------------------------------------------


def record_run(session: Session, **fields) -> PipelineRun:
    """Add a pipeline run row; the caller commits."""
    for key in ("started_at", "finished_at"):
        fields[key] = to_db_ts(fields[key])
    run = PipelineRun(**fields)
    session.add(run)
    session.flush()
    return run


def list_runs(session: Session, merchant_id: uuid.UUID, limit: int = 50) -> list[PipelineRun]:
    return list(
        session.scalars(
            select(PipelineRun)
            .where(PipelineRun.merchant_id == merchant_id)
            .order_by(PipelineRun.started_at.desc())
            .limit(limit)
        )
    )
