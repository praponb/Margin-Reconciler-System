from __future__ import annotations

import io
import uuid
from datetime import date
from zoneinfo import ZoneInfo

import polars as pl
from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from reconciler_app.api.deps import get_session
from reconciler_app.api.schemas import MarginPage, MarginRow, SummaryRow
from reconciler_app.core.config import get_settings
from reconciler_app.db import repositories as repo
from reconciler_app.db.models import FactSkuMargin as M

router = APIRouter(tags=["margins"])

EXCEPTION_STATUSES = ("UNMAPPED_SKU", "PENDING_PAYOUT", "LOSS_MAKING", "SHIPPING_OVERCHARGE")


class MarginFilters:
    def __init__(
        self,
        merchant_id: uuid.UUID = Query(...),
        channel: str | None = None,
        status: list[str] | None = Query(None),
        sku: str | None = None,
        order_id: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ):
        self.merchant_id = merchant_id
        self.channel = channel
        self.status = status
        self.sku = sku
        self.order_id = order_id
        self.date_from = date_from
        self.date_to = date_to

    def conditions(self, default_statuses: tuple[str, ...] | None = None) -> list:
        conds = [M.merchant_id == self.merchant_id]
        if self.channel:
            conds.append(M.channel == self.channel.upper())
        statuses = self.status or default_statuses
        if statuses:
            conds.append(M.reconcile_status.in_(statuses))
        if self.sku:
            conds.append(M.internal_sku.ilike(f"%{self.sku}%"))
        if self.order_id:
            conds.append(M.channel_order_id == self.order_id)
        start, end = repo.local_day_bounds(self.date_from, self.date_to)
        if start:
            conds.append(M.order_date >= start)
        if end:
            conds.append(M.order_date < end)
        return conds


def _as_row(m: M) -> MarginRow:
    row = MarginRow.model_validate(m)
    row.order_date = repo.as_utc(m.order_date).astimezone(ZoneInfo(get_settings().local_timezone))
    return row


def _page(session: Session, conds: list, limit: int, offset: int, order_by) -> MarginPage:
    total = session.scalar(select(func.count()).select_from(M).where(*conds)) or 0
    rows = session.scalars(select(M).where(*conds).order_by(*order_by).limit(limit).offset(offset))
    return MarginPage(total=total, limit=limit, offset=offset, rows=[_as_row(r) for r in rows])


@router.get("/margins", response_model=MarginPage)
def margins(
    filters: MarginFilters = Depends(),
    limit: int = Query(100, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
):
    return _page(session, filters.conditions(), limit, offset, (M.order_date.desc(), M.channel_order_id))


@router.get("/exceptions", response_model=MarginPage)
def exceptions(
    filters: MarginFilters = Depends(),
    limit: int = Query(100, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
):
    """Exception queue: every non-HEALTHY line, worst profit first."""
    conds = filters.conditions(default_statuses=EXCEPTION_STATUSES)
    return _page(session, conds, limit, offset, (M.net_profit.asc(), M.order_date.desc()))


@router.get("/margins/summary", response_model=list[SummaryRow])
def summary(filters: MarginFilters = Depends(), session: Session = Depends(get_session)):
    """P&L by channel and internal SKU."""
    fees = M.allocated_commission + M.allocated_service_fee + M.allocated_transaction
    stmt = (
        select(
            M.channel,
            M.internal_sku,
            func.count().label("lines"),
            func.sum(M.quantity).label("quantity"),
            func.sum(M.gross_revenue).label("gross_revenue"),
            func.sum(M.total_cogs).label("total_cogs"),
            func.sum(M.total_packaging).label("total_packaging"),
            func.sum(fees).label("total_fees"),
            func.sum(M.allocated_shipping_gap).label("shipping_gap"),
            func.sum(M.net_profit).label("net_profit"),
        )
        .where(*filters.conditions())
        .group_by(M.channel, M.internal_sku)
        .order_by(func.sum(M.net_profit).asc())
    )
    out = []
    for r in session.execute(stmt).mappings():
        revenue = float(r["gross_revenue"] or 0)
        profit = float(r["net_profit"] or 0)
        out.append(
            SummaryRow(
                **{
                    k: (float(v) if k not in ("channel", "internal_sku", "lines", "quantity") else v)
                    for k, v in r.items()
                },
                net_margin_pct=round(profit / revenue * 100, 2) if revenue > 0 else 0.0,
            )
        )
    return out


@router.get("/margins/export.csv")
def export_csv(
    filters: MarginFilters = Depends(),
    exceptions_only: bool = False,
    session: Session = Depends(get_session),
):
    """CSV export encoded as UTF-8 with BOM so Thai text opens correctly in Excel."""
    conds = filters.conditions(default_statuses=EXCEPTION_STATUSES if exceptions_only else None)
    rows = [
        _as_row(m).model_dump(mode="json")
        for m in session.scalars(select(M).where(*conds).order_by(M.order_date, M.channel_order_id))
    ]
    df = (
        pl.DataFrame(rows, schema=list(MarginRow.model_fields), orient="row")
        if rows
        else pl.DataFrame(schema=list(MarginRow.model_fields))
    )
    buf = io.StringIO()
    df.write_csv(buf)
    body = buf.getvalue().encode("utf-8-sig")
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="sku_margins.csv"'},
    )
