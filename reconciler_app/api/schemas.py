from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from reconciler_app.core.canonical_mapping import CHANNELS


class ProductIn(BaseModel):
    internal_sku: str = Field(min_length=1, max_length=100)
    product_name: str = Field(min_length=1, max_length=255)
    cogs_amount: float = Field(ge=0)
    packaging_cost: float = Field(default=0.0, ge=0)


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    product_id: uuid.UUID
    internal_sku: str
    product_name: str
    cogs_amount: Decimal
    packaging_cost: Decimal


class MappingIn(BaseModel):
    channel: str
    channel_sku: str = Field(min_length=1, max_length=150)
    internal_sku: str = Field(min_length=1, max_length=100)

    @field_validator("channel")
    @classmethod
    def _channel(cls, v: str) -> str:
        v = v.strip().upper()
        if v not in CHANNELS:
            raise ValueError(f"channel must be one of {', '.join(CHANNELS)}")
        return v


class MappingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    mapping_id: int
    channel: str
    channel_sku: str
    internal_sku: str


class UpsertResult(BaseModel):
    upserted: int


class IngestFileResult(BaseModel):
    filename: str
    status: str
    skipped_duplicate: bool
    report_type: str | None = None
    channel: str | None = None
    kind: str | None = None
    encoding: str | None = None
    header_row: int | None = None
    rows: int = 0
    skipped_rows: int = 0
    dropped_columns: list[str] = []
    batch_id: str | None = None
    message: str | None = None


class BatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    batch_id: uuid.UUID
    filename: str | None
    channel: str
    report_type: str
    row_count: int
    ingested_at: datetime


class ReconcileRequest(BaseModel):
    merchant_id: uuid.UUID
    date_from: date | None = None
    date_to: date | None = None


class ReconcileResponse(BaseModel):
    rows: int
    status_counts: dict[str, int]
    date_from: date | None
    date_to: date | None


class MarginRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    reconcile_id: uuid.UUID
    channel: str
    channel_order_id: str
    internal_sku: str
    order_date: datetime
    settlement_date: date | None
    quantity: int
    gross_revenue: float
    total_cogs: float
    total_packaging: float
    allocated_commission: float
    allocated_service_fee: float
    allocated_transaction: float
    allocated_shipping_gap: float
    net_profit: float
    net_margin_pct: float
    reconcile_status: str


class MarginPage(BaseModel):
    total: int
    limit: int
    offset: int
    rows: list[MarginRow]


class SummaryRow(BaseModel):
    channel: str
    internal_sku: str
    lines: int
    quantity: int
    gross_revenue: float
    total_cogs: float
    total_packaging: float
    total_fees: float
    shipping_gap: float
    net_profit: float
    net_margin_pct: float


class PricingRequest(BaseModel):
    cogs: float = Field(ge=0)
    packaging: float = Field(default=0.0, ge=0)
    shipping_buffer: float = Field(default=0.0, ge=0)
    commission_pct: float = Field(default=0.0, ge=0, le=100)
    transaction_pct: float = Field(default=0.0, ge=0, le=100)
    service_pct: float = Field(default=0.0, ge=0, le=100)
    target_margin_pct: float = Field(le=100)
    vat_rate: float | None = Field(default=None, ge=0, le=1)


class PricingResponse(BaseModel):
    recommended_price: float
    effective_fee_rate: float
    platform_fees: float
    vat_on_fees: float
    unit_cost: float
    net_profit: float
    net_margin_pct: float
