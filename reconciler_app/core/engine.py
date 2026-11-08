"""Vectorized reconciliation: value-weighted fee attribution and SKU margins (spec §4).

Pure Polars, no I/O. Input frames are conformed to the schemas below; missing
optional columns are added and financial nulls are coalesced to 0.0.

Deviations from the spec sample, by design (see README):
* joins use (merchant_id, channel, channel_order_id), not channel_order_id alone
* settlements are collapsed to one row per order before joining
* cancelled orders are excluded
* each order's allocations are rounded to 4 dp and the rounding residual goes
  to the highest-weight line, so they sum exactly to the order-level fee
* status precedence: UNMAPPED_SKU > PENDING_PAYOUT > LOSS_MAKING >
  SHIPPING_OVERCHARGE > HEALTHY
"""

from __future__ import annotations

import uuid

import polars as pl

from reconciler_app.core.canonical_mapping import SETTLEMENT_FEE_FIELDS
from reconciler_app.core.normalize import aggregate_settlements, is_cancelled

KEYS = ["merchant_id", "channel", "channel_order_id"]

TS = pl.Datetime("us", "UTC")

ORDER_SCHEMA = {
    "merchant_id": pl.Utf8,
    "channel": pl.Utf8,
    "channel_order_id": pl.Utf8,
    "order_date": TS,
    "order_status": pl.Utf8,
}
ITEM_SCHEMA = {
    "merchant_id": pl.Utf8,
    "channel": pl.Utf8,
    "channel_order_id": pl.Utf8,
    "channel_sku": pl.Utf8,
    "internal_sku": pl.Utf8,
    "quantity": pl.Int64,
    "unit_original_price": pl.Float64,
    "seller_discount": pl.Float64,
    "subtotal": pl.Float64,
}
SETTLEMENT_SCHEMA = {
    "merchant_id": pl.Utf8,
    "channel": pl.Utf8,
    "channel_order_id": pl.Utf8,
    "settlement_date": pl.Date,
    **{c: pl.Float64 for c in (*SETTLEMENT_FEE_FIELDS, "net_payout_amount")},
}
PRODUCT_SCHEMA = {
    "merchant_id": pl.Utf8,
    "internal_sku": pl.Utf8,
    "cogs_amount": pl.Float64,
    "packaging_cost": pl.Float64,
}
MAPPING_SCHEMA = {
    "merchant_id": pl.Utf8,
    "channel": pl.Utf8,
    "channel_sku": pl.Utf8,
    "internal_sku": pl.Utf8,
}

MART_COLUMNS = (
    "reconcile_id",
    "merchant_id",
    "channel",
    "channel_order_id",
    "internal_sku",
    "order_date",
    "settlement_date",
    "quantity",
    "gross_revenue",
    "total_cogs",
    "total_packaging",
    "allocated_commission",
    "allocated_service_fee",
    "allocated_transaction",
    "allocated_shipping_gap",
    "net_profit",
    "net_margin_pct",
    "reconcile_status",
)
EXTRA_COLUMNS = ("channel_sku", "order_status")

STATUSES = ("UNMAPPED_SKU", "PENDING_PAYOUT", "LOSS_MAKING", "SHIPPING_OVERCHARGE", "HEALTHY")

FEE_ALLOCATIONS = {
    "commission_fee": "allocated_commission",
    "service_fee": "allocated_service_fee",
    "transaction_fee": "allocated_transaction",
}


def conform(df: pl.DataFrame | None, schema: dict[str, pl.DataType]) -> pl.DataFrame:
    """Select/cast to schema; add missing columns; coalesce financial nulls to 0.0."""
    if df is None:
        return pl.DataFrame(schema=schema)
    exprs = []
    for name, dtype in schema.items():
        if name not in df.columns:
            expr = pl.lit(None, dtype=dtype)
        elif isinstance(dtype, pl.Datetime):
            col = pl.col(name)
            src = df.schema[name]
            if isinstance(src, pl.Datetime):
                if src.time_zone is None:
                    col = col.dt.replace_time_zone("UTC")
                expr = col.dt.convert_time_zone("UTC").dt.cast_time_unit("us")
            else:
                expr = col.cast(dtype)
        else:
            expr = pl.col(name).cast(dtype, strict=False)
        if dtype == pl.Float64:
            expr = expr.fill_null(0.0).fill_nan(0.0)
        exprs.append(expr.alias(name))
    return df.select(exprs)


def _allocate(total_col: str, out_col: str) -> pl.Expr:
    """F_k * W_i rounded to 4 dp; rounding residual added to the anchor line."""
    raw = (pl.col(total_col) * pl.col("item_weight")).round(4)
    residual = pl.col(total_col).round(4) - raw.sum().over(KEYS)
    return (raw + pl.when(pl.col("_is_anchor")).then(residual).otherwise(0.0)).round(4).alias(out_col)


