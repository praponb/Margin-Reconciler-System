"""SQLAlchemy 2.0 ORM definitions matching spec §3, plus `ingestion_batches`.

Types are portable across SQLite (dev) and PostgreSQL (prod). Timestamps are
stored in UTC; on SQLite, which has no time zone support, they are stored naive
and re-tagged as UTC when read (see repositories.as_utc).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

TZ_TIMESTAMP = DateTime(timezone=True)
MONEY = Numeric(12, 4)
SMALL_MONEY = Numeric(10, 4)
BIGSERIAL = BigInteger().with_variant(Integer, "sqlite")


class Base(DeclarativeBase):
    pass


class DimProduct(Base):
    __tablename__ = "dim_products"
    __table_args__ = (UniqueConstraint("merchant_id", "internal_sku", name="uq_merchant_sku"),)

    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    internal_sku: Mapped[str] = mapped_column(String(100), nullable=False)
    product_name: Mapped[str] = mapped_column(String(255), nullable=False)
    cogs_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    packaging_cost: Mapped[Decimal] = mapped_column(SMALL_MONEY, nullable=False, default=Decimal("0"))
    created_at: Mapped[datetime] = mapped_column(TZ_TIMESTAMP, server_default=func.current_timestamp())


class DimChannelSkuMapping(Base):
    __tablename__ = "dim_channel_sku_mapping"
    __table_args__ = (
        UniqueConstraint("merchant_id", "channel", "channel_sku", name="uq_channel_sku"),
        ForeignKeyConstraint(
            ["merchant_id", "internal_sku"],
            ["dim_products.merchant_id", "dim_products.internal_sku"],
            name="fk_internal_sku",
            ondelete="CASCADE",
        ),
    )

    mapping_id: Mapped[int] = mapped_column(BIGSERIAL, primary_key=True, autoincrement=True)
    merchant_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    channel: Mapped[str] = mapped_column(String(50), nullable=False)
    channel_sku: Mapped[str] = mapped_column(String(150), nullable=False)
    internal_sku: Mapped[str] = mapped_column(String(100), nullable=False)


class FactOrder(Base):
    __tablename__ = "fact_orders"
    __table_args__ = (
        UniqueConstraint("merchant_id", "channel", "channel_order_id", name="uq_channel_order"),
        Index("idx_fact_orders_lookup", "merchant_id", "channel_order_id"),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    channel: Mapped[str] = mapped_column(String(50), nullable=False)
    channel_order_id: Mapped[str] = mapped_column(String(100), nullable=False)
    order_date: Mapped[datetime] = mapped_column(TZ_TIMESTAMP, nullable=False)
    order_status: Mapped[str] = mapped_column(String(50), nullable=False)
    buyer_total_paid: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))

    items: Mapped[list[FactOrderItem]] = relationship(
        back_populates="order", cascade="all, delete-orphan", passive_deletes=True
    )


class FactOrderItem(Base):
    __tablename__ = "fact_order_items"
    __table_args__ = (CheckConstraint("quantity > 0", name="ck_item_quantity_positive"),)

    order_item_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("fact_orders.order_id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel_sku: Mapped[str] = mapped_column(String(150), nullable=False)
    internal_sku: Mapped[str | None] = mapped_column(String(100), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_original_price: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    seller_discount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    subtotal: Mapped[Decimal] = mapped_column(MONEY, nullable=False)

    order: Mapped[FactOrder] = relationship(back_populates="items")


class FactSettlement(Base):
    __tablename__ = "fact_settlements"
    __table_args__ = (
        # Addition to spec: one slice per order per settlement date (idempotent re-ingest).
        UniqueConstraint(
            "merchant_id", "channel", "channel_order_id", "settlement_date", name="uq_settlement_slice"
        ),
        Index("idx_settlements_order", "merchant_id", "channel_order_id"),
    )

    settlement_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    channel: Mapped[str] = mapped_column(String(50), nullable=False)
    channel_order_id: Mapped[str] = mapped_column(String(100), nullable=False)
    settlement_date: Mapped[date] = mapped_column(Date, nullable=False)
    commission_fee: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    transaction_fee: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    service_fee: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    shipping_fee_paid_by_buyer: Mapped[Decimal] = mapped_column(
        SMALL_MONEY, nullable=False, default=Decimal("0")
    )
    actual_shipping_fee_charged: Mapped[Decimal] = mapped_column(
        SMALL_MONEY, nullable=False, default=Decimal("0")
    )
    voucher_platform_subsidy: Mapped[Decimal] = mapped_column(
        SMALL_MONEY, nullable=False, default=Decimal("0")
    )
    net_payout_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)


class FactSkuMargin(Base):
    __tablename__ = "fact_sku_margins"
    __table_args__ = (
        Index("idx_sku_margins_perf", "merchant_id", "order_date", "channel"),
        Index("idx_sku_margins_status", "merchant_id", "reconcile_status"),
    )

    reconcile_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    channel: Mapped[str] = mapped_column(String(50), nullable=False)
    channel_order_id: Mapped[str] = mapped_column(String(100), nullable=False)
    internal_sku: Mapped[str] = mapped_column(String(100), nullable=False)
    order_date: Mapped[datetime] = mapped_column(TZ_TIMESTAMP, nullable=False)
    settlement_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    gross_revenue: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    total_cogs: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    total_packaging: Mapped[Decimal] = mapped_column(SMALL_MONEY, nullable=False)
    allocated_commission: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    allocated_service_fee: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    allocated_transaction: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    allocated_shipping_gap: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    net_profit: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    net_margin_pct: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    reconcile_status: Mapped[str] = mapped_column(String(30), nullable=False)


class IngestionBatch(Base):
    """Addition to spec: SHA-256 ledger for idempotent ingestion."""

    __tablename__ = "ingestion_batches"
    __table_args__ = (UniqueConstraint("merchant_id", "file_sha256", name="uq_batch_file_hash"),)

    batch_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    channel: Mapped[str] = mapped_column(String(50), nullable=False)
    report_type: Mapped[str] = mapped_column(String(50), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ingested_at: Mapped[datetime] = mapped_column(TZ_TIMESTAMP, server_default=func.current_timestamp())