def reconcile_orders(
    orders: pl.DataFrame,
    items: pl.DataFrame,
    settlements: pl.DataFrame | None,
    products: pl.DataFrame | None,
    mappings: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Return one row per order line with allocated fees, profit, margin and status."""
    orders = conform(orders, ORDER_SCHEMA)
    items = conform(items, ITEM_SCHEMA)
    settlements = conform(settlements, SETTLEMENT_SCHEMA)
    products = conform(products, PRODUCT_SCHEMA).unique(["merchant_id", "internal_sku"], keep="last")
    mappings = conform(mappings, MAPPING_SCHEMA).unique(
        ["merchant_id", "channel", "channel_sku"], keep="last"
    )

    active = orders.filter(~is_cancelled()).unique(KEYS, keep="last")
    lines = items.join(active.select(*KEYS, "order_date", "order_status"), on=KEYS, how="inner")

    # 1. Resolve internal SKU: explicit on the line > mapping table > identical code
    lines = lines.join(
        mappings.select("merchant_id", "channel", "channel_sku", pl.col("internal_sku").alias("_mapped_sku")),
        on=["merchant_id", "channel", "channel_sku"],
        how="left",
    ).with_columns(pl.coalesce("internal_sku", "_mapped_sku", "channel_sku").alias("_candidate_sku"))
    lines = lines.join(
        products.select(
            "merchant_id",
            pl.col("internal_sku").alias("_candidate_sku"),
            "cogs_amount",
            "packaging_cost",
            pl.lit(True).alias("_is_mapped"),
        ),
        on=["merchant_id", "_candidate_sku"],
        how="left",
    ).with_columns(
        pl.col("_is_mapped").fill_null(False),
        pl.col("cogs_amount").fill_null(0.0),
        pl.col("packaging_cost").fill_null(0.0),
        pl.col("_candidate_sku").alias("internal_sku"),
    )

    # 2. Weights (spec §4.1): subtotal share, or quantity share when the order subtotal is 0
    weight_base = pl.col("subtotal").clip(lower_bound=0.0)
    lines = lines.with_columns(
        weight_base.sum().over(KEYS).alias("_order_subtotal"),
        pl.col("quantity").sum().over(KEYS).alias("_order_qty"),
    ).with_columns(
        pl.when(pl.col("_order_subtotal") > 0)
        .then(weight_base / pl.col("_order_subtotal"))
        .otherwise(pl.col("quantity") / pl.col("_order_qty"))
        .alias("item_weight")
    )

    # Deterministic anchor line (highest weight, then SKU) for rounding residuals
    lines = lines.sort(
        [*KEYS, "item_weight", "channel_sku"], descending=[False, False, False, True, False]
    ).with_columns((pl.int_range(pl.len()).over(KEYS) == 0).alias("_is_anchor"))

    # 3. Join one settlement row per order; attribute fees and the shipping gap (§4.2)
    settled = aggregate_settlements(settlements, KEYS)
    lines = lines.join(settled, on=KEYS, how="left").with_columns(
        pl.col(c).fill_null(0.0) for c in (*SETTLEMENT_FEE_FIELDS, "net_payout_amount")
    )
    lines = lines.with_columns(
        (pl.col("actual_shipping_fee_charged") - pl.col("shipping_fee_paid_by_buyer"))
        .clip(lower_bound=0.0)
        .alias("shipping_gap")
    ).with_columns(
        *[_allocate(src, dst) for src, dst in FEE_ALLOCATIONS.items()],
        _allocate("shipping_gap", "allocated_shipping_gap"),
        pl.col("subtotal").round(4).alias("gross_revenue"),
        (pl.col("cogs_amount") * pl.col("quantity")).round(4).alias("total_cogs"),
        (pl.col("packaging_cost") * pl.col("quantity")).round(4).alias("total_packaging"),
    )

    # 4. Net profit, margin and status (§4.3)
    lines = lines.with_columns(
        (
            pl.col("gross_revenue")
            - pl.col("total_cogs")
            - pl.col("total_packaging")
            - pl.col("allocated_commission")
            - pl.col("allocated_service_fee")
            - pl.col("allocated_transaction")
            - pl.col("allocated_shipping_gap")
        )
        .round(4)
        .alias("net_profit")
    ).with_columns(
        pl.when(pl.col("gross_revenue") > 0)
        .then(pl.col("net_profit") / pl.col("gross_revenue") * 100.0)
        .otherwise(0.0)
        .round(2)
        .alias("net_margin_pct"),
        pl.when(~pl.col("_is_mapped"))
        .then(pl.lit("UNMAPPED_SKU"))
        .when(pl.col("settlement_date").is_null())
        .then(pl.lit("PENDING_PAYOUT"))
        .when(pl.col("net_profit") < 0)
        .then(pl.lit("LOSS_MAKING"))
        .when(pl.col("allocated_shipping_gap") > 0)
        .then(pl.lit("SHIPPING_OVERCHARGE"))
        .otherwise(pl.lit("HEALTHY"))
        .alias("reconcile_status"),
    )

    lines = lines.with_columns(
        pl.Series("reconcile_id", [str(uuid.uuid4()) for _ in range(lines.height)], dtype=pl.Utf8)
    )
    return lines.select(*MART_COLUMNS, *EXTRA_COLUMNS).sort(["order_date", "channel_order_id", "channel_sku"])
